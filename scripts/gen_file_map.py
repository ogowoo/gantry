# -*- coding: utf-8 -*-
"""生成 docs/FILE_MAP.md：按目录列出每个文件的功能与关键入口。"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CURATED = {
    "start.py": "统一启动入口：解析参数、选择模式、拉起 FastAPI/uvicorn。",
    "app.py": "应用装配层：创建 FastAPI、挂路由、生命周期与静态资源。",
    "install.py": "安装脚本：依赖、浏览器、模型与可选组件。",
    "pyproject.toml": "项目元数据、依赖、脚本入口与工具配置。",
    "README.md": "项目主说明（英文）。",
    "README.zh-CN.md": "项目主说明（简体中文）。",
    "README.zh-TW.md": "项目主说明（繁体中文）。",
    "AGENT.md": "Agent 使用/维护说明。",
    "config.example.json": "配置样例（提供商、浏览器、工作区等）。",
    ".env.example": "环境变量样例。",
    "gantry/version.py": "版本号唯一来源与版本解析/比较工具。",
    "gantry/api.py": "对外 HTTP API 的聚合导出层（把各 routes_* 组合进来）。",
    "gantry/bridge.py": "全局运行时桥：配置、浏览器、模型、Agent、工具注册表的共享状态。",
    "gantry/routes_core.py": "核心路由：健康检查、重启、浏览器、Power Automate UI 等。",
    "gantry/routes_agents.py": "Agent/子代理/记忆/自检相关路由。",
    "gantry/routes_chat.py": "聊天补全与流式响应路由。",
    "gantry/routes_config.py": "配置、插件、面板、插件 API 分发路由。",
    "gantry/routes_files.py": "文件浏览/读写/工作区相关路由。",
    "gantry/routes_tools.py": "工具列表/调用/权限相关路由。",
    "gantry/tools.py": "工具注册表与内置非文件工具；加载 tool_plugins。",
    "gantry/tool_plugin_loader.py": "扫描 gantry/tool_plugins/*.py 并 register(registry)。",
    "gantry/tools_files.py": "文件工具兼容层：把旧 FilesMixin 方法转发到 tool_plugins。",
    "gantry/__init__.py": "Gantry 核心包初始化。",
    "tests/__init__.py": "测试包初始化。",
    "plugins/coding-harness-tools/plugin.py": "Coding harness 工具插件：注册编码/校验相关工具。",
    "scripts/login.py": "浏览器登录辅助：检查 Playwright/服务、重置 profile、执行登录。",
    "scripts/login_teams.py": "Teams 登录辅助：打开登录页并等待人工完成 ADFS/MFA。",
    "requirements.txt": "运行依赖清单。",
    "requirements-lock.txt": "锁定版本的完整依赖清单。",
    "requirements-local.txt": "本地模型/推理相关可选依赖。",
    "requirements-embed.txt": "向量/嵌入相关可选依赖。",
    "requirements-ocr.txt": "OCR 相关可选依赖。",
    "requirements-ocr-xiaomi.txt": "小米 OCR 相关可选依赖。",
    ".gitignore": "Git 忽略规则（data/、workspace/、.venv 等）。",
    ".gitattributes": "Git 属性（换行/语言统计等）。",
    "gantry/plugins.py": "插件系统：发现、加载、启停、面板、插件 API、权限。",
    "gantry/teams.py": "Teams 自动化 facade：re-export DOM/发送/API/mentions 原语。",
    "gantry/teams_dom.py": "Teams 页面原语与 DOM 选择器（_SEL）：打开会话、点行、头部标题。",
    "gantry/teams_send.py": "Teams 发送核心：定位会话、输入、点发送、标题校验。",
    "gantry/teams_idb.py": "从 Teams IndexedDB 读会话/最后消息（读侧主路径）。",
    "gantry/teams_api.py": "Teams chatsvc 内部 API 探测与可选发送。",
    "gantry/teams_mentions.py": "Chat → Mentions 读取/点行打开会话。",
    "gantry/teams_logic.py": "Teams 纯逻辑：提及解析、问题提取、去重、分类（可离线测）。",
    "gantry/teams_common.py": "Teams 公共常量、页面锁、配置、身份规范化。",
    "gantry/teams_outbox.py": "Teams 发送 outbox：预留/成功/失败/幂等。",
    "gantry/power_automate.py": "Power Automate 工程/旧版包/解决方案包生成与校验。",
    "gantry/power_automate_ui.py": "maker 门户 UI 自动化：probe/login/dom/import。",
    "gantry/browser.py": "Playwright 浏览器生命周期、持久化 profile、登录态恢复。",
    "gantry/session_store.py": "会话/步骤持久化与检索。",
    "gantry/memory.py": "长期记忆存储与检索。",
    "gantry/kb.py": "知识库索引/搜索（SQLite FTS）。",
    "gantry/models.py": "模型提供商注册、选择与补全调用。",
    "gantry/providers.py": "各 LLM 提供商的适配与发现。",
    "gantry/agent.py": "Agent 主循环、工具调用与消息编排。",
    "gantry/agent_parse.py": "Agent 输出解析：从模型文本提取工具调用/参数（ParseMixin）。",
    "gantry/agent_prompt.py": "Agent 系统提示词与上下文拼装（PromptMixin）。",
    "gantry/agent_text.py": "Agent 文本清洗、截断与格式化（TextMixin）。",
    "gantry/editmatch.py": "文本替换/补丁匹配（含模糊匹配）。",
    "gantry/page.py": "通用页面操作/登录页识别/菜单选择。",
    "gantry/owa.py": "Outlook Web 自动化后端。",
    "gantry/copilot.py": "Copilot Web 自动化后端。",
    "gantry/office.py": "Office 文档读写/转换。",
    "gantry/i18n.py": "后端国际化。",
    "gantry/tray.py": "系统托盘/后台进程管理。",
    "gantry/updater.py": "更新检查与自升级。",
    "gantry/security.py": "敏感配置、脱敏、权限与命令安全。",
    "gantry/workspace.py": "工作区路径、隔离与清理。",
    "gantry/audit.py": "审计日志与事件。",
    "gantry/diagnostics.py": "诊断信息收集。",
    "gantry/logging_setup.py": "日志初始化与轮转。",
    "gantry/errors.py": "统一异常类型。",
    "gantry/util.py": "通用工具函数。",
}

GROUPS = [
    ("根目录", lambda p: "/" not in p),
    ("gantry/", lambda p: p.startswith("gantry/")),
    ("plugins/", lambda p: p.startswith("plugins/")),
    ("webui/", lambda p: p.startswith("webui/")),
    ("tests/", lambda p: p.startswith("tests/")),
    ("scripts/", lambda p: p.startswith("scripts/")),
    ("docs/", lambda p: p.startswith("docs/")),
    ("skills/", lambda p: p.startswith("skills/")),
    (".github/", lambda p: p.startswith(".github/")),
]

JS_CURATED = {
    "boot.js": "一次性迁移：旧 CBP 本地键迁移到 Gantry 键。",
    "i18n.js": "前端国际化：简/繁/英文案与简繁转换。",
    "animprefs.js": "动画参数：示波器与输入框呼吸。",
    "apistate.js": "API 状态与健康轮询（/health、模型/浏览器状态）。",
    "approvals.js": "破坏性工具审批 UI。",
    "atmenu.js": "@ 文件引用菜单。",
    "cards.js": "消息卡片/变更卡片渲染。",
    "codeux.js": "代码块展示、复制与折叠。",
    "composer.js": "输入框、发送、附件与快捷操作。",
    "control.js": "运行控制：停止/继续/插话。",
    "dialogs.js": "通用对话框。",
    "find.js": "页内查找。",
    "followload.js": "跟随滚动与增量加载。",
    "globals.js": "跨模块共享的全局状态/函数。",
    "kb.js": "知识库管理：导入/列表/删除/检索。",
    "main.js": "ES 模块入口与老代码兼容绑定。",
    "manual.js": "使用说明/帮助面板。",
    "models.js": "本地模型管理：列出/删除/下载 GGUF。",
    "msgrender.js": "聊天消息渲染。",
    "panels.js": "侧栏/面板与插件面板。",
    "scope.js": "任务进行中的示波器浮层。",
    "sessions.js": "会话列表与切换。",
    "settings.js": "设置页读写。",
    "slash.js": "斜杠命令。",
    "state.js": "ES 模块共享可变状态。",
    "steps.js": "步骤/时间线渲染。",
    "subagents.js": "子代理面板。",
    "teamcard.js": "团队任务卡片。",
    "themeboot.js": "主题初始化（避免首屏闪烁）。",
    "toast.js": "轻提示/通知。",
    "toollog.js": "工具调用日志。",
    "version.js": "页签标题与版本号。",
    "workspace.js": "工作区文件浏览。",
}

GROUP_DESC = {    "根目录": "启动入口、项目元数据、依赖清单、顶层说明。",
    "gantry/": "核心 Python 运行时：API 路由、工具、Agent、浏览器自动化、模型、存储。",
    "plugins/": "插件包；每个插件通常含 `plugin.json`（元数据/权限/面板）与 `plugin.py`（setup）。",
    "webui/": "Web 控制台；`index.html` + `css/` + `js/`（ES 模块）。",
    "tests/": "单元/集成/回归测试；运行：`python -m unittest discover -s tests`。",
    "scripts/": "运维、生成器、诊断脚本；用 `.venv\\Scripts\\python.exe` 运行。",
    "docs/": "架构、使用、开发与排障文档。",
    "skills/": "Agent 技能定义、模板与示例。",
    ".github/": "GitHub Actions / 模板配置。",
}


def files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return [x.replace("\\", "/") for x in out.splitlines() if x.strip()]


def py_info(path: Path) -> tuple[str, str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return "", ""
    doc = ast.get_docstring(tree) or ""
    first = next((l.strip() for l in doc.splitlines() if l.strip()), "")
    names = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
    return first, ", ".join(names[:6])


def js_info(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    comments: list[str] = []
    in_block = False
    for line in text.splitlines()[:80]:
        s = line.strip()
        if s.startswith("//"):
            comments.append(s[2:].strip())
        elif s.startswith("/*"):
            in_block = True
            comments.append(s[2:].strip())
        elif in_block:
            comments.append(s.rstrip("*/").strip())
            if "*/" in s:
                in_block = False
        elif comments:
            break
        if len(" ".join(comments)) > 240:
            break
    if not comments:
        return ""
    text = comments[0]
    if (text.endswith("—") or text.endswith("-")) and len(comments) > 1:
        text = text + " " + comments[1]
    for sep in ("。", ". "):
        if sep in text:
            text = text.split(sep)[0] + sep
            break
    return text[:160]


def md_info(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[:30]:
            s = line.strip()
            if s.startswith("#"):
                return s.lstrip("#").strip()
    except Exception:
        pass
    return ""


def describe(p: str) -> tuple[str, str]:
    if p in CURATED:
        return CURATED[p], ""
    path = ROOT / p
    suffix = path.suffix.lower()
    if suffix == ".py":
        doc, syms = py_info(path)
        if not doc:
            if p.startswith("tests/"):
                doc = f"测试：{path.stem}。"
            elif p.startswith("scripts/"):
                doc = f"脚本：{path.stem}。"
            else:
                doc = "（无模块注释）"
        return doc, syms
    if suffix == ".js":
        if path.name in JS_CURATED:
            return JS_CURATED[path.name], ""
        return (js_info(path) or "前端脚本。"), ""
    if suffix == ".md":
        return (md_info(path) or "文档。"), ""
    if suffix == ".json":
        if p.endswith("/plugin.json"):
            return "插件元数据：权限、配置、面板、贡献点。", ""
        return "数据/配置/示例 JSON。", ""
    if suffix in (".png", ".ico", ".jpg", ".jpeg", ".svg", ".webp"):
        return "静态图片资源。", ""
    if suffix in (".yml", ".yaml"):
        return "YAML 配置/工作流。", ""
    if suffix == ".css":
        return "样式表。", ""
    if suffix == ".html":
        return "页面模板。", ""
    return "", ""


def main() -> None:
    all_files = files()
    lines = [
        "# 项目文件功能地图",
        "",
        "> 按目录列出每个文件的功能，便于维护时快速定位。",
        "> 自动提取模块 docstring/首行注释；核心文件用人工说明覆盖。",
        "> 重新生成：`python scripts/gen_file_map.py`。",
        "",
        "## 架构速览",
        "",
        "```",
        "start.py / app.py               启动与 FastAPI 装配",
        "  └─ gantry/bridge.py           全局运行时（配置/浏览器/模型/工具）",
        "       ├─ gantry/tools.py        工具注册表 + tool_plugins/",
        "       ├─ gantry/agent.py        Agent 循环",
        "       ├─ gantry/plugins.py      插件系统",
        "       └─ gantry/routes_*.py     HTTP API",
        "webui/                          Web 控制台（HTML/CSS/ES 模块）",
        "plugins/                        插件（teams-bot 等）",
        "tests/                          单元/集成/回归测试",
        "scripts/                        运维、生成器、诊断工具",
        "docs/                           架构/使用/开发文档",
        "skills/                         Agent 技能说明与模板",
        "```",
        "",
        "## 维护入口",
        "",
        "- **新增文件工具**：在 `gantry/tool_plugins/` 加 `<name>.py`，暴露 `register(registry)`；共享 helper 放 `_files_utils.py`。",
        "- **新增 HTTP 路由**：在 `gantry/routes_*.py` 定义，确保 `gantry/api.py` 导入；复杂逻辑放 `gantry/<domain>.py`。",
        "- **新增插件**：`plugins/<name>/plugin.json` + `plugin.py`，在 `setup(ctx)` 里注册工具/API/面板。",
        "- **新增测试**：`tests/test_<area>.py`，用 `unittest`；跑全套 `python -m unittest discover -s tests`。",
        "- **版本发布**：`python scripts/bump_version.py preview`（会改 version.py、pyproject、CHANGELOG、README）。",
        "- **Teams 读/定位/发送**：读 `gantry/teams_idb.py`；定位 `gantry/teams_dom.py`（`open_chat_by_thread_id` / `open_chat_by_row`）；发送 `gantry/teams_send.py`；调度 `plugins/teams-bot/plugin.py`。",
        "",
    ]
    for title, pred in GROUPS:
        group = [p for p in all_files if pred(p)]
        if not group:
            continue
        lines += [f"## {title}", ""]
        if title in GROUP_DESC:
            lines += [GROUP_DESC[title], ""]
        lines += ["| 文件 | 功能 | 关键入口 |", "|---|---|---|"]
        for p in sorted(group):
            desc, syms = describe(p)
            name = p[len(title):] if title.endswith("/") and p.startswith(title) else p
            desc = desc.replace("|", "\\|")
            syms = syms.replace("|", "\\|")
            lines.append(f"| `{name}` | {desc} | {syms} |")
        lines.append("")
    out = ROOT / "docs" / "FILE_MAP.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", out, "files", len(all_files))


if __name__ == "__main__":
    main()
