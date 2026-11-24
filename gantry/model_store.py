"""本地 GGUF 模型库：类别识别 + 规范存放 + 完整性校验 + 位置自愈。

## 为什么需要它

本地模型出问题几乎都是"**文件对了，位置/账目不对**"：

- 从网上下载的 GGUF 落在 `data/models/` 根目录，而 provider 的 `model_path` 还指着
  别处的旧文件 —— 界面点「启动」加载的是旧路径，新模型看起来"下载了却启动不了"；
- 向量模型、多模态投影（mmproj）和对话模型混在一个目录里，自动发现只能挑排序第一
  个，挑到 embedding 或 mmproj 就报"加载失败"；
- 下载被中断的文件留着合法 GGUF 头，但张量数据不全，`llama.cpp` 加载时才炸；
- 文件被手动挪走/改名后，配置里的路径失效，没有任何自愈。

本模块把这几件事分开做：

    detect_kind()   看 GGUF 元数据（architecture / clip.* / bert.*）+ 文件名 → 类别
    scan()          遍历模型库（可含外部目录），给出类别、是否可用、该放哪里
    organize()      把放错位置的文件移进规范子目录（chat/ embedding/ rerank/ mmproj/）
    verify()        头合法 + 张量数据不越界 → 截断/损坏能被提前发现
    find_file()     按文件名在库里找（provider 路径失效时的自愈依据）

规范布局：

    data/models/
        chat/       对话模型（可含多模态主模型）
        embedding/  向量模型（bge / e5 / gte / nomic-embed …）
        rerank/     重排模型
        mmproj/     多模态投影（配合 chat/ 里的视觉模型使用）

类别判定优先看 GGUF 元数据（可信），元数据缺失或不可读时退回文件名启发式，
判定结果带 `reason` 与 `source`（metadata/filename/default），界面上能解释清楚。
"""
from __future__ import annotations

import io
import os
import shutil
import struct
from pathlib import Path
from typing import Any, Iterable

KINDS = ("chat", "embedding", "rerank", "mmproj")
KIND_LABEL = {"chat": "对话", "embedding": "向量", "rerank": "重排", "mmproj": "多模态投影"}

# GGUF KV 值类型 → (读取方式, 字节数)
_KV_TYPES = {
    0: ("<B", 1), 1: ("<b", 1), 2: ("<H", 2), 3: ("<h", 2),
    4: ("<I", 4), 5: ("<i", 4), 6: ("<f", 4), 7: ("<B", 1),
    8: ("str", 0), 9: ("array", 0), 10: ("<Q", 8), 11: ("<q", 8), 12: ("<d", 8),
}

# 常见 ggml 量化类型的 block 元素数与字节数（校验张量是否被截断用）
_GGML_TYPES = {
    0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22), 7: (32, 24),
    8: (32, 34), 9: (32, 36), 10: (256, 84), 11: (256, 110), 12: (256, 144),
    13: (256, 176), 14: (256, 210), 15: (256, 292), 16: (256, 66), 17: (256, 74),
    18: (256, 98), 19: (256, 50), 20: (32, 18), 21: (256, 110), 22: (256, 82),
    23: (256, 136), 24: (1, 1), 25: (1, 2), 26: (1, 4), 27: (1, 8), 28: (1, 8),
    29: (256, 56), 30: (1, 2), 34: (256, 54), 35: (256, 66),
}

_EMBED_HINTS = ("embed", "bge", "e5-", "e5_", "gte-", "gte_", "jina", "nomic-embed",
                "text-embedding", "minilm", "multilingual-e5")
_RERANK_HINTS = ("rerank", "cross-encoder", "bge-reranker")
_MMPROJ_HINTS = ("mmproj", "projector", "vision-encoder", "clip-model")
_EMBED_ARCHES = ("bert", "nomic-bert", "jina-bert-v2", "jina-bert", "gte", "bge-m3",
                 "modernbert", "mamba-embed")
_MMPROJ_ARCHES = ("clip", "mmproj", "projector")


class ModelStoreError(RuntimeError):
    """模型库操作失败（路径越界、文件缺失等）。"""


