"""统一随机源。

**硬规则**：游戏中的一切随机必须来自这里。
LLM 不得自己骰、不得重骰、不得为剧情作弊。

- 测试：``GameRNG(seed=42)`` —— 完全可复现。
- 正式游戏：``GameRNG()`` —— 使用系统随机源，seed 会被记录进存档。
"""

from __future__ import annotations

import random
import secrets
from typing import Any, Iterable, Mapping, Sequence, TypeVar

T = TypeVar("T")

MAX_LOG_DEFAULT = 500


class GameRNG:
    """可注入、可序列化、可审计的随机数发生器。"""

    def __init__(
        self,
        seed: int | None = None,
        *,
        log_rolls: bool = True,
        max_log: int = MAX_LOG_DEFAULT,
    ) -> None:
        if seed is None:
            seed = secrets.randbelow(2**31 - 1)
        self.seed: int = int(seed)
        self._random = random.Random(self.seed)
        self.log_rolls = bool(log_rolls)
        self.max_log = int(max_log)
        self.log: list[dict[str, Any]] = []
        self.count: int = 0

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _record(self, kind: str, value: Any, reason: str, extra: dict[str, Any] | None = None) -> None:
        self.count += 1
        if not self.log_rolls:
            return
        entry: dict[str, Any] = {"n": self.count, "kind": kind, "value": value, "reason": reason}
        if extra:
            entry.update(extra)
        self.log.append(entry)
        if len(self.log) > self.max_log:
            del self.log[: len(self.log) - self.max_log]

    # ------------------------------------------------------------------
    # 骰子
    # ------------------------------------------------------------------
    def d20(self, reason: str = "") -> int:
        """核心判定骰。"""
        value = self._random.randint(1, 20)
        self._record("d20", value, reason)
        return value

    def dice(self, count: int, sides: int, reason: str = "") -> list[int]:
        if count < 1 or sides < 2:
            raise ValueError("dice(count>=1, sides>=2)")
        rolls = [self._random.randint(1, sides) for _ in range(count)]
        self._record(f"{count}d{sides}", rolls, reason)
        return rolls

    def roll(self, count: int, sides: int, reason: str = "") -> int:
        return sum(self.dice(count, sides, reason))

    def randint(self, low: int, high: int, reason: str = "") -> int:
        value = self._random.randint(int(low), int(high))
        self._record("randint", value, reason, {"range": [low, high]})
        return value

    def uniform(self, low: float, high: float, reason: str = "") -> float:
        value = self._random.uniform(low, high)
        self._record("uniform", round(value, 4), reason)
        return value

    def chance(self, probability: float, reason: str = "") -> bool:
        """``probability`` 概率返回 True。"""
        probability = max(0.0, min(1.0, float(probability)))
        value = self._random.random()
        hit = value < probability
        self._record("chance", hit, reason, {"p": round(probability, 4), "rolled": round(value, 4)})
        return hit

    def choice(self, seq: Sequence[T], reason: str = "") -> T:
        if not seq:
            raise ValueError("choice() 收到空序列")
        value = self._random.choice(list(seq))
        self._record("choice", value if isinstance(value, (str, int, float)) else "<obj>", reason)
        return value

    def sample(self, seq: Sequence[T], k: int, reason: str = "") -> list[T]:
        items = list(seq)
        k = max(0, min(len(items), int(k)))
        value = self._random.sample(items, k)
        self._record("sample", k, reason)
        return value

    def shuffle(self, seq: list[T], reason: str = "") -> list[T]:
        self._random.shuffle(seq)
        self._record("shuffle", len(seq), reason)
        return seq

    def weighted_choice(
        self,
        options: Mapping[str, float] | Sequence[tuple[T, float]],
        reason: str = "",
    ) -> T | str | None:
        """按权重抽取。权重 <= 0 的项会被忽略；全为 0 时返回 None。"""
        if isinstance(options, Mapping):
            pairs: list[tuple[Any, float]] = [(k, float(v)) for k, v in options.items()]
        else:
            pairs = [(k, float(v)) for k, v in options]
        pairs = [(k, w) for k, w in pairs if w > 0]
        if not pairs:
            self._record("weighted_choice", None, reason)
            return None
        total = sum(w for _, w in pairs)
        target = self._random.random() * total
        upto = 0.0
        picked = pairs[-1][0]
        for key, weight in pairs:
            upto += weight
            if target <= upto:
                picked = key
                break
        self._record(
            "weighted_choice",
            picked if isinstance(picked, (str, int, float)) else "<obj>",
            reason,
            {"n_options": len(pairs)},
        )
        return picked

    def gauss_int(self, mu: float, sigma: float, low: int, high: int, reason: str = "") -> int:
        value = int(round(self._random.gauss(mu, sigma)))
        value = max(int(low), min(int(high), value))
        self._record("gauss_int", value, reason)
        return value

    def jitter(self, base: int, spread: int, reason: str = "") -> int:
        """在 base 附近抖动 ±spread（用于时间、日程偏移等）。"""
        return base + self.randint(-abs(spread), abs(spread), reason)

    # ------------------------------------------------------------------
    # 序列化（存档 / 读档后随机流保持连续）
    # ------------------------------------------------------------------
    def export_state(self) -> dict[str, Any]:
        version, internal, gauss = self._random.getstate()
        return {
            "seed": self.seed,
            "count": self.count,
            "version": version,
            "internal": list(internal),
            "gauss": gauss,
        }

    def restore_state(self, data: dict[str, Any]) -> None:
        if not data:
            return
        try:
            self.seed = int(data.get("seed", self.seed))
            self.count = int(data.get("count", 0))
            self._random.setstate(
                (int(data["version"]), tuple(int(x) for x in data["internal"]), data.get("gauss"))
            )
        except (KeyError, TypeError, ValueError) as exc:  # 损坏的存档不应炸掉游戏
            raise ValueError(f"RNG 状态损坏，无法恢复: {exc}") from exc

    def recent_log(self, n: int = 20) -> list[dict[str, Any]]:
        return self.log[-n:]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"GameRNG(seed={self.seed}, rolls={self.count})"


def make_rng(config: dict[str, Any] | None = None, seed: int | None = None) -> GameRNG:
    """按配置构造 RNG。``config['rng']['default_seed']`` 为 null 时使用系统随机源。"""
    cfg = (config or {}).get("rng", {}) if isinstance(config, dict) else {}
    if seed is None:
        seed = cfg.get("default_seed")
    return GameRNG(
        seed=seed,
        log_rolls=bool(cfg.get("log_rolls", True)),
        max_log=int(cfg.get("max_roll_log", MAX_LOG_DEFAULT)),
    )
