# -*- coding: utf-8 -*-
"""visual-design：HTML/CSS/SVG 2D 设计项目插件。

Agent 工具：
- design_create / design_modify / design_render / design_preview /
  design_inspect / design_export

设计文件在 `<workspace>/visual-projects/<project_id>/`，design-spec 与源文件分离。
渲染器只使用 Playwright bundled Chromium + Pillow，不启动 shell，不使用 Copilot Edge。
"""
from __future__ import annotations

import asyncio
import html
import importlib.util
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}\.(png|jpg|jpeg|svg)$", re.I)
_IMAGE_EXTS = (".png", ".jpg", ".jpeg")
_MAX_SOURCE = 2_000_000
_MAX_BRIEF = 20_000
_MAX_OUTPUT = 25_000_000
_MAX_PIXELS = 16_777_216


class DesignError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        self.code = code
        self.hint = hint
        super().__init__(message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self),
                **({"hint": self.hint} if self.hint else {})}


def _load_renderer():
    path = Path(__file__).resolve().parent / "renderer.py"
    spec = importlib.util.spec_from_file_location("gantry_visual_renderer", path)
    if spec is None or spec.loader is None:
        raise DesignError("PLUGIN_BROKEN", "renderer.py 无法加载")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_RENDERER = _load_renderer()


# ── 当前任务工作区 ─────────────────────────────────────────────────────────

def _registry(ctx: Any):
    try:
        from gantry import bridge as b
        # task_tools() 在没有任务上下文时会回退到全局 AGENT_TOOLS；测试/直接
        # 加载插件时应优先 host 注入的 registry（临时工作区）。
        ctx_var = getattr(b, "_TOOLS_CTX", None)
        current = ctx_var.get() if ctx_var is not None else None
        if current is not None:
            return current
    except Exception:
        pass
    return getattr(ctx.host, "registry", None)


def _workspace(ctx: Any) -> Path:
    reg = _registry(ctx)
    root = getattr(reg, "root", None) or getattr(ctx.host.registry, "root", None)
    if not root:
        raise DesignError("WORKSPACE_MISSING", "无法确定当前工作区")
    return Path(root).resolve()


def _guard_write(ctx: Any) -> None:
    reg = _registry(ctx)
    if bool(getattr(reg, "plan_mode", False)):
        raise DesignError("READ_ONLY", "当前处于计划模式（只读），不能修改设计项目")
    if str(getattr(reg, "sandbox_mode", "workspace-write")).lower() == "read-only":
        raise DesignError("READ_ONLY", "当前沙箱为 read-only，不能修改设计项目")


def _projects_root(ctx: Any) -> Path:
    root = _workspace(ctx) / "visual-projects"
    return root


def _project(ctx: Any, project_id: str, *, create: bool = False) -> Path:
    if not _PROJECT_ID_RE.match(str(project_id or "")):
        raise DesignError("INVALID_PROJECT_ID", "project_id 必须是 [a-z0-9][a-z0-9_-]{0,63}")
    root = _projects_root(ctx)
    root.mkdir(parents=True, exist_ok=True)
    project = (root / project_id).resolve()
    if root not in project.parents and project != root:
        raise DesignError("PATH_ESCAPE", "项目路径越界")
    if project.is_symlink():
        raise DesignError("PATH_ESCAPE", "项目目录不能是符号链接")
    if create:
        if project.exists():
            raise DesignError("PROJECT_EXISTS", f"项目已存在: {project_id}")
    elif not project.is_dir():
        raise DesignError("PROJECT_NOT_FOUND", f"项目不存在: {project_id}")
    return project


def _project_sub(project: Path, sub: str) -> Path:
    """项目子目录必须是项目内真实目录，不能是 symlink。"""
    path = project / sub
    try:
        resolved = path.resolve()
        resolved.relative_to(project)
    except ValueError as exc:
        raise DesignError("PATH_ESCAPE", f"{sub} 目录越界") from exc
    if path.is_symlink():
        raise DesignError("PATH_ESCAPE", f"{sub} 目录不能是符号链接")
    return path


