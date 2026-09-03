"""文生图：按需生成、本地缓存、按用户完全隔离。

支持两种上游：

* ``openai``：``POST {base_url}/images/generations``（官方、Azure 风格网关、各类中转站）
* ``custom``：你自己写请求体模板与取图路径，可以接 SD WebUI / ComfyUI / 国内厂商

**没有配置就整体关闭**：``generate()`` 会返回 ``skipped``，前端显示配色占位符。

提示词规则
----------
* 人物一律标注 **adult**——这个世界里不存在未成年角色，图像也必须如此。
* 默认强制 SFW：主流图像 API 会直接拒绝尺度内容，不加约束的结果是大量生成失败。
  这个开关只影响图像，文字叙事不受任何影响。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from engine import tools as T
from engine.tools import GameSession, use_session

from .config import Settings

log = logging.getLogger("server.images")

KINDS = ("avatar", "portrait", "scene", "cg")
SUBJECT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")

DEFAULT_STYLE = (
    "anime illustration, Japanese light novel art style, clean line art, "
    "soft natural lighting, muted warm palette, high detail"
)

SFW_SUFFIX = (
    "fully clothed, modest everyday clothing, wholesome, safe for work, "
    "no nudity, no suggestive posing"
)

#: SFW 开启时从提示词里剔除的词（大小写不敏感）
RISKY_WORDS = (
    "nude", "naked", "nsfw", "topless", "lingerie", "underwear", "bikini",
    "erotic", "sexual", "sensual", "seductive", "cleavage", "explicit",
    "裸", "情色", "性感", "内衣", "泳装",
)

KIND_TEMPLATES = {
    "avatar": (
        "Character portrait, head and shoulders, front facing, calm neutral expression, "
        "plain soft background. Adult university-preparatory student (18+ years old)."
    ),
    "portrait": (
        "Full character sheet illustration, three-quarter view, standing, natural pose, "
        "plain background, visible from head to knees. Adult university-preparatory student (18+ years old)."
    ),
    "scene": (
        "Establishing shot of an empty location, no people in frame, "
        "cinematic composition, depth of field."
    ),
    "cg": (
        "Story illustration, cinematic composition, expressive but restrained. "
        "All depicted characters are adults (18+)."
    ),
}

#: 制服领带颜色按年级区分，是世界设定里写死的（world/school.md）。
#: 不告诉模型具体年级的话，它会随便挑一个颜色，跨图不一致。
TIE_BY_GRADE = {1: "green", 2: "red", 3: "blue"}

UNIFORM_BASE = "wearing the school's navy blazer uniform with a white shirt"


def uniform_for(class_id: str | None) -> str:
    """按班级推出年级，给出正确的领带颜色。"""
    grade = None
    if isinstance(class_id, str):
        match = re.search(r"class_(\d)", class_id)
        if match:
            grade = int(match.group(1))
    if grade in TIE_BY_GRADE:
        return f"{UNIFORM_BASE}, with a {TIE_BY_GRADE[grade]} necktie (year {grade} student)"
    return UNIFORM_BASE


class ImageError(Exception):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _dig(payload: Any, path: str) -> Any:
    """按 ``data.0.b64_json`` 这样的点路径取值。"""
    node = payload
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def sanitize_prompt(prompt: str, *, sfw: bool) -> str:
    prompt = " ".join(str(prompt or "").split())
    if sfw:
        for word in RISKY_WORDS:
            prompt = re.sub(re.escape(word), "", prompt, flags=re.IGNORECASE)
        prompt = " ".join(prompt.split())
    return prompt[:1800]


class ImageService:
    def __init__(self, settings: Settings, session: GameSession, images_dir: Path) -> None:
        self.settings = settings
        self.session = session
        self.dir = images_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def resolve(self, credentials: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if isinstance(credentials, dict):
            data = credentials
        elif credentials is not None:
            data = {
                key: getattr(credentials, key, None)
                for key in ("provider", "base_url", "api_key", "model", "size",
                            "request_template", "response_path", "style", "sfw")
            }
        defaults = self.settings.image
        provider = (data.get("provider") or defaults.provider or "openai").strip().lower()
        if provider not in ("openai", "custom"):
            raise ImageError(f"不支持的图像 provider: {provider}", status_code=400)
        api_key = (data.get("api_key") or defaults.api_key or "").strip()
        base_url = (data.get("base_url") or defaults.base_url or "").strip()
        if provider == "openai" and not base_url:
            base_url = "https://api.openai.com/v1"
        # 服务器开着强制 SFW 时，客户端无法关掉；服务器允许时才由客户端决定。
        requested_sfw = data.get("sfw")
        resolved_sfw = True if self.settings.image_sfw else (True if requested_sfw is None else bool(requested_sfw))
        return {
            "provider": provider,
            "base_url": base_url,
            "api_key": api_key,
            "model": (data.get("model") or defaults.model or "gpt-image-1").strip(),
            "size": (data.get("size") or defaults.size or "1024x1024").strip(),
            "style": (data.get("style") or self.settings.image_style or DEFAULT_STYLE).strip(),
            "sfw": resolved_sfw,
            "request_template": (data.get("request_template") or defaults.request_template or "").strip(),
            "response_path": (data.get("response_path") or defaults.response_path or "").strip(),
        }

    def configured(self, credentials: Any) -> bool:
        if not self.settings.image_enabled:
            return False
        try:
            config = self.resolve(credentials)
        except ImageError:
            return False
        return bool(config["api_key"] or (config["provider"] == "custom" and config["base_url"]))

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------
    def build_prompt(self, kind: str, subject_id: str, extra: str, config: dict[str, Any]) -> str:
        parts: list[str] = [config["style"], KIND_TEMPLATES.get(kind, "")]
        with use_session(self.session):
            if kind in ("avatar", "portrait"):
                character = None
                if subject_id == "player":
                    player = T.get_player_state()
                    character = {
                        "name": player.get("name"), "age": player.get("age"),
                        "appearance": (self.session.state.player.get("appearance") or ""),
                        "gender": player.get("gender"), "class": player.get("class"),
                        "role": "student",
                    }
                else:
                    state = T.get_character_state(subject_id)
                    if state.get("ok"):
                        character = {
                            "name": state.get("name"), "age": state.get("age"),
                            "appearance": state.get("appearance") or "",
                            "gender": state.get("gender"), "class": state.get("class"),
                            "role": state.get("role"),
                        }
                if character:
                    parts.append(f"Character: {character['name']}, {character['age']} years old (adult), {character.get('gender') or ''}.")
                    if character["appearance"]:
                        parts.append(f"Appearance (follow exactly): {character['appearance']}")
                    if character.get("role") == "teacher":
                        parts.append("Adult teacher in ordinary office wear, not a school uniform")
                    else:
                        parts.append(uniform_for(character.get("class")))
            elif kind == "scene":
                location = self.session.registry.get("location", subject_id) or {}
                world = T.get_world_state()
                parts.append(f"Location: {location.get('name', subject_id)}. {location.get('description', '')}")
                parts.append(
                    f"Time: {world.get('time')}, {world.get('block') or ''}; weather: {world.get('weather_zh')}; "
                    f"season: month {world.get('date', '')[5:7]}."
                )
                parts.append("Japanese school / small seaside town setting. No characters in frame.")
            elif kind == "cg":
                world = T.get_world_state()
                location = (world.get("location") or {}).get("name", "")
                parts.append(f"Setting: {location}, {world.get('time')}, {world.get('weather_zh')}.")
        if extra:
            parts.append(extra)
        if config["sfw"]:
            parts.append(SFW_SUFFIX)
        return sanitize_prompt("; ".join(part for part in parts if part), sfw=config["sfw"])

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------
    def path_for(self, kind: str, subject_id: str) -> Path:
        folder = self.dir / kind
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{subject_id}.png"

    def index_path(self) -> Path:
        return self.dir / "index.json"

    def read_index(self) -> dict[str, Any]:
        path = self.index_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def write_index(self, key: str, entry: dict[str, Any]) -> None:
        index = self.read_index()
        index[key] = entry
        self.index_path().write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def listing(self, world_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, entry in self.read_index().items():
            kind, _, subject = key.partition(":")
            if not self.path_for(kind, subject).exists():
                continue
            out.append(
                {
                    "kind": kind, "subject_id": subject,
                    "url": f"/api/worlds/{world_id}/images/file/{kind}/{subject}.png",
                    "created_at": entry.get("created_at", 0),
                    "prompt": entry.get("prompt", ""),
                }
            )
        out.sort(key=lambda item: item["created_at"], reverse=True)
        return out

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    async def generate(
        self,
        *,
        world_id: str,
        kind: str,
        subject_id: str,
        extra: str = "",
        credentials: Any = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise ImageError(f"未知图像类型: {kind}（可用: {', '.join(KINDS)}）", status_code=400)
        subject_id = (subject_id or "").strip() or "unknown"
        if not SUBJECT_RE.match(subject_id):
            raise ImageError("非法的 subject_id", status_code=400)
        if not self.settings.image_enabled:
            return {"ok": False, "skipped": "服务器关闭了图像功能"}

        target = self.path_for(kind, subject_id)
        key = f"{kind}:{subject_id}"
        url = f"/api/worlds/{world_id}/images/file/{kind}/{subject_id}.png"
        if target.exists() and not force:
            return {"ok": True, "cached": True, "image": {"kind": kind, "subject_id": subject_id, "url": url}}

        config = self.resolve(credentials)
        if not (config["api_key"] or (config["provider"] == "custom" and config["base_url"])):
            return {"ok": False, "skipped": "没有配置文生图服务"}

        prompt = self.build_prompt(kind, subject_id, extra, config)
        raw = await self._call_upstream(prompt, config)
        target.write_bytes(raw)
        self.write_index(key, {
            "kind": kind, "subject_id": subject_id, "created_at": time.time(),
            "prompt": prompt, "model": config["model"],
            "hash": hashlib.sha256(raw).hexdigest()[:16],
        })
        log.info("generated image %s for world %s", key, world_id)
        return {
            "ok": True, "cached": False,
            "image": {"kind": kind, "subject_id": subject_id, "url": url},
            "prompt": prompt,
        }

    async def probe(self, credentials: Any = None, *, prompt: str = "") -> dict[str, Any]:
        """连通性自检：真实向上游要一张图，但不写入任何世界。

        与世界无关，所以不需要 world_id——这正是「设置」里那个测试按钮该走的路径。
        """
        if not self.settings.image_enabled:
            return {"ok": False, "error": "服务器关闭了图像功能（AKIZUKI_IMAGE_ENABLED=false）"}
        config = self.resolve(credentials)
        if not (config["api_key"] or (config["provider"] == "custom" and config["base_url"])):
            return {"ok": False, "error": "没有配置文生图服务：缺少 API Key，或自定义模式缺少接口地址"}
        test_prompt = sanitize_prompt(
            prompt or f"{config['style']}; an empty Japanese classroom in the afternoon, no people",
            sfw=config["sfw"],
        )
        raw = await self._call_upstream(test_prompt, config)
        result = {
            "ok": True,
            "bytes": len(raw),
            "provider": config["provider"],
            "model": config["model"],
            "size": config["size"],
            "sfw": config["sfw"],
            "prompt": test_prompt,
        }
        # 小图直接回一个 data URL，让你在设置里就能看到"确实出图了"
        if len(raw) <= 4 * 1024 * 1024:
            result["preview"] = "data:image/png;base64," + base64.b64encode(raw).decode()
        return result

    async def _call_upstream(self, prompt: str, config: dict[str, Any]) -> bytes:
        if config["provider"] == "custom":
            return await self._call_custom(prompt, config)
        return await self._call_openai(prompt, config)

    async def _call_openai(self, prompt: str, config: dict[str, Any]) -> bytes:
        base = config["base_url"].rstrip("/")
        url = base if base.endswith("/images/generations") else f"{base}/images/generations"
        payload = {"model": config["model"], "prompt": prompt, "n": 1, "size": config["size"]}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {config['api_key']}"}
        async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise ImageError(f"连接图像服务失败: {exc}", status_code=504) from exc
        if response.status_code >= 400:
            raise ImageError(_describe(response), status_code=response.status_code)
        data = response.json()
        item = _dig(data, "data.0") or {}
        if isinstance(item, dict) and item.get("b64_json"):
            return _decode_b64(item["b64_json"])
        if isinstance(item, dict) and item.get("url"):
            return await _download(item["url"], self.settings.request_timeout)
        raise ImageError("图像服务的响应里没有找到图片（既没有 b64_json 也没有 url）")

    async def _call_custom(self, prompt: str, config: dict[str, Any]) -> bytes:
        if not config["base_url"]:
            raise ImageError("自定义模式需要填写完整的接口地址", status_code=400)
        template = config["request_template"] or '{"prompt": "{prompt}"}'
        body_text = (
            template.replace("{prompt}", json.dumps(prompt, ensure_ascii=False)[1:-1])
            .replace("{model}", config["model"])
            .replace("{size}", config["size"])
        )
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise ImageError(f"请求体模板不是合法 JSON: {exc}", status_code=400) from exc

        headers = {"Content-Type": "application/json"}
        if config["api_key"]:
            headers["Authorization"] = f"Bearer {config['api_key']}"
        async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
            try:
                response = await client.post(config["base_url"], headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise ImageError(f"连接图像服务失败: {exc}", status_code=504) from exc
        if response.status_code >= 400:
            raise ImageError(_describe(response), status_code=response.status_code)

        content_type = response.headers.get("content-type", "")
        if content_type.startswith("image/"):
            return response.content
        data = response.json()
        path = config["response_path"] or "images.0"
        value = _dig(data, path)
        if not value:
            raise ImageError(f"按路径 {path} 没有取到图片。检查「取图路径」设置。")
        if isinstance(value, str) and value.startswith("http"):
            return await _download(value, self.settings.request_timeout)
        if isinstance(value, str):
            return _decode_b64(value)
        raise ImageError("取到的值既不是 URL 也不是 base64")


def _decode_b64(value: str) -> bytes:
    if "," in value and value.strip().startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ImageError(f"base64 解码失败: {exc}") from exc


async def _download(url: str, timeout: float) -> bytes:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            raise ImageError(f"下载图片失败: {exc}", status_code=504) from exc
    if response.status_code >= 400:
        raise ImageError(f"下载图片失败：{response.status_code}")
    return response.content


def _describe(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"图像服务 {response.status_code}: {response.text[:300]}"
    error = data.get("error")
    if isinstance(error, dict):
        return f"图像服务 {response.status_code}: {error.get('message') or error}"
    return f"图像服务 {response.status_code}: {str(data)[:300]}"
