"""LLM 代理路由。

存在的意义有三个：
1. 让玩家在不暴露 key 给浏览器的前提下使用服务器预置的 key；
2. 绕开中转站不开 CORS 的问题；
3. 抹平 OpenAI / Anthropic 两种格式，前端只需要一种调用方式。

前端也可以完全不用它（浏览器直连模式），那时这个路由不会被访问。
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..deps import AccessDep, SettingsDep
from ..llm import LLMError, Message, ToolSpec, build_adapter, resolve_provider
from ..schemas import LLMRequest

log = logging.getLogger("server.routes.llm")

router = APIRouter(tags=["llm"], prefix="/llm")


def _to_messages(payload: LLMRequest) -> list[Message]:
    messages: list[Message] = []
    for item in payload.messages:
        content = item.content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        messages.append(
            Message(
                role=item.role,
                content=content,
                tool_call_id=item.tool_call_id,
                name=item.name,
            )
        )
    return messages


@router.post("/chat")
async def chat(payload: LLMRequest, _: AccessDep, settings: SettingsDep) -> Any:
    """统一的对话补全接口。``stream=true`` 时返回 SSE。"""
    try:
        config = resolve_provider(payload.credentials, settings)
    except LLMError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    adapter = build_adapter(config, settings)
    tools = [ToolSpec(t["name"], t.get("description", ""), t.get("input_schema") or t.get("parameters") or {})
             for t in (payload.tools or [])]
    messages = _to_messages(payload)

    if not payload.stream:
        try:
            result = await adapter.complete(
                system=payload.system or "",
                messages=messages,
                tools=tools or None,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            )
        except LLMError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return {"ok": True, "provider": config.public(), **result.to_dict()}

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            async for event in adapter.stream(
                system=payload.system or "",
                messages=messages,
                tools=tools or None,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            ):
                yield _sse(event.type, event.to_dict())
        except LLMError as exc:
            yield _sse("error", exc.to_dict())

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/verify")
async def verify(payload: LLMRequest, _: AccessDep, settings: SettingsDep) -> dict[str, Any]:
    """连通性自检：用最小的请求确认 key / base_url / model 是否可用。"""
    try:
        config = resolve_provider(payload.credentials, settings)
        adapter = build_adapter(config, settings)
        result = await adapter.complete(
            system="你是一个连通性测试端点。",
            messages=[Message.user("回复两个字：可用")],
            max_tokens=16,
        )
    except LLMError as exc:
        return {"ok": False, "error": str(exc), "status": exc.status_code}
    return {"ok": True, "provider": config.public(), "reply": result.text.strip()[:50],
            "usage": result.usage.to_dict()}


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
