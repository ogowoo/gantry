# Gantry 结构与流程 Mindmap

> 用文字 + Mermaid 描述 `gantry` 的整体架构、数据流与扩展点。
> 在支持 Mermaid 的 Markdown 阅读器（如 GitHub、VS Code、Typora）里可直接渲染。

```mermaid
mindmap
  root((Gantry<br/>AI 自动化平台))
    入口层
      Web UI
        实时流 / 步骤时间线
        知识库 / 插件面板 / 设置
        工作区浏览器 / 会话管理
      OpenAI 兼容 API
        /v1/chat/completions
        /v1/models
        /v1/plugins/...
      CLI / 托盘
        start.py / install.py
        scripts/login.py
    核心服务
      FastAPI + Uvicorn
      配置中心 config.json
        providers / kb / agent
      桥接层 gantry/bridge.py
        全局 CFG / 会话 / 工具注册
      生命周期
        lifespan → 插件加载 → 浏览器懒启动
    Agent 运行层
      任务 Task
        输入：提示词 + 附件 + 工具
        状态机：waiting / running / suspended / completed
      会话 Session
        多轮对话 / 上下文压缩
        记忆注入 / 长期记忆
      工具调度 Agent Tools
        工具选择 → 执行 → 结果回传
        审批门 / 沙箱权限
      子代理 Subagents
        并发槽 / 结果聚合
    模型层
      Copilot 网页通道
        Playwright + EdgeProfile
        页面捕获 / 菜单发现
        对话 / 文件读取 / 代码执行
      本地模型
        llama.cpp 进程内 GGUF
        local/qwen3-4b / local embed
      HTTP 提供商
        OpenAI / DeepSeek / Kimi / ...
        api_key_env 配置
    浏览器自动化
      gantry/page.py
        页面操作 / DOM 提取
      gantry/api.py
        ensure_browser / raw
      EdgeProfile 登录态
        cookies / tokens / Service Worker
    知识库
      文档 gantry/kb_ingest.py
        docx / pdf / xlsx / pptx / md / html / mhtml / url
        OCR rapidocr / xiaomi
      索引 gantry/kb_semantic.py
        FTS5 trigram
        向量：fastembed / local / http / hash
      检索 kb_search
        混合打分：语义 + 词面覆盖
    插件系统
      加载器 tool_plugin_loader.py
      插件类型
        工具 Tools
        HTTP API
        面板 Panels
        UI 面板 / 菜单注入
      示例插件
        teams-bot
        web-search
        kb
    数据与状态
      data/kb / data/kb.db
      data/sessions / checkpoints
      data/plugins.json
      data/logs/gantry.log
      EdgeProfile/
    典型流程
      用户输入
        ↓
      模型选择模型
        ↓
      模型生成工具调用 / 直接回答
        ↓
      工具执行
        本地文件 / 浏览器 / 知识库 / 网络
        ↓
      结果回传模型
        ↓
      输出给用户
        代码 / 文件 / 总结 / 下一步
```

## 一句话流程

```text
用户通过 WebUI/API 提交任务 → Agent 选模型 → 模型决定用工具/直接回答
→ 工具在沙箱/浏览器/知识库/网络执行 → 结果回传 → 循环直到完成
```

## 主要扩展点

1. **加模型**：在 `config.json` 的 `providers` 里新增 OpenAI 兼容端点，或放 GGUF 到 `data/models` 用 `local` 后端。
2. **加工具**：在 `plugins/<name>/plugin.py` 里 `ctx.register_tool(...)`。
3. **加面板**：`ctx.register_panel(...)`，数据由后端 API 提供。
4. **改 UI**：`webui/js/*.js` + `webui/css/*.css`，事件统一走 `data-act`。
