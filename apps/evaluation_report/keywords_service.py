"""Keywords 通用 schema 与项目取值维护。

个性化内容保存在 EvaluationReportKeyword；通用 LaTeX 模板只用 \\newcommand / \\input 引用。
工作区 main.tex 的 % <<PROJECT_VARS>> 块，以及 files/*.tex 长文，由本模块写入。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from django.conf import settings

from converter.project_vars import (
    COMPOSED_COMMANDS,
    FILE_BACKED_COMMANDS,
    VARS_END,
    VARS_START,
    extract_newcommand_definitions,
    extract_project_values,
    load_keywords,
    patch_main_tex,
    render_newcommand_block,
)

from .models import EvaluationKeywordSchema, EvaluationReport, EvaluationReportKeyword


def normalize_tabular_blank_lines(text: str) -> str:
    """Remove blank lines inside tabular/longtable (they break m/p column preambles)."""
    if not text:
        return text

    def _collapse(m: re.Match[str]) -> str:
        return re.sub(r"\n[ \t]*\n+", "\n", m.group(0))

    return re.sub(
        r"\\begin\{(tabular\*?|longtable)\}.*?\\end\{\1\}",
        _collapse,
        text,
        flags=re.S,
    )


# 旧版 main.tex 宏名 → schema command
_MAIN_ALIASES = {
    "legalrepresentative": "buildunitlegalrep",
    "contactperson": "buildunitcontact",
    "contactphone": "buildunitphone",
    "natureofproject": "projectnature",
}

# 默认长文文件名（相对 converter/data/default_texts/）
_DEFAULT_TEXT_FILES: dict[str, str] = {
    "unitprofile": "unitProfile.tex",
    "projectintro": "projectIntro.tex",
    "basicmaterials": "basicmaterials.tex",
    "stafflist": "staffList.tex",
    "staffconfig": "staffConfig.tex",
    "staffduties": "staffDuties.tex",
    "surroundings": "surroundings.tex",
    "backgroundradiation": "backgroundradiation.tex",
    "protectionlayout": "protectionlayout.tex",
    "workloadrows": "workloadRows.tex",
    "sourcesummary": "sourcesummary.txt",
    "sourceoverview": "sourceoverview.tex",
    "tab412": "tab412.tex",
    "tab414": "tab414.tex",
    "fig41images": "fig41images.tex",
    "tab421": "tab421.tex",
    "fig42points": "fig42points.tex",
    "workload4222": "workload4222.tex",
    "tab423": "tab423.tex",
    "figsafetypos": "figSafetyPos.tex",
    "figvent": "figVent.tex",
    "figcable": "figCable.tex",
    "emergencyorg": "emergencyOrg.tex",
    "protectionorg": "protectionOrg.tex",
    "healthstaffrows": "healthStaffRows.tex",
}

LONG_TEXT_COMMANDS: frozenset[str] = frozenset(FILE_BACKED_COMMANDS)

# 编辑期不做「未填」提醒：签发日期通常定稿时再写，不必在正文编辑时催填
SKIP_UNFILLED_REMINDER_COMMANDS: frozenset[str] = frozenset(
    {"signyear", "signmonth", "signday"}
)

# Used as ``\input{files/...}`` *inside* ``tabular`` — trailing ``%`` eats the
# endline so TeX does not open a phantom empty last row (dangling vertical rules).
TABLE_ROW_BODY_COMMANDS: frozenset[str] = frozenset(
    {"stafflist", "staffconfig", "staffduties", "workloadrows", "healthstaffrows"}
)

# 封面编辑页首批个性化字段
COVER_COMMANDS: tuple[str, ...] = (
    "reportno",
    "reportver",
    "buildunit",
    "project",
    "radiationhazard",
    "reportkind",
    "mitnote",
    "company",
    "printdate",
)

# 项目信息页分组：与结构化编辑侧栏栏目对齐（category → 展示名 / 编辑页 key）
KEYWORD_SECTION_ORDER: tuple[str, ...] = (
    "cover",
    "declaration",
    "chapter1",
    "chapter2",
    "chapter3",
    "chapter4",
    "chapter7",
    "chapter8",
    "shared",
)

KEYWORD_SECTION_META: dict[str, dict[str, str]] = {
    "cover": {
        "title": "封面",
        "group": "前置",
        "chapter_key": "cover",
        "hint": "对应编辑页「封面」：编号、标题、单位与日期等",
    },
    "declaration": {
        "title": "声明",
        "group": "前置",
        "chapter_key": "declaration",
        "hint": "对应编辑页「声明」：人员、证书编号、评价单位联系方式等",
    },
    "chapter1": {
        "title": "第1章 概述",
        "group": "正文",
        "chapter_key": "ch1",
        "hint": "建设单位概况、项目背景、辐射源表与基础资料等",
    },
    "chapter2": {
        "title": "第2章 工程分析",
        "group": "正文",
        "chapter_key": "ch2",
        "hint": "概况表、人员表、环境、工作量与小结等",
    },
    "chapter3": {
        "title": "第3章 辐射源项",
        "group": "正文",
        "chapter_key": "ch3",
        "hint": "辐射源项概况等",
    },
    "chapter4": {
        "title": "第4章 防护措施",
        "group": "正文",
        "chapter_key": "ch4",
        "hint": "屏蔽、关注点、工作量与剂量率控制等表图",
    },
    "chapter7": {
        "title": "第7章 应急",
        "group": "正文",
        "chapter_key": "ch7",
        "hint": "应急组织名单、当地主管部门电话等",
    },
    "chapter8": {
        "title": "第8章 管理",
        "group": "正文",
        "chapter_key": "ch8",
        "hint": "放射防护管理组织名单、职业人员健康管理一览表等",
    },
    "shared": {
        "title": "报告全局",
        "group": "共用",
        "chapter_key": "",
        "hint": "跨页引用或由其他字段组合生成的全局字段",
    },
}


def default_schema_csv_path() -> Path:
    return Path(settings.CONVERTER_KEYWORDS_CSV)


def default_texts_dir() -> Path:
    return Path(settings.BASE_DIR) / "converter" / "data" / "default_texts"


def load_default_text(command: str) -> str:
    name = _DEFAULT_TEXT_FILES.get(command)
    if not name:
        return ""
    path = default_texts_dir() / name
    if not path.is_file():
        return ""
    return _strip_tex_comments(path.read_text(encoding="utf-8")).strip()


def _strip_tex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("%"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def seed_schema_from_csv(csv_path: Path | None = None) -> int:
    """从 project_keywords.csv 导入 command ↔ label_cn（通用映射）。"""
    path = csv_path or default_schema_csv_path()
    if not path.is_file():
        return 0
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            command = (row.get("command") or "").strip()
            label_cn = (row.get("label_cn") or "").strip()
            if not command or not label_cn:
                continue
            _, created = EvaluationKeywordSchema.objects.update_or_create(
                command=command,
                defaults={"label_cn": label_cn},
            )
            if created:
                count += 1
    for command, label in (
        ("reportdate", "报告日期"),
        ("reportname", "报告全称"),
        ("unitprofile", "建设单位概况"),
        ("projectintro", "项目建设背景"),
        ("sourcetype", "辐射源类型"),
        ("sourcedevice", "辐射源设备"),
        ("sourceremark", "辐射源备注"),
        ("basicmaterials", "基础资料（1.5.3）"),
        ("developmentplan", "发展规划"),
        ("stafflist", "表2.2-1现有放射工作人员"),
        ("staffconfig", "表2.2-2人员配备一览"),
        ("staffduties", "表2.2-3岗位职责"),
        ("surroundings", "周围环境"),
        ("backgroundradiation", "环境本底辐射水平"),
        ("protectionlayout", "防护设施布置（2.5.3）"),
        ("workloadrows", "表2.6-1预期运行工作量"),
        ("sourcesummary", "小结辐射源简述"),
        ("sourceoverview", "3.1辐射源项概况"),
        ("tab412", "表4.1-2周围环境一览"),
        ("tab414", "表4.1-4机房尺寸"),
        ("fig41images", "4.1附图"),
        ("tab421", "表4.2-1屏蔽设计"),
        ("fig42points", "图4.2关注点选取"),
        ("workload4222", "4.2.2.2后装治疗工作量"),
        ("tab423", "表4.2-3剂量率控制水平"),
        ("figsafetypos", "防护安全措施拟安装位置图"),
        ("figvent", "后装治疗机机房通风布局图"),
        ("figcable", "电缆沟风管穿墙示意图"),
        ("emergencyorg", "第7章应急组织名单"),
        ("localenvphone", "当地生态环境主管部门电话"),
        ("localhealthphone", "当地卫生健康主管部门电话"),
        ("protectionorg", "第8章放射防护管理组织名单"),
        ("healthstaffrows", "表8.3-2职业人员健康管理"),
    ):
        EvaluationKeywordSchema.objects.get_or_create(
            command=command,
            defaults={"label_cn": label},
        )
    return count


def schema_rows() -> list[EvaluationKeywordSchema]:
    return list(EvaluationKeywordSchema.objects.order_by("command"))


def normalize_input_rel_key(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if rel.endswith(".tex"):
        rel = rel[:-4]
    return rel


def personalized_file_label_map() -> dict[str, dict[str, str]]:
    """``files/xxx`` → {command, label}，供结构化编辑页标注个性化块。"""
    labels = {s.command: s.label_cn for s in schema_rows()}
    out: dict[str, dict[str, str]] = {}
    for cmd, rel in FILE_BACKED_COMMANDS.items():
        meta = {"command": cmd, "label": labels.get(cmd) or cmd}
        key = normalize_input_rel_key(rel)
        out[key] = meta
        # 兼容带/不带 files/、带扩展名
        out[rel.replace("\\", "/")] = meta
        bare = key.split("/")[-1]
        if bare and bare not in out:
            out[bare] = meta
            out[f"files/{bare}"] = meta
    return out


def project_keyword_map(report: EvaluationReport) -> dict[str, str]:
    return {
        row.command: row.value
        for row in report.project_keywords.all()
    }


def ensure_project_keyword_rows(report: EvaluationReport) -> list[EvaluationReportKeyword]:
    if not schema_rows():
        seed_schema_from_csv()
    # ensure new schema keys from CSV even if DB already had rows
    seed_schema_from_csv()
    rows: list[EvaluationReportKeyword] = []
    for schema in schema_rows():
        defaults: dict[str, str] = {"value": ""}
        if schema.command in _DEFAULT_TEXT_FILES:
            text = load_default_text(schema.command)
            if text:
                defaults["value"] = text
        obj, created = EvaluationReportKeyword.objects.get_or_create(
            report=report,
            command=schema.command,
            defaults=defaults,
        )
        if (
            not created
            and schema.command in _DEFAULT_TEXT_FILES
            and not (obj.value or "").strip()
        ):
            text = load_default_text(schema.command)
            if text:
                obj.value = text
                obj.save(update_fields=["value", "updated_at"])
        rows.append(obj)
    return rows


def _is_pingxiang_project(*, buildunit: str = "", markdown: str = "", command: str = "", value: str = "") -> bool:
    """本项目本身就是萍乡演示稿对应的建设单位时，不应把真实内容当「残留演示」。"""
    bu = (buildunit or "").strip()
    if "萍乡" in bu:
        return True
    if "萍乡" in (markdown or ""):
        return True
    # 建设单位/项目全称自身即为萍乡时，也视为本项目
    if command in {"buildunit", "company", "projectnamefull", "projectname", "projectshort"}:
        if "萍乡" in (value or ""):
            return True
    return False


def _looks_like_bundled_demo(
    command: str,
    value: str,
    *,
    markdown: str = "",
    buildunit: str = "",
) -> bool:
    """Detect Pingxiang / brachy demo content that must not stick to other projects."""
    cur = (value or "").strip()
    if not cur:
        return False

    # 加速器项目里残留后装演示稿（与是否萍乡无关）
    if (
        ("直线加速器" in markdown or "CT模拟定位" in markdown)
        and (
            "后装治疗机" in cur
            or "后装治疗" in cur
            or "后装机房" in cur
            or r"^{192}" in cur
            or "192Ir" in cur
            or r"\mathrm{^{192}Ir}" in cur
        )
        and command
        in {
            "sourcedevice",
            "sourcesummary",
            "sourceoverview",
            "sourceremark",
            "sourcetype",
            "equipment",
            "equipmentconfig",
            "staffconfig",
            "protectionlayout",
            "workloadrows",
            "workload4222",
            "tab412",
            "tab414",
            "tab421",
            "tab423",
            "fig41images",
            "fig42points",
            "figsafetypos",
            "figvent",
            "figcable",
            "projectintro",
        }
    ):
        return True

    # 本项目就是萍乡市人民医院时：字段里的萍乡地名/人员是真实内容，不是串稿
    if _is_pingxiang_project(
        buildunit=buildunit, markdown=markdown, command=command, value=cur
    ):
        return False

    # 其他项目仍带萍乡演示地名/单位 → 残留演示稿
    pingxiang_markers = (
        "萍乡市人民医院",
        "萍乡市",
        "萍乡地区",
        "武功山中大道",
        "常青路",
        "黄塘巷",
        "直冲小区",
        "陈明伟",
        "刘鲁根",
        "欧阳公怒",
        "委托编号：250420",
        "YP250420",
    )
    if any(m in cur for m in pingxiang_markers):
        return True
    if buildunit and "萍乡" in cur:
        return True
    markers: dict[str, tuple[str, ...]] = {
        "stafflist": ("陈明伟", "刘鲁根", "欧阳公怒", "张红宇"),
        "staffconfig": ("后装治疗机",),
        "staffduties": ("设备操作、患者摆位",),
        "projectintro": ("后装治疗机机房改建",),
        "emergencyorg": ("郑志刚", "陈佳龙", "易飞", "0799-6881712"),
        "protectionorg": ("刘绍华", "何建中", "郑志刚"),
        "healthstaffrows": ("陈伟", "刘鲁根", "张红宇", "幸焕中", "欧阳公怒"),
        "localenvphone": ("6778203",),
        "localhealthphone": ("6879191",),
    }
    for needle in markers.get(command, ()):
        if needle in cur:
            return True
    return False


def _placeholder_for_command(command: str) -> str:
    text = load_default_text(command).strip()
    if text and not _looks_like_bundled_demo(command, text):
        return text
    table_placeholders = {
        "stafflist": "1 & （待填写） &  &  &  &  &  \\\\\n\\hline",
        "staffconfig": (
            r"\multicolumn{7}{|c|}{（请按本项目填写人员配备一览表）} \\" + "\n\\hline"
        ),
        "staffduties": "（岗位） &  & （职责） \\\\\n\\hline",
        "workloadrows": "1 & （设备） &  &  &  &  &  &  &  \\\\\n\\hline",
        "healthstaffrows": (
            "1\n& （待填写）\n& \n& \n& \n& \n& \n& \n& \n& \n&  \\\\\n\\hline"
        ),
    }
    if command in table_placeholders:
        return table_placeholders[command]
    if command.startswith("tab") or command.startswith("fig") or command.startswith("workload"):
        # Prefer skeleton with fixed captions from default_texts/
        skel = load_default_text(command).strip()
        if skel:
            return skel
        return f"% （{command}：请按本项目填写）"
    return "（请填写）"


def _norm_placeholder_text(value: str) -> str:
    """Normalize for comparing filled content vs skeleton placeholders."""
    s = (value or "").strip()
    s = re.sub(r"%[^\n]*", "", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n+", "\n", s)
    return s.strip().rstrip("%").strip()


def _is_empty_or_placeholder(
    value: str,
    command: str = "",
    *,
    markdown: str = "",
    buildunit: str = "",
) -> bool:
    cur = (value or "").strip()
    if not cur:
        return True
    if cur.startswith("（") and cur.endswith("）"):
        return True
    if "（待填写）" in cur or "请按本项目填写" in cur or cur.startswith("% （"):
        return True
    if "附图占位" in cur:
        return True
    if "（　）" in cur or "（  ）" in cur:
        return True
    # 表体骨架（如「（岗位） &  & （职责）」）虽有文字，仍视为未填
    if command:
        cur_n = _norm_placeholder_text(cur)
        for candidate in (
            _placeholder_for_command(command),
            load_default_text(command).strip(),
        ):
            if candidate and cur_n == _norm_placeholder_text(candidate):
                return True
        # 常见表体占位词（岗位/设备等）且几乎无真实数据行
        if command in {
            "stafflist",
            "staffconfig",
            "staffduties",
            "workloadrows",
            "healthstaffrows",
        } and any(
            tip in cur
            for tip in ("（岗位）", "（职责）", "（设备）", "（培训单位）", "（监测单位）", "（体检单位）")
        ):
            return True
    if command and _looks_like_bundled_demo(
        command, cur, markdown=markdown, buildunit=buildunit
    ):
        return True
    return False


def scrub_demo_keywords(
    report: EvaluationReport,
    *,
    markdown: str = "",
    extracted: dict[str, str] | None = None,
) -> list[str]:
    """Clear keyword values that still look like bundled Pingxiang/brachy demos."""
    ensure_project_keyword_rows(report)
    extracted = extracted or {}
    buildunit = ""
    bu = report.project_keywords.filter(command="buildunit").first()
    if bu:
        buildunit = (bu.value or "").strip()
    if not buildunit:
        buildunit = (extracted.get("buildunit") or "").strip()
    cleared: list[str] = []
    work = report.work_dir
    files_root = (work / "latex" / "files") if work else None
    for row in report.project_keywords.all():
        if (extracted.get(row.command) or "").strip():
            continue
        if not _looks_like_bundled_demo(
            row.command, row.value or "", markdown=markdown, buildunit=buildunit
        ):
            continue
        text = _placeholder_for_command(row.command)
        if text != (row.value or ""):
            row.value = text
            row.save(update_fields=["value", "updated_at"])
            cleared.append(row.command)
        # 同步清掉工作区 files/*.tex，避免 apply 时又从磁盘把演示稿读回 DB
        rel = FILE_BACKED_COMMANDS.get(row.command)
        if files_root and rel:
            path = files_root.parent / rel
            header = f"% personalized \\{row.command} — scrubbed demo content\n"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(header + text.rstrip() + "\n", encoding="utf-8")
    return cleared


def scrub_latex_demo_hospital(latex_root: Path, buildunit: str) -> int:
    """Replace hardcoded Pingxiang hospital names in chapter .tex with this project's unit."""
    if not latex_root.is_dir():
        return 0
    unit = (buildunit or "").strip() or "（建设单位）"
    replacements = (
        ("萍乡市人民医院", unit),
        ("萍乡市生态环境局", "当地生态环境主管部门"),
        ("萍乡市卫健委", "当地卫生健康主管部门"),
        ("萍乡地区", "项目所在地区"),
        ("江西省萍乡市开发区武功山中大道8号", "（建设地址）"),
        ("萍乡市武功山中大道8号", "（建设地址）"),
        ("武功山中大道", "（道路名称）"),
        ("常青路", "（道路名称）"),
        ("黄塘巷", "（道路名称）"),
        ("直冲小区", "（相邻区域）"),
        ("陈明伟", "（放射工作人员）"),
    )
    changed = 0
    skip_names = {"backup.tex"}
    for path in latex_root.rglob("*.tex"):
        if path.name in skip_names:
            # 附录备份稿整份替换，避免上百处演示附件残留
            path.write_text(
                "% 原模板演示附件已清空；请按本项目附件另行编制。\n",
                encoding="utf-8",
            )
            changed += 1
            continue
        if path.name == "99-info.tex":
            path.write_text(
                "% 评价信息表已结构化提取至关键词/正文；本文件不再保留演示医院原稿。\n",
                encoding="utf-8",
            )
            changed += 1
            continue
        text = path.read_text(encoding="utf-8")
        new = text
        for old, repl in replacements:
            new = new.replace(old, repl)
        # 控评模板里误粘贴的萍乡检测报告描述
        new = re.sub(
            r"根据湖南合润检测技术有限公司出具的《[^》]*萍乡[^》]*》[^，。]*[，,]?",
            "（请补充本项目验证检测报告名称与结论）",
            new,
        )
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def scrub_report_demo_content(report: EvaluationReport, markdown: str = "") -> dict[str, int | list[str]]:
    """Full scrub: demo keywords + work-tree chapter leftovers."""
    cleared = scrub_demo_keywords(report, markdown=markdown)
    buildunit = ""
    bu = report.project_keywords.filter(command="buildunit").first()
    if bu:
        buildunit = (bu.value or "").strip()
    work = report.work_dir
    files_changed = 0
    if work:
        files_changed = scrub_latex_demo_hospital(work / "latex", buildunit)
    return {"cleared_keywords": cleared, "files_changed": files_changed}


