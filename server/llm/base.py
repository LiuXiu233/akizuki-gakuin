"""统一的 LLM 数据模型。

两种上游格式（OpenAI `/chat/completions` 与 Anthropic `/v1/messages`）
在这里被抹平成同一套 Message / ToolSpec / ToolCall / LLMResult。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


class LLMError(Exception):
    """上游调用失败。``status_code`` 会被路由透传给前端。"""

    def __init__(self, message: str, *, status_code: int = 502, provider: str = "", raw: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": str(self), "provider": self.provider, "status": self.status_code}


@dataclass(slots=True)
class ToolCall:
    """模型请求调用一个工具。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(slots=True)
class Message:
    """统一消息。``tool_results`` 用于把工具结果回传给模型。"""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(role="assistant", content=content, tool_calls=list(tool_calls or []))

    @classmethod
    def tool_result(cls, tool_call_id: str, name: str, result: Any) -> "Message":
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return cls(role="tool", content=text, tool_call_id=tool_call_id, name=name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }


@dataclass(slots=True)
class ToolSpec:
    """工具定义。直接由 engine.tools.tool_schemas() 转换而来。"""

    name: str
    description: str
    input_schema: dict[str, Any]

    @classmethod
    def from_engine(cls, schema: dict[str, Any]) -> "ToolSpec":
        description = str(schema.get("description", ""))
        # 工具文档很长，截断以控制 token；前几行已经包含关键约束
        if len(description) > 700:
            description = description[:700].rstrip() + "…"
        return cls(name=schema["name"], description=description, input_schema=schema["input_schema"])


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }


@dataclass(slots=True)
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "usage": self.usage.to_dict(),
            "stop_reason": self.stop_reason,
            "model": self.model,
        }


@dataclass(slots=True)
class StreamEvent:
    """流式事件。``type``: text | tool_call | usage | done | error"""

    type: str
    text: str = ""
    tool_call: ToolCall | None = None
    usage: Usage | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type}
        if self.text:
            payload["text"] = self.text
        if self.tool_call:
            payload["tool_call"] = self.tool_call.to_dict()
        if self.usage:
            payload["usage"] = self.usage.to_dict()
        if self.message:
            payload["message"] = self.message
        return payload


@dataclass(slots=True)
class ResolvedProvider:
    """一次调用最终使用的上游配置。**日志与响应中绝不能出现 api_key。**"""

    provider: str
    base_url: str
    api_key: str
    model: str
    source: str = "user"          # user | server
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: 透传进请求体的额外字段（各家私有开关，如 reasoning_effort）
    extra_params: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "source": self.source,
            "extra_params": self.extra_params,
        }


class LLMAdapter(Protocol):
    """适配器接口。"""

    provider: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> LLMResult: ...

    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...


def safe_json_loads(raw: Any) -> dict[str, Any]:
    """模型给的参数经常不是严格 JSON——尽量救回来，救不回就返回空 dict。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    return value if isinstance(value, dict) else {}