def _safe_file(project: Path, name: str, ext: str = "") -> Path:
    name = str(name or "")
    if not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise DesignError("INVALID_FILENAME", "文件名必须是无路径的安全文件名")
    if "/" in name or "\\" in name or ":" in name:
        raise DesignError("INVALID_FILENAME", "文件名不能包含路径分隔符")
    if ext and not name.lower().endswith(ext):
        raise DesignError("INVALID_FILENAME", f"文件名必须以 {ext} 结尾")
    target = (project / name).resolve()
    if project not in target.parents and target != project:
        raise DesignError("PATH_ESCAPE", "文件路径越界")
    return target


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".visual-", dir=str(path.parent))
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


def _read_text(path: Path, limit: int = _MAX_SOURCE) -> str:
    if not path.is_file():
        raise DesignError("SOURCE_MISSING", f"文件不存在: {path.name}")
    if path.stat().st_size > limit:
        raise DesignError("SOURCE_TOO_LARGE", f"文件超过 {limit} 字符")
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        raise DesignError("SOURCE_INVALID", f"读取源文件失败: {exc}") from exc


def _int_arg(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DesignError("INVALID_ARGUMENT", f"{name} 必须是整数")
    if not lo <= value <= hi:
        raise DesignError("INVALID_ARGUMENT", f"{name} 必须在 {lo}..{hi}")
    return value


def _bool_arg(value: Any, name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    raise DesignError("INVALID_ARGUMENT", f"{name} 必须是布尔值")


def _text_arg(value: Any, name: str, max_len: int, required: bool = True) -> str:
    text = str(value or "")
    if required and not text.strip():
        raise DesignError("INVALID_ARGUMENT", f"{name} 不能为空")
    if len(text) > max_len:
        raise DesignError("INVALID_ARGUMENT", f"{name} 超过 {max_len} 字符")
    return text


def _read_spec(project: Path) -> dict[str, Any]:
    path = project / "design-spec.json"
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DesignError("SPEC_INVALID", f"design-spec.json 读取失败: {exc}") from exc
    if not isinstance(spec, dict):
        raise DesignError("SPEC_INVALID", "design-spec.json 必须是对象")
    canvas = spec.get("canvas") or {}
    if not isinstance(canvas, dict):
        raise DesignError("SPEC_INVALID", "design-spec.canvas 必须是对象")
    _int_arg(canvas.get("width"), "canvas.width", 64, 4096)
    _int_arg(canvas.get("height"), "canvas.height", 64, 4096)
    w, h = int(canvas["width"]), int(canvas["height"])
    if w * h > _MAX_PIXELS:
        raise DesignError("CANVAS_TOO_LARGE", "画布像素数超过限制")
    return spec


def _update_spec(project: Path, **changes: Any) -> dict[str, Any]:
    spec = _read_spec(project)
    spec.update(changes)
    spec["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _atomic_write(project / "design-spec.json",
                  (json.dumps(spec, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return spec


# ── 六个工具 ───────────────────────────────────────────────────────────────

async def design_create(ctx: Any, project_id: str = "", title: str = "",
                        width: int = 1920, height: int = 1080,
                        brief: str = "", style: str = "editorial") -> dict[str, Any]:
    _guard_write(ctx)
    title = _text_arg(title, "title", 200)
    width = _int_arg(width, "width", 64, 4096)
    height = _int_arg(height, "height", 64, 4096)
    if width * height > _MAX_PIXELS:
        raise DesignError("CANVAS_TOO_LARGE", "画布像素数超过限制")
    brief = _text_arg(brief, "brief", _MAX_BRIEF, required=False)
    style = str(style or "editorial").lower()
    if style not in ("editorial", "playful", "minimal"):
        raise DesignError("INVALID_ARGUMENT", "style 必须是 editorial/playful/minimal")
    pid = str(project_id or "").strip() or time.strftime("vd-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    project = _project(ctx, pid, create=True)
    for sub in ("source", "assets", "renders", "output", "history"):
        (project / sub).mkdir(parents=True, exist_ok=True)
        _project_sub(project, sub)
    spec = {
        "version": 1, "project_id": pid, "title": title,
        "canvas": {"width": width, "height": height, "background": "#ffffff"},
        "render": {"engine": "chromium", "device_scale_factor": 1,
                   "timeout_seconds": 30, "block_network": True},
        "sources": {"html": "source/index.html", "svg": "source/index.svg"},
        "style": style,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _atomic_write(project / "brief.md", (brief or f"# {title}\n").encode("utf-8"))
    _atomic_write(project / "design-spec.json",
                  (json.dumps(spec, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    safe_title = html.escape(title)
    html_src = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
html,body{{width:{width}px;height:{height}px;margin:0;padding:0;overflow:hidden}}
body{{font-family:"Microsoft YaHei","Microsoft YaHei UI",sans-serif;
background:#fff;display:flex;align-items:center;justify-content:center}}
h1{{font-size:{max(36, min(width, height) // 8)}px;color:#243746;margin:0}}
</style></head><body><h1>{safe_title}</h1></body></html>
"""
    svg_src = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#ffffff"/>
<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle"
 font-family="Microsoft YaHei" font-size="{max(36, min(width, height) // 8)}" fill="#243746">{safe_title}</text>
</svg>
"""
    _atomic_write(project / "source" / "index.html", html_src.encode("utf-8"))
    _atomic_write(project / "source" / "index.svg", svg_src.encode("utf-8"))
    return {"ok": True, "project_id": pid,
            "project": project.relative_to(_workspace(ctx)).as_posix(),
            "spec": spec,
            "sources": ["source/index.html", "source/index.svg"]}


def _source_for(project: Path, target: str) -> Path:
    target = str(target or "html").lower()
    if target == "html":
        source_dir = _project_sub(project, "source")
        path = source_dir / "index.html"
    elif target == "svg":
        source_dir = _project_sub(project, "source")
        path = source_dir / "index.svg"
    else:
        raise DesignError("INVALID_TARGET", "target 必须是 html 或 svg")
    if path.is_symlink():
        raise DesignError("PATH_ESCAPE", "源文件不能是符号链接")
    try:
        path.resolve().relative_to(project)
    except ValueError as exc:
        raise DesignError("PATH_ESCAPE", "源文件路径越界") from exc
    return path


async def design_modify(ctx: Any, project_id: str, target: str = "html",
                        mode: str = "replace", content: str = "",
                        search: str = "") -> dict[str, Any]:
    _guard_write(ctx)
    project = _project(ctx, project_id)
    source = _source_for(project, target)
    content = _text_arg(content, "content", _MAX_SOURCE)
    mode = str(mode or "replace").lower()
    if mode not in ("replace", "replace_text", "append"):
        raise DesignError("INVALID_ARGUMENT", "mode 必须是 replace/replace_text/append")
    current = _read_text(source)
    if mode == "replace":
        new_text = content
    elif mode == "append":
        new_text = current + content
    else:
        needle = _text_arg(search, "search", 2000)
        count = current.count(needle)
        if count != 1:
            raise DesignError("REPLACE_NOT_UNIQUE",
                              f"search 必须恰好出现一次，实际 {count} 次")
        new_text = current.replace(needle, content, 1)
    if len(new_text.encode("utf-8")) > _MAX_SOURCE:
        raise DesignError("SOURCE_TOO_LARGE", "修改后的源文件超过 2 MB")
    stem = "index.html" if target == "html" else "index.svg"
    history_dir = _project_sub(project, "history")
    backup = history_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}-{stem}"
    (history_dir).mkdir(exist_ok=True)
    try:
        shutil.copyfile(source, backup)
    except Exception as exc:
        raise DesignError("HISTORY_FAILED", f"保存历史失败: {exc}") from exc
    _atomic_write(source, new_text.encode("utf-8"))
    _update_spec(project)
    return {"ok": True, "project_id": project_id, "target": target,
            "source": source.relative_to(project).as_posix(),
            "bytes": source.stat().st_size,
            "history": backup.relative_to(project).as_posix()}


def _render_path(project: Path, name: str, sub: str) -> Path:
    name = str(name or "").strip()
    if not name:
        raise DesignError("INVALID_FILENAME", "output 不能为空")
    if not _FILE_RE.match(name) or not name.lower().endswith(".png"):
        raise DesignError("INVALID_FILENAME", "output 必须是安全的 .png 文件名")
    base = _project_sub(project, sub)
    raw = base / name
    if raw.is_symlink():
        raise DesignError("PATH_ESCAPE", "输出不能是符号链接")
    target = raw.resolve()
    if base.resolve() not in target.parents:
        raise DesignError("PATH_ESCAPE", "输出路径越界")
    return target


async def design_render(ctx: Any, project_id: str, target: str = "html",
                        output: str = "") -> dict[str, Any]:
    _guard_write(ctx)
    project = _project(ctx, project_id)
    spec = _read_spec(project)
    source = _source_for(project, target)
    if not source.is_file():
        raise DesignError("SOURCE_MISSING", f"源文件不存在: {source.name}")
    name = output or f"render-{target}.png"
    out = _render_path(project, name, "renders")
    canvas = spec["canvas"]
    timeout = float((spec.get("render") or {}).get("timeout_seconds") or 30)
    timeout = max(0.01, min(120.0, timeout))
    try:
        info = await _RENDERER.render(project, target, int(canvas["width"]),
                                      int(canvas["height"]), timeout, out)
    except _RENDERER.RenderError as exc:
        raise DesignError(exc.code, str(exc), exc.hint) from exc
    return {"ok": True, "project_id": project_id, "target": target,
            "png": out.relative_to(project).as_posix(), **info}


async def design_preview(ctx: Any, project_id: str, target: str = "html",
                         max_side: int = 1024, output: str = "") -> dict[str, Any]:
    _guard_write(ctx)
    project = _project(ctx, project_id)
    spec = _read_spec(project)
    max_side = _int_arg(max_side, "max_side", 64, 2048)
    name = output or f"preview-{target}.png"
    out = _render_path(project, name, "renders")
    full = project / "renders" / f".full-{uuid.uuid4().hex}.png"
    try:
        try:
            await _RENDERER.render(project, target, int(spec["canvas"]["width"]),
                                   int(spec["canvas"]["height"]),
                                   float((spec.get("render") or {}).get("timeout_seconds") or 30),
                                   full)
            info = _RENDERER.make_preview(full, out, max_side)
        except _RENDERER.RenderError as exc:
            raise DesignError(exc.code, str(exc), exc.hint) from exc
    finally:
        try:
            full.unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True, "project_id": project_id, "target": target,
            "preview": out.relative_to(project).as_posix(), **info}


async def design_inspect(ctx: Any, project_id: str) -> dict[str, Any]:
    project = _project(ctx, project_id)
    spec = _read_spec(project)
    issues: list[str] = []
    sources = []
    for target, rel in (("html", "source/index.html"), ("svg", "source/index.svg")):
        try:
            p = _source_for(project, target)
            exists, size = p.is_file(), p.stat().st_size if p.is_file() else 0
        except DesignError as exc:
            p, exists, size = project / rel, False, 0
            issues.append(f"{rel}: {exc}")
        sources.append({"path": rel, "exists": exists, "bytes": size})
        if not exists and not any(str(exc).startswith(rel) for exc in issues):
            issues.append(f"缺少 {rel}")
    renders = []
    for p in sorted((project / "renders").glob("*.png")):
        try:
            info = _RENDERER.inspect_png(p)
            renders.append({**info, "path": p.relative_to(project).as_posix()})
            if p.name.startswith("preview-"):
                # 预览图有意缩放，不应按最终画布尺寸报错。
                continue
            if (info["width"], info["height"]) != (
                    int(spec["canvas"]["width"]), int(spec["canvas"]["height"])):
                issues.append(f"{p.name} 尺寸 {info['width']}x{info['height']} 与 spec 不一致")
        except _RENDERER.RenderError as exc:
            issues.append(f"{p.name}: {exc}")
    outputs = []
    for p in sorted((project / "output").glob("*")):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg"):
            try:
                outputs.append(_RENDERER.inspect_png(p))
            except _RENDERER.RenderError as exc:
                issues.append(f"{p.name}: {exc}")
    return {"ok": not issues, "project_id": project_id, "spec": spec,
            "sources": sources, "renders": renders, "outputs": outputs,
            "issues": issues}


async def design_export(ctx: Any, project_id: str, format: str = "png",
                        filename: str = "", source: str = "html",
                        overwrite: bool = False) -> dict[str, Any]:
    _guard_write(ctx)
    project = _project(ctx, project_id)
    overwrite = _bool_arg(overwrite, "overwrite", False)
    fmt = str(format or "png").lower()
    if fmt not in ("png", "jpg", "jpeg", "svg"):
        raise DesignError("INVALID_FORMAT", "format 必须是 png/jpg/svg（本阶段不支持 PDF）")
    fmt = "jpg" if fmt == "jpeg" else fmt
    source = str(source or "html").lower()
    if source not in ("html", "svg"):
        raise DesignError("INVALID_ARGUMENT", "source 必须是 html 或 svg")
    name = filename or f"design.{fmt}"
    if not _FILE_RE.match(name) or not name.lower().endswith("." + fmt):
        raise DesignError("INVALID_FILENAME", f"filename 必须是安全的 .{fmt} 文件名")
    output_dir = _project_sub(project, "output")
    raw = output_dir / name
    if raw.is_symlink():
        raise DesignError("PATH_ESCAPE", "输出不能是符号链接")
    target = raw.resolve()
    if output_dir.resolve() not in target.parents:
        raise DesignError("PATH_ESCAPE", "输出路径越界")
    if target.exists() and not overwrite:
        raise DesignError("OUTPUT_EXISTS", f"输出已存在: {name}（需要 overwrite=true）")
    if fmt == "svg":
        src = _source_for(project, "svg")
        if not src.is_file():
            raise DesignError("SOURCE_MISSING", "缺少 source/index.svg")
        _atomic_write(target, src.read_bytes())
        if target.stat().st_size > _MAX_OUTPUT:
            target.unlink(missing_ok=True)
            raise DesignError("OUTPUT_TOO_LARGE", "SVG 输出超过 25 MB")
        return {"ok": True, "path": target.relative_to(project).as_posix(),
                "format": "svg", "bytes": target.stat().st_size}
    render_dir = _project_sub(project, "renders")
    src = render_dir / f"render-{str(source or 'html').lower()}.png"
    if src.is_symlink() or not src.is_file():
        raise DesignError("RENDER_MISSING", f"找不到 {src.name}；先 design_render",
                          "先调用 design_render(target=html|svg)")
    try:
        if fmt == "png":
            _atomic_write(target, src.read_bytes())
            info = _RENDERER.inspect_png(target)
        else:
            info = _RENDERER.convert_jpg(src, target)
    except _RENDERER.RenderError as exc:
        raise DesignError(exc.code, str(exc), exc.hint) from exc
    if target.stat().st_size > _MAX_OUTPUT:
        target.unlink(missing_ok=True)
        raise DesignError("OUTPUT_TOO_LARGE", "导出文件超过 25 MB")
    return {"ok": True, "path": target.relative_to(project).as_posix(),
            "format": fmt, **info}


# ── 插件生命周期 ───────────────────────────────────────────────────────────

def setup(ctx: Any) -> None:
    ctx.register_tool(
        "design_create", lambda **kw: design_create(ctx, **kw),
        "Create a 2D design project (HTML/SVG) under visual-projects/<id>/",
        {"project_id": "safe slug, optional", "title": "design title",
         "width": "canvas width 64..4096", "height": "canvas height 64..4096",
         "brief": "optional brief, <=20000 chars",
         "style": "editorial|playful|minimal"})
    ctx.register_tool(
        "design_modify", lambda **kw: design_modify(ctx, **kw),
        "Modify the editable HTML or SVG source of a design project",
        {"project_id": "project slug", "target": "html|svg",
         "mode": "replace|replace_text|append", "content": "new source/text",
         "search": "exact single occurrence for replace_text"})
    ctx.register_tool(
        "design_render", lambda **kw: design_render(ctx, **kw),
        "Render HTML or SVG to PNG using bundled Chromium",
        {"project_id": "project slug", "target": "html|svg",
         "output": "optional safe .png name under renders/"})
    ctx.register_tool(
        "design_preview", lambda **kw: design_preview(ctx, **kw),
        "Render a downscaled preview PNG (longest side <= max_side)",
        {"project_id": "project slug", "target": "html|svg",
         "max_side": "64..2048, default 1024",
         "output": "optional safe .png name under renders/"})
    ctx.register_tool(
        "design_inspect", lambda **kw: design_inspect(ctx, **kw),
        "Inspect design spec, sources, rendered PNG sizes and outputs (read-only)",
        {"project_id": "project slug"})
    ctx.register_tool(
        "design_export", lambda **kw: design_export(ctx, **kw),
        "Export final PNG/JPG/SVG from the project (PDF not supported in this phase)",
        {"project_id": "project slug", "format": "png|jpg|svg",
         "filename": "safe output filename", "source": "html|svg render",
         "overwrite": "bool, default false"})
    ctx.log("visual-design 已就绪（六工具，HTML/SVG 渲染）")


def teardown(ctx: Any) -> None:
    # 渲染器每次调用自行关闭 Chromium；这里没有持久浏览器需要清理。
    pass
