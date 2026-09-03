"""凭据解析与适配器构造。

优先级：**玩家自带的 key** > 服务器预置的 key。
两者都没有时直接报错，绝不静默失败。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from .anthropic_adapter import AnthropicAdapter
from .base import LLMAdapter, LLMError, ResolvedProvider
from .openai_adapter import OpenAIAdapter

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-5",
}


def resolve_provider(credentials: Any, settings: Settings, *, model_override: str | None = None) -> ResolvedProvider:
    """把请求里的凭据 + 服务器配置合并成一份最终配置。

    ``credentials`` 可以是 :class:`server.schemas.LLMCredentials` 或普通 dict。
    """
    if credentials is None:
        data: dict[str, Any] = {}
    elif isinstance(credentials, dict):
        data = credentials
    else:
        data = {
            "provider": getattr(credentials, "provider", None),
            "base_url": getattr(credentials, "base_url", None),
            "api_key": getattr(credentials, "api_key", None),
            "model": getattr(credentials, "model", None),
            "extra_headers": getattr(credentials, "extra_headers", None) or {},
            "extra_params": getattr(credentials, "extra_params", None) or {},
        }

    user_key = (data.get("api_key") or "").strip()
    if user_key:
        provider = (data.get("provider") or settings.llm.provider or "openai").strip().lower()
        source = "user"
        api_key = user_key
        base_url = (data.get("base_url") or "").strip() or DEFAULT_BASE_URLS.get(provider, "")
        model = (model_override or data.get("model") or "").strip() or DEFAULT_MODELS.get(provider, "")
    elif settings.llm.configured:
        provider = (data.get("provider") or settings.llm.provider or "openai").strip().lower()
        source = "server"
        api_key = settings.llm.api_key
        base_url = (data.get("base_url") or settings.llm.base_url or "").strip() or DEFAULT_BASE_URLS.get(provider, "")
        model = (model_override or data.get("model") or settings.llm.model or "").strip() or DEFAULT_MODELS.get(provider, "")
    else:
        raise LLMError(
            "没有可用的 API key。请在设置里填入你自己的 key，或让服务器管理员配置 AKIZUKI_LLM_API_KEY。",
            status_code=400,
        )

    if provider not in ("openai", "anthropic"):
        raise LLMError(f"不支持的 provider: {provider}（可用: openai, anthropic）", status_code=400)
    if not base_url:
        raise LLMError("缺少 base_url", status_code=400)
    if not model:
        raise LLMError("缺少模型名", status_code=400)

    headers = data.get("extra_headers") or {}
    # 服务器预置的 extra_params 打底，用户传的覆盖
    params = {**(settings.llm.extra_params or {}), **(data.get("extra_params") or {})}
    return ResolvedProvider(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        source=source,
        extra_headers={str(k): str(v) for k, v in headers.items()},
        extra_params=params,
    )


def build_adapter(config: ResolvedProvider, settings: Settings) -> LLMAdapter:
    if config.provider == "anthropic":
        return AnthropicAdapter(config, timeout=settings.request_timeout)
    return OpenAIAdapter(config, timeout=settings.request_timeout)
