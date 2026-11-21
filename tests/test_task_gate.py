# -*- coding: utf-8 -*-
"""任务图最终验收门。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.task_gate import verify_task_plan
from gantry.task_plan import TaskStore


class TaskGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="task_gate_"))
        self.store = TaskStore(self.tmp / "tasks.db")
        self.ws = self.tmp / "ws"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_plan_skips(self):
        v = verify_task_plan("s1", self.ws, "", store=self.store)
        self.assertTrue(v["ok"])
        self.assertTrue(v["skipped"])

    def test_incomplete_plan_blocks(self):
        self.store.create_plan("s1", [{"id": "a", "title": "A"}])
        v = verify_task_plan("s1", self.ws, "", store=self.store)
        self.assertFalse(v["ok"])
        self.assertIn("未完成", v["reason"])

    def test_completed_plan_with_missing_output_blocks(self):
        self.store.create_plan("s1", [{"id": "a", "title": "A",
                                       "outputs": [{"path": "out.txt"}]}])
        self.store.update_task("s1", "a", status="completed")
        v = verify_task_plan("s1", self.ws, "", store=self.store)
        self.assertFalse(v["ok"])
        self.assertIn("out.txt", v["reason"])

    def test_completed_plan_with_real_output_passes(self):
        (self.ws / "out.txt").write_text("data", encoding="utf-8")
        self.store.create_plan("s1", [{"id": "a", "title": "A",
                                       "outputs": [{"path": "out.txt"}],
                                       "acceptance": [{"type": "file_exists",
                                                       "path": "out.txt"}]}])
        self.store.update_task("s1", "a", status="completed")
        v = verify_task_plan("s1", self.ws, "done", store=self.store)
        self.assertTrue(v["ok"])
        self.assertTrue(v["verified"])


if __name__ == "__main__":
    unittest.main()
