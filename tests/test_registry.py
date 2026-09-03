"""注册系统测试：查重、粒度、动态技能/知识/地点/组织、静态+动态合并。"""

from __future__ import annotations

import unittest

from tests.helpers import EngineTestCase


class TestRegistryReads(EngineTestCase):
    def test_static_registries_loaded(self) -> None:
        registry = self.T.get_registry()["registry"]
        self.assertGreaterEqual(registry["skill"]["count"], 25)
        self.assertGreaterEqual(registry["knowledge"]["count"], 19)
        self.assertGreaterEqual(registry["location"]["count"], 40)
        self.assertGreaterEqual(registry["group"]["count"], 14)
        self.assertGreaterEqual(registry["npc"]["count"], 21)

    def test_unknown_kind_rejected(self) -> None:
        self.assertFalse(self.T.get_registry("怪东西")["ok"])

    def test_core_skills_present(self) -> None:
        ids = self.T.get_registry("skill")["registry"]["skill"]["ids"]
        for expected in ("athletics", "conversation", "study", "cooking", "photography", "singing", "gaming"):
            self.assertIn(expected, ids)

    def test_core_knowledge_present(self) -> None:
        ids = self.T.get_registry("knowledge")["registry"]["knowledge"]["ids"]
        for expected in ("mathematics", "literature", "psychology", "school_rumors", "local_area"):
            self.assertIn(expected, ids)


class TestDuplicateDetection(EngineTestCase):
    def test_alias_duplicate_rejected(self) -> None:
        result = self.T.register_skill("camera_skill", "摄影技巧")
        self.assertFalse(result["ok"])
        self.assertIn("photography", result["error"])

    def test_chinese_name_duplicate_rejected(self) -> None:
        result = self.T.register_skill("shesing", "摄影")
        self.assertFalse(result["ok"])

    def test_find_duplicate_tool(self) -> None:
        found = self.T.find_duplicate("skill", name="拍照")
        self.assertIsNotNone(found["duplicate"])
        self.assertFalse(found["should_create"])
        clean = self.T.find_duplicate("skill", name="剑道", entry_id="kendo")
        self.assertIsNone(clean["duplicate"])
        self.assertTrue(clean["should_create"])

    def test_same_id_twice_rejected(self) -> None:
        self.assertTrue(self.T.register_skill("kendo", "剑道", category="physical", attribute="physique")["ok"])
        self.assertFalse(self.T.register_skill("kendo", "剑道二号")["ok"])

    def test_knowledge_duplicate(self) -> None:
        self.assertFalse(self.T.register_knowledge("photo_knowledge", "摄影知识")["ok"])

    def test_location_duplicate(self) -> None:
        self.assertFalse(self.T.register_location("loc_conbini", "便利店 SUNMART 秋月站前店")["ok"])


class TestDynamicRegistration(EngineTestCase):
    def test_register_skill_and_use_it(self) -> None:
        created = self.T.register_skill(
            "video_editing", "视频剪辑", category="art", attribute="intellect",
            aliases=["剪片"], reason="玩家开始做校园短片",
        )
        self.assertTrue(created["ok"])
        self.session.progression.set_level("player", "skill", "video_editing", 1)
        result = self.T.perform_action("custom", skill="video_editing", attribute="intellect", minutes=60)
        self.assertTrue(result["ok"])
        self.assertTrue(any(a["id"] == "video_editing" for a in result["xp"]))

    def test_register_knowledge(self) -> None:
        created = self.T.register_knowledge("coffee", "咖啡", category="hobby", aliases=["手冲"], unlocks=["认出豆子产地"])
        self.assertTrue(created["ok"])
        self.assertIn("coffee", self.T.get_registry("knowledge")["registry"]["knowledge"]["dynamic_ids"])

    def test_register_location_becomes_reachable(self) -> None:
        created = self.T.register_location(
            "loc_live_bar", "小型 LIVE BAR", zone="town_center", tags=["music", "night"],
            open_hours=[18, 25], description="只有二十个座位。",
        )
        self.assertTrue(created["ok"])
        ids = [loc["id"] for loc in self.T.get_locations()["locations"]]
        self.assertIn("loc_live_bar", ids)
        moved = self.T.move_character("player", "loc_live_bar")
        self.assertTrue(moved["ok"])
        self.assertGreater(moved["minutes"], 0)

    def test_register_location_rejects_bad_zone(self) -> None:
        self.assertFalse(self.T.register_location("loc_moon", "月球", zone="outer_space")["ok"])

    def test_register_group(self) -> None:
        created = self.T.register_group(
            "grp_film_club", "校园短片组", group_type="informal",
            members=["player", "npc_natsume_kou"], location="loc_photo_room",
            purpose="文化祭放映", temporary=True,
        )
        self.assertTrue(created["ok"])
        ids = [g["id"] for g in self.T.get_clubs()["groups"]]
        self.assertIn("grp_film_club", ids)

    def test_group_rejects_unknown_member(self) -> None:
        self.assertFalse(self.T.register_group("grp_ghost", "幽灵社", members=["npc_不存在"])["ok"])

    def test_dynamic_entries_persist_through_save(self) -> None:
        self.T.register_skill("kendo", "剑道", category="physical", attribute="physique")
        self.T.save_game("slot_reg")
        self.T.load_game("slot_reg")
        self.assertIn("kendo", self.T.get_registry("skill")["registry"]["skill"]["ids"])

    def test_id_validation(self) -> None:
        self.assertFalse(self.T.register_skill("Bad ID!", "坏名字")["ok"])
        self.assertFalse(self.T.register_skill("摄影新", "全中文ID")["ok"])


if __name__ == "__main__":
    unittest.main()
