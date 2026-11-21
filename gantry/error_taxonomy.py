# -*- coding: utf-8 -*-
"""统一错误分类：把工具/服务异常映射成可决策的类别。

分类只描述“该怎么处理”，不自动绕过审批或沙箱：
- network/timeout/rate_limit：可重试（有上限）；
- permission：停下来让用户处理，不自动重试；
- argument：改参数/换工具，不原样重试；
- business：业务规则失败，交给调用方修复；
- internal：记录后最多重试一次，仍失败则停止。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RETRYABLE = {"network", "timeout", "rate_limit", "internal"}
STOP_KINDS = {"permission", "argument", "business"}


@dataclass(frozen=True)
class ErrorInfo:
    kind: str
    retryable: bool
    action: str          # retry / fix / stop
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "retryable": self.retryable,
                "action": self.action, "reason": self.reason}


def classify_text(text: Any) -> ErrorInfo:
    s = str(text or "").lower()
    if any(x in s for x in ("rate limit", "too many requests", "429", "限流", "quota")):
        return ErrorInfo("rate_limit", True, "retry", "限流/配额，退避后重试")
    if any(x in s for x in ("timed out", "timeout", "超时", "deadline exceeded")):
        return ErrorInfo("timeout", True, "retry", "超时，可有限重试")
    if any(x in s for x in ("permission denied", "access denied", "sandbox", "unauthorized",
                            "forbidden", "403", "401", "权限", "拒绝访问", "沙箱")):
        return ErrorInfo("permission", False, "stop", "权限/沙箱拒绝，需人工处理")
    if any(x in s for x in ("connection", "network", "dns", "ssl", "econn", "断开",
                            "无法连接", "网络")):
        return ErrorInfo("network", True, "retry", "网络异常，可有限重试")
    if any(x in s for x in ("invalid argument", "missing required", "bad request", "400",
                            "参数", "不合法", "缺少", "无法解析", "syntaxerror")):
        return ErrorInfo("argument", False, "fix", "参数/输入错误，修改后重试")
    if any(x in s for x in ("not found", "不存在", "业务", "规则", "conflict", "already")):
        return ErrorInfo("business", False, "fix", "业务规则失败，需要改变方案")
    return ErrorInfo("internal", True, "retry", "内部/未知错误，有限重试")


def classify_exception(exc: BaseException) -> ErrorInfo:
    name = type(exc).__name__.lower()
    text = f"{type(exc).__name__}: {exc}"
    s = text.lower()
    if any(x in name for x in ("timeout", "timedout")):
        return ErrorInfo("timeout", True, "retry", "超时，可有限重试")
    if any(x in name for x in ("connection", "network", "ssl", "transport")):
        return ErrorInfo("network", True, "retry", "网络异常，可有限重试")
    if any(x in name for x in ("permission", "forbidden")):
        return ErrorInfo("permission", False, "stop", "权限拒绝，需人工处理")
    if any(x in name for x in ("valueerror", "typeerror", "argument")):
        return ErrorInfo("argument", False, "fix", "参数错误，修改后重试")
    return classify_text(s)


def retry_decision(info: ErrorInfo, attempt: int, max_attempts: int = 1) -> dict[str, Any]:
    attempt = max(0, int(attempt))
    max_attempts = max(1, int(max_attempts))
    if not info.retryable:
        return {"action": info.action, "retry": False,
                "reason": info.reason, "attempt": attempt}
    if attempt >= max_attempts:
        return {"action": "stop", "retry": False,
                "reason": f"已达重试上限 {max_attempts}", "attempt": attempt}
    return {"action": "retry", "retry": True,
            "reason": info.reason, "attempt": attempt + 1}
