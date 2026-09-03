"""检定系统测试：公式、DC、成功等级、Natural 20/1、社交自主权、RNG 可复现。"""

from __future__ import annotations

import unittest

from tests.helpers import EngineTestCase

from engine.checks import (
    NPC_AUTONOMY_NOTE,
    attribute_modifier,
    classify_margin,
    knowledge_modifier,
    needs_check,
    normalize_situational,
    perform_check,
    shift_result,
    skill_modifier,
)
from engine.models import CheckResult, Difficulty
from engine.rng import GameRNG


class TestFormulas(unittest.TestCase):
    def test_attribute_modifier(self) -> None:
        self.assertEqual(attribute_modifier(5), 0)
        self.assertEqual(attribute_modifier(8), 3)
        self.assertEqual(attribute_modifier(1), -4)

    def test_skill_modifier(self) -> None:
        for level in range(6):
            self.assertEqual(skill_modifier(level), level * 2)
        self.assertEqual(skill_modifier(99), 10)

    def test_knowledge_modifier_range(self) -> None:
        values = [knowledge_modifier(level) for level in range(6)]
        self.assertEqual(values, [0, 0, 1, 2, 3, 3])
        self.assertTrue(all(0 <= v <= 3 for v in values))

    def test_difficulty_dc_table(self) -> None:
        self.assertEqual(Difficulty.VERY_EASY.dc, 8)
        self.assertEqual(Difficulty.EASY.dc, 11)
        self.assertEqual(Difficulty.NORMAL.dc, 14)
        self.assertEqual(Difficulty.HARD.dc, 17)
        self.assertEqual(Difficulty.VERY_HARD.dc, 20)
        self.assertEqual(Difficulty.EXTREME.dc, 23)

    def test_situational_clamped(self) -> None:
        value, _ = normalize_situational({"a": 10, "b": 4})
        self.assertEqual(value, 5)
        value, _ = normalize_situational([("a", -9)])
        self.assertEqual(value, -5)

    def test_margin_classification(self) -> None:
        self.assertEqual(classify_margin(5), CheckResult.STRONG_SUCCESS)
        self.assertEqual(classify_margin(0), CheckResult.SUCCESS)
        self.assertEqual(classify_margin(-4), CheckResult.FAILURE)
        self.assertEqual(classify_margin(-5), CheckResult.MAJOR_FAILURE)

    def test_shift_result_bounded(self) -> None:
        self.assertEqual(shift_result(CheckResult.STRONG_SUCCESS, 1), CheckResult.STRONG_SUCCESS)
        self.assertEqual(shift_result(CheckResult.MAJOR_FAILURE, -1), CheckResult.MAJOR_FAILURE)
        self.assertEqual(shift_result(CheckResult.FAILURE, 1), CheckResult.SUCCESS)


class TestNaturalRolls(unittest.TestCase):
    def test_natural_20_upgrades(self) -> None:
        rng = GameRNG(seed=1)
        found = False
        for _ in range(400):
            outcome = perform_check(rng, attribute_value=5, difficulty="extreme")
            if outcome.roll == 20:
                found = True
                # 20 + 0 = 20 vs DC 23 -> margin -3 -> failure -> 提升为 success
                self.assertEqual(outcome.natural, "natural_20")
                self.assertEqual(outcome.result, CheckResult.SUCCESS.value)
                break
        self.assertTrue(found, "400 次里应该至少出现一次 natural 20")

    def test_natural_1_downgrades(self) -> None:
        rng = GameRNG(seed=2)
        for _ in range(400):
            outcome = perform_check(rng, attribute_value=10, skill_level=5, difficulty="very_easy")
            if outcome.roll == 1:
                self.assertEqual(outcome.natural, "natural_1")
                # 1+5+10=16 vs 8 -> strong_success -> 降一级
                self.assertEqual(outcome.result, CheckResult.SUCCESS.value)
                return
        self.fail("400 次里应该至少出现一次 natural 1")


class TestSocialAutonomy(unittest.TestCase):
    def test_social_check_carries_autonomy_note(self) -> None:
        rng = GameRNG(seed=3)
        outcome = perform_check(rng, attribute_value=8, action_type="persuade")
        self.assertEqual(outcome.npc_autonomy_note, NPC_AUTONOMY_NOTE)

    def test_non_social_check_has_no_note(self) -> None:
        rng = GameRNG(seed=3)
        outcome = perform_check(rng, attribute_value=8, action_type="cook")
        self.assertIsNone(outcome.npc_autonomy_note)

    def test_check_result_never_contains_npc_decision(self) -> None:
        rng = GameRNG(seed=4)
        outcome = perform_check(rng, attribute_value=10, skill_level=5, action_type="invite").to_dict()
        for forbidden in ("accepted", "npc_accepts", "consent", "agreed"):
            self.assertNotIn(forbidden, outcome)


class TestNeedsCheck(unittest.TestCase):
    def test_trivial_actions_skip_roll(self) -> None:
        needed, _ = needs_check("buy")
        self.assertFalse(needed)

    def test_impossible_to_fail_skips_roll(self) -> None:
        needed, reason = needs_check("cook", difficulty="very_easy", skill_level=5, attribute_value=8)
        self.assertFalse(needed)
        self.assertIn("不可能失败", reason)

    def test_normal_case_rolls(self) -> None:
        needed, _ = needs_check("cook", difficulty="hard", skill_level=1, attribute_value=5)
        self.assertTrue(needed)


class TestRNGDeterminism(unittest.TestCase):
    def test_same_seed_same_rolls(self) -> None:
        a = GameRNG(seed=42)
        b = GameRNG(seed=42)
        self.assertEqual([a.d20() for _ in range(30)], [b.d20() for _ in range(30)])

    def test_state_roundtrip(self) -> None:
        rng = GameRNG(seed=99)
        [rng.d20() for _ in range(5)]
        snapshot = rng.export_state()
        expected = [rng.d20() for _ in range(5)]
        restored = GameRNG(seed=1)
        restored.restore_state(snapshot)
        self.assertEqual([restored.d20() for _ in range(5)], expected)

    def test_rolls_are_logged(self) -> None:
        rng = GameRNG(seed=5)
        rng.d20(reason="test")
        self.assertEqual(rng.log[-1]["kind"], "d20")
        self.assertEqual(rng.log[-1]["reason"], "test")


class TestCheckViaTools(EngineTestCase):
    def test_resolve_check_tool(self) -> None:
        result = self.T.resolve_check(
            action_type="talk", attribute="charm", skill="conversation", difficulty="normal"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], result["roll"] + result["attribute_modifier"]
                         + result["skill_modifier"] + result["knowledge_modifier"]
                         + result["situational_modifier"])
        self.assertEqual(result["margin"], result["total"] - result["dc"])
        self.assertIn(result["result"], [r.value for r in CheckResult])

    def test_unknown_skill_rejected(self) -> None:
        result = self.T.resolve_check(action_type="talk", attribute="charm", skill="不存在的技能")
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
