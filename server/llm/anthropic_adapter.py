"""Anthropic Messages API 适配器（``POST {base_url}/v1/messages``）。"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from .base import LLMError, LLMResult, Message, ResolvedProvider, StreamEvent, ToolCall, ToolSpec, Usage, safe_json_loads

log = logging.getLogger("server.llm.anthropic")

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(self, config: ResolvedProvider, timeout: float = 180.0) -> None:
        self.config = config
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        headers.update(self.config.extra_headers)
        return headers

    def _url(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/messages"):
            return base
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"

    def _payload(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        wire: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "",
                    "content": message.content,
                }
                if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list):
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
            elif message.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    blocks.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                    )
                wire.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            else:
                wire.append({"role": "user", "content": message.content})

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": wire,
            "max_tokens": int(max_tokens or DEFAULT_MAX_TOKENS),
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                for tool in tools
            ]
        if temperature is not None:
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
        return payload

    # ------------------------------------------------------------------
    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        payload = self._payload(system, messages, tools, temperature, max_tokens, stream=False)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(self._url(), headers=self._headers(), json=payload)
            except httpx.HTTPError as exc:
                raise LLMError(f"连接上游失败: {exc}", provider=self.provider, status_code=504) from exc
        if response.status_code >= 400:
            raise LLMError(_describe_error(response), provider=self.provider, status_code=response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("上游返回的不是 JSON", provider=self.provider) from exc
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> LLMResult:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=block.get("input") if isinstance(block.get("input"), dict) else {},
                    )
                )
        usage_raw = data.get("usage") or {}
        return LLMResult(
            text="".join(text_parts),
            tool_calls=calls,
            usage=Usage(
                int(usage_raw.get("input_tokens", 0) or 0),
                int(usage_raw.get("output_tokens", 0) or 0),
            ),
            stop_reason=str(data.get("stop_reason") or ""),
            model=str(data.get("model") or self.config.model),
        )

    # ------------------------------------------------------------------
    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload = self._payload(system, messages, tools, temperature, max_tokens, stream=True)
        blocks: dict[int, dict[str, Any]] = {}
        usage = Usage()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                async with client.stream(
                    "POST", self._url(), headers=self._headers(), json=payload
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        raise LLMError(body[:500] or "上游返回错误", provider=self.provider,
                                       status_code=response.status_code)
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        try:
                            data = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        kind = data.get("type")
                        if kind == "content_block_start":
                            block = data.get("content_block") or {}
                            blocks[int(data.get("index", 0))] = {
                                "type": block.get("type"),
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "json": "",
                            }
                        elif kind == "content_block_delta":
                            delta = data.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                yield StreamEvent(type="text", text=delta.get("text", ""))
                            elif delta.get("type") == "input_json_delta":
                                slot = blocks.setdefault(
                                    int(data.get("index", 0)), {"type": "tool_use", "id": "", "name": "", "json": ""}
                                )
                                slot["json"] += delta.get("partial_json", "")
                        elif kind == "message_delta":
                            delta_usage = data.get("usage") or {}
                            usage = Usage(usage.input_tokens, int(delta_usage.get("output_tokens", 0) or 0))
                        elif kind == "message_start":
                            start_usage = (data.get("message") or {}).get("usage") or {}
                            usage = Usage(int(start_usage.get("input_tokens", 0) or 0), usage.output_tokens)
                        elif kind == "error":
                            raise LLMError(
                                str((data.get("error") or {}).get("message", "上游错误")),
                                provider=self.provider,
                            )
            except httpx.HTTPError as exc:
                raise LLMError(f"连接上游失败: {exc}", provider=self.provider, status_code=504) from exc

        for index in sorted(blocks):
            slot = blocks[index]
            if slot.get("type") == "tool_use" and slot.get("name"):
                yield StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(
                        id=slot.get("id") or f"call_{index}",
                        name=slot["name"],
                        arguments=safe_json_loads(slot.get("json")),
                    ),
                )
        yield StreamEvent(type="usage", usage=usage)
        yield StreamEvent(type="done")


def _describe_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"上游 {response.status_code}: {response.text[:300]}"
    error = data.get("error")
    if isinstance(error, dict):
        return f"上游 {response.status_code}: {error.get('message') or error}"
    return f"上游 {response.status_code}: {str(data)[:300]}"
