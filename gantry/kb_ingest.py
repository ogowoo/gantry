"""知识库文档导入：把 Word / PDF / Excel / PPT / HTML / 纯文本 / URL 变成 `data/kb/**/*.md`。

## 为什么先转 Markdown 再入库

`gantry/kb.py` 的切块是按 `#`/`##` 标题切的，导入时统一排版成
「front-matter + 一级标题 + 按原文段落/标题」，切块才均匀，检索命中才有小节名可引用。

## 支持的格式

| 扩展名 | 提取方式 |
| --- | --- |
| `.md` `.txt` `.csv` `.log` `.json` | 直接按 UTF-8/GBK 解码 |
| `.html` `.htm` | BeautifulSoup 去 script/style 取正文 |
| `.docx` | python-docx：段落 + 表格单元格 |
| `.xlsx` `.xlsm` | openpyxl：逐 sheet 逐行（制表符分隔） |
| `.pptx` | python-pptx：逐页逐形状的文本 |
| `.pdf` | pypdf：逐页文本（扫描件无文本层，会明确报"没有可提取文本"） |

URL 用 urllib 抓取（带 UA、超时），再用 BeautifulSoup 取正文；标题优先 `<title>`。

## 落盘约定

`data/kb/imported/<slug>.md`，front-matter 带 `title/category/source/updated`；
同名（同 slug）文件按来源覆盖，避免重复导入产生多份副本。
"""
from __future__ import annotations

import datetime as _dt
import re
import urllib.request
from pathlib import Path
from typing import Any

MAX_TEXT_CHARS = 2_000_000          # 单文档上限（约 200 万字符，足够放一本手册）
URL_TIMEOUT = 30.0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TEXT_EXTS = {".md", ".markdown", ".txt", ".csv", ".tsv", ".log", ".json", ".yaml", ".yml"}
HTML_EXTS = {".html", ".htm", ".xhtml"}


class IngestError(RuntimeError):
    """导入失败（格式不支持 / 文件损坏 / 抓取失败），消息直接给人看。"""


# ── 文本提取 ───────────────────────────────────────────────────────────────
def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _html_to_text(html: str) -> tuple[str, str]:
    """返回 (标题, 正文)。没有 bs4 时退化为去标签。"""
    try:
        from bs4 import BeautifulSoup
    except Exception:                            # noqa: BLE001
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        return (title.group(1).strip() if title else "",
                re.sub(r"[ \t]+", " ", text).strip())
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "")
    blocks = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th", "pre"]):
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            blocks.append("\n" + "#" * int(el.name[1]) + " " + txt)
        elif el.name == "li":
            blocks.append("- " + txt)
        else:
            blocks.append(txt)
    body = "\n".join(blocks) if blocks else soup.get_text("\n", strip=True)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return title, body


def _docx_text(data: bytes) -> str:
    import io

    from docx import Document

    doc = Document(io.BytesIO(data))
    out: list[str] = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if not t:
            continue
        style = (getattr(p.style, "name", "") or "").lower()
        if style.startswith("heading"):
            digits = re.sub(r"\D", "", style) or "2"
            out.append("#" * min(6, max(1, int(digits))) + " " + t)
        else:
            out.append(t)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                out.append(" | ".join(cells))
    return "\n\n".join(out)


