"""测试公共工具：在临时目录里跑一个隔离的存档，避免污染项目 state/。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import tools as T  # noqa: E402
from engine.tools import GameSession, reset_session  # noqa: E402

STATIC_DIRS = ("config", "world", "characters", "rules", "events")


def make_sandbox() -> Path:
    """复制静态资料到临时目录，state/ 与 saves/ 全新。"""
    tmp = Path(tempfile.mkdtemp(prefix="akizuki_test_"))
    for name in STATIC_DIRS:
        shutil.copytree(ROOT / name, tmp / name)
    for doc in ("AGENT.md",):
        source = ROOT / doc
        if source.exists():
            shutil.copy2(source, tmp / doc)
    (tmp / "state").mkdir()
    (tmp / "saves").mkdir()
    return tmp


class EngineTestCase(unittest.TestCase):
    """带固定种子的隔离会话。"""

    seed = 42
    player_preset = "preset_allrounder"

    def setUp(self) -> None:
        self.tmp = make_sandbox()
        session = GameSession(root=self.tmp, seed=self.seed, autoload=False)
        self.session = reset_session(session)
        self.T = T
        if self.player_preset:
            result = T.create_player(name="测试玩家", age=19, preset=self.player_preset)
            assert result["ok"], result

    def tearDown(self) -> None:
        reset_session(GameSession(root=self.tmp, seed=self.seed, autoload=False))
        shutil.rmtree(self.tmp, ignore_errors=True)
