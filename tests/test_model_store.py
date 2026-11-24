# -*- coding: utf-8 -*-
"""本地模型库：类别识别 / 规范存放 / 完整性校验 / 路径自愈。

用**合成的 GGUF 头**做测试（几十字节，不加载任何真实模型）：
- 类别：元数据优先（architecture / clip.* / bert.*），文件名兜底；
- 位置：放错目录能扫出来并移动，重名不覆盖，库外文件不动；
- 体检：张量区间越界 → 判为下载不完整（这类文件"下载成功但启动失败"）；
- 自愈：provider 路径失效时按文件名找回。
"""
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from gantry import model_store as ms

ROOT = Path(__file__).resolve().parent.parent


# ── 合成 GGUF ────────────────────────────────────────────────────────
def _s(text: str) -> bytes:
    b = text.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def write_gguf(path: Path, kv: dict | None = None, *, arch: str = "",
               tensors: list[tuple[str, list[int], int]] | None = None,
               data_bytes: int | None = None, magic: bytes = b"GGUF") -> Path:
    """写一个最小 GGUF：头 + KV + 张量信息 + 数据区。"""
    kv = dict(kv or {})
    if arch:
        kv["general.architecture"] = arch
    kv.setdefault("general.name", path.stem)
    tensors = tensors or []
    out = bytearray()
    out += magic
    out += struct.pack("<I", 3)
    out += struct.pack("<Q", len(tensors))
    out += struct.pack("<Q", len(kv))
    for key, val in kv.items():
        out += _s(key)
        if isinstance(val, bool):
            out += struct.pack("<I", 7) + struct.pack("<B", 1 if val else 0)
        elif isinstance(val, int):
            out += struct.pack("<I", 4) + struct.pack("<I", val)
        elif isinstance(val, float):
            out += struct.pack("<I", 6) + struct.pack("<f", val)
        else:
            out += struct.pack("<I", 8) + _s(str(val))
    expect = 0
    for name, shape, ttype in tensors:
        out += _s(name)
        out += struct.pack("<I", len(shape))
        n = 1
        for d in shape:
            out += struct.pack("<Q", d)
            n *= d
        out += struct.pack("<I", ttype)
        out += struct.pack("<Q", expect)
        blocks, nbytes = ms._GGML_TYPES.get(ttype, (1, 4))
        expect += max(1, n // max(1, blocks)) * nbytes
    total = expect if data_bytes is None else int(data_bytes)
    out += b"\0" * total
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


class HeaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_ms_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rejects_non_gguf(self):
        p = self.tmp / "x.gguf"
        p.write_bytes(b"NOTAGGUF" + b"\0" * 32)
        hdr = ms.read_header(p)
        self.assertFalse(hdr["ok"])
        self.assertIn("GGUF", hdr["error"])

    def test_reads_metadata(self):
        p = write_gguf(self.tmp / "m.gguf", arch="llama", kv={"general.file_type": 15})
        hdr = ms.read_header(p)
        self.assertTrue(hdr["ok"], hdr)
        self.assertEqual(hdr["kv"]["general.architecture"], "llama")
        self.assertEqual(hdr["kv"]["general.file_type"], 15)

    def test_detects_truncated_data(self):
        # 声明 1024 个 f32 = 4096 字节，但只写 100 字节
        p = write_gguf(self.tmp / "cut.gguf", arch="llama",
                       tensors=[("blk.0.w", [1024], 0)], data_bytes=100)
        hdr = ms.read_header(p)
        self.assertTrue(hdr["truncated"], hdr)

    def test_complete_file_not_flagged(self):
        p = write_gguf(self.tmp / "ok.gguf", arch="llama",
                       tensors=[("blk.0.w", [256], 0)])
        self.assertFalse(ms.read_header(p)["truncated"])

    def test_empty_file_reports_error(self):
        p = self.tmp / "empty.gguf"
        p.write_bytes(b"")
        hdr = ms.read_header(p)
        self.assertFalse(hdr["ok"])
        self.assertTrue(hdr["error"])


class DetectKindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_kind_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chat_by_architecture(self):
        p = write_gguf(self.tmp / "Qwen3-4B-Q4_K_M.gguf", arch="qwen3")
        d = ms.detect_kind(p)
        self.assertEqual(d["kind"], "chat")
        self.assertEqual(d["source"], "metadata")

    def test_embedding_by_architecture(self):
        p = write_gguf(self.tmp / "whatever.gguf", arch="bert",
                       kv={"bert.pooler_type": "cls"})
        self.assertEqual(ms.detect_kind(p)["kind"], "embedding")

    def test_embedding_by_filename_when_header_bare(self):
        # 元数据里没有类别线索（无 architecture、名字中性）→ 只能靠文件名
        p = write_gguf(self.tmp / "bge-m3-FP16.gguf", kv={"general.name": "unnamed"})
        d = ms.detect_kind(p)
        self.assertEqual(d["kind"], "embedding")
        self.assertEqual(d["source"], "filename")

    def test_mmproj_by_clip_metadata(self):
        p = write_gguf(self.tmp / "proj.gguf", kv={"clip.has_vision_encoder": True})
        self.assertEqual(ms.detect_kind(p)["kind"], "mmproj")

    def test_mmproj_by_filename(self):
        p = write_gguf(self.tmp / "Xiaomi-OCR-0.mmproj-bf16.gguf")
        self.assertEqual(ms.detect_kind(p)["kind"], "mmproj")

    def test_rerank_by_filename(self):
        p = write_gguf(self.tmp / "bge-reranker-v2-m3.gguf")
        self.assertEqual(ms.detect_kind(p)["kind"], "rerank")

    def test_default_is_chat_with_reason(self):
        p = write_gguf(self.tmp / "granite-4.0-h-micro-Q4_K_M.gguf")
        d = ms.detect_kind(p)
        self.assertEqual(d["kind"], "chat")
        self.assertEqual(d["source"], "default")
        self.assertTrue(d["reason"])

    def test_vision_chat_model_stays_chat(self):
        # 带视觉编码器的**主模型**不是 mmproj：有 clip.* 但也有普通 architecture
        p = write_gguf(self.tmp / "Qwen2.5-VL-7B-Q4_K_M.gguf", arch="qwen2vl")
        self.assertEqual(ms.detect_kind(p)["kind"], "chat")


class OrganizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_org_"))
        self.root = self.tmp / "models"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_flags_misplaced(self):
        write_gguf(self.root / "chat-a.gguf", arch="llama")
        write_gguf(self.root / "embed.gguf", arch="bert")
        rep = ms.scan(self.root)
        self.assertEqual(rep["needs_organize"], 2)
        kinds = {m["name"]: m["kind"] for m in rep["models"]}
        self.assertEqual(kinds["chat-a.gguf"], "chat")
        self.assertEqual(kinds["embed.gguf"], "embedding")

    def test_dry_run_does_not_move(self):
        write_gguf(self.root / "chat-a.gguf", arch="llama")
        rep = ms.organize(self.root, apply=False)
        self.assertEqual(rep["moved"], 0)
        self.assertTrue((self.root / "chat-a.gguf").is_file())

    def test_apply_moves_into_kind_dirs(self):
        write_gguf(self.root / "chat-a.gguf", arch="llama")
        write_gguf(self.root / "embed.gguf", arch="bert")
        write_gguf(self.root / "proj.gguf", kv={"clip.has_vision_encoder": True})
        rep = ms.organize(self.root, apply=True)
        self.assertEqual(rep["moved"], 3)
        self.assertTrue((self.root / "chat" / "chat-a.gguf").is_file())
        self.assertTrue((self.root / "embedding" / "embed.gguf").is_file())
        self.assertTrue((self.root / "mmproj" / "proj.gguf").is_file())

    def test_second_pass_is_noop(self):
        write_gguf(self.root / "chat-a.gguf", arch="llama")
        ms.organize(self.root, apply=True)
        again = ms.organize(self.root, apply=True)
        self.assertEqual(again["moved"], 0)
        self.assertEqual(ms.scan(self.root)["needs_organize"], 0)

    def test_conflict_gets_suffix_and_keeps_both(self):
        write_gguf(self.root / "chat" / "m.gguf", arch="llama",
                   kv={"general.name": "already-there"})
        write_gguf(self.root / "m.gguf", arch="llama",
                   kv={"general.name": "newcomer"})
        rep = ms.organize(self.root, apply=True)
        names = sorted(p.name for p in (self.root / "chat").iterdir())
        self.assertEqual(names, ["m-1.gguf", "m.gguf"])
        self.assertEqual(rep["moved"], 1)

    def test_external_files_are_not_touched(self):
        ext = self.tmp / "elsewhere"
        ext.mkdir()
        write_gguf(ext / "outside.gguf", arch="llama")
        rep = ms.scan(self.root, extra_roots=[ext])
        outside = [m for m in rep["models"] if m["name"] == "outside.gguf"][0]
        self.assertFalse(outside["inside"])
        self.assertFalse(outside["needs_move"])
        ms.organize(self.root, apply=True, extra_roots=[ext])
        self.assertTrue((ext / "outside.gguf").is_file())

    def test_mmproj_pairing(self):
        write_gguf(self.root / "chat" / "Qwen2.5-VL-7B-Q4_K_M.gguf", arch="qwen2vl")
        write_gguf(self.root / "mmproj" / "Qwen2.5-VL-7B-mmproj-F16.gguf",
                   kv={"clip.has_vision_encoder": True})
        got = ms.pair_mmproj(self.root / "chat" / "Qwen2.5-VL-7B-Q4_K_M.gguf", self.root)
        self.assertTrue(got.endswith("Qwen2.5-VL-7B-mmproj-F16.gguf"), got)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_safe_"))
        self.root = self.tmp / "models"
        (self.root / "chat").mkdir(parents=True)
        write_gguf(self.root / "chat" / "a.gguf", arch="llama")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_safe_path_blocks_traversal(self):
        with self.assertRaises(ms.ModelStoreError):
            ms.safe_path(self.root, "../../secret.txt")
        with self.assertRaises(ms.ModelStoreError):
            ms.safe_path(self.root, "chat/../../../x")

    def test_safe_path_allows_subdir(self):
        p = ms.safe_path(self.root, "chat/a.gguf")
        self.assertTrue(p.is_file())

    def test_delete_subdir_file(self):
        out = ms.delete(self.root, "chat/a.gguf")
        self.assertTrue(out["ok"])
        self.assertFalse((self.root / "chat" / "a.gguf").exists())

    def test_delete_missing_raises(self):
        with self.assertRaises(ms.ModelStoreError):
            ms.delete(self.root, "chat/nope.gguf")

    def test_find_file_recursive(self):
        hits = ms.find_file(self.root, "a.gguf")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], "chat/a.gguf")