# ── GGUF 头解析 ────────────────────────────────────────────────────────
def _read_exact(fh: Any, n: int) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise EOFError("文件提前结束")
    return data


def _read_str(fh: Any, cap: int) -> str:
    (n,) = struct.unpack("<Q", _read_exact(fh, 8))
    if n > (1 << 40):                        # 明显是解析错位，别去 seek 天文数字
        raise EOFError("字符串长度异常（头部解析错位）")
    if n > cap:
        # 超长字符串（词表之类）不读进内存，也不参与判定
        fh.seek(n, os.SEEK_CUR)
        return ""
    return _read_exact(fh, n).decode("utf-8", "ignore")


def read_header(path: str | Path, *, max_bytes: int = 16_000_000,
                max_pairs: int = 512, max_tensors: int = 4096) -> dict[str, Any]:
    """解析 GGUF 头：magic/版本/KV（裁剪过的）张量信息，并粗查数据是否被截断。

    返回值：
      {ok, error?, version, kv{...}, tensors, data_start, expected_end, truncated}
    只读文件头部（默认上限 16MB，够覆盖大多数分词器词表），不会把大模型读进内存。
    """
    p = Path(path)
    info: dict[str, Any] = {"ok": False, "kv": {}, "tensors": 0,
                            "version": None, "data_start": None,
                            "expected_end": None, "truncated": None}
    try:
        size = p.stat().st_size
    except OSError as exc:
        info["error"] = f"无法读取文件信息：{exc}"
        return info
    if size == 0:
        info["error"] = "文件为空（下载未开始/被截断）"
        return info
    # 一次性读进内存再解析：分词器词表有几十万个 KV，逐个小 read 会慢到秒级
    budget = min(int(size), int(max_bytes))
    try:
        with open(p, "rb") as raw:
            head = raw.read(budget)
    except OSError as exc:
        info["error"] = f"读取文件失败：{exc}"
        return info
    head_cut = size > len(head)
    fh: Any = io.BytesIO(head)
    try:
        if _read_exact(fh, 4) != b"GGUF":
            info["error"] = "不是 GGUF 文件（缺少 GGUF 头）"
            return info
        (version,) = struct.unpack("<I", _read_exact(fh, 4))
        info["version"] = version
        if version not in (1, 2, 3):
            info["error"] = f"不支持的 GGUF 版本：{version}"
            return info
        (tensor_count,) = struct.unpack("<Q", _read_exact(fh, 8))
        (kv_count,) = struct.unpack("<Q", _read_exact(fh, 8))
        info["tensors"] = int(tensor_count)
        kv: dict[str, Any] = {}
        try:
            for _ in range(min(int(kv_count), max_pairs)):
                key = _read_str(fh, 8192)
                (vtype,) = struct.unpack("<I", _read_exact(fh, 4))
                val = _read_value(fh, vtype, depth=0)
                if key:
                    kv[key] = val
        except EOFError:
            info["kv"] = kv
            if head_cut:
                info.update(ok=True, note="元数据超出头部预算，未做完整性校验")
                return info
            info["error"] = "文件在头部就结束了（下载未完成？）"
            return info
        info["kv"] = kv
        # 张量信息：只用来算数据区起点与是否越界
        dims_total = 0
        parsable = True
        try:
            for _ in range(int(tensor_count)):
                _read_str(fh, 4096)                      # 名字
                (n_dims,) = struct.unpack("<I", _read_exact(fh, 4))
                shape = [struct.unpack("<Q", _read_exact(fh, 8))[0]
                         for _ in range(min(int(n_dims), 8))]
                (ttype,) = struct.unpack("<I", _read_exact(fh, 4))
                (offset,) = struct.unpack("<Q", _read_exact(fh, 8))
                n_elem = 1
                for d in shape:
                    n_elem *= max(1, int(d))
                meta = _GGML_TYPES.get(int(ttype))
                if meta:
                    blocks, nbytes = meta
                    per_block = max(1, n_elem // max(1, blocks))
                    dims_total = max(dims_total, int(offset) + per_block * nbytes)
                else:
                    dims_total = max(dims_total, int(offset))
        except EOFError:
            parsable = False
        info["ok"] = True
        if parsable:
            data_start = fh.tell()
            # 张量偏移可能带对齐填充：取「数据区 + 最大张量终点」的保守下界
            info["data_start"] = data_start
            info["expected_end"] = data_start + dims_total
            if dims_total and size + 16 < info["expected_end"]:
                info["truncated"] = True
        elif head_cut:
            info["note"] = "张量信息超出头部预算，未做完整性校验"
    except EOFError:
        info["error"] = "文件在头部就结束了（下载未完成？）"
    except Exception as exc:                     # noqa: BLE001
        info["error"] = f"解析 GGUF 头失败：{str(exc)[:160]}"
    return info


def _read_value(fh: Any, vtype: int, *, depth: int) -> Any:
    kind = _KV_TYPES.get(int(vtype))
    if kind is None:
        raise ValueError(f"未知 KV 类型 {vtype}")
    how, _n = kind
    if how == "str":
        return _read_str(fh, 4096)
    if how == "array":
        (etype,) = struct.unpack("<I", _read_exact(fh, 4))
        (n,) = struct.unpack("<Q", _read_exact(fh, 8))
        n_int = int(n)
        # 大数组（词表：几十万个字符串）不逐个建对象——只用摘要，解析才快
        if n_int > 64:
            if int(etype) == 8:                 # array<string>
                for _ in range(n_int):
                    _skip_value(fh, etype)
                return f"<{n_int} strings>"
            elem = _KV_TYPES.get(int(etype))
            if elem and elem[1]:                # 定长数值：直接跳过整块
                fh.seek(n_int * int(elem[1]), os.SEEK_CUR)
                return f"<{n_int} values>"
            for _ in range(n_int):
                _skip_value(fh, etype)
            return f"<{n_int} values>"
        return [_read_value(fh, etype, depth=depth + 1) for _ in range(n_int)]
    return struct.unpack(how, _read_exact(fh, _n))[0]


def _skip_value(fh: Any, vtype: int) -> None:
    kind = _KV_TYPES.get(int(vtype))
    if kind is None:
        raise ValueError(f"未知 KV 类型 {vtype}")
    how, n = kind
    if how == "str":
        (ln,) = struct.unpack("<Q", _read_exact(fh, 8))
        ln = int(ln)
        if ln > (1 << 40):
            raise EOFError("字符串长度异常（头部解析错位）")
        fh.seek(ln, os.SEEK_CUR)
    elif how == "array":
        (etype,) = struct.unpack("<I", _read_exact(fh, 4))
        (n_items,) = struct.unpack("<Q", _read_exact(fh, 8))
        n_int = int(n_items)
        if int(etype) == 8:                     # array<string>：逐个跳长度
            for _ in range(n_int):
                _skip_value(fh, etype)
            return
        elem = _KV_TYPES.get(int(etype))
        if elem and elem[1]:
            fh.seek(n_int * int(elem[1]), os.SEEK_CUR)
            return
        for _ in range(n_int):
            _skip_value(fh, etype)
    else:
        fh.seek(n, os.SEEK_CUR)


# ── 类别识别 ─────────────────────────────────────────────────────────
def _kind_from_name(name: str) -> tuple[str, str] | None:
    n = name.lower()
    if any(k in n for k in _MMPROJ_HINTS):
        return "mmproj", "文件名含 mmproj/projector"
    if any(k in n for k in _RERANK_HINTS):
        return "rerank", "文件名含 rerank"
    if any(k in n for k in _EMBED_HINTS):
        return "embedding", "文件名含 embed/bge/e5/gte 等向量模型标记"
    return None


def _kind_from_meta(kv: dict[str, Any]) -> tuple[str, str] | None:
    arch = str(kv.get("general.architecture") or "").strip().lower()
    general_name = str(kv.get("general.name") or "").strip()
    # 只吃元数据里的名字，文件名交给 _kind_from_name 兜底（source 才能如实区分）
    blob = f"{arch} {general_name}".lower()
    if any(k in kv for k in ("clip.has_vision_encoder", "clip.has_vision_encoder_norm",
                             "clip.projector_type")) or arch in _MMPROJ_ARCHES:
        return "mmproj", f"元数据含 clip/投影字段（architecture={arch or '?'}）"
    if arch in _EMBED_ARCHES or "bert.pooler_type" in kv:
        return "embedding", f"元数据 architecture={arch or '?'} 属向量模型"
    if "rerank" in blob or "classifier" in arch:
        return "rerank", f"元数据提示重排（architecture={arch or '?'}）"
    if any(k in blob for k in _EMBED_HINTS):
        return "embedding", "元数据名称含向量模型标记"
    if arch:
        return "chat", f"元数据 architecture={arch}"
    return None


def detect_kind(path: str | Path, *, header: dict[str, Any] | None = None) -> dict[str, Any]:
    """判断一个 GGUF 属于哪类模型。

    返回 {kind, source, reason, arch, name, confidence, header}
    source: metadata / filename / default —— 界面据此说明"凭什么这么判"。
    """
    p = Path(path)
    hdr = header if header is not None else read_header(p)
    kv = hdr.get("kv") or {}
    arch = str(kv.get("general.architecture") or "")
    gname = str(kv.get("general.name") or "")
    hit = _kind_from_meta(kv) if hdr.get("ok") else None
    if hit:
        kind, reason = hit
        return {"kind": kind, "source": "metadata", "reason": reason,
                "arch": arch, "name": gname or p.stem, "confidence": "high",
                "header": hdr}
    hit = _kind_from_name(p.name)
    if hit:
        kind, reason = hit
        return {"kind": kind, "source": "filename", "reason": reason,
                "arch": arch, "name": gname or p.stem, "confidence": "medium",
                "header": hdr}
    return {"kind": "chat", "source": "default",
            "reason": "没有向量/投影标记，按对话模型处理",
            "arch": arch, "name": gname or p.stem, "confidence": "low",
            "header": hdr}


def is_chat_model(path: str | Path) -> bool:
    try:
        return detect_kind(path)["kind"] == "chat"
    except Exception:                            # noqa: BLE001
        return False


# ── 模型库扫描 / 整理 ────────────────────────────────────────────────
def _iter_gguf(root: Path, extra_roots: Iterable[str | Path] = ()) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for base in [root, *(Path(x) for x in extra_roots if str(x or "").strip())]:
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            try:
                if not p.is_file() or p.suffix.lower() != ".gguf":
                    continue
                key = str(p.resolve()).lower()
                if key in seen:
                    continue
                if any(part.startswith(".") for part in p.relative_to(base).parts):
                    continue
                seen.add(key)
                out.append(p)
            except OSError:
                continue
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def safe_path(root: Path, rel: str) -> Path:
    """把用户给的相对路径限制在模型库根目录内（防目录穿越）。"""
    raw = str(rel or "").replace("\\", "/").strip()
    if not raw:
        raise ModelStoreError("缺少文件路径")
    root_res = Path(root).resolve()
    target = (root_res / raw).resolve()
    if target != root_res and root_res not in target.parents:
        raise ModelStoreError(f"路径越出模型库：{raw}")
    return target


def canonical_rel(kind: str, name: str) -> str:
    return f"{kind}/{Path(name).name}"


def scan(root: str | Path, *, extra_roots: Iterable[str | Path] = (),
         verify_data: bool = True) -> dict[str, Any]:
    """扫描模型库：返回每个文件的类别、可用性、规范位置与是否需要移动。"""
    root_p = Path(root)
    items: list[dict[str, Any]] = []
    for p in _iter_gguf(root_p, extra_roots):
        hdr = read_header(p) if verify_data else None
        det = detect_kind(p, header=hdr)
        kind = det["kind"]
        issues: list[str] = []
        h = det["header"] or {}
        if h and h.get("error"):
            issues.append(h["error"])
        elif h and h.get("truncated"):
            issues.append("数据不完整（张量区间超出文件大小，下载可能中断）")
        name = p.name
        want = canonical_rel(kind, name)
        inside = _is_under(p, root_p)
        rel = _rel(p, root_p) if inside else str(p)
        needs_move = inside and rel.replace("\\", "/") != want
        items.append({
            "name": name,
            "path": rel,
            "full": str(p),
            "inside": inside,
            "kind": kind,
            "kind_label": KIND_LABEL.get(kind, kind),
            "source": det["source"],
            "reason": det["reason"],
            "arch": det["arch"],
            "size": _size(p),
            "mtime": _mtime(p),
            "valid": not issues,
            "issues": issues,
            "canonical": want,
            "needs_move": needs_move,
        })
    return {"root": str(root_p), "models": items,
            "needs_organize": sum(1 for x in items if x["needs_move"]),
            "broken": sum(1 for x in items if not x["valid"])}


def _is_under(path: Path, root: Path) -> bool:
    try:
        r = root.resolve()
        return path.resolve() != r and r in path.resolve().parents
    except OSError:
        return False


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)                     # 同盘：原子改名
    except OSError:
        shutil.move(str(src), str(dst))          # 跨盘：复制后删除


