"""后台世界模拟。

**世界不会冻结等待玩家。** 玩家不在场时，NPC 仍在上课、练习、
吵架、和好、开始或结束一段关系。

玩家不一定立刻知道这些事——必须通过合理渠道（看到 / 听说 / 被告知 / 传闻）得知。
模拟结果写入 ``world_state.background_log``，每条带 ``visibility``。
"""

from __future__ import annotations

import logging
from typing import Any

from .models import GameState, NPCTier, Relationship, RelationshipStage
from .npc_manager import NPCManager
from .registry_manager import RegistryManager
from .relationship_manager import RelationshipManager
from .rng import GameRNG
from .time_manager import TimeManager

log = logging.getLogger("engine.world")

CASUAL_EVENTS = ["small_talk", "conversation", "club_activity", "shared_meal", "teasing", "walk_home"]
POSITIVE_EVENTS = ["helped_them", "shared_interest", "deep_talk", "study_together"]
NEGATIVE_EVENTS = ["misunderstanding", "argument", "ignored_them"]


class WorldSimulator:
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
    def _log(self, entry: dict[str, Any]) -> None:
        entry.setdefault("day", self.state.world.get("day_index", 0))
        entry.setdefault("date", self.state.world.get("date"))
        entry.setdefault("time", self.state.world.get("time"))
        entry.setdefault("visibility", "private_fact")
        entry.setdefault("known_by_player", False)
        log_list = self.state.world.setdefault("background_log", [])
        log_list.append(entry)
        if len(log_list) > 300:
            del log_list[: len(log_list) - 300]

    def _simulated_ids(self) -> list[str]:
        cfg = self.state.config.get("simulation") or {}
        limit = int((self.state.config.get("npc") or {}).get("background_simulation_limit", 40))
        out: list[str] = []
        for npc_id in self.npcs.all_ids():
            tier = str(self.npcs.runtime(npc_id).get("tier", "background"))
            if tier == NPCTier.CORE.value and cfg.get("simulate_core", True):
                out.append(npc_id)
            elif tier == NPCTier.SUPPORTING.value and cfg.get("simulate_supporting", True):
                out.append(npc_id)
            elif tier == NPCTier.BACKGROUND.value and cfg.get("simulate_background", False):
                out.append(npc_id)
        return out[:limit]

    # ------------------------------------------------------------------
    def simulate(self, *, minutes: int = 60) -> dict[str, Any]:
        """推进后台世界。返回本次发生的（对玩家隐藏的）变化摘要。"""
        cfg = self.state.config.get("simulation") or {}
        npc_ids = self._simulated_ids()
        results: dict[str, Any] = {
            "simulated_npcs": len(npc_ids),
            "interactions": [],
            "romance_events": [],
            "conflicts": [],
            "schedule_deviations": [],
            "decay": [],
        }

        # 1) 日程与状态
        for npc_id in npc_ids:
            runtime = self.npcs.runtime(npc_id)
            schedule = self.npcs.get_schedule(npc_id)
            runtime["current_activity"] = schedule.get("activity")
            status = runtime.setdefault("status", {})
            drain = 2 * (minutes / 60.0)
            status["energy"] = max(0, min(100, int(status.get("energy", 100) - drain)))
            if self.rng.chance(float(cfg.get("schedule_deviation_chance", 0.18)) * (minutes / 60.0), reason=f"deviation:{npc_id}"):
                deviation = self._deviate(npc_id)
                if deviation:
                    results["schedule_deviations"].append(deviation)

        # 2) NPC ↔ NPC 互动
        by_location: dict[str, list[str]] = {}
        for npc_id in npc_ids:
            location = self.npcs.where_is(npc_id)
            if location:
                by_location.setdefault(location, []).append(npc_id)

        chance = float(cfg.get("npc_npc_interaction_chance", 0.35)) * (minutes / 60.0)
        for location, group in by_location.items():
            if len(group) < 2:
                continue
            for _ in range(min(3, len(group) // 2)):
                if not self.rng.chance(chance, reason="npc_npc_interaction"):
                    continue
                pair = self.rng.sample(group, 2, reason="npc_pair")
                if len(pair) < 2:
                    continue
                a, b = pair
                interaction = self._interact(a, b, location)
                if interaction:
                    results["interactions"].append(interaction)

        # 3) 恋爱推进（NPC ↔ NPC）
        if cfg.get("npc_npc_romance_chance", 0.06):
            for entry in self._advance_npc_romance(float(cfg["npc_npc_romance_chance"]) * (minutes / 60.0)):
                results["romance_events"].append(entry)

        # 4) 自然衰减（每天检查一次）
        day = int(self.state.world.get("day_index", 0))
        if self.state.world.get("last_decay_day") != day:
            self.state.world["last_decay_day"] = day
            results["decay"] = self.relationships.apply_natural_decay(current_day=day)

        return results

    # ------------------------------------------------------------------
    def _deviate(self, npc_id: str) -> dict[str, Any] | None:
        """NPC 临时偏离日程——所以玩家去找人不一定找得到。"""
        definition = self.npcs.definition(npc_id) or {}
        options = [
            definition.get("favorite_place"),
            definition.get("home_location"),
            "loc_convenience_store",
            "loc_vending_area",
            "loc_courtyard",
            "loc_library",
        ]
        options = [o for o in options if o and self.registry.exists("location", o)]
        if not options:
            return None
        target = self.rng.choice(options, reason=f"deviate:{npc_id}")
        if target == self.npcs.where_is(npc_id):
            return None
        self.npcs.move_character(npc_id, target, duration_minutes=self.rng.randint(30, 90, reason="deviate_dur"))
        entry = {
            "type": "schedule_deviation",
            "npc_id": npc_id,
            "location": target,
            "visibility": "known_fact",
        }
        self._log(entry)
        return entry

    def _interact(self, a: str, b: str, location: str) -> dict[str, Any] | None:
        rel_ab = self.relationships.get(a, b)
        rel_ba = self.relationships.get(b, a)
        assert rel_ab and rel_ba

        if rel_ab.values.conflict >= 45 and self.rng.chance(0.4, reason="npc_conflict"):
            event_type = self.rng.choice(["argument", "apology", "reconciliation"], reason="conflict_kind")
        elif rel_ab.values.familiarity >= 45 and self.rng.chance(0.35, reason="npc_positive"):
            event_type = self.rng.choice(POSITIVE_EVENTS, reason="positive_kind")
        elif self.rng.chance(0.08, reason="npc_negative"):
            event_type = self.rng.choice(NEGATIVE_EVENTS, reason="negative_kind")
        else:
            event_type = self.rng.choice(CASUAL_EVENTS, reason="casual_kind")

        intensity = self.rng.uniform(0.5, 1.1, reason="npc_intensity")
        try:
            result = self.relationships.apply_event(a, b, event_type, intensity=intensity, bidirectional=True)
        except Exception as exc:  # pragma: no cover
            log.warning("npc interaction failed %s->%s: %s", a, b, exc)
            return None

        entry = {
            "type": "npc_interaction",
            "a": a,
            "b": b,
            "location": location,
            "event": event_type,
            "stage": result["stage_after"],
            "visibility": "known_fact" if event_type in CASUAL_EVENTS else "private_fact",
        }
        self._log(entry)
        return entry

    def _advance_npc_romance(self, chance: float) -> list[dict[str, Any]]:
        """NPC 之间也会开始或结束一段关系——和玩家无关。"""
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for key, raw in list(self.state.relationships.items()):
            rel = Relationship.from_dict(raw)
            if rel.source == "player" or rel.target == "player":
                continue
            pair = tuple(sorted((rel.source, rel.target)))
            if pair in seen:
                continue
            seen.add(pair)
            reverse = self.relationships.get(rel.target, rel.source, create=False)
            if reverse is None:
                continue

            mutual = min(rel.values.romantic_interest, reverse.values.romantic_interest)
            confirmed = rel.explicit_events.get("relationship_confirmed")

            if not confirmed and mutual >= 55 and rel.values.trust >= 45 and reverse.values.trust >= 45:
                if self.rng.chance(chance, reason="npc_romance_start"):
                    decision_a = self.relationships.npc_decide_confession(rel.source, actor_id=rel.target)
                    decision_b = self.relationships.npc_decide_confession(rel.target, actor_id=rel.source)
                    if decision_a["decision"] == "accept" and decision_b["decision"] != "decline":
                        self.relationships.apply_event(rel.source, rel.target, "confession_accepted", intensity=1.0)
                        self.relationships.apply_event(rel.target, rel.source, "confession_accepted", intensity=1.0)
                        entry = {
                            "type": "npc_romance_started",
                            "a": rel.source,
                            "b": rel.target,
                            "visibility": "private_fact",
                            "note": "两个人开始交往了。玩家只能通过合理渠道得知（看到、听说、被告知）。",
                        }
                        self._log(entry)
                        out.append(entry)
            elif confirmed and (rel.values.conflict >= 70 or reverse.values.conflict >= 70):
                if self.rng.chance(chance * 0.8, reason="npc_breakup"):
                    self.relationships.apply_event(rel.source, rel.target, "breakup", intensity=1.0)
                    self.relationships.apply_event(rel.target, rel.source, "breakup", intensity=1.0)
                    entry = {
                        "type": "npc_breakup",
                        "a": rel.source,
                        "b": rel.target,
                        "visibility": "private_fact",
                    }
                    self._log(entry)
                    out.append(entry)
            elif not confirmed and mutual >= 30 and self.rng.chance(chance * 0.5, reason="npc_romance_drift"):
                self.relationships.apply_event(rel.source, rel.target, "ambiguous_moment", intensity=0.6)
                out.append({"type": "npc_romance_drift", "a": rel.source, "b": rel.target, "visibility": "secret"})
        return out

    # ------------------------------------------------------------------
    def discoverable(self, *, location: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        """玩家**有可能**通过观察或传闻得知的后台事件。

        返回的条目仍需要 Agent 用合理渠道呈现（看到 / 听说 / 被告知）。
        """
        location = location or self.state.player.get("location")
        out: list[dict[str, Any]] = []
        for entry in reversed(self.state.world.get("background_log", [])):
            if entry.get("known_by_player"):
                continue
            if entry.get("visibility") == "secret":
                continue
            if entry.get("visibility") == "known_fact" and entry.get("location") not in (None, location):
                continue
            out.append(entry)
            if len(out) >= limit:
                break
        return out

    def mark_known(self, entry: dict[str, Any]) -> None:
        entry["known_by_player"] = True
