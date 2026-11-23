# -*- coding: utf-8 -*-
"""Gantry 自身更新：检查 GitHub 版本、git/zip 两种安装方式、备份与回滚。

设计约束：
- 只允许配置里的 GitHub repo，不接受任意 URL；
- 默认只检查、不自动安装；安装必须显式调用；
- git 模式要求工作区干净，zip 模式只替换代码，不碰 data/ workspace/ .venv/ EdgeProfile/ config.json；
- 安装前先备份，失败/手动可回滚；
- 不打印/返回 token。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .subproc import hidden_kwargs
from .version import VERSION, compare, parse, sort_key

log = logging.getLogger("gantry.updater")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "ogowoo/gantry"
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "repo": DEFAULT_REPO,
    "channel": "beta",
    "method": "auto",              # auto / git / zip
    "line": "0",                   # 0.x 正式线；旧 11.x 标签不参与更新
    "token_env": "GITHUB_TOKEN",
    "max_download_mb": 200,
    "allow_dirty": False,
}
PROTECTED = {
    "data", "workspace", ".venv", ".git", "EdgeProfile", "config.json",
    "node_modules", "logs", "__pycache__", "for debug", ".tools",
}
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TAG_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")


class UpdateError(RuntimeError):
    """检查/安装/回滚更新失败。"""


def _now() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                            # noqa: BLE001
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Updater:
    def __init__(self, root: str | Path | None = None, config: dict[str, Any] | None = None,
                 current: str | None = None):
        self.root = Path(root or ROOT).resolve()
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({k: v for k, v in (config or {}).items() if v is not None})
        repo = str(cfg.get("repo") or "").strip()
        if not _REPO_RE.match(repo):
            raise UpdateError(f"update.repo 不合法（应为 owner/name）: {repo!r}")
        method = str(cfg.get("method") or "auto").lower()
        if method not in ("auto", "git", "zip"):
            raise UpdateError("update.method 只能是 auto/git/zip")
        self.config = cfg
        self.config["repo"] = repo
        self.config["method"] = method
        self.current = str(current or VERSION)
        self.update_dir = self.root / "data" / "updates"
        self._lock = threading.Lock()

    # ── 基础信息 ────────────────────────────────────────────────
    @property
    def repo(self) -> str:
        return str(self.config["repo"])

    @property
    def channel(self) -> str:
        return str(self.config.get("channel") or "beta").lower()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def _token(self) -> str:
        env = str(self.config.get("token_env") or "GITHUB_TOKEN")
        return str(os.environ.get(env) or os.environ.get("GH_TOKEN") or "").strip()

    def _git_exe(self) -> str:
        return shutil.which("git") or ""

    def _git(self, args: list[str], timeout: int = 60) -> str:
        exe = self._git_exe()
        if not exe:
            raise UpdateError("未找到 git")
        env = dict(os.environ)
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GCM_INTERACTIVE", "never")
        try:
            p = subprocess.run([exe, *args], cwd=self.root, capture_output=True,
                               text=True, errors="replace", timeout=timeout,
                               env=env, **hidden_kwargs())
        except Exception as exc:                 # noqa: BLE001
            raise UpdateError(f"git {' '.join(args)} 启动失败: {exc}") from exc
        if p.returncode != 0:
            detail = (p.stderr or p.stdout or "").strip()[:500]
            raise UpdateError(f"git {' '.join(args)} 失败（exit {p.returncode}）: {detail}")
        return (p.stdout or "").strip()

    def _allowed(self, version: str) -> bool:
        p = parse(version)
        line = str(self.config.get("line") or "").strip()
        if line and str(p["major"]) != line:
            return False
        ch = self.channel
        if ch == "stable":
            return p["stable"]
        if ch == "beta":
            return p["channel"] in ("beta", "") or p["stable"]
        if ch == "preview":
            return p["channel"] in ("preview", "beta", "") or p["stable"]
        return True

    def _versions_from_git(self) -> list[str]:
        try:
            out = self._git(["ls-remote", "--tags", "--refs", "origin"], timeout=25)
        except UpdateError:
            out = self._git(["tag", "--list"], timeout=15)
        versions: set[str] = set()
        for line in out.splitlines():
            ref = line.rsplit("\t", 1)[-1].strip()
            tag = ref.replace("refs/tags/", "")
            m = _TAG_RE.match(tag)
            if m:
                versions.add(m.group(1))
        return sorted(versions, key=sort_key)

    def _api(self, path: str) -> Any:
        url = f"https://api.github.com/repos/{self.repo}{path}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "Gantry-Updater"}
        tok = self._token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            hint = "（私有仓库需设置 GITHUB_TOKEN）" if exc.code in (401, 404) else ""
            raise UpdateError(f"GitHub API {exc.code}{hint}") from exc
        except Exception as exc:                 # noqa: BLE001
            raise UpdateError(f"GitHub API 请求失败: {exc}") from exc

    def _release_info(self, tag: str) -> dict[str, Any]:
        data = self._api("/releases?per_page=30")
        releases = data if isinstance(data, list) else []
        for rel in releases:
            if str(rel.get("tag_name") or "") != f"v{tag}" and \
                    str(rel.get("tag_name") or "") != tag:
                continue
            assets = rel.get("assets") or []
            pick = None
            for a in assets:
                name = str(a.get("name") or "")
                if name.lower().endswith(".zip") and not name.lower().endswith(".sha256"):
                    pick = a
                    if "gantry" in name.lower() or "source" in name.lower():
                        break
            url = (pick or {}).get("browser_download_url") or rel.get("zipball_url")
            if not url:
                continue
            return {"tag": tag, "version": tag, "url": url,
                    "asset": (pick or {}).get("name") or "",
                    "size": int((pick or {}).get("size") or 0),
                    "sha256": self._digest((pick or {}).get("digest")),
                    "notes": str(rel.get("body") or "")[:4000],
                    "html_url": rel.get("html_url") or "", "source": "release"}
        # 没发布 release：退到 tag 源码包
        return {"tag": tag, "version": tag,
                "url": f"https://github.com/{self.repo}/archive/refs/tags/v{tag}.zip",
                "asset": f"v{tag}.zip", "size": 0, "sha256": "",
                "notes": "", "html_url": f"https://github.com/{self.repo}/tree/v{tag}",
                "source": "tag"}

    @staticmethod
    def _digest(raw: Any) -> str:
        s = str(raw or "").strip().lower()
        if s.startswith("sha256:"):
            s = s.split(":", 1)[1]
        return s if re.fullmatch(r"[0-9a-f]{64}", s) else ""

    # ── 检查 ────────────────────────────────────────────────────
    def check(self, method: str = "") -> dict[str, Any]:
        if not self.enabled:
            raise UpdateError("更新系统未启用（update.enabled=false）")
        want = (method or self.config["method"]).lower()
        if want not in ("auto", "git", "zip"):
            raise UpdateError("method 只能是 auto/git/zip")
        errors: list[str] = []
        if want in ("auto", "git") and self._git_exe() and (self.root / ".git").is_dir():
            try:
                versions = self._versions_from_git()
                allowed = [v for v in versions if self._allowed(v)]
                if allowed:
                    latest = max(allowed, key=sort_key)
                    return self._check_payload(latest, method="git",
                                               notes="", errors=errors)
            except UpdateError as exc:
                errors.append(str(exc))
                if want == "git":
                    raise
        if want in ("auto", "zip") or not self._git_exe():
            try:
                tags = self._api("/tags?per_page=100")
                versions = []
                for t in (tags if isinstance(tags, list) else []):
                    m = _TAG_RE.match(str(t.get("name") or ""))
                    if m and self._allowed(m.group(1)):
                        versions.append(m.group(1))
                if versions:
                    latest = max(versions, key=sort_key)
                    info = self._release_info(latest)
                    return self._check_payload(latest, method="zip",
                                               notes=info.get("notes", ""), errors=errors,
                                               extra=info)
            except UpdateError as exc:
                errors.append(str(exc))
        latest = self.current
        return {
            "ok": True, "current": self.current, "latest": latest,
            "update_available": False, "method": want,
            "channel": self.channel, "repo": self.repo,
            "errors": errors, "checked_at": _now(),
        }

    def _check_payload(self, latest: str, method: str, notes: str,
                       errors: list[str], extra: dict | None = None) -> dict[str, Any]:
        out = {
            "ok": True, "current": self.current, "latest": latest,
            "update_available": compare(latest, self.current) > 0,
            "method": method, "channel": self.channel, "repo": self.repo,
            "notes": notes[:2000], "errors": errors, "checked_at": _now(),
        }
        out.update(extra or {})
        return out

    # ── 状态与备份 ──────────────────────────────────────────────
    def backups(self) -> list[dict[str, Any]]:
        rows = []
        for p in sorted((self.update_dir / "backups").glob("*/backup.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            d = _read_json(p, {}) or {}
            d["id"] = p.parent.name
            rows.append(d)
        return rows

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "current": self.current,
            "repo": self.repo, "channel": self.channel,
            "method": self.config["method"],
            "git": bool(self._git_exe() and (self.root / ".git").is_dir()),
            "token": bool(self._token()),
            "backups": self.backups()[:10],
        }

    def _new_backup(self, kind: str, **meta: Any) -> tuple[Path, dict[str, Any]]:
        bdir = self.update_dir / "backups" / f"{_now()}-{kind}"
        bdir.mkdir(parents=True, exist_ok=False)
        record = {"id": bdir.name, "kind": kind, "created": _now(),
                  "current": self.current, "root": str(self.root), **meta}
        return bdir, record

    def _save_backup(self, bdir: Path, record: dict[str, Any]) -> None:
        _write_json(bdir / "backup.json", record)

    def _prune_backups(self, keep: int = 5) -> None:
        """apply 成功后淘汰旧备份，只保留最近 keep 份。

        备份目录名 = {_now()}-{kind}（时间戳前缀），按目录名排序即按时间排序；
        超出的整体删除（backup.json 与备份内容一起）。
        """
        bdir = self.update_dir / "backups"
        if not bdir.is_dir():
            return
        dirs = sorted((p for p in bdir.iterdir() if p.is_dir()),
                      key=lambda p: p.name)
        for old in dirs[:-keep] if len(dirs) > keep else []:
            shutil.rmtree(old, ignore_errors=True)

    # ── 安装 ────────────────────────────────────────────────────
    def apply(self, version: str = "", method: str = "", restart: bool = True,
              dry_run: bool = False, force: bool = False) -> dict[str, Any]:
        if not self.enabled:
            raise UpdateError("更新系统未启用（update.enabled=false）")
        with self._lock:
            check = self.check(method=method)
            target = str(version or check.get("latest") or "")
            if not target:
                raise UpdateError("没有可安装的目标版本")
            if compare(target, self.current) <= 0 and not force:
                return {"ok": True, "changed": False, "current": self.current,
                        "target": target, "message": "已是最新版本"}
            plan_method = check.get("method") if not method else method
            if plan_method == "auto":
                plan_method = "git" if self._git_exe() and (self.root / ".git").is_dir() else "zip"
            if dry_run:
                return {"ok": True, "dry_run": True, "current": self.current,
                        "target": target, "method": plan_method,
                        "plan": check}
            if plan_method == "git":
                res = self._apply_git(target, force=force)
            else:
                res = self._apply_zip(target, force=force)
            res.update({"ok": True, "current_before": self.current,
                        "target": target, "method": plan_method,
                        "restart_required": bool(restart)})
            if res.get("changed"):
                self._prune_backups(keep=5)   # 每次成功安装后淘汰，只留最近 5 份备份
            if restart:
                self._schedule_restart()
            return res

    def _schedule_restart(self) -> None:
        try:
            from . import lifecycle
            lifecycle.schedule("restart", 1.2)
        except Exception as exc:                 # noqa: BLE001
            log.warning("无法排定重启: %s", exc)

    def _resolve_tag_commit(self, target: str, branch: str,
                            force: bool = False) -> tuple[str, bool]:
        """解析目标 tag 对应的 commit，返回 (commit, fallback)。

        为什么需要单独做：更新器**只 fetch 分支、不 fetch 全部 tag**（全量
        `git fetch --tags` 在本地 tag 与远端分叉时会以 "would clobber existing
        tag" 失败退出）。于是「本地没有该 tag」是常态——尤其异机全新 clone 后
        第一次更新时，本地一个 v0.11 的 tag 都没有，`rev-parse refs/tags/...`
        直接 exit 128。

        顺序：本地 → 强制抓单个 tag（`+refs/tags/x:refs/tags/x`，"+" 允许覆盖
        已存在的同名 tag，因此不会再撞 clobber）→ 远端分支头兜底。

        兜底策略：target 是明确版本号（v1.2.3 形态可解析）时，静默退到远端
        分支头意味着实际安装的并非所请求的 tag，禁止该兜底、直接报错；
        仅 force=True 允许兜底，且返回 fallback=True 让调用方在结果中标记。
        """
        ref = f"refs/tags/v{target}"
        try:
            out = self._git(["rev-parse", "--verify", f"{ref}^{{commit}}"], timeout=20)
            if out:
                return out, False
        except UpdateError:
            pass                                     # 本地没有这个 tag：正常
        try:
            self._git(["fetch", "--force", "origin", f"+{ref}:{ref}"], timeout=90)
            out = self._git(["rev-parse", "--verify", f"{ref}^{{commit}}"], timeout=20)
            if out:
                return out, False
        except UpdateError as exc:
            log.warning("抓取 tag v%s 失败（改用远端分支头）: %s", target, exc)
        explicit_tag = bool(_TAG_RE.match(str(target)))
        if explicit_tag and not force:
            raise UpdateError(
                f"无法解析 v{target} 的提交：本地与远端都没有对应 tag。"
                f"目标是明确版本号，拒绝静默回退到远端分支头"
                f"（那样安装的并非所请求的版本）；如确需兜底请显式 force=True")
        remote_ref = f"refs/remotes/origin/{branch}"
        try:
            out = self._git(["rev-parse", remote_ref], timeout=20)
            if out:
                log.warning("未解析到 tag v%s，改用 %s（%s）", target, remote_ref, out[:12])
                return out, True
        except UpdateError:
            pass
        raise UpdateError(f"无法解析 v{target} 的提交：本地与远端都没有对应 tag，且远端分支不可用")

    def _apply_git(self, target: str, *, force: bool = False) -> dict[str, Any]:
        if not (self.root / ".git").is_dir():
            raise UpdateError("不是 git 仓库，不能用 git 更新")
        # --untracked-files=no：只看已跟踪文件的改动。git clone 下来的仓库里
        # data/ workspace/ __pycache__/ 等未跟踪目录会让 --porcelain 输出 ??，
        # 误判为"工作区有未提交改动"而拒绝更新（实测：全新 clone 后启动服务再检查更新就报这个错）。
        dirty = self._git(["status", "--porcelain", "--untracked-files=no"], timeout=20)
        if dirty and not (force or self.config.get("allow_dirty")):
            raise UpdateError("工作区有未提交改动；先 commit/stash，或显式 force")
        branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"], timeout=10) or "main"
        previous = self._git(["rev-parse", "HEAD"], timeout=10)
        # 只 fetch 分支：`git fetch --tags` 在 tag 分叉时会 "would clobber existing tag"
        # 失败（exit 1）；需要的 tag 由 _resolve_tag_commit 单独强制抓取。
        self._git(["fetch", "origin", branch], timeout=90)
        commit, fallback = self._resolve_tag_commit(target, branch, force=force)
        bdir, record = self._new_backup("git", previous_commit=previous, branch=branch,
                                        target=target, target_commit=commit)
        self._save_backup(bdir, record)
        try:
            self._git(["merge", "--ff-only", commit], timeout=120)
        except UpdateError as exc:
            raise UpdateError(
                f"无法快进到 v{target}：本地 {branch} 可能已有自己的提交、或与远端分叉。"
                f"（{exc}）\n可在安装目录手动执行：git fetch origin && "
                f"git reset --hard v{target}，然后再更新依赖。") from exc
        res = {"changed": True, "backup": bdir.name,
               "previous_commit": previous[:12], "target_commit": commit[:12],
               "branch": branch}
        if fallback:
            # 安装的不是所请求 tag 的提交，而是远端分支头——必须显式暴露给调用方。
            res["fallback"] = True
        return res

    def _apply_zip(self, target: str, *, force: bool = False) -> dict[str, Any]:
        info = self._release_info(target)
        url = str(info.get("url") or "")
        if not url.startswith(("https://github.com/", "https://api.github.com/",
                               "https://objects.githubusercontent.com/",
                               "https://codeload.github.com/")):
            raise UpdateError("下载地址不是受信任的 GitHub 地址")
        downloads = self.update_dir / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        zpath = downloads / f"{target}.zip"
        self._download(url, zpath)
        expected = self._digest(info.get("sha256"))
        got = _sha256(zpath)
        if not expected:
            # release 未提供 digest 时不能静默跳过校验——那样等于完全不校验。
            # 默认拒绝安装，仅显式 force=True 放行（调用方自担风险）。
            if not force:
                raise UpdateError(
                    f"release v{target} 未提供 SHA256 digest，拒绝安装；"
                    f"确认来源可信后可显式传 force=True 放行")
        elif expected != got:
            raise UpdateError("下载文件 SHA256 与 release digest 不一致")
        staging = self.update_dir / "staging" / f"{target}-{_now()}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        self._safe_extract(zpath, staging)
        source_root = self._find_app_root(staging)
        if not source_root:
            raise UpdateError("压缩包中找不到 gantry/version.py")
        new_version = self._version_of(source_root)
        if new_version != target:
            raise UpdateError(f"压缩包版本 {new_version!r} 与目标 {target!r} 不一致")
        if not (source_root / "start.py").is_file() or not (source_root / "gantry").is_dir():
            raise UpdateError("压缩包不是完整 Gantry 源码")
        bdir, record = self._new_backup("zip", target=target, source="zip")
        code = bdir / "code"
        code.mkdir(parents=True, exist_ok=True)
        entries = []
        for name in self._code_entries(self.root):
            src = self.root / name
            dst = code / name
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            entries.append(name)
        record["entries"] = entries
        self._save_backup(bdir, record)
        new_entries = self._code_entries(source_root)
        try:
            for name in entries:
                if name not in new_entries:
                    self._remove_path(self.root / name)
            for name in new_entries:
                dst = self.root / name
                src = source_root / name
                self._remove_path(dst)
                if src.is_dir():
                    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    shutil.copy2(src, dst)
        except Exception as exc:                 # noqa: BLE001
            self._restore_zip(bdir, record)
            raise UpdateError(f"替换代码失败，已尝试恢复备份: {exc}") from exc
        return {"changed": True, "backup": bdir.name, "entries": new_entries,
                "source": info.get("source", "zip"), "sha256": got}

    def _download(self, url: str, dest: Path) -> None:
        headers = {"User-Agent": "Gantry-Updater"}
        tok = self._token()
        if tok and ("api.github.com" in url or "github.com" in url):
            headers["Authorization"] = f"Bearer {tok}"
        req = urllib.request.Request(url, headers=headers)
        max_bytes = int(self.config.get("max_download_mb") or 200) * 1024 * 1024
        try:
            with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:
                total = 0
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateError(f"下载超过上限 {max_bytes // (1024*1024)}MB")
                    f.write(chunk)
        except UpdateError:
            raise
        except Exception as exc:                 # noqa: BLE001
            raise UpdateError(f"下载失败: {exc}") from exc
        if not dest.is_file() or dest.stat().st_size == 0:
            raise UpdateError("下载文件为空")

    @staticmethod
    def _safe_extract(zpath: Path, dest: Path) -> None:
        max_files = 20000
        max_total = 400 * 1024 * 1024
        total = 0
        with zipfile.ZipFile(zpath) as z:
            infos = z.infolist()
            if len(infos) > max_files:
                raise UpdateError("压缩包文件数过多")
            for info in infos:
                name = info.filename.replace("\\", "/")
                parts = [p for p in name.split("/") if p not in ("", ".")]
                if name.startswith("/") or ".." in parts:
                    raise UpdateError(f"压缩包路径不安全: {name}")
                total += max(0, int(info.file_size))
                if total > max_total:
                    raise UpdateError("压缩包解压后过大")
                if info.is_dir():
                    continue
                target = dest.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)

    @staticmethod
    def _find_app_root(staging: Path) -> Path | None:
        if (staging / "gantry" / "version.py").is_file():
            return staging
        for p in staging.iterdir():
            if p.is_dir() and (p / "gantry" / "version.py").is_file():
                return p
        return None

    @staticmethod
    def _version_of(root: Path) -> str:
        try:
            text = (root / "gantry" / "version.py").read_text(encoding="utf-8")
            m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
            return m.group(1) if m else ""
        except Exception:                        # noqa: BLE001
            return ""

    @staticmethod
    def _code_entries(root: Path) -> list[str]:
        out = []
        for p in root.iterdir():
            name = p.name
            if name in PROTECTED or name.startswith("."):
                continue
            if p.is_dir() and name in ("data", "workspace", ".venv", "EdgeProfile"):
                continue
            out.append(name)
        return sorted(out)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    def _restore_zip(self, bdir: Path, record: dict[str, Any]) -> dict[str, Any]:
        code = bdir / "code"
        if not code.is_dir():
            raise UpdateError(f"备份不完整: {bdir}")
        entries = list(record.get("entries") or [n.name for n in code.iterdir()])
        current = self._code_entries(self.root)
        for name in current:
            if name not in entries:
                self._remove_path(self.root / name)
        for name in entries:
            dst = self.root / name
            src = code / name
            self._remove_path(dst)
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif src.is_file():
                shutil.copy2(src, dst)
        return {"ok": True, "restored_from": bdir.name, "entries": entries}

    # ── 回滚 ────────────────────────────────────────────────────
    def rollback(self, backup: str = "") -> dict[str, Any]:
        # 与 apply() 同一把锁：apply 持锁拷贝文件期间 rollback 若并发执行，
        # 会在文件树拷贝到一半时 git reset / 解 zip，得到混杂的安装（实测风险点）。
        with self._lock:
            return self._rollback_locked(backup)

    def _rollback_locked(self, backup: str = "") -> dict[str, Any]:
        rows = self.backups()
        if not rows:
            raise UpdateError("没有可回滚的更新备份")
        record = None
        if backup:
            record = next((r for r in rows if r.get("id") == backup), None)
            if not record:
                raise UpdateError(f"找不到备份: {backup}")
        else:
            record = rows[0]
        bdir = self.update_dir / "backups" / str(record["id"])
        if record.get("kind") == "git":
            prev = str(record.get("previous_commit") or "")
            if not prev:
                raise UpdateError("git 备份缺少 previous_commit")
            self._git(["reset", "--hard", prev], timeout=60)
            out = {"ok": True, "method": "git", "restored": prev[:12]}
        elif record.get("kind") == "zip":
            out = self._restore_zip(bdir, record)
            out["method"] = "zip"
        else:
            raise UpdateError(f"未知备份类型: {record.get('kind')}")
        out["backup"] = record["id"]
        out["restart_required"] = True
        return out


def from_config(root: str | Path | None = None, config: dict[str, Any] | None = None) -> Updater:
    """从项目 config.json 的 update 段构造 Updater；缺省用默认值。"""
    cfg = config
    if cfg is None:
        data = _read_json(Path(root or ROOT) / "config.json", {}) or {}
        cfg = data.get("update") if isinstance(data.get("update"), dict) else {}
    return Updater(root=root, config=cfg or {})
