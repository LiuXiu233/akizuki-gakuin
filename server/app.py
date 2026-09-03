"""FastAPI 应用入口。

启动::

    .venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 8000
    # 或
    python3 -m server
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .config import get_settings
from .deps import get_registry
from .routes import llm, meta, tools, turn, worlds

log = logging.getLogger("server")

DESCRIPTION = """
秋月学院 · 成人日式校园生活 / 恋爱 / TRPG 模拟世界的 HTTP 接口。

* 世界规则全部在 `engine/`，本服务只做传输、隔离与编排
* 每个用户令牌下可以有多个独立世界（存档）
* `POST /api/worlds/{world_id}/tools/{tool_name}` 是唯一的世界写入通道
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("data dir: %s", settings.data_dir)
    yield
    await get_registry().close_all()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="秋月学院 · Akizuki Gakuin API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-User-Token"],
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})

    app.include_router(meta.router, prefix="/api")
    app.include_router(worlds.router, prefix="/api")
    app.include_router(tools.router, prefix="/api")
    app.include_router(turn.router, prefix="/api")
    app.include_router(llm.router, prefix="/api")

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "ok": True,
            "name": "秋月学院 API",
            "version": __version__,
            "docs": "/docs",
            "health": "/api/health",
        }

    return app


app = create_app()
