# 快速安装手册

> 5 分钟从零跑到第一个任务。

## 1. 准备

- Windows 10/11 + Microsoft Edge + Python 3.11+

## 2. 安装（3 条命令）

```powershell
git clone https://github.com/ogowoo/gantry.git
cd gantry
python install.py
copy config.example.json config.json
```

> 可选：`--ocr`（扫描件 OCR）、`--xiaomi`（表格/公式）、`--local`（本地 GGUF 模型）

## 3. 登录（只做一次）

```powershell
python scripts/login.py
```

Edge 里登录 M365 账号，看到 Copilot 聊天页后回命令行按回车。

## 4. 启动

```powershell
python start.py
```

打开 **http://127.0.0.1:8765/ui** ，顶部显示 `Copilot 就绪` 即可用。

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
| Copilot 未就绪 | 点「重试浏览器」，或重跑 `login.py` |
| 端口被占 | `config.json` 改 `server.port` |
| 想用 DeepSeek/Kimi | 设环境变量 `DEEPSEEK_API_KEY` 等，模型列表自动出现 |

---

更全的文档 → [README](../README.md)