def organize(root: str | Path, *, apply: bool = False,
             extra_roots: Iterable[str | Path] = ()) -> dict[str, Any]:
    """把放错位置的模型移进规范子目录。

    默认只做计划（apply=False）；apply=True 才真的移动。
    目标重名时追加 `-1/-2…`，绝不覆盖已有文件。
    """
    root_p = Path(root)
    report = scan(root_p, extra_roots=extra_roots)
    actions: list[dict[str, Any]] = []
    moved = 0
    for item in report["models"]:
        if not item["needs_move"]:
            actions.append({"path": item["path"], "kind": item["kind"],
                            "action": "skip", "reason": "已在规范位置" if item["inside"] else "库外文件不动"})
            continue
        src = root_p / item["path"]
        dst_rel = item["canonical"]
        dst = root_p / dst_rel
        n = 1
        while dst.exists() and dst != src:
            stem, suf = dst.stem, dst.suffix
            dst = root_p / dst.parent.name / f"{stem}-{n}{suf}"
            n += 1
        act = {"from": item["path"], "to": _rel(dst, root_p), "kind": item["kind"],
               "kind_label": item["kind_label"], "action": "move"}
        if apply:
            try:
                _move(src, dst)
                moved += 1
            except Exception as exc:             # noqa: BLE001
                act.update(action="error", error=str(exc)[:160])
        actions.append(act)
    return {"root": str(root_p), "applied": bool(apply), "moved": moved,
            "actions": actions}


