# -*- coding: utf-8 -*-
"""分析 Teams 页面 dump：找出真实的 data-tid / role 结构，用来校准选择器。"""
import re
from collections import Counter
from pathlib import Path

f = sorted(Path("data/teams-probe").glob("teams-*.html"))[-1]
html = f.read_text(encoding="utf-8", errors="replace")
print("文件:", f.name, "大小:", len(html))

tids = Counter(re.findall(r'data-tid="([^"]+)"', html))
print("\n=== data-tid（出现次数最多的 40 个）")
for k, v in tids.most_common(40):
    print(f"  {v:5d}  {k}")

roles = Counter(re.findall(r'\brole="([^"]+)"', html))
print("\n=== role")
for k, v in roles.most_common(15):
    print(f"  {v:5d}  {k}")

print("\n=== 含 'chat' 的 data-tid")
for k in sorted({k for k in tids if "chat" in k.lower()})[:30]:
    print("  ", k, tids[k])

print("\n=== contenteditable / textbox")
for m in re.finditer(r'<[^>]*contenteditable[^>]*>', html):
    print("  ", m.group(0)[:200])
for m in re.finditer(r'<[^>]*role="textbox"[^>]*>', html):
    print("  ", m.group(0)[:200])

print("\n=== 标题/未读相关 aria-label")
labels = Counter(re.findall(r'aria-label="([^"]{2,60})"', html))
for k, v in labels.most_common(25):
    print(f"  {v:4d}  {k}")
