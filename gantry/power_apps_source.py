# -*- coding: utf-8 -*-
"""Power Apps Canvas App source 生成器（YAML + CanvasManifest）。

不直接逆向 `.msapp` 二进制：生成 `pac canvas pack` 接受的 unpacked source 目录，
再由可选的 `pac canvas pack --layout SourceCode` 打包。这样 Python 仍然是主体，
`pac` 只是可选外部工具；没装 pac 时仍能产出可审查的 YAML/源码。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import yaml


class PowerAppsSourceError(RuntimeError):
    """生成/打包 Canvas App source 失败。"""


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text or "").strip()).strip("-").lower()
    return s or "canvas-app"


def _ident(text: str, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "", str(text or ""))
    if not s or not s[0].isalpha():
        s = fallback
    return s


def _quote(text: Any) -> str:
    s = str(text if text is not None else "").replace('"', '""')
    return f'="{s}"'


def _num(value: Any, default: float = 0) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    return f"={int(n) if n.is_integer() else n}"


def _control(name: str, ctype: str, spec: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {
        "X": _num(spec.get("x"), 40),
        "Y": _num(spec.get("y"), 40),
        "Width": _num(spec.get("width"), 400),
        "Height": _num(spec.get("height"), 80),
    }
    if "text" in spec:
        props["Text"] = _quote(spec.get("text"))
    if "size" in spec:
        props["Size"] = _num(spec.get("size"), 20)
    if "fill" in spec:
        props["Fill"] = str(spec.get("fill"))
    if "on_select" in spec:
        props["OnSelect"] = str(spec.get("on_select"))
    for key, value in (spec.get("properties") or {}).items():
        props[str(key)] = str(value) if str(value).startswith("=") else _quote(value)
    return {name: {"Control": ctype, "Properties": props}}


def generate_source(name: str, spec: dict[str, Any] | None = None,
                    output_dir: str | Path = "apps") -> dict[str, Any]:
    """生成 Canvas App unpacked source 目录。

    spec:
      display_name / screen_name / screen / controls:
        [{name,type,text,x,y,width,height,size,fill,on_select,properties}]
    """
    spec = dict(spec or {})
    slug = _slug(name)
    display = str(spec.get("display_name") or name or slug)
    screen_name = _ident(str(spec.get("screen_name") or "Home"), "Home")
    controls = list(spec.get("controls") or [])
    if not controls:
        controls = [{"name": "lblTitle", "type": "Label", "text": display,
                     "x": 40, "y": 40, "width": 600, "height": 80, "size": 28}]

    out = Path(output_dir).resolve()
    src = out / "Src"
    src.mkdir(parents=True, exist_ok=True)

    app_yaml = {"App": {"Properties": {"StartScreen": f"={screen_name}"}}}
    if spec.get("on_start"):
        app_yaml["App"]["Properties"]["OnStart"] = str(spec["on_start"])

    screen_props: dict[str, Any] = {
        "Fill": str((spec.get("screen") or {}).get("fill")
                    or "=RGBA(255, 255, 255, 1)")
    }
    children = []
    for i, c in enumerate(controls, 1):
        if not isinstance(c, dict):
            continue
        cname = _ident(str(c.get("name") or f"Control{i}"), f"Control{i}")
        ctype = str(c.get("type") or "Label")
        children.append(_control(cname, ctype, c))
    screen_yaml = {
        "Screens": {
            screen_name: {
                "Properties": screen_props,
                "Children": children,
            }
        }
    }

    manifest = {
        "FormatVersion": "0.24",
        "Properties": {
            "Id": str(uuid.uuid4()),
            "Name": f"gantry_{slug}",
        },
        "PublishInfo": {"AppName": display},
        "DocumentLayoutWidth": int(spec.get("width") or 1366),
        "DocumentLayoutHeight": int(spec.get("height") or 768),
        "DocumentLayoutOrientation": "landscape",
        "AppPreviewFlagsMap": {},
    }

    (out / "CanvasManifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (src / "App.pa.yaml").write_text(
        yaml.safe_dump(app_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (src / f"{screen_name}.pa.yaml").write_text(
        yaml.safe_dump(screen_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (out / "README.md").write_text(
        f"""# {display}

