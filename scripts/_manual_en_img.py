# -*- coding: utf-8 -*-
"""补英文 quickstart 的配图（锚点是 'on the left: type the task' 那版文案）。"""
from pathlib import Path

p = Path("webui/js/control.js")
s = p.read_text(encoding="utf-8")
old = "<h3>Quick start</h3>\n<ul>\n<li><b>New task</b> on the left:"
new = '''<h3>Quick start</h3>
<figure>
<img src="img/quickstart-main.png" alt="Main screen: session list on the left, task box in the middle">
<figcaption>Main screen: session list on the left, task box in the middle. <b>Agent run</b> can read/write workspace files and verify its work; <b>Plain chat</b> only answers.</figcaption>
</figure>
<ul>
<li><b>New task</b> on the left:'''
assert old in s
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("英文 quickstart 配图完成")
