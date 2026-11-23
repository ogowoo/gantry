# -*- coding: utf-8 -*-
"""SOUL / PERSONA —— Agent 的身份层与表达层。

两层分开，是因为它们的**稳定性和作用域**不同：

- **Soul（灵魂）**：这个部署的"我是谁"。名字、价值观、判断原则、边界。
  全局唯一，跟着服务安装目录走（`<安装目录>/SOUL.md`）；缺省用内置的
  Hermes 风格 Soul（直接、务实、有判断力、不谄媚）。工作区不能覆盖它——
  否则换个目录 Agent 就换个人格，长期记忆与信任全部断裂。
- **Persona（人格/面具）**：可切换的**表达风格**——语气、节奏、详略、
  称呼。内置库在 `<安装目录>/personas/*.md`，工作区可以放同名文件覆盖，
  也可以用 `config.agent.persona` 或请求头 `X-Gantry-Persona` 按会话切换。
  Persona 只改"怎么说"，不改"信什么"——价值观冲突由 Soul 说了算。

文件格式：纯 Markdown。需要三语时用 `## zh` / `## en` 二级标题分节
（`tw` 由 zh 按字转换）；没有分节时整份注入，语言无关。

注入位置（`gantry/agent_prompt.py`）：
  language(-2000) → soul(-1500) → protocol(-1000) → … → profile(260) → persona(280)

字数上限：Soul 6000、Persona 4000 字符，超出截断并在文中留可见提示
（身份层不该靠字数取胜，太长反而每回合烧 token、稀释协议注意力）。
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path

from . import prompt_lang

SOUL_FILE = "SOUL.md"
PERSONAS_DIR = "personas"
DEFAULT_PERSONA = "default"
MAX_SOUL_CHARS = 6000
MAX_PERSONA_CHARS = 4000

# 请求级 persona 覆盖（API 中间件按 X-Gantry-Persona 头设置；排队消息入队时捕获）
_REQ_PERSONA: ContextVar[str] = ContextVar("cbp_req_persona", default="")

# 内置 Soul：没有 SOUL.md 时的缺省身份。刻意写短——它是"底线"，不是角色扮演。
DEFAULT_SOUL = {
    "zh": """【身份】你是这个部署的本地任务 Agent，由 Hermes 身份层定义：不是什么助手角色扮演，
而是一个有稳定判断力的执行者。

【原则】
- 直接：先给结论和动作，不写"好问题""我很乐意"之类的客套；不确定就说不确定，并给出验证方法。
- 务实：能跑通、能验证的方案优先于听起来漂亮的方案；每一条完成声明背后都要有工具输出。
- 诚实：不编造文件、命令、API 和结果；失败、超限、被拒绝，都如实报告并说明下一步。
- 有判断力：用户的方案有问题时明说，给出理由和替代方案；同意是因为它对，不是因为用户说了。
- 克制：不做没被要求的重构和扩展；不重复解释已经做过的事；风险操作先确认。

【边界】不越过工具能力承诺结果；不把密钥、凭据写进日志或回复；不做不可回滚的操作前必先确认。""",
    "en": """[Identity] You are the local task agent of this deployment, defined by the Hermes identity layer: not
a role-played assistant, but an executor with stable judgment.

[Principles]
- Direct: lead with the conclusion and the action; no "great question" filler. When unsure, say so and
  state how you would verify.
- Pragmatic: a solution that runs and can be verified beats one that merely sounds good; every claim of
  completion must be backed by real tool output.
- Honest: never invent files, commands, APIs or results; report failures, limits and refusals plainly
  with the next step.
- Judgmental: disagree when the user's approach is wrong, with reasons and an alternative; agree because
  it is right, not because it was asked.
- Restrained: no unrequested refactors or scope creep, no re-explaining what you already did, confirm
  before risky operations.

[Boundaries] Never promise beyond what tools can do; never put secrets or credentials in logs or replies;
always confirm before irreversible operations.""",
}

# 内置 Persona：工作区/安装目录里有同名文件时以文件为准（完全可覆盖）。
DEFAULT_PERSONAS = {
    "default": {
        "zh": """【表达风格】默认风格：结论先行，短句，能省的字就省；需要细节时再展开。
中文标点，数字与单位之间不加空格；不堆形容词，不写总结性套话。""",
        "en": """[Voice] Default style: conclusion first, short sentences, drop words that add nothing;
expand only when detail is needed. Do not stack adjectives or write summary filler.""",
    },
    "engineer": {
        "zh": """【表达风格 · 工程师】像结对编程的资深工程师：给代码、给 diff、给验证命令。
报错时先给根因假设再给修复；不用比喻，不客套；对不确定的 API 先说"我没验证过"。""",
        "en": """[Voice · Engineer] Like a senior pair-programming engineer: code, diffs, verification
commands. For errors, state the root-cause hypothesis before the fix; no metaphors, no pleasantries;
say "I have not verified this" for uncertain APIs.""",
    },
    "reviewer": {
        "zh": """【表达风格 · 审查者】默认怀疑，按严重度排序（阻塞/应当修/建议），每条给出证据
（path:line）和修复建议；先列最坏的问题；对"看起来没问题"也要说明检查了什么。""",
        "en": """[Voice · Reviewer] Default to skepticism; order findings by severity (blocking / should
fix / suggestion), each with evidence (path:line) and a fix; worst issue first; state what you checked
even when nothing is wrong.""",
    },
    "teacher": {
        "zh": """【表达风格 · 讲解者】先给结论，再拆步骤讲清为什么；用具体例子代替抽象定义；
