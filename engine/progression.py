"""成长系统：技能 XP / 知识 XP / 等级、防刷机制。

防刷三件套
----------
* ``daily_training_limit``  —— 单日 XP 上限（技能 60 / 知识 45）
* ``diminishing_returns``   —— 同一技能当日第 4 次起收益衰减
* ``difficulty_requirement``—— 等级越高，越低难度的行动给不了经验
"""

from __future__ import annotations

from typing import Any

from .models import Difficulty, ValidationError, clamp_int

DEFAULT_XP_THRESHOLDS: list[int] = [0, 100, 300, 700, 1400, 2600]

DEFAULT_DIFFICULTY_REQUIREMENT: dict[int, str] = {
    0: "very_easy",
    1: "very_easy",
    2: "easy",
    3: "normal",
    4: "hard",
    5: "very_hard",
}

#: XP 来源 -> 基础经验
XP_SOURCES: dict[str, int] = {
    "use": 6,            # 在行动中使用
    "practice": 10,      # 专门练习
    "study": 12,         # 学习 / 上课
    "taught": 15,        # 被 NPC 教
    "club": 10,          # 社团活动
    "reading": 8,        # 阅读
    "event": 14,         # 事件
    "lesson": 12,        # 课程
    "competition": 18,   # 比赛 / 演出
    "conversation": 4,   # 聊天中自然获得
    "observation": 5,    # 观察
}

RESULT_MULTIPLIER: dict[str, float] = {
    "strong_success": 1.4,
    "success": 1.0,
    "failure": 0.4,        # 失败也给经验
    "major_failure": 0.4,
}

DIFFICULTY_MULTIPLIER: dict[str, float] = {
    "very_easy": 0.6,
    "easy": 0.8,
    "normal": 1.0,
    "hard": 1.3,
    "very_hard": 1.6,
    "extreme": 2.0,
}


def xp_thresholds(config: dict[str, Any] | None = None) -> list[int]:
    if config:
        value = (config.get("progression") or {}).get("xp_per_level")
        if isinstance(value, list) and len(value) == 6:
            return [int(x) for x in value]
    return list(DEFAULT_XP_THRESHOLDS)


def level_for_xp(xp: int, config: dict[str, Any] | None = None) -> int:
    """累计 XP -> 等级 (0-5)。"""
    thresholds = xp_thresholds(config)
    level = 0
    for i, need in enumerate(thresholds):
        if xp >= need:
            level = i
    return clamp_int(level, 0, 5)


def xp_for_next_level(xp: int, config: dict[str, Any] | None = None) -> dict[str, int]:
    thresholds = xp_thresholds(config)
    level = level_for_xp(xp, config)
    if level >= 5:
        return {"level": 5, "current": xp, "next_at": thresholds[5], "remaining": 0}
    next_at = thresholds[level + 1]
    return {"level": level, "current": xp, "next_at": next_at, "remaining": max(0, next_at - xp)}


def difficulty_requirement_met(level: int, difficulty: Difficulty | str | None, config: dict[str, Any] | None = None) -> bool:
    """等级 ``level`` 的技能，做 ``difficulty`` 难度的事还能不能长经验。"""
    table = DEFAULT_DIFFICULTY_REQUIREMENT
    if config:
        raw = (config.get("progression") or {}).get("difficulty_requirement")
        if isinstance(raw, dict):
            table = {int(k): str(v) for k, v in raw.items()}
    required = Difficulty.parse(table.get(clamp_int(level, 0, 5), "very_easy"))
    actual = Difficulty.parse(difficulty or "normal")
    return actual.dc >= required.dc


