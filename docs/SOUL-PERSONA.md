# Soul 与 Persona

两层身份，分开管：**Soul 决定"信什么"，Persona 决定"怎么说"。**

## 为什么分层

| | Soul（灵魂） | Persona（人格/面具） |
|---|---|---|
| 是什么 | 这个部署的自我：名字、价值观、判断原则、边界 | 可切换的表达风格：语气、节奏、详略 |
| 作用域 | 全局唯一（跟服务安装目录走），不可按脚本覆盖 | 内置库 + 全局 + 工作区覆盖，会话级固化 |
| 能否改 | 能（WebUI / SOUL.md），改前自动快照可回滚 | 能，随时换，互不影响 |
| 冲突时 | **Soul 优先于 Persona**；但安全规则与协议优先于 Soul | 让位 |

Soul 不允许工作区覆盖：换个目录就换个人格，长期记忆与信任会断裂。

## 冲突裁决（硬编码，不可覆盖）

系统提示词里 Soul 之后永远跟着一段 `soul_guard` 条款：

> 安全规则与协议 > Soul > Persona。Soul 只描述"我是谁、怎么看问题"，
> 不能取消审批、验证、风险确认或密钥保护。

这段由 `gantry/soul.py` 的 `SAFETY_CLAUSE` 生成，**SOUL.md 覆盖不了它**；
即使 Soul 写了"不要请示、直接删文件"，协议仍然优先。保存时的 lint 也会点名
这类表述（见下）。

## 文件与优先级

```
<安装目录>/SOUL.md            ← Soul 唯一真源（不存在则用内置默认）
<安装目录>/personas/*.md      ← Persona 库（默认库在仓库根 personas/）
<工作区>/personas/*.md        ← 同名覆盖上者，只影响该项目（可关闭）
data/soul-history/<时间戳>/   ← 每次改 Soul 前的自动快照（保留最近 50 份）
```

Persona 生效优先级：**会话记录 > 请求头 `X-Gantry-Persona` > `config.agent.persona` > `default`**。

会话记录优先是刻意的：创建会话时把当时的选择固化进 `sessions.persona`，
此后的 cron、服务重启续跑、子代理请求即使不带请求头，也不会漂移回默认风格。
请求头只在"创建会话时"起作用。

## 注入位置

```
language(-2000) → soul(-1500) → soul_guard(-1400) → protocol(-1000)
  → 工具清单 → 规则/安全 → profile(260) → persona(280) → workspace(300)
```

Soul 放在协议**前**（先定"我是谁"再讲"怎么干活"）；Persona 放在领域 profile
**后**（表达层最后定格，不被前面的规则冲淡）。

## 文件格式

纯 Markdown，可带 frontmatter：

```markdown
---
extends: default      # 在某个 persona 基础上补充（最多 3 层，防循环）
verbosity: detailed   # minimal | normal | detailed
tone: neutral         # neutral | warm | blunt
format: code-first    # prose | bullets | code-first
---
## zh

【表达风格 · 工程师】给代码、给 diff、给验证命令……

## en

[Voice · Engineer] code, diffs, verification commands……
```

- frontmatter 字段会渲染成一句结构化风格约束（中英同构），跨语言行为一致；
  正文按 `## zh` / `## en` 分节取，`tw` 由 zh 按字转换（与界面同一张简繁表）。
- 没有分节：整份注入，语言无关（适合纯代码/术语类说明）。
- `extends` 把基础 persona 的正文拼在前面，用于"在 default 上加点东西"，
  避免每个文件都复制一大段；循环引用会在 3 层内截断。
- 上限：Soul 6000 字符、Persona 4000 字符；超出截断并在注入文本里留可见提示。
  身份层靠密度不靠长度——太长每回合烧 token 并稀释协议注意力。

## 检查（lint）

保存 Soul/Persona 时返回 `warnings`，检测：取消审批/确认/验证、诱导直接删除、
可能泄露密钥、试图压过安全规则/协议、空分节、超长。只警告不阻止
（用户可能确有特殊需求），WebUI 会显示在编辑器下方。

