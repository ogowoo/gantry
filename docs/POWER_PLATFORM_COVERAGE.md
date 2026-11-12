# Power Platform 工具覆盖矩阵

日期：2026-11-12  
目标：说明 Gantry 目前能操作 Power Platform 的哪些部分，以及还缺什么。

## 已覆盖

| 产品/入口 | 能力 | 实现 |
|---|---|---|
| Power Automate | 生成工程 / 旧版包 / 解决方案 zip | `gantry/tool_plugins/power_automate.py` + `gantry/power_automate.py` |
| Power Automate | 模型直接操作 maker 门户（probe/snapshot/click/fill/import） | `plugins/power-automate` + `gantry/power_automate_ui.py` |
| Power Apps | 模型直接操作 maker 门户（probe/snapshot/click/fill/select） | `plugins/power-apps` + `gantry/power_apps_ui.py` |

两者都复用 EdgeProfile 的租户 SSO，不调用 Power Platform API；插件默认关闭，
`power-automate.pa_import` 默认 dry-run 只上传不确认。

## 还没覆盖

| 产品/入口 | 典型入口 | 说明 |
|---|---|---|
| Power Pages | `make.powerpages.microsoft.com` | 建站、编辑页面、发布；目前无工具 |
| Microsoft Copilot Studio（原 Power Virtual Agents） | `copilotstudio.microsoft.com` | 建 copilot、主题、发布；目前无工具 |
| Power BI | `app.powerbi.com` | 报表/数据集/刷新；目前无工具 |
| Power Platform 管理中心 | `admin.powerplatform.microsoft.com` | 环境、DLP 策略、容量、分析；目前无工具 |
| Dataverse | 环境内 Tables/Web API | 无表/记录 CRUD 工具；PA 生成器只引用连接器 |
| 连接器 / 自定义连接器 | maker 门户 → Custom connectors | 无创建/测试工具 |
| AI Builder | maker 门户 → AI Builder | 无模型/提示词管理工具 |
| Power Automate Desktop | 本机桌面流 | 无桌面 UI 自动化 |
| Power Platform CLI `pac` | 命令行 | 未集成；可作为后续批量化/CI 路线 |
| 解决方案生命周期 | 导出/导入/升级/环境变量 | 只有 PA 生成 zip；没有跨产品解决方案管理 |

## 建议补齐顺序

1. **通用 Power Platform 浏览器插件**：不要为每个门户复制一套 `*_ui.py`。做一个
   `power-platform` 插件，提供 `pp_open(portal, path)`、`pp_snapshot`、`pp_click`、
   `pp_fill`、`pp_press`、`pp_select`，portal 只允许白名单域名。这样 Power Pages、
   Copilot Studio、Power BI、PPAC 都能先“能操作”，再按需加专用工具。
2. **Dataverse 数据层**：如果目标是“建应用/流时直接读写表”，需要 Dataverse Web API
   或环境内 OData 工具；UI 点击不适合批量数据。
3. **解决方案生命周期**：统一 `solution export/import/upgrade` 与连接引用映射，
   比每个产品单独导入更可靠。
4. **`pac` CLI 集成**：批量/CI 场景比 UI 稳定；可作为“生成 + 导入”的底层。
5. **AI Builder / 自定义连接器**：等前 1–4 稳定后再做，避免 UI 面铺太宽。

## 安全边界

- 所有 UI 操作插件默认 `enabled=false`；启用是显式动作。
- 破坏性/发布类操作应加审批或 `dry_run`；当前 `pa_import` 已 dry-run + approval。
- 不把密码/MFA 写进配置；登录由用户在可见浏览器完成。
- UI 文本/结构会变，工具要保留 `snapshot` 作为校准入口。
