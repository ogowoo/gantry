# -*- coding: utf-8 -*-
"""sensitive_names.py — 凭据/密钥类文件名的共享拒单。

凡是**自动批量**把文件内容带出本地（zip 解压落盘、工作区打包注入）的路径，
都必须过一遍这里：凭据类文件宁可少给，不可外送。模型仍然可以用 read_text
这类**显式单文件**工具读取工作区里自己项目的 config.json（那是用户授意的
按需读取，与自动全量外送不同）。

无任何内部依赖（仅 pathlib/os），可被 downloads/workspace_bundle 等安全共享。
"""
from __future__ import annotations

SENSITIVE_EXACT = {
    "config.json", "secrets.json", "credentials.json",
    "client_secret.json", "service_account.json", "api_token",
    ".env", ".env.local", ".env.development", ".env.production",
    ".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519",
}
SENSITIVE_HINTS = (
    "secret", "credential", "password", "passwd",
    "api_key", "apikey", "access_token", "private_key",
)
SENSITIVE_SUFFIX = (".pem", ".key", ".p12", ".pfx", ".keystore")


def sensitive_name(name: str) -> bool:
    """文件名是否属于凭据/密钥类。"""
    n = (name or "").lower()
    if n in SENSITIVE_EXACT or n.startswith(".env"):
        return True
    if n.endswith(SENSITIVE_SUFFIX):
        return True
    return any(h in n for h in SENSITIVE_HINTS)
