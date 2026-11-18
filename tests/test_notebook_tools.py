# -*- coding: utf-8 -*-
"""DSH / Claude Code 参考工具：Jupyter notebook 与 todo 读取。"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.tools import ToolRegistry, ToolError  # noqa: E402


def _notebook() -> dict:
    return {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Title\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {},
             "outputs": [], "source": ["print(1)\n"]},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


class NotebookToolTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="cbp_nb_"))
        self.nb = self.ws / "demo.ipynb"
        self.nb.write_text(json.dumps(_notebook(), ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
        self.reg = ToolRegistry(self.ws, {})

    def _call(self, name, **kwargs):
        return asyncio.run(self.reg._tools[name](**kwargs))

    def test_read_notebook(self):
        out = self._call("notebook_read", path="demo.ipynb")
        self.assertEqual(out["cell_count"], 2)
        self.assertEqual(out["cells"][0]["cell_type"], "markdown")
        self.assertIn("# Title", out["cells"][0]["source"])
        self.assertEqual(out["cells"][1]["cell_type"], "code")

    def test_replace_cell(self):
        out = self._call("notebook_edit", path="demo.ipynb", cell_index=0,
                         action="replace", cell_type="markdown", source="## New\n")
        self.assertTrue(out["changed"])
        data = json.loads(self.nb.read_text(encoding="utf-8"))
        self.assertEqual("".join(data["cells"][0]["source"]), "## New\n")

    def test_insert_cell(self):
        out = self._call("notebook_edit", path="demo.ipynb", cell_index=2,
                         action="insert", cell_type="code", source="x = 2\n")
        self.assertEqual(out["cell_count"], 3)
        data = json.loads(self.nb.read_text(encoding="utf-8"))
        self.assertEqual(data["cells"][2]["cell_type"], "code")

    def test_delete_cell(self):
        out = self._call("notebook_edit", path="demo.ipynb", cell_index=1, action="delete")
        self.assertEqual(out["cell_count"], 1)
        data = json.loads(self.nb.read_text(encoding="utf-8"))
        self.assertEqual(data["cells"][0]["cell_type"], "markdown")

    def test_index_out_of_range(self):
        with self.assertRaises(ToolError):
            self._call("notebook_edit", path="demo.ipynb", cell_index=9, action="delete")

    def test_reject_non_ipynb(self):
        (self.ws / "a.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ToolError):
            self._call("notebook_read", path="a.txt")


class TodoReadTests(unittest.TestCase):
    def test_reads_state_todos(self):
        reg = ToolRegistry(Path(tempfile.mkdtemp(prefix="cbp_todo_")), {})
        reg.state_getter = lambda: {"todos": [
            {"content": "a", "status": "completed"},
            {"content": "b", "status": "in_progress"},
        ]}
        out = asyncio.run(reg._tools["todo_read"]())
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["todos"][1]["status"], "in_progress")

    def test_empty_when_no_state(self):
        reg = ToolRegistry(Path(tempfile.mkdtemp(prefix="cbp_todo2_")), {})
        out = asyncio.run(reg._tools["todo_read"]())
        self.assertEqual(out, {"count": 0, "todos": []})


if __name__ == "__main__":
    unittest.main()
