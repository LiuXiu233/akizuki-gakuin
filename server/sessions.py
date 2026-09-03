"""用户 / 世界 / 会话管理。

数据布局::

    data/users/<user_id>/profile.json
    data/users/<user_id>/worlds/<world_id>/meta.json
    data/users/<user_id>/worlds/<world_id>/state/*.json     ← 引擎状态
    data/users/<user_id>/worlds/<world_id>/saves/*.json     ← 世界内的手动快照
    data/users/<user_id>/worlds/<world_id>/images/...       ← 该世界的立绘/头像/场景

每个「世界」= 一个完整独立的存档。引擎的静态资料（config/ world/ characters/
rules/ events/）全局共享且只读，可变数据全部落在世界目录里。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.state_manager import atomic_write_json
from engine.tools import GameSession, use_session

from .config import Settings, get_settings

log = logging.getLogger("server.sessions")

TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")
WORLD_ID_RE = re.compile(r"^[a-f0-9]{12}$")
NAME_MAX = 40


class SessionError(Exception):
    """用户 / 世界层面的错误（会被路由转换成 4xx）。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def new_user_id() -> str:
    return secrets.token_hex(16)


def new_world_id() -> str:
    return secrets.token_hex(6)


def clean_name(name: str, fallback: str) -> str:
    name = "".join(ch for ch in str(name or "") if ch == " " or ord(ch) >= 32).strip()
    return (name[:NAME_MAX] or fallback)


@dataclass(slots=True)
class WorldMeta:
    id: str
    name: str
    created_at: float
    updated_at: float
    pipeline: str = "multi"
    seed: int | None = None
    turn: int = 0
    date: str = ""
    time: str = ""
    player_name: str | None = None
    npc_count: int = 0
    image_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "pipeline": self.pipeline, "seed": self.seed, "turn": self.turn,
            "date": self.date, "time": self.time, "player_name": self.player_name,
            "npc_count": self.npc_count, "image_count": self.image_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldMeta":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("created_at", time.time())
        payload.setdefault("updated_at", time.time())
        return cls(**payload)