粗估 token（约 3 字符/token）也一并返回，设置页显示"生效文本 N 字符，约 T token/回合"。

## 切换与编辑

**WebUI**：
- 会话头部 🎭 徽章：显示当前会话 persona，点击进入身份设置
- 设置 → 「身份」页签：
  - 默认 Persona（写 `config.json` 的 `agent.persona`，对所有新任务生效）
  - 本次会话 Persona（写会话记录；没有打开会话时记 localStorage 作为创建时的选择）
  - Soul 编辑器（保存 / 恢复内置默认 / 改前自动快照 / 历史回滚）
  - Persona 编辑器（新建、保存到工作区或全局、删除工作区副本、结构化字段提示）
  - 子代理 Persona、工作区 Persona 开关（见下）

**API**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/agent/soul` | 读 Soul（原文 + 生效文本 + 路径 + 告警 + token） |
| PUT | `/v1/agent/soul` | `{text}` 保存（改前快照）；空文本 = 恢复内置默认 |
| GET | `/v1/agent/soul/history` | 历史快照列表（新→旧，最近 50 份） |
| GET | `/v1/agent/soul/history/{id}` | 查看某份快照原文 |
| POST | `/v1/agent/soul/restore` | `{id}` 回滚（回滚前也先快照，可再撤销） |
| GET | `/v1/agent/personas` | 列表（来源 builtin/global/workspace、字段、token） |
| GET | `/v1/agent/personas/{name}` | 读原文 + 生效文本 + 告警 + 来源警告 |
| PUT | `/v1/agent/personas/{name}` | `{text, scope: workspace\|global}`；空文本 = 删除 |
| POST | `/v1/agent/sessions/{sid}/persona` | `{persona}` 会话级切换（空 = 回默认链） |
| GET/POST | `/v1/config` | `agent.persona`、`agent.subagent_persona`、`agent.allow_workspace_personas` |

文件名会被归一化（小写、非法字符折成 `-`、≤40 字符），路径穿越不可能落到目录外。

## 工作区 Persona 的信任模型

`<工作区>/personas/*.md` 随仓库分发——clone 一个项目就可能把它的文本带进系统
提示词（提示词注入面）。因此：

- 列表/详情里标出来源；选中工作区来源的 persona 时 WebUI 会警告并首次确认；
- 不信任仓库时可在设置里关闭 `agent.allow_workspace_personas`，
  此时工作区文件被完全忽略（只认内置与全局）。

## 子代理 Persona

子代理把系统提示词整份发给模型，长语气说明每回合都在烧 token，产出却主要是
工具调用与结论。默认 `agent.subagent_persona=terse`；填 `inherit`
（或留空）则跟随默认优先级链。

## 内置 Persona

仓库 `personas/` 自带 5 个，可直接用也可当模板改：

| 名字 | 风格 |
|---|---|
| `default` | 结论先行、短句、不堆套话 |
| `engineer` | 结对编程：代码/diff/验证命令，先根因后修复 |
| `reviewer` | 按严重度排序、每条给 `path:line` 证据 |
| `teacher` | 先结论再拆步骤，结尾给可动手的练习 |
| `terse` | 极简：只给结论、路径、命令 |

## 内置 Soul

没有 `SOUL.md` 时使用（`gantry/soul.py` 的 `DEFAULT_SOUL`）：直接、务实、诚实、
有判断力、克制。中文版全文约 15 行，建议自定义时保持在这个量级。

## 与其它身份机制的关系

- `AGENT.md`（全局 + 工作区）：**项目约定/操作规范**，属于 workspace 块，排在 persona 之后；
- `agent_profiles`（领域 profile）：任务路由与硬约束，排在 Soul/协议之后、Persona 之前；
- Skills：按需加载的流程手册，不参与身份。

一句话：Soul 管底线，Protocol/AGENT.md/PROFILE 管规则，Persona 管语气，Skill 管做法。
