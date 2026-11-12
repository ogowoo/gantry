---
name: power-apps
description: 用 Playwright 直接操作 Power Apps maker 门户——打开应用、查看/点击/填写、编辑与发布前的页面操作
---

# Power Apps maker 门户直接操作

由 `plugins/power-apps` 提供，复用 Gantry 的 EdgeProfile / 租户 SSO，
不调用 Power Platform API。默认关闭，需在 `data/plugins.json` 里设置
`power-apps.enabled=true`。

| 工具 | 作用 |
|---|---|
| `papp_status` | 看是否启用 |
| `papp_probe` | 打开 `make.powerapps.com`，检查登录/许可并截图 |
| `papp_snapshot(query)` | 读取当前页面可见控件，可按文本/aria 过滤 |
| `papp_goto(path)` | 导航，如 `/apps`、`/solutions` |
| `papp_click(text, role?)` | 按可见文本点击按钮/菜单/链接 |
| `papp_fill(label, value)` | 按 label/placeholder 填输入框 |
| `papp_press(key)` | Enter/Tab/Escape 等 |
| `papp_select(label, value)` | 选择下拉框 |

典型顺序：

```text
papp_probe()
papp_goto("/apps")
papp_snapshot(query="Create")
papp_click("Create")
papp_snapshot(query="Canvas")
papp_click("Canvas app")
papp_fill("App name", "daily-report")
papp_click("Create")
```

原则：

- 每步先 `papp_snapshot` 拿控件，再点击/填写；Power Apps 是 SPA + iframe。
- 发布/删除等破坏性操作前，先让用户确认；`papp_click` 本身不区分按钮语义。
- 需要可复现的解决方案/应用包时，用解决方案 zip 流程，而不是逐页点。

## 开发参考

- 后端：`gantry/power_apps_ui.py`
- 插件：`plugins/power-apps/plugin.py`
- 覆盖矩阵：[docs/POWER_PLATFORM_COVERAGE.md](../../docs/POWER_PLATFORM_COVERAGE.md)
