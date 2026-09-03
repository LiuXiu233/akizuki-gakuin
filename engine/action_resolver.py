"""行动解析与结算。

一次行动的完整流水线：
    可行性检查 → 是否需要检定 → 真实骰点 → 时间/精力/压力/金钱结算
    → XP → 关系变化 → 返回结构化结果

**LLM 只能基于返回的真实结果来叙述。**
"""

from __future__ import annotations

import logging
from typing import Any

from .checks import needs_check, perform_check
from .models import (
    ATTRIBUTES,
    CheckResult,
    Difficulty,
    GameState,
    ValidationError,
    clamp_int,
    sanitize_text,
)
from .npc_manager import NPCManager
from .progression import ProgressionManager
from .registry_manager import RegistryManager
from .relationship_manager import RelationshipManager
from .rng import GameRNG
from .state_manager import MemoryStore
from .time_manager import TimeManager

log = logging.getLogger("engine.action")

#: 行动模板。Agent 可以传任意 action_type；未登记的会走 "custom" 默认值。
ACTION_TYPES: dict[str, dict[str, Any]] = {
    # --- 社交 ---
    "greet":        {"category": "social", "attribute": "charm", "skill": None, "minutes": 2, "energy": 0.5, "difficulty": "very_easy", "relationship_event": "greeting", "roll": False},
    "small_talk":   {"category": "social", "attribute": "charm", "skill": "conversation", "minutes": 8, "energy": 1.5, "difficulty": "very_easy", "relationship_event": "small_talk"},
    "talk":         {"category": "social", "attribute": "charm", "skill": "conversation", "minutes": 20, "energy": 3, "difficulty": "easy", "relationship_event": "conversation"},
    "deep_talk":    {"category": "social", "attribute": "perception", "skill": "empathy", "minutes": 45, "energy": 8, "difficulty": "normal", "relationship_event": "deep_talk"},
    "persuade":     {"category": "social", "attribute": "charm", "skill": "persuasion", "minutes": 15, "energy": 5, "difficulty": "normal", "relationship_event": "conversation"},
    "invite":       {"category": "social", "attribute": "charm", "skill": "conversation", "minutes": 5, "energy": 2, "difficulty": "normal", "relationship_event": None},
    "comfort":      {"category": "social", "attribute": "perception", "skill": "empathy", "minutes": 30, "energy": 8, "difficulty": "hard", "relationship_event": "comforted_them"},
    "apologize":    {"category": "social", "attribute": "willpower", "skill": "conversation", "minutes": 10, "energy": 4, "difficulty": "normal", "relationship_event": "apology"},
    "compliment":   {"category": "social", "attribute": "charm", "skill": "conversation", "minutes": 3, "energy": 1, "difficulty": "easy", "relationship_event": "compliment"},
    "joke":         {"category": "social", "attribute": "charm", "skill": "conversation", "minutes": 3, "energy": 1, "difficulty": "easy", "relationship_event": "teasing"},
    "help_someone": {"category": "social", "attribute": "perception", "skill": None, "minutes": 25, "energy": 8, "difficulty": "normal", "relationship_event": "helped_them"},
    "ask_contact":  {"category": "social", "attribute": "charm", "skill": "conversation", "minutes": 5, "energy": 2, "difficulty": "normal", "relationship_event": "exchanged_contact"},
    "share_topic":  {"category": "social", "attribute": "charm", "skill": "conversation", "minutes": 15, "energy": 3, "difficulty": "easy", "relationship_event": "shared_interest"},
    "read_the_room": {"category": "social", "attribute": "perception", "skill": "empathy", "minutes": 2, "energy": 1, "difficulty": "normal", "relationship_event": None},
    # --- 恋爱（**结果永远由 NPC 决定，不由骰子决定**）---
    "flirt":        {"category": "romance", "attribute": "charm", "skill": "conversation", "minutes": 10, "energy": 3, "difficulty": "normal", "relationship_event": "flirt"},
    "date":         {"category": "romance", "attribute": "charm", "skill": "conversation", "minutes": 120, "energy": 18, "difficulty": "normal", "relationship_event": "date"},
    "confess":      {"category": "romance", "attribute": "willpower", "skill": "conversation", "minutes": 15, "energy": 10, "difficulty": "hard", "relationship_event": None},
    "walk_home":    {"category": "romance", "attribute": "charm", "skill": "conversation", "minutes": 30, "energy": 5, "difficulty": "easy", "relationship_event": "walk_home"},
    # --- 学业 ---
    "study":        {"category": "study", "attribute": "intellect", "skill": "study", "minutes": 60, "energy": 12, "difficulty": "normal", "stress": 3, "xp_skill": "study"},
    "attend_class": {"category": "study", "attribute": "intellect", "skill": "study", "minutes": 50, "energy": 6, "difficulty": "easy", "roll": False, "stress": 2},
    "exam":         {"category": "study", "attribute": "intellect", "skill": "exam_strategy", "minutes": 60, "energy": 20, "difficulty": "hard", "stress": 12},
    "take_notes":   {"category": "study", "attribute": "intellect", "skill": "note_taking", "minutes": 30, "energy": 5, "difficulty": "easy"},
    "research":     {"category": "study", "attribute": "intellect", "skill": "study", "minutes": 45, "energy": 8, "difficulty": "normal"},
    # --- 兴趣 / 社团 ---
    "practice":     {"category": "hobby", "attribute": "agility", "skill": None, "minutes": 60, "energy": 12, "difficulty": "normal", "stress": -2},
    "club_activity": {"category": "club", "attribute": "willpower", "skill": None, "minutes": 90, "energy": 14, "difficulty": "normal", "stress": -3, "relationship_event": "club_activity"},
    "perform":      {"category": "hobby", "attribute": "charm", "skill": "performance", "minutes": 30, "energy": 12, "difficulty": "hard"},
    "photo":        {"category": "hobby", "attribute": "perception", "skill": "photography", "minutes": 30, "energy": 5, "difficulty": "normal"},
    "cook":         {"category": "hobby", "attribute": "agility", "skill": "cooking", "minutes": 45, "energy": 8, "difficulty": "normal"},
    "draw":         {"category": "hobby", "attribute": "agility", "skill": "drawing", "minutes": 60, "energy": 8, "difficulty": "normal", "stress": -3},
    "write":        {"category": "hobby", "attribute": "intellect", "skill": "writing", "minutes": 60, "energy": 8, "difficulty": "normal", "stress": -2},
    "play_game":    {"category": "hobby", "attribute": "agility", "skill": "gaming", "minutes": 60, "energy": 6, "difficulty": "normal", "stress": -5},
    "sing":         {"category": "hobby", "attribute": "charm", "skill": "singing", "minutes": 30, "energy": 6, "difficulty": "normal", "stress": -4},
    # --- 身体 ---
    "exercise":     {"category": "hobby", "attribute": "physique", "skill": "athletics", "minutes": 60, "energy": 20, "difficulty": "normal", "stress": -5},
    "run":          {"category": "hobby", "attribute": "physique", "skill": "running", "minutes": 40, "energy": 16, "difficulty": "normal", "stress": -5},
    "swim":         {"category": "hobby", "attribute": "physique", "skill": "swimming", "minutes": 45, "energy": 18, "difficulty": "normal", "stress": -5},
    # --- 生活 ---
    "move":         {"category": "explore", "attribute": "agility", "skill": None, "minutes": 0, "energy": 0, "roll": False},
    "explore":      {"category": "explore", "attribute": "perception", "skill": None, "minutes": 30, "energy": 5, "difficulty": "easy"},
    "observe":      {"category": "explore", "attribute": "perception", "skill": None, "minutes": 5, "energy": 1, "difficulty": "normal"},
    "buy":          {"category": "life", "attribute": "charm", "skill": None, "minutes": 8, "energy": 1, "roll": False},
    "eat":          {"category": "life", "attribute": "physique", "skill": None, "minutes": 30, "energy": -5, "roll": False, "stress": -2},
    "rest":         {"category": "rest", "attribute": "willpower", "skill": None, "minutes": 30, "energy": -8, "roll": False, "stress": -5},
    "nap":          {"category": "rest", "attribute": "willpower", "skill": None, "minutes": 40, "energy": -15, "roll": False, "stress": -6},
    "wait":         {"category": "rest", "attribute": "willpower", "skill": None, "minutes": 15, "energy": 1, "roll": False},
    "message":      {"category": "social", "attribute": "charm", "skill": "conversation", "minutes": 10, "energy": 1, "difficulty": "very_easy", "relationship_event": "messaged"},
    "custom":       {"category": "other", "attribute": "willpower", "skill": None, "minutes": 15, "energy": 3, "difficulty": "normal"},
}

