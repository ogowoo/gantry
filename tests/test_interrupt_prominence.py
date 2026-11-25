# -*- coding: utf-8 -*-
"""中断类提醒：① 文案必须完整翻译；② 必须显眼到不会被漏掉。

用户两条反馈：
- "授权执行的翻译不完整，设置里关闭服务的提示信息也没有完整 i18n 的翻译"
- "中断的提示（授权等）应该要更显眼点，不然可能会没注意到相关的提醒而影响项目"

## 当时的实情
- 授权横幅里**两段说明**和按钮「授权执行」是**裸中文**（没走 `tr()`）,英文界面下
  露出中文；`(该授权仅对这一次操作有效)` 对"本会话"授权也说错了范围；
- 终止服务确认框整段是硬编码中文（设置 → 危险区 → 终止服务）；
- 横幅只是静默出现在内容顶部，没有任何抢注意力的手段——用户"晚了好久才回答"。

## 现在的做法
- 所有授权/终止相关文案走 `tr()`,并按 `scope`（once/session）给不同措辞；
- 横幅 `role="alert"` + `.needs-attn`（左侧 5px 色条 + 呼吸发光 + 粘顶）
  + `scrollIntoView`,并在 `prefers-reduced-motion` 下退化为静态描边；
- 标签标题闪烁「⚠ 需要授权…」,处理完（横幅消失）由 `clearInterruptPing()` 恢复；
- 页面在后台时轻响一声（WebAudio，失败静默、不需要权限）。
"""
from gantry.ui_src import ui_css, ui_js
import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APP = ui_js()
I18N = (BASE / "webui" / "i18n.js").read_text(encoding="utf-8")
_RAW = ui_css()
CSS = re.sub(r"/\*[\s\S]*?\*/", " ", _RAW)

APPROVAL_KEYS = [
    "授权执行",
    "请核对参数后授权。「仅本次」只放行这一次；「整会话放行」会让该工具本会话内不再询问。",
    "已授权并继续执行（本会话内不再逐次询问）",
    "已授权并继续执行（该授权仅对这一次操作有效）",
    "仅本次",
    "整会话放行",
    "确认整会话放行？",
    "拒绝",
    "已拒绝；任务已暂停，可发送新消息继续",
    "暂不授权",
]
SHUTDOWN_KEYS = [
    "即将终止本服务（进程退出）：",
    "正在运行的任务会中断（状态已落库，重启后可恢复）",
    "自动化浏览器会被关闭，本页面随即断开",
    "需要时重新运行 start.py 启动服务",
    "确定终止？",
]


class InterruptI18nTests(unittest.TestCase):
    def test_approval_strings_go_through_tr(self):
        for k in APPROVAL_KEYS:
            self.assertIn(f"tr('{k}')", APP, f"「{k}」必须走 tr()，否则英文界面露中文")

    def test_no_bare_approval_button_label(self):
        for bare in ("textContent='授权执行'", "textContent='仅本次'",
                     "textContent='整会话放行'", "textContent='拒绝'"):
            self.assertNotIn(bare, APP,
                             "按钮文案不能用裸中文（英文模式下会露出来）")

    def test_shutdown_dialog_translated(self):
        for k in SHUTDOWN_KEYS:
            self.assertIn(f"tr('{k}')", APP, f"终止确认框「{k}」必须走 tr()")

    def test_english_entries_exist(self):
        for k in APPROVAL_KEYS + SHUTDOWN_KEYS:
            self.assertIn(f'"{k}"', I18N, f"i18n.js 缺少英文词条: {k}")

    def test_scope_aware_wording(self):
        """会话授权与仅本次授权必须是两个独立动作，不能混成一个按钮。"""
        i = APP.index("function showApprovalBar")
        seg = APP[i:i + 5000]
        self.assertIn("grant(btnOnce,'once',", seg, "「仅本次」必须是独立的 once 动作")
        self.assertIn("grant(btnSession,'session',", seg, "「整会话放行」必须是独立的 session 动作")
        self.assertIn("仅本次", seg)
        self.assertIn("确认整会话放行", seg, "会话级放行需要二次确认")


class InterruptProminenceTests(unittest.TestCase):
    def _fn(self, name: str, span: int = 5000) -> str:
        i = APP.index(f"function {name}")
        return APP[i:i + span]

    def test_approval_bar_grabs_attention(self):
        seg = self._fn("showApprovalBar")
        self.assertIn("role", seg)
        self.assertIn("needs-attn", seg, "要有抢注意力的 class")
        self.assertIn("scrollIntoView", seg, "可能在视口外，要滚进来")
        self.assertIn("pingInterrupt", seg)

    def test_question_bar_grabs_attention(self):
        seg = self._fn("showQuestionBar")
        self.assertIn("needs-attn", seg)
        self.assertIn("pingInterrupt", seg)

    def test_ping_flashes_title(self):
        seg = self._fn("pingInterrupt", 1200)
        self.assertIn("document.title", seg)
        self.assertIn("setInterval", seg)

    def test_ping_cleared_when_resolved(self):
        self.assertIn("function clearInterruptPing", APP)
        # 横幅消失 / 提交成功 都要清
        self.assertGreaterEqual(APP.count("clearInterruptPing()"), 4,
                                "横幅消失与提交成功都要停止闪烁")

    def test_css_pulses_and_respects_reduced_motion(self):
        self.assertIn(".approvalbar.needs-attn", CSS)
        self.assertRegex(CSS, r"@keyframes\s+attn")
        self.assertIn("prefers-reduced-motion", CSS)
        self.assertRegex(CSS, r"\.needs-attn[^{]*\{[^}]*position:sticky|"
                              r"\.approvalbar,\.questionbar\{[^}]*position:sticky|"
                              r"\.approvalbar,.questionbar\{[^}]*sticky")


if __name__ == "__main__":
    unittest.main()
