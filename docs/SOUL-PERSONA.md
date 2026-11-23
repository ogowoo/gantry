# Soul 与 Persona

两层身份，分开管：**Soul 决定"信什么"，Persona 决定"怎么说"。**

## 为什么分层

| | Soul（灵魂） | Persona（人格/面具） |
|---|---|---|
| 是什么 | 这个部署的自我：名字、价值观、判断原则、边界 | 可切换的表达风格：语气、节奏、详略、称呼 |
| 作用域 | 全局唯一（跟服务安装目录走） | 内置库 + 全局 + 工作区覆盖，可按会话切换 |
| 能否改 | 能（WebUI / SOUL.md），但所有任务共享 | 能，随时换，互不影响 |
| 冲突时 | **Soul 优先** | 让位 |

Soul 不允许工作区覆盖：换个目录就换个人格，长期记忆与信任会断裂。

## 文件与优先级

```
<安装目录>/SOUL.md            ← Soul 唯一真源（不存在则用内置默认）
<安装目录>/personas/*.md      ← Persona 库（默认库在仓库根 personas/）
<工作区>/personas/*.md        ← 同名覆盖上者，只影响该项目
```

Persona 生效优先级：**请求头 `X-Gantry-Persona` > `config.agent.persona` > `default`**。

工作区目录查在前（后命中者胜），所以项目里放 `personas/engineer.md` 就能在本项目
覆盖全局的同名风格。

## 注入位置

系统提示词按 `PromptRegistry` 的 order 装配：

```
language(-2000) → soul(-1500) → protocol(-1000) → 工具清单 → 规则/安全
  → profile(260) → persona(280) → workspace(300)
```

Soul 放在协议**前**（先定"我是谁"再讲"怎么干活"）；Persona 放在领域 profile
**后**（表达层最后定格，不被前面的规则冲淡）。

## 文件格式

纯 Markdown。要三语时用二级标题分节：

```markdown
# Engineer —— 资深工程师

## zh

【表达风格 · 工程师】给代码、给 diff、给验证命令……

## en

[Voice · Engineer] code, diffs, verification commands……
```

- `zh` / `en` 分节按提示词语言（`agent.prompt_language` / `X-Gantry-Lang`）取；
  `tw` 由 `zh` 按字转换（与界面同一张简繁表）。
- 没有分节：整份注入，语言无关（适合纯代码/术语类风格说明）。
- 上限：Soul 6000 字符、Persona 4000 字符；超出截断并在注入文本里留可见提示。
  身份层靠的是密度不是长度——太长会每回合烧 token 并稀释协议注意力。

## 切换与编辑

**WebUI**：设置 → 「身份」页签
- 默认 Persona（写 `config.json` 的 `agent.persona`，对所有任务生效）
- 本次会话 Persona（只存本浏览器 localStorage，按请求头发给后端）
- Soul 编辑器（保存 / 恢复内置默认）
- Persona 编辑器（新建名称、保存到工作区/全局、删除工作区副本）

**API**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/agent/soul` | 读 Soul（原文 + 生效文本 + 路径） |
| PUT | `/v1/agent/soul` | `{text}` 保存；空文本 = 删除文件、恢复内置 |
| GET | `/v1/agent/personas` | 列表（含来源 builtin/global/workspace 与当前选中） |
| GET | `/v1/agent/personas/{name}` | 读某个 persona 原文 |
| PUT | `/v1/agent/personas/{name}` | `{text, scope: workspace\|global}`；空文本 = 删除 |
| GET/POST | `/v1/config` | `agent.persona` 默认值（白名单键，persist=true 落盘） |

文件名会被归一化（小写、非法字符折成 `-`、≤40 字符），路径穿越不可能落到目录外。

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