def sync_project_keywords_from_markdown(
    report: EvaluationReport,
    markdown: str,
    *,
    only_empty: bool = False,
) -> dict[str, str]:
    """用 converter 规则从 Markdown 提取值，写入项目 keywords 表。

    only_empty=True 时不覆盖库里已有非空值（避免预览刷新冲掉封面/项目信息手工修改）。
    """
    keywords = load_keywords(default_schema_csv_path())
    extracted = extract_project_values(markdown, keywords, include_defaults=False)
    ensure_project_keyword_rows(report)
    from converter.project_vars import RAW_LATEX_FILE_COMMANDS

    for command, value in extracted.items():
        if not value:
            continue
        if command in LONG_TEXT_COMMANDS and command not in RAW_LATEX_FILE_COMMANDS:
            from converter.md2latex import convert_inline

            value = convert_inline(value)
        row = EvaluationReportKeyword.objects.filter(report=report, command=command).first()
        if row is None:
            continue
        cur = (row.value or "").strip()
        if only_empty and cur and not _is_empty_or_placeholder(
            cur, command, markdown=markdown, buildunit=(extracted.get("buildunit") or "")
        ):
            continue
        if value != row.value:
            row.value = value
            row.save(update_fields=["value", "updated_at"])

    is_evaluation_form = "评价类型" in markdown and "单位名称" in markdown
    if is_evaluation_form:
        scrub_demo_keywords(report, markdown=markdown, extracted=extracted)
        buildunit = (extracted.get("buildunit") or "").strip()
        if not buildunit:
            bu = report.project_keywords.filter(command="buildunit").first()
            buildunit = (bu.value or "").strip() if bu else ""
        work = report.work_dir
        if work and (work / "latex").is_dir():
            scrub_latex_demo_hospital(work / "latex", buildunit)
    return extracted


