"""服务端配置。全部来自环境变量，带合理默认值。

复制 ``.env.example`` 为 ``.env`` 后修改即可（uvicorn 启动时会自动读取）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _env_list(name: str, default: str = "") -> list[str]:
    raw = _env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_json(name: str) -> dict:
    """读一个 JSON 对象型环境变量；格式错误时忽略并告警。"""
    raw = _env(name)
    if not raw:
        return {}
    import json
    import logging

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        logging.getLogger("server.config").warning("%s 不是合法 JSON，已忽略: %s", name, exc)
        return {}
    return value if isinstance(value, dict) else {}


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 读取（不覆盖已存在的环境变量），避免引入额外依赖。"""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(slots=True)
class ProviderDefaults:
    """服务端预置的上游配置。留空表示"必须由用户自带"。"""

    provider: str = "openai"          # openai | anthropic
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    #: 透传给上游的额外请求体字段。用来适配各家的私有开关，
    #: 例如 DeepSeek 的 {"reasoning_effort": "none"}（关掉推理，省时省钱）。
    extra_params: dict = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(slots=True)
class ImageDefaults:
    provider: str = "openai"          # openai | custom
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    size: str = "1024x1024"
    #: custom 模式下的请求体模板与取图路径（见 server/images.py）
    request_template: str = ""
    response_path: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.base_url)


@dataclass(slots=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")

    access_password: str = ""
    allow_origins: list[str] = field(default_factory=lambda: ["*"])

    max_cached_sessions: int = 32
    max_worlds_per_user: int = 20
    max_users: int = 0                # 0 = 不限

    llm: ProviderDefaults = field(default_factory=ProviderDefaults)
    image: ImageDefaults = field(default_factory=ImageDefaults)

    image_enabled: bool = True
    image_sfw: bool = True
    image_style: str = ""

    request_timeout: float = 180.0
    turn_timeout: float = 300.0

    @property
    def auth_required(self) -> bool:
        return bool(self.access_password)

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    def world_dir(self, user_id: str, world_id: str) -> Path:
        return self.users_dir / user_id / "worlds" / world_id

    def to_public_dict(self) -> dict:
        """返回给前端的能力声明——**绝不包含任何密钥**。"""
        return {
            "auth_required": self.auth_required,
            "server_llm_configured": self.llm.configured,
            "server_llm_provider": self.llm.provider if self.llm.configured else None,
            "server_llm_model": self.llm.model if self.llm.configured else None,
            "image_enabled": self.image_enabled,
            "server_image_configured": self.image.configured,
            "image_sfw": self.image_sfw,
            "max_worlds_per_user": self.max_worlds_per_user,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    settings = Settings()
    if _env("AKIZUKI_DATA_DIR"):
        settings.data_dir = Path(_env("AKIZUKI_DATA_DIR")).expanduser().resolve()
    settings.access_password = _env("AKIZUKI_ACCESS_PASSWORD")
    settings.allow_origins = _env_list("AKIZUKI_ALLOW_ORIGINS", "*")
    settings.max_cached_sessions = _env_int("AKIZUKI_MAX_CACHED_SESSIONS", 32)
    settings.max_worlds_per_user = _env_int("AKIZUKI_MAX_WORLDS_PER_USER", 20)
    settings.max_users = _env_int("AKIZUKI_MAX_USERS", 0)
    settings.request_timeout = float(_env("AKIZUKI_REQUEST_TIMEOUT", "180") or 180)
    settings.turn_timeout = float(_env("AKIZUKI_TURN_TIMEOUT", "300") or 300)

    settings.llm = ProviderDefaults(
        provider=_env("AKIZUKI_LLM_PROVIDER", "openai") or "openai",
        base_url=_env("AKIZUKI_LLM_BASE_URL"),
        api_key=_env("AKIZUKI_LLM_API_KEY"),
        model=_env("AKIZUKI_LLM_MODEL"),
        extra_params=_env_json("AKIZUKI_LLM_EXTRA_PARAMS"),
    )
    settings.image = ImageDefaults(
        provider=_env("AKIZUKI_IMAGE_PROVIDER", "openai") or "openai",
        base_url=_env("AKIZUKI_IMAGE_BASE_URL"),
        api_key=_env("AKIZUKI_IMAGE_API_KEY"),
        model=_env("AKIZUKI_IMAGE_MODEL"),
        size=_env("AKIZUKI_IMAGE_SIZE", "1024x1024") or "1024x1024",
        request_template=_env("AKIZUKI_IMAGE_REQUEST_TEMPLATE"),
        response_path=_env("AKIZUKI_IMAGE_RESPONSE_PATH"),
    )
    settings.image_enabled = _env_bool("AKIZUKI_IMAGE_ENABLED", True)
    settings.image_sfw = _env_bool("AKIZUKI_IMAGE_SFW", True)
    settings.image_style = _env("AKIZUKI_IMAGE_STYLE")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.users_dir.mkdir(parents=True, exist_ok=True)
    return settings