Gantry 生成的 Power Apps Canvas App source。

## 打包

```powershell
pac canvas pack --sources "{out}" --msapp "{out / (slug + '.msapp')}" --layout SourceCode --overwrite
```

> 注意：pac 的 SourceCode pack 要求该 app 先在 Power Apps Studio 打开/验证过。
> 新生成的 source 直接 pack 会报 `must be validated first`；请先从 Studio 导出一个
> baseline `.msapp`，`pac canvas unpack` 后在此基础上修改。

`pac` 是可选的；没安装时本目录仍可作为可审查的 YAML source 使用。

## 文件

- `CanvasManifest.json`
- `Src/App.pa.yaml`
- `Src/{screen_name}.pa.yaml`
""", encoding="utf-8")
    return {"ok": True, "name": slug, "display_name": display,
            "dir": str(out), "screen": screen_name,
            "files": sorted(str(p.relative_to(out)).replace("\\", "/")
                            for p in out.rglob("*") if p.is_file()),
            "manifest": str(out / "CanvasManifest.json")}


def pack_msapp(source_dir: str | Path, msapp: str | Path | None = None,
               pac: str = "", timeout: int = 300) -> dict[str, Any]:
    """调用 `pac canvas pack` 把 source 目录打包成 .msapp。"""
    src = Path(source_dir).resolve()
    if not (src / "CanvasManifest.json").is_file():
        raise PowerAppsSourceError(f"不是 Canvas App source 目录（缺少 CanvasManifest.json）: {src}")
    out = Path(msapp).resolve() if msapp else src.with_suffix(".msapp")
    exe = str(pac or "").strip() or shutil.which("pac") or ""
    if not exe:
        raise PowerAppsSourceError(
            "未找到 pac。安装方式：Windows MSI（powerapps-cli-1.0.msi）"
            "或 dotnet tool install --global Microsoft.PowerApps.CLI.Tool（需要 .NET SDK）")
    cmd = [exe, "canvas", "pack", "--sources", str(src), "--msapp", str(out),
           "--layout", "SourceCode", "--overwrite"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:                     # noqa: BLE001
        raise PowerAppsSourceError(f"pac canvas pack 启动失败: {exc}") from exc
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip()
        if "must be validated first" in detail:
            detail += ("\n提示：pac 要求先有一个在 Power Apps Studio 打开/验证过的 "
                       "baseline .msapp；不能仅凭新生成的 YAML 直接 pack。")
        raise PowerAppsSourceError(
            f"pac canvas pack 失败（exit {p.returncode}）: {detail[:1000]}")
    if not out.is_file():
        raise PowerAppsSourceError(f"pac 返回成功但没有生成 .msapp: {out}")
    return {"ok": True, "source": str(src), "msapp": str(out),
            "size": out.stat().st_size}


def validate_source(source_dir: str | Path) -> dict[str, Any]:
    """轻量校验：manifest、App.pa.yaml、至少一个 Screen 文件。"""
    src = Path(source_dir).resolve()
    problems: list[str] = []
    manifest = src / "CanvasManifest.json"
    if not manifest.is_file():
        problems.append("缺少 CanvasManifest.json")
    else:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not str(data.get("FormatVersion") or ""):
                problems.append("CanvasManifest.FormatVersion 为空")
        except Exception as exc:                 # noqa: BLE001
            problems.append(f"CanvasManifest.json 无法解析: {exc}")
    app = src / "Src" / "App.pa.yaml"
    if not app.is_file():
        problems.append("缺少 Src/App.pa.yaml")
    else:
        try:
            data = yaml.safe_load(app.read_text(encoding="utf-8")) or {}
            if "App" not in data:
                problems.append("App.pa.yaml 缺少 App")
        except Exception as exc:                 # noqa: BLE001
            problems.append(f"App.pa.yaml 无法解析: {exc}")
    screens = sorted((src / "Src").glob("*.pa.yaml"))
    if len(screens) < 2:
        problems.append("Src 下至少需要 App.pa.yaml + 一个 Screen.pa.yaml")
    return {"ok": not problems, "problems": problems, "dir": str(src)}
