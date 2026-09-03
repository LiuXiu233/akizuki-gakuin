"""时间系统。

时间是这个游戏最核心的资源。**所有时间推进必须由代码执行**，
LLM 不得自行宣称"三小时过去了"。
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from .models import GameState, ValidationError, clamp_int
from .registry_manager import RegistryManager
from .rng import GameRNG

log = logging.getLogger("engine.time")

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_ZH = {"mon": "周一", "tue": "周二", "wed": "周三", "thu": "周四", "fri": "周五", "sat": "周六", "sun": "周日"}

WEATHER_ZH = {
    "sunny": "晴", "cloudy": "多云", "rain": "雨", "hot": "酷暑", "cold": "寒冷",
    "snow": "雪", "thunder": "雷雨", "typhoon": "台风", "warm": "温暖",
    "sakura_wind": "花吹雪",
}

#: 基础消耗：每小时精力 / 压力变化
BASE_ENERGY_PER_HOUR = 3.0
SLEEP_ENERGY_PER_HOUR = 12.0
CLASS_STRESS_PER_HOUR = 1.2


def parse_hhmm(value: str) -> int:
    """"HH:MM" -> 从 00:00 起的分钟数。"""
    try:
        hour, minute = str(value).split(":")
        return int(hour) * 60 + int(minute)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"非法时间格式 {value!r}，应为 HH:MM") from exc


def format_hhmm(minutes: int) -> str:
    minutes = int(minutes) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class TimeManager:
    """负责日期、时刻、日程块、移动时间、跨日结算。"""

    def __init__(self, state: GameState, rng: GameRNG, registry: RegistryManager | None = None) -> None:
        self.state = state
        self.rng = rng
        self.registry = registry or RegistryManager(state)

    # ------------------------------------------------------------------
    # 当前时间
    # ------------------------------------------------------------------
    @property
    def date(self) -> dt.date:
        return dt.date.fromisoformat(str(self.state.world["date"]))

    @property
    def minutes(self) -> int:
        return parse_hhmm(str(self.state.world["time"]))

    @property
    def time_str(self) -> str:
        return str(self.state.world["time"])

    @property
    def day_index(self) -> int:
        return int(self.state.world.get("day_index", 0))

    @property
    def weekday(self) -> str:
        return WEEKDAY_KEYS[self.date.weekday()]

    @property
    def weekday_zh(self) -> str:
        return WEEKDAY_ZH[self.weekday]

    def now_dict(self) -> dict[str, Any]:
        return {
            "date": self.state.world["date"],
            "time": self.time_str,
            "minutes": self.minutes,
            "weekday": self.weekday,
            "weekday_zh": self.weekday_zh,
            "day_index": self.day_index,
            "turn": int(self.state.world.get("turn", 0)),
            "weather": self.state.world.get("weather", "sunny"),
            "weather_zh": WEATHER_ZH.get(str(self.state.world.get("weather", "sunny")), "晴"),
            "block": self.current_block().get("name") if self.current_block() else None,
            "day_type": self.day_type(),
            "term": self.current_term(),
        }

    # ------------------------------------------------------------------
    # 日程块 / 日类型
    # ------------------------------------------------------------------
    def current_block(self, minutes: int | None = None) -> dict[str, Any] | None:
        minutes = self.minutes if minutes is None else minutes
        blocks = (self.state.static.get("schedule") or {}).get("daily_blocks", []) or []
        for block in blocks:
            start = parse_hhmm(block["start"])
            end = parse_hhmm(block["end"])
            if end <= start:  # 跨午夜
                if minutes >= start or minutes < end:
                    return block
            elif start <= minutes < end:
                return block
        return None

    def day_type(self) -> str:
        """school / school_no_club / half_day / holiday / vacation"""
        term = self.current_term()
        if term and term.get("id") in ("term_summer", "term_winter"):
            return "vacation"
        mapping = (self.state.static.get("schedule") or {}).get("weekday_type", {})
        return str(mapping.get(self.weekday, "holiday"))

    def is_school_day(self) -> bool:
        return self.day_type() in ("school", "school_no_club", "half_day")

    def is_class_time(self) -> bool:
        if not self.is_school_day():
            return False
        block = self.current_block()
        return bool(block and "class" in (block.get("tags") or []))

    def is_club_time(self) -> bool:
        if self.day_type() in ("holiday", "vacation", "school_no_club"):
            return False
        return parse_hhmm("15:40") <= self.minutes < parse_hhmm("18:30")

    def current_term(self) -> dict[str, Any] | None:
        cal = self.state.static.get("calendar") or {}
        today = self.date.strftime("%m-%d")
        for term in (cal.get("academic_year") or {}).get("terms", []) or []:
            start, end = str(term["start"]), str(term["end"])
            if start <= end:
                if start <= today <= end:
                    return term
            else:  # 跨年
                if today >= start or today <= end:
                    return term
        return None

    def calendar_events_today(self) -> list[dict[str, Any]]:
        """返回今天正在进行的日历事件（考虑 duration_days）。"""
        cal = self.state.static.get("calendar") or {}
        today = self.date
        out: list[dict[str, Any]] = []
        for event in cal.get("events", []) or []:
            try:
                month, day = (int(x) for x in str(event["date"]).split("-"))
            except Exception:  # noqa: BLE001
                continue
            duration = int(event.get("duration_days", 1))
            for year in (today.year - 1, today.year):
                try:
                    start = dt.date(year, month, day)
                except ValueError:
                    continue
                if start <= today < start + dt.timedelta(days=duration):
                    out.append(dict(event, day_offset=(today - start).days))
        return out

    def today_class_subjects(self, class_id: str = "class_2a") -> list[str]:
        table = (self.state.static.get("schedule") or {}).get("class_timetable", {})
        return list((table.get(class_id) or {}).get(self.weekday, []) or [])

    # ------------------------------------------------------------------
    # 移动时间
    # ------------------------------------------------------------------
    def travel_minutes(self, from_location: str, to_location: str) -> int:
        if from_location == to_location:
            return 0
        travel = (self.state.static.get("locations") or {}).get("travel", {})
        zone_a = str((self.registry.get("location", from_location) or {}).get("zone", "town_center"))
        zone_b = str((self.registry.get("location", to_location) or {}).get("zone", "town_center"))
        if zone_a == zone_b:
            return int((travel.get("within_zone_minutes") or {}).get(zone_a, 5))
        between = travel.get("between_zone_minutes") or {}
        for key in (f"{zone_a}|{zone_b}", f"{zone_b}|{zone_a}"):
            if key in between:
                return int(between[key])
        return int(travel.get("default_minutes", 20))

    def is_open(self, location_id: str, minutes: int | None = None) -> bool:
        loc = self.registry.get("location", location_id)
        if not loc:
            return False
        minutes = self.minutes if minutes is None else minutes
        hours = loc.get("open_hours") or [0, 24]
        hour = minutes / 60.0
        start, end = float(hours[0]), float(hours[1])
        if end >= 24 and hour < (end - 24):
            return True
        seasonal = loc.get("seasonal")
        if seasonal and self.date.month not in list(seasonal):
            return False
        return start <= hour < min(end, 24)

    # ------------------------------------------------------------------
    # 推进
    # ------------------------------------------------------------------
    def advance(
        self,
        minutes: int,
        *,
        reason: str = "",
        sleeping: bool = False,
        energy_cost: float | None = None,
        stress_delta: float | None = None,
    ) -> dict[str, Any]:
        """推进时间并结算精力 / 压力 / 跨日。

        返回结算报告，含 ``day_rollover``、``forced_sleep`` 等标记。
        """
        minutes = int(minutes)
        if minutes < 0:
            raise ValidationError("时间不能倒流")
        if minutes > 24 * 60:
            raise ValidationError("单次推进不得超过 24 小时")

        before_day = self.day_index
        start = dt.datetime.combine(self.date, dt.time()) + dt.timedelta(minutes=self.minutes)
        end = start + dt.timedelta(minutes=minutes)

        days_passed = (end.date() - start.date()).days
        self.state.world["date"] = end.date().isoformat()
        self.state.world["time"] = end.strftime("%H:%M")
        self.state.world["day_index"] = before_day + days_passed

        player = self.state.player
        status = player.setdefault("status", {})
        hours = minutes / 60.0

        if sleeping:
            recovery = SLEEP_ENERGY_PER_HOUR * hours
            status["energy"] = clamp_int(status.get("energy", 100) + recovery, 0, 100)
            status["stress"] = clamp_int(status.get("stress", 10) - 3 * hours, 0, 100)
            status["health"] = clamp_int(status.get("health", 100) + 2 * hours, 0, 100)
        else:
            cost = BASE_ENERGY_PER_HOUR * hours if energy_cost is None else float(energy_cost)
            status["energy"] = clamp_int(status.get("energy", 100) - cost, 0, 100)
            delta = stress_delta
            if delta is None:
                delta = CLASS_STRESS_PER_HOUR * hours if self.is_class_time() else 0.0
            status["stress"] = clamp_int(status.get("stress", 10) + delta, 0, 100)

        report: dict[str, Any] = {
            "advanced_minutes": minutes,
            "reason": reason,
            "date": self.state.world["date"],
            "time": self.state.world["time"],
            "day_rollover": days_passed > 0,
            "days_passed": days_passed,
            "energy": status.get("energy"),
            "stress": status.get("stress"),
            "new_calendar_events": [],
        }

        for _ in range(days_passed):
            report["new_calendar_events"].extend(self._on_new_day())

        report["forced_sleep"] = self.needs_forced_sleep()
        self._update_conditions()
        return report

    def _on_new_day(self) -> list[dict[str, Any]]:
        """跨日结算：天气、压力自然衰减、每日计数重置。"""
        world = self.state.world
        world["weather"] = self.roll_weather()
        player = self.state.player
        status = player.setdefault("status", {})
        decay = float((self.state.config.get("stress") or {}).get("daily_natural_decay", 3))
        status["stress"] = clamp_int(status.get("stress", 10) - decay, 0, 100)
        world.setdefault("daily_flags", {}).clear()
        world["events_today"] = 0
        for npc in self.state.npcs.values():
            npc_status = npc.setdefault("status", {})
            npc_status["energy"] = clamp_int(npc_status.get("energy", 100) + 60, 0, 100)
            npc_status["stress"] = clamp_int(npc_status.get("stress", 10) - 4, 0, 100)
            npc.pop("daily", None)
        events = self.calendar_events_today()
        if events:
            log.info("new day %s events=%s", world["date"], [e["id"] for e in events])
        return events

    def roll_weather(self) -> str:
        table = (self.state.static.get("calendar") or {}).get("weather", {})
        month_table = table.get(self.date.month) or table.get(str(self.date.month)) or {"sunny": 1}
        choice = self.rng.weighted_choice({k: float(v) for k, v in month_table.items()}, reason="weather")
        return str(choice or "sunny")

    def needs_forced_sleep(self) -> bool:
        limit = int((self.state.config.get("time") or {}).get("forced_sleep_hour", 26))
        hour = self.minutes // 60
        effective = hour + 24 if hour < 5 else hour
        return effective >= limit

    def _update_conditions(self) -> None:
        """根据数值刷新玩家的状态标签（tired / stressed / hungry ...）。"""
        player = self.state.player
        status = player.setdefault("status", {})
        conditions: list[str] = [
            c for c in player.get("conditions", []) if c not in
            {"tired", "exhausted", "stressed", "overloaded", "hungry", "sleepy"}
        ]
        energy = int(status.get("energy", 100))
        stress = int(status.get("stress", 10))
        if energy <= 10:
            conditions.append("exhausted")
        elif energy <= 25:
            conditions.append("tired")
        if stress >= 85:
            conditions.append("overloaded")
        elif stress >= 60:
            conditions.append("stressed")
        hour = self.minutes // 60
        last_meal = int(player.get("last_meal_minutes", -999))
        elapsed = self.day_index * 1440 + self.minutes - last_meal
        if last_meal > -999 and elapsed > 330:
            conditions.append("hungry")
        if hour >= 24 or hour < 5:
            conditions.append("sleepy")
        player["conditions"] = sorted(set(conditions))

    def sleep(self, hours: float = 7.0) -> dict[str, Any]:
        """睡觉直到指定时长（跨日由 advance 处理）。"""
        minutes = int(max(1, min(14, hours)) * 60)
        report = self.advance(minutes, reason="睡觉", sleeping=True)
        self.state.player["last_meal_minutes"] = self.day_index * 1440 + self.minutes
        report["slept_hours"] = round(minutes / 60, 2)
        return report

    def sleep_until(self, wake_time: str = "07:00") -> dict[str, Any]:
        target = parse_hhmm(wake_time)
        current = self.minutes
        minutes = (target - current) % (24 * 60)
        if minutes == 0:
            minutes = 24 * 60
        return self.advance(minutes, reason=f"睡到 {wake_time}", sleeping=True)

    # ------------------------------------------------------------------
    def time_cost(self, kind: str, *, fraction: float = 0.5) -> int:
        """从 schedule.yaml 的 time_costs 取一个合理时长。"""
        costs = (self.state.static.get("schedule") or {}).get("time_costs", {})
        rng_pair = costs.get(kind)
        if not rng_pair:
            return 15
        low, high = int(rng_pair[0]), int(rng_pair[1])
        return int(low + (high - low) * max(0.0, min(1.0, fraction)))
