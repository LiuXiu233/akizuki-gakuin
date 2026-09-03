"""数据模型与枚举。

本模块只依赖标准库，保证在任何 Python 3.11+ 环境下可用。
所有数据结构都提供 ``to_dict`` / ``from_dict``，直接对应存档 JSON。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

MIN_AGE = 18
"""世界硬规则：任何角色的最低年龄。"""

ATTRIBUTES: tuple[str, ...] = (
    "physique",
    "agility",
    "intellect",
    "perception",
    "charm",
    "willpower",
    "luck",
)

ATTRIBUTE_NAMES_ZH = {
    "physique": "体魄",
    "agility": "灵巧",
    "intellect": "智力",
    "perception": "感知",
    "charm": "魅力",
    "willpower": "意志",
    "luck": "幸运",
}

RELATIONSHIP_DIMENSIONS: tuple[str, ...] = (
    "familiarity",
    "trust",
    "closeness",
    "attraction",
    "romantic_interest",
    "comfort",
    "conflict",
)

SKILL_LEVEL_NAMES = {0: "不会", 1: "初学", 2: "熟练", 3: "擅长", 4: "专家", 5: "大师"}
KNOWLEDGE_LEVEL_NAMES = {0: "不知道", 1: "基础", 2: "了解", 3: "熟悉", 4: "专业", 5: "深入研究"}

VALID_MOODS = (
    "normal", "sleepy", "tired", "energetic", "inspired", "nervous",
    "embarrassed", "confident", "stressed", "hungry", "focused",
    "sick", "excited", "relaxed",
)

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class GameError(Exception):
    """引擎级错误：非法输入、越权操作、数据不一致。"""


class ValidationError(GameError):
    """数据校验失败。"""


class PermissionDeniedError(GameError):
    """LLM 试图绕过规则直接修改状态。"""


# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------


class Difficulty(Enum):
    """固定 DC 表。"""

    VERY_EASY = 8
    EASY = 11
    NORMAL = 14
    HARD = 17
    VERY_HARD = 20
    EXTREME = 23

    @property
    def dc(self) -> int:
        return self.value

    @property
    def label(self) -> str:
        return {
            "VERY_EASY": "很容易",
            "EASY": "容易",
            "NORMAL": "普通",
            "HARD": "困难",
            "VERY_HARD": "很困难",
            "EXTREME": "极困难",
        }[self.name]

    @classmethod
    def parse(cls, value: "Difficulty | str | int | None") -> "Difficulty":
        if value is None:
            return cls.NORMAL
        if isinstance(value, Difficulty):
            return value
        if isinstance(value, int):
            for d in cls:
                if d.value == value:
                    return d
            raise ValidationError(f"未知 DC 值: {value}")
        key = str(value).strip().lower()
        aliases = {
            "very_easy": cls.VERY_EASY, "veryeasy": cls.VERY_EASY, "很容易": cls.VERY_EASY,
            "easy": cls.EASY, "容易": cls.EASY,
            "normal": cls.NORMAL, "medium": cls.NORMAL, "普通": cls.NORMAL,
            "hard": cls.HARD, "困难": cls.HARD,
            "very_hard": cls.VERY_HARD, "veryhard": cls.VERY_HARD, "很困难": cls.VERY_HARD,
            "extreme": cls.EXTREME, "极困难": cls.EXTREME,
        }
        if key in aliases:
            return aliases[key]
        raise ValidationError(f"未知难度: {value!r}")


class CheckResult(str, Enum):
    """检定结果等级。"""

    MAJOR_FAILURE = "major_failure"
    FAILURE = "failure"
    SUCCESS = "success"
    STRONG_SUCCESS = "strong_success"

    @property
    def label(self) -> str:
        return {
            "major_failure": "严重失败",
            "failure": "失败",
            "success": "成功",
            "strong_success": "强成功",
        }[self.value]

    @property
    def is_success(self) -> bool:
        return self in (CheckResult.SUCCESS, CheckResult.STRONG_SUCCESS)


RESULT_ORDER: tuple[CheckResult, ...] = (
    CheckResult.MAJOR_FAILURE,
    CheckResult.FAILURE,
    CheckResult.SUCCESS,
    CheckResult.STRONG_SUCCESS,
)


class NPCTier(str, Enum):
    BACKGROUND = "background"
    SUPPORTING = "supporting"
    CORE = "core"

    @property
    def rank(self) -> int:
        return {"background": 0, "supporting": 1, "core": 2}[self.value]


class RelationshipStage(str, Enum):
    STRANGER = "stranger"
    ACQUAINTANCE = "acquaintance"
    FRIEND = "friend"
    CLOSE_FRIEND = "close_friend"
    AMBIGUOUS = "ambiguous"
    DATING = "dating"
    RELATIONSHIP = "relationship"
    STRAINED = "strained"
    FORMER_PARTNER = "former_partner"

    @property
    def label(self) -> str:
        return {
            "stranger": "还不认识",
            "acquaintance": "认识",
            "friend": "关系不错",
            "close_friend": "关系亲近",
            "ambiguous": "似乎有些暧昧",
            "dating": "正在约会阶段",
            "relationship": "正在交往",
            "strained": "关系紧张",
            "former_partner": "曾经的恋人",
        }[self.value]


class MemoryVisibility(str, Enum):
    GLOBAL_FACT = "global_fact"
    KNOWN_FACT = "known_fact"
    RUMOR = "rumor"
    PRIVATE_FACT = "private_fact"
    SECRET = "secret"


# --------------------------------------------------------------------------
# 数据类
# --------------------------------------------------------------------------


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp_int(value: float, low: int, high: int) -> int:
    return int(round(clamp(value, low, high)))


@dataclass(slots=True)
class Attributes:
    physique: int = 4
    agility: int = 4
    intellect: int = 4
    perception: int = 4
    charm: int = 4
    willpower: int = 4
    luck: int = 4

    def __post_init__(self) -> None:
        for name in ATTRIBUTES:
            value = getattr(self, name)
            if not isinstance(value, int):
                raise ValidationError(f"属性 {name} 必须是整数，收到 {value!r}")
            setattr(self, name, clamp_int(value, 1, 10))

    def get(self, name: str) -> int:
        if name not in ATTRIBUTES:
            raise ValidationError(f"未知属性: {name}")
        return int(getattr(self, name))

    def modifier(self, name: str) -> int:
        return self.get(name) - 5

    def to_dict(self) -> dict[str, int]:
        return {name: self.get(name) for name in ATTRIBUTES}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Attributes":
        data = data or {}
        return cls(**{k: int(data.get(k, 4)) for k in ATTRIBUTES})


@dataclass(slots=True)
class SkillEntry:
    """存档只保存 id / level / xp，定义留在 Registry。"""

    id: str
    level: int = 0
    xp: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "level": int(self.level), "xp": int(self.xp)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillEntry":
        return cls(id=str(data["id"]), level=int(data.get("level", 0)), xp=int(data.get("xp", 0)))


@dataclass(slots=True)
class Status:
    health: int = 100
    energy: int = 100
    stress: int = 10
    mood: str = "normal"
    money: int = 10000

    def __post_init__(self) -> None:
        self.health = clamp_int(self.health, 0, 100)
        self.energy = clamp_int(self.energy, 0, 100)
        self.stress = clamp_int(self.stress, 0, 100)
        self.money = max(0, int(self.money))
        if self.mood not in VALID_MOODS:
            self.mood = "normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Status":
        data = data or {}
        return cls(
            health=int(data.get("health", 100)),
            energy=int(data.get("energy", 100)),
            stress=int(data.get("stress", 10)),
            mood=str(data.get("mood", "normal")),
            money=int(data.get("money", 10000)),
        )


@dataclass(slots=True)
class RelationshipValues:
    """七维关系值，全部 0-100。"""

    familiarity: int = 0
    trust: int = 0
    closeness: int = 0
    attraction: int = 0
    romantic_interest: int = 0
    comfort: int = 0
    conflict: int = 0

    def __post_init__(self) -> None:
        for dim in RELATIONSHIP_DIMENSIONS:
            setattr(self, dim, clamp_int(getattr(self, dim), 0, 100))

    def to_dict(self) -> dict[str, int]:
        return {d: int(getattr(self, d)) for d in RELATIONSHIP_DIMENSIONS}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RelationshipValues":
        data = data or {}
        return cls(**{d: int(data.get(d, 0)) for d in RELATIONSHIP_DIMENSIONS})

    def apply(self, deltas: dict[str, float]) -> dict[str, int]:
        """套用增量并返回实际发生的变化（可能因为封顶而小于请求值）。"""
        actual: dict[str, int] = {}
        for dim, delta in deltas.items():
            if dim not in RELATIONSHIP_DIMENSIONS:
                continue
            before = getattr(self, dim)
            after = clamp_int(before + delta, 0, 100)
            if after != before:
                setattr(self, dim, after)
                actual[dim] = after - before
        return actual


@dataclass(slots=True)
class Relationship:
    """A 对 B 的单向关系。关系是不对称的——这非常重要。"""

    source: str
    target: str
    values: RelationshipValues = field(default_factory=RelationshipValues)
    stage: str = RelationshipStage.STRANGER.value
    shared_experiences: list[str] = field(default_factory=list)
    explicit_events: dict[str, Any] = field(default_factory=dict)
    interaction_count: int = 0
    last_interaction_day: int | None = None
    first_met_day: int | None = None
    #: 未满 1 点的关系变化余量。没有它，长期的微小积累会被取整抹掉。
    residual: dict[str, float] = field(default_factory=dict)
    daily_gain: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}->{self.target}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "values": self.values.to_dict(),
            "stage": self.stage,
            "shared_experiences": list(self.shared_experiences),
            "explicit_events": dict(self.explicit_events),
            "interaction_count": int(self.interaction_count),
            "last_interaction_day": self.last_interaction_day,
            "first_met_day": self.first_met_day,
            "residual": {k: round(float(v), 4) for k, v in self.residual.items()},
            "daily_gain": dict(self.daily_gain),
            "history": list(self.history[-40:]),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Relationship":
        return cls(
            source=str(data["source"]),
            target=str(data["target"]),
            values=RelationshipValues.from_dict(data.get("values")),
            stage=str(data.get("stage", RelationshipStage.STRANGER.value)),
            shared_experiences=list(data.get("shared_experiences") or []),
            explicit_events=dict(data.get("explicit_events") or {}),
            interaction_count=int(data.get("interaction_count", 0)),
            last_interaction_day=data.get("last_interaction_day"),
            first_met_day=data.get("first_met_day"),
            residual={k: float(v) for k, v in (data.get("residual") or {}).items()},
            daily_gain=dict(data.get("daily_gain") or {}),
            history=list(data.get("history") or []),
            notes=str(data.get("notes", "")),
        )


@dataclass(slots=True)
class Memory:
    """一条记忆必须区分 事实 / 主观解释 / 情绪 —— 这三者不是一回事。"""

    id: str
    owner: str
    fact: str
    interpretation: str = ""
    emotion: str = ""
    intensity: int = 3
    visibility: str = MemoryVisibility.PRIVATE_FACT.value
    participants: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    day: int = 0
    date: str = ""
    time: str = ""
    turn: int = 0
    decay: float = 1.0

    def __post_init__(self) -> None:
        self.intensity = clamp_int(self.intensity, 1, 10)
        if self.visibility not in {v.value for v in MemoryVisibility}:
            self.visibility = MemoryVisibility.PRIVATE_FACT.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Memory":
        known = {f for f in Memory.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(slots=True)
class CheckOutcome:
    """resolve_check 的返回结构。"""

    roll: int
    attribute_modifier: int
    skill_modifier: int
    knowledge_modifier: int
    situational_modifier: int
    total: int
    dc: int
    margin: int
    result: str
    natural: str | None = None
    actor_id: str = ""
    action_type: str = ""
    attribute: str = ""
    skill: str | None = None
    knowledge: str | None = None
    difficulty: str = "normal"
    modifiers_detail: list[dict[str, Any]] = field(default_factory=list)
    npc_autonomy_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# 校验工具
# --------------------------------------------------------------------------


def validate_id(value: str, kind: str = "id") -> str:
    """ID 必须是 snake_case ASCII，防止 LLM 传入奇怪的键。"""
    if not isinstance(value, str):
        raise ValidationError(f"{kind} 必须是字符串，收到 {type(value).__name__}")
    value = value.strip()
    if not ID_RE.match(value):
        raise ValidationError(
            f"非法 {kind}: {value!r}（要求：小写字母开头，仅含 a-z 0-9 _，长度 2-64）"
        )
    return value


def validate_age(age: Any, *, context: str = "角色") -> int:
    """世界硬规则：所有角色必须成年。"""
    try:
        age_int = int(age)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{context}的年龄必须是整数，收到 {age!r}") from exc
    if age_int < MIN_AGE:
        raise ValidationError(
            f"拒绝创建 {context}：age={age_int} 低于世界最低年龄 {MIN_AGE}。"
            " 本世界中所有角色都是成年人。"
        )
    if age_int > 120:
        raise ValidationError(f"{context}的年龄不合理: {age_int}")
    return age_int


def validate_level(level: Any, *, kind: str = "等级") -> int:
    try:
        value = int(level)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{kind}必须是整数，收到 {level!r}") from exc
    if not 0 <= value <= 5:
        raise ValidationError(f"{kind}必须在 0~5 之间，收到 {value}")
    return value


def normalize_name(name: str) -> str:
    """用于重复检测的名称归一化。"""
    if not isinstance(name, str):
        return ""
    text = name.strip().lower()
    text = re.sub(r"[\s_\-·・'\"()（）【】\[\]]+", "", text)
    return text


def sanitize_text(value: Any, *, max_len: int = 4000, field_name: str = "文本") -> str:
    """不信任 LLM 输入：限制长度、去掉控制字符。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = "".join(ch for ch in value if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    if len(value) > max_len:
        raise ValidationError(f"{field_name}过长（{len(value)} > {max_len}）")
    return value.strip()


def ensure_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(f"{field_name} 必须是对象/字典，收到 {type(value).__name__}")
    return dict(value)


def ensure_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise ValidationError(f"{field_name} 必须是数组，收到字符串")
    if not isinstance(value, Iterable):
        raise ValidationError(f"{field_name} 必须是数组")
    return list(value)


@dataclass
class GameState:
    """一局游戏的全部可变状态 + 只读静态数据。

    ``static`` 里是从 YAML 读入的世界设定（只读）。
    其余字段全部对应 ``state/*.json``。
    """

    config: dict[str, Any] = field(default_factory=dict)
    static: dict[str, Any] = field(default_factory=dict)
    world: dict[str, Any] = field(default_factory=dict)
    characters: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, Any] = field(default_factory=dict)
    memories: dict[str, Any] = field(default_factory=dict)
    events: dict[str, Any] = field(default_factory=dict)
    registry: dict[str, Any] = field(default_factory=dict)

    # ---- 便捷访问 ----
    @property
    def player(self) -> dict[str, Any]:
        return self.characters.setdefault("player", {})

    @property
    def npcs(self) -> dict[str, Any]:
        return self.characters.setdefault("npcs", {})

    def cfg(self, path: str, default: Any = None) -> Any:
        """``state.cfg("relationship.max")`` 形式的配置读取。"""
        node: Any = self.config
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def to_dict(self) -> dict[str, Any]:
        return {
            "world": self.world,
            "characters": self.characters,
            "relationships": self.relationships,
            "memories": self.memories,
            "events": self.events,
            "registry": self.registry,
        }
