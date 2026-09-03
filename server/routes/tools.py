"""工具调用路由 —— 前端与 Agent 共用的世界入口。

**这里是唯一能修改世界的通道。** 任何绕过 engine/tools.py 的写操作都不存在。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from engine import tools as T

from ..deps import AccessDep, RegistryDep, WorldDep
from ..schemas import BatchToolRequest, ToolCallRequest
from ..sessions import run_in_session

router = APIRouter(tags=["tools"])

#: 这些工具会换掉整个世界，需要在调用后同步 meta 并落盘
_WORLD_CHANGING = {"new_game", "load_game", "create_player", "end_turn", "save_game"}


@router.get("/worlds/{world_id}/tools")
async def list_tools(_: AccessDep, entry: WorldDep) -> dict[str, Any]:
    """列出可用工具（名称 + 首行说明）。"""
    import inspect

    return {
        "ok": True,
        "count": len(T.TOOLS),
        "tools": [
            {"name": name, "summary": (inspect.getdoc(fn) or "").splitlines()[0]}
            for name, fn in sorted(T.TOOLS.items())
        ],
    }


@router.post("/worlds/{world_id}/tools/{tool_name}")
async def call_tool(
    tool_name: str,
    payload: ToolCallRequest,
    _: AccessDep,
    registry: RegistryDep,
    entry: WorldDep,
) -> dict[str, Any]:
    """调用一个引擎工具。

    工具永不抛异常：失败时返回 ``{"ok": false, "error": ..., "hint": ...}``，
    HTTP 状态码仍是 200（除非工具名不存在）。
    """
    if tool_name not in T.TOOLS:
        raise HTTPException(status_code=404, detail=f"未知工具: {tool_name}")
    async with entry.lock:
        result = await run_in_threadpool(
            run_in_session, entry, T.call_tool, tool_name, payload.arguments
        )
        if tool_name in _WORLD_CHANGING or result.get("ok"):
            await run_in_threadpool(run_in_session, entry, entry.session.save)
            registry.sync_meta(entry)
    return result


@router.post("/worlds/{world_id}/tools")
async def call_tools_batch(
    payload: BatchToolRequest,
    _: AccessDep,
    registry: RegistryDep,
    entry: WorldDep,
) -> dict[str, Any]:
    """按顺序调用多个工具（组合行动 / 一次性拉取多份上下文）。

    ``stop_on_error=True`` 时遇到第一个失败就停止——组合行动中途情况变化，
    后面的意图本来就应该重新判断。
    """
    if not payload.calls:
        return {"ok": True, "results": []}

    def _run() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for call in payload.calls:
            result = T.call_tool(call.name, call.arguments)
            out.append({"name": call.name, "result": result})
            if payload.stop_on_error and not result.get("ok", True):
                break
        return out

    async with entry.lock:
        results = await run_in_threadpool(run_in_session, entry, _run)
        await run_in_threadpool(run_in_session, entry, entry.session.save)
        registry.sync_meta(entry)
    return {"ok": True, "results": results, "count": len(results)}
