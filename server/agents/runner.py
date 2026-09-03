"""流水线执行器。

一次「回合」= 按 YAML 定义依次跑若干阶段，每个阶段是一次（或多次）模型调用，
阶段之间通过黑板（``TurnContext``）传递结构化结果。

**引擎始终是权威**：面板、判定文本、成长文本、世界快照全部在收尾时
直接从工具重新取一遍，不采信模型的复述。

执行器本身永远是流式的（``run()`` 是 async generator）；
非流式接口只是把事件流抽干、取最后的 ``turn_end``。
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterable

from engine import tools as T

from ..config import Settings
from ..llm import LLMAdapter, LLMError, Message, ToolSpec, build_adapter, resolve_provider
from ..llm.base import LLMResult, Usage
from .prompts import (
    STAGE_OUTPUT_HINTS,
    build_npc_brief,
    build_system_prompt,
    build_world_brief,
    parse_json_output,
)

log = logging.getLogger("server.agents.runner")

#: 这些工具会改变世界，NPC 扮演阶段永远拿不到它们
WRITE_TOOLS = {
    "perform_action", "resolve_check", "advance_time", "sleep", "move_character",
    "buy_item", "apply_relationship_event", "add_memory", "roll_random_event",
    "trigger_event", "simulate_background_world", "create_npc", "promote_npc",
    "check_npc_promotions", "register_skill", "register_knowledge", "register_location",
    "register_group", "add_dynamic_interest", "end_turn", "save_game", "load_game",
    "new_game", "create_player", "join_club", "leave_club", "record_recommendations",
}

#: 无论流水线怎么配置，这些工具都不许出现在 Agent 手里（会毁掉存档）
FORBIDDEN_TOOLS = {"new_game", "load_game"}


@dataclass
class StageResult:
    id: str
    name: str
    role: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    duration_ms: int = 0
    error: str = ""
    calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "role": self.role,
            "text": self.text, "data": self.data,
            "usage": self.usage.to_dict(), "duration_ms": self.duration_ms,
            "error": self.error, "calls": self.calls,
        }


@dataclass
class TurnContext:
    """阶段之间共享的黑板。"""

    player_input: str
    snapshot: dict[str, Any]
    world_brief: str = ""
    stages: dict[str, StageResult] = field(default_factory=dict)
    dialogue: list[dict[str, Any]] = field(default_factory=list)
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    def produced(self, key: str) -> Any:
        """取某类产出（plan / facts / narration / recommendations / dialogue / images）。"""
        if key == "dialogue":
            return self.dialogue
        for result in self.stages.values():
            if key not in (result.data.get("__produces__") or []):
                continue
            if key == "narration":
                return result.text
            if key in result.data:
                return result.data[key]
            payload = {k: v for k, v in result.data.items() if k != "__produces__"}
            return payload or result.text
        return None


def _summarize_tool_result(name: str, result: Any) -> str:
    """给事件流用的一句话摘要（避免把整个 JSON 塞进前端日志）。"""
    if not isinstance(result, dict):
        return str(result)[:200]
    if not result.get("ok", True):
        return f"失败：{result.get('error', '')}"[:200]
    if name == "perform_action":
        check = (result.get("check") or {}).get("result")
        return f"已结算 {result.get('action_type')}" + (f"（{check}）" if check else "")
    if name in ("npc_decide_invitation", "npc_decide_confession"):
        decision = result.get("decision") or ("接受" if result.get("accepted") else "拒绝")
        return f"{result.get('npc_id')}：{decision}（{result.get('reason_code', '')}）"
    if name == "end_turn":
        event = (result.get("random_event") or {}).get("name")
        return f"回合 {result.get('turn')} 收尾" + (f"，事件：{event}" if event else "")
    if name == "resolve_check":
        return f"{result.get('result_label', '')}（{result.get('total')} vs DC {result.get('dc')}）"
    keys = [k for k in result if k not in ("ok", "note", "reminder")][:3]
    return "、".join(str(k) for k in keys) or "ok"


class PipelineRunner:
    """按流水线定义跑完一个回合。"""

    def __init__(
        self,
        *,
        pipeline: dict[str, Any],
        settings: Settings,
        credentials: Any,
        call_tool: Callable[[str, dict[str, Any]], Any],
        snapshot_fn: Callable[[], dict[str, Any]],
        stage_models: dict[str, str] | None = None,
        adapter_factory: Callable[[str], LLMAdapter] | None = None,
        images_enabled: bool = False,
        debug: bool = False,
    ) -> None:
        self.pipeline = pipeline
        self.settings = settings
        self.credentials = credentials
        self.call_tool = call_tool
        self.snapshot_fn = snapshot_fn
        self.stage_models = stage_models or {}
        self.adapter_factory = adapter_factory
        self.images_enabled = images_enabled
        self.debug = debug
        self._schemas = {schema["name"]: schema for schema in T.tool_schemas()}
        #: 引擎在本回合产生的权威文本（判定 / 成长），由工具返回值直接捕获
        self._check_texts: list[str] = []
        self._growth_texts: list[str] = []
        self._random_events: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 适配器
    # ------------------------------------------------------------------
    def _adapter(self, stage: dict[str, Any]) -> LLMAdapter:
        model = self.stage_models.get(stage["id"]) or stage.get("model")
        if self.adapter_factory is not None:
            return self.adapter_factory(model or "")
        config = resolve_provider(self.credentials, self.settings, model_override=model)
        return build_adapter(config, self.settings)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _tools_for(self, stage: dict[str, Any]) -> list[ToolSpec]:
        patterns: Iterable[str] = stage.get("tools") or []
        if not patterns:
            return []
        allowed: list[str] = []
        for name in sorted(self._schemas):
            if name in FORBIDDEN_TOOLS:
                continue
            if stage.get("role") == "npc" and name in WRITE_TOOLS:
                continue
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                allowed.append(name)
        return [ToolSpec.from_engine(self._schemas[name]) for name in allowed]

    async def _execute_tool(self, stage_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name in FORBIDDEN_TOOLS:
            return {"ok": False, "error": f"{name} 在回合中不可用（会重置或替换整个存档）"}
        try:
            result = await self.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - 工具层不应把异常抛给模型
            log.exception("tool %s failed", name)
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(result, dict):
            result = {"ok": True, "value": result}
        # 捕获引擎产生的权威文本，避免模型复述数值
        if result.get("check_text"):
            self._check_texts.append(str(result["check_text"]))
        if result.get("growth_text"):
            self._growth_texts.append(str(result["growth_text"]))
        if name == "resolve_check" and result.get("text"):
            self._check_texts.append(str(result["text"]))
        if name in ("end_turn", "roll_random_event") and result.get("random_event"):
            self._random_events.append(result["random_event"])
        elif name == "roll_random_event" and result.get("event"):
            self._random_events.append(result["event"])
        return result

    # ------------------------------------------------------------------
    # 单次模型调用（带工具循环）
    # ------------------------------------------------------------------
    async def _call_stage_model(
        self,
        stage: dict[str, Any],
        system: str,
        user_content: str,
        *,
        stream: bool,
        subject: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """产出事件；最后一个事件是 ``{"type": "__result__", ...}``。"""
        adapter = self._adapter(stage)
        tools = self._tools_for(stage)
        messages: list[Message] = [Message.user(user_content)]
        max_iterations = int(stage.get("max_tool_iterations") or self.pipeline.get("max_tool_iterations", 6))
        temperature = stage.get("temperature")
        max_tokens = stage.get("max_tokens")

        total = Usage()
        final_text = ""
        calls = 0

        for iteration in range(max_iterations):
            result: LLMResult
            if stream:
                text_parts: list[str] = []
                tool_calls = []
                usage = Usage()
                async for event in adapter.stream(
                    system=system, messages=messages, tools=tools or None,
                    temperature=temperature, max_tokens=max_tokens,
                ):
                    if event.type == "text" and event.text:
                        text_parts.append(event.text)
                        yield {
                            "type": "delta", "stage": stage["id"], "subject": subject,
                            "iteration": iteration, "text": event.text,
                        }
                    elif event.type == "tool_call" and event.tool_call:
                        tool_calls.append(event.tool_call)
                    elif event.type == "usage" and event.usage:
                        usage = event.usage
                result = LLMResult(text="".join(text_parts), tool_calls=tool_calls, usage=usage)
            else:
                result = await adapter.complete(
                    system=system, messages=messages, tools=tools or None,
                    temperature=temperature, max_tokens=max_tokens,
                )
            total = total.add(result.usage)

            if not result.tool_calls:
                final_text = result.text
                break

            messages.append(Message.assistant(result.text, result.tool_calls))
            for call in result.tool_calls:
                calls += 1
                yield {
                    "type": "tool_call", "stage": stage["id"], "subject": subject,
                    "name": call.name, "arguments": call.arguments,
                }
                tool_result = await self._execute_tool(stage["id"], call.name, call.arguments)
                yield {
                    "type": "tool_result", "stage": stage["id"], "subject": subject,
                    "name": call.name, "ok": bool(tool_result.get("ok", True)),
                    "summary": _summarize_tool_result(call.name, tool_result),
                    "result": tool_result if self.debug else None,
                }
                messages.append(Message.tool_result(call.id, call.name, tool_result))
            final_text = result.text
        else:
            log.warning("stage %s 到达工具调用上限", stage["id"])

        yield {"type": "__result__", "text": final_text, "usage": total, "calls": calls}

    # ------------------------------------------------------------------
    # 阶段
    # ------------------------------------------------------------------
    def _stage_user_content(self, stage: dict[str, Any], ctx: TurnContext) -> str:
        parts = [f"## 玩家这一回合的输入\n\n{ctx.player_input or '（玩家没有明确输入，只是让时间往前走一点）'}"]
        for key in stage.get("inputs") or []:
            value = ctx.produced(key)
            if not value:
                continue
            if key == "dialogue":
                lines = [f"{item['name']}：{item['text']}" for item in value]
                parts.append("## 在场角色刚才说了什么\n\n" + "\n".join(lines))
            elif isinstance(value, str):
                parts.append(f"## 前序阶段：{key}\n\n{value}")
            else:
                parts.append(f"## 前序阶段：{key}\n\n{json.dumps(value, ensure_ascii=False, indent=2)}")
        return "\n\n".join(parts)

    async def _run_simple_stage(
        self, stage: dict[str, Any], ctx: TurnContext, *, stream: bool
    ) -> AsyncIterator[dict[str, Any]]:
        started = time.monotonic()
        agent_md = ""
        if stage.get("include_agent_md"):
            agent_md = (self.snapshot_fn().get("agent_md") or "")
        system = build_system_prompt(
            role=stage.get("role", "main"),
            stage_prompt=stage.get("prompt", ""),
            world_brief=ctx.world_brief,
            agent_md=agent_md,
            output_hint=STAGE_OUTPUT_HINTS.get(stage.get("output_hint", ""), ""),
        )
        result = StageResult(id=stage["id"], name=stage.get("name", stage["id"]), role=stage.get("role", "main"))
        text = ""
        try:
            async for event in self._call_stage_model(
                stage, system, self._stage_user_content(stage, ctx),
                stream=stream and bool(stage.get("produces") and "narration" in stage["produces"]),
            ):
                if event["type"] == "__result__":
                    text = event["text"]
                    result.usage = event["usage"]
                    result.calls = event["calls"]
                else:
                    if event["type"] in ("tool_call", "tool_result"):
                        ctx.tool_log.append({k: v for k, v in event.items() if k != "result"})
                    yield event
        except LLMError as exc:
            result.error = str(exc)
            log.warning("stage %s 失败: %s", stage["id"], exc)
            yield {"type": "stage_error", "stage": stage["id"], "message": str(exc)}

        result.text = text.strip()
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if stage.get("output") == "json":
            result.data = parse_json_output(text)
        produces = stage.get("produces") or []
        result.data["__produces__"] = produces
        if "recommendations" in produces and not result.data.get("recommendations"):
            result.data["recommendations"] = _parse_recommendations(text)
        if "images" in produces:
            result.data.setdefault("images", [])
        ctx.stages[stage["id"]] = result
        ctx.usage = ctx.usage.add(result.usage)
        yield {"type": "stage_end", "stage": stage["id"], "result": result.to_dict()}

    async def _run_npc_stage(
        self, stage: dict[str, Any], ctx: TurnContext
    ) -> AsyncIterator[dict[str, Any]]:
        """并行地让每个在场 NPC 用自己的视角开口。"""
        started = time.monotonic()
        nearby = (ctx.snapshot.get("context") or {}).get("nearby_characters") or []
        limit = int(stage.get("max_subjects", 3))
        subjects = [npc["id"] for npc in nearby][:limit]
        result = StageResult(id=stage["id"], name=stage.get("name", stage["id"]), role="npc")

        if not subjects:
            result.duration_ms = int((time.monotonic() - started) * 1000)
            result.data = {"__produces__": stage.get("produces") or [], "dialogue": []}
            ctx.stages[stage["id"]] = result
            yield {"type": "stage_end", "stage": stage["id"], "result": result.to_dict(),
                   "note": "这里没有别人"}
            return

        facts = ctx.produced("facts")
        facts_text = json.dumps(facts, ensure_ascii=False) if isinstance(facts, (dict, list)) else str(facts or "")

        async def one(npc_id: str) -> dict[str, Any]:
            character = await self.call_tool("get_character_state", {"character_id": npc_id, "include_hidden": True})
            relationship = await self.call_tool("get_relationship", {"actor_id": npc_id, "target_id": "player"})
            memories = await self.call_tool(
                "get_relevant_memories", {"character_id": npc_id, "participants": ["player"], "limit": 6}
            )
            brief = build_npc_brief(character, relationship, memories.get("memories") or [])
            system = build_system_prompt(
                role="npc",
                stage_prompt=stage.get("prompt", ""),
                world_brief=ctx.world_brief,
                extra=brief,
            )
            user = (
                f"## 玩家做了 / 说了什么\n\n{ctx.player_input}\n\n"
                f"## 这一回合已经确定发生的事（引擎结算结果）\n\n{facts_text or '（没有特别的事）'}\n\n"
                "现在轮到你反应。如果你根本不会理会，就用一个动作表示（比如继续做自己的事）。"
            )
            adapter = self._adapter(stage)
            try:
                completion = await adapter.complete(
                    system=system, messages=[Message.user(user)],
                    temperature=stage.get("temperature"), max_tokens=stage.get("max_tokens"),
                )
            except LLMError as exc:
                return {"npc_id": npc_id, "name": character.get("name", npc_id), "text": "",
                        "error": str(exc), "usage": Usage()}
            return {
                "npc_id": npc_id,
                "name": character.get("name", npc_id),
                "text": completion.text.strip(),
                "usage": completion.usage,
            }

        for npc_id in subjects:
            yield {"type": "subject_start", "stage": stage["id"], "subject": npc_id}
        outcomes = await asyncio.gather(*(one(npc_id) for npc_id in subjects), return_exceptions=True)

        dialogue: list[dict[str, Any]] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                log.warning("NPC 阶段失败: %s", outcome)
                continue
            result.usage = result.usage.add(outcome.get("usage") or Usage())
            if outcome.get("text"):
                entry = {"npc_id": outcome["npc_id"], "name": outcome["name"], "text": outcome["text"]}
                dialogue.append(entry)
                yield {"type": "dialogue", "stage": stage["id"], **entry}
            elif outcome.get("error"):
                yield {"type": "stage_error", "stage": stage["id"], "subject": outcome["npc_id"],
                       "message": outcome["error"]}

        ctx.dialogue.extend(dialogue)
        result.data = {"__produces__": stage.get("produces") or [], "dialogue": dialogue}
        result.duration_ms = int((time.monotonic() - started) * 1000)
        result.calls = len(subjects)
        ctx.stages[stage["id"]] = result
        ctx.usage = ctx.usage.add(result.usage)
        yield {"type": "stage_end", "stage": stage["id"], "result": result.to_dict()}

    # ------------------------------------------------------------------
    # 回合
    # ------------------------------------------------------------------
    def _enabled_stages(self) -> list[dict[str, Any]]:
        stages = []
        for stage in self.pipeline.get("stages") or []:
            if stage.get("enabled") is False:
                continue
            if stage.get("requires") == "images_enabled" and not self.images_enabled:
                continue
            stages.append(stage)
        return stages

    async def run(self, player_input: str, *, stream: bool = True) -> AsyncIterator[dict[str, Any]]:
        self._check_texts.clear()
        self._growth_texts.clear()
        self._random_events.clear()
        snapshot = self.snapshot_fn()
        ctx = TurnContext(player_input=player_input.strip(), snapshot=snapshot)
        ctx.world_brief = build_world_brief(snapshot)
        stages = self._enabled_stages()

        yield {
            "type": "turn_start",
            "pipeline": self.pipeline.get("id"),
            "pipeline_name": self.pipeline.get("name"),
            "stages": [{"id": s["id"], "name": s.get("name", s["id"]), "role": s.get("role")} for s in stages],
        }

        for index, stage in enumerate(stages):
            yield {
                "type": "stage_start", "stage": stage["id"], "name": stage.get("name", stage["id"]),
                "role": stage.get("role", "main"), "index": index, "total": len(stages),
            }
            try:
                if stage.get("for_each") == "nearby_npcs":
                    async for event in self._run_npc_stage(stage, ctx):
                        yield event
                else:
                    async for event in self._run_simple_stage(stage, ctx, stream=stream):
                        yield event
            except Exception as exc:  # noqa: BLE001 - 单个阶段崩溃不应终止整个回合
                log.exception("stage %s crashed", stage["id"])
                yield {"type": "stage_error", "stage": stage["id"], "message": f"{type(exc).__name__}: {exc}"}

        yield {"type": "turn_end", "turn": await self._finalize(ctx)}

    async def _finalize(self, ctx: TurnContext) -> dict[str, Any]:
        """收尾：**面板与世界状态一律从引擎重新取**，不采信模型的复述。"""
        narration = ctx.produced("narration") or ""
        if not narration:
            for result in reversed(list(ctx.stages.values())):
                if result.text and result.role in ("narrator", "main"):
                    narration = result.text
                    break

        recommendations = ctx.produced("recommendations") or []
        if isinstance(recommendations, dict):
            recommendations = recommendations.get("recommendations") or []
        if not recommendations:
            recommendations = _parse_recommendations(narration)

        # 判定 / 成长文本一律来自工具的真实返回
        panel = await self.call_tool("get_turn_panel", {})
        world_state = await self.call_tool("get_world_state", {})
        context = await self.call_tool("get_action_context", {})

        images = ctx.produced("images") or []
        if isinstance(images, dict):
            images = images.get("images") or []

        return {
            "ok": True,
            "pipeline": self.pipeline.get("id"),
            "narration": narration.strip(),
            "narration_clean": _strip_recommendation_block(narration).strip(),
            "check_text": "\n\n".join(self._check_texts),
            "growth_text": "\n\n".join(self._growth_texts),
            "random_event": self._random_events[-1] if self._random_events else None,
            "dialogue": ctx.dialogue,
            "recommendations": _normalize_recommendations(recommendations),
            "images": images,
            "panel": panel,
            "panel_text": panel.get("text", ""),
            "world": world_state,
            "context": context,
            "turn": world_state.get("turn"),
            "stages": [result.to_dict() for result in ctx.stages.values()],
            "tool_log": ctx.tool_log,
            "usage": ctx.usage.to_dict(),
        }


# ----------------------------------------------------------------------
# 文本兜底解析
# ----------------------------------------------------------------------

_REC_MARKERS = ("【你可以……】", "【你可以…】", "【你可以】", "你可以……", "【接下来】")


def _strip_recommendation_block(text: str) -> str:
    for marker in _REC_MARKERS:
        index = text.find(marker)
        if index >= 0:
            return text[:index]
    return text


def _parse_recommendations(text: str) -> list[dict[str, Any]]:
    """单 Agent 模式下推荐行动混在正文里，用文本规则捞出来。"""
    if not text:
        return []
    block = ""
    for marker in _REC_MARKERS:
        index = text.find(marker)
        if index >= 0:
            block = text[index + len(marker) :]
            break
    if not block:
        return []
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[:1].isdigit() and (line[1:2] in ".、)" or line[2:3] in ".、)"):
            if current:
                out.append(current)
            body = line.split(".", 1)[-1].split("、", 1)[-1].split(")", 1)[-1].strip()
            current = {"text": body, "minutes": "", "category": ""}
        elif current is not None and ("约" in line or "分钟" in line or "小时" in line or "自由" in line):
            current["minutes"] = line
        elif current is not None and line.startswith("你也可以"):
            break
    if current:
        out.append(current)
    return out[:5]


def _normalize_recommendations(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (items or [])[:5]:
        if isinstance(item, str):
            out.append({"text": item, "minutes": "", "category": ""})
        elif isinstance(item, dict):
            out.append(
                {
                    "text": str(item.get("text") or item.get("action") or "")[:200],
                    "minutes": str(item.get("minutes") or item.get("duration") or "")[:40],
                    "category": str(item.get("category") or "")[:20],
                }
            )
    return [item for item in out if item["text"]]
