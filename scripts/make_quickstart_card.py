"""生成一页版「快速安装卡片」图片：简体 / 繁體 / English 各一张。

设计：极简版，和 docs/QUICKSTART.md 保持一致——
左侧 5 步（准备 → 安装 → 登录 → 启动 → 第一个任务），
右侧常用操作 + 常见问题；用 Playwright 渲染 HTML 再截图（2x）。
用法：python scripts/make_quickstart_card.py
输出：docs/quickstart-zh.png / quickstart-zh-TW.png / quickstart-en.png
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs"

CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
  background: #eef2f7; color: #1b2430; }
.card { width: 1240px; padding: 34px 38px 30px; background: #eef2f7; }
.head { display: flex; align-items: center; gap: 14px; margin-bottom: 6px; }
.logo { width: 44px; height: 44px; border-radius: 12px; background: #0b7a5a;
  display: flex; align-items: center; justify-content: center; color: #fff;
  font-weight: 800; font-size: 22px; letter-spacing: -.5px; }
h1 { font-size: 30px; margin: 0; letter-spacing: -.4px; }
.sub { color: #5b6b7e; font-size: 15px; margin: 2px 0 22px; }
.grid { display: grid; grid-template-columns: 1.02fr .98fr; gap: 22px; align-items: start; }
.step { display: flex; gap: 12px; background: #fff; border: 1px solid #dfe6ef;
  border-radius: 14px; padding: 14px 16px; margin-bottom: 12px; }
.num { flex: none; width: 26px; height: 26px; border-radius: 50%; background: #2563eb;
  color: #fff; font-size: 14px; font-weight: 700; display: flex; align-items: center;
  justify-content: center; margin-top: 1px; }
.step h3 { margin: 0 0 6px; font-size: 16.5px; }
.step p { margin: 0 0 6px; font-size: 13.5px; color: #41505f; line-height: 1.55; }
.step p:last-child { margin-bottom: 0; }
code { font-family: "Cascadia Mono", Consolas, monospace; font-size: 12.8px;
  background: #f3f6fa; border: 1px solid #e2e9f1; border-radius: 6px;
  padding: 1px 6px; color: #16324f; }
pre { margin: 6px 0 0; background: #0f1621; color: #d7e3f4; border-radius: 10px;
  padding: 10px 12px; font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 12.6px; line-height: 1.65; white-space: pre; overflow: hidden; }
pre .c { color: #7c8ba1; }
.panel, .faq { background: #fff; border: 1px solid #dfe6ef; border-radius: 14px;
  padding: 14px 16px; margin-bottom: 12px; }
.panel h3, .faq h3 { margin: 0 0 10px; font-size: 16px; }
.faq { margin-top: 0; }
table { width: 100%; border-collapse: collapse; font-size: 13.2px; }
td { padding: 6px 0; border-top: 1px solid #eef2f7; vertical-align: top; line-height: 1.5;
  word-break: keep-all; overflow-wrap: anywhere; }
tr:first-child td { border-top: 0; }
td.k { color: #2563eb; font-weight: 600; width: 38%; padding-right: 12px; }
.faq td.k { color: #b45309; }
.foot { margin-top: 14px; color: #7a8798; font-size: 12.5px; display: flex;
  justify-content: space-between; gap: 16px; }
"""