class WorldStore:
    """用户与世界的磁盘存储。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 用户
    # ------------------------------------------------------------------
    def user_dir(self, user_id: str) -> Path:
        return self.settings.users_dir / user_id

    def validate_token(self, user_id: str) -> str:
        if not isinstance(user_id, str) or not TOKEN_RE.match(user_id):
            raise SessionError("非法的用户令牌", 401)
        return user_id

    def user_exists(self, user_id: str) -> bool:
        return (self.user_dir(user_id) / "profile.json").exists()

    def create_user(self, display_name: str = "") -> dict[str, Any]:
        limit = self.settings.max_users
        if limit and len(list(self.settings.users_dir.glob("*/profile.json"))) >= limit:
            raise SessionError("服务器用户数量已达上限", 403)
        user_id = new_user_id()
        profile = {
            "id": user_id,
            "display_name": clean_name(display_name, "玩家"),
            "created_at": time.time(),
            "last_seen": time.time(),
            "settings": {},
        }
        (self.user_dir(user_id) / "worlds").mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.user_dir(user_id) / "profile.json", profile, backups=0)
        log.info("created user %s", user_id)
        return profile

    def get_profile(self, user_id: str, *, touch: bool = False) -> dict[str, Any]:
        self.validate_token(user_id)
        path = self.user_dir(user_id) / "profile.json"
        if not path.exists():
            raise SessionError("用户不存在或令牌已失效", 401)
        profile = json.loads(path.read_text(encoding="utf-8"))
        if touch:
            profile["last_seen"] = time.time()
            atomic_write_json(path, profile, backups=0)
        return profile

    def ensure_user(self, user_id: str | None) -> dict[str, Any]:
        """令牌有效则返回该用户，否则新建一个。"""
        if user_id and TOKEN_RE.match(user_id) and self.user_exists(user_id):
            return self.get_profile(user_id, touch=True)
        return self.create_user()

    def update_settings(self, user_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        profile = self.get_profile(user_id)
        current = dict(profile.get("settings") or {})
        current.update(settings or {})
        profile["settings"] = current
        atomic_write_json(self.user_dir(user_id) / "profile.json", profile, backups=0)
        return profile

    # ------------------------------------------------------------------
    # 世界
    # ------------------------------------------------------------------
    def worlds_dir(self, user_id: str) -> Path:
        path = self.user_dir(user_id) / "worlds"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def world_dir(self, user_id: str, world_id: str) -> Path:
        if not WORLD_ID_RE.match(str(world_id or "")):
            raise SessionError("非法的世界 ID", 400)
        path = self.worlds_dir(user_id) / world_id
        if not path.exists():
            raise SessionError("世界不存在", 404)
        return path

    def meta_path(self, user_id: str, world_id: str) -> Path:
        return self.worlds_dir(user_id) / world_id / "meta.json"

    def read_meta(self, user_id: str, world_id: str) -> WorldMeta:
        path = self.meta_path(user_id, world_id)
        if not path.exists():
            raise SessionError("世界不存在", 404)
        return WorldMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def write_meta(self, user_id: str, meta: WorldMeta) -> WorldMeta:
        meta.updated_at = time.time()
        atomic_write_json(self.meta_path(user_id, meta.id), meta.to_dict(), backups=0)
        return meta

    def list_worlds(self, user_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in self.worlds_dir(user_id).iterdir():
            if not path.is_dir() or not (path / "meta.json").exists():
                continue
            try:
                out.append(WorldMeta.from_dict(json.loads((path / "meta.json").read_text(encoding="utf-8"))).to_dict())
            except (json.JSONDecodeError, TypeError) as exc:
                log.warning("world meta corrupted: %s (%s)", path, exc)
        out.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return out

    def create_world(
        self,
        user_id: str,
        *,
        name: str = "",
        seed: int | None = None,
        pipeline: str = "multi",
    ) -> WorldMeta:
        existing = self.list_worlds(user_id)
        if len(existing) >= self.settings.max_worlds_per_user:
            raise SessionError(f"最多只能有 {self.settings.max_worlds_per_user} 个存档", 403)
        world_id = new_world_id()
        world_dir = self.worlds_dir(user_id) / world_id
        (world_dir / "state").mkdir(parents=True, exist_ok=True)
        (world_dir / "saves").mkdir(parents=True, exist_ok=True)
        (world_dir / "images").mkdir(parents=True, exist_ok=True)
        meta = WorldMeta(
            id=world_id,
            name=clean_name(name, f"新的秋月 {len(existing) + 1}"),
            created_at=time.time(),
            updated_at=time.time(),
            pipeline=pipeline,
            seed=seed,
        )
        return self.write_meta(user_id, meta)

    def delete_world(self, user_id: str, world_id: str) -> None:
        path = self.world_dir(user_id, world_id)
        shutil.rmtree(path, ignore_errors=True)
        log.info("deleted world %s/%s", user_id, world_id)

    def rename_world(self, user_id: str, world_id: str, name: str) -> WorldMeta:
        meta = self.read_meta(user_id, world_id)
        meta.name = clean_name(name, meta.name)
        return self.write_meta(user_id, meta)

    def images_dir(self, user_id: str, world_id: str) -> Path:
        path = self.worlds_dir(user_id) / world_id / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path


# ----------------------------------------------------------------------
# 会话缓存
# ----------------------------------------------------------------------


@dataclass
class WorldSession:
    """一个被加载进内存的世界。"""

    user_id: str
    world_id: str
    session: GameSession
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.time)

    @property
    def key(self) -> tuple[str, str]:
        return (self.user_id, self.world_id)


class SessionRegistry:
    """按 (user, world) 缓存 :class:`GameSession`，并保证同一世界串行执行。

    引擎不是线程安全的；每个世界一把锁，跨世界互不影响。
    """

    def __init__(self, store: WorldStore | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = store or WorldStore(self.settings)
        self._sessions: OrderedDict[tuple[str, str], WorldSession] = OrderedDict()
        self._registry_lock = asyncio.Lock()

    async def acquire(self, user_id: str, world_id: str) -> WorldSession:
        """取得（必要时加载）世界会话。调用方需自行 ``async with entry.lock``。"""
        key = (user_id, world_id)
        async with self._registry_lock:
            entry = self._sessions.get(key)
            if entry is None:
                world_dir = self.store.world_dir(user_id, world_id)
                entry = WorldSession(
                    user_id=user_id,
                    world_id=world_id,
                    session=GameSession(
                        root=self.settings.project_root,
                        data_root=world_dir,
                        autoload=True,
                    ),
                )
                self._sessions[key] = entry
                log.info("loaded world %s/%s", user_id, world_id)
            entry.last_used = time.time()
            self._sessions.move_to_end(key)
            await self._evict_locked()
            return entry

    async def _evict_locked(self) -> None:
        limit = max(1, self.settings.max_cached_sessions)
        while len(self._sessions) > limit:
            key, entry = next(iter(self._sessions.items()))
            if entry.lock.locked():
                self._sessions.move_to_end(key)
                break
            self._sessions.pop(key, None)
            try:
                entry.session.save()
            except Exception as exc:  # noqa: BLE001 - 淘汰失败不应影响请求
                log.warning("evict save failed %s: %s", key, exc)
            log.info("evicted world %s/%s", *key)

    async def drop(self, user_id: str, world_id: str) -> None:
        async with self._registry_lock:
            self._sessions.pop((user_id, world_id), None)

    async def drop_user(self, user_id: str) -> None:
        async with self._registry_lock:
            for key in [k for k in self._sessions if k[0] == user_id]:
                self._sessions.pop(key, None)

    def sync_meta(self, entry: WorldSession) -> WorldMeta:
        """把世界内的实时信息（回合、日期、玩家名）回写到 meta.json。"""
        state = entry.session.state
        meta = self.store.read_meta(entry.user_id, entry.world_id)
        meta.turn = int(state.world.get("turn", 0))
        meta.date = str(state.world.get("date", ""))
        meta.time = str(state.world.get("time", ""))
        meta.player_name = state.player.get("name")
        meta.npc_count = len(state.npcs)
        meta.seed = state.world.get("rng_seed", meta.seed)
        images = self.store.images_dir(entry.user_id, entry.world_id)
        meta.image_count = sum(1 for _ in images.rglob("*.png")) + sum(1 for _ in images.rglob("*.jpg"))
        return self.store.write_meta(entry.user_id, meta)

    async def close_all(self) -> None:
        async with self._registry_lock:
            for key, entry in list(self._sessions.items()):
                try:
                    entry.session.save()
                except Exception as exc:  # noqa: BLE001
                    log.warning("shutdown save failed %s: %s", key, exc)
            self._sessions.clear()


def run_in_session(entry: WorldSession, fn, *args, **kwargs):
    """在绑定了该世界的上下文中执行同步引擎调用。"""
    with use_session(entry.session):
        return fn(*args, **kwargs)


__all__ = [
    "SessionError", "SessionRegistry", "WorldMeta", "WorldSession", "WorldStore",
    "run_in_session", "clean_name",
]
