# Power Apps Canvas App 源码生成与 pac

日期：2026-11-15

## 作用

`power_apps_generate` 生成 Power Apps Canvas App 的 unpacked source：

```text
apps/<name>/
├── CanvasManifest.json
└── Src/
    ├── App.pa.yaml
    └── <Screen>.pa.yaml
```

这是 `pac canvas pack` 接受的 **SourceCode layout**，比点击 Canvas 模板/尺寸弹窗稳定。
`power_apps_pack` 可选调用 `pac` 打包成 `.msapp`；没装 pac 时仍可生成、审查 YAML。

## pac 需要安装什么

`pac` 是 **Power Platform CLI**，本身不是 Python 包。两种安装方式：

### A. Windows MSI（Windows 推荐）

1. 下载 `powerapps-cli-1.0.msi`（微软官方安装包）
2. 安装后打开新终端，运行 `pac --version`
3. 更新：`pac install latest`

官方文档：
[Install Power Platform CLI using Windows MSI](https://learn.microsoft.com/en-us/power-platform/developer/howto/install-cli-msi)

### B. .NET Tool（跨平台）

前置：安装 .NET SDK（官方推荐 .NET 10）。

```powershell
dotnet tool install --global Microsoft.PowerApps.CLI.Tool
pac --version
```

如果 `pac` 不在 PATH，通常在：

```text
%USERPROFILE%\.dotnet\tools\pac.exe
```

官方文档：
[Install Power Platform CLI with .NET tool](https://learn.microsoft.com/en-us/power-platform/developer/howto/install-cli-net-tool)

## 打包命令

```powershell
pac canvas pack `
  --sources apps/gantry-demo `
  --msapp apps/gantry-demo.msapp `
  --layout SourceCode `
  --overwrite
```

`pack` / `unpack` 目前是 **Preview / deprecated**，微软建议长期使用
[Git integration](https://learn.microsoft.com/en-us/power-platform/alm/git-integration/canvas-apps-git-integration)；
但作为本地打包/导入的桥接仍然可用。

## 认证

`pac canvas pack/unpack` 是本地文件操作，不需要 `pac auth`。  
`pac canvas list/download`、`pac solution import` 等环境命令才需要：

```powershell
pac auth create --environment https://org.crm.dynamics.com
```

## 与 UI 操作的关系

- **声明式生成**：默认推荐，可 diff、可重复、可 CI。
- **Playwright UI**：登录、一次性导入、官方未开放的能力。
- **pac**：可选外部适配器；不是核心 Python 运行时依赖。
