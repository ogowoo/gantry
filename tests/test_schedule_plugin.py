# -*- coding: utf-8 -*-
"""schedule 插件：工作区参数必须真正传到 run_task（不能只存不用）。"""
import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PLUGIN = BASE / "plugins" / "schedule" / "plugin.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("cbp_plugin_schedule_unit", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    """只实现 schedule 插件用到的接口（state/save_state/run_task/log/register_*）。"""

    def __init__(self):
        self.tools = {}
        self.apis = {}
        self.calls = []
        self._state = {}

    def log(self, msg):        # noqa: D401 - 测试替身
        pass

    def register_tool(self, name, fn, desc="", params=None):
        self.tools[name] = fn

    def register_api(self, method, path, handler):
        self.apis[(method, path)] = handler

    def register_panel(self, panel):
        self.panels = getattr(self, "panels", [])
        self.panels.append(panel)

    @property
    def state(self):
        return self._state

    def save_state(self, **kw):
        self._state.update(kw)

    async def run_task(self, task, model="", workspace="", prefix="plugin"):
        self.calls.append({"task": task, "model": model, "workspace": workspace})
        return {"session_id": "cron-test", "content": "", "steps": 0, "error": None}


class ScheduleWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_plugin()
        self.ctx = FakeCtx()
        self.mod.setup(self.ctx)

    def tearDown(self):
        task = getattr(getattr(self.ctx, "_scheduler", None), "_task", None)
        if task is not None:
            task.cancel()

    def test_workspace_stored_and_used(self):
        async def scenario():
            await self.ctx.tools["schedule_add"](
                name="a", spec={"every": "1d"}, task="t",
                model="m", enabled=True, workspace="/tmp/target-ws")
            listed = (await self.ctx.tools["schedule_list"]())["schedules"]
            self.assertEqual(listed[0]["workspace"], "/tmp/target-ws")
            await self.ctx.tools["schedule_run"](name="a")

        asyncio.run(scenario())
        self.assertEqual(len(self.ctx.calls), 1)
        self.assertEqual(self.ctx.calls[0]["workspace"], "/tmp/target-ws",
                         "工作区必须传给 run_task，而不是只存进列表")

    def test_empty_workspace_stays_empty(self):
        async def scenario():
            await self.ctx.tools["schedule_add"](name="b", spec={"every": "1d"}, task="t")
            await self.ctx.tools["schedule_run"](name="b")

        asyncio.run(scenario())
        self.assertEqual(self.ctx.calls[0]["workspace"], "")


class ScheduleUpdateTests(unittest.TestCase):
    """schedule_update 工具与 POST api/update：部分更新、重排期、历史保留。"""

    def setUp(self):
        self.mod = load_plugin()
        self.ctx = FakeCtx()
        self.mod.setup(self.ctx)

    def tearDown(self):
        task = getattr(getattr(self.ctx, "_scheduler", None), "_task", None)
        if task is not None:
            task.cancel()

    def test_update_tool_partial_and_rerun(self):
        async def scenario():
            await self.ctx.tools["schedule_add"](name="u", spec={"every": "1d"}, task="old")
            before = (await self.ctx.tools["schedule_list"]())["schedules"][0]["next_run"]
            out = await self.ctx.tools["schedule_update"](name="u", task="new", enabled=False)
            self.assertFalse(out["enabled"])
            listed = (await self.ctx.tools["schedule_list"]())["schedules"]
            self.assertEqual(listed[0]["task"], "new")
            self.assertFalse(listed[0]["enabled"])
            # 没改 spec 就不重排 next_run（display 字符串相同）
            self.assertEqual(listed[0]["next_run"], before)
            with self.assertRaises(ValueError):
                await self.ctx.tools["schedule_update"](name="不存在", task="x")

        asyncio.run(scenario())

    def test_update_spec_recomputes_next_run(self):
        async def scenario():
            await self.ctx.tools["schedule_add"](name="s", spec={"daily": "09:00"}, task="t")
            out = await self.ctx.tools["schedule_update"](name="s", spec={"every": "30m"})
            self.assertEqual(out["spec"], {"every": "30m"})
            self.assertIsNotNone(out["next_run"])

        asyncio.run(scenario())

    def test_api_update_registered(self):
        self.assertIn(("POST", "api/update"), self.ctx.apis)

    def test_last_session_and_error_recorded(self):
        async def scenario():
            await self.ctx.tools["schedule_add"](name="r", spec={"every": "1d"}, task="t")
            await self.ctx.tools["schedule_run"](name="r")
            listed = (await self.ctx.tools["schedule_list"]())["schedules"]
            self.assertEqual(listed[0]["last_session"], "cron-test")
            self.assertIsNone(listed[0]["last_error"])

        asyncio.run(scenario())

    def test_failure_records_last_error(self):
        async def boom(task, model="", workspace="", prefix="plugin"):
            raise RuntimeError("模型炸了")

        self.ctx.run_task = boom

        async def scenario():
            await self.ctx.tools["schedule_add"](name="f", spec={"every": "1d"}, task="t")
            await self.ctx.tools["schedule_run"](name="f")
            listed = (await self.ctx.tools["schedule_list"]())["schedules"]
            self.assertIn("模型炸了", listed[0]["last_error"])
            self.assertIsNone(listed[0]["last_session"])

        asyncio.run(scenario())


class ScheduleEveryTests(unittest.TestCase):
    """_parse_every：拒 nan/inf/非正/超一年的间隔（否则任务静默永不触发）。"""

    def setUp(self):
        self.mod = load_plugin()

    def test_nan_inf_rejected(self):
        for bad in ("nand", "infd", "nanm", "infs", "-infh"):
            with self.assertRaises(ValueError, msg=f"{bad!r} 应被拒绝"):
                self.mod._parse_every(bad)

    def test_non_positive_rejected(self):
        for bad in ("0m", "0s", "-5m", "-1d"):
            with self.assertRaises(ValueError, msg=f"{bad!r} 应被拒绝"):
                self.mod._parse_every(bad)

    def test_upper_bound(self):
        self.assertEqual(self.mod._parse_every("366d"), 366 * 86400, "边界值应接受")
        for bad in ("367d", "1000d", "1e9s"):
            with self.assertRaises(ValueError, msg=f"{bad!r} 超上限应被拒绝"):
                self.mod._parse_every(bad)

    def test_garbage_rejected(self):
        for bad in ("", "abc", "10x", "m"):
            with self.assertRaises(ValueError, msg=f"{bad!r} 应被拒绝"):
                self.mod._parse_every(bad)

    def test_valid_unchanged(self):
        self.assertEqual(self.mod._parse_every("30s"), 30)
        self.assertEqual(self.mod._parse_every("10m"), 600)
        self.assertEqual(self.mod._parse_every("2h"), 7200)
        self.assertEqual(self.mod._parse_every("1d"), 86400)

class ScheduleImportExportTests(unittest.TestCase):
    """schedule_export / schedule_import（工具 + API）：merge 保历史、replace 清空、坏条目跳过。"""

    def setUp(self):
        self.mod = load_plugin()
        self.ctx = FakeCtx()
        self.mod.setup(self.ctx)

    def tearDown(self):
        task = getattr(getattr(self.ctx, "_scheduler", None), "_task", None)
        if task is not None:
            task.cancel()

    def test_export_roundtrip_merge(self):
        async def scenario():
            await self.ctx.tools["schedule_add"](name="e1", spec={"every": "30m"},
                                                 task="t1", workspace="D:/ws", enabled=False)
            exported = await self.ctx.tools["schedule_export"]()
            self.assertEqual(exported["version"], 1)
            self.assertEqual(len(exported["schedules"]), 1)
            self.assertNotIn("runs", exported["schedules"][0], "导出是配置，不带运行历史")
            # 改本地任务后 merge 导入：按名更新定义、保留 runs
            await self.ctx.tools["schedule_update"](name="e1", task="changed")
            r = await self.ctx.tools["schedule_import"](data=exported, mode="merge")
            self.assertEqual((r["added"], r["updated"], r["skipped"]), (0, 1, 0))
            after = (await self.ctx.tools["schedule_list"]())["schedules"][0]
            self.assertEqual(after["task"], "t1")
            self.assertFalse(after["enabled"])
        asyncio.run(scenario())

    def test_import_replace_clears_and_errors_skipped(self):
        async def scenario():
            await self.ctx.tools["schedule_add"](name="old1", spec={"every": "1h"}, task="x")
            payload = {"schedules": [
                {"name": "new1", "spec": {"daily": "09:00"}, "task": "y"},
                {"name": "bad", "task": "no spec"},
                {"name": "bad2", "spec": {"nope": 1}, "task": "z"},
            ]}
            r = await self.ctx.tools["schedule_import"](data=payload, mode="replace")
            self.assertEqual((r["added"], r["skipped"]), (1, 2))
            self.assertEqual(r["total"], 1, "replace 先清空，只剩合法新条目")
            names = [s["name"] for s in (await self.ctx.tools["schedule_list"]())["schedules"]]
            self.assertEqual(names, ["new1"])
        asyncio.run(scenario())

    def test_import_api_and_bad_payload(self):
        async def scenario():
            with self.assertRaises(ValueError):
                sched_import = self.ctx.apis[("POST", "api/import")]
                class R:  # 空 body 也按 {} 处理，走"不是列表"分支
                    async def json(self): return {}
                await sched_import(R())
            export_api = self.ctx.apis[("GET", "api/export")]
            class Q:  # 单任务导出：?name= 过滤
                query_params = {"name": ""}
            out = await export_api(Q())
            self.assertIn("schedules", out)
            with self.assertRaises(ValueError):
                class Q2:
                    query_params = {"name": "不存在"}
                await export_api(Q2())
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
