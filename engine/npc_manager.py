"""NPC 管理：定义、运行时状态、日程、动态创建与分级晋升。

分级
----
``background`` → ``supporting`` → ``core``

- background：ID / 姓名 / 年龄 / 身份 / 少量人格标签
- supporting：+ 属性 / 技能 / 知识 / 日程 / 关系 / 记忆 / 兴趣 / 目标
- core：完整角色系统

**NPC 不是攻略奖励。** 他们可以对玩家无感、只当朋友、喜欢别人、
已经在交往、拒绝玩家，也可以主动追求玩家。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .models import (
    ATTRIBUTES,
    GameState,
    MIN_AGE,
    NPCTier,
    ValidationError,
    clamp_int,
    normalize_name,
    sanitize_text,
    validate_age,
    validate_id,
    validate_level,
)
from .registry_manager import RegistryManager
from .relationship_manager import RelationshipManager
from .rng import GameRNG
from .time_manager import TimeManager, parse_hhmm

log = logging.getLogger("engine.npc")

DEFAULT_STATUS = {"health": 100, "energy": 100, "stress": 15, "mood": "normal", "money": 5000}

PLACEHOLDER_KEYS = ("@home_class", "@club_location", "@home", "@favorite_place", "@work")


class NPCManager:
    def __init__(
        self,
        state: GameState,
        rng: GameRNG,
        registry: RegistryManager,
        time_manager: TimeManager,
        relationships: RelationshipManager,
    ) -> None:
        self.state = state
        self.rng = rng
        self.registry = registry
        self.time = time_manager
        self.relationships = relationships
        relationships.npc_lookup = self.definition

    # ------------------------------------------------------------------
    # 定义与运行时
    # ------------------------------------------------------------------
    def definition(self, npc_id: str) -> dict[str, Any] | None:
        if npc_id == "player":
            return self.state.player
        return self.registry.get("npc", npc_id)

    def exists(self, npc_id: str) -> bool:
        return npc_id == "player" or self.registry.exists("npc", npc_id)

    def all_ids(self) -> list[str]:
        return self.registry.ids("npc")

    def runtime(self, npc_id: str) -> dict[str, Any]:
        """获取（必要时初始化）NPC 的运行时状态。"""
        if npc_id == "player":
            return self.state.player
        if not self.exists(npc_id):
            raise ValidationError(f"未知 NPC: {npc_id}")
        npcs = self.state.npcs
        if npc_id not in npcs:
            definition = self.definition(npc_id) or {}
            npcs[npc_id] = {
                "id": npc_id,
                "tier": definition.get("tier", NPCTier.BACKGROUND.value),
                "status": dict(DEFAULT_STATUS),
                "location": None,
                "current_activity": None,
                "skills": dict(definition.get("skills") or {}),
                "knowledge": dict(definition.get("knowledge") or {}),
                "skill_xp": {},
                "knowledge_xp": {},
                "interaction_count": 0,
                "flags": {},
                "dynamic_interests": [],
            }
        return npcs[npc_id]

    def character_state(self, npc_id: str, *, include_hidden: bool = False) -> dict[str, Any]:
        """合并定义 + 运行时的对外视图。

        ``include_hidden=False`` 时会移除 secrets / hidden_personality 等
        玩家不该看到的字段（Agent 需要时可显式索取）。
        """
        if npc_id == "player":
            return dict(self.state.player)
        definition = self.definition(npc_id)
        if definition is None:
            raise ValidationError(f"未知 NPC: {npc_id}")
        runtime = self.runtime(npc_id)
        merged = dict(definition)
        merged.update(
            {
                "tier": runtime.get("tier", definition.get("tier")),
                "status": runtime.get("status"),
                "location": self.where_is(npc_id),
                "current_activity": runtime.get("current_activity"),
                "skills": {**(definition.get("skills") or {}), **(runtime.get("skills") or {})},
                "knowledge": {**(definition.get("knowledge") or {}), **(runtime.get("knowledge") or {})},
                "interaction_count": runtime.get("interaction_count", 0),
                "dynamic_interests": runtime.get("dynamic_interests", []),
            }
        )
        if not include_hidden:
            for field in ("secrets", "hidden_personality"):
                merged.pop(field, None)
            merged["_hidden_fields_note"] = (
                "secrets / hidden_personality 未包含。"
                "调用 get_character_state(include_hidden=True) 才能取得，"
                "且这些内容绝不能让 NPC 说出自己不知道的部分。"
            )
        return merged

    # ------------------------------------------------------------------
    # 属性 / 技能 / 知识
    # ------------------------------------------------------------------
    def attribute(self, npc_id: str, attribute: str) -> int:
        if attribute not in ATTRIBUTES:
            raise ValidationError(f"未知属性: {attribute}")
        if npc_id == "player":
            return int((self.state.player.get("attributes") or {}).get(attribute, 4))
        definition = self.definition(npc_id) or {}
        return int((definition.get("attributes") or {}).get(attribute, 5))

    def skill_level(self, npc_id: str, skill_id: str | None) -> int:
        if not skill_id:
            return 0
        character = self.state.player if npc_id == "player" else self.runtime(npc_id)
        level = (character.get("skills") or {}).get(skill_id)
        if level is None and npc_id != "player":
            level = ((self.definition(npc_id) or {}).get("skills") or {}).get(skill_id, 0)
        return clamp_int(level or 0, 0, 5)

    def knowledge_level(self, npc_id: str, knowledge_id: str | None) -> int:
        if not knowledge_id:
            return 0
        character = self.state.player if npc_id == "player" else self.runtime(npc_id)
        level = (character.get("knowledge") or {}).get(knowledge_id)
        if level is None and npc_id != "player":
            level = ((self.definition(npc_id) or {}).get("knowledge") or {}).get(knowledge_id, 0)
        return clamp_int(level or 0, 0, 5)

    # ------------------------------------------------------------------
    # 日程与位置
    # ------------------------------------------------------------------
    def _resolve_placeholder(self, npc_id: str, token: str | None) -> str | None:
        if not token:
            return None
        definition = self.definition(npc_id) or {}
        if not str(token).startswith("@"):
            return str(token)
        if token == "@home_class":
            class_id = definition.get("class") or definition.get("homeroom")
            mapping = {"class_2a": "loc_class_2a", "class_2b": "loc_class_2b", "class_3a": "loc_class_3a"}
            return mapping.get(str(class_id), "loc_class_2b")
        if token == "@club_location":
            club = self.registry.get("group", definition.get("club")) if definition.get("club") else None
            return (club or {}).get("location") or definition.get("favorite_place") or "loc_corridor"
        if token == "@home":
            return definition.get("home_location") or "loc_station"
        if token == "@favorite_place":
            return definition.get("favorite_place") or definition.get("home_location") or "loc_shopping_street"
        if token == "@work":
            return "loc_staff_room" if definition.get("role") == "teacher" else "loc_class_2b"
        return None

    def get_schedule(self, npc_id: str, *, weekday: str | None = None, minutes: int | None = None) -> dict[str, Any]:
        """返回 NPC 在指定时刻的**计划**位置与活动。

        注意：计划 ≠ 实际。NPC 有自己的目标和随机变化，玩家去找人不一定找得到。
        """
        definition = self.definition(npc_id) or {}
        weekday = weekday or self.time.weekday
        minutes = self.time.minutes if minutes is None else minutes

        for entry in definition.get("schedule_overrides") or []:
            days = entry.get("days") or []
            if weekday not in days:
                continue
            start, end = parse_hhmm(entry["start"]), parse_hhmm(entry["end"])
            inside = start <= minutes < end if end > start else (minutes >= start or minutes < end)
            if inside:
                return {
                    "npc_id": npc_id,
                    "location": self._resolve_placeholder(npc_id, entry.get("location")),
                    "activity": entry.get("activity", ""),
                    "source": "override",
                    "start": entry["start"],
                    "end": entry["end"],
                }

        routines = (self.state.static.get("schedule") or {}).get("default_npc_routine", {})
        role = "teacher" if definition.get("role") == "teacher" else "student"
        day_type = self.time.day_type()
        key = "school_day" if day_type in ("school", "school_no_club", "half_day") else "holiday"
        for entry in (routines.get(role) or {}).get(key, []) or []:
            start, end = parse_hhmm(entry["start"]), parse_hhmm(entry["end"])
            inside = start <= minutes < end if end > start else (minutes >= start or minutes < end)
            if inside:
                return {
                    "npc_id": npc_id,
                    "location": self._resolve_placeholder(npc_id, entry.get("location")),
                    "activity": entry.get("activity", ""),
                    "source": "default",
                    "start": entry["start"],
                    "end": entry["end"],
                }
        return {
            "npc_id": npc_id,
            "location": self._resolve_placeholder(npc_id, "@home"),
            "activity": "自由时间",
            "source": "fallback",
        }

    def where_is(self, npc_id: str) -> str | None:
        """NPC 现在实际在哪（考虑临时偏离）。"""
        runtime = self.runtime(npc_id)
        override = runtime.get("location_override")
        if override and int(runtime.get("override_until_day", -1)) == self.time.day_index:
            until = int(runtime.get("override_until_minutes", 0))
            if self.time.minutes < until:
                return str(override)
            runtime["location_override"] = None
        return self.get_schedule(npc_id).get("location")

    def move_character(
        self, npc_id: str, location_id: str, *, duration_minutes: int = 60, activity: str = ""
    ) -> dict[str, Any]:
        """把角色移动到某个地点（玩家或 NPC）。"""
        if not self.registry.exists("location", location_id):
            raise ValidationError(f"地点不存在: {location_id}。请先 register_location()。")
        if npc_id == "player":
            self.state.player["location"] = location_id
            return {"character": "player", "location": location_id}
        runtime = self.runtime(npc_id)
        runtime["location_override"] = location_id
        runtime["override_until_day"] = self.time.day_index
        runtime["override_until_minutes"] = min(24 * 60 - 1, self.time.minutes + int(duration_minutes))
        if activity:
            runtime["current_activity"] = sanitize_text(activity, max_len=120, field_name="活动")
        return {
            "character": npc_id,
            "location": location_id,
            "until": f"{runtime['override_until_minutes'] // 60:02d}:{runtime['override_until_minutes'] % 60:02d}",
        }

    def nearby(self, location_id: str | None = None, *, include_tier: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """当前地点有谁。"""
        location_id = location_id or self.state.player.get("location")
        tiers = set(include_tier or ("core", "supporting", "background"))
        out: list[dict[str, Any]] = []
        for npc_id in self.all_ids():
            runtime = self.runtime(npc_id)
            if str(runtime.get("tier", "background")) not in tiers:
                continue
            if self.where_is(npc_id) != location_id:
                continue
            definition = self.definition(npc_id) or {}
            schedule = self.get_schedule(npc_id)
            rel = self.relationships.describe("player", npc_id)
            out.append(
                {
                    "id": npc_id,
                    "name": definition.get("name", npc_id),
                    "age": definition.get("age"),
                    "role": definition.get("role"),
                    "tier": runtime.get("tier"),
                    "activity": runtime.get("current_activity") or schedule.get("activity"),
                    "mood": (runtime.get("status") or {}).get("mood"),
                    "relationship": rel.get("label"),
                    "stage": rel.get("stage"),
                }
            )
        return out

    # ------------------------------------------------------------------
    # 动态创建
    # ------------------------------------------------------------------
    def _suggest_id(self, name: str, role: str, reading: str = "") -> str:
        base = normalize_name(reading or name)
        ascii_base = "".join(ch for ch in base if ch.isascii() and ch.isalnum())
        if not ascii_base:
            ascii_base = f"{role}{len(self.all_ids()) + 1}"
        candidate = f"npc_{ascii_base}"[:60]
        suffix = 1
        while self.registry.exists("npc", candidate):
            suffix += 1
            candidate = f"npc_{ascii_base}_{suffix}"[:60]
        return candidate

    def create_npc(
        self,
        *,
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
        interests: Iterable[str] | None = None,
        attributes: dict[str, int] | None = None,
        skills: dict[str, int] | None = None,
        knowledge: dict[str, int] | None = None,
        home_location: str | None = None,
        favorite_place: str | None = None,
        schedule_overrides: list[dict[str, Any]] | None = None,
        social_links: list[dict[str, Any]] | None = None,
        romance_available: bool = True,
        existing_partner: str | None = None,
        short_term_goals: Iterable[str] | None = None,
        relationship_attitude: str = "",
        romantic_preferences: str = "",
        created_reason: str = "",
        allow_isolated: bool = False,
    ) -> dict[str, Any]:
        """创建一个会持久存在的 NPC。

        强制校验：
          1. ``age >= 18``（世界硬规则）
          2. ID / 姓名查重
          3. 地点必须已注册
          4. 技能 / 知识必须已注册
          5. **社交网络**：新 NPC 至少要认识一个已有角色（不能只认识玩家）
        """
        name = sanitize_text(name, max_len=40, field_name="姓名")
        if not name:
            raise ValidationError("NPC 必须有名字")
        age = validate_age(age, context=f"NPC「{name}」")
        if role == "student" and age < MIN_AGE:
            raise ValidationError("学生 NPC 必须成年")
        if tier not in {t.value for t in NPCTier}:
            raise ValidationError(f"tier 必须是 background/supporting/core，收到 {tier!r}")

        npc_id = validate_id(npc_id, "npc_id") if npc_id else self._suggest_id(name, role, reading)
        duplicate = self.registry.find_duplicate("npc", entry_id=npc_id, name=name)
        if duplicate:
            raise ValidationError(
                f"拒绝创建 NPC：与已有角色 '{duplicate['id']}'（{duplicate['name']}）重复。"
                " 如果就是同一个人，请直接使用已有 ID。"
            )

        for loc in (home_location, favorite_place):
            if loc and not self.registry.exists("location", loc):
                raise ValidationError(f"地点不存在: {loc}。请先 register_location()。")
        if club and not self.registry.exists("group", club):
            raise ValidationError(f"社团/组织不存在: {club}。请先 register_group()。")

        skills = {k: validate_level(v, kind="技能等级") for k, v in (skills or {}).items()}
        knowledge = {k: validate_level(v, kind="知识等级") for k, v in (knowledge or {}).items()}
        for skill_id in skills:
            if not self.registry.exists("skill", skill_id):
                raise ValidationError(f"技能未注册: {skill_id}。请先 register_skill()。")
        for knowledge_id in knowledge:
            if not self.registry.exists("knowledge", knowledge_id):
                raise ValidationError(f"知识未注册: {knowledge_id}。请先 register_knowledge()。")

        attrs = {k: clamp_int(int(v), 1, 10) for k, v in (attributes or {}).items() if k in ATTRIBUTES}
        if tier != NPCTier.BACKGROUND.value:
            for attribute in ATTRIBUTES:
                attrs.setdefault(attribute, self.rng.randint(3, 7, reason=f"npc_attr:{npc_id}"))

        links = list(social_links or [])
        if not links and not allow_isolated:
            raise ValidationError(
                "新 NPC 必须至少与一个已有角色建立关系（social_links）。"
                " 世界里的人不能只认识玩家。若确实是完全的陌生人，请显式传 allow_isolated=True。"
            )

        definition: dict[str, Any] = {
            "id": npc_id,
            "name": name,
            "reading": sanitize_text(reading, max_len=60, field_name="读音"),
            "age": age,
            "gender": gender,
            "role": role,
            "tier": tier,
            "class": class_id,
            "club": club,
            "archetype": archetype,
            "appearance": sanitize_text(appearance, max_len=800, field_name="外貌"),
            "personality": sanitize_text(personality, max_len=800, field_name="性格"),
            "speech_style": sanitize_text(speech_style, max_len=500, field_name="说话方式"),
            "interests": [sanitize_text(i, max_len=40, field_name="兴趣") for i in (interests or [])],
            "attributes": attrs,
            "skills": skills,
            "knowledge": knowledge,
            "home_location": home_location,
            "favorite_place": favorite_place,
            "schedule_overrides": list(schedule_overrides or []),
            "romance_available": bool(romance_available),
            "existing_partner": existing_partner,
            "short_term_goals": list(short_term_goals or []),
            "relationship_attitude": sanitize_text(relationship_attitude, max_len=500, field_name="恋爱态度"),
            "romantic_preferences": sanitize_text(romantic_preferences, max_len=500, field_name="恋爱倾向"),
            "social_circle": [str(link.get("npc_id")) for link in links if link.get("npc_id")],
            "created_reason": sanitize_text(created_reason, max_len=300, field_name="创建理由"),
            "memories": [],
            "secrets": [],
        }
        self.registry.register_npc_definition(definition)
        self.runtime(npc_id)

        created_links: list[dict[str, Any]] = []
        for link in links:
            other = str(link.get("npc_id", ""))
            if other == npc_id:
                continue
            if other != "player" and not self.exists(other):
                raise ValidationError(f"social_links 中的角色不存在: {other}")
            values = {
                dim: int(link.get(dim, default))
                for dim, default in (
                    ("familiarity", 35), ("trust", 25), ("closeness", 25),
                    ("attraction", 5), ("romantic_interest", 3), ("comfort", 35), ("conflict", 2),
                )
            }
            note = sanitize_text(link.get("note", ""), max_len=300, field_name="关系备注")
            self.relationships.set_values(npc_id, other, values, notes=note)
            if link.get("mutual", True):
                self.relationships.set_values(other, npc_id, values, notes=note)
            self.relationships.refresh_stage(npc_id, other)
            self.relationships.refresh_stage(other, npc_id)
            created_links.append({"with": other, "values": values})

        # 与玩家的初始关系（默认：陌生人）
        self.relationships.get("player", npc_id)
        self.relationships.get(npc_id, "player")

        log.info("created npc %s (%s, %s, tier=%s)", npc_id, name, age, tier)
        return {
            "id": npc_id,
            "definition": definition,
            "social_links": created_links,
            "note": "已持久化。若这个人只是路人，本来就不应该创建。",
        }

    # ------------------------------------------------------------------
    # 晋升
    # ------------------------------------------------------------------
    def check_promotion(self, npc_id: str) -> dict[str, Any]:
        """按互动次数 / 熟悉度 / 共同经历判断是否应当晋升。"""
        runtime = self.runtime(npc_id)
        current = NPCTier(str(runtime.get("tier", "background")))
        if current is NPCTier.CORE:
            return {"npc_id": npc_id, "tier": current.value, "promote": False, "reason": "已经是 core"}

        rel = self.relationships.get("player", npc_id)
        assert rel is not None
        thresholds = (self.state.config.get("npc") or {}).get("promotion_thresholds", {})
        interactions = rel.interaction_count
        familiarity = rel.values.familiarity
        shared = len(rel.shared_experiences)

        if current is NPCTier.BACKGROUND:
            need = thresholds.get("background_to_supporting", {"interactions": 3, "familiarity": 20})
            ok = interactions >= int(need.get("interactions", 3)) and familiarity >= int(need.get("familiarity", 20))
            target = NPCTier.SUPPORTING
        else:
            need = thresholds.get("supporting_to_core", {"interactions": 12, "familiarity": 45, "shared_experiences": 3})
            ok = (
                interactions >= int(need.get("interactions", 12))
                and familiarity >= int(need.get("familiarity", 45))
                and shared >= int(need.get("shared_experiences", 3))
            )
            target = NPCTier.CORE

        return {
            "npc_id": npc_id,
            "tier": current.value,
            "promote": bool(ok),
            "target": target.value,
            "interactions": interactions,
            "familiarity": familiarity,
            "shared_experiences": shared,
            "requirement": need,
        }

    def promote_npc(self, npc_id: str, tier: str | None = None, *, force: bool = False) -> dict[str, Any]:
        """晋升 NPC 等级，并按新等级补齐所需字段。"""
        runtime = self.runtime(npc_id)
        current = NPCTier(str(runtime.get("tier", "background")))
        if tier is None:
            check = self.check_promotion(npc_id)
            if not check["promote"] and not force:
                return {"promoted": False, **check}
            target = NPCTier(check["target"])
        else:
            target = NPCTier(tier)
            if target.rank <= current.rank and not force:
                return {"promoted": False, "npc_id": npc_id, "tier": current.value, "reason": "不能降级"}

        definition = self.definition(npc_id) or {}
        if definition.get("source") == "static":
            # 静态定义写入动态层的覆盖记录
            definition = dict(definition)
            definition["tier"] = target.value
            self.state.registry.setdefault("npcs", {})[npc_id] = definition
            self.registry.invalidate()
        else:
            definition["tier"] = target.value
        runtime["tier"] = target.value

        if target.rank >= NPCTier.SUPPORTING.rank:
            attrs = definition.setdefault("attributes", {})
            for attribute in ATTRIBUTES:
                attrs.setdefault(attribute, self.rng.randint(3, 7, reason=f"promote_attr:{npc_id}"))
            definition.setdefault("interests", [])
            definition.setdefault("short_term_goals", [])
            definition.setdefault("schedule_overrides", [])
            runtime.setdefault("skills", dict(definition.get("skills") or {}))
            runtime.setdefault("knowledge", dict(definition.get("knowledge") or {}))
        if target is NPCTier.CORE:
            for field in ("values", "worries", "long_term_goals", "strengths", "weaknesses", "dislikes"):
                definition.setdefault(field, [])
            definition.setdefault("hidden_personality", "")
            definition.setdefault("family_background", "")
            definition.setdefault("secrets", [])

        log.info("promoted %s: %s -> %s", npc_id, current.value, target.value)
        return {
            "promoted": True,
            "npc_id": npc_id,
            "from": current.value,
            "to": target.value,
            "todo": (
                "该角色已升级，请在后续叙事中逐步补完其人格细节"
                "（隐藏性格、价值观、目标、担忧、秘密），并保持与既有表现一致。"
            ),
        }

    def check_all_promotions(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for npc_id in list(self.state.npcs.keys()):
            try:
                check = self.check_promotion(npc_id)
            except ValidationError:
                continue
            if check.get("promote"):
                results.append(self.promote_npc(npc_id))
        return results

    # ------------------------------------------------------------------
    # 兴趣的自然形成
    # ------------------------------------------------------------------
    def add_dynamic_interest(self, npc_id: str, interest: str, *, evidence: str = "") -> dict[str, Any]:
        """NPC 在长期经历后形成新兴趣。必须有事件依据。"""
        interest = sanitize_text(interest, max_len=40, field_name="兴趣")
        if not evidence:
            raise ValidationError("新兴趣必须提供 evidence（长期事件依据），不能凭空产生。")
        runtime = self.runtime(npc_id)
        interests = runtime.setdefault("dynamic_interests", [])
        if any(item.get("interest") == interest for item in interests):
            return {"npc_id": npc_id, "interest": interest, "added": False, "reason": "已经有了"}
        interests.append(
            {
                "interest": interest,
                "evidence": sanitize_text(evidence, max_len=300, field_name="依据"),
                "day": self.state.world.get("day_index", 0),
            }
        )
        return {"npc_id": npc_id, "interest": interest, "added": True}