def extract_newcommand_values(main_tex: Path) -> dict[str, str]:
    """Read ``\\newcommand{\\cmd}{val}`` from main.tex (with alias normalize)."""
    if not main_tex.is_file():
        return {}
    text = main_tex.read_text(encoding="utf-8")
    raw = extract_newcommand_definitions(text)
    out: dict[str, str] = {}
    for cmd, val in raw.items():
        if cmd in COMPOSED_COMMANDS and "\\" in val:
            continue
        key = _MAIN_ALIASES.get(cmd, cmd)
        out[key] = val
    return out


def sync_project_keywords_from_files(
    report: EvaluationReport,
    latex_root: Path,
    *,
    only_empty: bool = True,
) -> dict[str, str]:
    """Import long personalized bodies from files/*.tex."""
    ensure_project_keyword_rows(report)
    buildunit = ""
    bu = report.project_keywords.filter(command="buildunit").first()
    if bu:
        buildunit = (bu.value or "").strip()
    updated: dict[str, str] = {}
    for command, rel in FILE_BACKED_COMMANDS.items():
        path = latex_root / rel
        if not path.is_file():
            continue
        value = _strip_tex_comments(path.read_text(encoding="utf-8")).strip()
        if not value or (value.startswith("（") and value.endswith("）")):
            continue
        row = EvaluationReportKeyword.objects.filter(report=report, command=command).first()
        if row is None:
            continue
        cur = (row.value or "").strip()
        if only_empty and cur and not _is_empty_or_placeholder(
            cur, command, buildunit=buildunit
        ):
            continue
        if value != row.value:
            row.value = value
            row.save(update_fields=["value", "updated_at"])
            updated[command] = value
    return updated