# ── 文案：三语；结构对齐 docs/QUICKSTART.md ───────────────────
TEXT = {
    "zh": {
        "title": "Gantry · 快速安装",
        "sub": "Windows + Edge + Python 3.11+ ｜ 5 分钟从零跑到第一个任务",
        "steps": [
            ("准备", [
                ("p", "Windows 10/11、Microsoft Edge、Python 3.11+。"),
            ]),
            ("安装", [
                ("", ["git clone https://github.com/ogowoo/gantry.git",
                      "cd gantry",
                      "python install.py",
                      "copy config.example.json config.json"]),
                ("p", "install.py 会安装依赖和 Playwright 浏览器。"),
            ]),
            ("登录 Copilot（只做一次）", [
                ("", ["python scripts/login.py"]),
                ("p", "在 Edge 里登录 M365 账号；看到 Copilot 聊天页后回命令行按回车。"
                      "登录态存 EdgeProfile/，之后不用再登。"),
            ]),
            ("启动", [
                ("", ["python start.py"]),
                ("p", "打开 http://127.0.0.1:8765/ui；顶部显示「Copilot 就绪」即可用。"),
            ]),
            ("跑第一个任务", [
                ("p", "① 输入任务　② 选模型（copilot-auto 够用）　③ 选工作目录　"
                      "④ 选权限（默认「工作区内修改」）　⑤ 点「Agent 执行」→「开始」。"),
            ]),
        ],
        "ops_title": "常用操作",
        "ops": [
            ("暂停 / 继续", "任务条「暂停」→「恢复」从检查点续跑"),
            ("换模型", "任务条右侧下拉；限流时用挂起卡片里的「换模型继续」"),
            ("授权", "弹出横幅点「授权执行」（本会话内不再逐次询问）"),
            ("重启服务", "设置 → 危险区 → 重启服务"),
            ("回滚改动", "更多 ▾ → 回滚任务改动…"),
        ],
        "faq_title": "常见问题",
        "faq": [
            ("Copilot 未就绪", "点「重试浏览器」；仍不行重跑 python scripts/login.py"),
            ("端口被占用", "改 config.json 的 server.port 再启动"),
            ("想用 DeepSeek/Kimi", "设置环境变量 DEEPSEEK_API_KEY，模型列表自动出现"),
        ],
        "foot_left": "详细说明：docs/QUICKSTART.md ｜ README.md",
        "foot_right": "权限三档：仅可查看 / 工作区内修改（默认） / 完全权限",
    },
    "zh-TW": {
        "title": "Gantry · 快速安裝",
        "sub": "Windows + Edge + Python 3.11+ ｜ 5 分鐘從零跑到第一個任務",
        "steps": [
            ("準備", [
                ("p", "Windows 10/11、Microsoft Edge、Python 3.11+。"),
            ]),
            ("安裝", [
                ("", ["git clone https://github.com/ogowoo/gantry.git",
                      "cd gantry",
                      "python install.py",
                      "copy config.example.json config.json"]),
                ("p", "install.py 會安裝依賴與 Playwright 瀏覽器。"),
            ]),
            ("登入 Copilot（只做一次）", [
                ("", ["python scripts/login.py"]),
                ("p", "在 Edge 裡登入 M365 帳號；看到 Copilot 聊天頁後回命令列按 Enter。"
                      "登入狀態存 EdgeProfile/，之後不用再登。"),
            ]),
            ("啟動", [
                ("", ["python start.py"]),
                ("p", "開啟 http://127.0.0.1:8765/ui；頂部顯示「Copilot 就緒」即可使用。"),
            ]),
            ("跑第一個任務", [
                ("p", "① 輸入任務　② 選模型（copilot-auto 夠用）　③ 選工作目錄　"
                      "④ 選權限（預設「工作區內修改」）　⑤ 點「Agent 執行」→「開始」。"),
            ]),
        ],
        "ops_title": "常用操作",
        "ops": [
            ("暫停 / 繼續", "任務條「暫停」→「恢復」從檢查點續跑"),
            ("換模型", "任務條右側下拉；限流時用掛起卡片裡的「換模型繼續」"),
            ("授權", "彈出橫幅點「授權執行」（本工作階段內不再逐次詢問）"),
            ("重啟服務", "設定 → 危險區 → 重啟服務"),
            ("回滾改動", "更多 ▾ → 回滾任務改動…"),
        ],
        "faq_title": "常見問題",
        "faq": [
            ("Copilot 未就緒", "點「重試瀏覽器」；仍不行重跑 python scripts/login.py"),
            ("連接埠被占用", "改 config.json 的 server.port 再啟動"),
            ("想用 DeepSeek/Kimi", "設定環境變數 DEEPSEEK_API_KEY，模型列表自動出現"),
        ],
        "foot_left": "詳細說明：docs/QUICKSTART.md ｜ README.md",
        "foot_right": "權限三檔：僅可檢視 / 工作區內修改（預設） / 完整權限",
    },
    "en": {
        "title": "Gantry · Quick install",
        "sub": "Windows + Edge + Python 3.11+ ｜ zero to your first task in 5 minutes",
        "steps": [
            ("Prerequisites", [
                ("p", "Windows 10/11, Microsoft Edge and Python 3.11+."),
            ]),
            ("Install", [
                ("", ["git clone https://github.com/ogowoo/gantry.git",
                      "cd gantry",
                      "python install.py",
                      "copy config.example.json config.json"]),
                ("p", "install.py installs dependencies and the Playwright browser."),
            ]),
            ("Sign in to Copilot (once)", [
                ("", ["python scripts/login.py"]),
                ("p", "Sign in to your M365 account in Edge; when the Copilot chat page "
                      "appears, return to the terminal and press Enter. The session is kept "
                      "in EdgeProfile/."),
            ]),
            ("Start", [
                ("", ["python start.py"]),
                ("p", "Open http://127.0.0.1:8765/ui; when the top bar says "
                      "\"Copilot ready\" you are good to go."),
            ]),
            ("Run your first task", [
                ("p", "1) Describe the task  2) pick a model (copilot-auto is fine)  "
                      "3) pick the working directory  4) pick permissions (default "
                      "\"Modify workspace\")  5) click Agent run → Start."),
            ]),
        ],
        "ops_title": "Everyday actions",
        "ops": [
            ("Pause / resume", "task bar \"Pause\" → \"Resume\" continues from the checkpoint"),
            ("Switch model", "model dropdown on the task bar; when rate-limited use "
                             "\"Switch model & continue\""),
            ("Approve tools", "click \"Approve\" on the banner (no more prompts this session)"),
            ("Restart service", "Settings → Danger zone → Restart service"),
            ("Roll back changes", "More ▾ → Roll back task changes…"),
        ],
        "faq_title": "Troubleshooting",
        "faq": [
            ("Copilot not ready", "click \"Retry browser\"; if it persists re-run "
                                  "python scripts/login.py"),
            ("Port already in use", "change server.port in config.json and restart"),
            ("Use DeepSeek/Kimi", "set DEEPSEEK_API_KEY; the model list updates automatically"),
        ],
        "foot_left": "Details: docs/QUICKSTART.md ｜ README.md",
        "foot_right": "Permissions: View only / Modify workspace (default) / Full access",
    },
}


def esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def html(lang: str) -> str:
    t = TEXT[lang]
    steps = []
    for idx, (title, blocks) in enumerate(t["steps"], 1):
        inner = []
        for kind, payload in blocks:
            if kind == "p":
                inner.append(f"<p>{esc(payload)}</p>")
            else:
                lines = []
                for c in payload:
                    if "  # " in c:
                        cmd, note = c.split("  # ", 1)
                        lines.append(f'{esc(cmd)}<span class="c">  # {esc(note)}</span>')
                    else:
                        lines.append(esc(c))
                inner.append("<pre>" + "\n".join(lines) + "</pre>")
        steps.append(f'<div class="step"><div class="num">{idx}</div><div style="flex:1 1 auto">'
                     f'<h3>{esc(title)}</h3>{"".join(inner)}</div></div>')

    def table(rows, cls=""):
        body = "".join(f'<tr><td class="k">{esc(k)}</td><td>{esc(v)}</td></tr>'
                       for k, v in rows)
        return f'<table class="{cls}">{body}</table>'

    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<style>{CSS}</style></head><body><div class="card">
<div class="head"><div class="logo">G</div><h1>{esc(t['title'])}</h1></div>
<div class="sub">{esc(t['sub'])}</div>
<div class="grid">
  <div>{''.join(steps)}</div>
  <div>
    <div class="panel"><h3>⌨ {esc(t['ops_title'])}</h3>{table(t['ops'])}</div>
    <div class="faq"><h3>🩺 {esc(t['faq_title'])}</h3>{table(t['faq'], 'faqtable')}</div>
  </div>
</div>
<div class="foot"><span>{esc(t['foot_left'])}</span><span>{esc(t['foot_right'])}</span></div>
</div></body></html>"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1240, "height": 1000}, device_scale_factor=2)
        for lang in ("zh", "zh-TW", "en"):
            src = html(lang)
            tmp = OUT / f".quickstart-{lang}.html"
            tmp.write_text(src, encoding="utf-8")
            page.goto(tmp.as_uri(), wait_until="load")
            page.wait_for_timeout(250)
            el = page.query_selector(".card")
            png = OUT / f"quickstart-{lang}.png"
            el.screenshot(path=str(png))
            tmp.unlink(missing_ok=True)
            made.append((png.name, png.stat().st_size))
        browser.close()
    for name, size in made:
        print(f"  {name}: {size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
