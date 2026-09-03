"""成长系统测试：等级门槛、失败给经验、每日上限、递减、难度门槛。"""

from __future__ import annotations

import unittest

from tests.helpers import EngineTestCase

from engine.progression import (
    compute_xp_gain,
    difficulty_requirement_met,
    level_for_xp,
    xp_for_next_level,
)


class TestXPMath(unittest.TestCase):
    def test_level_thresholds(self) -> None:
        self.assertEqual(level_for_xp(0), 0)
        self.assertEqual(level_for_xp(99), 0)
        self.assertEqual(level_for_xp(100), 1)
        self.assertEqual(level_for_xp(300), 2)
        self.assertEqual(level_for_xp(700), 3)
        self.assertEqual(level_for_xp(1400), 4)
        self.assertEqual(level_for_xp(2600), 5)
        self.assertEqual(level_for_xp(99999), 5)

    def test_next_level_progress(self) -> None:
        progress = xp_for_next_level(150)
        self.assertEqual(progress["level"], 1)
        self.assertEqual(progress["next_at"], 300)
        self.assertEqual(progress["remaining"], 150)

    def test_failure_still_grants_xp(self) -> None:
        gain = compute_xp_gain(source="use", result="failure", difficulty="normal", level=0)
        self.assertGreater(gain["xp"], 0)
        success = compute_xp_gain(source="use", result="success", difficulty="normal", level=0)
        self.assertGreater(success["xp"], gain["xp"])

    def test_difficulty_requirement(self) -> None:
        self.assertTrue(difficulty_requirement_met(0, "very_easy"))
        self.assertFalse(difficulty_requirement_met(3, "easy"))
        self.assertTrue(difficulty_requirement_met(3, "normal"))
        self.assertFalse(difficulty_requirement_met(5, "hard"))
        self.assertTrue(difficulty_requirement_met(5, "very_hard"))

    def test_blocked_gain_returns_zero(self) -> None:
        gain = compute_xp_gain(source="practice", difficulty="very_easy", level=4)
        self.assertTrue(gain["blocked"])
        self.assertEqual(gain["xp"], 0)

    def test_diminishing_returns(self) -> None:
        base = compute_xp_gain(source="practice", difficulty="normal", level=0, uses_today=0)["xp"]
        later = compute_xp_gain(source="practice", difficulty="normal", level=0, uses_today=6)["xp"]
        self.assertLess(later, base)


class TestProgressionManager(EngineTestCase):
    def test_award_increases_xp_and_level(self) -> None:
        before = self.T.get_player_state()["skills"].get("study", 0)
        for _ in range(3):
            self.session.progression.award(
                "player", kind="skill", entry_id="study", source="study", difficulty="normal"
            )
        state = self.T.get_player_state()
        self.assertGreater(state["skill_xp"]["study"], 0)
        self.assertGreaterEqual(state["skills"]["study"], before)

    def test_daily_cap_enforced(self) -> None:
        total = 0
        for _ in range(40):
            award = self.session.progression.award(
                "player", kind="skill", entry_id="study", source="taught", difficulty="hard"
            )
            total += award["gained"]
        cap = self.session.state.cfg("progression.daily_skill_xp_cap", 60)
        self.assertLessEqual(total, cap)

    def test_knowledge_cap_separate_from_skill(self) -> None:
        for _ in range(30):
            self.session.progression.award("player", kind="skill", entry_id="study", source="study")
            self.session.progression.award("player", kind="knowledge", entry_id="literature", source="reading")
        daily = self.session.state.player["daily"]
        self.assertLessEqual(daily["skill_xp"], self.session.state.cfg("progression.daily_skill_xp_cap", 60))
        self.assertLessEqual(daily["knowledge_xp"], self.session.state.cfg("progression.daily_knowledge_xp_cap", 45))

    def test_daily_counters_reset_next_day(self) -> None:
        self.session.progression.award("player", kind="skill", entry_id="study", source="study")
        self.assertGreater(self.session.state.player["daily"]["skill_xp"], 0)
        self.T.advance_time(60 * 20, reason="到第二天")
        self.session.progression.award("player", kind="skill", entry_id="study", source="study")
        self.assertLessEqual(self.session.state.player["daily"]["skill_xp"], 20)

    def test_action_grants_xp(self) -> None:
        result = self.T.perform_action("study", skill="study", knowledge="literature", minutes=60)
        self.assertTrue(result["ok"])
        self.assertTrue(any(a["gained"] > 0 for a in result["xp"]))

    def test_level_never_decreases(self) -> None:
        self.session.progression.set_level("player", "skill", "cooking", 3)
        self.session.progression.award("player", kind="skill", entry_id="cooking", source="use", difficulty="hard")
        self.assertGreaterEqual(self.T.get_player_state()["skills"]["cooking"], 3)


if __name__ == "__main__":
    unittest.main()
