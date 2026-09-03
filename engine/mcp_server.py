#!/usr/bin/env python3
"""最小 MCP (Model Context Protocol) stdio 服务器。

让任何支持 MCP 的 Agent 客户端（Claude Desktop / Claude Code / 自研框架）
直接把这个世界当成工具集接进去。零第三方依赖。

启动::

    python3 -m engine.mcp_server

客户端配置示例::

    {
      "mcpServers": {
        "akizuki": {
          "command": "python3",
          "args": ["-m", "engine.mcp_server"],
          "cwd": "/path/to/highschool-life"
        }
      }
    }

实现的方法：initialize / tools/list / tools/call / ping / shutdown。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from . import tools as T

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "akizuki-gakuin", "version": "1.0.0"}

log = logging.getLogger("engine.mcp")


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return _response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "秋月学院 · 成人日式校园生活 / 恋爱 / TRPG 模拟世界。"
                    "读取项目根目录的 AGENT.md 作为系统提示词。"
                    "所有世界状态只能通过本服务器的工具修改；不要自己骰骰子或改数值。"
                ),
            },
        )
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(
            request_id,
            {
                "tools": [
                    {
                        "name": schema["name"],
                        "description": schema["description"],
                        "inputSchema": schema["input_schema"],
                    }
                    for schema in T.tool_schemas()
                ]
            },
        )
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        result = T.call_tool(name, arguments)
        return _response(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                "isError": not result.get("ok", True),
            },
        )
    if method in ("shutdown", "exit"):
        return _response(request_id, {})
    return _error(request_id, -32601, f"未实现的方法: {method}")


def main() -> int:  # pragma: no cover - I/O 循环
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
            sys.stdout.flush()
            continue
        try:
            response = handle(message)
        except Exception as exc:  # noqa: BLE001
            log.exception("MCP handler failed")
            response = _error(message.get("id"), -32603, f"{type(exc).__name__}: {exc}")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
