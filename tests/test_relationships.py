"""关系与恋爱系统测试。

核心断言：
* 关系是单向的
* 日常互动变化非常小
* 骰子无法强迫 NPC 接受邀请或告白
* 隐藏数值默认不外泄
* 已有恋人 / 明确不谈恋爱的 NPC 会拒绝
"""

from __future__ import annotations

import unittest

from tests.helpers import EngineTestCase

from engine.models import RelationshipStage


class TestRelationshipBasics(EngineTestCase):
    def test_relationship_is_directional(self) -> None:
        self.session.relationships.apply_event("player", "npc_amano_rin", "deep_talk", intensity=1.5)
        forward = self.session.relationships.get("player", "npc_amano_rin")
        backward = self.session.relationships.get("npc_amano_rin", "player")
        self.assertNotEqual(forward.values.to_dict(), backward.values.to_dict())

    def test_daily_interaction_is_small(self) -> None:
        before = self.session.relationships.get("player", "npc_hoshino_makoto").values.familiarity
        result = self.session.relationships.apply_event("player", "npc_hoshino_makoto", "small_talk")
        after = self.session.relationships.get("player", "npc_hoshino_makoto").values.familiarity
        self.assertLessEqual(after - before, 2)
        self.assertIn("narrative_hint", result)

    def test_repeat_interaction_decays(self) -> None:
        target = "npc_kurosawa_daichi"
        gains = []
        for _ in range(4):
            before = self.session.relationships.get("player", target).values.familiarity
            self.session.relationships.apply_event("player", target, "conversation")
            after = self.session.relationships.get("player", target).values.familiarity
            gains.append(after - before)
        self.assertLessEqual(gains[-1], gains[0])

    def test_daily_gain_cap(self) -> None:
        target = "npc_saotome_yui"
        for _ in range(50):
            self.session.relationships.apply_event("player", target, "deep_talk", intensity=2.0)
        values = self.session.relationships.get("player", target).values.to_dict()
        cap = self.session.state.cfg("relationship.max_relationship_gain_per_day", 12)
        total = sum(v for k, v in values.items() if k != "conflict")
        self.assertLess(total, 40 + cap * 2)

    def test_romance_needs_familiarity_first(self) -> None:
        target = "npc_natsume_kou"
        for _ in range(5):
            self.session.relationships.apply_event("player", target, "flirt", intensity=2.0)
        values = self.session.relationships.get("player", target).values
        self.assertLess(values.romantic_interest, 20)

    def test_unknown_event_rejected(self) -> None:
        result = self.T.apply_relationship_event("player", "npc_amano_rin", "凭空发明的事件")
        self.assertFalse(result["ok"])


class TestHiddenNumbers(EngineTestCase):
    def test_describe_hides_numbers_by_default(self) -> None:
        described = self.T.get_relationship("player", "npc_amano_rin")
        self.assertNotIn("values", described)
        self.assertIn("label", described)

    def test_debug_flag_reveals(self) -> None:
        described = self.T.get_relationship("player", "npc_amano_rin", debug=True)
        self.assertIn("values", described)
        self.assertIn("debug_warning", described)

    def test_apply_event_hides_changes(self) -> None:
        result = self.T.apply_relationship_event("player", "npc_amano_rin", "conversation")
        self.assertNotIn("changes", result)
        self.assertIn("reminder", result)

    def test_player_sheet_has_no_hidden_values(self) -> None:
        sheet = self.T.get_player_sheet()
        text = sheet["text"]
        for token in ("attraction", "romantic_interest", "好感度"):
            self.assertNotIn(token, text)


