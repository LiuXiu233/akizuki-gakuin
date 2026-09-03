"""动态注册系统：技能 / 知识 / 地点 / 组织 / NPC 定义。

世界是开放的：LLM 可以创造新的技能、知识、地点、组织。
但是——**必须先查重，再通过本模块注册**，否则不进入世界。

静态定义来自 ``rules/*.yaml`` 与 ``world/*.yaml``；
动态定义写入 ``state/world_registry.json``，两者在读取时合并（动态优先）。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .models import (
    GameState,
    ValidationError,
    normalize_name,
    sanitize_text,
    validate_id,
)

log = logging.getLogger("engine.registry")

KINDS: tuple[str, ...] = ("skill", "knowledge", "location", "group", "npc")

_PLURAL = {
    "skill": "skills",
    "knowledge": "knowledge",
    "location": "locations",
    "group": "groups",
    "npc": "npcs",
}

VALID_SKILL_CATEGORIES = ("physical", "social", "academic", "practical", "art", "leisure", "other")
VALID_KNOWLEDGE_CATEGORIES = ("academic", "hobby", "local", "professional", "other")


class RegistryManager:
    """合并静态 + 动态注册表，并负责查重与写入。"""

    def __init__(self, state: GameState) -> None:
        self.state = state
        self._cache: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def invalidate(self) -> None:
        self._cache = None

    def _static(self, kind: str) -> dict[str, Any]:
        static = self.state.static
        out: dict[str, Any] = {}
        if kind == "skill":
            for entry in (static.get("skill_registry") or {}).get("skills", []) or []:
                out[entry["id"]] = dict(entry, source="static")
        elif kind == "knowledge":
            for entry in (static.get("knowledge_registry") or {}).get("knowledge", []) or []:
                out[entry["id"]] = dict(entry, source="static")
        elif kind == "location":
            for entry in (static.get("locations") or {}).get("locations", []) or []:
                out[entry["id"]] = dict(entry, source="static")
        elif kind == "group":
            for entry in (static.get("clubs") or {}).get("clubs", []) or []:
                out[entry["id"]] = dict(entry, type="club", source="static")
        elif kind == "npc":
            for entry in (static.get("npcs") or {}).get("npcs", []) or []:
                out[entry["id"]] = dict(entry, source="static")
        return out

    def _dynamic(self, kind: str) -> dict[str, Any]:
        bucket = self.state.registry.setdefault(_PLURAL[kind], {})
        if not isinstance(bucket, dict):  # 存档损坏保护
            bucket = {}
            self.state.registry[_PLURAL[kind]] = bucket
        return bucket

    def all(self, kind: str) -> dict[str, Any]:
        """静态 + 动态合并结果。"""
        if kind not in KINDS:
            raise ValidationError(f"未知注册表类型: {kind}（可用: {', '.join(KINDS)}）")
        if self._cache is None:
            self._cache = {}
        if kind not in self._cache:
            merged = self._static(kind)
            merged.update(self._dynamic(kind))
            self._cache[kind] = merged
        return self._cache[kind]

    def get(self, kind: str, entry_id: str) -> dict[str, Any] | None:
        return self.all(kind).get(entry_id)

    def exists(self, kind: str, entry_id: str) -> bool:
        return entry_id in self.all(kind)

    def ids(self, kind: str) -> list[str]:
        return sorted(self.all(kind).keys())

    # ------------------------------------------------------------------
    # 查重
    # ------------------------------------------------------------------
    def _tokens(self, entry: dict[str, Any]) -> set[str]:
        tokens = {normalize_name(str(entry.get("id", "")))}
        tokens.add(normalize_name(str(entry.get("name", ""))))
        for alias in entry.get("aliases") or []:
            tokens.add(normalize_name(str(alias)))
        tokens.discard("")
        return tokens

    def find_duplicate(
        self,
        kind: str,
        *,
        entry_id: str | None = None,
        name: str | None = None,
        aliases: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        """创建前必须调用。返回 ``{"id":..., "name":..., "matched_on":...}`` 或 None。"""
        candidate = {
            "id": entry_id or "",
            "name": name or "",
            "aliases": list(aliases or []),
        }
        cand_tokens = self._tokens(candidate)
        if not cand_tokens:
            return None
        for existing_id, entry in self.all(kind).items():
            existing_tokens = self._tokens(entry)
            overlap = cand_tokens & existing_tokens
            if overlap:
                return {
                    "id": existing_id,
                    "name": entry.get("name", existing_id),
                    "matched_on": sorted(overlap)[0],
                    "reason": "exact",
                }
            # 包含关系（"摄影技巧" vs "摄影"），只在长度足够时判定，避免误伤
            for token in cand_tokens:
                if len(token) < 2:
                    continue
                for other in existing_tokens:
                    if len(other) < 2:
                        continue
                    if (token in other or other in token) and abs(len(token) - len(other)) <= 4:
                        return {
                            "id": existing_id,
                            "name": entry.get("name", existing_id),
                            "matched_on": other,
                            "reason": "substring",
                        }
        return None

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def _register(self, kind: str, entry: dict[str, Any], *, allow_duplicate: bool = False) -> dict[str, Any]:
        entry_id = validate_id(str(entry["id"]), f"{kind}_id")
        entry["id"] = entry_id
        if self.exists(kind, entry_id):
            raise ValidationError(f"{kind} '{entry_id}' 已存在，不要重复创建。")
        if not allow_duplicate:
            dup = self.find_duplicate(
                kind, entry_id=entry_id, name=entry.get("name"), aliases=entry.get("aliases")
            )
            if dup:
                raise ValidationError(
                    f"拒绝创建 {kind} '{entry_id}'：与已有的 '{dup['id']}'（{dup['name']}）重复"
                    f"，匹配于 '{dup['matched_on']}'。请直接使用已有条目。"
                )
        entry["source"] = "dynamic"
        entry.setdefault("created_turn", self.state.world.get("turn", 0))
        entry.setdefault("created_date", self.state.world.get("date", ""))
        self._dynamic(kind)[entry_id] = entry
        self.invalidate()
        log.info("registered %s: %s", kind, entry_id)
        return entry

    def register_skill(
        self,
        skill_id: str,
        name: str,
        *,
        category: str = "other",
        attribute: str = "agility",
        description: str = "",
        aliases: Iterable[str] | None = None,
        specializations: Iterable[str] | None = None,
        reason: str = "",
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """注册新技能。

        **粒度铁律**：只有拥有独立成长路线、独立应用场景、足够使用频率的能力
        才能成为技能。煎鸡蛋 / 炒饭 / 咖喱 一律归入 ``cooking``。
        """
        from .models import ATTRIBUTES

        if attribute not in ATTRIBUTES:
            raise ValidationError(f"技能的主属性必须是 7 项属性之一，收到 {attribute!r}")
        if category not in VALID_SKILL_CATEGORIES:
            category = "other"
        entry = {
            "id": skill_id,
            "name": sanitize_text(name, max_len=40, field_name="技能名"),
            "category": category,
            "attribute": attribute,
            "description": sanitize_text(description, max_len=500, field_name="技能描述"),
            "aliases": [sanitize_text(a, max_len=40, field_name="别名") for a in (aliases or [])],
            "specializations": list(specializations or []),
            "created_reason": sanitize_text(reason, max_len=300, field_name="创建理由"),
        }
        if not entry["name"]:
            raise ValidationError("技能必须有名字")
        return self._register("skill", entry, allow_duplicate=allow_duplicate)

    def register_knowledge(
        self,
        knowledge_id: str,
        name: str,
        *,
        category: str = "other",
        description: str = "",
        aliases: Iterable[str] | None = None,
        unlocks: Iterable[str] | None = None,
        reason: str = "",
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """注册新知识领域（知道 ≠ 会做）。"""
        if category not in VALID_KNOWLEDGE_CATEGORIES:
            category = "other"
        entry = {
            "id": knowledge_id,
            "name": sanitize_text(name, max_len=40, field_name="知识名"),
            "category": category,
            "description": sanitize_text(description, max_len=500, field_name="知识描述"),
            "aliases": [sanitize_text(a, max_len=40, field_name="别名") for a in (aliases or [])],
            "unlocks": [sanitize_text(u, max_len=120, field_name="解锁项") for u in (unlocks or [])],
            "created_reason": sanitize_text(reason, max_len=300, field_name="创建理由"),
        }
        if not entry["name"]:
            raise ValidationError("知识必须有名字")
        return self._register("knowledge", entry, allow_duplicate=allow_duplicate)

    def register_location(
        self,
        location_id: str,
        name: str,
        *,
        zone: str = "town_center",
        area: str = "town",
        open_hours: tuple[int, int] | list[int] | None = None,
        tags: Iterable[str] | None = None,
        description: str = "",
        actions: Iterable[str] | None = None,
        shop_items: Iterable[dict[str, Any]] | None = None,
        aliases: Iterable[str] | None = None,
        reason: str = "",
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """注册新地点。创建后永久进入玩家的世界。"""
        travel = (self.state.static.get("locations") or {}).get("travel", {})
        valid_zones = set((travel.get("within_zone_minutes") or {}).keys()) or {"town_center"}
        if zone not in valid_zones:
            raise ValidationError(f"未知 zone: {zone!r}（可用: {', '.join(sorted(valid_zones))}）")
        hours = list(open_hours or [0, 24])
        if len(hours) != 2 or not (0 <= int(hours[0]) <= 26 and 0 <= int(hours[1]) <= 26):
            raise ValidationError("open_hours 必须是 [开门小时, 关门小时]，范围 0~26")
        entry = {
            "id": location_id,
            "name": sanitize_text(name, max_len=60, field_name="地点名"),
            "zone": zone,
            "area": area if area in ("school", "town") else "town",
            "open_hours": [int(hours[0]), int(hours[1])],
            "tags": [sanitize_text(t, max_len=30, field_name="标签") for t in (tags or [])],
            "description": sanitize_text(description, max_len=800, field_name="地点描述"),
            "actions": list(actions or []),
            "aliases": [sanitize_text(a, max_len=40, field_name="别名") for a in (aliases or [])],
            "created_reason": sanitize_text(reason, max_len=300, field_name="创建理由"),
        }
        if shop_items:
            items = []
            for item in shop_items:
                items.append(
                    {
                        "id": validate_id(str(item.get("id", "")), "item_id"),
                        "name": sanitize_text(item.get("name", ""), max_len=40, field_name="商品名"),
                        "price": max(0, int(item.get("price", 0))),
                    }
                )
            entry["shop"] = {"items": items}
        if not entry["name"]:
            raise ValidationError("地点必须有名字")
        return self._register("location", entry, allow_duplicate=allow_duplicate)

    def register_group(
        self,
        group_id: str,
        name: str,
        *,
        group_type: str = "informal",
        members: Iterable[str] | None = None,
        location: str | None = None,
        purpose: str = "",
        leader: str | None = None,
        aliases: Iterable[str] | None = None,
        temporary: bool = False,
        reason: str = "",
        allow_duplicate: bool = False,
    ) -> dict[str, Any]:
        """注册新组织：临时乐队、学习小组、文化祭委员会、兴趣小组……"""
        member_ids = [str(m) for m in (members or [])]
        for member in member_ids:
            if member != "player" and not self.exists("npc", member) and member not in self.state.npcs:
                raise ValidationError(f"组织成员 {member!r} 不存在，请先 create_npc()")
        if location and not self.exists("location", location):
            raise ValidationError(f"组织地点 {location!r} 不存在，请先 register_location()")
        entry = {
            "id": group_id,
            "name": sanitize_text(name, max_len=60, field_name="组织名"),
            "type": group_type,
            "members": member_ids,
            "leader": leader,
            "location": location,
            "purpose": sanitize_text(purpose, max_len=500, field_name="组织目的"),
            "aliases": [sanitize_text(a, max_len=40, field_name="别名") for a in (aliases or [])],
            "temporary": bool(temporary),
            "created_reason": sanitize_text(reason, max_len=300, field_name="创建理由"),
        }
        if not entry["name"]:
            raise ValidationError("组织必须有名字")
        return self._register("group", entry, allow_duplicate=allow_duplicate)

    def register_npc_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        """由 :mod:`engine.npc_manager` 调用；不要直接使用。"""
        return self._register("npc", definition, allow_duplicate=True)

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    def summary(self, kind: str | None = None, *, verbose: bool = False) -> dict[str, Any]:
        kinds = [kind] if kind else list(KINDS)
        out: dict[str, Any] = {}
        for k in kinds:
            entries = self.all(k)
            dynamic = [i for i, e in entries.items() if e.get("source") == "dynamic"]
            item: dict[str, Any] = {
                "count": len(entries),
                "dynamic_count": len(dynamic),
                "ids": sorted(entries.keys()),
                "dynamic_ids": sorted(dynamic),
            }
            if verbose:
                item["entries"] = {
                    i: {
                        "name": e.get("name", i),
                        "category": e.get("category"),
                        "aliases": e.get("aliases", []),
                        "source": e.get("source", "static"),
                    }
                    for i, e in entries.items()
                }
            out[k] = item
        return out


# ----------------------------------------------------------------------
# 便捷函数（供其它模块使用，避免循环依赖）
# ----------------------------------------------------------------------

def get_location(state: GameState, location_id: str) -> dict[str, Any] | None:
    return RegistryManager(state).get("location", location_id)


def location_zone(state: GameState, location_id: str) -> str:
    loc = get_location(state, location_id)
    return str((loc or {}).get("zone", "town_center"))
