"""**LLM Agent 唯一的世界接口。**

规则：LLM 负责内容，Python 负责规则，存档负责历史。
Agent 不能直接读写 JSON、不能自己骰骰子、不能自己改数值——
只能调用本模块的工具函数。

用法（Python）::

    from engine.tools import get_turn_panel, perform_action
    print(get_turn_panel()["text"])

用法（任意 Agent / CLI）::

    python -m engine.tools list
    python -m engine.tools schema
    python -m engine.tools call get_turn_panel '{}'
    python -m engine.tools call perform_action '{"action_type":"talk","target":"npc_amano_rin"}'

所有工具都返回 JSON 可序列化的字典。出错时返回
``{"ok": false, "error": "...", "hint": "..."}``，不会抛异常到 Agent 层。
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from .action_resolver import ACTION_TYPES, ActionResolver
from .checks import needs_check
from .event_manager import EventManager
from .models import (
    ATTRIBUTE_NAMES_ZH,
    ATTRIBUTES,
    CheckResult,
    Difficulty,
    GameError,
    GameState,
    KNOWLEDGE_LEVEL_NAMES,
    SKILL_LEVEL_NAMES,
    ValidationError,
)
from .npc_manager import NPCManager
from .progression import ProgressionManager, xp_for_next_level
from .registry_manager import RegistryManager
from .relationship_manager import RELATIONSHIP_EVENTS, RelationshipManager
from .rng import GameRNG, make_rng
from .state_manager import MemoryStore, StateManager, setup_logging
from .time_manager import WEATHER_ZH, TimeManager
from .world_simulator import WorldSimulator

log = logging.getLogger("engine.tools")


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


class GameSession:
    """把所有管理器绑在一个 :class:`GameState` 上。"""

    def __init__(self, root: str | Path | None = None, *, seed: int | None = None, autoload: bool = True) -> None:
        self.manager = StateManager(root)
        setup_logging(self.manager.root, self.manager.config)
        self.state: GameState = self.manager.load() if autoload else self.manager.new_game(seed=seed)
        self.rng = make_rng(self.state.config, seed)
        stored = self.state.world.get("rng")
        if stored and seed is None:
            try:
                self.rng.restore_state(stored)
            except ValueError as exc:  # 损坏的 RNG 状态不应让游戏无法启动
                log.warning("RNG 状态无法恢复，使用新随机源: %s", exc)
        self._build()

    def _build(self) -> None:
        self.registry = RegistryManager(self.state)
        self.time = TimeManager(self.state, self.rng, self.registry)
        self.relationships = RelationshipManager(self.state, self.rng)
        self.npcs = NPCManager(self.state, self.rng, self.registry, self.time, self.relationships)
        self.progression = ProgressionManager(self.state)
        self.memories = MemoryStore(self.state)
        self.events = EventManager(self.state, self.rng, self.time, self.npcs, self.relationships, self.registry)
        self.world = WorldSimulator(self.state, self.rng, self.time, self.npcs, self.relationships, self.registry)
        self.actions = ActionResolver(
            self.state, self.rng, self.time, self.npcs, self.relationships,
            self.progression, self.registry, self.memories,
        )

    # ------------------------------------------------------------------
    def save(self) -> dict[str, str]:
        return self.manager.save(self.state, rng=self.rng)

    def reload_from(self, state: GameState) -> None:
        self.state = state
        stored = state.world.get("rng")
        if stored:
            try:
                self.rng.restore_state(stored)
            except ValueError:
                pass
        self._build()

    @property
    def debug_numbers(self) -> bool:
        return bool(self.state.cfg("visibility.debug_relationship_numbers", False))


_SESSION: GameSession | None = None


def get_session(*, root: str | Path | None = None) -> GameSession:
    """获取（必要时创建）全局会话。"""
    global _SESSION
    if _SESSION is None:
        _SESSION = GameSession(root)
    return _SESSION


def reset_session(session: GameSession | None = None) -> GameSession:
    """替换全局会话（测试与 new_game 使用）。"""
    global _SESSION
    _SESSION = session if session is not None else GameSession()
    return _SESSION


# ---------------------------------------------------------------------------
# 只读：世界与角色
# ---------------------------------------------------------------------------


def get_world_state() -> dict[str, Any]:
    """获取当前世界状态：时间、日期、天气、地点、今日日历事件、学期。"""
    s = get_session()
    now = s.time.now_dict()
    location_id = s.state.player.get("location")
    location = s.registry.get("location", location_id) or {}
    return {
        "ok": True,
        **now,
        "school": s.state.world.get("school_name"),
        "location": {
            "id": location_id,
            "name": location.get("name"),
            "description": location.get("description"),
            "tags": location.get("tags", []),
            "open": s.time.is_open(location_id) if location_id else False,
        },
        "calendar_events": s.time.calendar_events_today(),
        "class_subjects_today": s.time.today_class_subjects(s.state.player.get("class", "class_2a")),
        "is_class_time": s.time.is_class_time(),
        "is_club_time": s.time.is_club_time(),
        "must_sleep": s.time.needs_forced_sleep(),
        "events_today": s.state.world.get("events_today", 0),
    }


def get_player_state() -> dict[str, Any]:
    """获取玩家的完整状态（属性、状态值、技能、知识、社团、位置、物品）。"""
    s = get_session()
    player = s.state.player
    return {
        "ok": True,
        "id": "player",
        "name": player.get("name"),
        "age": player.get("age"),
        "gender": player.get("gender"),
        "class": player.get("class"),
        "attributes": player.get("attributes", {}),
        "status": player.get("status", {}),
        "conditions": player.get("conditions", []),
        "skills": player.get("skills", {}),
        "knowledge": player.get("knowledge", {}),
        "skill_xp": player.get("skill_xp", {}),
        "knowledge_xp": player.get("knowledge_xp", {}),
        "clubs": player.get("clubs", []),
        "location": player.get("location"),
        "inventory": player.get("inventory", {}),
        "flags": player.get("flags", {}),
    }


def get_character_state(character_id: str, include_hidden: bool = False) -> dict[str, Any]:
    """获取某个角色的状态。

    ``include_hidden=False`` 时不返回 secrets / hidden_personality。
    即使取到隐藏内容，也**绝不能**让该角色说出自己不知道的事。
    """
    s = get_session()
    try:
        data = s.npcs.character_state(character_id, include_hidden=bool(include_hidden))
    except ValidationError as exc:
        return _error(str(exc), hint="先用 get_registry('npc') 查看已有角色，或 create_npc() 创建。")
    data["ok"] = True
    if character_id != "player":
        data["relationship_with_player"] = s.relationships.describe("player", character_id)
        data["schedule_now"] = s.npcs.get_schedule(character_id)
    return data


def get_nearby_characters(location_id: str | None = None) -> dict[str, Any]:
    """当前（或指定）地点有哪些角色在场。"""
    s = get_session()
    location_id = location_id or s.state.player.get("location")
    return {
        "ok": True,
        "location": location_id,
        "characters": s.npcs.nearby(location_id),
        "note": "NPC 有自己的日程，玩家想找的人不一定在。",
    }


def get_relationship(actor_id: str, target_id: str, debug: bool | None = None) -> dict[str, Any]:
    """获取关系描述。**默认不返回任何数值**——玩家只能从行为判断。"""
    s = get_session()
    if not (actor_id == "player" or s.npcs.exists(actor_id)):
        return _error(f"未知角色: {actor_id}")
    if not (target_id == "player" or s.npcs.exists(target_id)):
        return _error(f"未知角色: {target_id}")
    data = s.relationships.describe(actor_id, target_id, debug=debug)
    data["ok"] = True
    data["actor"] = actor_id
    if not (debug if debug is not None else s.debug_numbers):
        data["reminder"] = "禁止向玩家透露 attraction / romantic_interest / trust 的数值或成功率。"
    return data


def get_relevant_memories(
    character_id: str,
    context: str = "",
    participants: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """取出某角色与当前情境最相关的记忆（事实 / 解释 / 情绪 三层分离）。"""
    s = get_session()
    memories = s.memories.relevant(
        character_id, context=context, participants=participants or [], tags=tags or [], limit=int(limit)
    )
    return {
        "ok": True,
        "character_id": character_id,
        "memories": memories,
        "count": len(memories),
        "note": "fact 是发生过的事；interpretation 是这个角色的主观理解；emotion 是当时的情绪。不要混为一谈。",
    }


# ---------------------------------------------------------------------------
# 判定与时间
# ---------------------------------------------------------------------------


def resolve_check(
    actor_id: str = "player",
    action_type: str = "custom",
    attribute: str = "willpower",
    skill: str | None = None,
    knowledge: str | None = None,
    difficulty: str = "normal",
    situational_modifiers: Any = None,
) -> dict[str, Any]:
    """执行一次 D20 判定。**这是唯一合法的骰点方式。**

    返回真实结果；Agent 只能据此叙述，不得重骰或修改。
    社交类检定只决定玩家表达得如何，绝不决定 NPC 的选择。
    """
    s = get_session()
    try:
        if attribute not in ATTRIBUTES:
            raise ValidationError(f"未知属性 {attribute!r}，可用: {', '.join(ATTRIBUTES)}")
        if skill and not s.registry.exists("skill", skill):
            raise ValidationError(f"技能未注册: {skill}")
        if knowledge and not s.registry.exists("knowledge", knowledge):
            raise ValidationError(f"知识未注册: {knowledge}")
        from .checks import perform_check

        outcome = perform_check(
            s.rng,
            attribute_value=s.npcs.attribute(actor_id, attribute),
            skill_level=s.npcs.skill_level(actor_id, skill),
            knowledge_level=s.npcs.knowledge_level(actor_id, knowledge),
            difficulty=difficulty,
            situational_modifiers=s.actions._auto_situational(situational_modifiers),
            actor_id=actor_id,
            action_type=action_type,
            attribute=attribute,
            skill=skill,
            knowledge=knowledge,
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    data = outcome.to_dict()
    data["ok"] = True
    data["result_label"] = CheckResult(outcome.result).label
    data["text"] = _check_text(data)
    return data


def should_check(action_type: str, difficulty: str = "normal", skill: str | None = None, actor_id: str = "player") -> dict[str, Any]:
    """判断一个行动是否需要检定（结果毫无悬念的事不要骰）。"""
    s = get_session()
    needed, reason = needs_check(
        action_type,
        difficulty=difficulty,
        skill_level=s.npcs.skill_level(actor_id, skill),
        attribute_value=5,
    )
    return {"ok": True, "needs_check": needed, "reason": reason}


def advance_time(minutes: int, reason: str = "", sleeping: bool = False) -> dict[str, Any]:
    """推进时间并结算精力 / 压力 / 跨日。时间只能由代码推进。"""
    s = get_session()
    try:
        report = s.time.advance(int(minutes), reason=reason, sleeping=bool(sleeping))
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    report["ok"] = True
    return report


def sleep(hours: float = 7.0, until: str | None = None) -> dict[str, Any]:
    """睡觉。``until="07:00"`` 可以直接睡到指定时刻。"""
    s = get_session()
    try:
        report = s.time.sleep_until(until) if until else s.time.sleep(float(hours))
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    report["ok"] = True
    return report


def move_character(character_id: str, location_id: str, duration_minutes: int = 60, activity: str = "") -> dict[str, Any]:
    """移动角色。玩家移动会消耗真实通勤时间。"""
    s = get_session()
    try:
        if character_id == "player":
            result = s.actions.move(location_id)
        else:
            result = s.npcs.move_character(character_id, location_id, duration_minutes=int(duration_minutes), activity=activity)
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    result["ok"] = True
    return result


def get_schedule(character_id: str, weekday: str | None = None, time_hhmm: str | None = None) -> dict[str, Any]:
    """查询某角色在某时刻的**计划**日程（计划不等于实际）。"""
    s = get_session()
    if not s.npcs.exists(character_id):
        return _error(f"未知角色: {character_id}")
    minutes = None
    if time_hhmm:
        from .time_manager import parse_hhmm

        minutes = parse_hhmm(time_hhmm)
    schedule = s.npcs.get_schedule(character_id, weekday=weekday, minutes=minutes)
    schedule["ok"] = True
    schedule["actual_location_now"] = s.npcs.where_is(character_id)
    return schedule


# ---------------------------------------------------------------------------
# 行动
# ---------------------------------------------------------------------------


def perform_action(
    action_type: str,
    actor_id: str = "player",
    target: str | None = None,
    skill: str | None = None,
    knowledge: str | None = None,
    attribute: str | None = None,
    difficulty: str | None = None,
    situational_modifiers: Any = None,
    minutes: int | None = None,
    energy_cost: float | None = None,
    stress_delta: float | None = None,
    money_cost: int = 0,
    force_check: bool | None = None,
    relationship_event: str | None = None,
    intensity: float = 1.0,
    context: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """执行一次行动并完成全部结算（检定 / 时间 / 精力 / 压力 / 金钱 / XP / 关系）。

    这是 Agent 最常用的工具。返回结果里的每一个数字都是引擎算出来的，
    Agent 只能据此叙述。
    """
    s = get_session()
    try:
        result = s.actions.perform(
            actor_id=actor_id, action_type=action_type, target=target, skill=skill,
            knowledge=knowledge, attribute=attribute, difficulty=difficulty,
            situational_modifiers=situational_modifiers, minutes=minutes,
            energy_cost=energy_cost, stress_delta=stress_delta, money_cost=int(money_cost),
            force_check=force_check, relationship_event=relationship_event,
            intensity=float(intensity), context=context, note=note,
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    result["ok"] = True
    if result.get("check"):
        result["check_text"] = _check_text(result["check"])
    if result.get("xp"):
        result["growth_text"] = _growth_text(result["xp"])
    if result.get("relationship") and not s.debug_numbers:
        for side in ("player_to_target", "target_to_player"):
            entry = result["relationship"].get(side)
            if entry:
                entry.pop("changes", None)
        result["relationship"]["note"] = (
            "关系变化已结算但对玩家隐藏。请用行为、语气、距离来表现，禁止写【好感+2】。"
        )
    return result


def buy_item(item_id: str, quantity: int = 1, location_id: str | None = None) -> dict[str, Any]:
    """购买物品。营业中 + 有钱 + 无异常 = 不需要检定。"""
    s = get_session()
    try:
        result = s.actions.buy(item_id, location=location_id, quantity=int(quantity))
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    result["ok"] = True
    return result


def apply_relationship_event(
    actor_id: str,
    target_id: str,
    event_type: str,
    intensity: float = 1.0,
    context: dict[str, Any] | None = None,
    bidirectional: bool = False,
    note: str = "",
) -> dict[str, Any]:
    """套用一次关系事件。日常互动的变化非常小；恋爱必须长期积累。

    可用 event_type 见返回的 ``available_events``（或 get_rules_digest）。
    """
    s = get_session()
    try:
        result = s.relationships.apply_event(
            actor_id, target_id, event_type, intensity=float(intensity),
            context=context, bidirectional=bool(bidirectional), note=note,
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc), hint=f"可用事件: {', '.join(sorted(RELATIONSHIP_EVENTS))}")
    result["ok"] = True
    if not s.debug_numbers:
        result.pop("changes", None)
        if "mirror" in result:
            result["mirror"].pop("changes", None)
        result["reminder"] = "不要把关系变化写成数值。用行为表现。"
    return result


def npc_decide_invitation(npc_id: str, invite_type: str = "casual", actor_id: str = "player", context: dict[str, Any] | None = None) -> dict[str, Any]:
    """NPC 是否接受邀请。**与检定结果无关**——这是 NPC 自己的决定。

    invite_type: casual / group_activity / meal / study / one_on_one /
    walk_home / date / trip / intimate
    """
    s = get_session()
    if not s.npcs.exists(npc_id):
        return _error(f"未知角色: {npc_id}")
    result = s.relationships.npc_decide_invitation(npc_id, invite_type, actor_id=actor_id, context=context)
    result["ok"] = True
    return result


def npc_decide_confession(npc_id: str, actor_id: str = "player", context: dict[str, Any] | None = None) -> dict[str, Any]:
    """NPC 对告白的回应：accept / defer / decline。**绝不由骰子决定。**"""
    s = get_session()
    if not s.npcs.exists(npc_id):
        return _error(f"未知角色: {npc_id}")
    result = s.relationships.npc_decide_confession(npc_id, actor_id=actor_id, context=context)
    result["ok"] = True
    return result


def add_memory(
    character_id: str,
    fact: str,
    interpretation: str = "",
    emotion: str = "",
    intensity: int = 3,
    visibility: str = "private_fact",
    participants: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """写入一条记忆。必须区分 事实 / 主观解释 / 情绪。

    visibility: global_fact / known_fact / rumor / private_fact / secret
    """
    s = get_session()
    try:
        memory = s.memories.add(
            character_id, fact=fact, interpretation=interpretation, emotion=emotion,
            intensity=int(intensity), visibility=visibility,
            participants=participants or [], tags=tags or [],
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    return {"ok": True, "memory": memory}


# ---------------------------------------------------------------------------
# 事件与世界模拟
# ---------------------------------------------------------------------------


def roll_random_event(force: bool = False) -> dict[str, Any]:
    """由代码决定是否发生随机事件，以及发生哪一个。

    返回 ``{"event": null}`` 表示这一回合什么都没发生——这非常正常。
    """
    s = get_session()
    event = s.events.roll_random_event(force=bool(force))
    return {"ok": True, "event": event, "candidates": len(s.events.candidates())}


def get_event_candidates() -> dict[str, Any]:
    """查看当前满足条件的事件候选（调试 / 导演参考用）。"""
    s = get_session()
    candidates = s.events.candidates()
    return {
        "ok": True,
        "count": len(candidates),
        "candidates": [
            {"id": c["id"], "name": c.get("name"), "category": c.get("category"),
             "weight": round(c["_weight"], 3), "npcs": c.get("_eligible_npcs", [])}
            for c in candidates
        ],
    }


def trigger_event(event_id: str, npc_id: str | None = None) -> dict[str, Any]:
    """强制触发某个事件（用于日历事件或剧情推进）。"""
    s = get_session()
    try:
        result = s.events.trigger(event_id, npc_id=npc_id)
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    result["ok"] = True
    return result


def simulate_background_world(minutes: int = 60) -> dict[str, Any]:
    """推进后台世界：NPC 日程、NPC↔NPC 互动、关系发展、恋爱与冲突。

    返回的内容对玩家是**隐藏的**，只能通过合理渠道（看到 / 听说 / 被告知）呈现。
    """
    s = get_session()
    result = s.world.simulate(minutes=int(minutes))
    result["ok"] = True
    result["discoverable"] = s.world.discoverable()
    result["note"] = "玩家不一定知道这些。要让 NPC 知道自己不该知道的事，是严重错误。"
    return result


def get_background_events(limit: int = 5) -> dict[str, Any]:
    """玩家**有可能**通过观察或传闻得知的后台变化。"""
    s = get_session()
    return {"ok": True, "events": s.world.discoverable(limit=int(limit))}


# ---------------------------------------------------------------------------
# 动态注册
# ---------------------------------------------------------------------------


def create_npc(
    name: str,
    age: int,
    gender: str = "unspecified",
    npc_id: str | None = None,
    reading: str = "",
    role: str = "student",
    tier: str = "background",
    class_id: str | None = None,
    club: str | None = None,
    archetype: str | None = None,
    appearance: str = "",
    personality: str = "",
    speech_style: str = "",
    interests: list[str] | None = None,
    attributes: dict[str, int] | None = None,
    skills: dict[str, int] | None = None,
    knowledge: dict[str, int] | None = None,
    home_location: str | None = None,
    favorite_place: str | None = None,
    schedule_overrides: list[dict[str, Any]] | None = None,
    social_links: list[dict[str, Any]] | None = None,
    romance_available: bool = True,
    existing_partner: str | None = None,
    relationship_attitude: str = "",
    romantic_preferences: str = "",
    created_reason: str = "",
    allow_isolated: bool = False,
) -> dict[str, Any]:
    """创建一个会持久存在的 NPC。

    强制校验：age >= 18、ID/姓名查重、地点与技能必须已注册、
    **必须至少与一个已有角色建立关系**（新人不能只认识玩家）。

    只有"玩家主动交流 / 会重复出现 / 参与事件 / 与已有 NPC 有关系 /
    对世界有持续影响"的角色才需要创建。路人不要注册。
    """
    s = get_session()
    try:
        result = s.npcs.create_npc(
            name=name, age=int(age), gender=gender, npc_id=npc_id, reading=reading, role=role, tier=tier,
            class_id=class_id, club=club, archetype=archetype, appearance=appearance,
            personality=personality, speech_style=speech_style, interests=interests,
            attributes=attributes, skills=skills, knowledge=knowledge,
            home_location=home_location, favorite_place=favorite_place,
            schedule_overrides=schedule_overrides, social_links=social_links,
            romance_available=bool(romance_available), existing_partner=existing_partner,
            relationship_attitude=relationship_attitude, romantic_preferences=romantic_preferences,
            created_reason=created_reason, allow_isolated=bool(allow_isolated),
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc), hint="先 get_registry('npc') 查重；确认 age >= 18；提供 social_links。")
    result["ok"] = True
    return result


def promote_npc(npc_id: str, tier: str | None = None, force: bool = False) -> dict[str, Any]:
    """提升 NPC 等级 background → supporting → core。"""
    s = get_session()
    if not s.npcs.exists(npc_id):
        return _error(f"未知角色: {npc_id}")
    result = s.npcs.promote_npc(npc_id, tier, force=bool(force))
    result["ok"] = True
    return result


def check_npc_promotions() -> dict[str, Any]:
    """检查所有 NPC 是否达到晋升条件并自动晋升（每回合调用一次）。"""
    s = get_session()
    return {"ok": True, "promotions": s.npcs.check_all_promotions()}


def register_skill(
    skill_id: str,
    name: str,
    category: str = "other",
    attribute: str = "agility",
    description: str = "",
    aliases: list[str] | None = None,
    specializations: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """注册新技能（会做的事）。

    **粒度铁律**：只有拥有独立成长路线、独立应用、足够使用频率的能力才建技能。
    煎鸡蛋 / 炒饭 / 咖喱 → 一律用 cooking。创建前引擎会自动查重。
    """
    s = get_session()
    try:
        entry = s.registry.register_skill(
            skill_id, name, category=category, attribute=attribute, description=description,
            aliases=aliases, specializations=specializations, reason=reason,
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc), hint="先 get_registry('skill') 检查是否已有同义技能。")
    return {"ok": True, "skill": entry}


def register_knowledge(
    knowledge_id: str,
    name: str,
    category: str = "other",
    description: str = "",
    aliases: list[str] | None = None,
    unlocks: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """注册新知识领域（知道的事）。知识与技能完全分离。"""
    s = get_session()
    try:
        entry = s.registry.register_knowledge(
            knowledge_id, name, category=category, description=description,
            aliases=aliases, unlocks=unlocks, reason=reason,
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc), hint="先 get_registry('knowledge') 查重。")
    return {"ok": True, "knowledge": entry}


def register_location(
    location_id: str,
    name: str,
    zone: str = "town_center",
    area: str = "town",
    open_hours: list[int] | None = None,
    tags: list[str] | None = None,
    description: str = "",
    actions: list[str] | None = None,
    shop_items: list[dict[str, Any]] | None = None,
    aliases: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """注册新地点。创建后永久进入玩家的世界。

    zone: school_indoor / school_outdoor / town_center / riverside / residential / far
    """
    s = get_session()
    try:
        entry = s.registry.register_location(
            location_id, name, zone=zone, area=area, open_hours=open_hours, tags=tags,
            description=description, actions=actions, shop_items=shop_items,
            aliases=aliases, reason=reason,
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc), hint="先 get_registry('location') 查重；zone 必须合法。")
    return {"ok": True, "location": entry}


def register_group(
    group_id: str,
    name: str,
    group_type: str = "informal",
    members: list[str] | None = None,
    location: str | None = None,
    purpose: str = "",
    leader: str | None = None,
    temporary: bool = False,
    aliases: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """注册新组织：临时乐队、学习小组、文化祭委员会、兴趣小组……"""
    s = get_session()
    try:
        entry = s.registry.register_group(
            group_id, name, group_type=group_type, members=members, location=location,
            purpose=purpose, leader=leader, temporary=bool(temporary), aliases=aliases, reason=reason,
        )
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    return {"ok": True, "group": entry}


def get_registry(kind: str | None = None, verbose: bool = False) -> dict[str, Any]:
    """查看注册表。**创建任何新内容之前必须先调用这个查重。**

    kind: skill / knowledge / location / group / npc（省略则返回全部）
    """
    s = get_session()
    try:
        summary = s.registry.summary(kind, verbose=bool(verbose))
    except ValidationError as exc:
        return _error(str(exc))
    return {"ok": True, "registry": summary}


def find_duplicate(kind: str, name: str = "", entry_id: str = "", aliases: list[str] | None = None) -> dict[str, Any]:
    """在创建前检查是否已经存在同义条目。"""
    s = get_session()
    try:
        dup = s.registry.find_duplicate(kind, entry_id=entry_id or None, name=name or None, aliases=aliases)
    except ValidationError as exc:
        return _error(str(exc))
    return {"ok": True, "duplicate": dup, "should_create": dup is None}


def add_dynamic_interest(npc_id: str, interest: str, evidence: str) -> dict[str, Any]:
    """让 NPC 形成一个新兴趣。**必须提供长期事件依据。**"""
    s = get_session()
    try:
        return {"ok": True, **s.npcs.add_dynamic_interest(npc_id, interest, evidence=evidence)}
    except (ValidationError, GameError) as exc:
        return _error(str(exc))


# ---------------------------------------------------------------------------
# 面板与推荐上下文
# ---------------------------------------------------------------------------


def _skill_name(session: GameSession, skill_id: str) -> str:
    entry = session.registry.get("skill", skill_id) or {}
    return entry.get("name", skill_id)


def _knowledge_name(session: GameSession, knowledge_id: str) -> str:
    entry = session.registry.get("knowledge", knowledge_id) or {}
    return entry.get("name", knowledge_id)


CONDITION_ZH = {
    "tired": "有点累", "exhausted": "精疲力竭", "stressed": "压力有点大",
    "overloaded": "压力过载", "hungry": "稍微有些饿", "sleepy": "困",
    "focused": "注意力集中", "inspired": "有灵感", "confident": "状态不错",
    "nervous": "有点紧张", "embarrassed": "有点尴尬", "sick": "身体不舒服",
    "excited": "兴奋", "relaxed": "放松", "energetic": "精力充沛",
}

MOOD_ZH = {
    "normal": "平静", "sleepy": "困倦", "tired": "疲惫", "energetic": "精神很好",
    "inspired": "有灵感", "nervous": "紧张", "embarrassed": "尴尬", "confident": "自信",
    "stressed": "焦躁", "hungry": "饿", "focused": "专注", "sick": "不舒服",
    "excited": "兴奋", "relaxed": "放松",
}


def get_turn_panel() -> dict[str, Any]:
    """生成每回合的简化状态面板（文本 + 结构化）。**面板必须来自代码。**"""
    s = get_session()
    player = s.state.player
    status = player.get("status", {})
    now = s.time.now_dict()
    location = s.registry.get("location", player.get("location")) or {}

    relevant_skills = sorted(
        ((k, v) for k, v in (player.get("skills") or {}).items() if v > 0),
        key=lambda kv: -kv[1],
    )[:4]
    relevant_knowledge = sorted(
        ((k, v) for k, v in (player.get("knowledge") or {}).items() if v > 0),
        key=lambda kv: -kv[1],
    )[:4]
    conditions = [CONDITION_ZH.get(c, c) for c in player.get("conditions", [])]

    date_obj = s.time.date
    lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"{s.state.world.get('school_name', '秋月学院')} · 角色状态",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"{date_obj.month}月{date_obj.day}日 · {now['weekday_zh']}",
        now["time"],
        "",
        f"位置：{location.get('name', '未知')}",
        f"天气：{now['weather_zh']}",
        "",
        f"健康 {status.get('health', 100)}/100",
        f"精力 {status.get('energy', 100)}/100",
        f"压力 {status.get('stress', 0)}/100",
        f"心情：{MOOD_ZH.get(status.get('mood', 'normal'), '平静')}",
        "",
        f"金钱：¥{status.get('money', 0):,}",
    ]
    if conditions:
        lines += ["", "状态："] + [f"• {c}" for c in conditions]
    if relevant_skills:
        lines += ["", "相关技能："] + [
            f"• {_skill_name(s, k)} Lv.{v}" for k, v in relevant_skills
        ]
    if relevant_knowledge:
        lines += ["", "相关知识："] + [
            f"• {_knowledge_name(s, k)} Lv.{v}" for k, v in relevant_knowledge
        ]
    lines.append("━━━━━━━━━━━━━━━━━━")

    return {
        "ok": True,
        "text": "\n".join(lines),
        "date": now["date"],
        "time": now["time"],
        "weekday": now["weekday_zh"],
        "location": {"id": player.get("location"), "name": location.get("name")},
        "weather": now["weather_zh"],
        "status": status,
        "conditions": player.get("conditions", []),
        "skills": dict(relevant_skills),
        "knowledge": dict(relevant_knowledge),
    }


def get_player_sheet() -> dict[str, Any]:
    """完整角色面板：属性、状态、全部技能与知识、XP、金钱、社团、公开关系描述。

    **默认不显示任何 NPC 的隐藏恋爱数值。**
    """
    s = get_session()
    player = s.state.player
    attributes = player.get("attributes", {})
    skills = player.get("skills", {})
    knowledge = player.get("knowledge", {})
    skill_xp = player.get("skill_xp", {})
    knowledge_xp = player.get("knowledge_xp", {})

    relationships: list[dict[str, Any]] = []
    for npc_id, rel in s.relationships.all_for("player").items():
        if rel.values.familiarity < 8:
            continue
        definition = s.npcs.definition(npc_id) or {}
        described = s.relationships.describe("player", npc_id)
        relationships.append(
            {
                "id": npc_id,
                "name": definition.get("name", npc_id),
                "label": described["label"],
                "hints": described.get("hints", []),
            }
        )
    relationships.sort(key=lambda r: r["name"])

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{player.get('name') or '（未命名）'} · {player.get('age')}岁 · {player.get('class', '')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "【属性】",
    ]
    for attribute in ATTRIBUTES:
        value = attributes.get(attribute, 4)
        lines.append(f"  {ATTRIBUTE_NAMES_ZH[attribute]} {value}  (修正 {value - 5:+d})")
    status = player.get("status", {})
    lines += [
        "",
        "【状态】",
        f"  健康 {status.get('health', 100)} / 精力 {status.get('energy', 100)} / 压力 {status.get('stress', 0)}",
        f"  心情 {MOOD_ZH.get(status.get('mood', 'normal'), '平静')}   金钱 ¥{status.get('money', 0):,}",
    ]
    if player.get("conditions"):
        lines.append("  当前效果：" + "、".join(CONDITION_ZH.get(c, c) for c in player["conditions"]))
    lines += ["", "【技能】"]
    if skills:
        for skill_id, level in sorted(skills.items(), key=lambda kv: -kv[1]):
            progress = xp_for_next_level(int(skill_xp.get(skill_id, 0)), s.state.config)
            lines.append(
                f"  {_skill_name(s, skill_id)} Lv.{level} ({SKILL_LEVEL_NAMES.get(level, '')})"
                f"  XP {progress['current']}/{progress['next_at'] if level < 5 else '—'}"
            )
    else:
        lines.append("  （还没有）")
    lines += ["", "【知识】"]
    if knowledge:
        for knowledge_id, level in sorted(knowledge.items(), key=lambda kv: -kv[1]):
            progress = xp_for_next_level(int(knowledge_xp.get(knowledge_id, 0)), s.state.config)
            lines.append(
                f"  {_knowledge_name(s, knowledge_id)} Lv.{level} ({KNOWLEDGE_LEVEL_NAMES.get(level, '')})"
                f"  XP {progress['current']}/{progress['next_at'] if level < 5 else '—'}"
            )
    else:
        lines.append("  （还没有）")
    clubs = player.get("clubs") or []
    lines += ["", "【社团】", "  " + ("、".join((s.registry.get("group", c) or {}).get("name", c) for c in clubs) if clubs else "（没有参加社团）")]
    lines += ["", "【人际关系】"]
    if relationships:
        for rel in relationships:
            hint = ("　" + "；".join(rel["hints"])) if rel["hints"] else ""
            lines.append(f"  {rel['name']}：{rel['label']}{hint}")
    else:
        lines.append("  （还没有认识的人）")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return {
        "ok": True,
        "text": "\n".join(lines),
        "name": player.get("name"),
        "age": player.get("age"),
        "attributes": attributes,
        "status": status,
        "conditions": player.get("conditions", []),
        "skills": skills,
        "knowledge": knowledge,
        "skill_xp": skill_xp,
        "knowledge_xp": knowledge_xp,
        "clubs": clubs,
        "relationships": relationships,
        "hidden_numbers_policy": "NPC 的 attraction / romantic_interest / trust 数值不在此显示，也不得告诉玩家。",
    }


def get_action_context() -> dict[str, Any]:
    """生成推荐行动所需的全部上下文。

    Agent 据此生成 3～5 条推荐行动。
    **没有合理恋爱机会时不要硬塞恋爱选项**（考试中、极度疲惫、重要社团事件中同理）。
    """
    s = get_session()
    player = s.state.player
    status = player.get("status", {})
    now = s.time.now_dict()
    location_id = player.get("location")
    location = s.registry.get("location", location_id) or {}

    nearby = s.npcs.nearby(location_id)
    calendar = s.time.calendar_events_today()
    calendar_tags = {t for e in calendar for t in (e.get("tags") or [])}

    # 可去的地点（当前开门的）
    available: list[dict[str, Any]] = []
    for loc_id in s.registry.ids("location"):
        if loc_id == location_id or not s.time.is_open(loc_id):
            continue
        loc = s.registry.get("location", loc_id) or {}
        available.append(
            {
                "id": loc_id,
                "name": loc.get("name"),
                "minutes": s.time.travel_minutes(location_id, loc_id),
                "tags": loc.get("tags", []),
            }
        )
    available.sort(key=lambda item: item["minutes"])

    energy = int(status.get("energy", 100))
    stress = int(status.get("stress", 0))
    exam_period = bool(calendar_tags & {"exam"})
    busy_event = bool(calendar_tags & {"major", "busy"})

    romance_candidates: list[dict[str, Any]] = []
    for npc in nearby:
        rel = s.relationships.get("player", npc["id"], create=False)
        if rel is None:
            continue
        definition = s.npcs.definition(npc["id"]) or {}
        if definition.get("role") == "teacher" or definition.get("romance_available") is False:
            continue
        if rel.values.familiarity >= 25 and rel.stage in ("friend", "close_friend", "ambiguous", "dating", "relationship"):
            romance_candidates.append({"id": npc["id"], "name": npc["name"], "stage": rel.stage})

    romance_ok = bool(romance_candidates) and energy >= 25 and stress < 80 and not exam_period
    suggested = ["social", "hobby", "study", "explore"]
    if energy < 30 or stress >= 70:
        suggested = ["rest", "social", "hobby"]
    if romance_ok:
        suggested.insert(1, "romance")
    if s.time.is_club_time() and player.get("clubs"):
        suggested.insert(0, "club")
    if exam_period:
        suggested = ["study", "rest", "social"]

    return {
        "ok": True,
        "current_time": {"date": now["date"], "time": now["time"], "weekday": now["weekday_zh"], "block": now["block"]},
        "current_location": {"id": location_id, "name": location.get("name"), "tags": location.get("tags", [])},
        "nearby_characters": nearby,
        "available_locations": available[:12],
        "current_events": calendar,
        "player_energy": energy,
        "player_stress": stress,
        "player_money": status.get("money", 0),
        "player_conditions": player.get("conditions", []),
        "clubs": player.get("clubs", []),
        "club_time": s.time.is_club_time(),
        "class_time": s.time.is_class_time(),
        "must_sleep": s.time.needs_forced_sleep(),
        "relationships": [
            {"id": npc["id"], "name": npc["name"], "label": npc["relationship"], "stage": npc["stage"]}
            for npc in nearby
        ],
        "recent_actions": s.state.world.get("recent_actions", [])[-6:],
        "recent_recommendations": s.state.world.get("recent_recommendations", [])[-12:],
        "romance_opportunity": romance_ok,
        "romance_candidates": romance_candidates,
        "suggested_categories": suggested,
        "player_skills": {k: v for k, v in (player.get("skills") or {}).items() if v > 0},
        "player_knowledge": {k: v for k, v in (player.get("knowledge") or {}).items() if v > 0},
        "constraints": [
            "推荐 3～5 条，尽量覆盖多个类型。",
            "不要泄露隐藏数值，不要暗示成功率。",
            "重复出现过的推荐要降低权重（见 recent_recommendations）。",
            "如果 romance_opportunity 为 false，不要出现约会类推荐。",
            "玩家永远可以输入任意自然语言，包括组合行动。",
        ],
    }


def record_recommendations(recommendations: list[str]) -> dict[str, Any]:
    """记录本回合给出的推荐行动，用于避免重复推荐。"""
    s = get_session()
    recent = s.state.world.setdefault("recent_recommendations", [])
    recent.extend(str(r)[:120] for r in (recommendations or []))
    s.state.world["recent_recommendations"] = recent[-int(s.state.cfg("recommendations.recent_memory_size", 12)):]
    return {"ok": True, "stored": len(s.state.world["recent_recommendations"])}


# ---------------------------------------------------------------------------
# 回合与存档
# ---------------------------------------------------------------------------


def end_turn(simulate_minutes: int = 60, autosave: bool = True) -> dict[str, Any]:
    """回合收尾：后台世界模拟 → 随机事件 → NPC 晋升检查 → 存档 → 面板 + 推荐上下文。

    Agent 在每个正常回合的最后调用一次。
    """
    s = get_session()
    s.state.world["turn"] = int(s.state.world.get("turn", 0)) + 1
    simulation = s.world.simulate(minutes=int(simulate_minutes))
    event = s.events.roll_random_event()
    promotions = s.npcs.check_all_promotions()
    saved = s.save() if autosave else {}
    return {
        "ok": True,
        "turn": s.state.world["turn"],
        "simulation": {
            "interactions": len(simulation["interactions"]),
            "romance_events": simulation["romance_events"],
            "deviations": len(simulation["schedule_deviations"]),
        },
        "random_event": event,
        "promotions": promotions,
        "discoverable": s.world.discoverable(),
        "panel": get_turn_panel(),
        "action_context": get_action_context(),
        "saved": bool(saved),
    }


def save_game(slot: str = "save_001") -> dict[str, Any]:
    """保存游戏到指定存档槽（同时写入 state/）。原子写入 + 备份。"""
    s = get_session()
    try:
        path = s.manager.save_game(s.state, slot, rng=s.rng)
    except (ValidationError, GameError, OSError) as exc:
        return _error(str(exc))
    return {"ok": True, "slot": slot, "path": path}


def load_game(slot: str = "save_001") -> dict[str, Any]:
    """读取存档。"""
    s = get_session()
    try:
        state = s.manager.load_game(slot)
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    s.reload_from(state)
    return {"ok": True, "slot": slot, "world": s.time.now_dict()}


def list_saves() -> dict[str, Any]:
    """列出全部存档。"""
    return {"ok": True, "saves": get_session().manager.list_saves()}


def new_game(seed: int | None = None, player: dict[str, Any] | None = None) -> dict[str, Any]:
    """开始新游戏（会覆盖 state/ 下的当前状态）。"""
    session = GameSession(seed=seed, autoload=False)
    if player:
        session.state.characters["player"].update(player)
    reset_session(session)
    session.save()
    return {"ok": True, "world": session.time.now_dict(), "seed": session.rng.seed}


def create_player(
    name: str,
    age: int = 18,
    gender: str = "unspecified",
    attributes: dict[str, int] | None = None,
    skills: list[str] | None = None,
    knowledge: list[str] | None = None,
    appearance: str = "",
    interests: list[str] | None = None,
    personality_tendency: list[str] | None = None,
    pronouns: str = "",
    preset: str | None = None,
) -> dict[str, Any]:
    """创建玩家角色。

    属性：基础 4，自由分配 12 点，创建时下限 3、上限 8（总和 40）。
    技能：选 3 个，初始 Lv.2。知识：选 3～5 个，初始 Lv.2。
    **age 必须 >= 18。**
    """
    s = get_session()
    rules = (s.state.static.get("player_template") or {}).get("creation_rules", {})
    try:
        from .models import validate_age

        age = validate_age(age, context="玩家角色")
        if preset:
            presets = {p["id"]: p for p in (s.state.static.get("player_template") or {}).get("presets", [])}
            chosen = presets.get(preset)
            if not chosen:
                raise ValidationError(f"未知预设: {preset}（可用: {', '.join(presets)}）")
            attributes = attributes or dict(chosen["attributes"])
            skills = skills or list(chosen["skills"])
            knowledge = knowledge or list(chosen["knowledge"])

        attributes = dict(attributes or {})
        for attribute in ATTRIBUTES:
            attributes.setdefault(attribute, int(rules.get("attribute_base", 4)))
        unknown = set(attributes) - set(ATTRIBUTES)
        if unknown:
            raise ValidationError(f"未知属性: {', '.join(sorted(unknown))}")
        low, high = int(rules.get("attribute_min", 3)), int(rules.get("attribute_max", 8))
        for attribute, value in attributes.items():
            if not low <= int(value) <= high:
                raise ValidationError(f"属性 {attribute}={value} 超出创建范围 {low}~{high}")
        total = sum(int(v) for v in attributes.values())
        expected = int(rules.get("attribute_base", 4)) * len(ATTRIBUTES) + int(rules.get("attribute_points", 12))
        if total != expected:
            raise ValidationError(f"属性点总和必须是 {expected}（当前 {total}）")

        skills = list(skills or [])
        knowledge = list(knowledge or [])
        if len(skills) != int(rules.get("skill_choices", 3)):
            raise ValidationError(f"必须选择 {rules.get('skill_choices', 3)} 个初始技能")
        if not int(rules.get("knowledge_choices_min", 3)) <= len(knowledge) <= int(rules.get("knowledge_choices_max", 5)):
            raise ValidationError("必须选择 3~5 个初始知识")
        for skill_id in skills:
            if not s.registry.exists("skill", skill_id):
                raise ValidationError(f"技能不存在: {skill_id}")
        for knowledge_id in knowledge:
            if not s.registry.exists("knowledge", knowledge_id):
                raise ValidationError(f"知识不存在: {knowledge_id}")
    except (ValidationError, GameError) as exc:
        return _error(str(exc))

    player = s.state.player
    player.update(
        {
            "name": name,
            "age": age,
            "gender": gender,
            "pronouns": pronouns or None,
            "appearance": appearance,
            "interests": list(interests or []),
            "personality_tendency": list(personality_tendency or []),
            "attributes": {k: int(v) for k, v in attributes.items()},
            "skills": {sid: int(rules.get("skill_start_level", 2)) for sid in skills},
            "knowledge": {kid: int(rules.get("knowledge_start_level", 2)) for kid in knowledge},
            "attribute_points_remaining": 0,
        }
    )
    for skill_id in skills:
        s.progression.set_level("player", "skill", skill_id, int(rules.get("skill_start_level", 2)))
    for knowledge_id in knowledge:
        s.progression.set_level("player", "knowledge", knowledge_id, int(rules.get("knowledge_start_level", 2)))
    s.save()
    return {"ok": True, "player": get_player_state()}


def join_club(club_id: str) -> dict[str, Any]:
    """加入社团（上限 2 个）。"""
    s = get_session()
    club = s.registry.get("group", club_id)
    if not club:
        return _error(f"社团不存在: {club_id}")
    clubs = s.state.player.setdefault("clubs", [])
    limit = int((s.state.static.get("clubs") or {}).get("policy", {}).get("max_clubs_per_player", 2))
    if club_id in clubs:
        return {"ok": True, "joined": False, "reason": "已经在这个社团了", "clubs": clubs}
    if len(clubs) >= limit:
        return {"ok": True, "joined": False, "reason": f"最多只能加入 {limit} 个社团", "clubs": clubs}
    clubs.append(club_id)
    return {"ok": True, "joined": True, "club": club.get("name"), "clubs": clubs}


def leave_club(club_id: str) -> dict[str, Any]:
    """退出社团。"""
    s = get_session()
    clubs = s.state.player.setdefault("clubs", [])
    if club_id not in clubs:
        return {"ok": True, "left": False, "reason": "本来就不在这个社团"}
    clubs.remove(club_id)
    return {"ok": True, "left": True, "clubs": clubs}


# ---------------------------------------------------------------------------
# 世界资料（供 Agent 读取设定）
# ---------------------------------------------------------------------------


def get_content_rules() -> dict[str, Any]:
    """获取内容硬规则与配比表（config/content_rules.yaml 原样返回）。"""
    return {"ok": True, "rules": get_session().state.static.get("content_rules", {})}


def get_world_lore(topic: str = "school") -> dict[str, Any]:
    """读取世界设定文档。topic: school / culture / rules / agent"""
    s = get_session()
    key = f"doc_{topic}"
    if key not in s.state.static:
        return _error(f"未知主题: {topic}（可用: school, culture, rules, agent）")
    return {"ok": True, "topic": topic, "text": s.state.static[key]}


def get_rules_digest() -> dict[str, Any]:
    """一次性获取所有 Agent 需要的规则速查表（难度、修正、关系事件、行动类型）。"""
    s = get_session()
    return {
        "ok": True,
        "difficulty": {d.name.lower(): {"dc": d.dc, "label": d.label} for d in Difficulty},
        "modifiers": {
            "attribute": "attribute - 5",
            "skill": "level * 2",
            "knowledge": {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 3},
            "situational": "-5 ~ +5",
        },
        "success_levels": {
            "strong_success": "margin >= 5", "success": "margin >= 0",
            "failure": "margin >= -4", "major_failure": "margin <= -5",
        },
        "natural": {"20": "结果等级 +1（不能剥夺 NPC 自主权）", "1": "结果等级 -1（不能制造荒谬灾难）"},
        "relationship_events": {
            k: {"label": v.get("label"), "weight": v.get("weight")} for k, v in sorted(RELATIONSHIP_EVENTS.items())
        },
        "action_types": {
            k: {"category": v.get("category"), "attribute": v.get("attribute"),
                "skill": v.get("skill"), "minutes": v.get("minutes")}
            for k, v in sorted(ACTION_TYPES.items())
        },
        "invite_types": list(RelationshipManager.INVITE_REQUIREMENTS.keys()),
        "hard_rules": [r["text"] for r in (s.state.static.get("content_rules") or {}).get("hard_rules", [])],
    }


def get_locations(area: str | None = None, open_only: bool = False) -> dict[str, Any]:
    """列出地点（可按 school / town 过滤，或只看现在开着的）。"""
    s = get_session()
    out = []
    for loc_id in s.registry.ids("location"):
        loc = s.registry.get("location", loc_id) or {}
        if area and loc.get("area") != area:
            continue
        is_open = s.time.is_open(loc_id)
        if open_only and not is_open:
            continue
        out.append(
            {
                "id": loc_id, "name": loc.get("name"), "area": loc.get("area"),
                "zone": loc.get("zone"), "tags": loc.get("tags", []), "open": is_open,
                "minutes_from_here": s.time.travel_minutes(s.state.player.get("location"), loc_id),
                "source": loc.get("source", "static"),
            }
        )
    return {"ok": True, "count": len(out), "locations": out}


def get_clubs() -> dict[str, Any]:
    """列出所有社团 / 组织。"""
    s = get_session()
    out = []
    for group_id in s.registry.ids("group"):
        group = s.registry.get("group", group_id) or {}
        out.append(
            {
                "id": group_id, "name": group.get("name"), "type": group.get("type", "club"),
                "location": group.get("location"), "members": group.get("members", []),
                "leader": group.get("leader"), "activity_days": group.get("activity_days"),
                "description": group.get("description") or group.get("purpose"),
            }
        )
    return {"ok": True, "count": len(out), "groups": out}


def get_rng_log(n: int = 20) -> dict[str, Any]:
    """查看最近的骰点日志（审计用：证明没有人偷偷重骰）。"""
    s = get_session()
    return {"ok": True, "seed": s.rng.seed, "total_rolls": s.rng.count, "log": s.rng.recent_log(int(n))}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _error(message: str, *, hint: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "error": message}
    if hint:
        result["hint"] = hint
    return result


ACTION_ZH = {
    "greet": "打招呼", "small_talk": "闲聊", "talk": "会话", "deep_talk": "深谈",
    "persuade": "说服", "invite": "邀请", "comfort": "安慰", "apologize": "道歉",
    "compliment": "称赞", "joke": "玩笑", "help_someone": "帮忙", "ask_contact": "要联系方式",
    "share_topic": "分享话题", "read_the_room": "读空气", "flirt": "调情", "date": "约会",
    "confess": "告白", "walk_home": "一起回家", "study": "学习", "attend_class": "上课",
    "exam": "考试", "take_notes": "记笔记", "research": "查资料", "practice": "练习",
    "club_activity": "社团活动", "perform": "表演", "photo": "摄影", "cook": "料理",
    "draw": "绘画", "write": "写作", "play_game": "游戏", "sing": "歌唱",
    "exercise": "运动", "run": "跑步", "swim": "游泳", "explore": "探索",
    "observe": "观察", "message": "发消息", "custom": "行动",
}


def _localize_modifier(name: str) -> str:
    """把内部修正名翻译成给玩家看的中文标签。"""
    session = get_session()
    if name.startswith("attribute:"):
        return ATTRIBUTE_NAMES_ZH.get(name.split(":", 1)[1], name)
    if name.startswith("skill:"):
        raw = name.split(":", 1)[1]
        skill_id, _, level = raw.partition("(")
        return f"{_skill_name(session, skill_id)} {level.rstrip(')')}".strip()
    if name.startswith("knowledge:"):
        raw = name.split(":", 1)[1]
        knowledge_id, _, level = raw.partition("(")
        return f"{_knowledge_name(session, knowledge_id)} {level.rstrip(')')}".strip()
    if name == "situational":
        return "情境"
    if name == "clipped_to_range":
        return "情境上限修正"
    return name


def _check_text(check: dict[str, Any]) -> str:
    action = check.get("action_type", "")
    title = ACTION_ZH.get(action)
    if not title and check.get("skill"):
        title = _skill_name(get_session(), check["skill"])
    lines = ["【判定】", "", f"{title or action or ''}检定", "",
             f"D20：{check['roll']}" + (f"  ({'大成功' if check['natural'] == 'natural_20' else '大失败'})"
                                        if check.get("natural") else "")]
    for item in check.get("modifiers_detail", []):
        if item["value"]:
            lines.append(f"{_localize_modifier(item['name'])}：{item['value']:+g}")
    lines += ["", f"总计：{check['total']}", f"DC：{check['dc']}", "", CheckResult(check["result"]).label]
    return "\n".join(lines)


def _growth_text(awards: list[dict[str, Any]]) -> str:
    s = get_session()
    lines: list[str] = []
    for award in awards:
        if award.get("gained", 0) <= 0:
            continue
        name = _skill_name(s, award["id"]) if award["kind"] == "skill" else _knowledge_name(s, award["id"]) + "知识"
        lines.append(f"{name} +{award['gained']} XP")
        if award.get("level_up"):
            lines.append(f"  → 提升到 Lv.{award['level_after']}")
    return "【成长】\n\n" + "\n".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# 工具注册表（供任意 Agent / MCP / CLI 使用）
# ---------------------------------------------------------------------------

TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    fn.__name__: fn
    for fn in [
        # 读取
        get_world_state, get_player_state, get_character_state, get_nearby_characters,
        get_relationship, get_relevant_memories, get_schedule, get_locations, get_clubs,
        get_content_rules, get_world_lore, get_rules_digest, get_registry, find_duplicate,
        get_event_candidates, get_background_events, get_rng_log, list_saves,
        # 判定 / 时间 / 行动
        resolve_check, should_check, advance_time, sleep, move_character, perform_action,
        buy_item,
        # 关系 / 记忆
        apply_relationship_event, npc_decide_invitation, npc_decide_confession, add_memory,
        # 事件 / 世界
        roll_random_event, trigger_event, simulate_background_world,
        # 动态注册
        create_npc, promote_npc, check_npc_promotions, register_skill, register_knowledge,
        register_location, register_group, add_dynamic_interest,
        # 面板 / 回合 / 存档
        get_turn_panel, get_player_sheet, get_action_context, record_recommendations,
        end_turn, save_game, load_game, new_game, create_player, join_club, leave_club,
    ]
}

_TYPE_MAP = {
    "str": "string", "int": "integer", "float": "number", "bool": "boolean",
    "dict": "object", "list": "array",
}


def _param_schema(annotation: Any) -> dict[str, Any]:
    text = str(annotation).replace("typing.", "")
    if annotation is inspect.Parameter.empty or "Any" in text:
        return {}
    for key, value in _TYPE_MAP.items():
        if text.startswith(key) or text.startswith(f"{key} |") or f"{key} " in text[:12]:
            schema: dict[str, Any] = {"type": value}
            if value == "array":
                schema["items"] = {}
            return schema
    return {}


def tool_schemas() -> list[dict[str, Any]]:
    """返回所有工具的 JSON Schema —— 任何 Agent 框架都能直接消费。"""
    schemas: list[dict[str, Any]] = []
    for name, fn in TOOLS.items():
        signature = inspect.signature(fn)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in signature.parameters.items():
            properties[param_name] = _param_schema(param.annotation)
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
        doc = inspect.getdoc(fn) or ""
        schemas.append(
            {
                "name": name,
                "description": doc,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
        )
    return schemas


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """按名字调用工具。**这是给外部 Agent 的统一入口。**"""
    fn = TOOLS.get(name)
    if fn is None:
        return _error(f"未知工具: {name}", hint=f"可用工具: {', '.join(sorted(TOOLS))}")
    arguments = dict(arguments or {})
    signature = inspect.signature(fn)
    unknown = set(arguments) - set(signature.parameters)
    if unknown:
        return _error(f"未知参数: {', '.join(sorted(unknown))}", hint=f"{name} 接受: {', '.join(signature.parameters)}")
    try:
        return fn(**arguments)
    except TypeError as exc:
        return _error(f"参数错误: {exc}")
    except (ValidationError, GameError) as exc:
        return _error(str(exc))
    except Exception as exc:  # pragma: no cover - 兜底，绝不把异常抛给 Agent
        log.exception("tool %s failed", name)
        return _error(f"内部错误: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    command = argv[0]
    if command == "list":
        for name in sorted(TOOLS):
            doc = (inspect.getdoc(TOOLS[name]) or "").splitlines()[0]
            print(f"{name:<28} {doc}")
        return 0
    if command == "schema":
        print(json.dumps(tool_schemas(), ensure_ascii=False, indent=2))
        return 0
    if command == "call":
        if len(argv) < 2:
            print("用法: python -m engine.tools call <tool_name> '<json_args>'", file=sys.stderr)
            return 2
        name = argv[1]
        raw = argv[2] if len(argv) > 2 else "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"参数不是合法 JSON: {exc}"}, ensure_ascii=False))
            return 2
        result = call_tool(name, arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    print(f"未知命令: {command}（可用: list / schema / call）", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(_main(sys.argv[1:]))
