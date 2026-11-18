---
name: power-platform
description: 通用 Power Platform 门户操作——Power Apps / Power Automate / Power Pages / Copilot Studio / Power BI / PPAC 的 Playwright UI 工具
---

# 通用 Power Platform 门户操作

由 `plugins/power-platform` 提供，门户域名白名单，复用 EdgeProfile 租户 SSO，
不调用 Power Platform API。默认关闭，需在 `data/plugins.json` 里设置
`power-platform.enabled=true`。

| 工具 | 作用 |
|---|---|
| `pp_status` | 是否启用、可用门户、当前门户 |
| `pp_open(portal, path)` | 打开门户 |
| `pp_probe(portal)` | 检查登录/许可并截图 |
| `pp_snapshot(query)` | 读当前页面控件 |
| `pp_goto(path, portal?)` | 在当前/指定门户内导航 |
| `pp_click(text, role?)` | 按文本点击 |
| `pp_fill(label, value)` | 填输入框 |
| `pp_press(key)` | Enter/Tab/Escape |
| `pp_select(label, value)` | 下拉框 |

门户 key：

```text
powerapps       make.powerapps.com
powerautomate   make.powerautomate.com
powerpages      make.powerpages.microsoft.com
copilotstudio   copilotstudio.microsoft.com
powerbi         app.powerbi.com
ppac            admin.powerplatform.microsoft.com
```

典型：

```text
pp_probe("powerpages")
pp_open("powerpages", "/")
pp_snapshot(query="Create")
pp_click("Create")
pp_snapshot()
```

边界：

- 已覆盖门户的**页面操作**，不等于覆盖产品 API/data plane；Dataverse CRUD、解决方案生命周期、
  `pac` CLI 仍需要专门工具。
- 发布/删除前先让用户确认；`pp_click` 不区分按钮语义。
- 页面结构变化时，先用 `pp_snapshot` 校准。

覆盖矩阵见 [docs/POWER_PLATFORM_COVERAGE.md](../../docs/POWER_PLATFORM_COVERAGE.md)。