class TestNPCAutonomy(EngineTestCase):
    def test_dice_cannot_force_date(self) -> None:
        """即使连续 Natural 20，也不能让关系尚浅的 NPC 答应约会。"""
        target = "npc_hoshino_makoto"
        for _ in range(50):
            self.T.resolve_check(action_type="persuade", attribute="charm", skill="persuasion", difficulty="very_easy")
        decision = self.T.npc_decide_invitation(target, "date")
        self.assertFalse(decision["accepted"])
        self.assertIn("autonomy_note", decision)

    def test_casual_invite_can_be_accepted(self) -> None:
        target = "npc_amano_rin"
        for _ in range(6):
            self.session.relationships.apply_event("player", target, "conversation")
            self.T.advance_time(60 * 24, reason="第二天")
        decision = self.T.npc_decide_invitation(target, "casual")
        self.assertTrue(decision["accepted"])

    def test_npc_without_romance_declines(self) -> None:
        decision = self.T.npc_decide_invitation("npc_fuyutsuki_iori", "date")
        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["reason_code"], "not_interested")

    def test_npc_with_partner_declines(self) -> None:
        decision = self.T.npc_decide_invitation("npc_sakaki_juri", "date")
        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["reason_code"], "has_partner")

    def test_teacher_boundary(self) -> None:
        decision = self.T.npc_decide_invitation("npc_tsukishima_kaoru", "date")
        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["reason_code"], "boundary_teacher")
        confession = self.T.npc_decide_confession("npc_tsukishima_kaoru")
        self.assertEqual(confession["decision"], "decline")

    def test_confession_requires_time_and_history(self) -> None:
        target = "npc_amano_rin"
        self.session.relationships.set_values(
            "player", target,
            {"familiarity": 80, "trust": 80, "closeness": 80, "attraction": 80,
             "romantic_interest": 80, "comfort": 80, "conflict": 0},
        )
        self.session.relationships.set_values(
            target, "player",
            {"familiarity": 80, "trust": 80, "closeness": 80, "attraction": 80,
             "romantic_interest": 80, "comfort": 80, "conflict": 0},
        )
        decision = self.T.npc_decide_confession(target)
        # 数值够了，但认识时间与共同经历不够 → 不能直接接受
        self.assertIn(decision["decision"], ("defer", "decline"))

    def test_confession_accept_after_real_history(self) -> None:
        target = "npc_amano_rin"
        rel = self.session.relationships.get(target, "player")
        rel.first_met_day = -40
        rel.shared_experiences = [f"{i}:date" for i in range(8)]
        self.session.relationships.save(rel)
        self.session.relationships.set_values(
            target, "player",
            {"familiarity": 75, "trust": 70, "closeness": 70, "attraction": 65,
             "romantic_interest": 70, "comfort": 70, "conflict": 0},
        )
        decision = self.T.npc_decide_confession(target)
        self.assertEqual(decision["decision"], "accept")


class TestStages(EngineTestCase):
    def test_stage_progression_requires_more_than_numbers(self) -> None:
        target = "npc_amano_rin"
        self.session.relationships.set_values(
            "player", target,
            {"familiarity": 70, "trust": 60, "closeness": 60, "attraction": 70,
             "romantic_interest": 70, "comfort": 60, "conflict": 0},
        )
        self.session.relationships.refresh_stage("player", target)
        stage = self.session.relationships.get("player", target).stage
        # 缺少共同经历 → 不能进入 ambiguous
        self.assertNotEqual(stage, RelationshipStage.AMBIGUOUS.value)

    def test_conflict_creates_strained(self) -> None:
        target = "npc_kurosawa_daichi"
        self.session.relationships.set_values(
            "player", target, {"familiarity": 50, "conflict": 70, "comfort": 20}
        )
        _before, after = self.session.relationships.refresh_stage("player", target)
        self.assertEqual(after, RelationshipStage.STRAINED.value)

    def test_friendship_high_but_no_romance(self) -> None:
        """非常亲密的朋友 ≠ 暧昧。"""
        target = "npc_takahashi_nao"
        self.session.relationships.set_values(
            "player", target,
            {"familiarity": 85, "trust": 80, "closeness": 90, "attraction": 20,
             "romantic_interest": 10, "comfort": 95, "conflict": 5},
        )
        _b, after = self.session.relationships.refresh_stage("player", target)
        self.assertEqual(after, RelationshipStage.CLOSE_FRIEND.value)

    def test_natural_decay(self) -> None:
        target = "npc_shirai_kanade"
        self.session.relationships.set_values("player", target, {"familiarity": 60, "closeness": 50})
        rel = self.session.relationships.get("player", target)
        rel.last_interaction_day = 0
        self.session.relationships.save(rel)
        changed = self.session.relationships.apply_natural_decay(current_day=21)
        self.assertTrue(any(c["key"] == f"player->{target}" for c in changed))


if __name__ == "__main__":
    unittest.main()
