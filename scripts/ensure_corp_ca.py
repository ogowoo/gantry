#!/usr/bin/env python
"""公司网络 SSL 审查（SSL inspection）CA 信任与抓取兜底工具。

背景：公司网络对 TLS 做 SSL inspection——代理用内部根证书重签站点证书。
curl 走 schannel/Windows 证书库（IT 已推公司根）没事，但 Python 的 certifi
bundle 不含公司根证书，requests/urllib 一律抛 CERTIFICATE_VERIFY_FAILED。

策略（三层）：
1. 先用「certifi + 已收录的公司证书」合并 bundle（data/ca-bundle.pem）校验抓取；
2. 校验失败时把对端证书链抓下来（ssl.CERT_NONE + get_unverified_chain），
   链中自签根（subject == issuer）追加进 data/corp-ca.pem 后再重试——
   这一步需要在公司网络下跑过一次才能把根收进来；
3. 仍失败则退回 curl 子进程（schannel 走 Windows 证书库，天然信任公司根）。

环境变量 REQUESTS_CA_BUNDLE / SSL_CERT_FILE 指向合并 bundle。脚本每次运行都会
幂等自检并补写用户级环境变量（winreg 写 HKCU\\Environment，等价 setx 但不经
cmd），因此全新机器上只要跑过一次本脚本即完成部署，更新 bundle 路径也不用
再手动设置；服务/计划任务重启进程后生效。

用法：
    python scripts/ensure_corp_ca.py                # 自检 + 需要时收公司根
    python scripts/ensure_corp_ca.py <url> [输出文件]  # 抓页面（带兜底）
"""
from __future__ import annotations

import hashlib
import socket
import ssl
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
CORP_PEM = DATA / "corp-ca.pem"      # 公司网络收来的证书（只增不手工编辑）
BUNDLE = DATA / "ca-bundle.pem"      # certifi + corp-ca.pem 的合并产物


def _fp(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def _der_to_pem(der: bytes) -> str:
    import base64

    b = base64.encodebytes(der).decode("ascii")
    lines = "".join(b[i:i + 64] for i in range(0, len(b), 64))
    return f"-----BEGIN CERTIFICATE-----\n{lines}-----END CERTIFICATE-----\n"


def _split_pem(text: str) -> list[str]:
    """按 PEM 块切分，容忍注释行，返回每块的指纹集合用文本列表。"""
    blocks, cur = [], []
    for line in text.splitlines():
        if line.startswith("-----BEGIN"):
            cur = [line]
        elif line.startswith("-----END"):
            cur.append(line)
            blocks.append("\n".join(cur) + "\n")
            cur = []
        elif cur:
            cur.append(line)  # PEM 正文行（certifi 的注释行混在里面，解码端忽略）
    return blocks


def _read_user_env(name: str) -> str | None:
    """读用户级环境变量当前值（注册表读也被禁时退回进程环境）。"""
    import os

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return os.environ.get(name)


def setup_env(bundle: Path) -> list[str]:
    """幂等补写用户级环境变量，让 requests/stdlib 的全局校验都认合并 bundle。

    用 winreg 写 HKCU\\Environment（与 setx 等效，不经 cmd）；已存在且值正确
    就跳过，所以每次运行调用都无副作用。返回本次改动的变量名列表。
    非 Windows 平台直接跳过（linux 上通常没有这种 SSL inspection 问题）。
    """
    if sys.platform != "win32":
        return []
    import winreg

    changed = []
    target = str(bundle)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
                try:
                    val, _ = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    val = None
                if val == target:
                    continue
                winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, target)
                changed.append(name)
    except OSError:
        # 有的沙箱/策略禁 python 进程写注册表（winreg OpenKey 即拒），退 setx。
        # setx 也不行的机器（如受限 CI）就此跳过——抓取兜底链不受影响，
        # 只是其他 Python 进程得靠人工设置环境变量。
        import subprocess

        for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
            cur = _read_user_env(name)
            if cur == target:
                continue
            p = subprocess.run(["setx", name, target], capture_output=True, timeout=30)
            if p.returncode == 0:
                changed.append(name)
            else:
                print(f"（警告：补写 {name} 失败：{p.stderr.decode(errors='replace')[:120]}）")
    # 同时刷新当前进程，脚本内后续抓取立即生效
    import os

    os.environ["REQUESTS_CA_BUNDLE"] = target
    os.environ["SSL_CERT_FILE"] = target
    return changed