class AutodiscoverTests(unittest.TestCase):
    def test_picks_chat_not_embedding(self):
        from gantry import providers as pv

        tmp = Path(tempfile.mkdtemp(prefix="cbp_auto_"))
        old_root = ROOT / "data" / "models"
        try:
            # 用临时目录冒充仓库 data/models：monkeypatch 模块内的 root 计算
            models = tmp / "data" / "models"
            (models / "embedding").mkdir(parents=True)
            (models / "chat").mkdir(parents=True)
            write_gguf(models / "embedding" / "aaa-bge.gguf", arch="bert")
            write_gguf(models / "chat" / "zzz-chat.gguf", arch="llama")
            fake = tmp / "gantry"
            fake.mkdir()
            (fake / "providers.py").write_text("", encoding="utf-8")

            merged: dict = {}
            import importlib.util
            if importlib.util.find_spec("llama_cpp") is None:
                self.skipTest("未安装 llama-cpp-python")
            # 直接调用内部实现（它按 __file__ 定位仓库根，测试里替换该函数）
            orig = pv.__file__
            try:
                pv.__file__ = str(fake / "providers.py")
                pv._autodiscover_local(merged)
            finally:
                pv.__file__ = orig
            self.assertIn("local", merged)
            self.assertTrue(merged["local"]["model_path"].endswith("zzz-chat.gguf"))
            self.assertIn("chat", merged["local"]["model_path"].replace("\\", "/"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
