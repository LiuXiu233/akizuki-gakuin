"""动态 NPC 测试：年龄门槛、查重、社交网络、晋升、日程、后台模拟。"""

from __future__ import annotations

import unittest

from tests.helpers import EngineTestCase

from engine.models import NPCTier


class TestNPCCreation(EngineTestCase):
    def _base_link(self) -> list[dict[str, object]]:
        return [{"npc_id": "npc_natsume_kou", "familiarity": 50, "trust": 40, "note": "摄影部同伴"}]

    def test_create_npc(self) -> None:
        result = self.T.create_npc(
            name="桑原树", reading="kuwabara itsuki", age=19, gender="male",
            role="student", tier="supporting", class_id="class_2c",
            appearance="瘦高，戴棒球帽", personality="话不多，但器材摸得很熟",
            speech_style="短句，偶尔来一个冷笑话",
            skills={"photography": 2, "technology": 3}, knowledge={"photography": 2},
            home_location="loc_station", favorite_place="loc_photo_room",
            social_links=self._base_link(), created_reason="玩家去摄影部时需要一个成员",
        )
        self.assertTrue(result["ok"], result)
        npc_id = result["id"]
        state = self.T.get_character_state(npc_id)
        self.assertTrue(state["ok"])
        self.assertEqual(state["age"], 19)
        self.assertEqual(state["tier"], "supporting")

    def test_underage_rejected(self) -> None:
        result = self.T.create_npc(name="未成年", age=17, social_links=self._base_link())
        self.assertFalse(result["ok"])
        self.assertIn("18", result["error"])

    def test_all_initial_students_are_adults(self) -> None:
        for npc_id in self.session.npcs.all_ids():
            definition = self.session.npcs.definition(npc_id)
            self.assertGreaterEqual(definition["age"], 18, f"{npc_id} 未成年")

    def test_duplicate_name_rejected(self) -> None:
        self.T.create_npc(name="桑原树", reading="kuwabara", age=19, social_links=self._base_link())
        again = self.T.create_npc(name="桑原树", reading="kuwabara2", age=20, social_links=self._base_link())
        self.assertFalse(again["ok"])

    def test_social_network_required(self) -> None:
        lonely = self.T.create_npc(name="孤立的人", reading="kodoku", age=20)
        self.assertFalse(lonely["ok"])
        self.assertIn("social_links", lonely["error"])

    def test_social_links_create_npc_to_npc_relationship(self) -> None:
        result = self.T.create_npc(
            name="平野瞳", reading="hirano hitomi", age=19,
            social_links=[{"npc_id": "npc_kagurazaka_hina", "familiarity": 60}],
        )
        self.assertTrue(result["ok"])
        rel = self.T.get_relationship(result["id"], "npc_kagurazaka_hina")
        self.assertTrue(rel["known"])
        self.assertNotEqual(rel["stage"], "stranger")

    def test_unregistered_skill_rejected(self) -> None:
        result = self.T.create_npc(
            name="未知技能者", reading="unknown", age=20,
            skills={"没注册的技能": 3}, social_links=self._base_link(),
        )
        self.assertFalse(result["ok"])

    def test_unknown_location_rejected(self) -> None:
        result = self.T.create_npc(
            name="幽灵", reading="ghost", age=20,
            home_location="loc_不存在", social_links=self._base_link(),
        )
        self.assertFalse(result["ok"])


class TestPromotion(EngineTestCase):
    def test_promotion_thresholds(self) -> None:
        created = self.T.create_npc(
            name="小林光", reading="kobayashi hikaru", age=19,
            social_links=[{"npc_id": "npc_oda_shun", "familiarity": 40}],
        )
        npc_id = created["id"]
        self.assertEqual(self.T.get_character_state(npc_id)["tier"], NPCTier.BACKGROUND.value)

        check = self.session.npcs.check_promotion(npc_id)
        self.assertFalse(check["promote"])

        for _ in range(4):
            self.session.relationships.apply_event("player", npc_id, "conversation", intensity=1.5)
            self.T.advance_time(60 * 24, reason="第二天")
        self.session.relationships.set_values("player", npc_id, {"familiarity": 30})
        promotions = self.T.check_npc_promotions()["promotions"]
        self.assertTrue(any(p["npc_id"] == npc_id for p in promotions))
        self.assertEqual(self.T.get_character_state(npc_id)["tier"], NPCTier.SUPPORTING.value)

    def test_manual_promotion_to_core(self) -> None:
        result = self.T.promote_npc("npc_oda_shun", "core")
        self.assertTrue(result["promoted"])
        self.assertEqual(result["to"], "core")
        self.assertEqual(self.T.get_character_state("npc_oda_shun")["tier"], "core")

    def test_no_demotion(self) -> None:
        self.T.promote_npc("npc_oda_shun", "core")
        result = self.T.promote_npc("npc_oda_shun", "background")
        self.assertFalse(result["promoted"])


class TestScheduleAndPresence(EngineTestCase):
    def test_npc_has_schedule(self) -> None:
        schedule = self.T.get_schedule("npc_amano_rin")
        self.assertTrue(schedule["ok"])
        self.assertIsNotNone(schedule["location"])

    def test_npc_not_always_where_player_wants(self) -> None:
        self.T.move_character("player", "loc_music_room")
        present = {c["id"] for c in self.T.get_nearby_characters()["characters"]}
        # 周三是社团休息日，轻音部不在音乐室
        self.assertNotIn("npc_amano_rin", present)

    def test_hidden_fields_excluded_by_default(self) -> None:
        state = self.T.get_character_state("npc_amano_rin")
        self.assertNotIn("secrets", state)
        self.assertNotIn("hidden_personality", state)
        full = self.T.get_character_state("npc_amano_rin", include_hidden=True)
        self.assertIn("secrets", full)


class TestBackgroundSimulation(EngineTestCase):
    def test_simulation_runs(self) -> None:
        result = self.T.simulate_background_world(180)
        self.assertTrue(result["ok"])
        self.assertGreater(result["simulated_npcs"], 0)

    def test_npc_npc_relationships_change_without_player(self) -> None:
        before = self.session.relationships.get("npc_amano_rin", "npc_shirai_kanade").values.to_dict()
        for _ in range(40):
            self.T.simulate_background_world(120)
            self.T.advance_time(120, reason="时间流逝")
        after = self.session.relationships.get("npc_amano_rin", "npc_shirai_kanade").values.to_dict()
        self.assertNotEqual(before, after, "后台世界应该在玩家不在场时也发生变化")

    def test_player_not_center_of_world(self) -> None:
        """后台互动里应该有完全不涉及玩家的条目。"""
        for _ in range(20):
            self.T.simulate_background_world(120)
            self.T.advance_time(120, reason="时间流逝")
        log = self.session.state.world.get("background_log", [])
        npc_only = [e for e in log if e.get("type") == "npc_interaction" and "player" not in (e.get("a"), e.get("b"))]
        self.assertTrue(npc_only, "应该存在 NPC↔NPC 的互动记录")

    def test_background_events_are_hidden_until_discovered(self) -> None:
        self.T.simulate_background_world(240)
        for entry in self.session.state.world.get("background_log", []):
            self.assertFalse(entry.get("known_by_player", False))


if __name__ == "__main__":
    unittest.main()
