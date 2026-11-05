# -*- coding: utf-8 -*-
"""修拆包后残留的跨模块引用：把 teams_dom 需要的 _ME_PREFIX 一并搬过去，
并检查三个新模块里"引用了但未定义/未 import"的名字。"""
import ast
import re
from pathlib import Path

DOM = Path("gantry/teams_dom.py")
TEAMS = Path("gantry/teams.py")

# 1) 把 _ME_PREFIX 定义从 teams.py 搬到 teams_dom.py
src = TEAMS.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src)
span = None
for node in tree.body:
    if isinstance(node, ast.Assign):
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if names == ["_ME_PREFIX"]:
            span = (node.lineno, node.end_lineno)
if span:
    s, e = span
    block = "".join(lines[s - 1:e])
    dom = DOM.read_text(encoding="utf-8")
    dom += "\n\n" + block.rstrip() + "\n"
    DOM.write_text(dom, encoding="utf-8")
    drop = set(range(s - 1, e))
    kept = [ln for i, ln in enumerate(lines) if i not in drop]
    out = "".join(kept)
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    out = out.replace("from .teams_dom import (  # noqa: F401  （对外名字保持不变）\n",
                      "from .teams_dom import (  # noqa: F401  （对外名字保持不变）\n    _ME_PREFIX,\n", 1)
    TEAMS.write_text(out, encoding="utf-8")
    print("_ME_PREFIX 已搬到 teams_dom")

# 2) 静态检查：每个新模块里调用的裸名是否可在本模块或 import 中解析
MODULES = ["gantry/teams_dom.py", "gantry/teams_send.py", "gantry/teams_api.py"]
BUILTINS = {"print", "len", "str", "int", "float", "dict", "list", "set", "tuple", "bool",
            "range", "enumerate", "zip", "sorted", "min", "max", "sum", "abs", "any", "all",
            "isinstance", "getattr", "setattr", "hasattr", "repr", "round", "open", "bytes",
            "type", "ValueError", "Exception", "RuntimeError", "KeyError", "IndexError",
            "AttributeError", "TypeError", "NotImplementedError", "None", "True", "False"}
for path in MODULES:
    text = Path(path).read_text(encoding="utf-8")
    mod = ast.parse(text)
    defined = set()
    imported = set()
    for node in ast.walk(mod):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                imported.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    used = {n.id for n in ast.walk(mod) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    missing = sorted(used - defined - imported - BUILTINS)
    # 过滤掉方法/属性式访问（ast.Name 已排除），只报裸名
    print(f"{path}: 未解析裸名 -> {missing[:14]}")