CATEGORY_ZH = {
    "social": "社交", "romance": "恋爱", "study": "学习", "hobby": "兴趣",
    "club": "社团", "explore": "探索", "life": "生活", "rest": "休息", "other": "其它",
}


class ActionResolver:
    def __init__(
        self,
        state: GameState,
        rng: GameRNG,
        time_manager: TimeManager,
        npcs: NPCManager,
        relationships: RelationshipManager,
        progression: ProgressionManager,
        registry: RegistryManager,
        memories: MemoryStore,
    ) -> None:
        self.state = state
        self.rng = rng
        self.time = time_manager
        self.npcs = npcs
        self.relationships = relationships
        self.progression = progression
        self.registry = registry
        self.memories = memories

    # ------------------------------------------------------------------
    def template(self, action_type: str) -> dict[str, Any]:
        return dict(ACTION_TYPES.get(str(action_type), ACTION_TYPES["custom"]))

    def feasibility(self, action_type: str, *, target: str | None = None, location: str | None = None) -> dict[str, Any]:
        """行动是否可能？（地点是否开门、对方是否在场、精力够不够……）"""
        problems: list[str] = []
        player = self.state.player
        location = location or player.get("location")
        if location and not self.registry.exists("location", location):
            problems.append(f"地点不存在: {location}")
        elif location and not self.time.is_open(location):
            problems.append(f"{(self.registry.get('location', location) or {}).get('name', location)} 现在没有开门")
        if target and target != "player":
            if not self.npcs.exists(target):
                problems.append(f"角色不存在: {target}")
            elif self.npcs.where_is(target) != location:
                problems.append("对方不在这里")
        status = player.get("status") or {}
        template = self.template(action_type)
        if int(status.get("energy", 100)) <= 0 and float(template.get("energy", 0)) > 0:
            problems.append("已经完全没有精力了，需要休息")
        if self.time.needs_forced_sleep():
            problems.append("太晚了，必须先睡觉")
        return {"possible": not problems, "problems": problems}

    # ------------------------------------------------------------------
    def perform(
        self,
        *,
        actor_id: str = "player",
        action_type: str = "custom",
        target: str | None = None,
        skill: str | None = None,
        knowledge: str | None = None,
        attribute: str | None = None,
        difficulty: str | Difficulty | None = None,
        situational_modifiers: Any = None,
        minutes: int | None = None,
        energy_cost: float | None = None,
        stress_delta: float | None = None,
        money_cost: int = 0,
        force_check: bool | None = None,
        relationship_event: str | None = None,
        intensity: float = 1.0,
        xp_source: str = "use",
        context: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """执行一次行动并完成全部结算。"""
        context = dict(context or {})
        template = self.template(action_type)
        attribute = attribute or template.get("attribute") or "willpower"
        if attribute not in ATTRIBUTES:
            raise ValidationError(f"未知属性: {attribute}")
        skill = skill if skill is not None else template.get("skill")
        if skill and not self.registry.exists("skill", skill):
            raise ValidationError(f"技能未注册: {skill}。请先 register_skill() 或改用已有技能。")
        if knowledge and not self.registry.exists("knowledge", knowledge):
            raise ValidationError(f"知识未注册: {knowledge}。请先 register_knowledge()。")
        difficulty = difficulty or template.get("difficulty") or "normal"

        feasibility = self.feasibility(action_type, target=target)
        result: dict[str, Any] = {
            "actor": actor_id,
            "action_type": action_type,
            "category": template.get("category", "other"),
            "category_zh": CATEGORY_ZH.get(template.get("category", "other"), "其它"),
            "target": target,
            "feasible": feasibility["possible"],
            "problems": feasibility["problems"],
            "check": None,
            "time": None,
            "costs": {},
            "xp": [],
            "relationship": None,
            "npc_decision": None,
            "notes": [],
        }
        if not feasibility["possible"]:
            result["notes"].append("行动无法进行——请把原因写成剧情，而不是报错。")
            return result

        # --- 检定 ---
        skill_level = self.npcs.skill_level(actor_id, skill)
        knowledge_level = self.npcs.knowledge_level(actor_id, knowledge)
        attribute_value = self.npcs.attribute(actor_id, attribute)

        should_roll, roll_reason = needs_check(
            action_type,
            difficulty=difficulty,
            skill_level=skill_level,
            attribute_value=attribute_value,
            forced=force_check if force_check is not None else (None if template.get("roll", True) else False),
        )
        check_result: str | None = None
        if should_roll:
            outcome = perform_check(
                self.rng,
                attribute_value=attribute_value,
                skill_level=skill_level,
                knowledge_level=knowledge_level,
                difficulty=difficulty,
                situational_modifiers=self._auto_situational(situational_modifiers),
                actor_id=actor_id,
                action_type=action_type,
                attribute=attribute,
                skill=skill,
                knowledge=knowledge,
            )
            result["check"] = outcome.to_dict()
            check_result = outcome.result
        else:
            result["notes"].append(f"无需判定：{roll_reason}")

        # --- 金钱 ---
        if money_cost:
            status = self.state.player.setdefault("status", {})
            if int(status.get("money", 0)) < int(money_cost):
                result["feasible"] = False
                result["problems"].append("钱不够")
                return result
            status["money"] = int(status.get("money", 0)) - int(money_cost)
            result["costs"]["money"] = int(money_cost)

        # --- 时间 / 精力 / 压力 ---
        spent = int(minutes if minutes is not None else template.get("minutes", 15))
        energy = float(energy_cost if energy_cost is not None else template.get("energy", 3))
        stress = float(stress_delta if stress_delta is not None else template.get("stress", 0))
        time_report = self.time.advance(
            spent, reason=action_type, energy_cost=energy, stress_delta=stress
        )
        result["time"] = time_report
        result["costs"].update({"minutes": spent, "energy": energy, "stress": stress})

        # --- XP ---
        if skill:
            award = self.progression.award(
                actor_id, kind="skill", entry_id=skill, source=xp_source,
                result=check_result, difficulty=difficulty,
            )
            if award["gained"] or award.get("blocked"):
                result["xp"].append(award)
        if knowledge:
            award = self.progression.award(
                actor_id, kind="knowledge", entry_id=knowledge, source=xp_source,
                result=check_result, difficulty=difficulty,
            )
            if award["gained"] or award.get("blocked"):
                result["xp"].append(award)

        # --- 关系 ---
        rel_event = relationship_event if relationship_event is not None else template.get("relationship_event")
        if rel_event and target and target != actor_id:
            rel_context = dict(context)
            rel_context.setdefault("check_result", check_result)
            applied = self.relationships.apply_event(
                actor_id, target, rel_event, intensity=intensity, context=rel_context, note=note
            )
            mirror = self.relationships.apply_event(
                target, actor_id, rel_event, intensity=intensity * 0.9, context=rel_context, note=note
            )
            result["relationship"] = {"player_to_target": applied, "target_to_player": mirror}
            runtime = self.npcs.runtime(target)
            runtime["interaction_count"] = int(runtime.get("interaction_count", 0)) + 1

        # --- 记录 ---
        self.state.world.setdefault("recent_actions", []).append(
            {
                "turn": self.state.world.get("turn"),
                "action": action_type,
                "target": target,
                "result": check_result,
                "time": self.state.world.get("time"),
            }
        )
        self.state.world["recent_actions"] = self.state.world["recent_actions"][-20:]

        if action_type in ("eat",):
            self.state.player["last_meal_minutes"] = self.time.day_index * 1440 + self.time.minutes

        result["notes"].append(
            "所有数值已由引擎结算。请只根据本结果叙述，不要自行增删任何数值。"
        )
        return result

    # ------------------------------------------------------------------
    def _auto_situational(self, extra: Any) -> list[dict[str, Any]]:
        """自动加入由**状态**决定的情境修正（疲惫、压力、心情）。"""
        modifiers: list[dict[str, Any]] = []
        status = self.state.player.get("status") or {}
        energy = int(status.get("energy", 100))
        stress = int(status.get("stress", 0))
        mood = str(status.get("mood", "normal"))
        if energy <= 10:
            modifiers.append({"name": "精疲力竭", "value": -4})
        elif energy <= 25:
            modifiers.append({"name": "疲惫", "value": -2})
        if stress >= 85:
            modifiers.append({"name": "压力过载", "value": -3})
        elif stress >= 60:
            modifiers.append({"name": "压力较大", "value": -2})
        mood_table = (self.state.static.get("attributes") or {}).get("state_effects", {}).get("mood", {})
        mood_mod = int((mood_table.get(mood) or {}).get("modifier", 0))
        if mood_mod:
            modifiers.append({"name": f"心情:{mood}", "value": mood_mod})
        if extra is None:
            return modifiers
        if isinstance(extra, dict):
            modifiers.extend({"name": str(k), "value": float(v)} for k, v in extra.items())
        elif isinstance(extra, (int, float)):
            modifiers.append({"name": "situational", "value": float(extra)})
        elif isinstance(extra, list):
            for item in extra:
                if isinstance(item, dict):
                    modifiers.append({"name": str(item.get("name", "situational")), "value": float(item.get("value", 0))})
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    modifiers.append({"name": str(item[0]), "value": float(item[1])})
        return modifiers

    # ------------------------------------------------------------------
    def move(self, location_id: str, *, actor_id: str = "player") -> dict[str, Any]:
        """移动到某个地点，按 zone 计算真实通勤时间。"""
        if not self.registry.exists("location", location_id):
            raise ValidationError(f"地点不存在: {location_id}。请先 register_location()。")
        current = self.state.player.get("location") if actor_id == "player" else self.npcs.where_is(actor_id)
        minutes = self.time.travel_minutes(current or location_id, location_id)
        report = self.time.advance(minutes, reason=f"移动到 {location_id}", energy_cost=minutes * 0.05)
        self.npcs.move_character(actor_id, location_id, duration_minutes=120)
        location = self.registry.get("location", location_id) or {}
        return {
            "from": current,
            "to": location_id,
            "name": location.get("name", location_id),
            "minutes": minutes,
            "open": self.time.is_open(location_id),
            "time": report,
            "description": location.get("description", ""),
        }

    def buy(self, item_id: str, *, location: str | None = None, quantity: int = 1) -> dict[str, Any]:
        """购买（无需检定：店开着、有钱、没有异常情况）。"""
        location = location or self.state.player.get("location")
        loc = self.registry.get("location", location) or {}
        items = ((loc.get("shop") or {}).get("items")) or []
        item = next((i for i in items if i.get("id") == item_id), None)
        if item is None:
            raise ValidationError(f"这里没有卖 {item_id}。可买: {', '.join(i['id'] for i in items) or '（无）'}")
        if not self.time.is_open(location):
            raise ValidationError("店没有开门")
        quantity = max(1, int(quantity))
        total = int(item["price"]) * quantity
        status = self.state.player.setdefault("status", {})
        if int(status.get("money", 0)) < total:
            return {"bought": False, "reason": "钱不够", "price": total, "money": status.get("money", 0)}
        status["money"] = int(status["money"]) - total
        self.time.advance(5 * quantity, reason="购买", energy_cost=0.5)
        inventory = self.state.player.setdefault("inventory", {})
        inventory[item_id] = int(inventory.get(item_id, 0)) + quantity
        if "food" in (loc.get("tags") or []) or item_id.startswith("item_set") or item_id in ("item_bento", "item_onigiri", "item_udon"):
            self.state.player["last_meal_minutes"] = self.time.day_index * 1440 + self.time.minutes
        return {
            "bought": True,
            "item": item["name"],
            "quantity": quantity,
            "price": total,
            "money_left": status["money"],
        }
