# -*- coding: utf-8 -*-
"""整套重载后的验证：版本 / 插件加载 / 工具注册 / IndexedDB 读侧 / 安全开关。

重启服务后浏览器会重新启动（Teams 需要重新加载），因此 idb-watch 可能要先等几分钟。
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path, timeout=300):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


def post(path, body=None, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


print("=== 1) 重启服务")
try:
    post("/v1/restart", timeout=30)
    print("restart accepted")
except Exception as exc:                          # noqa: BLE001
    print("restart 请求异常（正常，连接会先断）:", str(exc)[:80])

ver = ""
deadline = time.time() + 180
while time.time() < deadline:
    try:
        h = get("/health", 5)
        ver = str(h.get("version") or "")
        if ver:
            break
    except Exception:                             # noqa: BLE001
        pass
    time.sleep(3)
print("version:", ver, "| browser_state:", (h or {}).get("browser_state"))

print("\n=== 2) 插件加载状态")
try:
    pl = get("/v1/plugins", 60)
    for p in pl.get("plugins") or []:
        if p.get("name") == "teams-bot":
            print("teams-bot:", p.get("status"), "| error:", p.get("error"))
            print("   tools:", (p.get("contributes") or {}).get("tools"))
except Exception as exc:                          # noqa: BLE001
    print("读插件列表失败:", str(exc)[:120])

print("\n=== 3) 机器人状态（安全开关）")
try:
    st = get("/v1/plugins/teams-bot/api/status", 60)
    print("enabled:", st.get("enabled"), "| dry_run:", st.get("dry_run"),
          "| send_enabled:", st.get("send_enabled"), "| source:", st.get("source"))
    print("kb:", json.dumps(st.get("kb"), ensure_ascii=False))
    print("replies:", st.get("replies"), "| seen:", st.get("seen"))
except Exception as exc:                          # noqa: BLE001
    print("status 失败:", str(exc)[:160])

print("\n=== 4) 工具面里是否有 teams_/kb_ 工具")
try:
    tl = get("/v1/agent/tools", 120)
    names = [t.get("name") or (t.get("function") or {}).get("name")
             for t in (tl.get("tools") or [])]
    mine = [n for n in names if n and (n.startswith("teams_") or n.startswith("kb_"))]
    print("共", len(names), "个工具；相关:", mine)
except Exception as exc:                          # noqa: BLE001
    print("读工具列表失败:", str(exc)[:160])

print("\n=== 5) IndexedDB 读侧（可能要等浏览器/Teams 起来）")
for attempt in range(3):
    try:
        w = get("/v1/plugins/teams-bot/api/idb-watch", 600)
        print("total:", w.get("total"), "| direct:", w.get("direct"),
              "| group:", w.get("group"), "| profiles:", w.get("profiles"),
              "| skypeTenant:", w.get("skypeTenant"))
        for c in (w.get("directSample") or [])[:3]:
            print("   ", c.get("peerName"), "|", str(c.get("lastText"))[:50])
        break
    except Exception as exc:                      # noqa: BLE001
        print(f"第 {attempt + 1} 次失败:", str(exc)[:120])
        time.sleep(10)

print("\n=== 6) 日志尾部")
import subprocess

try:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-Content data/logs/gantry.log -Tail 6"],
                         capture_output=True, text=True, timeout=30)
    for line in (out.stdout or "").splitlines():
        print("   ", line[:150])
except Exception as exc:                          # noqa: BLE001
    print("读日志失败:", str(exc)[:80])
