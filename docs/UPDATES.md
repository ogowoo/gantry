# Gantry 更新系统

从 `0.11.0-beta.1` 起，Gantry 提供**检查更新 + 手动一键更新**：

- 只检查，不自动安装；
- 支持 **git 源码仓库** 与 **zip 源码包** 两种安装形态；
- 更新前先备份代码，失败/手动可回滚；
- 更新后自动重启服务；`data/ workspace/ .venv/ EdgeProfile/ config.json` 不会被替换。

## 配置

`config.json`：

```json
{
  "update": {
    "enabled": true,
    "repo": "ogowoo/gantry",
    "channel": "beta",
    "method": "auto",
    "line": "0",
    "token_env": "GITHUB_TOKEN",
    "max_download_mb": 200
  }
}
```

| 键 | 说明 |
|---|---|
| `enabled` | 关闭后 API/CLI 检查与安装都拒绝 |
| `repo` | 只允许 `owner/name`，不能填任意 URL |
| `channel` | `alpha` / `beta` / `preview` / `stable`，默认 `beta` |
| `method` | `auto`（有 .git 用 git，否则 zip）/ `git` / `zip` |
| `line` | 版本主线，默认 `0`；旧 `11.x` 标签不参与更新，未来 `1.0` 时改成 `1` |
| `token_env` | 私有仓库读取的环境变量名，默认 `GITHUB_TOKEN` |
| `max_download_mb` | zip 下载大小上限 |

私有仓库：设置 `GITHUB_TOKEN` 环境变量；token 不会写入日志/返回值。

## 命令行

```powershell
python scripts/update.py status
python scripts/update.py check
python scripts/update.py check --method zip
python scripts/update.py apply --dry-run
python scripts/update.py apply
python scripts/update.py apply --version 0.11.0-beta.2 --method git
python scripts/update.py list
python scripts/update.py rollback --backup <id>
```

CLI 只改代码，**不替你重启服务**；安装/回滚后运行：

```powershell
python start.py
```

## WebUI

- **自动检查**：页面加载后检查一次，之后每 6 小时检查；只提示，不自动安装。
- 有新版本时，侧栏左下角版本号出现 **↑**，点击直接打开「设置 → 危险区 → 更新」。
- 设置 → 危险区 → **更新**：
  - `检查更新`：显示当前/最新版本；
  - 确认后 `POST /v1/update/apply`，服务重启，页面自动等 `/health` 恢复；
  - `回滚最近更新`：恢复最近一次备份并重启。

## API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/v1/update/status` | 当前版本、repo、method、最近备份 |
| GET | `/v1/update/check?method=` | 检查最新版本 |
| POST | `/v1/update/apply` | `{version?,method?,restart?,dry_run?,force?}` |
| POST | `/v1/update/rollback` | `{backup?,restart?}` |

## git 模式

要求：

- 项目根有 `.git`；
- 工作区干净（有未提交改动会拒绝，除非 `force=true`）；
- 有可用的 origin 凭据（已有 credential helper / SSH agent 均可）。

流程：

```text
git fetch --tags
git merge --ff-only <tag commit>
备份记录：data/updates/backups/<id>/backup.json
```

回滚：`git reset --hard <previous_commit>`。

## zip 模式

用于非 git 安装：

1. 优先 GitHub Release 的 `.zip` 资产；没有资产时退到该 tag 的源码包；
2. 可选 SHA256（Release asset digest）校验；
3. 安全解压（拒绝绝对路径/`..`/超大包）；
4. 备份当前代码到 `data/updates/backups/`；
5. 只替换代码条目，保留受保护目录/文件。

## 回滚与故障

- WebUI：设置 → 危险区 → 回滚最近更新
- CLI：`python scripts/update.py rollback`
- 服务起不来：先看 `data/logs/crash.log`，再运行上面的 CLI；代码备份在 `data/updates/backups/`。

## 安全边界

- 只从配置的 GitHub repo 检查/下载；
- zip URL 必须属于 GitHub 域名；
- 不返回 token；
- 更新是**显式动作**，没有后台自动安装；
- 仍然建议更新前 `git status` 干净，并跑一次 `python -m unittest discover -s tests`。
