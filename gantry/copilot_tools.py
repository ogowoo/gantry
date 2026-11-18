# -*- coding: utf-8 -*-
"""Copilot 网页模型的 tool_calls 合成层。

Copilot 网页没有原生 function calling。这里用"提示词让它输出 JSON + 解析"
合成 OpenAI 的 tool_calls：只在 `copilot_tool_shim=true` 且请求带 tools 时启用。

可靠性取舍：模型可能夹解释/代码围栏，所以解析失败一律退回普通文本，
不让整个请求报错；多轮工具循环也不如提供商模型稳。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

TOOL_CALL_PROMPT = (
    "你是一个工具调用路由器。可用工具（JSON Schema）：\n"
    "{tools}\n\n"
    "规则：\n"
    "1. 需要调用工具时，只输出："
    '{{"tool_calls":[{{"name":"工具名","arguments":{{...}}}}]}}\n'
    "2. 不需要调用工具时，只输出："
    '{{"content":"给用户的回答"}}\n'
    "3. 不要输出代码围栏、解释、引用或任何多余文字。\n"
    "4. arguments 必须是 JSON 对象，字段符合对应工具的 parameters。\n"
    "{extra}"
    "\n对话：\n{transcript}\n\n现在只输出 JSON："
)


def enabled() -> bool:
    from . import bridge as b

    return bool(b.CFG.get("copilot_tool_shim", False))


def _tool_specs(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "description": str(fn.get("description") or ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _transcript(messages: list[Any], limit: int = 16) -> str:
    from . import bridge as b

    lines: list[str] = []
    for m in list(messages or [])[-limit:]:
        role = str(getattr(m, "role", "") or "")
        text = b.message_text(m).strip()
        calls = getattr(m, "tool_calls", None)
        if role == "tool":
            lines.append(f"[tool result] {text}")
        elif role == "assistant" and calls:
            try:
                calls_txt = json.dumps(calls, ensure_ascii=False)
            except Exception:                    # noqa: BLE001
                calls_txt = str(calls)
            lines.append(f"[assistant tool_calls] {calls_txt}")
        elif text:
            lines.append(f"[{role}] {text}")
    return "\n".join(lines) or "[user] （空）"


def build_prompt(messages: list[Any], tools: list[dict[str, Any]] | None,
                 tool_choice: Any = None) -> str:
    specs = _tool_specs(tools)
    extra = ""
    if tool_choice == "none":
        extra = "5. 本轮不允许调用工具，只能输出 content。\n"
    elif tool_choice in ("required", "any"):
        extra = "5. 本轮必须调用至少一个工具。\n"
    elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        name = (tool_choice.get("function") or {}).get("name")
        if name:
            extra = f"5. 本轮必须调用工具「{name}」。\n"
    return TOOL_CALL_PROMPT.format(
        tools=json.dumps(specs, ensure_ascii=False, indent=1),
        extra=extra,
        transcript=_transcript(messages),
    )


def _repair_json(text: str) -> str:
    """修模型常见的非法 JSON 转义。

    实测 Copilot 会输出 `$git=\\'C:\\Program Files\\...\\'`：`\\'` 不是合法
    JSON 转义，json.loads 直接失败；另外偶尔有字符串里的裸换行/制表符。
    这里按字符串上下文逐字符修，不破坏 JSON 结构。
    """
    out: list[str] = []
    in_str = False
    i = 0
    while i < len(text):
        ch = text[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_str = False
            out.append(ch)
            i += 1
            continue
        if ch == "\\":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt in '"\\/bfnrtu':
                out.append(ch)
                i += 1
                continue
            if nxt == "'":
                # 模型把单引号也转义了：JSON 里没必要，去掉反斜杠
                out.append("'")
                i += 2
                continue
            # 其它非法转义（如 Windows 路径的单反斜杠）→ 转成字面反斜杠
            out.append("\\\\")
            i += 1
            continue
        if ch == "\n":
            out.append("\\n")
            i += 1
            continue
        if ch == "\r":
            out.append("\\r")
            i += 1
            continue
        if ch == "\t":
            out.append("\\t")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_tolerant(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:                        # noqa: BLE001
        try:
            return json.loads(_repair_json(text))
        except Exception:                    # noqa: BLE001
            return None


def _extract_json(text: str) -> Any:
    """从模型输出里抠出第一个完整 JSON 对象（容忍代码围栏/前后解释）。"""
    body = (text or "").strip()
    body = re.sub(r"^```(?:json)?\s*", "", body, flags=re.I)
    body = re.sub(r"\s*```$", "", body)
    start = body.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(body)):
        ch = body[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return _loads_tolerant(body[start:i + 1])
    return None


def _normalize_args(value: Any) -> str | None:
    if value is None:
        return "{}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        s = value.strip() or "{}"
        try:
            json.loads(s)
        except Exception:                        # noqa: BLE001
            return None
        return s
    return None


def parse_decision(raw: str, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """把模型输出解析成 {content, tool_calls}；解析不出就当普通文本。"""
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return {"content": (raw or "").strip(), "tool_calls": None}
    calls = data.get("tool_calls") or data.get("toolCalls")
    if isinstance(calls, dict):
        calls = [calls]
    normalized: list[dict[str, Any]] = []
    if isinstance(calls, list):
        for c in calls:
            if not isinstance(c, dict):
                continue
            fn = c.get("function") if isinstance(c.get("function"), dict) else c
            name = str(fn.get("name") or c.get("name") or "").strip()
            args = _normalize_args(fn.get("arguments", c.get("arguments")))
            if not name or args is None:
                continue
            normalized.append({
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": name, "arguments": args},
            })
    if normalized:
        content = data.get("content")
        return {"content": str(content) if isinstance(content, str) and content.strip() else None,
                "tool_calls": normalized}
    content = data.get("content")
    if isinstance(content, str) and content.strip():
        return {"content": content.strip(), "tool_calls": None}
    return {"content": (raw or "").strip(), "tool_calls": None}


async def decide(req: Any) -> dict[str, Any]:
    """让 Copilot 网页按 tools 决定：返回 {content, tool_calls}。

    每轮保留一个 Gantry 会话（`toolcall-*`）——侧栏「⋯ → 删除会话」需要它
    才能连带回收远端 Copilot 对话；标题固定成可读的「DSH 工具调用」，
    不再是一屏提示词第一行。
    """
    from . import bridge as b

    prompt = build_prompt(req.messages, getattr(req, "tools", None),
                          getattr(req, "tool_choice", None))
    sid = f"toolcall-{uuid.uuid4().hex[:12]}"
    agent_req = b.AgentRequest(
        model=req.model,
        messages=[b.Message(role="user", content=prompt)],
        stream=False,
        session_id=sid,
        workspace=None,
        agent_mode=False,
    )
    res = await b.plain_completions(agent_req)
    try:
        b.AGENT_SESSIONS.patch(sid, title="DSH 工具调用", tag="DSH")
    except Exception:                            # noqa: BLE001
        pass
    try:
        raw = (res["choices"][0]["message"]["content"] or "")
    except Exception:                            # noqa: BLE001
        raw = ""
    return parse_decision(raw, getattr(req, "tools", None))
