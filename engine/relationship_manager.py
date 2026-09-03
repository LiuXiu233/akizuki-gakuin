"""关系与恋爱系统。

七维关系 (0-100)：familiarity / trust / closeness / attraction /
romantic_interest / comfort / conflict。

三条铁律
--------
1. **关系是单向的。** A 对 B 的感觉和 B 对 A 的感觉是两条独立记录。
2. **没有"好感 >= 80 自动恋爱"。** 阶段由 数值 + 共同经历 + 明确事件 + NPC 意愿 共同决定。
3. **骰子不能控制 NPC。** 是否答应邀请、是否接受告白，全部走
   :meth:`RelationshipManager.npc_decide_invitation` /
   :meth:`RelationshipManager.npc_decide_confession`，与任何检定结果无关。
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .models import (
    GameState,
    Relationship,
    RelationshipStage,
    RelationshipValues,
    RELATIONSHIP_DIMENSIONS,
    ValidationError,
    clamp_int,
    sanitize_text,
)

log = logging.getLogger("engine.relationship")

# ---------------------------------------------------------------------------
# 关系事件表
# ---------------------------------------------------------------------------
#: 每个事件的基础增量。**日常互动必须非常小**——恋爱只能靠长期积累。
RELATIONSHIP_EVENTS: dict[str, dict[str, Any]] = {
    # --- 极轻 ---
    "greeting": {"familiarity": 0.6, "comfort": 0.2, "weight": 0.3, "label": "打招呼"},
    "small_talk": {"familiarity": 1.0, "comfort": 0.5, "trust": 0.2, "weight": 0.5, "label": "闲聊"},
    "seen_around": {"familiarity": 0.4, "weight": 0.2, "label": "碰见"},
    # --- 日常 ---
    "conversation": {"familiarity": 1.4, "comfort": 0.8, "trust": 0.4, "closeness": 0.4, "weight": 0.8, "label": "聊天"},
    "shared_meal": {"familiarity": 1.5, "comfort": 1.2, "closeness": 1.0, "trust": 0.5, "weight": 1.0, "label": "一起吃饭"},
    "walk_home": {"familiarity": 1.2, "closeness": 1.4, "comfort": 1.0, "attraction": 0.6, "weight": 1.0, "label": "一起回家"},
    "study_together": {"familiarity": 1.2, "trust": 1.0, "comfort": 0.8, "weight": 0.9, "label": "一起学习"},
    "club_activity": {"familiarity": 1.3, "trust": 0.8, "comfort": 0.9, "closeness": 0.5, "weight": 0.9, "label": "社团活动"},
    "helped_them": {"trust": 2.2, "closeness": 1.2, "comfort": 0.8, "familiarity": 0.6, "weight": 1.4, "label": "帮了对方"},
    "received_help": {"trust": 1.4, "closeness": 1.0, "comfort": 1.0, "weight": 1.1, "label": "受到帮助"},
    "small_gift": {"closeness": 1.2, "comfort": 0.8, "attraction": 0.6, "trust": 0.5, "weight": 1.0, "label": "小礼物"},
    "compliment": {"comfort": 0.8, "attraction": 1.0, "closeness": 0.4, "weight": 0.8, "label": "称赞"},
    "shared_interest": {"closeness": 1.6, "comfort": 1.2, "familiarity": 0.8, "trust": 0.6, "weight": 1.2, "label": "发现共同兴趣"},
    "teasing": {"comfort": 0.8, "closeness": 0.6, "attraction": 0.5, "conflict": 0.3, "weight": 0.7, "label": "打趣"},
    # --- 深入 ---
    "deep_talk": {"trust": 2.4, "closeness": 2.2, "comfort": 1.4, "familiarity": 1.0, "weight": 1.8, "label": "深谈"},
    "shared_secret": {"trust": 3.5, "closeness": 3.0, "comfort": 1.0, "familiarity": 0.8, "weight": 2.5, "label": "分享秘密"},
    "comforted_them": {"trust": 3.0, "closeness": 2.6, "comfort": 1.8, "attraction": 0.8, "familiarity": 0.6, "weight": 2.2, "label": "在对方难过时陪着"},
    "exchanged_contact": {"familiarity": 2.0, "comfort": 1.5, "closeness": 1.0, "weight": 1.5, "label": "交换联系方式"},
    "messaged": {"familiarity": 0.8, "comfort": 0.6, "closeness": 0.4, "weight": 0.5, "label": "消息往来"},
    "kept_promise": {"trust": 2.8, "comfort": 1.0, "weight": 1.8, "label": "守约"},
    # --- 暧昧 / 恋爱 ---
    "flirt": {"attraction": 1.8, "romantic_interest": 1.0, "comfort": -0.2, "familiarity": 0.4, "weight": 1.2, "label": "调情"},
    "alone_together": {"attraction": 1.4, "romantic_interest": 1.2, "closeness": 1.4, "comfort": 0.6, "familiarity": 0.8, "weight": 1.4, "label": "单独相处"},
    "ambiguous_moment": {"attraction": 2.2, "romantic_interest": 2.0, "closeness": 1.2, "familiarity": 0.6, "weight": 1.8, "label": "暧昧时刻"},
    "date": {"attraction": 2.6, "romantic_interest": 2.6, "closeness": 2.2, "comfort": 1.2, "familiarity": 1.2, "weight": 2.5, "label": "约会"},
    "physical_closeness": {"attraction": 2.4, "romantic_interest": 1.8, "comfort": 0.6, "weight": 2.0, "label": "身体接近"},
    "kiss": {"attraction": 4.0, "romantic_interest": 4.0, "closeness": 3.0, "weight": 4.0, "label": "亲吻"},
    "intimacy": {"attraction": 4.0, "romantic_interest": 3.5, "closeness": 4.0, "trust": 2.0, "weight": 5.0, "label": "亲密关系"},
    "confession_accepted": {"romantic_interest": 8.0, "closeness": 6.0, "trust": 4.0, "comfort": 2.0, "weight": 8.0, "label": "告白被接受"},
    "confession_rejected": {"comfort": -8.0, "closeness": -3.0, "romantic_interest": -6.0, "conflict": 3.0, "weight": 8.0, "label": "告白被拒绝"},
    "relationship_confirmed": {"romantic_interest": 6.0, "closeness": 5.0, "trust": 3.0, "comfort": 3.0, "weight": 8.0, "label": "确认关系"},
    # --- 负面 ---
    "invite_declined": {"comfort": -0.8, "closeness": -0.3, "weight": 0.6, "label": "邀请被婉拒"},
    "declined_them": {"comfort": -1.2, "trust": -0.6, "conflict": 0.8, "weight": 0.8, "label": "拒绝了对方"},
    "misunderstanding": {"conflict": 3.0, "comfort": -2.0, "trust": -1.0, "weight": 2.0, "label": "误会"},
    "argument": {"conflict": 6.0, "comfort": -3.0, "trust": -2.0, "closeness": -1.0, "weight": 3.0, "label": "争执"},
    "jealousy": {"conflict": 2.5, "comfort": -1.5, "romantic_interest": 0.8, "weight": 1.8, "label": "吃醋"},
    "broke_promise": {"trust": -5.0, "conflict": 4.0, "comfort": -2.0, "weight": 3.5, "label": "失约"},
    "ignored_them": {"comfort": -1.5, "closeness": -1.0, "conflict": 1.0, "weight": 1.0, "label": "被无视"},
    "apology": {"conflict": -4.0, "comfort": 1.5, "trust": 0.8, "weight": 1.8, "label": "道歉"},
    "reconciliation": {"conflict": -6.0, "comfort": 2.5, "trust": 1.5, "closeness": 1.5, "weight": 2.5, "label": "和好"},
    "breakup": {"romantic_interest": -20.0, "closeness": -8.0, "comfort": -8.0, "conflict": 5.0, "weight": 9.0, "label": "分手"},
    "long_absence": {"familiarity": -1.0, "closeness": -1.5, "comfort": -0.5, "weight": 0.5, "label": "很久没见"},
}

#: 会被记入 shared_experiences 的事件
SIGNIFICANT_EVENTS = frozenset(
    {
        "date", "deep_talk", "shared_secret", "comforted_them", "club_activity",
        "ambiguous_moment", "kiss", "intimacy", "confession_accepted",
        "relationship_confirmed", "argument", "reconciliation", "shared_interest",
        "walk_home", "study_together", "shared_meal",
    }
)

#: 明确的恋爱推进事件——这些事件只能由 NPC 同意后才会发生
CONSENT_REQUIRED_EVENTS = frozenset(
    {"kiss", "intimacy", "confession_accepted", "relationship_confirmed", "date"}
)

ROMANTIC_DIMENSIONS = ("attraction", "romantic_interest")


# ---------------------------------------------------------------------------
# 人格气质
# ---------------------------------------------------------------------------

PACE_KEYWORDS = {
    "极慢": 0.55, "极端慢热": 0.55, "非常慢": 0.6, "慢热": 0.7, "很久": 0.75,
    "迟钝": 0.8, "慎重": 0.8, "小心": 0.8, "犹豫": 0.85,
    "主动": 1.2, "直接": 1.15, "纯粹": 1.1,
}


def npc_temperament(definition: dict[str, Any]) -> dict[str, Any]:
    """从 NPC 定义推导出关系推进的气质参数（确定性，不使用随机）。"""
    attitude = str(definition.get("relationship_attitude") or "")
    prefs = str(definition.get("romantic_preferences") or "")
    text = attitude + prefs

    pace = 1.0
    for keyword, factor in PACE_KEYWORDS.items():
        if keyword in text:
            pace = min(pace, factor) if factor < 1 else max(pace, factor)

    attrs = definition.get("attributes") or {}
    willpower = int(attrs.get("willpower", 5))
    perception = int(attrs.get("perception", 5))
    charm = int(attrs.get("charm", 5))

    return {
        "pace": round(pace, 2),
        # 意志高 = 不容易被推着走；感知高 = 更快注意到别人
        "trust_gain": round(1.0 + (perception - 5) * 0.04 - (willpower - 5) * 0.03, 2),
        "romance_gain": round(pace * (1.0 + (charm - 5) * 0.02), 2),
        "conflict_sensitivity": round(1.0 + (willpower - 5) * 0.05, 2),
        "romance_available": bool(definition.get("romance_available", True)),
        "has_partner": bool(definition.get("existing_partner")),
        "is_teacher": definition.get("role") == "teacher",
    }


# ---------------------------------------------------------------------------
# 管理器
# ---------------------------------------------------------------------------


class RelationshipManager:
    def __init__(self, state: GameState, rng: Any = None, npc_lookup: Any = None) -> None:
        self.state = state
        self.rng = rng
        #: 可选回调 ``(npc_id) -> definition``，由 NPCManager 注入
        self.npc_lookup = npc_lookup

    # ------------------------------------------------------------------
    # 基础存取
    # ------------------------------------------------------------------
    @staticmethod
    def key(source: str, target: str) -> str:
        return f"{source}->{target}"

    def _definition(self, character_id: str) -> dict[str, Any]:
        if self.npc_lookup is not None:
            try:
                return self.npc_lookup(character_id) or {}
            except Exception:  # pragma: no cover
                return {}
        return {}

    def get(self, source: str, target: str, *, create: bool = True) -> Relationship | None:
        if source == target:
            raise ValidationError("角色不能和自己建立关系")
        key = self.key(source, target)
        raw = self.state.relationships.get(key)
        if raw is None:
            if not create:
                return None
            rel = Relationship(source=source, target=target)
            rel.first_met_day = int(self.state.world.get("day_index", 0))
            self.state.relationships[key] = rel.to_dict()
            return rel
        return Relationship.from_dict(raw)

    def save(self, rel: Relationship) -> None:
        self.state.relationships[rel.key] = rel.to_dict()

    def pair(self, a: str, b: str) -> tuple[Relationship, Relationship]:
        return self.get(a, b), self.get(b, a)  # type: ignore[return-value]

    def all_for(self, character_id: str) -> dict[str, Relationship]:
        out: dict[str, Relationship] = {}
        for key, raw in self.state.relationships.items():
            if key.startswith(f"{character_id}->"):
                rel = Relationship.from_dict(raw)
                out[rel.target] = rel
        return out

    def set_values(self, source: str, target: str, values: dict[str, int], *, stage: str | None = None, notes: str = "") -> Relationship:
        """直接设定（仅用于初始化 / 数据导入，不是游戏内路径）。"""
        rel = self.get(source, target)
        assert rel is not None
        rel.values = RelationshipValues.from_dict({**rel.values.to_dict(), **values})
        if stage:
            rel.stage = str(stage)
        if notes:
            rel.notes = sanitize_text(notes, max_len=500, field_name="关系备注")
        self.save(rel)
        return rel

    # ------------------------------------------------------------------
    # 阶段
    # ------------------------------------------------------------------
    def compute_stage(self, rel: Relationship, reverse: Relationship | None = None) -> str:
        """阶段 = 数值 + 共同经历 + 明确事件 + NPC 意愿，四者共同决定。"""
        ev = rel.explicit_events or {}
        v = rel.values

        if ev.get("breakup"):
            return RelationshipStage.FORMER_PARTNER.value
        if ev.get("relationship_confirmed"):
            if v.conflict >= 65:
                return RelationshipStage.STRAINED.value
            return RelationshipStage.RELATIONSHIP.value
        if ev.get("dating_started"):
            if v.conflict >= 65:
                return RelationshipStage.STRAINED.value
            return RelationshipStage.DATING.value

        if v.conflict >= 55 and v.familiarity >= 25:
            return RelationshipStage.STRAINED.value

        shared = len(rel.shared_experiences)
        mutual_romance = True
        if reverse is not None:
            mutual_romance = reverse.values.romantic_interest >= 30 or reverse.values.attraction >= 40

        if (
            v.romantic_interest >= 45
            and v.attraction >= 45
            and v.closeness >= 45
            and v.trust >= 35
            and shared >= 3
            and mutual_romance
        ):
            return RelationshipStage.AMBIGUOUS.value
        if v.familiarity >= 60 and v.trust >= 55 and v.closeness >= 50 and v.comfort >= 55:
            return RelationshipStage.CLOSE_FRIEND.value
        if v.familiarity >= 30 and v.comfort >= 25:
            return RelationshipStage.FRIEND.value
        if v.familiarity >= 10:
            return RelationshipStage.ACQUAINTANCE.value
        return RelationshipStage.STRANGER.value

    def refresh_stage(self, source: str, target: str) -> tuple[str, str]:
        rel = self.get(source, target)
        reverse = self.get(target, source)
        assert rel is not None and reverse is not None
        before = rel.stage
        after = self.compute_stage(rel, reverse)
        if after != before:
            rel.stage = after
            rel.history.append(
                {
                    "day": self.state.world.get("day_index", 0),
                    "type": "stage_change",
                    "from": before,
                    "to": after,
                }
            )
            self.save(rel)
        return before, after

    # ------------------------------------------------------------------
    # 关系事件
    # ------------------------------------------------------------------
    def apply_event(
        self,
        source: str,
        target: str,
        event_type: str,
        *,
        intensity: float = 1.0,
        context: dict[str, Any] | None = None,
        bidirectional: bool = False,
        note: str = "",
    ) -> dict[str, Any]:
        """套用一次关系事件。返回的 ``changes`` 是隐藏信息，不要直接展示给玩家。"""
        if event_type not in RELATIONSHIP_EVENTS:
            raise ValidationError(
                f"未知关系事件: {event_type!r}。可用: {', '.join(sorted(RELATIONSHIP_EVENTS))}"
            )
        context = dict(context or {})
        intensity = max(0.2, min(2.5, float(intensity)))

        rel = self.get(source, target)
        reverse = self.get(target, source)
        assert rel is not None and reverse is not None

        template = RELATIONSHIP_EVENTS[event_type]
        definition = self._definition(target if source == "player" else source)
        temperament = npc_temperament(definition)

        day = int(self.state.world.get("day_index", 0))
        daily = rel.daily_gain if rel.daily_gain.get("day") == day else {"day": day, "total": 0.0, "events": {}}

        # --- 重复衰减 ---
        repeats = int(daily["events"].get(event_type, 0))
        repeat_factor = float(self.state.cfg("relationship.repeat_interaction_decay", 0.5)) ** repeats

        # --- 心情 / 场合 ---
        mood_factor = 1.0
        npc_state = self.state.npcs.get(target if source == "player" else source, {})
        npc_mood = (npc_state.get("status") or {}).get("mood", "normal")
        if npc_mood in ("stressed", "tired", "sick", "nervous"):
            mood_factor *= 0.7
        elif npc_mood in ("excited", "relaxed", "confident", "inspired"):
            mood_factor *= 1.15
        if context.get("atmosphere") == "good":
            mood_factor *= 1.15
        elif context.get("atmosphere") == "bad":
            mood_factor *= 0.75
        if context.get("private"):
            mood_factor *= 1.1
        if context.get("crowded"):
            mood_factor *= 0.9

        check_result = str(context.get("check_result") or "")
        if check_result == "strong_success":
            mood_factor *= 1.2
        elif check_result == "failure":
            mood_factor *= 0.7
        elif check_result == "major_failure":
            mood_factor *= 0.4

        deltas: dict[str, float] = {}
        for dim in RELATIONSHIP_DIMENSIONS:
            base = float(template.get(dim, 0.0))
            if base == 0.0:
                continue
            value = base * intensity * repeat_factor * mood_factor
            if dim in ROMANTIC_DIMENSIONS and value > 0:
                value *= temperament["romance_gain"]
                # 还不够熟的时候，恋爱兴趣几乎不会增长
                if rel.values.familiarity < 25:
                    value *= 0.25
                elif rel.values.familiarity < 40:
                    value *= 0.6
                if not temperament["romance_available"]:
                    value = 0.0
                elif temperament["has_partner"]:
                    value *= 0.15
            elif dim == "trust" and value > 0:
                value *= temperament["trust_gain"]
            elif dim == "conflict" and value > 0:
                value *= temperament["conflict_sensitivity"]
            deltas[dim] = value

        # --- 每日总量封顶 ---
        cap = float(self.state.cfg("relationship.max_relationship_gain_per_day", 12))
        positive_total = sum(v for v in deltas.values() if v > 0)
        already = float(daily.get("total", 0.0))
        if positive_total > 0 and already + positive_total > cap:
            allowed = max(0.0, cap - already)
            scale = allowed / positive_total if positive_total else 0.0
            for dim, value in list(deltas.items()):
                if value > 0:
                    deltas[dim] = value * scale
            positive_total *= scale

        # 累积不足 1 点的余量：日常互动每次只推动零点几点，
        # 如果直接取整，长期积累会被完全抹掉，恋爱就永远不可能发生。
        whole_deltas: dict[str, float] = {}
        for dim, value in deltas.items():
            total = value + float(rel.residual.get(dim, 0.0))
            whole = math.trunc(total)
            rel.residual[dim] = round(total - whole, 4)
            if whole:
                whole_deltas[dim] = float(whole)
        changes = rel.values.apply(whole_deltas)

        daily["total"] = already + positive_total
        daily["events"][event_type] = repeats + 1
        rel.daily_gain = daily
        rel.interaction_count += 1
        rel.last_interaction_day = day
        if rel.first_met_day is None:
            rel.first_met_day = day

        if event_type in SIGNIFICANT_EVENTS:
            tag = f"{day}:{event_type}"
            if tag not in rel.shared_experiences:
                rel.shared_experiences.append(tag)
                rel.shared_experiences = rel.shared_experiences[-60:]

        if event_type in ("confession_accepted", "relationship_confirmed"):
            rel.explicit_events["relationship_confirmed"] = day
        elif event_type == "date":
            rel.explicit_events.setdefault("first_date_day", day)
            rel.explicit_events["date_count"] = int(rel.explicit_events.get("date_count", 0)) + 1
        elif event_type == "breakup":
            rel.explicit_events["breakup"] = day
            rel.explicit_events.pop("relationship_confirmed", None)
            rel.explicit_events.pop("dating_started", None)
        elif event_type == "confession_rejected":
            rel.explicit_events["last_rejection_day"] = day
        elif event_type == "exchanged_contact":
            rel.explicit_events["contact_exchanged"] = True

        rel.history.append(
            {
                "day": day,
                "date": self.state.world.get("date"),
                "type": event_type,
                "intensity": round(intensity, 2),
                "note": sanitize_text(note, max_len=200, field_name="备注"),
            }
        )
        rel.history = rel.history[-60:]
        self.save(rel)

        stage_before, stage_after = self.refresh_stage(source, target)

        result = {
            "source": source,
            "target": target,
            "event": event_type,
            "label": template.get("label", event_type),
            "changes": {k: v for k, v in changes.items() if v},   # 隐藏信息
            "stage_before": stage_before,
            "stage_after": stage_after,
            "stage_changed": stage_before != stage_after,
            "narrative_hint": self.narrative_hint(event_type, changes, rel),
            "daily_cap_reached": daily["total"] >= cap - 0.01,
            "repeat_count": repeats,
        }

        if bidirectional:
            mirror = self.apply_event(
                target, source, event_type, intensity=intensity * 0.8, context=context, note=note
            )
            result["mirror"] = mirror
        return result

    # ------------------------------------------------------------------
    # 描述（对玩家可见的部分——绝不含数字）
    # ------------------------------------------------------------------
    def narrative_hint(self, event_type: str, changes: dict[str, int], rel: Relationship) -> str:
        """给 Agent 的叙事提示：用行为而不是数字表达关系变化。"""
        if not changes:
            return "这次互动没有改变什么——这也很正常。"
        gained = sorted(changes.items(), key=lambda kv: -abs(kv[1]))
        top, value = gained[0]
        if value < 0:
            table = {
                "comfort": "对方的语气比刚才客气了一点，那种客气不是好事。",
                "trust": "她/他把话说得更含糊了。",
                "closeness": "两人之间多出了半步的距离。",
                "romantic_interest": "某种正在生长的东西被按了回去。",
                "conflict": "有句话没说出口，但气氛已经变了。",
            }
            return table.get(top, "气氛冷了下来。")
        table = {
            "familiarity": "对方记住了刚才聊的内容——下次见面会更自然一点。",
            "trust": "对方多说了一句本来不打算说的话。",
            "closeness": "两个人站得比刚才近了一点，谁都没注意到。",
            "attraction": "对方的视线停留的时间比必要的长了一瞬。",
            "romantic_interest": "对方好像在想别的事，但没有走开。",
            "comfort": "沉默没有让任何人觉得需要找话说。",
        }
        return table.get(top, "气氛变得更好了一些。")

    def describe(self, source: str, target: str, *, debug: bool | None = None) -> dict[str, Any]:
        """给玩家看的关系描述。默认**不含任何数值**。"""
        rel = self.get(source, target, create=False)
        if rel is None:
            return {"target": target, "known": False, "label": "还不认识", "stage": "stranger"}
        if debug is None:
            debug = bool(self.state.cfg("visibility.debug_relationship_numbers", False))

        stage = RelationshipStage(rel.stage) if rel.stage in {s.value for s in RelationshipStage} else RelationshipStage.STRANGER
        v = rel.values
        hints: list[str] = []
        if v.familiarity >= 70:
            hints.append("你们已经很熟了")
        elif v.familiarity >= 40:
            hints.append("你们说得上话")
        if v.comfort >= 70:
            hints.append("在一起时不需要刻意找话题")
        if v.trust >= 65:
            hints.append("对方会跟你说一些不对别人说的事")
        if v.conflict >= 40:
            hints.append("有些事你们还没说开")
        if stage == RelationshipStage.AMBIGUOUS:
            hints.append("最近的气氛有点说不清楚")

        out: dict[str, Any] = {
            "target": target,
            "known": True,
            "stage": rel.stage,
            "label": stage.label,
            "hints": hints,
            "shared_experiences": len(rel.shared_experiences),
            "interactions": rel.interaction_count,
            "last_interaction_day": rel.last_interaction_day,
        }
        if debug:
            out["values"] = rel.values.to_dict()
            out["explicit_events"] = rel.explicit_events
            out["debug_warning"] = "debug_relationship_numbers 已开启；正常游戏中禁止把这些数字告诉玩家。"
        return out

    # ------------------------------------------------------------------
    # NPC 的决定（**不使用骰子**）
    # ------------------------------------------------------------------
    INVITE_REQUIREMENTS: dict[str, dict[str, int]] = {
        "casual": {"familiarity": 5, "comfort": 0},
        "group_activity": {"familiarity": 12, "comfort": 8},
        "meal": {"familiarity": 20, "comfort": 15},
        "study": {"familiarity": 18, "trust": 12},
        "one_on_one": {"familiarity": 35, "comfort": 30, "trust": 20},
        "walk_home": {"familiarity": 25, "comfort": 20},
        "date": {"familiarity": 45, "comfort": 45, "trust": 35, "romantic_interest": 35, "attraction": 30},
        "trip": {"familiarity": 55, "trust": 50, "comfort": 50},
        "intimate": {"familiarity": 65, "trust": 70, "comfort": 70, "romantic_interest": 65, "attraction": 60},
    }

    def npc_decide_invitation(
        self,
        npc_id: str,
        invite_type: str = "casual",
        *,
        actor_id: str = "player",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """NPC 是否接受邀请。**与任何检定结果无关。**

        决定依据：人格 / 关系 / 当前情绪 / 个人边界 / 恋爱倾向 / 日程 / 过去经历。
        """
        context = dict(context or {})
        invite_type = str(invite_type or "casual")
        if invite_type not in self.INVITE_REQUIREMENTS:
            invite_type = "casual"

        rel = self.get(npc_id, actor_id)
        assert rel is not None
        definition = self._definition(npc_id)
        temperament = npc_temperament(definition)
        npc_state = self.state.npcs.get(npc_id, {})
        status = npc_state.get("status") or {}
        v = rel.values

        romantic = invite_type in ("date", "intimate")
        reasons: list[str] = []

        def decline(code: str, text: str, *, alternative: str | None = None, tone: str = "polite") -> dict[str, Any]:
            return {
                "accepted": False,
                "npc_id": npc_id,
                "invite_type": invite_type,
                "reason_code": code,
                "reason": text,
                "tone": tone,
                "alternative": alternative,
                "stage": rel.stage,
                "autonomy_note": "这是 NPC 自己的决定，与玩家的检定结果无关。",
            }

        # 1) 硬边界
        if temperament["is_teacher"] and romantic:
            return decline("boundary_teacher", "对方是教职员，不存在这种可能。", tone="firm")
        if romantic and temperament["has_partner"]:
            return decline(
                "has_partner",
                "对方已经有交往对象，会礼貌但明确地划清界限。",
                alternative="普通朋友之间的邀约还是可以的",
                tone="clear",
            )
        if romantic and not temperament["romance_available"]:
            return decline(
                "not_interested",
                "对方明确表示过自己现在不考虑这类关系。这不是欲擒故纵，是真实立场。",
                alternative="作为朋友一起做点别的事",
                tone="clear",
            )

        # 2) 日程冲突
        if context.get("busy") or context.get("schedule_conflict"):
            return decline(
                "busy",
                "对方今天已经有安排了——这是真的有安排，不是借口。",
                alternative="改约其它时间",
            )

        # 3) 身体 / 情绪状态
        if int(status.get("energy", 100)) < 20 and invite_type not in ("casual",):
            return decline("exhausted", "对方看起来已经累到不想动了。", alternative="下次吧")
        if str(status.get("mood", "normal")) in ("sick",):
            return decline("unwell", "对方身体不太舒服。", alternative="改天")

        # 4) 关系门槛
        requirement = self.INVITE_REQUIREMENTS[invite_type]
        missing = {dim: need for dim, need in requirement.items() if getattr(v, dim) < need}
        if missing:
            worst = min(missing, key=lambda d: getattr(v, d) - missing[d])
            text = {
                "familiarity": "你们还没熟到这个程度，对方会觉得有点突然。",
                "comfort": "对方和你单独相处还不太自在。",
                "trust": "对方还没有那么信任你。",
                "romantic_interest": "对方还没有把你放进'那种可能'里考虑。",
                "attraction": "对方对你目前没有那个方向的感觉。",
            }.get(worst, "现在还太早了。")
            return decline("too_soon", text, alternative="从更轻的邀约开始")

        # 5) 未解决的冲突
        if v.conflict >= 45:
            return decline("unresolved_conflict", "你们之间还有没说开的事，对方现在不太想。", alternative="先把话说清楚")

        # 6) 推进太快
        recent_dates = int(rel.explicit_events.get("date_count", 0))
        if romantic and rel.last_interaction_day == self.state.world.get("day_index") and recent_dates == 0:
            if v.romantic_interest < 55:
                return decline("too_fast", "对方觉得节奏有点快了，会想先缓一缓。", alternative="先多相处几次")
        if rel.explicit_events.get("last_rejection_day") is not None and romantic:
            days_since = int(self.state.world.get("day_index", 0)) - int(rel.explicit_events["last_rejection_day"])
            if days_since < 14:
                return decline("recently_rejected", "距离上次那件事还太近，对方需要时间。", alternative="给彼此一点空间")

        # 7) 接受
        warmth = "warm" if v.comfort >= 60 else "neutral"
        if romantic and v.romantic_interest >= 60:
            warmth = "eager"
        reasons.append(f"stage={rel.stage}")
        return {
            "accepted": True,
            "npc_id": npc_id,
            "invite_type": invite_type,
            "reason_code": "accepted",
            "reason": "对方愿意——但这是因为你们之前累积的关系，不是因为你说得多好听。",
            "tone": warmth,
            "stage": rel.stage,
            "detail": reasons,
            "autonomy_note": "这是 NPC 自己的决定，与玩家的检定结果无关。",
        }

    def npc_decide_confession(
        self, npc_id: str, *, actor_id: str = "player", context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """NPC 对告白的回应：接受 / 犹豫（暂缓）/ 拒绝。**绝不由骰子决定。**"""
        context = dict(context or {})
        rel = self.get(npc_id, actor_id)
        assert rel is not None
        definition = self._definition(npc_id)
        temperament = npc_temperament(definition)
        v = rel.values
        day = int(self.state.world.get("day_index", 0))
        romance_cfg = self.state.config.get("romance", {}) or {}

        base = {
            "npc_id": npc_id,
            "stage": rel.stage,
            "autonomy_note": "告白的结果由 NPC 决定，任何检定结果都不能改变它。",
        }

        if temperament["is_teacher"]:
            return {**base, "decision": "decline", "reason_code": "boundary_teacher",
                    "reason": "对方是教职员，会认真但明确地拒绝。"}
        if temperament["has_partner"]:
            return {**base, "decision": "decline", "reason_code": "has_partner",
                    "reason": "对方已经有交往对象，会直接说明。"}
        if not temperament["romance_available"]:
            return {**base, "decision": "decline", "reason_code": "not_available",
                    "reason": "对方现在不考虑恋爱关系。这个立场是真实的，请不要写成'再努力一下就行'。"}

        days_known = day - int(rel.first_met_day or day)
        min_days = int(romance_cfg.get("min_days_before_confession_accept", 14))
        min_shared = int(romance_cfg.get("min_shared_experiences_for_dating", 5))
        shared = len(rel.shared_experiences)

        if v.conflict >= 45:
            return {**base, "decision": "decline", "reason_code": "conflict",
                    "reason": "你们之间还有没解决的问题，对方无法在这个状态下回答。"}

        strong = (
            v.romantic_interest >= 60 and v.attraction >= 50 and v.trust >= 55
            and v.comfort >= 55 and v.familiarity >= 55
        )
        if strong and days_known >= min_days and shared >= min_shared:
            return {**base, "decision": "accept", "reason_code": "mutual",
                    "reason": "对方的感情已经积累到可以回应的程度——这是长期相处的结果。",
                    "guidance": "不要写成理所当然。让对方也有紧张、停顿和自己的措辞。"}

        if v.romantic_interest >= 40 or (strong and (days_known < min_days or shared < min_shared)):
            missing = []
            if days_known < min_days:
                missing.append(f"认识时间还短（{days_known} 天）")
            if shared < min_shared:
                missing.append(f"共同经历还不够（{shared}/{min_shared}）")
            if v.trust < 55:
                missing.append("信任还没到那个程度")
            return {**base, "decision": "defer", "reason_code": "needs_time",
                    "reason": "对方没有立刻拒绝，但需要时间。" + ("；".join(missing) if missing else ""),
                    "guidance": "写成真实的犹豫：她/他可能会说'我现在没办法马上回答'。这不是暗示成功率。"}

        return {**base, "decision": "decline", "reason_code": "no_romantic_feeling",
                "reason": "对方没有把你放在这个位置上。可以珍惜这段关系，但答案是不。",
                "guidance": "拒绝要具体、要尊重、不要留下'再试一次就行'的暗示。关系不会归零，但会改变。"}

    # ------------------------------------------------------------------
    # 衰减
    # ------------------------------------------------------------------
    def apply_natural_decay(self, *, current_day: int) -> list[dict[str, Any]]:
        """长期不互动会自然疏远（每 7 天一次，有下限）。"""
        decay = float(self.state.cfg("relationship.natural_decay_per_week", 1))
        floor = float(self.state.cfg("relationship.decay_floor_familiarity", 10))
        changed: list[dict[str, Any]] = []
        for key, raw in list(self.state.relationships.items()):
            rel = Relationship.from_dict(raw)
            last = rel.last_interaction_day
            if last is None or current_day - last < 7:
                continue
            weeks = (current_day - last) // 7
            if rel.explicit_events.get("decayed_weeks") == weeks:
                continue
            deltas = {
                "familiarity": -decay if rel.values.familiarity > floor else 0,
                "closeness": -decay,
                "comfort": -decay * 0.5,
            }
            if not rel.explicit_events.get("relationship_confirmed"):
                deltas["romantic_interest"] = -decay * 0.5
            actual = rel.values.apply(deltas)
            rel.explicit_events["decayed_weeks"] = weeks
            self.save(rel)
            if actual:
                changed.append({"key": key, "changes": actual})
        return changed
