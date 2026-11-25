# -*- coding: utf-8 -*-
"""MCP 客户端插件：stdio 与 HTTP 两种传输、resources、错误与隔离。"""
import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PLUGIN = BASE / "plugins" / "mcp" / "plugin.py"
MOCK = BASE / "tests" / "fixtures" / "mock_mcp_server.py"
BIG = BASE / "tests" / "fixtures" / "mock_mcp_big.py"
sys.path.insert(0, str(BASE / "tests" / "fixtures"))

import mock_mcp_http  # noqa: E402


def load_plugin():
    spec = importlib.util.spec_from_file_location("cbp_plugin_mcp_unit", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class McpStdioTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_plugin()
        self.mgr = self.mod.McpManager()

    def tearDown(self):
        asyncio.run(self.mgr.close_all())

    def test_connect_lists_tools(self):
        async def scenario():
            self.mgr.add("mock", {"command": sys.executable, "args": [str(MOCK)]})
            await self.mgr.connect_all()
            self.assertNotIn("mock", self.mgr.errors)
            names = [t["tool"] for t in self.mgr.tool_specs()]
            # mock 同时提供 prompts，所以还会多一个 use_prompt（每个有 prompt 的 server 一个）
            self.assertEqual(sorted(names), ["add", "boom", "echo", "use_prompt"])
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_large_tools_list_exceeds_default_stream_limit(self):
        async def scenario():
            self.mgr.add("big", {"command": sys.executable, "args": [str(BIG)]})
            await self.mgr.connect_all()
            self.assertNotIn("big", self.mgr.errors)
            names = [t["tool"] for t in self.mgr.tool_specs()]
            self.assertIn("huge", names)
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_call_tool_returns_text(self):
        async def scenario():
            self.mgr.add("mock", {"command": sys.executable, "args": [str(MOCK)]})
            await self.mgr.connect_all()
            res = await self.mgr.call("mock", "echo", {"text": "你好 MCP"})
            self.assertEqual(res["text"], "你好 MCP")
            self.assertFalse(res["isError"])
            res2 = await self.mgr.call("mock", "add", {"a": 2, "b": 3})
            self.assertEqual(res2["text"], "5.0")
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_tool_error_surfaces(self):
        async def scenario():
            self.mgr.add("mock", {"command": sys.executable, "args": [str(MOCK)]})
            await self.mgr.connect_all()
            with self.assertRaises(self.mod.McpError):
                await self.mgr.call("mock", "boom", {})
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_registered_name_is_sanitized(self):
        async def scenario():
            self.mgr.add("my-server.v2", {"command": sys.executable, "args": [str(MOCK)]})
            await self.mgr.connect_all()
            names = [t["registered_name"] for t in self.mgr.tool_specs()]
            self.assertIn("mcp__my_server_v2__echo", names)
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_stdio_prompts_list_and_get(self):
        async def scenario():
            self.mgr.add("mock", {"command": sys.executable, "args": [str(MOCK)]})
            await self.mgr.connect_all()
            specs = self.mgr.prompt_specs()
            self.assertEqual([p["name"] for p in specs], ["greet"])
            self.assertEqual(specs[0]["arguments"][0]["name"], "who")
            res = await self.mgr.get_prompt("mock", "greet", {"who": "世界"})
            self.assertIn("请向 世界 打招呼", res["text"])
            reg = [t["registered_name"] for t in self.mgr.tool_specs()]
            self.assertIn("mcp__mock__use_prompt", reg)
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_bad_server_reports_error_without_breaking_others(self):
        async def scenario():
            self.mgr.add("good", {"command": sys.executable, "args": [str(MOCK)]})
            self.mgr.add("bad", {"command": "definitely-not-a-real-binary-xyz"})
            await self.mgr.connect_all()
            self.assertIn("bad", self.mgr.errors)
            self.assertNotIn("good", self.mgr.errors)
            self.assertTrue([t for t in self.mgr.tool_specs() if t["server"] == "good"])
            await self.mgr.close_all()

        asyncio.run(scenario())


class FakeCtx:
    """只实现 _register_dynamic 需要的接口，用于验证工具表对账。"""

    def __init__(self):
        self.tools: dict[str, object] = {}
        self.registered = {"tools": []}
        self.removed: list[str] = []

        class Reg:
            def __init__(self, outer):
                self.outer = outer

            def register(self, name, fn, desc, args, approval=False):
                self.outer.tools[name] = fn

            def unregister(self, name):
                self.outer.removed.append(name)
                self.outer.tools.pop(name, None)

        class Host:
            def __init__(self, outer):
                self.registry = Reg(outer)

        self.host = Host(self)

    def register_tool(self, name, fn, desc, args=None, approval=False):
        self.tools[name] = fn
        self.registered["tools"].append(name)


class McpReconcileTests(unittest.TestCase):
    """移除 server 后，它的工具必须从工具表里摘掉（否则是"幽灵工具"）。"""

    def setUp(self):
        self.mod = load_plugin()
        self.mgr = self.mod.McpManager()

    def tearDown(self):
        self.mgr = self.mod.McpManager()

    def test_removed_server_tools_are_unregistered(self):
        ctx = FakeCtx()

        async def scenario():
            self.mgr.add("mock", {"command": sys.executable, "args": [str(MOCK)]})
            await self.mgr.connect_all()
            self.mod._register_dynamic(ctx, self.mgr)
            self.assertIn("mcp__mock__echo", ctx.tools)
            # 移除 server（模拟 api/remove）
            client = self.mgr.clients.pop("mock")
            await client.close()
            self.mgr.remove("mock")
            self.mod._register_dynamic(ctx, self.mgr)
            self.assertNotIn("mcp__mock__echo", ctx.tools)
            self.assertIn("mcp__mock__echo", ctx.removed)

        asyncio.run(scenario())


class AddCtx:
    """setup() 所需的完整接口（AUD-P0-10 端点安全测试用）。"""

    def __init__(self, config=None):
        self.config = config or {}
        self.state: dict = {}
        self.tools: dict = {}
        self.apis: dict = {}
        self.logs: list[str] = []

    def log(self, msg, *a, **k):
        self.logs.append(str(msg))

    def save_state(self, **kw):
        self.state.update(kw)

    def register_tool(self, name, fn, desc, args=None, approval=False):
        self.tools[name] = fn

    def register_api(self, method, path, fn):
        self.apis[path] = fn

    def register_panel(self, panel):
        pass


class McpAddEndpointSecurityTests(unittest.TestCase):
    """AUD-P0-10：HTTP api/add 的解释器参数签名白名单（审核 ⑦-4）。"""

    def setUp(self):
        self.mod = load_plugin()

    def _add(self, spec, config=None):
        ctx = AddCtx(config)
        self.mod.setup(ctx)

        class _Req:
            def __init__(self, body):
                self._b = body

            async def json(self):
                return self._b

        fn = ctx.apis["api/add"]
        r = asyncio.new_event_loop().run_until_complete(
            fn(_Req({"name": "x", "spec": spec})))
        self.mod.teardown(ctx)
        return r

    def test_python_minus_c_rejected(self):
        r = self._add({"command": "python",
                       "args": ["-c", "import os; os.system('calc')"]})
        self.assertFalse(r["connected"])
        self.assertIn("-m", r["error"])

    def test_python_script_path_rejected(self):
        r = self._add({"command": "python", "args": [str(Path(__file__))]})
        self.assertFalse(r["connected"])
        self.assertIn("-m", r["error"])

    def test_node_eval_rejected(self):
        r = self._add({"command": "node", "args": ["-e", "process.exit(0)"]})
        self.assertFalse(r["connected"])
        self.assertIn("node", r["error"])

    def test_npx_unregistered_package_rejected(self):
        r = self._add({"command": "npx", "args": ["some-evil-package"]})
        self.assertFalse(r["connected"])
        self.assertIn("npx", r["error"])

    def test_deno_blocked_without_config_flag(self):
        r = self._add({"command": "deno", "args": ["run", "x.ts"]})
        self.assertFalse(r["connected"])
        self.assertIn("allow_script_interpreters", r["error"])

    def test_python_m_shape_passes_arg_gate(self):
        # 合法形态不被参数校验拒绝（连接失败是另一回事——模块不存在）
        r = self._add({"command": "python",
                       "args": ["-m", "definitely_missing_module_xyz"]})
        self.assertNotIn("只允许", r.get("error") or "")


class McpHttpTests(unittest.TestCase):
    """streamable HTTP 传输：JSON 响应、SSE 响应、session 头、resources。"""

    def setUp(self):
        self.mod = load_plugin()
        self.mgr = self.mod.McpManager()
        self.srv = mock_mcp_http.MockMcpHttp()
        self.url = self.srv.start()

    def tearDown(self):
        asyncio.run(self.mgr.close_all())
        self.srv.stop()

    def test_http_connect_and_tools(self):
        async def scenario():
            self.mgr.add("httpmock", {"url": self.url})
            await self.mgr.connect_all()
            self.assertNotIn("httpmock", self.mgr.errors)
            client = self.mgr.clients["httpmock"]
            self.assertEqual(client.transport, "http")
            self.assertEqual(client.session_id, "sess-test-1")
            names = [t["tool"] for t in self.mgr.tool_specs()]
            self.assertIn("echo", names)
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_http_sse_response_is_parsed(self):
        """tools/call 由服务端用 text/event-stream 返回，客户端要能取出结果。"""
        async def scenario():
            self.mgr.add("httpmock", {"url": self.url})
            await self.mgr.connect_all()
            res = await self.mgr.call("httpmock", "echo", {"text": "SSE 也通"})
            self.assertEqual(res["text"], "SSE 也通")
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_http_resources_list_and_read(self):
        async def scenario():
            self.mgr.add("httpmock", {"url": self.url})
            await self.mgr.connect_all()
            specs = self.mgr.resource_specs()
            self.assertEqual([s["uri"] for s in specs], ["mock://readme"])
            # 每个 server 一个只读资源工具
            reg = [t["registered_name"] for t in self.mgr.tool_specs()]
            self.assertIn("mcp__httpmock__read_resource", reg)
            res = await self.mgr.call("httpmock", "read_resource",
                                      {"uri": "mock://readme"})
            self.assertIn("MCP 资源内容 OK", res["text"])
            with self.assertRaises(self.mod.McpError):
                await self.mgr.call("httpmock", "read_resource", {"uri": "mock://nope"})
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_http_prompts_list_and_get(self):
        async def scenario():
            self.mgr.add("httpmock", {"url": self.url})
            await self.mgr.connect_all()
            specs = self.mgr.prompt_specs()
            self.assertEqual([p["name"] for p in specs], ["greet"])
            res = await self.mgr.get_prompt("httpmock", "greet", {"who": "HTTP"})
            self.assertIn("请向 HTTP 打招呼", res["text"])
            await self.mgr.close_all()

        asyncio.run(scenario())

    def test_http_bad_endpoint_reports_error(self):
        async def scenario():
            self.mgr.add("dead", {"url": "http://127.0.0.1:9/mcp", "timeout": 3})
            await self.mgr.connect_all()
            self.assertIn("dead", self.mgr.errors)
            await self.mgr.close_all()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