def ensure_bundle() -> Path:
    """生成/刷新合并 bundle：certifi 全量 + corp-ca.pem，按指纹去重。"""
    import certifi

    DATA.mkdir(exist_ok=True)
    seen: set[str] = set()
    out: list[str] = []
    src = Path(certifi.where()).read_text(encoding="ascii", errors="ignore")
    extra = CORP_PEM.read_text(encoding="ascii", errors="ignore") if CORP_PEM.exists() else ""
    for block in _split_pem(src) + _split_pem(extra):
        import base64

        body = "".join(l for l in block.splitlines() if not l.startswith("-----"))
        try:
            der = base64.b64decode(body)
        except Exception:
            continue  # 坏块跳过，不因为一个坏 PEM 废掉整个 bundle
        fp = _fp(der)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(block)
    BUNDLE.write_text("".join(out), encoding="ascii")
    return BUNDLE


def collect_corp_root(host: str, port: int = 443) -> int:
    """SSL 校验失败时抓对端证书链，把链里没进 bundle 的证书追加到 corp-ca.pem。

    返回新收录的证书数。只收「新」的（按指纹比对现有 bundle），避免每次重复追加。
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # 故意不校验：此刻正是校验不过才来的
    existing = _split_pem(BUNDLE.read_text(encoding="ascii", errors="ignore")) if BUNDLE.exists() else []
    import base64

    known = set()
    for block in existing:
        body = "".join(l for l in block.splitlines() if not l.startswith("-----"))
        try:
            known.add(_fp(base64.b64decode(body)))
        except Exception:
            pass
    added = 0
    chunks: list[str] = []
    with socket.create_connection((host, port), timeout=15) as s:
        with ctx.wrap_socket(s, server_hostname=host) as ss:
            for der in ss.get_unverified_chain():
                if _fp(der) in known:
                    continue
                known.add(_fp(der))
                chunks.append(_der_to_pem(der))
                added += 1
    if chunks:
        with CORP_PEM.open("a", encoding="ascii") as f:
            f.write(f"# collected from {host}:{port} (SSL inspection chain)\n")
            f.writelines(chunks)
        ensure_bundle()
    return added


def fetch(url: str, out: Path | None = None) -> bytes:
    """抓页面：合并 bundle 校验 → 失败收公司根重试 → 再失败退 curl(schannel)。"""
    import re
    import urllib.error
    import urllib.request

    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    bundle = ensure_bundle()
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 gantry-corp-ca/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context(cafile=str(bundle))) as r:
                body = r.read()
            if out:
                out.write_bytes(body)
            return body
        except urllib.error.URLError as exc:
            last = exc
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if attempt == 1 and "CERTIFICATE_VERIFY_FAILED" in str(reason):
                added = collect_corp_root(host)
                print(f"（已把 {added} 张对端证书收入 {CORP_PEM.name}，重试…）")
                continue
            break
    # 第三层兜底：curl 走 schannel/Windows 证书库，公司根由 IT 推送，天然信任
    cmd = ["curl", "-sS", "--fail", "--location", "--max-time", "60", url]
    if out:
        cmd.extend(["-o", str(out)])
    proc = subprocess.run(cmd, capture_output=True, timeout=90)
    if proc.returncode == 0:
        return out.read_bytes() if out else proc.stdout
    raise RuntimeError(f"抓取失败：{last!r}；curl 兜底也失败：{proc.stderr.decode(errors='replace')[:300]}")


def main(argv: list[str]) -> int:
    bundle = ensure_bundle()
    changed = setup_env(bundle)
    if changed:
        print(f"（已补写用户级环境变量：{', '.join(changed)}；新开的进程生效）")
    if len(argv) > 1:
        out = Path(argv[2]) if len(argv) > 2 else None
        body = fetch(argv[1], out)
        print(f"OK {len(body)} bytes" + (f" -> {out}" if out else ""))
        return 0
    # 自检：抓 github.com 首页，顺带演示完整兜底链
    print(f"bundle: {bundle} ({len(_split_pem(bundle.read_text(encoding='ascii', errors='ignore')))} 张证书)")
    try:
        body = fetch("https://github.com/")
        print(f"self-check OK: https://github.com/ {len(body)} bytes")
        return 0
    except Exception as exc:
        print(f"self-check FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
