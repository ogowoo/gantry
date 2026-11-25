# -*- coding: utf-8 -*-
"""更新系统：版本排序、git 检查、zip 替换/回滚与安全解压。"""
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry import updater as u
from gantry.version import compare, is_newer, sort_key


class VersionCompareTests(unittest.TestCase):
    def test_order(self):
        self.assertGreater(compare("0.11.0", "0.11.0-beta.2"), 0)
        self.assertGreater(compare("0.11.0-beta.2", "0.11.0-beta.1"), 0)
        self.assertGreater(compare("0.11.0-preview.1", "0.11.0-beta.9"), 0)
        self.assertEqual(compare("0.11.0-beta.1", "0.11.0-beta.1"), 0)
        self.assertLess(compare("0.11.0-beta.1", "0.11.0"), 0)
        self.assertTrue(is_newer("0.12.0-beta.1", "0.11.0-beta.1"))


class UpdaterCheckTests(unittest.TestCase):
    def test_git_check_picks_newer_beta(self):
        up = u.Updater(config={"method": "git", "channel": "beta"},
                       current="0.11.0-beta.1")
        up._versions_from_git = lambda: ["0.11.0-beta.1", "0.11.0-beta.2", "0.12.0"]
        with mock.patch.object(up, "_git_exe", return_value="git"), \
                mock.patch.object(Path, "is_dir", return_value=True):
            out = up.check("git")
        self.assertTrue(out["update_available"])
        self.assertEqual(out["method"], "git")
        self.assertEqual(out["latest"], "0.12.0")

    def test_legacy_11_tags_are_ignored(self):
        up = u.Updater(config={"method": "git", "channel": "beta", "line": "0"},
                       current="0.11.0-beta.1")
        up._versions_from_git = lambda: ["0.11.0-beta.2", "11.0.0-preview.102"]
        with mock.patch.object(up, "_git_exe", return_value="git"), \
                mock.patch.object(Path, "is_dir", return_value=True):
            out = up.check("git")
        self.assertEqual(out["latest"], "0.11.0-beta.2")

    def test_stable_channel_ignores_beta(self):
        up = u.Updater(config={"method": "git", "channel": "stable"},
                       current="0.11.0")
        up._versions_from_git = lambda: ["0.11.1", "0.12.0-beta.1"]
        with mock.patch.object(up, "_git_exe", return_value="git"), \
                mock.patch.object(Path, "is_dir", return_value=True):
            out = up.check("git")
        self.assertEqual(out["latest"], "0.11.1")


class UpdaterZipTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gantry_upd_"))
        (self.root / "gantry").mkdir()
        (self.root / "gantry" / "version.py").write_text(
            'VERSION = "0.10.0"\n', encoding="utf-8")
        (self.root / "gantry" / "old.py").write_text("old = 1\n", encoding="utf-8")
        (self.root / "start.py").write_text("print('old')\n", encoding="utf-8")
        (self.root / "config.json").write_text('{"keep": true}\n', encoding="utf-8")
        (self.root / "data").mkdir()
        (self.root / "data" / "keep.txt").write_text("keep", encoding="utf-8")
        (self.root / "README.md").write_text("old readme\n", encoding="utf-8")
        self.zip_path = self.root / "new.zip"
        with zipfile.ZipFile(self.zip_path, "w") as z:
            z.writestr("gantry-new/gantry/version.py", 'VERSION = "0.11.0-beta.1"\n')
            z.writestr("gantry-new/gantry/new.py", "new = 2\n")
            z.writestr("gantry-new/start.py", "print('new')\n")
            z.writestr("gantry-new/README.md", "new readme\n")
            z.writestr("gantry-new/data/should-not-copy.txt", "no\n")
        self.up = u.Updater(root=self.root, current="0.10.0",
                            config={"method": "zip", "channel": "beta"})
        self.up.check = lambda method="": {  # type: ignore[assignment]
            "ok": True, "current": "0.10.0", "latest": "0.11.0-beta.1",
            "update_available": True, "method": "zip",
        }

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_apply_zip_preserves_data_and_rollback(self):
        def fake_release(tag):
            return {"tag": tag, "version": tag, "url": "https://github.com/x/y.zip",
                    "sha256": "", "notes": "", "source": "release"}

        def fake_download(url, dest):
            shutil.copyfile(self.zip_path, dest)

        with mock.patch.object(self.up, "_release_info", side_effect=fake_release), \
                mock.patch.object(self.up, "_download", side_effect=fake_download), \
                mock.patch.object(self.up, "_schedule_restart"):
            # release 无 digest 时必须显式 force=True 才放行（新安全语义）
            out = self.up.apply(version="0.11.0-beta.1", method="zip", restart=False,
                                force=True)
        self.assertTrue(out["changed"])
        self.assertTrue((self.root / "gantry" / "new.py").is_file())
        self.assertFalse((self.root / "gantry" / "old.py").exists())
        self.assertEqual((self.root / "data" / "keep.txt").read_text(), "keep")
        self.assertEqual((self.root / "config.json").read_text(), '{"keep": true}\n')
        self.assertFalse((self.root / "data" / "should-not-copy.txt").exists())
        # 回滚
        out2 = self.up.rollback()
        self.assertTrue(out2["ok"])
        self.assertEqual(out2["method"], "zip")
        self.assertTrue((self.root / "gantry" / "old.py").is_file())
        self.assertFalse((self.root / "gantry" / "new.py").exists())

    def test_zip_preserves_unknown_toplevel_and_user_plugins(self):
        # AUD-P0-06：新包里没有的顶层条目（AGENT.md/skills/用户插件）不得被删除；
        # plugins/ 按子项合并——用户装的第三方插件保留，新包自带插件正常安装。
        (self.root / "AGENT.md").write_text("project rules\n", encoding="utf-8")
        (self.root / "skills").mkdir()
        (self.root / "skills" / "mine.md").write_text("skill\n", encoding="utf-8")
        (self.root / "plugins").mkdir()
        (self.root / "plugins" / "user-plugin").mkdir()
        (self.root / "plugins" / "user-plugin" / "plugin.py").write_text("x=1\n",
                                                                        encoding="utf-8")
        with zipfile.ZipFile(self.zip_path, "w") as z:
            z.writestr("gantry-new/gantry/version.py", 'VERSION = "0.11.0-beta.1"\n')
            z.writestr("gantry-new/gantry/new.py", "new = 2\n")
            z.writestr("gantry-new/start.py", "print('new')\n")
            z.writestr("gantry-new/plugins/shipped/plugin.py", "shipped\n")

        def fake_release(tag):
            return {"tag": tag, "version": tag, "url": "https://github.com/x/y.zip",
                    "sha256": "", "notes": "", "source": "release"}

        def fake_download(url, dest):
            shutil.copyfile(self.zip_path, dest)

        with mock.patch.object(self.up, "_release_info", side_effect=fake_release), \
                mock.patch.object(self.up, "_download", side_effect=fake_download), \
                mock.patch.object(self.up, "_schedule_restart"):
            out = self.up.apply(version="0.11.0-beta.1", method="zip", restart=False,
                                force=True)
        self.assertTrue(out["changed"])
        self.assertTrue((self.root / "AGENT.md").is_file(),
                        "新包没有的顶层条目应保留")
        self.assertTrue((self.root / "skills" / "mine.md").is_file())
        self.assertTrue((self.root / "plugins" / "user-plugin" / "plugin.py").is_file(),
                        "用户插件不得被整目录替换清空")
        self.assertTrue((self.root / "plugins" / "shipped" / "plugin.py").is_file(),
                        "新包自带的插件应安装")

    def test_zip_missing_digest_requires_force(self):
        """release 无 SHA256 digest 时默认拒绝安装，仅 force=True 放行。"""
        def fake_release(tag):
            return {"tag": tag, "version": tag, "url": "https://github.com/x/y.zip",
                    "sha256": "", "notes": "", "source": "release"}

        def fake_download(url, dest):
            shutil.copyfile(self.zip_path, dest)

        with mock.patch.object(self.up, "_release_info", side_effect=fake_release), \
                mock.patch.object(self.up, "_download", side_effect=fake_download), \
                mock.patch.object(self.up, "_schedule_restart"):
            with self.assertRaises(u.UpdateError):
                self.up.apply(version="0.11.0-beta.1", method="zip", restart=False)
            self.assertTrue((self.root / "gantry" / "old.py").is_file(),
                            "拒绝安装时不应改动现有代码")
            out = self.up.apply(version="0.11.0-beta.1", method="zip", restart=False,
                                force=True)
        self.assertTrue(out["changed"])

    def test_zip_slip_rejected(self):
        bad = self.root / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../evil.txt", "x")
        dest = self.root / "extract"
        with self.assertRaises(u.UpdateError):
            u.Updater._safe_extract(bad, dest)
        self.assertFalse((self.root / "evil.txt").exists())

    def test_apply_prunes_old_backups_keep_5(self):
        """成功安装后淘汰旧备份：目录名带时间戳，按序删旧的，总数只留最近 5 份。"""
        backups = self.root / "data" / "updates" / "backups"
        for i in range(6):   # 预置 6 份旧备份
            d = backups / f"2026010{i}-120000-zip"
            d.mkdir(parents=True)
            (d / "backup.json").write_text("{}", encoding="utf-8")

        def fake_release(tag):
            return {"tag": tag, "version": tag, "url": "https://github.com/x/y.zip",
                    "sha256": "", "notes": "", "source": "release"}

        def fake_download(url, dest):
            shutil.copyfile(self.zip_path, dest)

        with mock.patch.object(self.up, "_release_info", side_effect=fake_release), \
                mock.patch.object(self.up, "_download", side_effect=fake_download), \
                mock.patch.object(self.up, "_schedule_restart"):
            out = self.up.apply(version="0.11.0-beta.1", method="zip", restart=False,
                                force=True)
        self.assertTrue(out["changed"])
        kept = sorted(p.name for p in backups.iterdir() if p.is_dir())
        self.assertEqual(len(kept), 5, f"6 旧 + 1 新 = 7 份，应淘汰到 5 份：{kept}")
        self.assertNotIn("20260100-120000-zip", kept, "最旧的应先被淘汰")
        self.assertNotIn("20260101-120000-zip", kept)
        self.assertIn("20260105-120000-zip", kept)
        self.assertIn(out["backup"], kept, "本次新备份必须保留")
        # 被淘汰的目录整体删除（backup.json 一起没）
        self.assertFalse((backups / "20260100-120000-zip" / "backup.json").exists())

    def test_dry_run_does_not_touch_code(self):
        with mock.patch.object(self.up, "_release_info") as rel, \
                mock.patch.object(self.up, "_download") as dl:
            out = self.up.apply(version="0.11.0-beta.1", method="zip",
                                restart=False, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertTrue((self.root / "gantry" / "old.py").is_file())
        rel.assert_not_called()
        dl.assert_not_called()

    def test_git_apply_ignores_untracked_files(self):
        """git clone 后启动服务产生的 data/ workspace/ __pycache__/ 等未跟踪文件
        不应触发 '工作区有未提交改动' 的误判（回归：--porcelain 输出 ?? 导致拒绝更新）。"""
        up = u.Updater(config={"method": "git", "channel": "beta"},
                       current="0.10.0")
        calls = []
        def fake_git(args, timeout=None):
            calls.append(args)
            if args[:2] == ["status", "--porcelain"]:
                return ""          # 干净（即使磁盘上有未跟踪文件）
            if args[0] == "rev-parse":
                return "abc123" if "HEAD" in args else "def456"
            if args[0] == "merge":
                return ""
            return ""
        with mock.patch.object(up, "_git", side_effect=fake_git), \
                mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(up, "_new_backup", return_value=(Path("/tmp/b"), {})), \
                mock.patch.object(up, "_save_backup"):
            up._apply_git("0.11.0-beta.1", force=False)
        # 关键断言：status 调用必须带 --untracked-files=no
        status_calls = [c for c in calls if c[:2] == ["status", "--porcelain"]]
        self.assertTrue(any("--untracked-files=no" in c for c in status_calls),
                        f"status 调用缺少 --untracked-files=no: {status_calls}")

    def test_git_fetch_does_not_use_tags(self):
        """fetch 不能带 --tags：本地 tag 被移动过时会报 would clobber existing tag。"""
        up = u.Updater(config={"method": "git", "channel": "beta"},
                       current="0.10.0")
        calls = []
        def fake_git(args, timeout=None):
            calls.append(args)
            if args[:2] == ["status", "--porcelain"]:
                return ""
            if args[0] == "rev-parse":
                return "abc123"
            if args[0] == "fetch":
                return ""
            if args[0] == "merge":
                return ""
            return ""
        with mock.patch.object(up, "_git", side_effect=fake_git), \
                mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(up, "_new_backup", return_value=(Path("/tmp/b"), {})), \
                mock.patch.object(up, "_save_backup"):
            up._apply_git("0.11.0-beta.1", force=False)
        fetch_calls = [c for c in calls if c[0] == "fetch"]
        # fetch 调用不能带 --tags
        for c in fetch_calls:
            self.assertNotIn("--tags", c, f"fetch 不应带 --tags: {c}")
        # 应该至少有一次 fetch origin main
        self.assertTrue(any(c[:2] == ["fetch", "origin"] for c in fetch_calls),
                        f"缺少 fetch origin main: {fetch_calls}")

    def test_git_apply_force_fetches_missing_tag(self):
        """回归：异机全新 clone 本地没有任何 v0.11 tag，rev-parse 会 exit 128。
        更新器必须强制抓取单个 tag（而不是直接失败）。"""
        up = u.Updater(config={"method": "git", "channel": "beta"}, current="0.10.0")
        calls = []
        state = {"tag_fetched": False}

        def fake_git(args, timeout=None):
            calls.append(args)
            if args[:2] == ["status", "--porcelain"]:
                return ""
            if args[0] == "rev-parse" and "refs/tags/" in " ".join(args):
                if state["tag_fetched"]:
                    return "tagcommit123"
                raise u.UpdateError("git rev-parse ... 失败（exit 128）: fatal: Needed a single revision")
            if args[0] == "rev-parse":
                if "HEAD" in args or "--abbrev-ref" in args:
                    return "main" if "--abbrev-ref" in args else "prevcommit"
                return "prevcommit"
            if args[0] == "fetch":
                if any("+refs/tags/" in a for a in args):
                    state["tag_fetched"] = True
                return ""
            if args[0] == "merge":
                return ""
            return ""

        with mock.patch.object(up, "_git", side_effect=fake_git), \
                mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(up, "_new_backup", return_value=(Path("/tmp/b"), {})), \
                mock.patch.object(up, "_save_backup"):
            out = up._apply_git("0.11.0-beta.14", force=False)
        self.assertTrue(out["changed"])
        self.assertEqual(out["target_commit"], "tagcommit123"[:12])
        # 必须用 "+refs/tags/x:refs/tags/x" 强制抓取（"+" 才允许覆盖已存在的 tag）
        tag_fetch = [c for c in calls if c[0] == "fetch" and any("+refs/tags/" in a for a in c)]
        self.assertTrue(tag_fetch, f"缺少强制抓取 tag 的 fetch: {calls}")
        self.assertIn("--force", tag_fetch[0])

    def test_git_apply_falls_back_to_remote_branch(self):
        """tag 抓不到时退到 origin/<branch>：明确版本号必须 force=True，且结果带 fallback 标记。"""
        up = u.Updater(config={"method": "git", "channel": "beta"}, current="0.10.0")
        calls = []

        def fake_git(args, timeout=None):
            calls.append(args)
            if args[:2] == ["status", "--porcelain"]:
                return ""
            if args[0] == "rev-parse" and any("refs/tags/" in a for a in args):
                raise u.UpdateError("exit 128")
            if args[0] == "rev-parse" and any("refs/remotes/origin/" in a for a in args):
                return "remotehead456"
            if args[0] == "rev-parse":
                return "main" if "--abbrev-ref" in args else "prevcommit"
            if args[0] == "fetch":
                if any("+refs/tags/" in a for a in args):
                    raise u.UpdateError("tag fetch failed")
                return ""
            if args[0] == "merge":
                return ""
            return ""

        with mock.patch.object(up, "_git", side_effect=fake_git), \
                mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(up, "_new_backup", return_value=(Path("/tmp/b"), {})), \
                mock.patch.object(up, "_save_backup"):
            # 目标是明确版本号：非 force 禁止静默兜底
            with self.assertRaises(u.UpdateError):
                up._apply_git("0.11.0-beta.14", force=False)
            out = up._apply_git("0.11.0-beta.14", force=True)
        self.assertEqual(out["target_commit"], "remotehead456"[:12])
        self.assertTrue(out.get("fallback"))

    def test_git_merge_failure_gives_actionable_hint(self):
        """ff-only 失败（本地有自己的提交/分叉）要给出可执行的修复命令。"""
        up = u.Updater(config={"method": "git", "channel": "beta"}, current="0.10.0")

        def fake_git(args, timeout=None):
            if args[:2] == ["status", "--porcelain"]:
                return ""
            if args[0] == "rev-parse" and any("refs/tags/" in a for a in args):
                return "tagcommit"
            if args[0] == "rev-parse":
                return "main" if "--abbrev-ref" in args else "prevcommit"
            if args[0] == "fetch":
                return ""
            if args[0] == "merge":
                raise u.UpdateError("git merge --ff-only 失败（exit 128）: Not possible to fast-forward")
            return ""

        with mock.patch.object(up, "_git", side_effect=fake_git), \
                mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(up, "_new_backup", return_value=(Path("/tmp/b"), {})), \
                mock.patch.object(up, "_save_backup"):
            with self.assertRaises(u.UpdateError) as ctx:
                up._apply_git("0.11.0-beta.14", force=False)
        msg = str(ctx.exception)
        self.assertIn("git reset --hard", msg)
        self.assertIn("0.11.0-beta.14", msg)


class DepsSyncTests(unittest.TestCase):
    """zip 更新后的依赖同步：锁文件变化才跑 pip，失败只降级为警告。"""

    def _make(self, old_lock: bytes | None, new_lock: bytes | None,
              config: dict | None = None):
        root = Path(tempfile.mkdtemp(prefix="cbp_deps_"))
        bdir = root / "update" / "backups" / "b1"
        (bdir / "code").mkdir(parents=True)
        source = root / "staging" / "src"
        source.mkdir(parents=True)
        if old_lock is not None:
            (bdir / "code" / "requirements-lock.txt").write_bytes(old_lock)
        if new_lock is not None:
            (source / "requirements-lock.txt").write_bytes(new_lock)
        up = u.Updater(root=root, config=config or {}, current="0.11.0")
        return up, source, bdir

    def test_unchanged_lock_skips_pip(self):
        up, source, bdir = self._make(b"same", b"same")
        with mock.patch.object(u.subprocess, "run") as run:
            out = up._sync_deps_after_zip(source, bdir)
        run.assert_not_called()
        self.assertFalse(out["deps_synced"])
        self.assertIn("未变化", out["deps_note"])

    def test_changed_lock_runs_pip(self):
        up, source, bdir = self._make(b"old", b"new")
        with mock.patch.object(u.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            out = up._sync_deps_after_zip(source, bdir)
        self.assertTrue(out["deps_synced"])
        args = run.call_args[0][0]
        self.assertEqual(args[1:4], ["-m", "pip", "install"])
        self.assertIn(str(source / "requirements-lock.txt"), args)

    def test_pip_failure_is_warning_not_rollback(self):
        up, source, bdir = self._make(b"old", b"new")
        with mock.patch.object(u.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            out = up._sync_deps_after_zip(source, bdir)   # 不得抛异常
        self.assertFalse(out["deps_synced"])
        self.assertIn("pip install -r requirements-lock.txt", out["deps_warning"])

    def test_sync_deps_disabled(self):
        up, source, bdir = self._make(b"old", b"new", {"sync_deps": False})
        with mock.patch.object(u.subprocess, "run") as run:
            out = up._sync_deps_after_zip(source, bdir)
        run.assert_not_called()
        self.assertIn("禁用", out["deps_note"])

    def test_missing_new_lock_skips(self):
        up, source, bdir = self._make(b"old", None)
        with mock.patch.object(u.subprocess, "run") as run:
            out = up._sync_deps_after_zip(source, bdir)
        run.assert_not_called()
        self.assertIn("未附", out["deps_note"])


if __name__ == "__main__":
    unittest.main()
