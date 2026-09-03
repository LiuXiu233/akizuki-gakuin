"""D20 判定系统。

    D20 + 属性修正 + 技能修正 + 知识修正 + 情境修正   VS   DC

铁律
----
1. 社交检定 **只能** 决定玩家表达得如何，绝不能决定 NPC 的选择。
2. Natural 20 不能完成物理上不可能的事，也不能剥夺 NPC 自主权。
3. Natural 1 不能凭空制造荒谬灾难。
4. 结果毫无悬念的行动不需要检定。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .models import (
    CheckResult,
    CheckOutcome,
    Difficulty,
    RESULT_ORDER,
    ValidationError,
    clamp_int,
)
from .rng import GameRNG

# 知识等级 -> 检定修正 (0 ~ +3)
KNOWLEDGE_MODIFIER_TABLE: dict[int, int] = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 3}

SITUATIONAL_MIN = -5
SITUATIONAL_MAX = 5

#: 社交类行动 —— 这些检定绝不产生"NPC 因此答应"的结果。
SOCIAL_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "talk", "small_talk", "conversation", "deep_talk", "persuade", "invite",
        "flirt", "confess", "comfort", "apologize", "joke", "compliment",
        "ask_contact", "negotiate", "introduce", "read_the_room", "empathize",
        "social", "romance", "date", "tease", "share_topic",
    }
)

NPC_AUTONOMY_NOTE = (
    "社交检定只反映玩家的表达效果与留下的印象。"
    "对方是否答应，由其人格、关系、当前情绪、个人边界与过去经历决定，"
    "不受本次骰点影响（Natural 20 也不例外）。"
)

#: 不需要骰子的典型行动（结果毫无悬念）
NO_ROLL_ACTIONS: frozenset[str] = frozenset(
    {
        "move", "walk", "buy", "buy_drink", "wait", "rest", "sleep", "nap",
        "greet", "look", "observe_casual", "eat", "leave", "check_phone",
        "read_casual", "commute", "attend_class", "sit",
    }
)


def attribute_modifier(attribute_value: int) -> int:
    """属性修正 = attribute - 5。"""
    return int(attribute_value) - 5


def skill_modifier(skill_level: int) -> int:
    """技能修正 = level * 2。"""
    return clamp_int(skill_level, 0, 5) * 2


def knowledge_modifier(knowledge_level: int) -> int:
    """知识修正 = 0 ~ +3。"""
    return KNOWLEDGE_MODIFIER_TABLE.get(clamp_int(knowledge_level, 0, 5), 0)


def normalize_situational(modifiers: Any) -> tuple[int, list[dict[str, Any]]]:
    """把 Agent 传来的情境修正裁剪到 -5 ~ +5，并返回明细。

    接受形式：
      * int / float
      * {"名称": 值, ...}
      * [{"name": ..., "value": ...}, ...] 或 [("name", value), ...]
    """
    detail: list[dict[str, Any]] = []
    total = 0.0
    if modifiers is None:
        return 0, detail
    if isinstance(modifiers, (int, float)):
        total = float(modifiers)
        detail.append({"name": "situational", "value": float(modifiers)})
    elif isinstance(modifiers, Mapping):
        for name, value in modifiers.items():
            try:
                v = float(value)
            except (TypeError, ValueError):
                raise ValidationError(f"情境修正 {name!r} 的值必须是数字")
            total += v
            detail.append({"name": str(name), "value": v})
    elif isinstance(modifiers, Iterable):
        for item in modifiers:
            if isinstance(item, Mapping):
                name = str(item.get("name", "situational"))
                v = float(item.get("value", 0))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                name, v = str(item[0]), float(item[1])
            elif isinstance(item, (int, float)):
                name, v = "situational", float(item)
            else:
                raise ValidationError(f"无法解析的情境修正项: {item!r}")
            total += v
            detail.append({"name": name, "value": v})
    else:
        raise ValidationError(f"无法解析的情境修正: {modifiers!r}")

    clipped = clamp_int(total, SITUATIONAL_MIN, SITUATIONAL_MAX)
    if clipped != int(round(total)):
        detail.append({"name": "clipped_to_range", "value": clipped - int(round(total))})
    return clipped, detail


def classify_margin(margin: int) -> CheckResult:
    """margin -> 成功等级。"""
    if margin >= 5:
        return CheckResult.STRONG_SUCCESS
    if margin >= 0:
        return CheckResult.SUCCESS
    if margin >= -4:
        return CheckResult.FAILURE
    return CheckResult.MAJOR_FAILURE


def shift_result(result: CheckResult, steps: int) -> CheckResult:
    """Natural 20 / Natural 1 的等级偏移（带边界）。"""
    index = RESULT_ORDER.index(result)
    index = max(0, min(len(RESULT_ORDER) - 1, index + steps))
    return RESULT_ORDER[index]


def is_social(action_type: str) -> bool:
    return str(action_type or "").lower() in SOCIAL_ACTION_TYPES


def needs_check(
    action_type: str,
    *,
    difficulty: Difficulty | str | None = "normal",
    skill_level: int = 0,
    attribute_value: int = 5,
    forced: bool | None = None,
) -> tuple[bool, str]:
    """判断一个行动是否需要检定。

    返回 ``(需要检定?, 原因)``。原则：只有存在**合理失败概率**时才骰。
    """
    if forced is not None:
        return bool(forced), "由调用方显式指定"
    action = str(action_type or "").lower()
    if action in NO_ROLL_ACTIONS:
        return False, "结果毫无悬念的日常行动，不需要判定"
    diff = Difficulty.parse(difficulty)
    best = 20 + attribute_modifier(attribute_value) + skill_modifier(skill_level) + SITUATIONAL_MAX
    worst = 1 + attribute_modifier(attribute_value) + skill_modifier(skill_level) + SITUATIONAL_MIN
    if worst >= diff.dc:
        return False, "以当前能力不可能失败，自动成功"
    if best < diff.dc:
        return False, "以当前能力不可能成功，自动失败（应改为叙述性后果）"
    return True, "存在合理的成功与失败概率"


def perform_check(
    rng: GameRNG,
    *,
    attribute_value: int,
    skill_level: int = 0,
    knowledge_level: int = 0,
    difficulty: Difficulty | str | int = "normal",
    situational_modifiers: Any = None,
    actor_id: str = "",
    action_type: str = "",
    attribute: str = "",
    skill: str | None = None,
    knowledge: str | None = None,
) -> CheckOutcome:
    """执行一次真实的 D20 判定，返回不可篡改的结果结构。"""
    diff = Difficulty.parse(difficulty)
    roll = rng.d20(reason=f"{actor_id or 'actor'}:{action_type or 'check'}")

    attr_mod = attribute_modifier(attribute_value)
    skill_mod = skill_modifier(skill_level)
    know_mod = knowledge_modifier(knowledge_level)
    sit_mod, sit_detail = normalize_situational(situational_modifiers)

    total = roll + attr_mod + skill_mod + know_mod + sit_mod
    margin = total - diff.dc
    result = classify_margin(margin)

    natural: str | None = None
    if roll == 20:
        natural = "natural_20"
        result = shift_result(result, 1)
    elif roll == 1:
        natural = "natural_1"
        result = shift_result(result, -1)

    detail: list[dict[str, Any]] = [
        {"name": f"attribute:{attribute}" if attribute else "attribute", "value": attr_mod},
    ]
    if skill:
        detail.append({"name": f"skill:{skill}(Lv.{clamp_int(skill_level, 0, 5)})", "value": skill_mod})
    elif skill_mod:
        detail.append({"name": "skill", "value": skill_mod})
    if knowledge:
        detail.append(
            {"name": f"knowledge:{knowledge}(Lv.{clamp_int(knowledge_level, 0, 5)})", "value": know_mod}
        )
    detail.extend(sit_detail)

    return CheckOutcome(
        roll=roll,
        attribute_modifier=attr_mod,
        skill_modifier=skill_mod,
        knowledge_modifier=know_mod,
        situational_modifier=sit_mod,
        total=total,
        dc=diff.dc,
        margin=margin,
        result=result.value,
        natural=natural,
        actor_id=actor_id,
        action_type=action_type,
        attribute=attribute,
        skill=skill,
        knowledge=knowledge,
        difficulty=diff.name.lower(),
        modifiers_detail=detail,
        npc_autonomy_note=NPC_AUTONOMY_NOTE if is_social(action_type) else None,
    )


def format_check(outcome: CheckOutcome, *, title: str = "判定") -> str:
    """生成【判定】区块文本（供 Agent 直接输出）。"""
    lines = [f"【{title}】", ""]
    if outcome.action_type:
        lines.append(f"{outcome.action_type} 检定")
        lines.append("")
    lines.append(f"D20：{outcome.roll}" + (f"  ({outcome.natural})" if outcome.natural else ""))
    for item in outcome.modifiers_detail:
        value = item["value"]
        if value == 0:
            continue
        lines.append(f"{item['name']}：{value:+g}")
    lines.append("")
    lines.append(f"总计：{outcome.total}")
    lines.append(f"DC：{outcome.dc}")
    lines.append("")
    lines.append(CheckResult(outcome.result).label)
    return "\n".join(lines)
