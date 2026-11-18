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
            out = self.up.apply(version="0.11.0-beta.1", method="zip", restart=False)
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

    def test_zip_slip_rejected(self):
        bad = self.root / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../evil.txt", "x")
        dest = self.root / "extract"
        with self.assertRaises(u.UpdateError):
            u.Updater._safe_extract(bad, dest)
        self.assertFalse((self.root / "evil.txt").exists())

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
        """tag 抓不到时退到 origin/<branch>，而不是整个更新失败。"""
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
            out = up._apply_git("0.11.0-beta.14", force=False)
        self.assertEqual(out["target_commit"], "remotehead456"[:12])

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


if __name__ == "__main__":
    unittest.main()
