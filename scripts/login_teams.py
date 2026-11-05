"""登录 Teams（人工过 MFA 用）：把自动化浏览器窗口带到前台并等登录完成。

用法：
    python scripts/login_teams.py                 # 默认最多等 15 分钟
    python scripts/login_teams.py --timeout 1800  # MFA 慢就再放宽

它会调 `POST /v1/plugins/teams-bot/api/open-teams` 把 Teams 标签带到前台，
之后每 10 秒查一次 `api/idb-watch`；一旦返回 `ok` 就说明登录成功。
**不需要**关闭窗口或重启服务。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(path: str, body: dict | None = None, timeout: float = 300) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def get(path: str, timeout: float = 300) -> dict:
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


def main() -> int:
    ap = argparse.ArgumentParser(description="登录 Teams（等待 MFA）")
    ap.add_argument("--timeout", type=float, default=900.0, help="最多等多少秒（默认 900）")
    ap.add_argument("--wait", type=float, default=8.0, help="打开标签后等页面渲染的秒数")
    args = ap.parse_args()

    print("→ 把自动化浏览器的 Teams 标签带到前台 …")
    try:
        res = post("/v1/plugins/teams-bot/api/open-teams", {"wait": args.wait}, 600)
    except Exception as exc:                     # noqa: BLE001
        print(f"✗ 调用失败：{exc}")
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res.get("ok"):
        print("✗ 打不开 Teams 页面，先看看 data/logs/gantry.log 尾部")
        return 2
    if res.get("logged_in"):
        print("✓ Teams 已经登录，无需操作")
        return 0

    print("\n请在刚切到前台的浏览器窗口里完成 Teams 登录（MFA 慢慢来，不要关窗口）。")
    print("登录成功后脚本会自动退出；也可随时 Ctrl+C 中断。\n")
    started = time.time()
    last = ""
    while time.time() - started < args.timeout:
        time.sleep(10)
        try:
            d = get("/v1/plugins/teams-bot/api/idb-watch", 300)
        except Exception as exc:                 # noqa: BLE001
            print(f"  … 读取失败（{exc}），继续等")
            continue
        if d.get("ok") is not False:
            total, direct = d.get("total"), d.get("direct")
            print(f"✓ 登录成功：会话 {total} 个（私聊 {direct}）")
            return 0
        auth = d.get("auth")
        msg = f"  … 还没登录（auth={auth}），已等 {int(time.time() - started)}s"
        if msg != last:
            print(msg)
            last = msg
    print(f"✗ 等了 {args.timeout:.0f}s 仍未登录成功；可加大 --timeout 再试")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
