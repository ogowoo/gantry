# 模型提供商（Providers）

"提供商"决定请求发给谁：Copilot 网页自动化、某个 OpenAI 兼容网关、Anthropic Messages 服务，
或进程内跑的 GGUF。设置页「本地模型 → 模型提供商」把这一层做成可配置的，不用再手改 `config.json`。

## 两类来源

| 来源 | 位置 | 能否删除 | 说明 |
|---|---|---|---|
| 预设 | `providers.PRESETS`（代码） | 不能 | openai / deepseek / kimi / kimi-coding / grok，自带 base_url、模型与 key 环境变量 |
| 自定义 | `config.json` 的 `providers.<name>` | 能 | 预设名写在这里 = **覆盖层**，删掉即恢复预设默认 |

合并语义在 `providers.build_providers()`：`{预设} ← {config.json}`，后者按键覆盖。
所以改预设不用复制整份配置，只写你改的那几个键。

## 设置页能做什么

- 列出全部提供商：类型徽章（预设/自定义/本地进程内）、接口地址、模型数、Key 状态
  （`🔑 环境变量名` / `🔑 配置文件` / `未配置 Key`）；
- **新增**：可先选一个预设把 base_url / 协议 / 环境变量 / 模型填进表单，再改；
- **编辑**：部分更新，只写你改动过的键；
- **测试连接**：`GET {base_url}/models`（Anthropic 走 `/v1/models`），返回状态码、耗时、
  模型数；本地进程内 provider 只检查 GGUF 文件是否存在。不发送任何用户内容、不消耗生成额度；
- **删除 / 恢复默认**：自定义提供商删除；预设删除的是覆盖层；
- **默认模型**：顶部下拉写 `default_model`（`provider/model`），**立即生效**。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/providers` | 列表（脱敏）+ `default_model` + 预设名清单 |
| POST | `/v1/providers` | 新增/修改：`{name, base_url, api, api_key_env, api_key, models, max_tokens, timeout_seconds, json_mode, vision, description, proxy, use_system_proxy}`，全部可选（`name` 必填） |
| DELETE | `/v1/providers/{name}` | 删除自定义；对预设是恢复默认 |
| POST | `/v1/providers/{name}/test` | 连通性检查 |
| POST | `/v1/providers/default-model` | `{model: "provider/model"}` |

保存后调用 `_reload_providers()` 重建内存里的 `b.PROVIDERS`，`/v1/models` 与模型选择器
按 `b.PROVIDERS` 动态生成，所以**不需要重启**。

## Key 的处理

- 只写不读：`GET` 永远不回 `api_key` 明文，只回 `api_key_set` / `api_key_in_config` / `api_key_env`；
- 优先级：`api_key_env`（可多个，按顺序回退）→ `api_key`（config.json 明文）；
- 编辑时不传 `api_key` = 保持原值；显式传空串 = 清除；界面另有「清除已保存的 Key」按钮；
- 推荐放环境变量（`sk-` 明文在 `config.json` 里会被启动日志提示）。

## 校验（后端硬约束）

名称 `^[a-z0-9][a-z0-9._-]{0,40}$` 且不能是 `copilot`（网页自动化通道）；
`base_url` 必须 http(s)；`api` ∈ {openai, anthropic}；`json_mode` ∈ {auto, json_schema,
json_object, off}；`max_tokens` 0–2,000,000（0 = 不发这个字段）；`timeout_seconds` 5–7200；
模型数 ≤200、单个名字 ≤200 字符；未知字段直接 400（避免把任意东西塞进 providers）。

## 默认模型为什么"立即生效"

`bridge.DEFAULT_MODEL` 是模块级变量，多处直接读它。`POST /v1/providers/default-model`
与 `POST /v1/config` 的 `default_model` 分支都会同步改写它——否则界面上改了默认模型，
本次进程里跑的任务仍然用旧的（以前就是这个行为，这次一并修掉）。
