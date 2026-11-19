# GIMP MCP 接入

日期：2026-11-19

## 当前配置

Gantry 的 `mcp` 插件连接：

```json
{
  "name": "gimp",
  "transport": "stdio",
  "command": "C:\\Tools\\Test4All\\CopilotBridge_Agent\\.venv\\Scripts\\python.exe",
  "args": ["C:\\Tools\\Test4All\\CopilotBridge_Agent\\scripts\\gimp_mcp_stdio.py"]
}
```

`scripts/gimp_mcp_stdio.py` 直接 exec 到 uv 缓存里的 `gimp-mcp.exe`。
不直接用 `uvx gimp-mcp serve`，因为 Windows 上 uvx 多一层进程会导致
Gantry MCP 客户端的 `tools/list` 超时。

## 前置条件

1. 已安装 GIMP 3.2。
2. `uvx gimp-mcp doctor` 全部通过，尤其是：

```text
A running GIMP answers on the bridge
```

3. GIMP 正在运行；没有 GIMP 时 `gimp_status` 会显示断连。

## 工具

MCP server 连接后注册为：

```text
mcp__gimp__gimp_status
mcp__gimp__gimp_list_toolsets
mcp__gimp__gimp_enable_toolsets
mcp__gimp__gimp_inspect_image
mcp__gimp__gimp_preview
mcp__gimp__gimp_create_image
mcp__gimp__gimp_open_image
mcp__gimp__gimp_save_image
mcp__gimp__gimp_export_image
mcp__gimp__gimp_create_layer
mcp__gimp__gimp_create_text_layer
mcp__gimp__gimp_fill
mcp__gimp__gimp_draw_shape
mcp__gimp__gimp_gaussian_blur
mcp__gimp__gimp_create_checkpoint
...
```

完整列表见 MCP 面板或 `GET /v1/plugins/mcp/api/list`。

## 冒烟测试

```powershell
# 1. 确认 GIMP bridge
uvx gimp-mcp doctor

# 2. Gantry 侧状态
curl -H "X-Gantry-Token: <token>" http://127.0.0.1:8765/v1/plugins/mcp/api/call ^
  -H "Content-Type: application/json" ^
  -d "{\"server\":\"gimp\",\"tool\":\"gimp_status\",\"arguments\":{}}"
```

预期：

```json
{"connected":true,"gimp_version":"3.2.8","images":[]}
```

## 已知修复

`plugins/mcp/plugin.py` 的 stdio 子进程设置了 16 MB 的 StreamReader 上限。
gimp-mcp 的 `tools/list` 响应超过 asyncio 默认 64 KB，旧代码会在
`readline()` 抛 `LimitOverrunError`，表现为 `tools/list 超时 30s`。

## 安全

GIMP MCP 修改的是用户正在运行的真实 GIMP 文档。保存/导出前必须确认路径，
大改前先建 checkpoint。
