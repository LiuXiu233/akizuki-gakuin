"""流水线定义的读取（里程碑 2 会补上执行器）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def pipelines_dir(project_root: Path) -> Path:
    return project_root / "pipelines"


def list_pipelines(project_root: Path) -> list[dict[str, Any]]:
    directory = pipelines_dir(project_root)
    if not directory.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        out.append(
            {
                "id": data.get("id", path.stem),
                "name": data.get("name", path.stem),
                "description": data.get("description", ""),
                "stages": [s.get("id") for s in (data.get("stages") or [])],
                "stage_count": len(data.get("stages") or []),
            }
        )
    return out


def load_pipeline(project_root: Path, pipeline_id: str) -> dict[str, Any]:
    path = pipelines_dir(project_root) / f"{pipeline_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"未知流水线: {pipeline_id}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
