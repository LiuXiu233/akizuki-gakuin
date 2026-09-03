"""``python3 -m server`` 启动开发服务器。"""

from __future__ import annotations

import argparse
import logging

from .config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="秋月学院后端")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper())
    settings = get_settings()
    print(f"数据目录: {settings.data_dir}")
    print(f"访问口令: {'已启用' if settings.auth_required else '未设置（任何人可访问）'}")
    print(f"服务器 LLM: {'已配置 ' + (settings.llm.model or settings.llm.provider) if settings.llm.configured else '未配置（玩家自带 key）'}")

    import uvicorn

    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=args.reload, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
