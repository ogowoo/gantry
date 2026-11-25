# -*- coding: utf-8 -*-
"""系统托盘 + 后台启动（`python start.py` 派生 pythonw）的回归。

用户需求："python start.py 之后可以关掉终端（相当于 pythonw），托盘常驻，菜单里能启动
自动化浏览器、打开 WebUI，日志也要留档。"

Win32 那部分只能在 Windows 真机上验证（`python start.py --tray-selftest` 会真的建图标，
并用 Shell_NotifyIconGetRect 确认注册成功）；这里守住**纯逻辑**与**接线**：
菜单项都有实现、后台命令用 pythonw、日志真的落盘、start.py 不在导入期拉 uvicorn。
"""
import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

START_SRC = (ROOT / "start.py").read_text(encoding="utf-8")
TRAY_SRC = (ROOT / "gantry" / "tray.py").read_text(encoding="utf-8")
ICON = ROOT / "webui" / "favicon.ico"   # 浏览器 favicon 与托盘同一份


class IconTests(unittest.TestCase):
    def test_icon_exists_and_is_ico(self):
        self.assertTrue(ICON.is_file(), "webui/favicon.ico 不存在（favicon 与托盘图标都会退回默认）")
        head = ICON.read_bytes()[:4]
        self.assertEqual(head, b"\x00\x00\x01\x00", "不是合法的 .ico 文件头")

    def test_icon_has_multiple_sizes(self):
        """多尺寸才会有清晰的 16px 小图标（否则任务栏里糊成一团）。"""
        data = ICON.read_bytes()
        count = int.from_bytes(data[4:6], "little")
        self.assertGreaterEqual(count, 4, f"图标只有 {count} 个尺寸")


    def test_index_references_the_same_icon(self):
        """页面 favicon 必须就是托盘用的那个文件（一份图标，两处展示）。"""
        html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="icon"', html)
        self.assertIn('href="favicon.ico"', html, "favicon 应指向 webui/favicon.ico")
        self.assertNotIn("data:image/svg+xml", html, "别再用内联 SVG（与托盘的 G 不一致）")


class MenuTests(unittest.TestCase):
    def setUp(self):
        from gantry import tray

        self.tray = tray
        self.t = tray.Tray(port=9, root=ROOT)      # 端口 9 必无服务 → "未响应"分支

    @staticmethod
    def _flat(items):
        """把子菜单摊平：[(action, label)]（分隔线/信息行不算）。"""
        out = []
        for item in items:
            if item[0] == "-" or item[0] is None:
                continue
            if item[0] == "--submenu--":
                out.extend(item[2])
            else:
                out.append((item[0], item[1]))
        return out

    def test_menu_has_required_actions(self):
        actions = {a for a, _ in self._flat(self.tray.default_menu(self.t))}
        for need in ("open_webui", "start_browser", "restart", "quit_tray", "shutdown",
                     "open_logs", "open_data", "lang_zh", "lang_en"):
            self.assertIn(need, actions, f"托盘菜单缺少 {need}")

    def test_every_action_has_handler(self):
        for a, label in self._flat(self.tray.default_menu(self.t)):
            self.assertTrue(label.strip(), f"{a} 没有文案")
            if a == "quit_tray":
                continue                            # 这个走 _on_message 里的 stop()
            self.assertTrue(hasattr(self.tray.Tray, f"_a_{a}"), f"{a} 没有实现 _a_{a}()")

    def test_labels_are_short(self):
        """用户："不要那么宽"——菜单标签必须短（中文 ≤ 8 字，英文 ≤ 22 字符）。"""
        import re

        cjk = re.compile(r"[\u4e00-\u9fff]")
        for a, label in self._flat(self.tray.default_menu(self.t)):
            limit = 8 if cjk.search(label) else 22
            self.assertLessEqual(len(label), limit, f"{a} 的文案太长（{len(label)}）: {label}")

    def test_first_line_is_just_version(self):
        """状态/端口在 tooltip 里，菜单首行只留版本号（长状态行会把菜单撑宽）。"""
        head = self.tray.default_menu(self.t)[0]
        self.assertIsNone(head[0])
        text = head[1]
        self.assertNotIn("端口", text)
        self.assertNotIn("port", text.lower())
        self.assertLessEqual(len(text), 40)

    def test_port_lives_in_tooltip(self):
        """端口/状态放 tooltip（放进菜单会把它撑宽）。"""
        tip = self.t._tooltip()
        self.assertIn("9", tip, f"tooltip 应含端口: {tip}")
        self.assertTrue(any(w in tip for w in ("未响应", "运行中")), tip)

    def test_teams_status_line(self):
        """托盘菜单要能看到 Teams 机器人就绪状态。"""
        self.t._teams_status = lambda: "就绪"
        menu = self.tray.default_menu(self.t)
        labels = [item[1] for item in menu if item[0] is None]
        self.assertTrue(any("Teams" in x and "就绪" in x for x in labels), labels)
        self.assertIn("Teams", self.t._tooltip())

    def test_start_browser_uses_http_not_login_script(self):
        """托盘启动浏览器必须走服务的按需接口，不依赖登录脚本。"""
        fn = getattr(self.tray.Tray, "_a_start_browser")
        src = __import__("inspect").getsource(fn)
        self.assertIn("/v1/agent/browser", src)
        self.assertNotIn("subprocess", src, "不要在托盘里自己拉浏览器进程")
        self.assertNotIn("scripts", src, "不要依赖 scripts/ 下的脚本")


