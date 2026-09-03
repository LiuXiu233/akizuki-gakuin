"""存档 / 状态 / 时间 / 事件 / 面板 / 工具接口测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.helpers import EngineTestCase

from engine.state_manager import StateManager, atomic_write_json


class TestSaveLoad(EngineTestCase):
    def test_state_files_written(self) -> None:
        self.T.save_game("save_001")
        for name in ("world_state", "character_state", "relationships", "memories", "event_state", "world_registry"):
            self.assertTrue((self.tmp / "state" / f"{name}.json").exists(), name)
        self.assertTrue((self.tmp / "saves" / "save_001.json").exists())

    def test_roundtrip_preserves_world(self) -> None:
        self.T.perform_action("talk", target=None, minutes=30)
        self.T.register_skill("kendo", "剑道", category="physical", attribute="physique")
        before_time = self.T.get_world_state()["time"]
        self.T.save_game("slot_a")
        self.T.advance_time(120, reason="乱走一会儿")
        self.assertNotEqual(self.T.get_world_state()["time"], before_time)
        self.T.load_game("slot_a")
        self.assertEqual(self.T.get_world_state()["time"], before_time)
        self.assertIn("kendo", self.T.get_registry("skill")["registry"]["skill"]["ids"])

    def test_backup_created(self) -> None:
        self.T.save_game("slot_b")
        self.T.save_game("slot_b")
        backups = list((self.tmp / "saves" / ".backups").glob("*.bak"))
        self.assertTrue(backups)

    def test_atomic_write_no_partial_file(self) -> None:
        path = self.tmp / "state" / "atomic_test.json"
        atomic_write_json(path, {"a": 1})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})
        tmp_leftovers = list((self.tmp / "state").glob(".*tmp"))
        self.assertFalse(tmp_leftovers)

    def test_list_saves(self) -> None:
        self.T.save_game("slot_c")
        slots = [s["slot"] for s in self.T.list_saves()["saves"]]
        self.assertIn("slot_c", slots)

    def test_load_missing_slot_errors_gracefully(self) -> None:
        result = self.T.load_game("不存在的存档")
        self.assertFalse(result["ok"])

    def test_corrupted_save_reports_clearly(self) -> None:
        (self.tmp / "saves" / "broken.json").write_text("{ not json", encoding="utf-8")
        result = self.T.load_game("broken")
        self.assertFalse(result["ok"])

    def test_rng_state_persists(self) -> None:
        self.T.save_game("slot_rng")
        expected = [self.session.rng.d20() for _ in range(5)]
        self.T.load_game("slot_rng")
        self.assertEqual([self.session.rng.d20() for _ in range(5)], expected)


class TestTime(EngineTestCase):
    def test_advance_costs_energy(self) -> None:
        before = self.T.get_player_state()["status"]["energy"]
        self.T.advance_time(180, reason="发呆")
        self.assertLess(self.T.get_player_state()["status"]["energy"], before)

    def test_day_rollover(self) -> None:
        before = self.T.get_world_state()["date"]
        report = self.T.advance_time(60 * 20, reason="到明天")
        self.assertTrue(report["day_rollover"])
        self.assertNotEqual(self.T.get_world_state()["date"], before)

    def test_sleep_restores_energy(self) -> None:
        self.T.advance_time(60 * 10, reason="消耗精力")
        before = self.T.get_player_state()["status"]["energy"]
        self.T.sleep(hours=8)
        self.assertGreater(self.T.get_player_state()["status"]["energy"], before)

    def test_travel_time_depends_on_distance(self) -> None:
        self.T.move_character("player", "loc_class_2a")
        near = self.T.move_character("player", "loc_corridor")["minutes"]
        far = self.T.move_character("player", "loc_beach")["minutes"]
        self.assertLess(near, far)

    def test_cannot_reverse_time(self) -> None:
        self.assertFalse(self.T.advance_time(-30)["ok"])

    def test_forced_sleep_flag(self) -> None:
        self.T.advance_time(60 * 19, reason="熬到深夜")
        self.assertTrue(self.T.get_world_state()["must_sleep"])

    def test_class_time_detection(self) -> None:
        self.T.advance_time(90, reason="到第一节课")
        world = self.T.get_world_state()
        self.assertIn("is_class_time", world)


class TestEvents(EngineTestCase):
    def test_candidates_respect_conditions(self) -> None:
        self.T.move_character("player", "loc_library")
        candidates = self.T.get_event_candidates()["candidates"]
        ids = {c["id"] for c in candidates}
        self.assertNotIn("ev_summer_festival_date", ids)

    def test_cooldown_blocks_repeat(self) -> None:
        first = self.T.roll_random_event(force=True)["event"]
        if first is None:
            self.skipTest("当前上下文没有候选事件")
        for _ in range(10):
            event = self.T.roll_random_event(force=True)["event"]
            if event and event["event_id"] == first["event_id"]:
                self.fail("冷却期内不应重复触发同一事件")

    def test_daily_event_limit(self) -> None:
        self.session.state.world["events_today"] = 99
        self.assertIsNone(self.T.roll_random_event()["event"])

    def test_trigger_unknown_event(self) -> None:
        self.assertFalse(self.T.trigger_event("ev_不存在")["ok"])


class TestPanels(EngineTestCase):
    def test_turn_panel_from_code(self) -> None:
        panel = self.T.get_turn_panel()
        self.assertTrue(panel["ok"])
        self.assertIn("秋月学院", panel["text"])
        self.assertIn("精力", panel["text"])
        self.assertEqual(panel["status"]["energy"], self.T.get_player_state()["status"]["energy"])

    def test_player_sheet(self) -> None:
        sheet = self.T.get_player_sheet()
        self.assertTrue(sheet["ok"])
        self.assertIn("【属性】", sheet["text"])
        self.assertIn("【技能】", sheet["text"])
        self.assertIn("【人际关系】", sheet["text"])

    def test_action_context_fields(self) -> None:
        context = self.T.get_action_context()
        for key in ("current_time", "current_location", "nearby_characters", "available_locations",
                    "current_events", "player_energy", "player_stress", "relationships",
                    "recent_actions", "recent_recommendations"):
            self.assertIn(key, context)

    def test_no_romance_recommendation_when_exhausted(self) -> None:
        self.session.state.player["status"]["energy"] = 5
        context = self.T.get_action_context()
        self.assertFalse(context["romance_opportunity"])
        self.assertNotIn("romance", context["suggested_categories"])

    def test_recommendations_recorded(self) -> None:
        self.T.record_recommendations(["去图书馆", "找凛聊天"])
        context = self.T.get_action_context()
        self.assertIn("去图书馆", context["recent_recommendations"])


class TestToolInterface(EngineTestCase):
    def test_all_tools_have_docstrings(self) -> None:
        for name, fn in self.T.TOOLS.items():
            self.assertTrue((fn.__doc__ or "").strip(), f"{name} 缺少文档")

    def test_tool_schemas_valid(self) -> None:
        schemas = self.T.tool_schemas()
        self.assertGreaterEqual(len(schemas), 40)
        for schema in schemas:
            self.assertIn("name", schema)
            self.assertIn("description", schema)
            self.assertEqual(schema["input_schema"]["type"], "object")
        json.dumps(schemas, ensure_ascii=False)

    def test_call_tool_dispatch(self) -> None:
        result = self.T.call_tool("get_world_state", {})
        self.assertTrue(result["ok"])

    def test_call_unknown_tool(self) -> None:
        result = self.T.call_tool("不存在的工具", {})
        self.assertFalse(result["ok"])

    def test_call_tool_bad_argument(self) -> None:
        result = self.T.call_tool("get_world_state", {"乱传的参数": 1})
        self.assertFalse(result["ok"])

    def test_required_tools_exist(self) -> None:
        required = [
            "get_world_state", "get_player_state", "get_character_state", "get_nearby_characters",
            "get_relationship", "get_relevant_memories", "resolve_check", "advance_time",
            "perform_action", "apply_relationship_event", "add_memory", "roll_random_event",
            "get_schedule", "move_character", "get_player_sheet", "get_turn_panel",
            "get_action_context", "save_game", "create_npc", "promote_npc", "register_skill",
            "register_knowledge", "register_location", "register_group", "get_registry",
            "simulate_background_world",
        ]
        for name in required:
            self.assertIn(name, self.T.TOOLS, f"缺少必需工具 {name}")

    def test_errors_never_raise(self) -> None:
        result = self.T.call_tool("get_character_state", {"character_id": "npc_根本不存在"})
        self.assertFalse(result["ok"])
        self.assertIn("error", result)


class TestMemories(EngineTestCase):
    def test_memory_three_layers(self) -> None:
        result = self.T.add_memory(
            "npc_amano_rin",
            fact="玩家把伞借给了她。",
            interpretation="她觉得这个人似乎挺细心。",
            emotion="开心，稍微有点在意。",
            intensity=6, visibility="private_fact", participants=["player"], tags=["rain"],
        )
        self.assertTrue(result["ok"])
        memory = result["memory"]
        self.assertNotEqual(memory["fact"], memory["interpretation"])
        self.assertTrue(memory["emotion"])

    def test_relevant_memories_prefer_participants(self) -> None:
        self.T.add_memory("npc_amano_rin", fact="和玩家一起走到车站。", participants=["player"], intensity=5)
        self.T.add_memory("npc_amano_rin", fact="自己在便利店买了咖啡。", intensity=2)
        memories = self.T.get_relevant_memories("npc_amano_rin", participants=["player"])["memories"]
        self.assertIn("玩家", memories[0]["fact"])

    def test_memory_requires_fact(self) -> None:
        self.assertFalse(self.T.add_memory("npc_amano_rin", fact="")["ok"])

    def test_invalid_visibility_rejected(self) -> None:
        self.assertFalse(self.T.add_memory("npc_amano_rin", fact="x", visibility="随便写的")["ok"])


class TestPlayerCreation(EngineTestCase):
    player_preset = None

    def test_attribute_points_enforced(self) -> None:
        bad = self.T.create_player(
            name="超人", age=19,
            attributes={"physique": 8, "agility": 8, "intellect": 8, "perception": 8,
                        "charm": 8, "willpower": 8, "luck": 8},
            skills=["conversation", "study", "cooking"], knowledge=["literature", "local_area", "anime"],
        )
        self.assertFalse(bad["ok"])

    def test_valid_creation(self) -> None:
        good = self.T.create_player(
            name="佐藤悠", age=19,
            attributes={"physique": 5, "agility": 5, "intellect": 6, "perception": 6,
                        "charm": 6, "willpower": 6, "luck": 6},
            skills=["conversation", "study", "cooking"], knowledge=["literature", "local_area", "anime"],
        )
        self.assertTrue(good["ok"], good)
        state = self.T.get_player_state()
        self.assertEqual(sum(state["attributes"].values()), 40)
        self.assertEqual(state["skills"]["conversation"], 2)

    def test_underage_player_rejected(self) -> None:
        result = self.T.create_player(name="小明", age=16, preset="preset_allrounder")
        self.assertFalse(result["ok"])

    def test_attribute_bounds(self) -> None:
        result = self.T.create_player(
            name="偏科", age=19,
            attributes={"physique": 9, "agility": 3, "intellect": 6, "perception": 6,
                        "charm": 6, "willpower": 5, "luck": 5},
            skills=["conversation", "study", "cooking"], knowledge=["literature", "local_area", "anime"],
        )
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
