"""请求 / 响应模型。所有来自前端的输入都先过 pydantic，绝不直接喂给引擎。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionRequest(BaseModel):
    user_id: str | None = Field(default=None, description="已有令牌；无效或缺省则新建用户")
    display_name: str = ""


class SessionResponse(BaseModel):
    ok: bool = True
    user_id: str
    display_name: str = ""
    created: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    worlds: list[dict[str, Any]] = Field(default_factory=list)


class UserSettingsRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class WorldCreateRequest(BaseModel):
    name: str = ""
    seed: int | None = None
    pipeline: str = "multi"


class WorldPatchRequest(BaseModel):
    name: str | None = None
    pipeline: str | None = None


class WorldImportRequest(BaseModel):
    name: str = ""
    snapshot: dict[str, Any]


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class BatchToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class BatchToolRequest(BaseModel):
    calls: list[BatchToolCall] = Field(default_factory=list, max_length=32)
    stop_on_error: bool = False


class SnapshotRequest(BaseModel):
    slot: str = "save_001"


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


class LLMCredentials(BaseModel):
    """前端可以自带 key；留空则使用服务器预置（若已配置且口令通过）。"""

    provider: Literal["openai", "anthropic"] | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Any
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class LLMRequest(BaseModel):
    messages: list[LLMMessage]
    credentials: LLMCredentials = Field(default_factory=LLMCredentials)
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False


# ---------------------------------------------------------------------------
# 回合 / 流水线
# ---------------------------------------------------------------------------


class TurnRequest(BaseModel):
    input: str = Field(default="", max_length=4000)
    pipeline: str | None = None
    credentials: LLMCredentials = Field(default_factory=LLMCredentials)
    stream: bool = False
    debug: bool = False
    generate_images: bool | None = None
    stage_models: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 图像
# ---------------------------------------------------------------------------


class ImageCredentials(BaseModel):
    provider: Literal["openai", "custom"] | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    size: str | None = None
    request_template: str | None = None
    response_path: str | None = None
    style: str | None = None
    sfw: bool | None = None


class ImageRequest(BaseModel):
    kind: Literal["avatar", "portrait", "scene", "cg"]
    subject_id: str = Field(default="", max_length=80, description="NPC id / 地点 id / 事件 id")
    prompt_extra: str = Field(default="", max_length=1000)
    credentials: ImageCredentials = Field(default_factory=ImageCredentials)
    force: bool = False


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    hint: str = ""
