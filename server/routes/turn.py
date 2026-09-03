"""回合路由：跑多 Agent 流水线。

* ``POST /api/worlds/{id}/turn``        一次性返回整回合结果
* ``POST /api/worlds/{id}/turn/stream`` SSE 流式（阶段进度 + 逐字叙事 + 工具日志）

两条路径跑的是**同一个执行器**，只是消费方式不同。
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from engine import tools as T

from ..agents.pipeline import load_pipeline
from ..agents.runner import PipelineRunner
from ..journal import Journal
from ..config import Settings
from ..deps import AccessDep, RegistryDep, SettingsDep, WorldDep
from ..llm import LLMError, resolve_provider
from ..schemas import TurnRequest
from ..sessions import SessionRegistry, WorldSession, run_in_session

log = logging.getLogger("server.routes.turn")

router = APIRouter(tags=["turn"])


def _record(entry: WorldSession, registry: SessionRegistry, payload: TurnRequest, turn: dict[str, Any]) -> None:
    """把这一回合的叙事落盘，退出重进后还能读到。"""
    world = turn.get("world") or {}
    Journal(registry.store.worlds_dir(entry.user_id) / entry.world_id).append(
        {
            "turn": turn.get("turn"),
            "date": world.get("date"),
            "time": world.get("time"),
            "location": ((world.get("location") or {}).get("name")),
            "pipeline": turn.get("pipeline"),
            "playerInput": payload.input,
            "narration": turn.get("narration_clean") or turn.get("narration"),
            "dialogue": turn.get("dialogue"),
            "checkText": turn.get("check_text"),
            "growthText": turn.get("growth_text"),
            "recommendations": turn.get("recommendations"),
            "randomEvent": turn.get("random_event"),
            "usage": turn.get("usage"),
            "errors": turn.get("stage_errors"),
        }
    )


def _build_runner(
    entry: WorldSession,
    settings: Settings,
    payload: TurnRequest,
    registry: SessionRegistry,
) -> PipelineRunner:
    pipeline_id = payload.pipeline or registry.store.read_meta(entry.user_id, entry.world_id).pipeline or "multi"
    try:
        pipeline = load_pipeline(settings.project_root, pipeline_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        return await run_in_threadpool(run_in_session, entry, T.call_tool, name, arguments)

    def snapshot() -> dict[str, Any]:
        def _read() -> dict[str, Any]:
            return {
                "world_state": T.get_world_state(),
                "player": T.get_player_state(),
                "context": T.get_action_context(),
                "agent_md": entry.session.state.static.get("doc_agent", ""),
            }

        return run_in_session(entry, _read)

    # 先校验凭据：没有可用 key 时立刻失败，而不是让每个阶段各报一次错
    try:
        resolve_provider(payload.credentials, settings)
    except LLMError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    images_enabled = (
        settings.image_enabled if payload.generate_images is None else bool(payload.generate_images)
    )
    return PipelineRunner(
        pipeline=pipeline,
        settings=settings,
        credentials=payload.credentials,
        call_tool=call_tool,
        snapshot_fn=snapshot,
        stage_models=payload.stage_models,
        images_enabled=images_enabled,
        debug=payload.debug,
    )


@router.post("/worlds/{world_id}/turn")
async def run_turn(
    payload: TurnRequest,
    _: AccessDep,
    settings: SettingsDep,
    registry: RegistryDep,
    entry: WorldDep,
) -> dict[str, Any]:
    """跑完一个回合并一次性返回结果（非流式）。"""
    runner = _build_runner(entry, settings, payload, registry)
    async with entry.lock:
        result: dict[str, Any] | None = None
        errors: list[str] = []
        try:
            async for event in runner.run(payload.input, stream=False):
                if event["type"] == "turn_end":
                    result = event["turn"]
                elif event["type"] == "stage_error":
                    errors.append(f"{event.get('stage')}: {event.get('message')}")
        except LLMError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        await run_in_threadpool(run_in_session, entry, entry.session.save)
        registry.sync_meta(entry)
    if result is None:
        raise HTTPException(status_code=500, detail="流水线没有产出结果")
    if errors:
        result["stage_errors"] = errors
    _record(entry, registry, payload, result)
    return result


@router.post("/worlds/{world_id}/turn/stream")
async def stream_turn(
    payload: TurnRequest,
    _: AccessDep,
    settings: SettingsDep,
    registry: RegistryDep,
    entry: WorldDep,
) -> StreamingResponse:
    """SSE 流式回合。事件类型：

    ``turn_start`` / ``stage_start`` / ``delta`` / ``tool_call`` / ``tool_result`` /
    ``dialogue`` / ``subject_start`` / ``stage_end`` / ``stage_error`` / ``turn_end``
    """
    runner = _build_runner(entry, settings, payload, registry)

    async def events() -> AsyncIterator[bytes]:
        async with entry.lock:
            try:
                async for event in runner.run(payload.input, stream=True):
                    if event["type"] == "turn_end":
                        _record(entry, registry, payload, event.get("turn") or {})
                    yield _sse(event["type"], event)
            except LLMError as exc:
                yield _sse("error", exc.to_dict())
            except Exception as exc:  # noqa: BLE001
                log.exception("turn stream crashed")
                yield _sse("error", {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            finally:
                try:
                    await run_in_threadpool(run_in_session, entry, entry.session.save)
                    registry.sync_meta(entry)
                except Exception as exc:  # noqa: BLE001
                    log.warning("turn 收尾保存失败: %s", exc)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = {k: v for k, v in data.items() if v is not None}
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n".encode("utf-8")
