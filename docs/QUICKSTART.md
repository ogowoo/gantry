# 快速安装手册

> 5 分钟从零跑到第一个任务。

> 🧪 **[beta.16 更新测试标记]** 如果你的 `docs/QUICKSTART.md` 里出现这一行，
> 说明控制台在线更新已经成功把代码换成本版本（`0.11.0-beta.16`）。

## 1. 准备

- Windows 10/11 + Microsoft Edge + Python 3.11+

## 2. 安装（3 条命令）

```powershell
git clone https://github.com/ogowoo/gantry.git
cd gantry
python install.py
```

> `install.py` 会自动从 `config.example.json` 生成 `config.json`；已存在则保留。
> 不需要再手工 `copy`。

> 可选：`--ocr`（扫描件 OCR）、`--xiaomi`（表格/公式）、`--local`（本地 GGUF 模型）

## 3. 启动

```powershell
python start.py
```

打开 **http://127.0.0.1:8765/ui**。首次启动可能显示 `未就绪` / `等待登录`，这是正常的。

## 4. 首次登录（可选，只做一次）

**不需要先跑 `scripts/login.py`。** 推荐直接在 console/托盘里唤起自动化浏览器：

- WebUI 顶部点 **「重试浏览器」**，或托盘右键 → **「启动自动化浏览器」**
- 在弹出的自动化 Edge 里登录 M365 账号
- 回到 WebUI 再点一次 **「重试浏览器」**，顶部变成 `Copilot 就绪`

登录态保存在 `EdgeProfile/`，之后启动不需要再登录。

> 也可以使用独立登录向导：先停止服务，再运行 `python scripts/login.py`。  
> 它只是可选的 CLI 入口，不是启动服务的必需步骤。

## 5. 跑任务

1. 输入框写任务 → 选模型（`copilot-auto` 够用）
2. 选工作目录（默认 `workspace`）
3. 选权限（默认 `工作区内修改`）
4. 点 **Agent 执行 → 开始**

## 6. 常用

| 操作 | 方法 |
|---|---|
| 暂停/继续 | 任务条「暂停」→「恢复」 |
| 换模型 | 任务条右侧下拉 |
| 授权 | 弹出横幅点「授权执行」（本会话内不再问） |
| 重启服务 | 设置 → 危险区 → 重启服务 |

## 7. 问题

| 现象 | 处理 |
|---|---|
| Copilot 未就绪 | 先点「重试浏览器」，在自动化 Edge 完成登录后再点一次；仍不行才停服务跑 `scripts/login.py` |
| 端口被占 | `config.json` 改 `server.port` |
| 想用 DeepSeek/Kimi | 设环境变量 `DEEPSEEK_API_KEY` 等，模型列表自动出现 |

---

一页图：[简体](quickstart-zh.png) · [繁體](quickstart-zh-TW.png) · [English](quickstart-en.png)

更全的文档 → [README](../README.md)
