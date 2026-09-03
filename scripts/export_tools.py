#!/usr/bin/env python3
"""导出机器可读的工具清单，供任意 Agent 框架直接消费。

生成:
  agent_tools.json   —— 全部工具的 JSON Schema（Anthropic / OpenAI 风格通用）
  agent_manifest.json —— 项目自述：入口、文件、规则要点

运行:  python3 scripts/export_tools.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import __version__  # noqa: E402
from engine import tools as T  # noqa: E402


def main() -> int:
    schemas = T.tool_schemas()
    (ROOT / "agent_tools.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_by": "scripts/export_tools.py",
                "engine_version": __version__,
                "call_conventions": {
                    "python": "from engine.tools import call_tool; call_tool(name, args)",
                    "cli": "python3 -m engine.tools call <name> '<json>'",
                    "mcp": "python3 -m engine.mcp_server  (stdio JSON-RPC)",
                },
                "error_shape": {"ok": False, "error": "string", "hint": "string?"},
                "tools": schemas,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest = {
        "name": "akizuki-gakuin",
        "title": "秋月学院 · 成人日式校园生活 / 恋爱 / TRPG 模拟",
        "version": __version__,
        "language": "zh-CN",
        "python": ">=3.11",
        "dependencies": ["PyYAML"],
        "system_prompt": "AGENT.md",
        "quickstart_prompt": "PROMPT.md",
        "entrypoints": {
            "python": "engine.tools",
            "cli_list": "python3 -m engine.tools list",
            "cli_schema": "python3 -m engine.tools schema",
            "cli_call": "python3 -m engine.tools call <tool> '<json>'",
            "mcp_stdio": "python3 -m engine.mcp_server",
        },
        "documents": {
            "system_prompt": "AGENT.md",
            "rules": "rules/rules.md",
            "world": "world/school.md",
            "culture": "world/culture.md",
            "content_rules": "config/content_rules.yaml",
        },
        "verification": {
            "unit_tests": "python3 -m unittest discover -s tests -t .",
            "consistency": "python3 scripts/verify_consistency.py",
            "smoke_test": "python3 scripts/smoke_test.py",
        },
        "hard_rules": [
            "所有角色 age >= 18",
            "LLM 不得直接修改状态，只能通过 engine/tools.py",
            "所有随机来自 engine/rng.py，不得自行骰点或重骰",
            "社交检定不能决定 NPC 的选择（Natural 20 也不行）",
            "默认不向玩家暴露 attraction / romantic_interest / trust 数值",
            "不得替玩家做决定",
            "NPC 只能知道自己有渠道知道的事",
        ],
        "tool_count": len(schemas),
        "tools": sorted(t["name"] for t in schemas),
    }
    (ROOT / "agent_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已导出 agent_tools.json（{len(schemas)} 个工具）与 agent_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