class I18nTests(unittest.TestCase):
    """托盘菜单三语言：键齐全、标签短、可切换、写回 config.json。"""

    def test_all_langs_complete(self):
        from gantry.tray import TEXT

        keys = set(TEXT["zh"])
        self.assertEqual(keys, set(TEXT["tw"]), "繁中缺词条")
        self.assertEqual(keys, set(TEXT["en"]), "英文缺词条")

    def test_menu_built_per_lang(self):
        from gantry import tray as tray_mod

        need = ("open_webui", "start_browser", "restart", "shutdown")
        for lang in ("zh", "tw", "en"):
            t = tray_mod.Tray(port=9, root=ROOT, lang=lang)
            labels = {a: lab for a, lab in MenuTests._flat(tray_mod.default_menu(t))}
            self.assertEqual(set(labels) >= set(need), True, f"{lang} 菜单不全")
            self.assertEqual(t.lang, lang)
            for a in need:
                self.assertTrue(labels[a].strip())

    def test_set_language_persists_to_config(self):
        """切换语言要写 config.json 的 ui.language，下次启动还是它。"""
        import json
        import tempfile

        from gantry import tray as tray_mod

        tmp = Path(tempfile.mkdtemp(prefix="gantry_tray_cfg_"))
        (tmp / "config.json").write_text(json.dumps({"server": {"port": 9}}), encoding="utf-8")
        t = tray_mod.Tray(port=9, root=tmp, lang="zh")
        t.set_language("en")
        self.assertEqual(t.lang, "en")
        cfg = json.loads((tmp / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["ui"]["language"], "en")
        # 再次构造时应当读到 en
        t2 = tray_mod.Tray(port=9, root=tmp)
        self.assertEqual(t2.lang, "en")

    def test_detect_lang_env_override(self):
        import os
        import tempfile
        from pathlib import Path

        from gantry import tray as tray_mod

        old = os.environ.get("GANTRY_LANG")
        os.environ["GANTRY_LANG"] = "en"
        try:
            # 传一个不存在的根目录让 config.json 分支落空：detect_lang 的优先级
            # 是 config > 环境变量，CI 的 checkout 里现在有 config.json（ui.language=zh），
            # 用真实 ROOT 会把环境变量覆盖测成「配置优先」而失败
            missing_root = Path(tempfile.mkdtemp(prefix="tray_lang_")) / "no_such"
            self.assertEqual(tray_mod.detect_lang(missing_root), "en")
        finally:
            if old is None:
                os.environ.pop("GANTRY_LANG", None)
            else:
                os.environ["GANTRY_LANG"] = old


class DetachTests(unittest.TestCase):
    def test_detach_command_uses_pythonw_and_tray_child(self):
        import start

        cmd = start.build_detach_command([])
        self.assertTrue(cmd[1].endswith("start.py"), cmd)
        self.assertEqual(cmd[2], "--tray-child", "派生出的子进程必须带 --tray-child")
        self.assertTrue(cmd[0].lower().endswith("pythonw.exe") or "python" in cmd[0].lower())

    def test_pythonw_preferred_when_present(self):
        import start

        exe = start._pythonw()
        if exe:
            self.assertTrue(exe.lower().endswith("pythonw.exe"), exe)

    def test_no_uvicorn_import_at_module_time(self):
        """`--help` / `--print-menu` / `--tray-selftest` 不该被 uvicorn 拖住。"""
        for line in START_SRC.splitlines():
            if line.strip() == "import uvicorn":
                self.assertTrue(line.startswith(" "), "uvicorn 必须在函数里 import（顶层会拖慢所有子命令）")

    def test_print_menu_runs_without_service(self):
        import subprocess

        r = subprocess.run([sys.executable, str(ROOT / "start.py"), "--print-menu"],
                           capture_output=True, text=True, encoding="utf-8", timeout=90,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("open_webui", r.stdout)
        self.assertIn("start_browser", r.stdout)


class CrashLogTests(unittest.TestCase):
    """后台模式没有 stderr：任何启动期崩溃都必须留痕（2026-11-01 重启后服务凭空消失）。"""

    def test_start_installs_crash_log(self):
        self.assertIn("crash.log", START_SRC)
        self.assertIn("sys.excepthook", START_SRC)
        self.assertIn("_install_crash_log()", START_SRC)

    def test_crash_log_actually_written(self):
        import sys
        import tempfile

        import start

        tmp = Path(tempfile.mkdtemp(prefix="gantry_crash_"))
        old_base = start.BASE
        start.BASE = tmp
        try:
            path = start._install_crash_log()
            try:
                raise RuntimeError("boom-for-test")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
            self.assertTrue(path.is_file(), "崩溃日志没写出来")
            text = path.read_text(encoding="utf-8")
            self.assertIn("boom-for-test", text)
            self.assertIn("Traceback", text)
        finally:
            start.BASE = old_base
            sys.excepthook = sys.__excepthook__

    def test_tray_restart_waits_for_health(self):
        """托盘报"已重启"之前必须真的等到 /health=ok，而不是睡一会儿就宣布成功。"""
        import inspect

        from gantry import tray as tray_mod

        src = inspect.getsource(tray_mod.Tray._a_restart)
        self.assertIn("/health", src)
        self.assertIn("status", src)
        self.assertIn("restart_timeout", src, "超时要有明确的失败提示")
        self.assertNotIn("time.sleep(1.5)", src, "不许用固定 sleep 假装等到了")

    def test_restart_timeout_text_in_all_langs(self):
        from gantry.tray import TEXT

        for lang in ("zh", "tw", "en"):
            self.assertIn("restart_timeout", TEXT[lang], f"{lang} 缺 restart_timeout")
            self.assertIn("crash.log", TEXT[lang]["restart_timeout"],
                          f"{lang} 的失败提示要指向 crash.log")


class HangDumpTests(unittest.TestCase):
    """卡死 ≠ 崩溃：crash.log 只在异常时写，卡住要靠 faulthandler 吐栈。

    2026-11-02 重启后进程停在 uvicorn 的 "Waiting for application startup"，后台模式
    没有 stderr、crash.log 也是空的，只能干瞪眼——所以加了这个**按需开启**的诊断。
    """

    def test_start_has_hang_dump(self):
        self.assertIn("GANTRY_FAULTHANDLER", START_SRC)
        self.assertIn("dump_traceback(file=handle, all_threads=True)", START_SRC,
                      "要真打线程栈，而不是只建个空文件")
        self.assertIn("hang.txt", START_SRC)
        self.assertIn("_install_hang_dump()", START_SRC)

    def test_opt_in_only(self):
        """默认**不**开（不然每个进程都多根监控线程/定时器）。"""
        import os
        import tempfile

        import start

        old_base, old_env = start.BASE, os.environ.pop("GANTRY_FAULTHANDLER", None)
        tmp = Path(tempfile.mkdtemp(prefix="gantry_hang_"))
        start.BASE = tmp
        try:
            start._install_hang_dump()               # 没设环境变量 → 什么都不做
            self.assertFalse((tmp / "data" / "logs" / "hang.txt").exists())

            os.environ["GANTRY_FAULTHANDLER"] = "1"
            start._install_hang_dump()
            f = tmp / "data" / "logs" / "hang.txt"
            self.assertTrue(f.is_file(), "开了开关就该有 hang.txt")
            body = f.read_text(encoding="utf-8")
            self.assertIn("监控开始", body)
            self.assertIn("每 60s", body)
        finally:
            stop = getattr(start, "_hang_stop", None)
            if stop is not None:
                stop.set()                           # 让守护线程退出
            handle = getattr(start, "_hang_handle", None)
            if handle is not None:
                try:
                    handle.close()
                except Exception:                    # noqa: BLE001
                    pass
            start.BASE = old_base
            if old_env is None:
                os.environ.pop("GANTRY_FAULTHANDLER", None)
            else:
                os.environ["GANTRY_FAULTHANDLER"] = old_env


class LogTests(unittest.TestCase):
    def test_setup_logging_writes_file_and_is_idempotent(self):
        from gantry import logcfg

        tmp = Path(tempfile.mkdtemp(prefix="gantry_log_"))
        p1 = logcfg.setup_logging(tmp, console=False)
        p2 = logcfg.setup_logging(tmp, console=False)
        self.assertEqual(p1, p2, "重复配置应指向同一个日志文件")
        logging.getLogger("gantry.test").info("hello-tray-log")
        self.assertTrue(p1.is_file())
        self.assertIn("hello-tray-log", p1.read_text(encoding="utf-8"))
        # 不能叠加 handler（每个进程只有一个文件 handler，否则日志重复）
        files = [h for h in logging.getLogger().handlers if getattr(h, "_gantry_file", False)]
        self.assertEqual(len(files), 1, f"文件 handler 有 {len(files)} 个")
        # 收拾干净，别影响其它测试
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_gantry_file", False) or getattr(h, "_gantry_console", False):
                logging.getLogger().removeHandler(h)

    def test_handler_level_redact_token_on_disk(self):
        """脱敏 filter 挂在 handler 层：gantry.updater 这类子 logger 传播上来的
        含 token= 记录，落盘文本必须已脱敏（logger 层 filter 覆盖不到传播路径）。"""
        import shutil

        from gantry import logcfg

        tmp = Path(tempfile.mkdtemp(prefix="gantry_redact_"))
        try:
            path = logcfg.setup_logging(tmp, console=False)
            logging.getLogger("gantry.updater").info(
                "下载 release 失败 url=https://api.example.com/?token=SUPERSECRET&x=1")
            for h in logging.getLogger().handlers:
                h.flush()
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("SUPERSECRET", text, "token 不得落盘")
            self.assertIn("token=***", text, "落盘文本必须已脱敏")
        finally:
            for h in list(logging.getLogger().handlers):
                if getattr(h, "_gantry_file", False) or getattr(h, "_gantry_console", False):
                    logging.getLogger().removeHandler(h)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_log_env_override(self):
        from gantry import logcfg

        tmp = Path(tempfile.mkdtemp(prefix="gantry_logenv_"))
        import os

        old = os.environ.get("GANTRY_LOG_DIR")
        os.environ["GANTRY_LOG_DIR"] = str(tmp / "custom")
        try:
            d = logcfg.log_dir(ROOT)
        finally:
            if old is None:
                os.environ.pop("GANTRY_LOG_DIR", None)
            else:
                os.environ["GANTRY_LOG_DIR"] = old
        self.assertEqual(d, tmp / "custom", "GANTRY_LOG_DIR 应生效（便于把日志放到别处）")
        self.assertTrue(d.is_dir())

    def test_tray_uses_file_logger(self):
        """托盘动作要留痕，否则"点了没反应"没法查。"""
        for frag in ("托盘图标已添加", "托盘：启动自动化浏览器", "托盘：重启服务"):
            self.assertIn(frag, TRAY_SRC, f"托盘日志缺少：{frag}")


class ElevatedTrayTests(unittest.TestCase):
    """提权运行时 Windows 不注册托盘图标（UIPI → Shell_NotifyIcon 得 ACCESS_DENIED 5）。

    用户实测：管理员权限下启动，日志里只有「重试 3 次 → 托盘线程异常退出」，
    看不出真正原因。要求：提前判掉、只留一条说明原因的日志，服务照常运行。
    """

    def test_is_elevated_returns_bool(self):
        from gantry import tray

        self.assertIsInstance(tray.is_elevated(), bool,
                              "is_elevated() 必须返回 bool，不能抛异常/返回 None")

    def test_run_skips_window_and_icon_when_elevated(self):
        from unittest import mock

        from gantry import tray

        t = tray.Tray(port=9, root=ROOT)
        with mock.patch.object(tray, "is_elevated", return_value=True), \
                mock.patch.object(tray.Tray, "_create_window",
                                  side_effect=AssertionError("提权时不该建消息窗")), \
                mock.patch.object(tray.Tray, "_add_icon",
                                  side_effect=AssertionError("提权时不该注册图标")):
            with self.assertLogs("gantry.tray", level="WARNING") as cap:
                t._run()                      # 不抛异常 = 服务不会因托盘挂掉
        self.assertEqual(t.skipped_reason, "elevated")
        self.assertTrue(t._ready.is_set(), "跳过也必须置 ready，否则启动方白等 5 秒")
        text = "\n".join(cap.output)
        self.assertIn("管理员权限", text)
        self.assertIn("--no-tray", text, "提示里要给出一条可执行的出路")

    def test_skip_logs_exactly_once(self):
        from unittest import mock

        from gantry import tray

        t = tray.Tray(port=9, root=ROOT)
        with mock.patch.object(tray, "is_elevated", return_value=True):
            with self.assertLogs("gantry.tray", level="WARNING") as cap:
                t._run()
        self.assertEqual(len(cap.output), 1, f"只该有一条告警，实际：{cap.output}")
        self.assertNotIn("重试", "".join(cap.output), "提权场景不该再走 ACCESS_DENIED 重试")

    def test_start_tray_returns_none_and_stays_silent(self):
        """start.py 不该在跳过时再打一条「未能在 5s 内就绪」（误导成启动失败）。"""
        from unittest import mock

        import start

        fake = mock.MagicMock()
        fake.skipped_reason = "elevated"
        fake.wait_ready.return_value = True
        records: list[str] = []

        class _Cap(logging.Handler):
            def emit(self, record):                 # noqa: D102 - 测试用
                records.append(record.getMessage())

        handler = _Cap()
        root_log = logging.getLogger()
        root_log.addHandler(handler)
        try:
            with mock.patch("gantry.tray.Tray", return_value=fake):
                self.assertIsNone(start.start_tray(9),
                                  "跳过后应返回 None（main 里只做 stop，不需要假对象）")
        finally:
            root_log.removeHandler(handler)
        self.assertFalse([m for m in records if "未能在 5s 内就绪" in m],
                         f"跳过时不该报超时：{records}")

    def test_selftest_reports_skip_not_failure(self):
        """`--tray-selftest` 在提权下应报 SKIP(2)，不是 FAIL(1)。"""
        from unittest import mock

        from gantry import tray

        with mock.patch.object(tray, "is_elevated", return_value=True):
            code = tray.selftest(9, ROOT, seconds=0)
        self.assertEqual(code, 2)

    def test_detach_prints_skip_hint_when_elevated(self):
        """提权派生时终端不该再说"托盘图标在右下角"，而要说清楚被跳过了。"""
        from unittest import mock

        import start

        printed: list[str] = []
        with mock.patch("gantry.tray.is_elevated", return_value=True), \
                mock.patch.object(start, "health_ok", return_value=False), \
                mock.patch.object(start, "_has_uvicorn", return_value=True), \
                mock.patch.object(start.subprocess, "Popen", mock.MagicMock()), \
                mock.patch.object(start, "_wait_ready", return_value=True), \
                mock.patch("builtins.print",
                           side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            rc = start.detach(["--lang", "zh"])
        self.assertEqual(rc, 0)
        text = "\n".join(printed)
        self.assertIn("托盘已跳过", text)
        self.assertNotIn("右下角", text)

    def test_detach_prints_normal_hint_when_not_elevated(self):
        from unittest import mock

        import start

        printed: list[str] = []
        with mock.patch("gantry.tray.is_elevated", return_value=False), \
                mock.patch.object(start, "health_ok", return_value=False), \
                mock.patch.object(start, "_has_uvicorn", return_value=True), \
                mock.patch.object(start.subprocess, "Popen", mock.MagicMock()), \
                mock.patch.object(start, "_wait_ready", return_value=True), \
                mock.patch("builtins.print",
                           side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            rc = start.detach(["--lang", "zh"])
        self.assertEqual(rc, 0)
        self.assertIn("右下角", "\n".join(printed))

    def test_no_tray_prints_no_hint(self):
        from unittest import mock

        import start

        printed: list[str] = []
        with mock.patch("gantry.tray.is_elevated", return_value=False), \
                mock.patch.object(start, "health_ok", return_value=False), \
                mock.patch.object(start, "_has_uvicorn", return_value=True), \
                mock.patch.object(start.subprocess, "Popen", mock.MagicMock()), \
                mock.patch.object(start, "_wait_ready", return_value=True), \
                mock.patch("builtins.print",
                           side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            start.detach(["--lang", "zh", "--no-tray"])
        text = "\n".join(printed)
        self.assertNotIn("右下角", text)
        self.assertNotIn("托盘已跳过", text)


if __name__ == "__main__":
    unittest.main()
