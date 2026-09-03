"""LLM 适配层：对上暴露统一接口，对下同时兼容 OpenAI 与 Anthropic 两种格式。"""

from .base import (
    LLMAdapter,
    LLMError,
    Message,
    ResolvedProvider,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Usage,
    LLMResult,
)
from .client import build_adapter, resolve_provider

__all__ = [
    "LLMAdapter", "LLMError", "Message", "ResolvedProvider", "StreamEvent",
    "ToolCall", "ToolSpec", "Usage", "LLMResult", "build_adapter", "resolve_provider",
]
