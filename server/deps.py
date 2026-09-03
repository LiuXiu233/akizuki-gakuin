"""FastAPI 依赖：鉴权、当前用户、世界会话。"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path

from .config import Settings, get_settings
from .sessions import SessionError, SessionRegistry, WorldSession, WorldStore

_store: WorldStore | None = None
_registry: SessionRegistry | None = None


def get_store() -> WorldStore:
    global _store
    if _store is None:
        _store = WorldStore(get_settings())
    return _store


def get_registry() -> SessionRegistry:
    global _registry
    if _registry is None:
        _registry = SessionRegistry(get_store(), get_settings())
    return _registry


def reset_state() -> None:
    """测试用：丢弃缓存的 store / registry。"""
    global _store, _registry
    _store = None
    _registry = None


def settings_dep() -> Settings:
    return get_settings()


async def require_access(
    settings: Annotated[Settings, Depends(settings_dep)],
    x_access_password: Annotated[str | None, Header(alias="X-Access-Password")] = None,
) -> None:
    """访问口令。未配置口令时直接放行。"""
    if not settings.auth_required:
        return
    if not x_access_password or not hmac.compare_digest(x_access_password, settings.access_password):
        raise HTTPException(status_code=401, detail="访问口令不正确")


async def current_user(
    store: Annotated[WorldStore, Depends(get_store)],
    x_user_token: Annotated[str | None, Header(alias="X-User-Token")] = None,
) -> str:
    if not x_user_token:
        raise HTTPException(status_code=401, detail="缺少用户令牌，请先调用 POST /api/session")
    try:
        store.get_profile(x_user_token, touch=True)
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return x_user_token


async def world_session(
    world_id: Annotated[str, Path()],
    user_id: Annotated[str, Depends(current_user)],
    registry: Annotated[SessionRegistry, Depends(get_registry)],
) -> WorldSession:
    try:
        return await registry.acquire(user_id, world_id)
    except SessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


AccessDep = Annotated[None, Depends(require_access)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]
StoreDep = Annotated[WorldStore, Depends(get_store)]
RegistryDep = Annotated[SessionRegistry, Depends(get_registry)]
UserDep = Annotated[str, Depends(current_user)]
WorldDep = Annotated[WorldSession, Depends(world_session)]
