"""OpenAI 兼容格式适配器（``POST {base_url}/chat/completions``）。

只要端点遵循 OpenAI 的请求 / 响应结构就能用：官方 API、Azure 风格网关、
各类中转站、本地 vLLM / Ollama 的 OpenAI 兼容层。
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from .base import LLMError, LLMResult, Message, ResolvedProvider, StreamEvent, ToolCall, ToolSpec, Usage, safe_json_loads

log = logging.getLogger("server.llm.openai")


class OpenAIAdapter:
    provider = "openai"

    def __init__(self, config: ResolvedProvider, timeout: float = 180.0) -> None:
        self.config = config
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        headers.update(self.config.extra_headers)
        return headers

    def _url(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

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
        if system:
            wire.append({"role": "system", "content": system})
        for message in messages:
            if message.role == "tool":
                wire.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id or "",
                        "content": message.content,
                    }
                )
            elif message.role == "assistant" and message.tool_calls:
                wire.append(
                    {
                        "role": "assistant",
                        "content": message.content or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )
            else:
                wire.append({"role": message.role, "content": message.content})

        payload: dict[str, Any] = {"model": self.config.model, "messages": wire}
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
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
            raise LLMError(
                _describe_error(response), provider=self.provider, status_code=response.status_code
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("上游返回的不是 JSON", provider=self.provider) from exc
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> LLMResult:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("上游没有返回任何结果", provider=self.provider, raw=data)
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # 某些网关返回块数组
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            calls.append(
                ToolCall(
                    id=str(call.get("id") or f"call_{index}"),
                    name=str(function.get("name", "")),
                    arguments=safe_json_loads(function.get("arguments")),
                )
            )
        usage_raw = data.get("usage") or {}
        return LLMResult(
            text=content or "",
            tool_calls=calls,
            usage=Usage(
                int(usage_raw.get("prompt_tokens", 0) or 0),
                int(usage_raw.get("completion_tokens", 0) or 0),
            ),
            stop_reason=str(choices[0].get("finish_reason") or ""),
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
        pending: dict[int, dict[str, Any]] = {}
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
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            data = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        if data.get("usage"):
                            usage = Usage(
                                int(data["usage"].get("prompt_tokens", 0) or 0),
                                int(data["usage"].get("completion_tokens", 0) or 0),
                            )
                        for choice in data.get("choices") or []:
                            delta = choice.get("delta") or {}
                            text = delta.get("content")
                            if text:
                                yield StreamEvent(type="text", text=text)
                            for call in delta.get("tool_calls") or []:
                                index = int(call.get("index", 0))
                                slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
                                if call.get("id"):
                                    slot["id"] = call["id"]
                                function = call.get("function") or {}
                                if function.get("name"):
                                    slot["name"] = function["name"]
                                if function.get("arguments"):
                                    slot["arguments"] += function["arguments"]
            except httpx.HTTPError as exc:
                raise LLMError(f"连接上游失败: {exc}", provider=self.provider, status_code=504) from exc

        for index in sorted(pending):
            slot = pending[index]
            if slot["name"]:
                yield StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(
                        id=slot["id"] or f"call_{index}",
                        name=slot["name"],
                        arguments=safe_json_loads(slot["arguments"]),
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
