"""叙事日志：把每一回合的正文、台词、判定、推荐持久化到世界目录。

引擎存的是**世界的状态**（关系、记忆、时间、注册表），
但玩家读到的那些字是叙事层产物，之前只活在浏览器内存里——
刷新一下就全没了。故事本身也是存档的一部分，所以落盘。

格式用 JSON Lines：追加成本恒定，单行损坏不会毁掉整个文件。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("server.journal")

MAX_ENTRIES = 400
MAX_TEXT = 20000


def _clip(value: Any, limit: int = MAX_TEXT) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


class Journal:
    def __init__(self, world_dir: Path) -> None:
        self.path = world_dir / "journal.jsonl"

    # ------------------------------------------------------------------
    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        record = {
            "id": entry.get("id") or f"t{int(time.time() * 1000)}",
            "turn": int(entry.get("turn") or 0),
            "at": time.time(),
            "date": _clip(entry.get("date"), 20),
            "time": _clip(entry.get("time"), 10),
            "location": _clip(entry.get("location"), 80),
            "pipeline": _clip(entry.get("pipeline"), 32),
            "playerInput": _clip(entry.get("playerInput"), 4000),
            "narration": _clip(entry.get("narration")),
            "dialogue": [
                {
                    "npc_id": _clip(line.get("npc_id"), 80),
                    "name": _clip(line.get("name"), 40),
                    "text": _clip(line.get("text"), 4000),
                }
                for line in (entry.get("dialogue") or [])[:12]
                if isinstance(line, dict)
            ],
            "checkText": _clip(entry.get("checkText"), 2000),
            "growthText": _clip(entry.get("growthText"), 2000),
            "recommendations": [
                {
                    "text": _clip(item.get("text"), 300),
                    "minutes": _clip(item.get("minutes"), 40),
                    "category": _clip(item.get("category"), 20),
                }
                for item in (entry.get("recommendations") or [])[:5]
                if isinstance(item, dict)
            ],
            "randomEvent": entry.get("randomEvent") or None,
            "usage": entry.get("usage") or None,
            "errors": [_clip(e, 500) for e in (entry.get("errors") or [])[:5]],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:  # 写日志失败不该让整个回合失败
            log.warning("journal 写入失败: %s", exc)
        return record

    # ------------------------------------------------------------------
    def read(self, limit: int = 60) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue      # 坏行跳过，不影响其余记录
        except OSError as exc:
            log.warning("journal 读取失败: %s", exc)
            return []
        return out[-max(1, limit):]

    def compact(self) -> None:
        """超过上限时只保留最近的记录。"""
        entries = self.read(limit=MAX_ENTRIES * 2)
        if len(entries) <= MAX_ENTRIES:
            return
        keep = entries[-MAX_ENTRIES:]
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for item in keep:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
