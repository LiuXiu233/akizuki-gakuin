"""文生图路由：生成、列出、取文件、删除。

图像**按用户完全隔离**——每个世界一个 images 目录，别的用户看不到也用不到。
没有配置图像服务时全部返回 ``skipped``，前端自动退回配色占位符。
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..deps import AccessDep, RegistryDep, SettingsDep, WorldDep
from ..images import ImageError, ImageService, KINDS
from ..schemas import ImageRequest

router = APIRouter(tags=["images"])

FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}\.png$")


def _service(settings, registry, entry) -> ImageService:
    return ImageService(
        settings,
        entry.session,
        registry.store.images_dir(entry.user_id, entry.world_id),
    )


@router.get("/worlds/{world_id}/images")
async def list_images(
    world_id: str, _: AccessDep, settings: SettingsDep, registry: RegistryDep, entry: WorldDep
) -> dict[str, Any]:
    """列出这个世界已经生成过的所有图。"""
    service = _service(settings, registry, entry)
    return {"ok": True, "enabled": settings.image_enabled, "images": service.listing(world_id)}


@router.post("/worlds/{world_id}/images")
async def create_image(
    payload: ImageRequest,
    world_id: str,
    _: AccessDep,
    settings: SettingsDep,
    registry: RegistryDep,
    entry: WorldDep,
) -> dict[str, Any]:
    """按需生成一张图（已有则直接返回缓存）。

    kind: avatar / portrait / scene / cg
    """
    service = _service(settings, registry, entry)
    async with entry.lock:
        try:
            result = await service.generate(
                world_id=world_id,
                kind=payload.kind,
                subject_id=payload.subject_id,
                extra=payload.prompt_extra,
                credentials=payload.credentials,
                force=payload.force,
            )
        except ImageError as exc:
            return {"ok": False, "error": str(exc), "status": exc.status_code}
        registry.sync_meta(entry)
    return result


@router.get("/worlds/{world_id}/images/file/{kind}/{filename}")
async def get_image_file(
    world_id: str, kind: str, filename: str,
    _: AccessDep, settings: SettingsDep, registry: RegistryDep, entry: WorldDep,
) -> FileResponse:
    """读取图片文件。路径经过严格校验，无法越出这个世界的目录。"""
    if kind not in KINDS or not FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="非法的图片路径")
    path = registry.store.images_dir(entry.user_id, entry.world_id) / kind / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.delete("/worlds/{world_id}/images/{kind}/{subject_id}")
async def delete_image(
    world_id: str, kind: str, subject_id: str,
    _: AccessDep, settings: SettingsDep, registry: RegistryDep, entry: WorldDep,
) -> dict[str, Any]:
    """删掉一张图（下次会重新生成）。"""
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="未知图像类型")
    service = _service(settings, registry, entry)
    path = service.path_for(kind, subject_id)
    if path.exists():
        path.unlink()
    return {"ok": True, "deleted": f"{kind}:{subject_id}"}
