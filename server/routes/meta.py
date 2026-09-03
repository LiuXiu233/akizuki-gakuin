"""元信息：健康检查、服务器能力、规则速查、工具 Schema、世界设定文档。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from engine import __version__ as engine_version
from engine import tools as T
from engine.tools import GameSession, use_session

from .. import __version__ as server_version
from ..config import Settings
from ..deps import AccessDep, SettingsDep

router = APIRouter(tags=["meta"])

_READONLY_SESSION: GameSession | None = None


def readonly_session(settings: Settings) -> GameSession:
    """一个只读的样板世界，用来回答与存档无关的问题（规则、图鉴、Schema）。"""
    global _READONLY_SESSION
    if _READONLY_SESSION is None:
        _READONLY_SESSION = GameSession(
            root=settings.project_root,
            data_root=settings.data_dir / "_reference",
            autoload=True,
        )
    return _READONLY_SESSION


@router.get("/health")
async def health(settings: SettingsDep) -> dict[str, Any]:
    """健康检查。前端启动时用它判断后端是否可用、是否需要口令。"""
    return {
        "ok": True,
        "server_version": server_version,
        "engine_version": engine_version,
        **settings.to_public_dict(),
    }


@router.get("/meta")
async def meta(_: AccessDep, settings: SettingsDep) -> dict[str, Any]:
    """世界的静态元信息：规则速查、可用行动、关系事件、社团、地点、预设。"""
    session = readonly_session(settings)
    with use_session(session):
        return {
            "ok": True,
            "rules": T.get_rules_digest(),
            "content_rules": T.get_content_rules()["rules"],
            "locations": T.get_locations()["locations"],
            "clubs": T.get_clubs()["groups"],
            "registry": T.get_registry()["registry"],
            "player_presets": (session.state.static.get("player_template") or {}).get("presets", []),
            "creation_rules": (session.state.static.get("player_template") or {}).get("creation_rules", {}),
            "attributes": (session.state.static.get("attributes") or {}).get("attributes", {}),
            "skills": (session.state.static.get("skill_registry") or {}).get("skills", []),
            "knowledge": (session.state.static.get("knowledge_registry") or {}).get("knowledge", []),
        }


@router.get("/tools/schema")
async def tools_schema(_: AccessDep) -> dict[str, Any]:
    """全部工具的 JSON Schema —— 与 agent_tools.json 内容一致。"""
    return {"ok": True, "tools": T.tool_schemas()}


@router.get("/lore/{topic}")
async def lore(topic: str, _: AccessDep, settings: SettingsDep) -> dict[str, Any]:
    """世界设定文档：school / culture / rules / agent。"""
    session = readonly_session(settings)
    with use_session(session):
        result = T.get_world_lore(topic)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "未知主题"))
    return result


@router.get("/pipelines")
async def pipelines(_: AccessDep, settings: SettingsDep) -> dict[str, Any]:
    """可用的 Agent 流水线预设。"""
    from ..agents.pipeline import list_pipelines

    return {"ok": True, "pipelines": list_pipelines(settings.project_root)}
