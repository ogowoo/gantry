# -*- coding: utf-8 -*-
"""原子落盘助手：同目录临时文件写完后 os.replace 整体替换。

为什么要它：工具直接 open(path, "w") 时，进程/机器恰好在写入中途挂掉会留下
半个文件（真实事故：改服务自身代码时半截文件把服务搞挂）。先写同目录临时文件、
再 os.replace（POSIX/Windows 上都是原子语义：要么旧文件要么新文件），
调用方永远看不到半成品。

Windows 注意：os.replace 对同目录内的文件是原子的；临时文件放目标目录里
（跨盘 replace 会退化成 copy+delete，不再是原子）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8",
                      newline: str | None = "",
                      preserve_mode: bool = False) -> None:
    """把 text 原子写入 path。

    - `newline=""`：原样写入，Windows 下不把 \\n 静默转成 \\r\\n（与 open(newline="") 一致）；
      传 None 走平台默认转换。
    - `preserve_mode=True`：目标已存在时把它的权限位复制到临时文件再替换
      （os.replace 用临时文件的权限创建新文件，默认会变成 0600；
      kb 落盘等场景需要保留原文件权限语义）。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                               prefix=target.name + ".", suffix=".tmp")
    try:
        if preserve_mode:
            try:
                os.chmod(tmp, os.stat(target).st_mode & 0o7777)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