def sync_project_keywords_from_main_tex(
    report: EvaluationReport,
    main_tex: Path,
    *,
    only_empty: bool = True,
) -> dict[str, str]:
    """Import personalized values from work main.tex into the keyword table."""
    ensure_project_keyword_rows(report)
    values = extract_newcommand_values(main_tex)
    valid = {s.command for s in schema_rows()}
    buildunit = (values.get("buildunit") or "").strip()
    if not buildunit:
        bu = report.project_keywords.filter(command="buildunit").first()
        if bu:
            buildunit = (bu.value or "").strip()
    updated: dict[str, str] = {}
    for command, value in values.items():
        if command not in valid or command in FILE_BACKED_COMMANDS:
            continue
        value = (value or "").strip()
        if value.startswith("（") and value.endswith("）"):
            continue
        row = EvaluationReportKeyword.objects.filter(report=report, command=command).first()
        if row is None:
            continue
        cur = (row.value or "").strip()
        if only_empty and cur and not _is_empty_or_placeholder(
            cur, command, buildunit=buildunit
        ):
            continue
        if value and value != row.value:
            row.value = value
            row.save(update_fields=["value", "updated_at"])
            updated[command] = value
    return updated


def project_keywords_for_template(report: EvaluationReport) -> list[dict]:
    """合并 schema + 项目值（扁平列表，顺序与 CSV 一致）。"""
    groups = project_keywords_grouped(report)
    rows: list[dict] = []
    for g in groups:
        rows.extend(g["rows"])
    return rows