def find_file(root: str | Path, name: str,
              extra_roots: Iterable[str | Path] = ()) -> list[dict[str, Any]]:
    """按文件名在库里（含外部目录）找候选；provider 路径失效时用来自愈。"""
    want = Path(str(name or "")).name.lower()
    if not want:
        return []
    out: list[dict[str, Any]] = []
    for p in _iter_gguf(Path(root), extra_roots):
        if p.name.lower() == want:
            out.append({"full": str(p), "path": _rel(p, Path(root)) if _is_under(p, Path(root)) else str(p),
                        "kind": detect_kind(p)["kind"], "size": _size(p)})
    return out


def delete(root: str | Path, rel: str) -> dict[str, Any]:
    """删除库内文件（支持子目录，路径经越界校验）。"""
    root_p = Path(root)
    target = safe_path(root_p, rel)
    if not target.is_file():
        raise ModelStoreError(f"文件不存在：{rel}")
    target.unlink()
    # 顺手清掉空的类别目录，避免界面里留空壳
    try:
        parent = target.parent
        if parent != root_p.resolve() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    return {"ok": True, "deleted": _rel(target, root_p) if _is_under(target, root_p) else str(target)}


def pair_mmproj(model_path: str | Path, root: str | Path) -> str:
    """给多模态主模型找配套的 mmproj（同名前缀优先）。找不到返回空串。"""
    p = Path(model_path)
    stem = p.stem.lower()
    best = ""
    best_score = 0
    for cand in _iter_gguf(Path(root)):
        cname = cand.name.lower()
        if "mmproj" not in cname and "projector" not in cname:
            continue
        score = 0
        cstem = cand.stem.lower()
        # 逐段比对前缀："Qwen2.5-VL-7B-Q4" vs "Qwen2.5-VL-7B-mmproj-F16"
        for token in stem.replace(".", "-").split("-"):
            if len(token) >= 3 and token in cstem:
                score += 1
        if score > best_score or (score == best_score and not best):
            best_score = score
            best = str(cand)
    return best if best_score else ""
