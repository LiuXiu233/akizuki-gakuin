"""事件系统：候选筛选、冷却、权重、随机触发。

**随机事件必须由代码决定。** LLM 只负责把代码给出的事件写成故事。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .models import GameState, RelationshipStage, ValidationError
from .npc_manager import NPCManager
from .registry_manager import RegistryManager
from .relationship_manager import RelationshipManager
from .rng import GameRNG
from .time_manager import TimeManager

log = logging.getLogger("engine.event")

STAGE_ORDER = [
    RelationshipStage.STRANGER.value,
    RelationshipStage.ACQUAINTANCE.value,
    RelationshipStage.FRIEND.value,
    RelationshipStage.CLOSE_FRIEND.value,
    RelationshipStage.AMBIGUOUS.value,
    RelationshipStage.DATING.value,
    RelationshipStage.RELATIONSHIP.value,
]


class EventManager:
    def __init__(
        self,
        state: GameState,
        rng: GameRNG,
        time_manager: TimeManager,
        npcs: NPCManager,
        relationships: RelationshipManager,
        registry: RegistryManager,
    ) -> None:
        self.state = state
        self.rng = rng
        self.time = time_manager
        self.npcs = npcs
        self.relationships = relationships
        self.registry = registry

    # ------------------------------------------------------------------
    @property
    def pool(self) -> list[dict[str, Any]]:
        return (self.state.static.get("event_pool") or {}).get("events", []) or []

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        for event in self.pool:
            if event["id"] == event_id:
                return event
        return None

    def _event_state(self) -> dict[str, Any]:
        events = self.state.events
        events.setdefault("cooldowns", {})
        events.setdefault("history", [])
        events.setdefault("active", [])
        events.setdefault("counters", {})
        return events

    # ------------------------------------------------------------------
    # 条件判定
    # ------------------------------------------------------------------
    def _matches(self, event: dict[str, Any], context: dict[str, Any]) -> tuple[bool, str]:
        cond = event.get("conditions") or {}
        location = context.get("location")
        loc_def = self.registry.get("location", location) or {}
        loc_tags = set(loc_def.get("tags") or [])
        block = context.get("block_id")
        weekday = context.get("weekday")
        weather = context.get("weather")
        month = context.get("month")
        calendar_tags = set(context.get("calendar_tags") or [])
        nearby: list[str] = list(context.get("nearby") or [])
        player = self.state.player
        status = player.get("status") or {}

        if "time_blocks" in cond and block not in cond["time_blocks"]:
            return False, "time_block"
        if "not_time_blocks" in cond and block in cond["not_time_blocks"]:
            return False, "not_time_block"
        if "locations" in cond and location not in cond["locations"]:
            return False, "location"
        if "not_locations" in cond and location in cond["not_locations"]:
            return False, "not_location"
        if "location_tags" in cond and not (loc_tags & set(cond["location_tags"])):
            return False, "location_tags"
        if "weekday" in cond and weekday not in cond["weekday"]:
            return False, "weekday"
        if "day_type" in cond and context.get("day_type") not in cond["day_type"]:
            return False, "day_type"
        if "weather" in cond and weather not in cond["weather"]:
            return False, "weather"
        if "season_months" in cond and month not in cond["season_months"]:
            return False, "season"
        if "calendar_tags" in cond and not (calendar_tags & set(cond["calendar_tags"])):
            return False, "calendar_tags"
        if "min_day_index" in cond and int(self.state.world.get("day_index", 0)) < int(cond["min_day_index"]):
            return False, "too_early"
        if "player_energy_min" in cond and int(status.get("energy", 100)) < int(cond["player_energy_min"]):
            return False, "energy_min"
        if "player_energy_max" in cond and int(status.get("energy", 100)) > int(cond["player_energy_max"]):
            return False, "energy_max"
        if "player_stress_min" in cond and int(status.get("stress", 0)) < int(cond["player_stress_min"]):
            return False, "stress_min"
        if "player_stress_max" in cond and int(status.get("stress", 0)) > int(cond["player_stress_max"]):
            return False, "stress_max"
        if "requires_flag" in cond and not (self.state.world.get("flags") or {}).get(cond["requires_flag"]):
            return False, "flag"
        if "requires_skill" in cond:
            if int((player.get("skills") or {}).get(cond["requires_skill"], 0)) <= 0:
                return False, "skill"
        if "requires_knowledge" in cond:
            if int((player.get("knowledge") or {}).get(cond["requires_knowledge"], 0)) <= 0:
                return False, "knowledge"
        if "requires_club" in cond:
            clubs = player.get("clubs") or []
            if cond["requires_club"] == "any":
                if not clubs:
                    return False, "no_club"
            elif cond["requires_club"] not in clubs:
                return False, "club"

        if cond.get("requires_npc"):
            if not nearby:
                return False, "no_npc"
            candidates = self._eligible_npcs(cond, nearby)
            if not candidates:
                return False, "no_matching_npc"
        return True, "ok"

    def _eligible_npcs(self, cond: dict[str, Any], nearby: Iterable[str]) -> list[str]:
        out: list[str] = []
        for npc_id in nearby:
            rel = self.relationships.get("player", npc_id, create=False)
            values = rel.values.to_dict() if rel else {d: 0 for d in
                                                       ("familiarity", "trust", "closeness", "attraction",
                                                        "romantic_interest", "comfort", "conflict")}
            stage = rel.stage if rel else RelationshipStage.STRANGER.value

            ok = True
            for dim, need in (cond.get("min_relationship") or {}).items():
                if values.get(dim, 0) < int(need):
                    ok = False
                    break
            if ok:
                for dim, cap in (cond.get("max_relationship") or {}).items():
                    if values.get(dim, 0) > int(cap):
                        ok = False
                        break
            if ok and "npc_stage_in" in cond and stage not in cond["npc_stage_in"]:
                ok = False
            if ok and "npc_stage_min" in cond:
                try:
                    if STAGE_ORDER.index(stage) < STAGE_ORDER.index(cond["npc_stage_min"]):
                        ok = False
                except ValueError:
                    ok = False
            if ok and "npc_tags" in cond:
                definition = self.npcs.definition(npc_id) or {}
                tags = set()
                if definition.get("class", "").startswith("class_3") if isinstance(definition.get("class"), str) else False:
                    tags.add("senior")
                if definition.get("role") == "teacher":
                    tags.add("teacher")
                if not (tags & set(cond["npc_tags"])):
                    ok = False
            if ok:
                out.append(npc_id)
        return out

    # ------------------------------------------------------------------
    def build_context(self) -> dict[str, Any]:
        block = self.time.current_block() or {}
        return {
            "location": self.state.player.get("location"),
            "block_id": block.get("id"),
            "weekday": self.time.weekday,
            "day_type": self.time.day_type(),
            "weather": self.state.world.get("weather"),
            "month": self.time.date.month,
            "calendar_tags": [t for e in self.time.calendar_events_today() for t in (e.get("tags") or [])],
            "nearby": [n["id"] for n in self.npcs.nearby()],
            "day_index": self.time.day_index,
        }

    def candidates(self, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """返回当前所有满足条件、且不在冷却中的事件（含权重）。"""
        context = context or self.build_context()
        state = self._event_state()
        day = int(self.state.world.get("day_index", 0))
        default_cd = int((self.state.config.get("events") or {}).get("default_cooldown_days", 5))
        major_gap = int((self.state.config.get("events") or {}).get("major_event_min_interval_days", 6))
        last_major = int(state["counters"].get("last_major_day", -999))
        recent_ids = [h["event_id"] for h in state["history"][-8:]]

        out: list[dict[str, Any]] = []
        for event in self.pool:
            event_id = event["id"]
            cooldown = int(event.get("cooldown_days", default_cd))
            last = state["cooldowns"].get(event_id)
            if last is not None and day - int(last) < cooldown:
                continue
            if event.get("major") and day - last_major < major_gap:
                continue
            ok, _reason = self._matches(event, context)
            if not ok:
                continue
            weight = float(event.get("weight", 1.0))
            if event_id in recent_ids:
                weight *= 0.3
            floor = float((self.state.config.get("events") or {}).get("weight_floor", 0.05))
            weight = max(floor, weight)
            candidate = dict(event)
            candidate["_weight"] = weight
            cond = event.get("conditions") or {}
            if cond.get("requires_npc"):
                candidate["_eligible_npcs"] = self._eligible_npcs(cond, context.get("nearby") or [])
            out.append(candidate)
        return out

    def roll_random_event(self, *, force: bool = False, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """真正的随机事件掷骰。返回事件（含建议的 NPC）或 None。"""
        cfg = self.state.config.get("events") or {}
        state = self._event_state()
        today_count = int(self.state.world.get("events_today", 0))
        if not force and today_count >= int(cfg.get("max_events_per_day", 4)):
            return None
        base_chance = float(cfg.get("random_event_base_chance", 0.28))
        if not force and not self.rng.chance(base_chance, reason="random_event_gate"):
            return None

        context = context or self.build_context()
        pool = self.candidates(context)
        if not pool:
            return None
        weights = {event["id"]: event["_weight"] for event in pool}
        picked_id = self.rng.weighted_choice(weights, reason="random_event_pick")
        if picked_id is None:
            return None
        picked = next(event for event in pool if event["id"] == picked_id)

        npc_id = None
        eligible = picked.get("_eligible_npcs") or []
        if eligible:
            npc_id = self.rng.choice(eligible, reason=f"event_npc:{picked_id}")

        self.mark_fired(picked_id, major=bool(picked.get("major")))
        result = {
            "event_id": picked_id,
            "name": picked.get("name"),
            "category": picked.get("category"),
            "tags": picked.get("tags", []),
            "prompt": picked.get("prompt"),
            "suggested_relationship_events": picked.get("suggested_relationship_events", []),
            "npc_id": npc_id,
            "npc_name": (self.npcs.definition(npc_id) or {}).get("name") if npc_id else None,
            "location": context.get("location"),
            "major": bool(picked.get("major")),
            "note": "这是引擎决定的事件契机，不是结局。结果取决于玩家的选择和 NPC 的自主决定。",
        }
        log.info("event fired: %s (npc=%s)", picked_id, npc_id)
        return result

    def mark_fired(self, event_id: str, *, major: bool = False) -> None:
        state = self._event_state()
        day = int(self.state.world.get("day_index", 0))
        state["cooldowns"][event_id] = day
        state["history"].append(
            {
                "event_id": event_id,
                "day": day,
                "date": self.state.world.get("date"),
                "time": self.state.world.get("time"),
                "turn": self.state.world.get("turn"),
            }
        )
        state["history"] = state["history"][-200:]
        if major:
            state["counters"]["last_major_day"] = day
        self.state.world["events_today"] = int(self.state.world.get("events_today", 0)) + 1

    def trigger(self, event_id: str, *, npc_id: str | None = None) -> dict[str, Any]:
        """强制触发某个事件（用于日历事件与剧情推进）。"""
        event = self.get_event(event_id)
        if event is None:
            raise ValidationError(f"未知事件: {event_id}")
        self.mark_fired(event_id, major=bool(event.get("major")))
        return {
            "event_id": event_id,
            "name": event.get("name"),
            "category": event.get("category"),
            "prompt": event.get("prompt"),
            "npc_id": npc_id,
            "forced": True,
        }

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        return self._event_state()["history"][-n:]

    def active_calendar(self) -> list[dict[str, Any]]:
        return self.time.calendar_events_today()