def list_unfilled_personalized_keywords(report: EvaluationReport) -> list[dict[str, str]]:
    """列出仍为空或占位的个性化字段（不含自动组合宏）。"""
    ensure_project_keyword_rows(report)
    work = report.work_dir
    if work and (work / "latex").is_dir():
        try:
            sync_file_backed_keyword_from_disk(report, work / "latex")
        except Exception:
            pass

    label_map = {s.command: s.label_cn for s in schema_rows()}
    keywords = load_keywords(default_schema_csv_path())
    cat_of = {item.command: item.category for item in keywords}
    values = project_keyword_map(report)
    buildunit = (values.get("buildunit") or "").strip()
    missing: list[dict[str, str]] = []

    for schema in schema_rows():
        cmd = schema.command
        if cmd in COMPOSED_COMMANDS or cmd in SKIP_UNFILLED_REMINDER_COMMANDS:
            continue
        value = values.get(cmd, "")
        if not _is_empty_or_placeholder(value, cmd, buildunit=buildunit):
            continue
        cat = cat_of.get(cmd, "shared")
        meta = KEYWORD_SECTION_META.get(cat) or {}
        rel = FILE_BACKED_COMMANDS.get(cmd, "")
        missing.append(
            {
                "command": cmd,
                "label_cn": label_map.get(cmd) or cmd,
                "category": cat,
                "category_title": str(meta.get("title") or cat),
                "chapter_key": str(meta.get("chapter_key") or ""),
                "source_file": normalize_input_rel_key(rel) if rel else "",
            }
        )
    return missing


