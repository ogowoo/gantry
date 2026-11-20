#!/usr/bin/env python3
"""国家法律法规数据库（flk.npc.gov.cn）下载脚本。

只使用官方站点：
- 搜索：/law-search/search/list
- 详情：/law-search/search/flfgDetails
- 下载：/law-search/download/pc（返回官方 OSS 的签名 PDF URL）

站点的部分接口有 WAF/JS 壳，直接用 httpx 会被拦截；本脚本用独立的
Playwright Chromium 打开官网，再在页面上下文里 fetch JSON，拿到公开的
签名下载 URL 后用 httpx 下载 PDF。不操作 Copilot 页面，不执行模型生成的
任意 PowerShell。

示例：
  python scripts/law_download.py --query 民法典 --limit 1
  python scripts/law_download.py --query "" --limit 20 --pages 1
  python scripts/law_download.py --query 民法典 --scope shenzhen --limit 5
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "https://flk.npc.gov.cn"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")
MAX_PDF_BYTES = 100 * 1024 * 1024


def _safe_name(text: str, limit: int = 90) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text or "")).strip(" .")
    return (text or "law")[:limit]


def _listed_rows(page, body: dict[str, Any]) -> list[dict[str, Any]]:
    data = page.evaluate("""async (body) => {
      const r = await fetch('/law-search/search/list', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      return await r.json();
    }""", body)
    if int(data.get("code") or 0) != 200:
        raise RuntimeError(f"搜索结果异常: {str(data)[:400]}")
    return list(data.get("rows") or [])


def _detail(page, bbbs: str) -> dict[str, Any]:
    data = page.evaluate("""async (bbbs) => {
      const u = '/law-search/search/flfgDetails?bbbs=' + encodeURIComponent(bbbs);
      const r = await fetch(u, {headers: {'Accept':'application/json'}});
      return {status: r.status, body: await r.json()};
    }""", bbbs)
    body = data.get("body") or {}
    if data.get("status") != 200 or int(body.get("code") or 0) != 200:
        raise RuntimeError(f"详情接口失败 {bbbs}: {str(body)[:400]}")
    return dict(body.get("data") or {})


def _download_url(page, bbbs: str, fmt: str = "pdf") -> str:
    data = page.evaluate("""async ({bbbs, fmt}) => {
      const u = '/law-search/download/pc?' + new URLSearchParams({
        format: fmt, bbbs: bbbs, fileId: ''
      });
      const r = await fetch(u);
      return await r.json();
    }""", {"bbbs": bbbs, "fmt": fmt})
    if int(data.get("code") or 0) != 200:
        raise RuntimeError(f"下载链接失败 {bbbs}: {str(data)[:400]}")
    url = str((data.get("data") or {}).get("url") or "")
    if not url.startswith("https://"):
        raise RuntimeError(f"下载链接不可用: {url[:200]}")
    return url


def download_law(page, row: dict[str, Any], out_dir: Path, fmt: str = "pdf") -> dict[str, Any]:
    bbbs = str(row.get("bbbs") or "").strip()
    if not bbbs:
        raise RuntimeError("搜索结果缺少 bbbs")
    detail = _detail(page, bbbs)
    title = str(detail.get("title") or row.get("title") or bbbs)
    title = re.sub(r"<[^>]+>", "", title)
    date = str(detail.get("gbrq") or row.get("gbrq") or "")
    oss = detail.get("ossFile") or {}
    if fmt == "pdf" and not str(oss.get("ossPdfPath") or "").strip():
        raise RuntimeError("该法规没有可下载的 PDF 附件")
    if fmt == "docx" and not str(oss.get("ossWordPath") or "").strip():
        raise RuntimeError("该法规没有可下载的 Word 附件")
    meta = {
        "bbbs": bbbs,
        "title": title,
        "gbrq": date,
        "sxrq": detail.get("sxrq") or row.get("sxrq"),
        "sxx": detail.get("sxx") or row.get("sxx"),
        "flxz": detail.get("flxz") or row.get("flxz"),
        "zdjg_name": detail.get("zdjgName") or row.get("zdjgName"),
        "source": f"{BASE}/detail?bbbs={bbbs}",
        "ossFile": oss,
        "format": fmt,
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(f"{title}_{date}".strip("_"))
    target = out_dir / f"{stem}.{fmt}"
    meta_path = out_dir / f"{stem}.meta.json"
    url = _download_url(page, bbbs, fmt)
    with httpx.stream("GET", url, timeout=120, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}) as response:
        response.raise_for_status()
        total = 0
        tmp = target.with_suffix(target.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError(f"{title}: 文件超过 100 MB")
                fh.write(chunk)
        tmp.replace(target)
    if fmt == "pdf":
        with target.open("rb") as fh:
            if fh.read(5) != b"%PDF-":
                target.unlink(missing_ok=True)
                raise RuntimeError(f"{title}: 下载内容不是 PDF")
    meta.update({"file": target.name, "bytes": target.stat().st_size})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="下载国家法律法规数据库 PDF")
    ap.add_argument("--query", default="", help="标题关键词；空串表示按分类列表")
    ap.add_argument("--limit", type=int, default=10, help="最多下载几条（默认 10）")
    ap.add_argument("--pages", type=int, default=1, help="最多搜索页数（默认 1）")
    ap.add_argument("--page-size", type=int, default=20)
    ap.add_argument("--out", default="workspace/LAW", help="输出根目录")
    ap.add_argument("--scope", choices=("national", "shenzhen", "all"),
                    default="national", help="national 过滤地方法规")
    ap.add_argument("--format", choices=("pdf", "docx"), default="pdf")
    ap.add_argument("--effective", action="store_true", default=True,
                    help="只取现行有效（默认开启）")
    ap.add_argument("--include-pending", action="store_true",
                    help="包含尚未生效的法规")
    args = ap.parse_args()

    out_root = Path(args.out).resolve()
    target_dir = out_root / "laws" / (args.scope if args.scope != "all" else "all")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    with __import__("playwright.sync_api", fromlist=["sync_playwright"]).sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900},
                                user_agent=USER_AGENT)
        page.goto(BASE + "/", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(800)
        for page_no in range(1, max(1, args.pages) + 1):
            body = {
                "searchRange": 1, "sxrq": [], "gbrq": [], "searchType": 2,
                "sxx": [] if args.include_pending else [3],
                "gbrqYear": [], "flfgCodeId": [], "zdjgCodeId": [],
                "searchContent": (args.query or ("深圳" if args.scope == "shenzhen" else "")),
                "orderByParam": {"order": "-1", "sort": ""},
                "pageNum": page_no, "pageSize": max(1, min(100, args.page_size)),
            }
            rows = _listed_rows(page, body)
            for row in rows:
                if len(records) >= args.limit:
                    break
                bbbs = str(row.get("bbbs") or "")
                if not bbbs or bbbs in seen:
                    continue
                flxz = str(row.get("flxz") or "")
                if args.scope == "national" and flxz == "地方法规":
                    continue
                if args.scope == "shenzhen":
                    hay = f"{row.get('title') or ''} {row.get('zdjgName') or ''}"
                    if "深圳" not in hay:
                        continue
                seen.add(bbbs)
                try:
                    rec = download_law(page, row, target_dir, args.format)
                    records.append(rec)
                    print(f"[{len(records)}/{args.limit}] {rec['title']} "
                          f"({rec['bytes']} bytes) -> {rec['file']}")
                except Exception as exc:
                    print(f"[skip] {row.get('title') or bbbs}: {exc}")
            if len(records) >= args.limit:
                break
        browser.close()

    index = out_root / "laws" / "index.json"
    old = []
    if index.is_file():
        try:
            old = json.loads(index.read_text(encoding="utf-8")).get("records") or []
        except Exception:
            old = []
    old.extend(records)
    index.write_text(json.dumps({"source": BASE, "records": old},
                                ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    print(f"downloaded {len(records)}; index {index}")
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