def compute_xp_gain(
    *,
    source: str = "use",
    result: str | None = "success",
    difficulty: Difficulty | str | None = "normal",
    level: int = 0,
    uses_today: int = 0,
    base_override: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """计算一次 XP 收益（未套用每日上限）。"""
    prog = (config or {}).get("progression", {}) if config else {}
    base = base_override if base_override is not None else XP_SOURCES.get(source, 6)
    reasons: list[str] = [f"source:{source}={base}"]

    mult = RESULT_MULTIPLIER.get(str(result or "success"), 1.0)
    if result:
        reasons.append(f"result:{result}x{mult}")

    diff_key = Difficulty.parse(difficulty or "normal").name.lower()
    dmult = DIFFICULTY_MULTIPLIER.get(diff_key, 1.0)
    reasons.append(f"difficulty:{diff_key}x{dmult}")

    value = base * mult * dmult

    if not difficulty_requirement_met(level, difficulty, config):
        return {
            "xp": 0,
            "blocked": True,
            "reason": f"Lv.{level} 已经不能从 {diff_key} 难度的行动中获得经验了",
            "detail": reasons,
        }

    threshold = int(prog.get("diminishing_returns_after", 3))
    factor = float(prog.get("diminishing_factor", 0.5))
    if uses_today >= threshold:
        over = uses_today - threshold + 1
        decay = factor**over
        value *= decay
        reasons.append(f"diminishing(x{over})x{round(decay, 3)}")

    minimum = int(prog.get("minimum_gain", 1))
    gained = max(minimum if value > 0 else 0, int(round(value)))
    return {"xp": gained, "blocked": False, "reason": "", "detail": reasons}


class ProgressionManager:
    """管理玩家与 NPC 的技能 / 知识成长。"""

    def __init__(self, state: Any) -> None:
        self.state = state

    # ------------------------------------------------------------------
    def _character(self, actor_id: str) -> dict[str, Any]:
        if actor_id == "player":
            return self.state.player
        npc = self.state.npcs.get(actor_id)
        if npc is None:
            raise ValidationError(f"未知角色: {actor_id}")
        return npc

    def _daily(self, character: dict[str, Any]) -> dict[str, Any]:
        today = self.state.world.get("date", "")
        daily = character.setdefault("daily", {})
        if daily.get("date") != today:
            daily.clear()
            daily["date"] = today
            daily["skill_xp"] = 0
            daily["knowledge_xp"] = 0
            daily["uses"] = {}
        daily.setdefault("skill_xp", 0)
        daily.setdefault("knowledge_xp", 0)
        daily.setdefault("uses", {})
        return daily

    # ------------------------------------------------------------------
    def award(
        self,
        actor_id: str,
        *,
        kind: str,
        entry_id: str,
        source: str = "use",
        result: str | None = "success",
        difficulty: Difficulty | str | None = "normal",
        amount: int | None = None,
    ) -> dict[str, Any]:
        """给 ``actor_id`` 的技能或知识加经验。

        kind: ``"skill"`` 或 ``"knowledge"``。
        返回 ``{"gained": int, "level_before": int, "level_after": int, "level_up": bool, ...}``
        """
        if kind not in ("skill", "knowledge"):
            raise ValidationError("kind 必须是 'skill' 或 'knowledge'")
        character = self._character(actor_id)
        config = self.state.config

        book = character.setdefault(kind + "s" if kind == "skill" else "knowledge", {})
        xp_book = character.setdefault("skill_xp" if kind == "skill" else "knowledge_xp", {})

        current_level = int(book.get(entry_id, 0))
        current_xp = int(xp_book.get(entry_id, 0))

        daily = self._daily(character)
        uses_today = int(daily["uses"].get(entry_id, 0))

        gain = compute_xp_gain(
            source=source,
            result=result,
            difficulty=difficulty,
            level=current_level,
            uses_today=uses_today,
            base_override=amount,
            config=config,
        )
        daily["uses"][entry_id] = uses_today + 1

        if gain["blocked"]:
            return {
                "actor": actor_id, "kind": kind, "id": entry_id, "gained": 0,
                "level_before": current_level, "level_after": current_level,
                "level_up": False, "blocked": True, "reason": gain["reason"],
                "xp_total": current_xp,
            }

        cap_key = "daily_skill_xp_cap" if kind == "skill" else "daily_knowledge_xp_cap"
        cap = int((config.get("progression") or {}).get(cap_key, 60 if kind == "skill" else 45))
        spent_key = "skill_xp" if kind == "skill" else "knowledge_xp"
        already = int(daily[spent_key])
        allowed = max(0, cap - already)
        gained = min(gain["xp"], allowed)
        capped = gained < gain["xp"]
        daily[spent_key] = already + gained

        new_xp = current_xp + gained
        xp_book[entry_id] = new_xp
        new_level = level_for_xp(new_xp, config)
        # 等级不因为其他原因回落
        new_level = max(new_level, current_level)
        book[entry_id] = new_level

        return {
            "actor": actor_id,
            "kind": kind,
            "id": entry_id,
            "gained": gained,
            "requested": gain["xp"],
            "capped_by_daily_limit": capped,
            "level_before": current_level,
            "level_after": new_level,
            "level_up": new_level > current_level,
            "xp_total": new_xp,
            "progress": xp_for_next_level(new_xp, config),
            "blocked": False,
            "detail": gain["detail"],
        }

    # ------------------------------------------------------------------
    def get_level(self, actor_id: str, kind: str, entry_id: str) -> int:
        character = self._character(actor_id)
        book_key = "skills" if kind == "skill" else "knowledge"
        return int((character.get(book_key) or {}).get(entry_id, 0))

    def set_level(self, actor_id: str, kind: str, entry_id: str, level: int) -> dict[str, Any]:
        """直接设定等级（仅用于角色创建 / 数据导入，不是游戏内成长路径）。"""
        character = self._character(actor_id)
        book_key = "skills" if kind == "skill" else "knowledge"
        xp_key = "skill_xp" if kind == "skill" else "knowledge_xp"
        level = clamp_int(level, 0, 5)
        character.setdefault(book_key, {})[entry_id] = level
        character.setdefault(xp_key, {})[entry_id] = xp_thresholds(self.state.config)[level]
        return {"actor": actor_id, "kind": kind, "id": entry_id, "level": level}

    def format_growth(self, awards: list[dict[str, Any]], registry: Any = None) -> str:
        """生成【成长】区块文本。"""
        lines: list[str] = []
        for award in awards:
            if not award or award.get("gained", 0) <= 0:
                continue
            name = award["id"]
            if registry is not None:
                try:
                    entry = registry.get(award["kind"], award["id"])
                    name = entry.get("name", name) if entry else name
                except Exception:  # pragma: no cover - 名称查询失败不应影响结算
                    pass
            suffix = "知识" if award["kind"] == "knowledge" else ""
            lines.append(f"{name}{suffix} +{award['gained']} XP")
            if award.get("level_up"):
                lines.append(f"  → 提升到 Lv.{award['level_after']}")
        if not lines:
            return ""
        return "【成长】\n\n" + "\n".join(lines)