def project_keywords_grouped(report: EvaluationReport) -> list[dict]:
    """按编辑页栏目归类，供「项目信息」页分组展示。"""
    ensure_project_keyword_rows(report)
    values = project_keyword_map(report)
    buildunit = (values.get("buildunit") or "").strip()
    keywords = load_keywords(default_schema_csv_path())
    label_map = {s.command: s.label_cn for s in schema_rows()}

    by_cat: dict[str, list[dict]] = {key: [] for key in KEYWORD_SECTION_ORDER}
    seen: set[str] = set()

    for item in keywords:
        cmd = item.command
        seen.add(cmd)
        cat = item.category if item.category in KEYWORD_SECTION_META else "shared"
        by_cat.setdefault(cat, [])
        by_cat[cat].append(
            {
                "command": cmd,
                "label_cn": label_map.get(cmd) or item.label_cn,
                "value": values.get(cmd, ""),
                "category": cat,
                "long_text": cmd in LONG_TEXT_COMMANDS,
                "composed": cmd in COMPOSED_COMMANDS,
                "notes": item.notes,
                "unfilled": _is_empty_or_placeholder(
                    values.get(cmd, ""), cmd, buildunit=buildunit
                )
                and cmd not in COMPOSED_COMMANDS
                and cmd not in SKIP_UNFILLED_REMINDER_COMMANDS,
            }
        )

    # schema 里有、CSV 里没有的字段 → 报告全局
    for schema in schema_rows():
        if schema.command in seen:
            continue
        by_cat.setdefault("shared", []).append(
            {
                "command": schema.command,
                "label_cn": schema.label_cn,
                "value": values.get(schema.command, ""),
                "category": "shared",
                "long_text": schema.command in LONG_TEXT_COMMANDS,
                "composed": schema.command in COMPOSED_COMMANDS,
                "notes": "",
                "unfilled": _is_empty_or_placeholder(
                    values.get(schema.command, ""),
                    schema.command,
                    buildunit=buildunit,
                )
                and schema.command not in COMPOSED_COMMANDS
                and schema.command not in SKIP_UNFILLED_REMINDER_COMMANDS,
            }
        )

    groups: list[dict] = []
    ordered_keys = list(KEYWORD_SECTION_ORDER) + [
        k for k in by_cat if k not in KEYWORD_SECTION_ORDER
    ]
    for key in ordered_keys:
        rows = by_cat.get(key) or []
        if not rows:
            continue
        meta = KEYWORD_SECTION_META.get(
            key,
            {
                "title": key,
                "group": "其他",
                "chapter_key": "",
                "hint": "",
            },
        )
        groups.append(
            {
                "key": key,
                "title": meta["title"],
                "group": meta["group"],
                "chapter_key": meta.get("chapter_key", ""),
                "hint": meta.get("hint", ""),
                "rows": rows,
            }
        )
    return groups