def _xlsx_text(data: bytes) -> str:
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[str] = []
    for ws in wb.worksheets:
        out.append(f"## {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c) for c in row]
            if any(x.strip() for x in cells):
                out.append("\t".join(cells))
        out.append("")
    wb.close()
    return "\n".join(out)


def _pptx_text(data: bytes) -> str:
    import io

    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    out: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        out.append(f"## 第 {i} 页")
        for shape in slide.shapes:
            text = getattr(shape, "text", "") or ""
            for line in text.splitlines():
                line = line.strip()
                if line:
                    out.append(line)
        out.append("")
    return "\n".join(out)


def _pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    out: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:                        # noqa: BLE001
            text = ""
        if text.strip():
            out.append(f"## 第 {i} 页")
            out.append(text.strip())
    if not out:
        raise IngestError("这个 PDF 没有可提取的文本层（多半是扫描件）："
                          "先用 OCR 转成文本，或导出成 Word 再导入")
    return "\n\n".join(out)


def extract_text(name: str, data: bytes) -> str:
    """按扩展名提取正文（Markdown 风格）。不支持的格式给出可行动错误。"""
    ext = Path(str(name or "")).suffix.lower()
    if not data:
        raise IngestError("文件内容为空")
    if len(data) > 80 * 1024 * 1024:
        raise IngestError("文件超过 80 MB，请拆分后再导入")
    try:
        if ext in TEXT_EXTS:
            text = _decode(data)
        elif ext in HTML_EXTS:
            text = _html_to_text(_decode(data))[1]
        elif ext == ".docx":
            text = _docx_text(data)
        elif ext in (".xlsx", ".xlsm"):
            text = _xlsx_text(data)
        elif ext == ".pptx":
            text = _pptx_text(data)
        elif ext == ".pdf":
            text = _pdf_text(data)
        else:
            raise IngestError(
                f"不支持的格式 {ext or '(无扩展名)'}：支持 "
                f"{sorted(TEXT_EXTS | HTML_EXTS | {'.docx', '.xlsx', '.pptx', '.pdf'})}；"
                "老格式 .doc/.xls/.ppt 请先另存为 .docx/.xlsx/.pptx")
    except IngestError:
        raise
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"解析失败（{ext}）：{str(exc)[:200]}") from exc
    text = (text or "").strip()
    if not text:
        raise IngestError(f"{ext} 里没有提取到文本（可能是空文档或图片扫描件）")
    return text[:MAX_TEXT_CHARS]


def fetch_url(url: str) -> tuple[str, str]:
    """抓网页并转 Markdown 文本：返回 (标题, 正文)。"""
    u = str(url or "").strip()
    if not re.match(r"^https?://", u, re.I):
        raise IngestError("URL 必须以 http:// 或 https:// 开头")
    req = urllib.request.Request(u, headers={"User-Agent": UA,
                                             "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=URL_TIMEOUT) as resp:
            raw = resp.read(8 * 1024 * 1024)
            ctype = str(resp.headers.get("content-type") or "")
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"抓取失败：{str(exc)[:200]}") from exc
    if "pdf" in ctype.lower():
        return Path(u).stem, _pdf_text(raw)
    html = _decode(raw)
    title, text = _html_to_text(html)
    if not text.strip():
        raise IngestError("网页没有提取到正文（可能需要登录或者是纯 JS 渲染页面）")
    return title or Path(u).stem, text[:MAX_TEXT_CHARS]


# ── 落盘 ───────────────────────────────────────────────────────────────────
def slug(text: str, fallback: str = "document") -> str:
    """文件名安全化：保留中英文数字，其它转 -。"""
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", str(text or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-._")
    if not s:
        return fallback
    return s[:80]


def _front_matter(title: str, source: str, category: str) -> str:
    today = _dt.date.today().isoformat()
    safe = str(title or "").replace("\n", " ").strip()
    return ("---\n"
            f"title: {safe}\n"
            f"category: {category}\n"
            f"source: {source}\n"
            f"updated: {today}\n"
            "---\n\n")


def save_document(kb_dir: Path, name: str, text: str, *, title: str = "",
                  source: str = "", category: str = "imported",
                  overwrite: bool = True) -> Path:
    """把正文写成 `kb_dir/imported/<slug>.md`；同名按来源覆盖（幂等导入）。"""
    base = Path(kb_dir) / "imported"
    base.mkdir(parents=True, exist_ok=True)
    title = (title or Path(str(name or "")).stem or "未命名文档").strip()
    path = base / (slug(title) + ".md")
    if path.exists() and not overwrite:
        stem, i = path.stem, 1
        while path.exists():
            i += 1
            path = base / f"{stem}-{i}.md"
    body = _front_matter(title, source or str(name or ""), category)
    body += f"# {title}\n\n" + str(text or "").strip() + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def ingest_bytes(kb_dir: Path, name: str, data: bytes, *,
                 title: str = "", source: str = "") -> dict[str, Any]:
    """一步到位：提取 → 落盘 → 返回摘要（是否重建索引由调用方决定）。"""
    text = extract_text(name, data)
    path = save_document(kb_dir, name, text, title=title,
                         source=source or str(name or ""))
    return {"path": str(path), "name": Path(str(name or "")).name,
            "title": (title or Path(str(name or "")).stem), "chars": len(text)}


def ingest_url(kb_dir: Path, url: str) -> dict[str, Any]:
    title, text = fetch_url(url)
    path = save_document(kb_dir, url, text, title=title, source=url, category="web")
    return {"path": str(path), "name": title, "title": title,
            "chars": len(text), "source": url}
