# -*- coding: utf-8 -*-
"""托盘「检查更新」升级为可安装更新：确认/取消/失败/成功四条路径（HTTP 全 mock）。"""
import email.message
import unittest
import urllib.error
from typing import cast
from unittest import mock

import gantry.tray as tray

ROOT = tray.Path(__file__).resolve().parent.parent


def _mk():
    t = tray.Tray(port=9, root=ROOT, lang="zh")
    t.balloon = cast(mock.Mock, mock.Mock())   # 断言走 _balloon(t) 看 Mock 视角
    return t


def _balloon(t):
    return cast(mock.Mock, t.balloon)


class TestTrayUpdate(unittest.TestCase):
    def test_no_update_never_confirms(self):
        t = _mk()
        t._http = mock.Mock(return_value={"update_available": False})
        t._confirm = mock.Mock()
        t._a_check_update()
        t._confirm.assert_not_called()
        self.assertEqual(t._http.call_count, 1)
        self.assertIn("最新", _balloon(t).call_args[0][1])

    def test_decline_does_not_apply(self):
        t = _mk()
        t._http = mock.Mock(return_value={
            "update_available": True, "latest": "9.9.9", "method": "zip"})
        t._confirm = mock.Mock(return_value=False)
        t._a_check_update()
        self.assertEqual(t._http.call_count, 1)          # 只查了没装

    def test_confirm_applies_with_restart(self):
        t = _mk()
        t._http = mock.Mock(side_effect=[
            {"update_available": True, "latest": "9.9.9", "method": "zip"},
            {"target": "9.9.9"},
        ])
        t._confirm = mock.Mock(return_value=True)
        t._a_check_update()
        args, kwargs = t._http.call_args_list[1]
        self.assertEqual(args[0], "/v1/update/apply")
        self.assertEqual(args[1], {"version": "9.9.9", "method": "zip", "restart": True})
        self.assertGreaterEqual(kwargs["timeout"], 600)  # 下载+依赖同步给足时间
        self.assertIn("9.9.9", _balloon(t).call_args[0][1])

    def test_apply_http_error_balloons_detail(self):
        t = _mk()
        err = urllib.error.HTTPError(
            "http://x", 400, "Bad Request", email.message.Message(), None)
        err.read = mock.Mock(return_value=b'{"detail": "\\u66f4\\u65b0\\u7cfb\\u7edf\\u672a\\u542f\\u7528"}')
        t._http = mock.Mock(side_effect=[
            {"update_available": True, "latest": "9.9.9", "method": "zip"}, err])
        t._confirm = mock.Mock(return_value=True)
        t._a_check_update()
        kind, text = _balloon(t).call_args[0][0], _balloon(t).call_args[0][1]
        self.assertEqual(kind, t.text["failed"])
        self.assertIn("更新系统未启用", text)
        self.assertTrue(_balloon(t).call_args[1].get("error"))

    def test_confirm_dialog_failure_defaults_to_no(self):
        t = _mk()
        t._user32 = mock.Mock()
        t._user32.MessageBoxW.side_effect = OSError("no win32")
        self.assertFalse(t._confirm("c", "t"))


if __name__ == "__main__":
    unittest.main()
