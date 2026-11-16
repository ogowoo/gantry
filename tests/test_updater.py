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


if __name__ == "__main__":
    unittest.main()
