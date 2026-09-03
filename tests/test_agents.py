"""LLM 适配层与多 Agent 流水线测试。

不联网：用一个可编排的 MockAdapter 替换上游，验证
* 两种上游格式的请求体 / 响应解析
* 凭据优先级（玩家自带 key > 服务器预置）
* 工具白名单（NPC 阶段拿不到写工具，危险工具永远不可见）
* 单 / 双 / 多三种流水线都能跑通并产出叙事与推荐
* 流式 SSE 事件序列
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401

    HAS_SERVER = True
except Exception:  # noqa: BLE001
    HAS_SERVER = False

if HAS_SERVER:
    from server.config import Settings, get_settings
    from server.llm.anthropic_adapter import AnthropicAdapter
    from server.llm.base import LLMError, LLMResult, Message, ResolvedProvider, StreamEvent, ToolCall, ToolSpec, Usage
    from server.llm.client import resolve_provider
    from server.llm.openai_adapter import OpenAIAdapter


# ---------------------------------------------------------------------------
# Mock 上游
# ---------------------------------------------------------------------------


class MockAdapter:
    """按 system prompt 里的角色关键字返回预设结果。"""

    provider = "mock"

    def __init__(self, model: str = "mock-model") -> None:
        self.model = model
        self.calls: list[dict] = []

    def _script(self, system: str, messages: list) -> "LLMResult":
        used_tools = any(m.role == "tool" for m in messages)
        single_agent = "你要独自完成这一回合的全部工作" in system
        if single_agent:
            if not used_tools:
                return LLMResult(text="", tool_calls=[ToolCall(id="s1", name="perform_action",
                                                               arguments={"action_type": "talk",
                                                                          "target": "npc_amano_rin"})],
                                 usage=Usage(40, 10))
            return LLMResult(
                text=(
                    "教室里的人走得差不多了。你和天野聊了一会儿昨天的话题。\n\n"
                    "【你可以……】\n\n1. 继续和她聊下去\n   约 15 分钟\n\n2. 去图书馆\n   约 1 小时\n\n"
                    "你也可以直接输入任何想做的事情。"
                ),
                usage=Usage(50, 120),
            )
        if "裁判" in system:
            if not used_tools:
                return LLMResult(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="perform_action",
                                         arguments={"action_type": "talk", "target": "npc_amano_rin"})],
                    usage=Usage(10, 5),
                )
            return LLMResult(text=json.dumps({"facts": ["玩家和天野凛聊了一会儿"], "blocked": []},
                                             ensure_ascii=False), usage=Usage(8, 12))
        if "主控解析" in system or ("主控" in system and "只读世界" in system):
            return LLMResult(
                text=json.dumps(
                    {"summary": "和凛聊天", "steps": [{"description": "搭话", "tool": "perform_action",
                                                    "arguments": {"action_type": "talk", "target": "npc_amano_rin"}}],
                     "npcs_involved": ["npc_amano_rin"], "needs_check": True},
                    ensure_ascii=False,
                ),
                usage=Usage(20, 30),
            )
        if "你是" in system and "说话方式" in system:      # NPC 视角
            return LLMResult(text="「……你今天倒是挺闲。」她把乐谱往旁边推了推。", usage=Usage(15, 8))
        if "旁白" in system:
            return LLMResult(text="午后的教室里只剩下几个人。窗边的光把桌面切成两半。", usage=Usage(30, 40))
        if "导演" in system:
            if not used_tools:
                return LLMResult(text="", tool_calls=[ToolCall(id="d1", name="end_turn", arguments={})],
                                 usage=Usage(10, 5))
            return LLMResult(
                text=json.dumps(
                    {"recommendations": [
                        {"text": "继续和凛聊下去", "minutes": "约 15 分钟", "category": "social"},
                        {"text": "去图书馆看书", "minutes": "约 1 小时", "category": "study"},
                        {"text": "回家休息", "minutes": "约 30 分钟", "category": "rest"},
                    ]}, ensure_ascii=False),
                usage=Usage(12, 25),
            )
        if "画师" in system:
            return LLMResult(text=json.dumps({"images": []}), usage=Usage(5, 5))
        # 单 Agent / 双 Agent 主控
        if not used_tools:
            return LLMResult(text="", tool_calls=[ToolCall(id="s1", name="perform_action",
                                                           arguments={"action_type": "talk",
                                                                      "target": "npc_amano_rin"})],
                             usage=Usage(40, 10))
        return LLMResult(
            text=(
                "教室里的人走得差不多了。你和天野聊了一会儿昨天的话题。\n\n"
                "【你可以……】\n\n1. 继续和她聊下去\n   约 15 分钟\n\n2. 去图书馆\n   约 1 小时\n\n"
                "你也可以直接输入任何想做的事情。"
            ),
            usage=Usage(50, 120),
        )

    async def complete(self, *, system, messages, tools=None, temperature=None, max_tokens=None,
                       extra_params=None):
        self.calls.append({"system": system, "messages": len(messages),
                           "tools": [t.name for t in (tools or [])], "extra_params": extra_params})
        await asyncio.sleep(0)
        return self._script(system, messages)

    async def stream(self, *, system, messages, tools=None, temperature=None, max_tokens=None,
                     extra_params=None):
        result = await self.complete(system=system, messages=messages, tools=tools,
                                     temperature=temperature, max_tokens=max_tokens,
                                     extra_params=extra_params)
        for chunk in (result.text[i : i + 12] for i in range(0, len(result.text), 12)):
            yield StreamEvent(type="text", text=chunk)
        for call in result.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=call)
        yield StreamEvent(type="usage", usage=result.usage)
        yield StreamEvent(type="done")


# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_SERVER, "未安装 fastapi/httpx")
class TestAdapterPayloads(unittest.TestCase):
    """不联网，只验证请求体结构是否符合两家的规范。"""

    def setUp(self) -> None:
        self.tool = ToolSpec("perform_action", "执行行动", {"type": "object", "properties": {}})
        self.messages = [
            Message.user("我去找凛"),
            Message.assistant("", [ToolCall("call_1", "perform_action", {"action_type": "talk"})]),
            Message.tool_result("call_1", "perform_action", {"ok": True}),
        ]

    def test_openai_payload(self) -> None:
        adapter = OpenAIAdapter(ResolvedProvider("openai", "https://x/v1", "k", "gpt-4o"))
        payload = adapter._payload("系统提示", self.messages, [self.tool], 0.7, 500, False)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][2]["tool_calls"][0]["function"]["name"], "perform_action")
        self.assertEqual(payload["messages"][3]["role"], "tool")
        self.assertEqual(payload["tools"][0]["type"], "function")
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(adapter._url(), "https://x/v1/chat/completions")

    def test_openai_parse(self) -> None:
        adapter = OpenAIAdapter(ResolvedProvider("openai", "https://x/v1", "k", "gpt-4o"))
        result = adapter._parse({
            "model": "gpt-4o",
            "choices": [{"finish_reason": "tool_calls", "message": {
                "content": "好的",
                "tool_calls": [{"id": "a", "function": {"name": "perform_action", "arguments": '{"x":1}'}}],
            }}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22},
        })
        self.assertEqual(result.text, "好的")
        self.assertEqual(result.tool_calls[0].arguments, {"x": 1})
        self.assertEqual(result.usage.to_dict()["total_tokens"], 33)

    def test_anthropic_payload(self) -> None:
        adapter = AnthropicAdapter(ResolvedProvider("anthropic", "https://api.anthropic.com", "k", "claude-sonnet-5"))
        payload = adapter._payload("系统提示", self.messages, [self.tool], 0.7, 500, False)
        self.assertEqual(payload["system"], "系统提示")
        self.assertNotIn("system", [m["role"] for m in payload["messages"]])
        self.assertEqual(payload["messages"][1]["content"][0]["type"], "tool_use")
        self.assertEqual(payload["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual(payload["tools"][0]["input_schema"], {"type": "object", "properties": {}})
        self.assertIn("max_tokens", payload)
        self.assertEqual(adapter._url(), "https://api.anthropic.com/v1/messages")

    def test_anthropic_parse(self) -> None:
        adapter = AnthropicAdapter(ResolvedProvider("anthropic", "https://api.anthropic.com", "k", "claude-sonnet-5"))
        result = adapter._parse({
            "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": "嗯"},
                        {"type": "tool_use", "id": "t1", "name": "resolve_check", "input": {"a": 1}}],
            "usage": {"input_tokens": 5, "output_tokens": 7},
            "stop_reason": "tool_use",
        })
        self.assertEqual(result.text, "嗯")
        self.assertEqual(result.tool_calls[0].name, "resolve_check")
        self.assertEqual(result.stop_reason, "tool_use")

    def test_extra_params_merged_into_payload(self) -> None:
        config = ResolvedProvider("openai", "https://x/v1", "k", "m",
                                  extra_params={"reasoning_effort": "none"})
        adapter = OpenAIAdapter(config)
        payload = adapter._payload("s", self.messages, None, None, 500, False)
        self.assertEqual(payload["reasoning_effort"], "none")
        override = adapter._payload("s", self.messages, None, None, 500, False,
                                    extra_params={"reasoning_effort": "high"})
        self.assertEqual(override["reasoning_effort"], "high", "阶段级应能覆盖全局")

    def test_headers_differ(self) -> None:
        openai = OpenAIAdapter(ResolvedProvider("openai", "https://x/v1", "k1", "m"))
        anthropic = AnthropicAdapter(ResolvedProvider("anthropic", "https://y", "k2", "m"))
        self.assertIn("Authorization", openai._headers())
        self.assertIn("x-api-key", anthropic._headers())
        self.assertEqual(anthropic._headers()["anthropic-version"], "2023-06-01")


@unittest.skipUnless(HAS_SERVER, "未安装 fastapi/httpx")
class TestCredentialResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()

    def test_user_key_wins(self) -> None:
        self.settings.llm.api_key = "server-key"
        self.settings.llm.model = "server-model"
        config = resolve_provider({"api_key": "user-key", "model": "user-model"}, self.settings)
        self.assertEqual(config.api_key, "user-key")
        self.assertEqual(config.model, "user-model")
        self.assertEqual(config.source, "user")

    def test_server_key_fallback(self) -> None:
        self.settings.llm.api_key = "server-key"
        self.settings.llm.model = "server-model"
        config = resolve_provider({}, self.settings)
        self.assertEqual(config.source, "server")
        self.assertEqual(config.model, "server-model")

    def test_no_key_raises(self) -> None:
        with self.assertRaises(LLMError) as ctx:
            resolve_provider({}, self.settings)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_default_base_urls(self) -> None:
        openai = resolve_provider({"api_key": "k", "provider": "openai", "model": "m"}, self.settings)
        anthropic = resolve_provider({"api_key": "k", "provider": "anthropic", "model": "m"}, self.settings)
        self.assertIn("openai.com", openai.base_url)
        self.assertIn("anthropic.com", anthropic.base_url)

    def test_public_view_hides_key(self) -> None:
        config = resolve_provider({"api_key": "sk-secret", "model": "m"}, self.settings)
        self.assertNotIn("sk-secret", json.dumps(config.public()))

    def test_unknown_provider_rejected(self) -> None:
        with self.assertRaises(LLMError):
            resolve_provider({"api_key": "k", "provider": "gemini", "model": "m"}, self.settings)


@unittest.skipUnless(HAS_SERVER, "未安装 fastapi/httpx")
class TestPipelineRunner(unittest.IsolatedAsyncioTestCase):
    """用 MockAdapter 跑完整流水线。"""

    async def asyncSetUp(self) -> None:
        from engine.tools import GameSession, use_session
        from server.agents.pipeline import load_pipeline

        self.tmp = Path(tempfile.mkdtemp(prefix="akizuki_pipe_"))
        (self.tmp / "state").mkdir()
        (self.tmp / "saves").mkdir()
        self.session = GameSession(root=ROOT, data_root=self.tmp, seed=42, autoload=False)
        with use_session(self.session):
            from engine import tools as T

            T.create_player(name="佐藤悠", age=19, preset="preset_allrounder")
            T.advance_time(70, reason="到上课时间")   # 07:30 → 08:40，同学们都到教室了
            T.move_character("player", "loc_class_2a")
            assert T.get_nearby_characters()["characters"], "测试前提：教室里应该有人"
        self.load_pipeline = load_pipeline
        self.adapter = MockAdapter()

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _runner(self, pipeline_id: str, **kwargs):
        from engine import tools as T
        from engine.tools import use_session
        from server.agents.runner import PipelineRunner

        async def call_tool(name, arguments):
            with use_session(self.session):
                return T.call_tool(name, arguments)

        def snapshot():
            with use_session(self.session):
                return {
                    "world_state": T.get_world_state(),
                    "player": T.get_player_state(),
                    "context": T.get_action_context(),
                    "agent_md": self.session.state.static.get("doc_agent", ""),
                }

        return PipelineRunner(
            pipeline=self.load_pipeline(ROOT, pipeline_id),
            settings=Settings(),
            credentials={"api_key": "test", "model": "mock"},
            call_tool=call_tool,
            snapshot_fn=snapshot,
            adapter_factory=lambda model: self.adapter,
            images_enabled=kwargs.pop("images_enabled", True),
            **kwargs,
        )

    async def _run(self, pipeline_id: str, text: str = "我去找凛聊聊", stream: bool = False):
        runner = self._runner(pipeline_id)
        events = []
        async for event in runner.run(text, stream=stream):
            events.append(event)
        turn = next(e["turn"] for e in events if e["type"] == "turn_end")
        return events, turn

    async def test_single_pipeline(self) -> None:
        events, turn = await self._run("single")
        self.assertTrue(turn["ok"])
        self.assertIn("天野", turn["narration"])
        self.assertGreaterEqual(len(turn["recommendations"]), 2)
        self.assertNotIn("【你可以", turn["narration_clean"])
        self.assertIn("秋月学院", turn["panel_text"])

    async def test_dual_pipeline_has_npc_dialogue(self) -> None:
        events, turn = await self._run("dual")
        self.assertTrue(turn["dialogue"], "双 Agent 模式应该有 NPC 台词")
        self.assertTrue(any(e["type"] == "dialogue" for e in events))
        names = {d["name"] for d in turn["dialogue"]}
        self.assertTrue(names)

    async def test_multi_pipeline_all_stages(self) -> None:
        events, turn = await self._run("multi")
        stage_ids = [e["stage"] for e in events if e["type"] == "stage_start"]
        self.assertEqual(stage_ids[:5], ["intent", "resolve", "npc_react", "narrate", "direct"])
        self.assertIn("illustrate", stage_ids)
        self.assertTrue(turn["narration"])
        self.assertEqual(len(turn["recommendations"]), 3)
        self.assertTrue(turn["dialogue"])

    async def test_illustrate_skipped_without_images(self) -> None:
        runner = self._runner("multi")
        runner.images_enabled = False
        stages = [s["id"] for s in runner._enabled_stages()]
        self.assertNotIn("illustrate", stages)
        runner.images_enabled = True
        self.assertIn("illustrate", [s["id"] for s in runner._enabled_stages()])

    async def test_engine_actually_advanced(self) -> None:
        from engine import tools as T
        from engine.tools import use_session

        with use_session(self.session):
            before = T.get_world_state()["time"]
        await self._run("multi")
        with use_session(self.session):
            after = T.get_world_state()["time"]
            turn_no = T.get_world_state()["turn"]
        self.assertNotEqual(before, after, "流水线必须真的通过工具推进了世界")
        self.assertGreaterEqual(turn_no, 1)

    async def test_tool_whitelist(self) -> None:
        runner = self._runner("multi")
        stages = {s["id"]: s for s in runner.pipeline["stages"]}
        npc_tools = {t.name for t in runner._tools_for(stages["npc_react"])}
        self.assertEqual(npc_tools, set(), "NPC 扮演阶段不应有任何工具")
        intent_tools = {t.name for t in runner._tools_for(stages["intent"])}
        self.assertIn("get_world_state", intent_tools)
        self.assertNotIn("perform_action", intent_tools, "解析阶段不应能改变世界")
        resolve_tools = {t.name for t in runner._tools_for(stages["resolve"])}
        self.assertIn("perform_action", resolve_tools)
        self.assertNotIn("new_game", resolve_tools)
        self.assertNotIn("load_game", resolve_tools)

    async def test_forbidden_tools_never_exposed(self) -> None:
        runner = self._runner("single")
        all_tools = {t.name for t in runner._tools_for(runner.pipeline["stages"][0])}
        self.assertNotIn("new_game", all_tools)
        self.assertNotIn("load_game", all_tools)
        self.assertIn("perform_action", all_tools)

    async def test_streaming_emits_deltas(self) -> None:
        events, turn = await self._run("multi", stream=True)
        deltas = [e for e in events if e["type"] == "delta"]
        self.assertTrue(deltas, "流式模式应该产生 delta 事件")
        self.assertTrue(any(e["stage"] == "narrate" for e in deltas))

    async def test_tool_log_recorded(self) -> None:
        _events, turn = await self._run("multi")
        names = {entry["name"] for entry in turn["tool_log"] if entry["type"] == "tool_call"}
        self.assertIn("perform_action", names)
        self.assertIn("end_turn", names)

    async def test_extra_params_reach_adapter(self) -> None:
        """阶段级 extra_params 必须透传到适配器（推理模型开关靠它）。"""
        runner = self._runner("multi")
        runner.pipeline["stages"][0]["extra_params"] = {"reasoning_effort": "none"}
        async for _ in runner.run("测试", stream=False):
            pass
        self.assertTrue(
            any(call.get("extra_params") == {"reasoning_effort": "none"} for call in self.adapter.calls),
            "extra_params 没有传到上游",
        )

    async def test_usage_accumulated(self) -> None:
        _events, turn = await self._run("multi")
        self.assertGreater(turn["usage"]["total_tokens"], 0)

    async def test_panel_comes_from_engine_not_model(self) -> None:
        from engine import tools as T
        from engine.tools import use_session

        _events, turn = await self._run("multi")
        with use_session(self.session):
            panel = T.get_turn_panel()
        self.assertEqual(turn["panel_text"], panel["text"])

    async def test_stage_failure_does_not_kill_turn(self) -> None:
        class BrokenAdapter(MockAdapter):
            async def complete(self, **kwargs):
                if "旁白" in kwargs["system"]:
                    raise LLMError("上游炸了", status_code=502)
                return await super().complete(**kwargs)

        self.adapter = BrokenAdapter()
        events, turn = await self._run("multi")
        self.assertTrue(any(e["type"] == "stage_error" for e in events))
        self.assertTrue(turn["ok"], "单个阶段失败不应让整个回合失败")
        self.assertTrue(turn["panel_text"])


@unittest.skipUnless(HAS_SERVER, "未安装 fastapi/httpx")
class TestTurnAPI(unittest.TestCase):
    """HTTP 层：/turn 与 /turn/stream。"""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        import server.deps
        import server.routes.meta
        from server.config import get_settings

        self.tmp = Path(tempfile.mkdtemp(prefix="akizuki_turnapi_"))
        os.environ["AKIZUKI_DATA_DIR"] = str(self.tmp)
        os.environ.pop("AKIZUKI_ACCESS_PASSWORD", None)
        get_settings.cache_clear()
        server.deps.reset_state()
        server.routes.meta._READONLY_SESSION = None

        from server.app import create_app

        self.client = TestClient(create_app())
        self.user = self.client.post("/api/session", json={}).json()["user_id"]
        self.headers = {"X-User-Token": self.user}
        self.world = self.client.post(
            "/api/worlds", json={"name": "测试", "seed": 42}, headers=self.headers
        ).json()["world"]["id"]
        self.client.post(
            f"/api/worlds/{self.world}/tools/create_player",
            json={"arguments": {"name": "佐藤悠", "age": 19, "preset": "preset_allrounder"}},
            headers=self.headers,
        )
        # 用 Mock 替换上游
        import server.agents.runner as runner_module

        self._original = runner_module.build_adapter
        runner_module.build_adapter = lambda config, settings: MockAdapter(config.model)

    def tearDown(self) -> None:
        import server.agents.runner as runner_module

        runner_module.build_adapter = self._original
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AKIZUKI_DATA_DIR", None)

    def test_turn_without_key_is_400(self) -> None:
        response = self.client.post(
            f"/api/worlds/{self.world}/turn", json={"input": "去教室"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("API key", response.json()["detail"])

    def test_turn_with_user_key(self) -> None:
        response = self.client.post(
            f"/api/worlds/{self.world}/turn",
            json={"input": "我去找凛聊聊", "pipeline": "multi",
                  "credentials": {"api_key": "test-key", "model": "mock"}},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["narration"])
        self.assertTrue(data["recommendations"])
        self.assertIn("秋月学院", data["panel_text"])

    def test_pipelines_listed(self) -> None:
        data = self.client.get("/api/pipelines").json()
        ids = {p["id"] for p in data["pipelines"]}
        self.assertEqual(ids, {"single", "dual", "multi"})

    def test_unknown_pipeline_404(self) -> None:
        response = self.client.post(
            f"/api/worlds/{self.world}/turn",
            json={"input": "x", "pipeline": "不存在",
                  "credentials": {"api_key": "k", "model": "m"}},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_stream_endpoint_emits_sse(self) -> None:
        with self.client.stream(
            "POST",
            f"/api/worlds/{self.world}/turn/stream",
            json={"input": "我去找凛聊聊", "pipeline": "dual",
                  "credentials": {"api_key": "test-key", "model": "mock"}},
            headers=self.headers,
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())
        self.assertIn("event: turn_start", body)
        self.assertIn("event: stage_start", body)
        self.assertIn("event: turn_end", body)

    def test_llm_verify_reports_missing_key(self) -> None:
        data = self.client.post("/api/llm/chat", json={"messages": [{"role": "user", "content": "hi"}]}).json()
        self.assertIn("detail", data)


if __name__ == "__main__":
    unittest.main()
