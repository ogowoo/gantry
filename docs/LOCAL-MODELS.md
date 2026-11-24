# 本地模型库（GGUF）

本地模型出问题几乎都是"**文件对了，位置/账目不对**"：下载回来的 GGUF 躺在别处，
provider 的 `model_path` 还指着旧文件；向量模型和对话模型混在一个目录里，自动发现挑错；
下载被中断的文件留着合法头却没有数据，`llama.cpp` 加载时才炸。模型库把这些收敛成四件事。

## 目录约定

```
data/models/
    chat/        对话模型（含视觉主模型，如 Qwen2.5-VL-7B-Q4_K_M.gguf）
    embedding/   向量模型（bge / e5 / gte / nomic-embed / jina …）
    rerank/      重排模型
    mmproj/      多模态投影（配合 chat/ 里的视觉模型）
```

库根目录和类别之外的文件不会被删除，也不会被强制移动：扫描会标出来，
由「整理模型库」按计划移动（`apply=false` 先给计划，确认后才真动）。

## 类别识别（为什么这么判）

`gantry/model_store.py` 优先读 GGUF 元数据，读不到才退回文件名：

| 依据 | 判为 | 说明 |
|---|---|---|
| `clip.has_vision_encoder` / `clip.projector_type` / architecture `clip` | mmproj | 投影文件（不是对话模型） |
| architecture ∈ bert / nomic-bert / jina-bert / gte … 或 `bert.pooler_type` | embedding | 向量模型 |
| 名字含 rerank / cross-encoder | rerank | 重排模型 |
| 其余（有 architecture） | chat | 对话模型 |
| 元数据无线索 | 文件名含 embed/bge/e5/gte → embedding；mmproj/projector → mmproj；否则 chat | `source=filename/default` |

接口返回 `kind` / `kind_source` / `kind_reason`，界面把理由显示在图标 tooltip 上——
"凭什么把它当向量模型"是可解释的。

## 完整性校验

只读文件头（默认上限 16MB，够覆盖绝大多数分词器词表），做两件事：

1. magic/版本/元数据必须成立（不是 GGUF 直接标记为坏文件）；
2. 按张量表计算"数据区 + 最大张量终点"，与文件实际大小比较——
   小于实际大小说明**下载被截断**，这种文件"下载成功但启动必失败"。

大文件只解析到头部预算为止；超出时标 `note`（未做完整性校验）而不是误报损坏。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/models/local` | 列表：类别、来源、是否可用、问题、规范位置、是否需要移动 |
| POST | `/v1/models/local/scan` | `{apply:false|true}` 整理模型库（默认 dry-run） |
| POST | `/v1/models/local/{provider}/use` | `{path}` 把某个 GGUF 设为该 provider 的当前模型 |
| POST | `/v1/models/local/{provider}/start` | 加载到内存：先体检，路径失效时按文件名自愈，非对话/截断文件直接报错 |
| POST | `/v1/models/local/{provider}/stop` | `{force?, timeout?}` 卸载释放内存；`force:true` 为强行停止 |
| POST | `/v1/models/local/stop` | 一键强行停止所有本地模型 |
| POST | `/v1/models/local/download` | `{url, filename?, kind?}` 下载 → 体检 → 按类别归位 |
| DELETE | `/v1/models/local/{path}` | 删除（支持 `chat/xxx.gguf`，路径限制在库内） |

## 启动 / 停止 / 强行停止

引擎有四个状态，界面徽章直接显示：`idle`（未加载）、`loading`（加载中）、`ready`（运行中）、
`generating`（生成中）。

- **启动** `start`：体检 + 自愈后 `warm()`，同步返回加载耗时；加载在 worker 线程里做，
  不阻塞事件循环。
- **停止** `stop`：先协作式取消（生成循环下一个 chunk 退出），再等锁卸载；
  5 秒拿不到锁就返回 `ok:false` 并提示"模型正忙，可强行停止"，不会把请求挂死。
- **强行停止** `stop {force:true}`：
  - 生成中：置取消位并等锁，拿到锁立即释放上下文（流式生成在下一个 chunk 退出）；
  - 加载中：置 `abort_load`——llama.cpp 的加载不可中断，但**结果绝不会留在内存**，
    加载返回时立刻丢弃并回报"加载被强行中止"，界面状态马上回到 idle；
  - 已加载：直接释放。
  - 极少数底层调用不响应取消（非流式 `create_chat_completion` 正在 C 里跑）：接口如实
    返回 `ok:false` 与原因，提示"本次返回后会释放内存"，不假装成功。
- **一键停止** `stop`（全体）：对每个 local provider 做同样的强行停止，返回逐项结果。

管理页：provider 行在加载/生成中显示「停止」，点一次普通停止，忙时再确认一次改为强行停止；
底部「全部强制停止」用于内存告急或加载卡死时一键释放。

## 自愈与"下载完就能用"

以前只有一种"用"的方式：手改 `config.json` 的 `providers.<name>.model_path`——
于是新产品下载到 `data/models` 后，界面点「启动」加载的仍是旧路径，看起来像"下载了却启动不了"。

现在：

- 卡片上的「设为当前」直接写 `model_path`（原子写 config.json）并重建 providers；
- 「整理模型库」移动文件后，旧路径失效 → `start` 会按**文件名**在库里查找同名文件，
  找到就改正配置再加载，并在返回里带 `repaired:{from,to}`；
- 找不到则返回 404 与可行动提示（列出查找过的目录），而不是让 llama.cpp 抛底层异常。

## 多模态（mmproj）

视觉 GGUF 需要配套投影文件才能看图。库会把 `mmproj/*.gguf` 按同名前缀与
`chat/` 里的主模型配对，写进 `providers.<name>.mmproj_path`；
`LocalChatEngine` 通过 `chat_handler`（Qwen2VL / MiniCPM / Llava15）接上，
缺 handler 或投影文件缺失时**在加载阶段**就报明确错误。

## 自动发现

没有任何 `backend:"local"` / `model_path` 配置时，启动会递归扫描模型库，
**只挑 chat 类**（`chat/` 优先，其次库根）注册 `local` provider，
并顺带配对 mmproj。以前按文件名排序取第一个，挑到 embedding/mmproj 就会"一启动就失败"。

## 界面

本地模型弹窗（🧠）每个卡片显示：类别徽章、体检徽章（文件异常）、规范位置提示、
「设为当前 / 复制路径 / 删除」；顶部横幅提示"N 个模型不在规范目录"，一键整理（带移动预览）。
