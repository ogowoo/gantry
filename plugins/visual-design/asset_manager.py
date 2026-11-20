"""visual-design 项目资产导入与登记。

资产文件落在项目 `assets/`，元数据统一写入 `assets/index.json`：
asset_id / path / source / width / height / bytes / sha256 / role / version。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

MAX_ASSET_BYTES = 25 * 1024 * 1024
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".svg"}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class AssetError(RuntimeError):
    pass


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".asset-", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _index_path(project: Path) -> Path:
    return project / "assets" / "index.json"


def load_index(project: Path) -> dict[str, Any]:
    path = _index_path(project)
    if not path.is_file():
        return {"version": 1, "assets": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AssetError(f"assets/index.json 读取失败: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
        raise AssetError("assets/index.json 结构不合法")
    data["version"] = int(data.get("version") or 1)
    return data


def save_index(project: Path, data: dict[str, Any]) -> None:
    _atomic(_index_path(project),
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _source_path(workspace: Path, source: str) -> Path:
    text = str(source or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or ".." in Path(text).parts:
        raise AssetError("source 必须是工作区内的相对路径")
    candidate = (workspace / text).resolve()
    if workspace not in candidate.parents:
        raise AssetError("source 越出工作区")
    if candidate.is_symlink():
        raise AssetError("source 不能是符号链接")
    if not candidate.is_file():
        raise AssetError(f"source 不存在: {text}")
    if candidate.suffix.lower() not in _IMAGE_EXT:
        raise AssetError("只支持图片资产（png/jpg/webp/gif/bmp/avif/svg）")
    return candidate


def _safe_name(source: Path, name: str) -> str:
    base = str(name or source.name).strip() or source.name
    base = Path(base).name
    base = _SAFE.sub("-", base).strip(".-") or "asset"
    if not base.lower().endswith(source.suffix.lower()):
        base += source.suffix.lower()
    return base[:120]


def _dimensions(path: Path, data: bytes) -> tuple[int, int, str]:
    ext = path.suffix.lower()
    if ext == ".svg":
        try:
            root = ET.fromstring(data)
            width = str(root.get("width") or "")
            height = str(root.get("height") or "")
            w = int(float(re.sub(r"[^0-9.]", "", width) or 0))
            h = int(float(re.sub(r"[^0-9.]", "", height) or 0))
            if (not w or not h) and root.get("viewBox"):
                parts = root.get("viewBox").replace(",", " ").split()
                if len(parts) == 4:
                    w, h = int(float(parts[2])), int(float(parts[3]))
            return w, h, "image/svg+xml"
        except Exception:
            return 0, 0, "image/svg+xml"
    try:
        from PIL import Image
        with Image.open(path) as im:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
                    "GIF": "image/gif", "BMP": "image/bmp"}.get(
                        str(im.format).upper(), "image/png")
            return int(im.width), int(im.height), mime
    except Exception as exc:
        raise AssetError(f"图片无法解码: {exc}") from exc


def import_asset(project: Path, workspace: Path, source: str,
                 name: str = "", role: str = "") -> dict[str, Any]:
    project = project.resolve()
    workspace = workspace.resolve()
    src = _source_path(workspace, source)
    data = src.read_bytes()
    if len(data) > MAX_ASSET_BYTES:
        raise AssetError("图片超过 25 MB 限制")
    sha = hashlib.sha256(data).hexdigest()
    index = load_index(project)
    for row in index.get("assets") or []:
        if row.get("sha256") == sha:
            return {**row, "reused": True}
    width, height, mime = _dimensions(src, data)
    safe = _safe_name(src, name)
    asset_id = "asset_" + sha[:12]
    rel = f"assets/{asset_id}_{safe}"
    target = (project / rel).resolve()
    assets_root = (project / "assets").resolve()
    if assets_root not in target.parents:
        raise AssetError("资产路径越界")
    _atomic(target, data)
    row = {
        "id": asset_id,
        "name": safe,
        "path": rel,
        "source": src.relative_to(workspace).as_posix(),
        "mime": mime,
        "width": width,
        "height": height,
        "bytes": len(data),
        "sha256": sha,
        "role": str(role or "")[:80],
        "version": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    index.setdefault("assets", []).append(row)
    save_index(project, index)
    return {**row, "reused": False}
