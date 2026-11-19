# -*- coding: utf-8 -*-
"""Agent 的领域 profile：按任务自动注入一小段针对性规则，并展开相关工具组。

不是完整 skill：这里只做**路由和硬约束**，具体流程放在 skills/*/SKILL.md，
避免每个任务的系统提示词被大段教程淹没。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import prompt_lang


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    keywords: tuple[str, ...]
    groups: tuple[str, ...]
    zh: str
    en: str


PROFILES: tuple[Profile, ...] = (
    Profile(
        "power_automate", "Power Automate",
        ("power automate", "powerautomate", "云端流", "自动化流", "审批流",
         "流程自动化", "pa 流", "make.powerautomate", "flow 自动化"),
        ("power_automate",),
        """【本任务按 Power Automate 方向执行】
- 先读 skill power-automate；优先用 power_automate_generate 生成 definition/legacy/solution 包，再让用户导入；不要假装能直接调 Power Automate API。
- UI 操作只用于登录、验证、一次性导入或官方未开放的能力；点击前先 snapshot，不要盲点。
- 生成后必须 power_automate_validate 校验；交付时说明触发方式、连接器、运行前置条件和导入步骤。
- 涉及删除/发布/覆盖线上流时先停下来向用户确认，不要自行执行不可回滚操作。""",
        """【This task uses the Power Automate profile】
- Read skill power-automate first; prefer power_automate_generate for definition/legacy/solution packages, then let the user import. Do not pretend to call the Power Automate API.
- Use UI actions only for sign-in, verification, one-off imports, or capabilities not exposed officially; snapshot before clicking.
- Always validate generated packages with power_automate_validate; report triggers, connectors, prerequisites, and import steps.
- Ask before deleting, publishing, or overwriting live flows; never perform an irreversible action on your own.""",
    ),
    Profile(
        "power_apps", "Power Apps",
        ("power apps", "powerapps", "canvas app", "画布应用", "模型驱动",
         "papp", "make.powerapps", "canvas 应用"),
        (),
        """【本任务按 Power Apps 方向执行】
- 先读 skill power-apps；可复现的 app 优先用 power_apps_generate 生成 YAML source，再用 pac/Studio 导入，不要一上来点模板。
- UI 只用于登录、验证、一次性导入；Playwright 失败不要重复盲点，先 snapshot/dom 定位。
- 交付前 power_apps_validate；说明数据源、连接、环境、发布步骤和 pac 的限制（新 source 不能直接 pack，需要 baseline）。
- 不要未经确认创建/删除租户里的 app，测试对象必须用唯一 gantry-e2e-* 名称并在结束后清理。""",
        """【This task uses the Power Apps profile】
- Read skill power-apps first; prefer power_apps_generate YAML source for reproducible apps, then import via pac/Studio instead of clicking templates blindly.
- Use UI only for sign-in, verification, or one-off imports; on Playwright failures, inspect snapshot/DOM before retrying.
- Run power_apps_validate; report data sources, connections, environment, publishing steps, and the pac baseline limitation.
- Do not create/delete tenant apps without confirmation; use unique gantry-e2e-* names and clean up.""",
    ),
    Profile(
        "office365", "Office 365",
        ("office 365", "office365", "m365", "excel", "xlsx", "表格", "word",
         "docx", "文档", "ppt", "pptx", "幻灯片", "pdf", "outlook", "邮件",
         "teams", "sharepoint", "onedrive", "报表"),
        ("office", "outlook"),
        """【本任务按 Office 365 方向执行】
- 先读 skill office365；先读原文件再改，不要凭标题猜内容；保留原有格式、公式、样式、页眉页脚和表格结构。
- Excel/Word/PPT/PDF 优先用本地 office 工具；写完后重新打开/读取结果验证，不只说“已生成”。
- Outlook 默认走 OWA 自动化；发信是高风险操作，必须确认收件人/主题/正文，且只在用户明确要求时发送。
- 引用文件用相对路径和实际范围；不确定的数据不要编造，先读源文件。""",
        """【This task uses the Office 365 profile】
