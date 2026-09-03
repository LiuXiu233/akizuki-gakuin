"""用户令牌与世界（存档）管理。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from engine import tools as T
from engine.tools import GameSession

from ..deps import AccessDep, RegistryDep, SettingsDep, StoreDep, UserDep, WorldDep
from ..schemas import (
    SessionRequest,
    SessionResponse,
    SnapshotRequest,
    UserSettingsRequest,
    WorldCreateRequest,
    WorldImportRequest,
    WorldPatchRequest,
)
from ..journal import Journal
from ..sessions import SessionError, run_in_session

router = APIRouter(tags=["worlds"])


def _guard(exc: SessionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


# ---------------------------------------------------------------------------
# 用户令牌
# ---------------------------------------------------------------------------


@router.post("/session", response_model=SessionResponse)
async def create_session(payload: SessionRequest, _: AccessDep, store: StoreDep) -> SessionResponse:
    """换取 / 校验用户令牌。前端把它存在 localStorage，之后所有请求都带上。

    令牌就是存档的钥匙：导出给另一台设备即可继续游戏。
    """
    existing = bool(payload.user_id and store.user_exists(payload.user_id))
    try:
        profile = store.ensure_user(payload.user_id)
        worlds = store.list_worlds(profile["id"])
    except SessionError as exc:
        raise _guard(exc) from exc
    return SessionResponse(
        user_id=profile["id"],
        display_name=profile.get("display_name", ""),
        created=not existing,
        settings=profile.get("settings") or {},
        worlds=worlds,
    )


@router.get("/session", response_model=SessionResponse)
async def read_session(_: AccessDep, store: StoreDep, user_id: UserDep) -> SessionResponse:
    """读取当前令牌对应的用户与存档列表。"""
    profile = store.get_profile(user_id, touch=True)
    return SessionResponse(
        user_id=user_id,
        display_name=profile.get("display_name", ""),
        settings=profile.get("settings") or {},
        worlds=store.list_worlds(user_id),
    )


@router.put("/session/settings")
async def update_settings(
    payload: UserSettingsRequest, _: AccessDep, store: StoreDep, user_id: UserDep
) -> dict[str, Any]:
    """保存用户偏好（界面模式、流水线、是否开图等）。**不要存 API key。**"""
    profile = store.update_settings(user_id, payload.settings)
    return {"ok": True, "settings": profile.get("settings", {})}


# ---------------------------------------------------------------------------
# 世界
# ---------------------------------------------------------------------------


@router.get("/worlds")
async def list_worlds(_: AccessDep, store: StoreDep, user_id: UserDep) -> dict[str, Any]:
    """列出该用户的全部存档。"""
    return {"ok": True, "worlds": store.list_worlds(user_id)}


@router.post("/worlds")
async def create_world(
    payload: WorldCreateRequest,
    _: AccessDep,
    store: StoreDep,
    registry: RegistryDep,
    settings: SettingsDep,
    user_id: UserDep,
) -> dict[str, Any]:
    """新建一个世界（= 一个新存档）。会立即初始化引擎状态。"""
    try:
        meta = store.create_world(user_id, name=payload.name, seed=payload.seed, pipeline=payload.pipeline)
    except SessionError as exc:
        raise _guard(exc) from exc

    def _init() -> dict[str, Any]:
        session = GameSession(
            root=settings.project_root,
            data_root=store.worlds_dir(user_id) / meta.id,
            seed=payload.seed,
            autoload=False,
        )
        session.save()
        return session.time.now_dict()

    world = await run_in_threadpool(_init)
    entry = await registry.acquire(user_id, meta.id)
    updated = registry.sync_meta(entry)
    return {"ok": True, "world": updated.to_dict(), "time": world}


@router.get("/worlds/{world_id}")
async def read_world(
    _: AccessDep, store: StoreDep, registry: RegistryDep, user_id: UserDep, entry: WorldDep
) -> dict[str, Any]:
    """世界快照：元信息 + 世界状态 + 玩家状态 + 面板 + 推荐上下文。"""
    async with entry.lock:
        def _snapshot() -> dict[str, Any]:
            return {
                "world_state": T.get_world_state(),
                "player": T.get_player_state(),
                "panel": T.get_turn_panel(),
                "context": T.get_action_context(),
                "nearby": T.get_nearby_characters(),
            }

        data = await run_in_threadpool(run_in_session, entry, _snapshot)
        meta = registry.sync_meta(entry)
    return {"ok": True, "meta": meta.to_dict(), **data}


@router.patch("/worlds/{world_id}")
async def patch_world(
    payload: WorldPatchRequest,
    world_id: str,
    _: AccessDep,
    store: StoreDep,
    user_id: UserDep,
) -> dict[str, Any]:
    """重命名存档 / 切换默认流水线。"""
    try:
        meta = store.read_meta(user_id, world_id)
        if payload.name is not None:
            meta = store.rename_world(user_id, world_id, payload.name)
        if payload.pipeline is not None:
            meta.pipeline = payload.pipeline
            meta = store.write_meta(user_id, meta)
    except SessionError as exc:
        raise _guard(exc) from exc
    return {"ok": True, "world": meta.to_dict()}


@router.delete("/worlds/{world_id}")
async def delete_world(
    world_id: str, _: AccessDep, store: StoreDep, registry: RegistryDep, user_id: UserDep
) -> dict[str, Any]:
    """删除存档（连同其图片）。不可恢复。"""
    try:
        store.world_dir(user_id, world_id)
    except SessionError as exc:
        raise _guard(exc) from exc
    await registry.drop(user_id, world_id)
    store.delete_world(user_id, world_id)
    return {"ok": True, "deleted": world_id}


@router.get("/worlds/{world_id}/export")
async def export_world(_: AccessDep, registry: RegistryDep, entry: WorldDep) -> dict[str, Any]:
    """导出完整存档 JSON（可下载保存，或导入到另一台机器）。"""
    async with entry.lock:
        def _export() -> dict[str, Any]:
            state = entry.session.state
            return {
                "meta": registry.store.read_meta(entry.user_id, entry.world_id).to_dict(),
                "engine_version": state.config.get("game", {}).get("version"),
                **state.to_dict(),
            }

        return {"ok": True, "snapshot": await run_in_threadpool(run_in_session, entry, _export)}


@router.post("/worlds/import")
async def import_world(
    payload: WorldImportRequest,
    _: AccessDep,
    store: StoreDep,
    registry: RegistryDep,
    settings: SettingsDep,
    user_id: UserDep,
) -> dict[str, Any]:
    """从导出的 JSON 新建一个世界。"""
    snapshot = payload.snapshot or {}
    required = {"world", "characters", "relationships", "memories", "events", "registry"}
    missing = required - set(snapshot)
    if missing:
        raise HTTPException(status_code=400, detail=f"存档缺少字段: {', '.join(sorted(missing))}")
    try:
        meta = store.create_world(
            user_id,
            name=payload.name or (snapshot.get("meta") or {}).get("name", ""),
            pipeline=(snapshot.get("meta") or {}).get("pipeline", "multi"),
        )
    except SessionError as exc:
        raise _guard(exc) from exc

    def _write() -> None:
        session = GameSession(
            root=settings.project_root,
            data_root=store.worlds_dir(user_id) / meta.id,
            autoload=False,
        )
        for key in ("world", "characters", "relationships", "memories", "events", "registry"):
            setattr(session.state, key, snapshot.get(key) or {})
        session.state.characters.setdefault("player", {})
        session.state.characters.setdefault("npcs", {})
        session.save()

    await run_in_threadpool(_write)
    entry = await registry.acquire(user_id, meta.id)
    return {"ok": True, "world": registry.sync_meta(entry).to_dict()}


@router.get("/worlds/{world_id}/journal")
async def read_journal(
    world_id: str, _: AccessDep, store: StoreDep, user_id: UserDep, entry: WorldDep, limit: int = 60
) -> dict[str, Any]:
    """读取这个世界的叙事日志——退出重进后靠它恢复历史记录。"""
    journal = Journal(store.worlds_dir(user_id) / world_id)
    return {"ok": True, "entries": journal.read(limit=max(1, min(200, limit)))}


@router.post("/worlds/{world_id}/journal")
async def append_journal(
    payload: dict[str, Any], world_id: str, _: AccessDep, store: StoreDep, user_id: UserDep, entry: WorldDep
) -> dict[str, Any]:
    """追加一条叙事记录（浏览器直连模式下由前端写入）。"""
    journal = Journal(store.worlds_dir(user_id) / world_id)
    record = journal.append(payload or {})
    journal.compact()
    return {"ok": True, "entry": record}


# ---------------------------------------------------------------------------
# 世界内的手动快照（复用引擎的存档槽）
# ---------------------------------------------------------------------------


@router.get("/worlds/{world_id}/snapshots")
async def list_snapshots(_: AccessDep, entry: WorldDep) -> dict[str, Any]:
    """列出这个世界内部的手动存档点。"""
    async with entry.lock:
        return await run_in_threadpool(run_in_session, entry, T.list_saves)


@router.post("/worlds/{world_id}/snapshots")
async def create_snapshot(payload: SnapshotRequest, _: AccessDep, entry: WorldDep) -> dict[str, Any]:
    """在当前世界内打一个存档点。"""
    async with entry.lock:
        return await run_in_threadpool(run_in_session, entry, T.save_game, payload.slot)


@router.post("/worlds/{world_id}/restore")
async def restore_snapshot(
    payload: SnapshotRequest, _: AccessDep, registry: RegistryDep, entry: WorldDep
) -> dict[str, Any]:
    """回到某个存档点。"""
    async with entry.lock:
        result = await run_in_threadpool(run_in_session, entry, T.load_game, payload.slot)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "存档不存在"))
        await run_in_threadpool(run_in_session, entry, entry.session.save)
        registry.sync_meta(entry)
    return result