def cover_keywords_for_edit(report: EvaluationReport) -> dict[str, str]:
    """Values shown on the cover editor (DB overrides)."""
    ensure_project_keyword_rows(report)
    values = project_keyword_map(report)
    defaults = {k.command: k.default_value for k in load_keywords(default_schema_csv_path())}
    out: dict[str, str] = {}
    for cmd in COVER_COMMANDS:
        val = (values.get(cmd) or "").strip()
        if not val:
            val = (defaults.get(cmd) or "").strip()
        out[cmd] = val
    return out


def save_project_keywords(report: EvaluationReport, data: dict[str, str]) -> None:
    ensure_project_keyword_rows(report)
    valid_commands = {s.command for s in schema_rows()}
    for command, value in data.items():
        if command not in valid_commands:
            continue
        # keep internal newlines for long texts
        cleaned = value if command in LONG_TEXT_COMMANDS else (value or "").strip()
        if command in LONG_TEXT_COMMANDS:
            cleaned = (value or "").strip()
        EvaluationReportKeyword.objects.filter(report=report, command=command).update(
            value=cleaned
        )
    vals = project_keyword_map(report)
    hazard = (vals.get("radiationhazard") or "").strip()
    kind = (vals.get("reportkind") or "").strip()
    if hazard and kind:
        EvaluationReportKeyword.objects.filter(report=report, command="reporttype").update(
            value=hazard + kind
        )
    buildunit = (vals.get("buildunit") or "").strip()
    project = (vals.get("project") or "").strip()
    if buildunit and project:
        EvaluationReportKeyword.objects.filter(report=report, command="projectnamefull").update(
            value=buildunit + project
        )
        EvaluationReportKeyword.objects.filter(report=report, command="reportname").update(
            value=buildunit + project + hazard + kind
            if hazard and kind
            else buildunit + project
        )


def _table_body_structure_score(text: str) -> int:
    """Higher = more likely a fixed merged table (prefer over stale DB)."""
    s = text or ""
    score = 0
    if r"\multirow" in s and "{=}" in s:
        score += 50
    if r"\multirow" in s and "{*}" in s and "{=}" not in s:
        score -= 40
    if r"\cline{" in s:
        score += 30
    if r"\resizebox" in s:
        score += 15
    if r"\multicolumn{3}{|c|}" in s:
        score -= 25
    if re.search(r"\{\|c\|c\|", s):
        score -= 20
    if "分区" in s and "细部名称" in s:
        score += 10
    return score


