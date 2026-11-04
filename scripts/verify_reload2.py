# -*- coding: utf-8 -*-
"""等新进程（preview.47）真正就绪后再验证整套：插件 / 工具 / IDB 读侧 / 安全开关。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path, timeout=600):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


print("=== 等 preview.47 就绪")
ver = ""
h = {}
deadline = time.time() + 300
while time.time() < deadline:
    try:
        h = get("/health", 5)
        ver = str(h.get("version") or "")
        if "preview.47" in ver:
            break
    except Exception:                             # noqa: BLE001
        pass
    time.sleep(3)
print("version:", ver, "| browser_state:", h.get("browser_state"),
      "| copilot_ready:", h.get("copilot_ready"))

print("\n=== 插件 / 工具")
pl = get("/v1/plugins", 60)
for p in pl.get("plugins") or []:
    if p.get("name") == "teams-bot":
        print("teams-bot:", p.get("status"), "| tools:", (p.get("contributes") or {}).get("tools"))
st = get("/v1/plugins/teams-bot/api/status", 60)
print("enabled:", st.get("enabled"), "| dry_run:", st.get("dry_run"),
      "| send_enabled:", st.get("send_enabled"), "| source:", st.get("source"))
print("kb:", json.dumps(st.get("kb"), ensure_ascii=False))
tl = get("/v1/agent/tools", 120)
names = [t.get("name") or (t.get("function") or {}).get("name") for t in (tl.get("tools") or [])]
print("工具数:", len(names), "| teams/kb:", [n for n in names if n and (n.startswith("teams_") or n.startswith("kb_"))])

print("\n=== IndexedDB 读侧（浏览器可能要重新加载 Teams）")
for attempt in range(3):
    try:
        w = get("/v1/plugins/teams-bot/api/idb-watch", 900)
        print("total:", w.get("total"), "| direct:", w.get("direct"),
              "| profiles:", w.get("profiles"), "| skypeTenant:", w.get("skypeTenant"))
        for c in (w.get("directSample") or [])[:3]:
            print("   ", c.get("peerName"), "|", str(c.get("lastText"))[:50])
        break
    except Exception as exc:                      # noqa: BLE001
        print(f"第 {attempt + 1} 次失败:", str(exc)[:120])
        time.sleep(15)

print("\n=== 打开会话 + 输入框演练（send=false，不会发送）")
import urllib.error
body = json.dumps({"peer": "Lynn Ling", "snippet": "我在BPM下载下来的",
                   "text": "[Gantry 自检] 只验证输入框", "send": False}).encode()
req = urllib.request.Request(BASE + "/v1/plugins/teams-bot/api/compose", data=body,
                             headers={"Content-Type": "application/json"})
try:
    d = json.loads(urllib.request.urlopen(req, timeout=900).read())
    print(json.dumps(d, ensure_ascii=False)[:400])
except urllib.error.HTTPError as exc:
    print("HTTP", exc.code, exc.read().decode()[:300])