- Read skill office365 first; inspect the original file before editing and preserve formatting, formulas, styles, headers/footers, and table structure.
- Prefer local Office tools for Excel/Word/PPT/PDF; reopen/read the result to verify instead of merely claiming it was generated.
- Outlook uses OWA automation by default; sending is high-risk—confirm recipients/subject/body and send only when explicitly requested.
- Cite relative paths and actual ranges; never invent data—read the source first.""",
    ),
    Profile(
        "coding", "Coding",
        ("编程", "代码", "python", "重构", "实现", "修复", "bug", "测试",
         "脚本", "函数", "接口", "项目", "运行测试", "unit test", "pytest",
         "typescript", "javascript", "java", "c#"),
        ("python", "notebook"),
        """【本任务按编程方向执行】
- 先读 skill coding；修改已有代码前先 read_text/find_files 定位，不要凭记忆重写整文件；优先 apply_patch/replace_text 做最小改动。
- 写完必须 validate_python；有测试目录时 run_unittest，失败要定位并修复，未通过不得宣称完成。
- 不做与任务无关的重构、格式化或依赖升级；新增依赖前说明理由。
- 交付时给出改了哪些文件/行为、验证命令与结果、剩余风险。""",
        """【This task uses the coding profile】
- Read skill coding first; locate existing code with read_text/find_files before editing, prefer minimal apply_patch/replace_text changes over rewriting whole files.
- Run validate_python after edits; run_unittest when a tests directory exists. Fix failures; do not claim completion while tests fail.
- Do not perform unrelated refactors, formatting, or dependency upgrades; justify any new dependency.
- Report changed files/behavior, verification commands/results, and remaining risks.""",
    ),
    Profile(
        "visual", "Visual design",
        ("海报", "封面", "宣传图", "活动图", "节日图", "图片处理", "修图",
         "抠图", "gimp", "banner", "poster", "logo", "社交媒体图"),
        (),
        """【本任务按视觉设计方向执行】
- 先读 skill poster-design 和 gimp；先定画布/安全边距/信息层级，再动 GIMP。
- 不要用 GIMP 椭圆/折线/贝塞尔基础形状拼人物或复杂插画；需要插画先确认 image_generate 可用或使用用户素材/SVG。生成服务 401/未配置时停下来说明，不要用粗糙几何图假装完成。
- 文字每个角色只建一个图层；反复重试前先 inspect 现有图层，禁止叠加重复文字。
- 导出前 preview 检查重叠/错位/截断/残留选区；确认无选区、无参考线、无文本框边线。
- 交付 PNG + 可编辑源文件；说明画布、字体、素材来源和导出路径。""",
        """【This task uses the visual design profile】
- Read skill poster-design and gimp first; define canvas/margins/hierarchy before touching GIMP.
- Do not construct people or complex illustrations from GIMP ellipses/lines/Bézier primitives; use an available image generator, user assets, or SVG/HTML. If image generation is unconfigured/401, stop and say so; do not substitute crude geometry.
- Create exactly one text layer per role; inspect existing layers before retrying and never stack duplicate text.
- Before export, preview for overlap/misalignment/truncation/selection artifacts; confirm no selection, guides, or text-box outlines.
- Deliver PNG plus editable source; report canvas, fonts, asset source, and export path.""",
    ),
)


def _score(task: str, p: Profile) -> int:
    text = (task or "").lower()
    score = 0
    for kw in p.keywords:
        if kw in text:
            score += max(1, len(kw) // 3)
    return score


def profile_for_task(task: str, config: dict[str, Any] | None = None) -> Profile | None:
    """显式配置优先；否则按关键词命中数选一个；无命中返回 None。"""
    forced = str((config or {}).get("profile") or "auto").strip().lower()
    if forced in ("off", "none", "disabled", "false", "0"):
        return None
    by_name = {p.name: p for p in PROFILES}
    if forced and forced != "auto":
        return by_name.get(forced)
    scored = [( _score(task, p), p) for p in PROFILES]
    scored = [(s, p) for s, p in scored if s > 0]
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], PROFILES.index(x[1])))
    return scored[0][1]


def profile_prompt(p: Profile | None, lang: str = "") -> str:
    if p is None:
        return ""
    target = lang or prompt_lang.request_lang()
    if target == "en":
        return p.en
    if target == "tw":
        return prompt_lang.to_tw(p.zh)
    return p.zh


def profile_groups(p: Profile | None) -> tuple[str, ...]:
    return p.groups if p else ()