def apply_file_backed_keywords(report: EvaluationReport, latex_root: Path) -> None:
    """Write long personalized bodies to files/*.tex under the work latex root.

    Never clobber a work file that the structured editor has enriched with
    figures / A3 page wrappers — always prefer disk and refresh DB from it.
    """
    values = project_keyword_map(report)
    for command, rel in FILE_BACKED_COMMANDS.items():
        path = latex_root / rel
        text = (values.get(command) or "").strip()
        if path.is_file():
            existing = path.read_text(encoding="utf-8")
            existing_body = _strip_tex_comments(existing).strip()
            # Prefer a structurally better table body already on disk over stale DB.
            if command in TABLE_ROW_BODY_COMMANDS or command in {
                "sourceoverview",
                "tab414",
                "tab421",
                "tab423",
            }:
                if (
                    existing_body
                    and _table_body_structure_score(existing_body)
                    > _table_body_structure_score(text)
                ):
                    sync_file_backed_keyword_from_disk(report, latex_root, command)
                    continue
            # Protect editor-enriched figures on disk when DB has nothing better.
            has_real_graphics = (
                r"\includegraphics" in existing_body
                or r"\BeginAThreeLandscapePage" in existing_body
            )
            is_placeholder_figure = (
                "附图占位" in existing_body or r"\fbox{" in existing_body
            )
            disk_has_a3 = r"\BeginAThreeLandscapePage" in existing_body
            db_has_a3 = r"\BeginAThreeLandscapePage" in text
            # Disk A3 wrappers win over a stale DB body without them.
            if disk_has_a3 and not db_has_a3:
                sync_file_backed_keyword_from_disk(report, latex_root, command)
                continue
            if has_real_graphics and not is_placeholder_figure:
                buildunit = (values.get("buildunit") or "").strip()
                db_is_weak = (
                    not text
                    or _is_empty_or_placeholder(text, command, buildunit=buildunit)
                    or _looks_like_bundled_demo(command, text, buildunit=buildunit)
                )
                if db_is_weak:
                    if not _looks_like_bundled_demo(
                        command, existing_body, buildunit=buildunit
                    ):
                        sync_file_backed_keyword_from_disk(report, latex_root, command)
                        continue
                    text = _placeholder_for_command(command)
        if not text:
            # Keep existing project file if DB is empty (do not clobber with placeholders).
            if path.is_file():
                existing = _strip_tex_comments(path.read_text(encoding="utf-8")).strip()
                if existing and not (
                    (existing.startswith("（") and existing.endswith("）"))
                    or existing.startswith("% 个性化")
                ):
                    continue
            text = load_default_text(command)
        if not text:
            text = f"（{command}）"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = f"% personalized \\{command} — do not edit structure in library template\n"
        content = normalize_tabular_blank_lines(text.rstrip())
        if command in TABLE_ROW_BODY_COMMANDS and content and not content.endswith("%"):
            content += "%"
        path.write_text(header + content + "\n", encoding="utf-8")


def sync_file_backed_keyword_from_disk(
    report: EvaluationReport, latex_root: Path, command: str | None = None
) -> None:
    """Update DB keywords from files/*.tex written by the structured editor."""
    commands = (
        {command: FILE_BACKED_COMMANDS[command]}
        if command and command in FILE_BACKED_COMMANDS
        else FILE_BACKED_COMMANDS
    )
    for cmd, rel in commands.items():
        path = latex_root / rel
        if not path.is_file():
            continue
        body = _strip_tex_comments(path.read_text(encoding="utf-8")).strip()
        if not body:
            continue
        body = normalize_tabular_blank_lines(body)
        EvaluationReportKeyword.objects.update_or_create(
            report=report,
            command=cmd,
            defaults={"value": body},
        )


def apply_project_keywords_to_main_tex(report: EvaluationReport, main_tex: Path) -> None:
    """将项目 keywords 写入 main.tex 的 \\newcommand 块，并同步 files/*.tex。"""
    if not main_tex.is_file():
        return
    keywords = load_keywords(default_schema_csv_path())
    db_values = project_keyword_map(report)
    merged: dict[str, str] = {}
    for item in keywords:
        if item.command in FILE_BACKED_COMMANDS:
            continue
        if db_values.get(item.command, "").strip():
            merged[item.command] = db_values[item.command].strip()
        elif item.default_value:
            merged[item.command] = item.default_value
        else:
            merged[item.command] = ""
    block = render_newcommand_block(merged, keywords)
    _ensure_vars_region(main_tex)
    patch_main_tex(main_tex, block)
    apply_file_backed_keywords(report, main_tex.parent)


def _ensure_vars_region(main_tex: Path) -> None:
    """Guarantee % <<PROJECT_VARS>> markers exist (migrate old free-form newcommands)."""
    content = main_tex.read_text(encoding="utf-8")
    if VARS_START in content and VARS_END in content:
        return
    begin = content.find(r"\begin{document}")
    if begin < 0:
        content = content.rstrip() + f"\n\n{VARS_START}\n{VARS_END}\n"
        main_tex.write_text(content, encoding="utf-8")
        return
    marker = content.find("% ====================== 项目信息")
    if marker < 0:
        marker = content.find(r"\newcommand{\company}")
        if marker > 0:
            marker = content.rfind("\n", 0, marker) + 1
    if marker >= 0 and marker < begin:
        content = content[:marker] + f"{VARS_START}\n{VARS_END}\n\n" + content[begin:]
    else:
        content = content[:begin] + f"{VARS_START}\n{VARS_END}\n\n" + content[begin:]
    main_tex.write_text(content, encoding="utf-8")


def ensure_report_personalized_latex(report: EvaluationReport, latex_root: Path) -> None:
    """After template→work copy: import macros/files into DB (if empty) then write back."""
    main_tex = latex_root / "main.tex"
    if not main_tex.is_file():
        return
    ensure_project_keyword_rows(report)
    sync_project_keywords_from_main_tex(report, main_tex, only_empty=True)
    sync_project_keywords_from_files(report, latex_root, only_empty=True)
    apply_project_keywords_to_main_tex(report, main_tex)
