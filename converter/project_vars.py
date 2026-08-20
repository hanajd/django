#!/usr/bin/env python3
"""Load keyword table, extract project variables from Markdown, emit LaTeX \\newcommand."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

VARS_START = r"% <<PROJECT_VARS>>"
VARS_END = r"% <<END_PROJECT_VARS>>"


def extract_newcommand_definitions(text: str) -> dict[str, str]:
    """Parse ``\\newcommand{\\cmd}{...}`` with nested braces (e.g. ``\\mathrm{^{192}Ir}``)."""
    out: dict[str, str] = {}
    if not text:
        return out
    pos = 0
    while True:
        m = re.search(r"\\newcommand\{\\([a-zA-Z]+)\}", text[pos:])
        if not m:
            break
        name = m.group(1)
        brace_start = pos + m.end()
        if brace_start >= len(text) or text[brace_start] != "{":
            pos = brace_start + 1
            continue
        depth = 0
        k = brace_start
        while k < len(text):
            ch = text[k]
            # Skip escaped braces so \} inside values does not end the argument.
            if ch == "\\" and k + 1 < len(text) and text[k + 1] in "{}":
                k += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out[name] = text[brace_start + 1 : k]
                    pos = k + 1
                    break
            k += 1
        else:
            break
    return out


CERT_IN_CELL_RE = re.compile(
    r"^(.+?)(?:[，,]\s*证书编号[：:]\s*(.+))?$"
)
REPORTNO_LINE_RE = re.compile(
    r"^\*\*报告编号[：:]\s*(.+?)(?:[（(](.+?)[）)])?\*\*$"
)
TITLE_RE = re.compile(
    r"^#\s*(.+?)(放射性职业病危害)(预评价报告书|控制效果评价报告书|现状评价报告书)\s*$"
)


@dataclass
class KeywordDef:
    command: str
    label_cn: str
    category: str
    md_pattern: str
    md_table_label: str
    default_value: str
    notes: str


def load_keywords(csv_path: Path) -> list[KeywordDef]:
    rows: list[KeywordDef] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                KeywordDef(
                    command=row["command"].strip(),
                    label_cn=row["label_cn"].strip(),
                    category=row["category"].strip(),
                    md_pattern=row.get("md_pattern", "").strip(),
                    md_table_label=row.get("md_table_label", "").strip(),
                    default_value=row.get("default_value", "").strip(),
                    notes=row.get("notes", "").strip(),
                )
            )
    return rows


def parse_markdown_tables(text: str) -> dict[str, str]:
    """Collect label→value pairs from Markdown pipe tables and HTML ``<table>`` blocks."""
    table: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.count("|") < 2:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if all(re.fullmatch(r":?-{1,}:?", cell) for cell in cells):
            continue
        cleaned = [_clean_md_cell(cell) for cell in cells]
        _ingest_label_value_cells(table, cleaned)
    _ingest_html_tables(text, table)
    return table


_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_HTML_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html_cell(value: str) -> str:
    value = _HTML_TAG_RE.sub("", value)
    value = (
        value.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return _clean_md_cell(value)


def _ingest_label_value_cells(table: dict[str, str], cells: list[str]) -> None:
    for index in range(len(cells) - 1):
        label = _normalize_table_label(cells[index])
        value = next(
            (candidate.strip() for candidate in cells[index + 1 :] if candidate.strip()),
            "",
        )
        if not label or not value or label in {"项目", "内容", "---", "序号"}:
            continue
        # Prefer first non-empty mapping; later tables may overwrite with richer values.
        table[label] = value


def _ingest_html_tables(text: str, table: dict[str, str]) -> None:
    for html in _HTML_TABLE_RE.findall(text):
        for row_html in _HTML_ROW_RE.findall(html):
            cells = [_strip_html_cell(cell) for cell in _HTML_CELL_RE.findall(row_html)]
            if len(cells) < 2:
                continue
            _ingest_label_value_cells(table, cells)


def _normalize_table_label(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "", value, flags=re.I)
    value = re.sub(r"\s+", "", value)
    return value.rstrip("：:").strip()


def _clean_md_cell(value: str) -> str:
    value = value.replace(r"\.", ".")
    value = value.replace(r"\-", "-")
    value = value.replace(r"\(", "(").replace(r"\)", ")")
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return value.strip()


def _extract_by_pattern(text: str, pattern: str) -> str | None:
    if not pattern:
        return None
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _split_person_cert(value: str) -> tuple[str, str | None]:
    match = CERT_IN_CELL_RE.match(value.strip())
    if not match:
        return value.strip(), None
    name = match.group(1).strip()
    cert = match.group(2).strip() if match.group(2) else None
    return name, cert


def _extract_from_title(text: str, values: dict[str, str]) -> None:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    match = TITLE_RE.match(first_line)
    if not match:
        return
    buildunit, hazard, kind = match.group(1), match.group(2), match.group(3)
    values.setdefault("buildunit", buildunit)
    values.setdefault("radiationhazard", hazard)
    values.setdefault("reportkind", kind)
    values.setdefault("reporttype", hazard + kind)


def _normalize_extracted_value(value: str) -> str:
    value = _clean_md_cell(value)
    value = value.replace(r"\-", "-")
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = value.replace(" (签字或盖章)", "（签字或盖章）")
    value = value.replace("(签字或盖章)", "（签字或盖章）")
    return value.strip()


def _selected_checkbox_value(value: str, choices: tuple[str, ...]) -> str | None:
    normalized = value.replace("\uf052", "☑").replace("\uf0a3", "☐")
    normalized = normalized.replace("\uf0fe", "☑").replace("\uf0a8", "☐")
    normalized = normalized.replace("□", "☐").replace("■", "☑")
    for choice in choices:
        if re.search(re.escape(choice) + r"\s*☑", normalized):
            return choice
        if re.search(r"☑\s*" + re.escape(choice), normalized):
            return choice
        if re.search(r"☑[^☑☐]{0,40}" + re.escape(choice), normalized):
            return choice
    return None


def _extract_reportno_line(text: str, values: dict[str, str]) -> None:
    for line in text.splitlines():
        match = REPORTNO_LINE_RE.match(line.strip())
        if not match:
            continue
        raw = _normalize_extracted_value(match.group(1))
        ver_match = re.match(r"^(.+?)[（(]([^）)]+)[）)]\s*$", raw)
        if ver_match:
            values["reportno"] = ver_match.group(1).strip()
            ver = ver_match.group(2).strip()
            values["reportver"] = ver if ver.startswith("(") else f"({ver})"
        else:
            values["reportno"] = raw
        break


def _normalize_cn_date(raw: str) -> str:
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", raw or "")
    if not m:
        return (raw or "").strip()
    return f"{m.group(1)} 年 {int(m.group(2))} 月 {int(m.group(3))} 日"


def _extract_commission_date(markdown_text: str) -> str:
    """Prefer the date near 委托单位 seal line on the commission form."""
    m = re.search(
        r"委托单位[：:][^\n]{0,80}\n+\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
        markdown_text,
        re.S,
    )
    if m:
        return _normalize_cn_date(m.group(1))
    # Fallback: last date on the first form page (before 信息表)
    head = markdown_text.split("评价信息表", 1)[0]
    dates = re.findall(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", head)
    if dates:
        return _normalize_cn_date(dates[-1])
    return ""


def compose_basicmaterials(
    *,
    buildunit: str,
    commission_no: str = "",
    commission_date: str = "",
) -> str:
    """Compose §1.5.3 基础资料 from evaluation commission form fields."""
    unit = (buildunit or "").strip() or "（建设单位）"
    no = (commission_no or "").strip() or "（委托编号）"
    date = (commission_date or "").strip() or "____ 年 __ 月 __ 日"
    return (
        f"（1）《建设项目放射性职业病危害评价委托单》，委托编号：{no}，"
        f"委托单位：{unit}，委托时间：{date}；\n\n"
        f"（2）{unit}提供的辐射源项清单、本项目相关设计图纸、放射防护设计方案、"
        f"现有放射防护管理制度、拟配备的放射工作人员清单、现有职业健康管理等资料。"
    )


def extract_project_values(
    markdown_text: str,
    keywords: list[KeywordDef],
    *,
    include_defaults: bool = True,
) -> dict[str, str]:
    tables = parse_markdown_tables(markdown_text)
    values: dict[str, str] = {}

    _extract_from_title(markdown_text, values)
    _extract_reportno_line(markdown_text, values)

    cert_pairs = {
        "projectleader": "projectleadercert",
        "reportwriter": "reportwritercert",
        "reportreviewer": "reportreviewercert",
        "reportsignatory": "reportsignatorycert",
    }

    for item in keywords:
        if item.command in {"reportno", "reportver"} and item.command in values:
            continue

        value: str | None = None

        if item.md_pattern:
            value = _extract_by_pattern(markdown_text, item.md_pattern)

        if value is None and item.md_table_label and item.md_table_label in tables:
            value = tables[item.md_table_label]
            if item.command == "company":
                value = re.sub(r"\s*[\(（]盖章[\)）]\s*", "", value)

        if value is None and item.default_value and include_defaults:
            value = item.default_value

        if value is None:
            continue

        value = _normalize_extracted_value(value)

        if item.command in cert_pairs and item.command in values:
            continue

        if item.command in cert_pairs:
            name, cert = _split_person_cert(value)
            values[item.command] = name
            if cert:
                values[cert_pairs[item.command]] = cert
            continue

        if item.command.endswith("cert"):
            continue

        values[item.command] = value

    # Evaluation commission forms use the construction unit's legal person.
    # Do not overwrite the evaluation company's declaration-page legal person.
    is_evaluation_form = "评价类型" in tables and "单位名称" in tables
    if is_evaluation_form:
        values.pop("legalrep", None)
        direct_map = {
            "buildunit": "单位名称",
            "projectnamefull": "项目名称",
            "buildaddress": "建设地址",
            "buildunitlegalrep": "法定代表人",
            "buildunitcontact": "联系人",
            "buildunitphone": "电话",
            "unitprofile": "单位简介",
            "projectintro": "项目简介",
        }
        for command, label in direct_map.items():
            value = tables.get(label, "").strip()
            if value:
                values[command] = _normalize_extracted_value(value)

        if not values.get("buildaddress"):
            addr = tables.get("地址", "").strip() or tables.get("通讯地址", "").strip()
            if addr:
                values["buildaddress"] = _normalize_extracted_value(addr)

        # Greeting line: **江西辐射剂量检测院有限公司：**
        if "company" not in values:
            for line in markdown_text.splitlines():
                m = re.match(r"^\*{0,2}(.+?(?:有限公司|检测院|研究所).+?)[:：]\*{0,2}\s*$", line.strip())
                if m:
                    values["company"] = _normalize_extracted_value(m.group(1))
                    break

        commission_no = ""
        m = re.search(r"委托编号[：:]\s*([A-Za-z0-9\-]+)", markdown_text)
        if m:
            commission_no = m.group(1).strip()
            if "reportno" not in values:
                values["reportno"] = f"赣检测院{commission_no}"

        full_name = values.get("projectnamefull", "")
        buildunit = values.get("buildunit", "")
        if full_name:
            project = full_name
            if buildunit and project.startswith(buildunit):
                project = project[len(buildunit) :].strip()
            values["project"] = project

        project_nature = _selected_checkbox_value(
            tables.get("项目性质", ""),
            ("新建", "改建", "扩建", "技术改造", "技术引进"),
        )
        if project_nature:
            values["projectnature"] = project_nature

        evaluation_type = tables.get("评价类型", "")
        if "放射性职业病危害" in evaluation_type:
            values["radiationhazard"] = "放射性职业病危害"
        if _selected_checkbox_value(evaluation_type, ("预评价",)) or "预评价" in evaluation_type and "☑" not in evaluation_type:
            values["reportkind"] = "预评价报告书"
        elif _selected_checkbox_value(evaluation_type, ("控制效果评价",)):
            values["reportkind"] = "控制效果评价报告书"

        total = tables.get("项目总投资", "").strip()
        protection = tables.get("放射卫生防护投资", "").strip()
        if total:
            values["totalinvestment"] = total + (f"，放射卫生防护计划投资 {protection}" if protection else "")

        # 1.5.3 基础资料：委托单不单独列出该段，按委托信息自动生成个性化正文
        values["basicmaterials"] = compose_basicmaterials(
            buildunit=values.get("buildunit", ""),
            commission_no=commission_no,
            commission_date=_extract_commission_date(markdown_text),
        )

        # 从射线装置/设备名称推断，避免残留演示稿「后装治疗机」等
        devices: list[str] = []
        for name in (
            "医用电子直线加速器",
            "CT模拟定位机",
            "后装治疗机",
            "伽玛刀",
            "质子治疗装置",
        ):
            if name in markdown_text and name not in devices:
                devices.append(name)
        if devices:
            joined = "、".join(devices)
            values.setdefault("equipment", joined)
            values["sourcedevice"] = f"拟新购的{len(devices)}台（套）：{joined}"
            values["equipmentconfig"] = joined
            if any("加速器" in d or "CT" in d for d in devices):
                values["sourcetype"] = "射线装置"
                values["sourcesummary"] = joined
                values["sourceremark"] = (
                    r"1）设备参数见下文表3.1-1\\2）用途：医用电子直线加速器放射治疗及CT模拟定位"
                    r"\\3）射线种类：X射线/电子线"
                )
                values["sourceoverview"] = (
                    f"本项目拟涉及{joined}的使用，主要技术参数及拟安装场所详见表"
                    r"~\ref{tab:ch3-linac-src}（请按建设单位拟购参数补充）。"
                )
            elif "后装" in joined:
                values["sourcetype"] = "含密封源装置"
                values["sourcesummary"] = joined

        # 建设规模：优先用项目简介摘要
        if not values.get("constructionscale") and values.get("projectintro"):
            intro = values["projectintro"]
            if len(intro) > 40:
                values["constructionscale"] = intro[:220] + ("…" if len(intro) > 220 else "")

        if values.get("buildaddress") and not values.get("developmentplan"):
            values["developmentplan"] = "除本项目外，放射治疗项目尚无其他发展规划。"

        # Multi-row tables / figures from 评价信息表 → files/*.tex bodies
        from converter.eval_form_bodies import extract_eval_form_bodies

        for command, body in extract_eval_form_bodies(markdown_text).items():
            if body and body.strip():
                values[command] = body.strip()

    if "reporttype" not in values:
        hazard = values.get("radiationhazard", "")
        kind = values.get("reportkind", "")
        if hazard and kind:
            values["reporttype"] = hazard + kind

    if "projectnamefull" not in values and "buildunit" in values and "project" in values:
        values["projectnamefull"] = values["buildunit"] + values["project"]

    return values


def escape_latex_value(value: str) -> str:
    """Escape plain text for \\newcommand bodies; keep math/macros intact."""
    if not value:
        return value
    # Already contains math or TeX macros — do not escape braces/backslashes.
    if "$" in value or "\\" in value:
        # Comment char would truncate the rest of a \\newcommand line.
        out: list[str] = []
        i = 0
        while i < len(value):
            ch = value[i]
            if ch == "%" and (i == 0 or value[i - 1] != "\\"):
                out.append(r"\%")
            elif ch == "&" and (i == 0 or value[i - 1] != "\\"):
                out.append(r"\&")
            elif ch == "#" and (i == 0 or value[i - 1] != "\\"):
                out.append(r"\#")
            else:
                out.append(ch)
            i += 1
        return "".join(out)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
    }
    for char, repl in replacements.items():
        value = value.replace(char, repl)
    return value


# Long personalized bodies live in files/*.tex, not in \\newcommand.
FILE_BACKED_COMMANDS: dict[str, str] = {
    "unitprofile": "files/unitProfile.tex",
    "projectintro": "files/projectIntro.tex",
    "basicmaterials": "files/basicmaterials.tex",
    "stafflist": "files/staffList.tex",
    "staffconfig": "files/staffConfig.tex",
    "staffduties": "files/staffDuties.tex",
    "surroundings": "files/surroundings.tex",
    "backgroundradiation": "files/backgroundradiation.tex",
    "protectionlayout": "files/protectionlayout.tex",
    "workloadrows": "files/workloadRows.tex",
    "sourceoverview": "files/sourceoverview.tex",
    "tab412": "files/tab412.tex",
    "tab414": "files/tab414.tex",
    "fig41images": "files/fig41images.tex",
    "tab421": "files/tab421.tex",
    "fig42points": "files/fig42points.tex",
    "workload4222": "files/workload4222.tex",
    "tab423": "files/tab423.tex",
    "figsafetypos": "files/figSafetyPos.tex",
    "figvent": "files/figVent.tex",
    "figcable": "files/figCable.tex",
    "emergencyorg": "files/应急组织与职责.tex",
    "protectionorg": "files/放射防护管理组织.tex",
    "healthstaffrows": "files/healthStaffRows.tex",
}

# File-backed bodies that are already LaTeX (tables/figures); do not run convert_inline.
RAW_LATEX_FILE_COMMANDS: frozenset[str] = frozenset(
    {
        "stafflist",
        "staffconfig",
        "staffduties",
        "workloadrows",
        "healthstaffrows",
        "sourceoverview",
        "tab412",
        "tab414",
        "tab421",
        "tab423",
        "fig41images",
        "fig42points",
        "figsafetypos",
        "figvent",
        "figcable",
        "workload4222",
        "protectionlayout",
        "surroundings",
        "backgroundradiation",
        "emergencyorg",
        "protectionorg",
    }
)

# Composed macros written as expansions of other macros.
COMPOSED_COMMANDS: dict[str, str] = {
    "reporttype": r"\radiationhazard\reportkind",
    "reportname": r"\projectnamefull\reporttype",
}


def render_newcommand_block(values: dict[str, str], keywords: list[KeywordDef]) -> str:
    lines = [
        VARS_START,
    ]
    for item in keywords:
        if item.command in FILE_BACKED_COMMANDS:
            continue
        if item.command in COMPOSED_COMMANDS:
            lines.append(
                rf"\newcommand{{\{item.command}}}{{{COMPOSED_COMMANDS[item.command]}}}"
            )
            continue
        raw = values.get(item.command, item.default_value)
        if raw == "" and item.command in {"signyear", "signmonth", "signday"}:
            raw = " "
        escaped = escape_latex_value(raw)
        lines.append(rf"\newcommand{{\{item.command}}}{{{escaped}}}")
    lines.append(VARS_END)
    return "\n".join(lines) + "\n"


def patch_main_tex(main_tex: Path, command_block: str) -> None:
    content = main_tex.read_text(encoding="utf-8")
    start = content.find(VARS_START)
    end = content.find(VARS_END)
    if start != -1 and end != -1:
        end += len(VARS_END)
        content = content[:start] + command_block.strip() + content[end:]
    else:
        marker = r"\begin{document}"
        if marker not in content:
            raise ValueError(f"Cannot find {VARS_START} or \\begin{{document}} in {main_tex}")
        content = content.replace(
            marker,
            command_block + "\n" + marker,
            1,
        )
    main_tex.write_text(content, encoding="utf-8")


def build_substitution_map(values: dict[str, str]) -> list[tuple[str, str]]:
    """Longest-value-first replacements for md2latex body text."""
    mapping: list[tuple[str, str]] = []
    for value in sorted({v for v in values.values() if v and v.strip()}, key=len, reverse=True):
        for cmd, val in values.items():
            if val == value:
                mapping.append((value, f"\\{cmd}"))
                break
    return mapping


def substitute_variables_in_text(text: str, values: dict[str, str]) -> str:
    for value, command in build_substitution_map(values):
        if value in {" ", "\n"}:
            continue
        text = text.replace(value, command)
    return text