每次只引入一个新概念；结尾给一个可以马上动手的小练习或验证命令。""",
        "en": """[Voice · Teacher] Conclusion first, then step-by-step reasoning; concrete examples over
abstract definitions; one new concept at a time; end with a small exercise or verification command.""",
    },
    "terse": {
        "zh": """【表达风格 · 极简】能一行不写两行；只给结论、路径、命令；不问与任务无关的问题；
被要求解释时才展开。""",
        "en": """[Voice · Terse] One line over two; conclusions, paths and commands only; no unrelated
questions; expand only when asked to explain.""",
    },
}


def normalize_name(name: str | None) -> str:
    """persona 名归一：去空白、小写、只允许字母数字下划线连字符。"""
    v = (name or "").strip().lower()
    if not v:
        return ""
    v = re.sub(r"[^a-z0-9_-]+", "-", v).strip("-")
    return v[:40]


def set_request_persona(name: str | None) -> str:
    """由 API 层在请求入口调用（排队消息入队时也调用，捕获当次选择）。"""
    v = normalize_name(name)
    _REQ_PERSONA.set(v)
    return v


def request_persona() -> str:
    return _REQ_PERSONA.get()


def effective_persona(config_value: str | None = None) -> str:
    """优先级：请求头 > 配置 > default。"""
    req = request_persona()
    if req:
        return req
    return normalize_name(config_value) or DEFAULT_PERSONA


def _pick_lang_section(text: str, lang: str) -> str:
    """按 `## zh` / `## en` 分节取当前语言；没有分节时整份返回。"""
    if not text:
        return ""
    m = re.findall(r"^##\s+(zh|en|tw)\s*$", text, re.M)
    if not m:
        return text.strip()
    want = "en" if lang == "en" else "zh"
    blocks: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        head = re.match(r"^##\s+(zh|en|tw)\s*$", line.strip())
        if head:
            cur = head.group(1)
            blocks.setdefault(cur, [])
            continue
        if cur:
            blocks[cur].append(line)
    body = "\n".join(blocks.get(want) or []).strip()
    if lang == "tw" and body:
        body = prompt_lang.to_tw(body)
    return body


def _clip(text: str, cap: int, label: str) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n[{label} 共 {len(text)} 字符，已截断到 {cap}]"


def soul_text(global_root: str | Path | None = None,
              lang: str | None = None) -> tuple[str, str]:
    """返回 (标题, 正文)。文件不存在时用内置默认 Soul。

    返回标题用于注入块前缀；正文已按语言取节并转义块闭合标签
    （转义由调用方做，这里只管内容）。
    """
    lg = prompt_lang.normalize(lang or prompt_lang.request_lang())
    text = ""
    if global_root:
        for name in (SOUL_FILE, "soul.md", "SOUL.txt"):
            p = Path(global_root) / name
            try:
                if p.is_file():
                    text = p.read_text(encoding="utf-8", errors="replace")
                    break
            except Exception:
                continue
    if text.strip():
        body = _pick_lang_section(text, lg)
        if body:
            title = "【灵魂 / Soul】" if lg != "en" else "[Soul]"
            return title, _clip(body, MAX_SOUL_CHARS, "SOUL.md")
    return "", DEFAULT_SOUL["en" if lg == "en" else "zh"]


def persona_dirs(global_root: str | Path | None,
                 workspace: str | Path | None) -> list[Path]:
    """persona 搜索目录：安装目录在前，工作区在后（后出现的同名覆盖）。"""
    out: list[Path] = []
    for root in (global_root, workspace):
        if root:
            out.append(Path(root) / PERSONAS_DIR)
    return out


def list_personas(global_root: str | Path | None = None,
                  workspace: str | Path | None = None) -> list[str]:
    """可用 persona 名（内置 + 文件，workspace 覆盖同名）。"""
    names = dict.fromkeys(DEFAULT_PERSONAS)
    for d in persona_dirs(global_root, workspace):
        try:
            if not d.is_dir():
                continue
            for p in d.glob("*.md"):
                n = normalize_name(p.stem)
                if n:
                    names[n] = None
        except Exception:
            continue
    return sorted(names)


def persona_text(name: str | None = None, global_root: str | Path | None = None,
                 workspace: str | Path | None = None,
                 lang: str | None = None) -> tuple[str, str]:
    """返回 (生效的 persona 名, 正文)。找不到名字时回退 default。"""
    lg = prompt_lang.normalize(lang or prompt_lang.request_lang())
    want = normalize_name(name) or DEFAULT_PERSONA
    text = ""
    # 工作区优先：倒序查目录，命中即用
    for d in reversed(persona_dirs(global_root, workspace)):
        p = d / f"{want}.md"
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace")
                break
        except Exception:
            continue
    if text.strip():
        body = _pick_lang_section(text, lg)
        if body:
            return want, _clip(body, MAX_PERSONA_CHARS, f"persona {want}")
    builtin = DEFAULT_PERSONAS.get(want) or DEFAULT_PERSONAS[DEFAULT_PERSONA]
    used = want if want in DEFAULT_PERSONAS else DEFAULT_PERSONA
    return used, builtin["en" if lg == "en" else "zh"]
