"""存档与状态管理：静态数据加载、初始化、原子写入、备份、读档、记忆库。

所有 JSON 写入都是**原子**的（临时文件 + os.replace），并保留备份。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .models import (
    ATTRIBUTES,
    GameState,
    Memory,
    MemoryVisibility,
    ValidationError,
    clamp_int,
    sanitize_text,
    validate_age,
)
from .rng import GameRNG, make_rng

log = logging.getLogger("engine.state")

ROOT = Path(__file__).resolve().parent.parent

STATIC_FILES: dict[str, str] = {
    "game": "config/game.yaml",
    "content_rules": "config/content_rules.yaml",
    "locations": "world/locations.yaml",
    "calendar": "world/calendar.yaml",
    "schedule": "world/schedule.yaml",
    "clubs": "world/clubs.yaml",
    "npcs": "characters/npcs.yaml",
    "player_template": "characters/player_template.yaml",
    "archetypes": "characters/archetypes.yaml",
    "attributes": "rules/attributes.yaml",
    "skill_registry": "rules/skill_registry.yaml",
    "knowledge_registry": "rules/knowledge_registry.yaml",
    "difficulty": "rules/difficulty.yaml",
    "event_pool": "events/event_pool.yaml",
}

STATE_FILES: dict[str, str] = {
    "world": "state/world_state.json",
    "characters": "state/character_state.json",
    "relationships": "state/relationships.json",
    "memories": "state/memories.json",
    "events": "state/event_state.json",
    "registry": "state/world_registry.json",
}

DOC_FILES: dict[str, str] = {
    "school": "world/school.md",
    "culture": "world/culture.md",
    "rules": "rules/rules.md",
    "agent": "AGENT.md",
}


def _load_yaml(path: Path) -> Any:
    import yaml  # PyYAML 是唯一的第三方依赖；缺失时给出清晰指引

    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def atomic_write_json(path: Path, data: Any, *, backups: int = 5) -> None:
    """原子写入 + 轮转备份。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and backups > 0:
        backup_dir = path.parent / ".backups"
        backup_dir.mkdir(exist_ok=True)
        shutil.copy2(path, backup_dir / f"{path.stem}.bak")
        existing = sorted(backup_dir.glob(f"{path.stem}.*.bak"))
        for index in range(min(len(existing), backups), 0, -1):
            src = backup_dir / f"{path.stem}.{index}.bak"
            if src.exists() and index < backups:
                src.replace(backup_dir / f"{path.stem}.{index + 1}.bak")
        (backup_dir / f"{path.stem}.bak").replace(backup_dir / f"{path.stem}.1.bak")

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def setup_logging(root: Path, config: dict[str, Any]) -> None:
    """按 config.logging 配置引擎日志（默认写入 state/engine.log，不打扰终端输出）。"""
    cfg = (config or {}).get("logging") or {}
    logger = logging.getLogger("engine")
    if getattr(logger, "_akizuki_configured", False):
        return
    logger.setLevel(getattr(logging, str(cfg.get("level", "INFO")).upper(), logging.INFO))
    target = cfg.get("file")
    if target:
        path = root / str(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    if cfg.get("console"):
        logger.addHandler(logging.StreamHandler())
    logger.propagate = False
    logger._akizuki_configured = True  # type: ignore[attr-defined]


class StateManager:
    """负责把磁盘上的东西变成 :class:`GameState`，以及反过来。"""

    def __init__(self, root: str | Path | None = None, data_root: str | Path | None = None) -> None:
        #: 静态世界资料（config/ world/ characters/ rules/ events/）的根目录
        self.root = Path(root) if root else ROOT
        #: 可变数据（state/ saves/）的根目录。多用户部署时每个世界一个独立目录。
        self.data_root = Path(data_root) if data_root else self.root
        self._static: dict[str, Any] | None = None

    def _display_path(self, path: Path) -> str:
        """尽量给出相对路径，跨根目录时退回绝对路径。"""
        for base in (self.data_root, self.root):
            try:
                return str(path.relative_to(base))
            except ValueError:
                continue
        return str(path)

    # ------------------------------------------------------------------
    # 静态数据
    # ------------------------------------------------------------------
    def load_static(self, *, reload: bool = False) -> dict[str, Any]:
        if self._static is not None and not reload:
            return self._static
        static: dict[str, Any] = {}
        for key, rel_path in STATIC_FILES.items():
            path = self.root / rel_path
            if not path.exists():
                raise FileNotFoundError(f"缺少必需的静态文件: {rel_path}")
            static[key] = _load_yaml(path)
        for key, rel_path in DOC_FILES.items():
            path = self.root / rel_path
            static[f"doc_{key}"] = path.read_text(encoding="utf-8") if path.exists() else ""
        self._static = static
        return static

    @property
    def config(self) -> dict[str, Any]:
        return self.load_static()["game"]

    # ------------------------------------------------------------------
    # 新游戏
    # ------------------------------------------------------------------
    def new_game(self, *, seed: int | None = None, player: dict[str, Any] | None = None) -> GameState:
        static = self.load_static()
        config = static["game"]
        world_cfg = config.get("world", {})

        state = GameState(config=config, static=static)
        state.world = {
            "date": str(world_cfg.get("start_date", "2025-04-16")),
            "time": str(world_cfg.get("start_time", "07:30")),
            "day_index": 0,
            "turn": 0,
            "weather": "sunny",
            "events_today": 0,
            "flags": {},
            "daily_flags": {},
            "recent_actions": [],
            "recent_recommendations": [],
            "background_log": [],
            "school_name": world_cfg.get("school_name", "秋月学院"),
            "rng": {},
        }
        state.characters = {"player": self._build_player(player), "npcs": {}}
        state.relationships = {}
        state.memories = {}
        state.events = {"cooldowns": {}, "history": [], "active": [], "counters": {}}
        state.registry = {"skills": {}, "knowledge": {}, "locations": {}, "groups": {}, "npcs": {}}

        self._bootstrap_npcs(state)
        rng = make_rng(config, seed)
        state.world["rng"] = rng.export_state()
        state.world["rng_seed"] = rng.seed
        return state

    def _build_player(self, overrides: dict[str, Any] | None) -> dict[str, Any]:
        template = dict((self.load_static()["player_template"] or {}).get("template", {}))
        player = json.loads(json.dumps(template))  # 深拷贝
        player.setdefault("id", "player")
        player.setdefault("skills", {})
        player.setdefault("knowledge", {})
        player.setdefault("skill_xp", {})
        player.setdefault("knowledge_xp", {})
        player.setdefault("clubs", [])
        player.setdefault("conditions", [])
        player["location"] = template.get("location") or "loc_school_gate"
        player["last_meal_minutes"] = -999
        if overrides:
            player.update(overrides)
        if player.get("age") is not None:
            validate_age(player["age"], context="玩家角色")
        return player

    def _bootstrap_npcs(self, state: GameState) -> None:
        """把 npcs.yaml 的定义与初始关系装入运行时状态。"""
        from .relationship_manager import RelationshipManager

        npc_data = state.static.get("npcs") or {}
        relationships = RelationshipManager(state)
        for definition in npc_data.get("npcs", []) or []:
            npc_id = definition["id"]
            validate_age(definition.get("age", 18), context=f"NPC {npc_id}")
            state.npcs[npc_id] = {
                "id": npc_id,
                "tier": definition.get("tier", "background"),
                "status": {
                    "health": 100,
                    "energy": 100,
                    "stress": 15,
                    "mood": "normal",
                    "money": 5000,
                },
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
        for entry in npc_data.get("initial_relationships", []) or []:
            values = {
                dim: int(entry.get(dim, 0))
                for dim in (
                    "familiarity", "trust", "closeness", "attraction",
                    "romantic_interest", "comfort", "conflict",
                )
            }
            relationships.set_values(
                entry["a"], entry["b"], values, stage=entry.get("stage"), notes=entry.get("note", "")
            )
        # 玩家与所有初始 NPC 的关系从"同班同学"级别开始 —— 刻意保持很低
        for definition in npc_data.get("npcs", []) or []:
            npc_id = definition["id"]
            same_class = definition.get("class") == "class_2a"
            base = (
                {"familiarity": 18, "trust": 8, "closeness": 6, "comfort": 12}
                if same_class
                else {"familiarity": 4, "trust": 2, "closeness": 2, "comfort": 4}
            )
            relationships.set_values("player", npc_id, base)
            relationships.set_values(npc_id, "player", base)
            relationships.refresh_stage("player", npc_id)
            relationships.refresh_stage(npc_id, "player")

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def state_path(self, key: str) -> Path:
        return self.data_root / STATE_FILES[key]

    def exists(self) -> bool:
        path = self.state_path("world")
        if not path.exists():
            return False
        try:
            return bool(json.loads(path.read_text(encoding="utf-8")).get("date"))
        except (json.JSONDecodeError, OSError):
            return False

    def load(self) -> GameState:
        """从 ``state/*.json`` 载入；文件缺失或为空时自动开新游戏。"""
        if not self.exists():
            state = self.new_game()
            self.save(state)
            return state
        static = self.load_static()
        state = GameState(config=static["game"], static=static)
        for key in STATE_FILES:
            path = self.state_path(key)
            try:
                data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except json.JSONDecodeError as exc:
                raise ValidationError(f"存档文件损坏: {path.name} ({exc})") from exc
            setattr(state, key, data or {})
        state.characters.setdefault("player", {})
        state.characters.setdefault("npcs", {})
        state.registry.setdefault("npcs", {})
        return state

    def save(self, state: GameState, *, rng: GameRNG | None = None) -> dict[str, str]:
        if rng is not None:
            state.world["rng"] = rng.export_state()
            state.world["rng_seed"] = rng.seed
        backups = int((state.config.get("save") or {}).get("keep_backups", 5))
        written: dict[str, str] = {}
        for key in STATE_FILES:
            path = self.state_path(key)
            atomic_write_json(path, getattr(state, key), backups=backups)
            written[key] = self._display_path(path)
        return written

    # ------------------------------------------------------------------
    # 存档槽
    # ------------------------------------------------------------------
    def save_dir(self) -> Path:
        path = self.data_root / "saves"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_game(self, state: GameState, slot: str = "save_001", *, rng: GameRNG | None = None) -> str:
        if rng is not None:
            state.world["rng"] = rng.export_state()
            state.world["rng_seed"] = rng.seed
        snapshot = {
            "meta": {
                "slot": slot,
                "version": state.config.get("game", {}).get("version", "1.0.0"),
                "date": state.world.get("date"),
                "time": state.world.get("time"),
                "turn": state.world.get("turn"),
                "day_index": state.world.get("day_index"),
                "player_name": state.player.get("name"),
                "npc_count": len(state.npcs),
                "relationship_count": len(state.relationships),
            },
            **state.to_dict(),
        }
        path = self.save_dir() / f"{slot}.json"
        atomic_write_json(path, snapshot, backups=int((state.config.get("save") or {}).get("keep_backups", 5)))
        self.save(state)
        return self._display_path(path)

    def load_game(self, slot: str = "save_001") -> GameState:
        path = self.save_dir() / f"{slot}.json"
        if not path.exists():
            raise ValidationError(f"存档不存在: {slot}")
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"存档损坏: {slot} ({exc})") from exc
        static = self.load_static()
        state = GameState(config=static["game"], static=static)
        for key in STATE_FILES:
            setattr(state, key, snapshot.get(key) or {})
        state.characters.setdefault("player", {})
        state.characters.setdefault("npcs", {})
        return state

    def list_saves(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.save_dir().glob("*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8")).get("meta", {})
            except (json.JSONDecodeError, OSError):
                meta = {"error": "损坏"}
            out.append({"slot": path.stem, "path": self._display_path(path), **meta})
        return out


# ----------------------------------------------------------------------
# 记忆库
# ----------------------------------------------------------------------


class MemoryStore:
    """记忆必须区分 **事实 / 主观解释 / 情绪** —— 这三者不是一回事。"""

    def __init__(self, state: GameState) -> None:
        self.state = state

    def _bucket(self, owner: str) -> list[dict[str, Any]]:
        return self.state.memories.setdefault(owner, [])

    def add(
        self,
        owner: str,
        *,
        fact: str,
        interpretation: str = "",
        emotion: str = "",
        intensity: int = 3,
        visibility: str = MemoryVisibility.PRIVATE_FACT.value,
        participants: Iterable[str] | None = None,
        tags: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        fact = sanitize_text(fact, max_len=600, field_name="记忆事实")
        if not fact:
            raise ValidationError("记忆必须包含 fact（发生了什么）")
        if visibility not in {v.value for v in MemoryVisibility}:
            raise ValidationError(
                f"visibility 必须是 {', '.join(v.value for v in MemoryVisibility)} 之一"
            )
        bucket = self._bucket(owner)
        memory = Memory(
            id=f"mem_{owner}_{len(bucket) + 1}_{self.state.world.get('day_index', 0)}",
            owner=owner,
            fact=fact,
            interpretation=sanitize_text(interpretation, max_len=600, field_name="主观解释"),
            emotion=sanitize_text(emotion, max_len=200, field_name="情绪"),
            intensity=clamp_int(intensity, 1, 10),
            visibility=visibility,
            participants=[str(p) for p in (participants or [])],
            tags=[str(t) for t in (tags or [])],
            day=int(self.state.world.get("day_index", 0)),
            date=str(self.state.world.get("date", "")),
            time=str(self.state.world.get("time", "")),
            turn=int(self.state.world.get("turn", 0)),
        )
        bucket.append(memory.to_dict())
        if len(bucket) > 400:
            bucket.sort(key=lambda m: (m.get("intensity", 0), m.get("day", 0)))
            del bucket[: len(bucket) - 400]
        return memory.to_dict()

    def relevant(
        self,
        owner: str,
        *,
        context: str = "",
        participants: Iterable[str] | None = None,
        tags: Iterable[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """按参与者 / 标签 / 关键词 / 强度 / 时近性打分，返回最相关的记忆。"""
        participants = set(participants or [])
        tags = set(tags or [])
        today = int(self.state.world.get("day_index", 0))
        keywords = [w for w in str(context).split() if len(w) >= 2]
        scored: list[tuple[float, dict[str, Any]]] = []
        for memory in self._bucket(owner):
            score = float(memory.get("intensity", 3))
            if participants and participants & set(memory.get("participants") or []):
                score += 6
            if tags and tags & set(memory.get("tags") or []):
                score += 4
            text = f"{memory.get('fact', '')} {memory.get('interpretation', '')}"
            for word in keywords:
                if word in text:
                    score += 2
            if context and context in text:
                score += 3
            age = max(0, today - int(memory.get("day", 0)))
            score -= min(5.0, age * 0.05)
            scored.append((score, memory))
        scored.sort(key=lambda item: -item[0])
        return [memory for _score, memory in scored[: max(1, limit)]]

    def all_for(self, owner: str) -> list[dict[str, Any]]:
        return list(self._bucket(owner))
