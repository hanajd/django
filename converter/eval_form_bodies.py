"""Extract multi-row tables and figures from 评价信息表 Markdown into file-backed bodies."""

from __future__ import annotations

import re
from pathlib import Path

from converter.md2latex import (
    MergeAnchor,
    convert_cell_content,
    convert_inline,
    html_rows_to_anchors,
    merged_anchors_to_latex,
    parse_html_table,
)

_STAFF_DUTY_ORDER: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "放射肿瘤医师",
        ("放射肿瘤医师", "肿瘤医师", "放射医师"),
        "患者靶区划定及相关治疗方案的确定、质控把控",
    ),
    (
        "放射治疗物理师",
        ("放射治疗物理师", "医学物理", "物理人员", "物理师"),
        "治疗计划制定",
    ),
    (
        "放射治疗技师",
        ("放射治疗技师", "放疗技师", "技师"),
        "设备操作、患者摆位",
    ),
    (
        "维修人员",
        ("辅助维修", "维修工程师", "维修人员", "维修"),
        "放射治疗设备维修",
    ),
)


def _canonicalize_staff_role(text: str) -> str | None:
    t = (text or "").strip()
    if not t or len(t) > 40:
        return None
    # Skip非岗位说明行
    if any(x in t for x in ("病理", "影像", "法规", "评价", "开展项目", "计划增加")):
        return None
    for canonical, aliases, _duty in _STAFF_DUTY_ORDER:
        for alias in aliases:
            if alias in t:
                return canonical
    return None


def _staff_role_counts_from_config_html(html: str) -> dict[str, int]:
    """从表2.2-2人员配备一览解析各岗位现有人数。"""
    rows = parse_html_table(html)
    counts: dict[str, int] = {}
    for row in rows[1:]:
        texts = [c.text.strip() for c in row]
        role: str | None = None
        count: int | None = None
        for i, t in enumerate(texts):
            canon = _canonicalize_staff_role(t)
            if not canon:
                continue
            role = canon
            for u in texts[i + 1 :]:
                if re.fullmatch(r"\d+", u):
                    count = int(u)
                    break
            break
        if role and count is not None and count > 0:
            counts[role] = counts.get(role, 0) + count
    return counts


def _staffduties_from_config_html(html: str) -> str:
    """表2.2-3：固定岗位职责搭配 + 表2.2-2定岗人数。"""
    counts = _staff_role_counts_from_config_html(html)
    if not counts:
        return ""
    lines: list[str] = []
    for canonical, _aliases, duty in _STAFF_DUTY_ORDER:
        n = counts.get(canonical)
        if not n:
            continue
        duty_tex = convert_cell_content(duty)
        lines.append(f"{canonical} & {n}名 & {duty_tex} \\\\\n\\hline")
    return "\n".join(lines)


_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_BOLD_LINE_RE = re.compile(r"^\*{0,2}(.+?)\*{0,2}\s*$")

_TABLE_TITLE_COMMANDS: tuple[tuple[str, str], ...] = (
    ("放射治疗项目含密封源装置一览表", "sourceoverview"),
    ("建设射线装置和含密封源装置预期运行工作量", "workloadrows"),
    ("放射治疗项目机房屏蔽设计情况表", "tab421"),
    ("建设项目放射工作人员清单", "stafflist"),
    ("本项目放射工作人员配备情况", "staffconfig"),
    ("本项目职业人员健康管理一览表", "healthstaffrows"),
)

_FIG_CAPTIONS: tuple[tuple[str, str], ...] = (
    ("后装治疗机机房平面布局及尺寸图", "plan"),
    ("后装治疗机机房剖面图", "section"),
    ("本项目放射分区示意图", "zoning"),
    ("放射治疗机房防护安全措施拟安装位置图", "safety"),
    ("后装治疗机机房通风布局图", "vent"),
    ("放射治疗机房电缆沟、风管穿墙示意图", "cable"),
)

_KNOWN_TITLES: tuple[str, ...] = tuple(t for t, _ in _TABLE_TITLE_COMMANDS + _FIG_CAPTIONS)


def _norm_title(value: str) -> str:
    value = re.sub(r"[`*#]", "", value or "")
    value = re.sub(r"\s+", "", value)
    return value.strip()


def _match_known_title(line: str) -> str | None:
    stripped = (line or "").strip()
    m = _BOLD_LINE_RE.match(stripped)
    cand = _norm_title(m.group(1) if m else stripped)
    if not cand:
        return None
    for known in _KNOWN_TITLES:
        if known == cand or known in cand or (cand in known and len(cand) >= 6):
            return known
    return None


def _polish_sci(text: str) -> str:
    text = (text or "").replace("×", r"\times")
    text = re.sub(
        r"(\d+(?:\.\d+)?)\s*\\times\s*10\s*(\d+)\s*(Bq|Ci|Gy|Sv|MeV)?",
        lambda m: (
            f"${m.group(1)}\\times10^{{{m.group(2)}}}"
            + (f"\\,\\mathrm{{{m.group(3)}}}" if m.group(3) else "")
            + "$"
        ),
        text,
    )
    text = re.sub(r"(?<![\^${\\])(192)\s*Ir\b", r"$^{192}\\mathrm{Ir}$", text)
    text = re.sub(r"(?<![\^${\\])(60)\s*Co\b", r"$^{60}\\mathrm{Co}$", text)
    return text


def _latex_image_path(raw: str) -> str:
    path = (raw or "").strip().replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    name = Path(path).name
    return f"images/{name}" if name else path


def _figure_block(
    image_paths: list[str],
    caption: str,
    label: str,
    *,
    dual: bool = False,
) -> str:
    if not image_paths:
        return (
            "\\begin{figure}[H]\n"
            "\\centering\n"
            "\\fbox{\\parbox{0.85\\linewidth}{\\centering\\vspace{2.2cm}"
            "（附图占位，上传后替换）\\vspace{2.2cm}}}\n"
            f"\\caption{{{caption}}}\n"
            f"\\label{{{label}}}\n"
            "\\end{figure}"
        )
    if dual and len(image_paths) >= 2:
        left = _latex_image_path(image_paths[0])
        right = _latex_image_path(image_paths[1])
        return (
            "\\begin{figure}[H]\n"
            "\\centering\n"
            "\\begin{minipage}{0.48\\linewidth}\n"
            "  \\centering\n"
            f"  \\includegraphics[width=\\linewidth]{{{left}}}\n"
            "\\end{minipage}\n"
            "\\hfill\n"
            "\\begin{minipage}{0.48\\linewidth}\n"
            "  \\centering\n"
            f"  \\includegraphics[width=\\linewidth]{{{right}}}\n"
            "\\end{minipage}\n"
            f"\\caption{{{caption}}}\n"
            f"\\label{{{label}}}\n"
            "\\end{figure}"
        )
    path = _latex_image_path(image_paths[0])
    return (
        "\\begin{figure}[H]\n"
        "\\centering\n"
        f"\\includegraphics[width=0.85\\linewidth]{{{path}}}\n"
        f"\\caption{{{caption}}}\n"
        f"\\label{{{label}}}\n"
        "\\end{figure}"
    )


def _find_titled_html_tables(markdown: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _HTML_TABLE_RE.finditer(markdown):
        before = markdown[max(0, match.start() - 500) : match.start()]
        title_key = None
        for line in reversed(before.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            title_key = _match_known_title(stripped)
            if title_key:
                break
            if stripped.lower().startswith("<") or stripped.startswith("|"):
                break
        if not title_key:
            plain = _norm_title(re.sub(r"<[^>]+>", " ", match.group(0)))
            for known in _KNOWN_TITLES:
                if known in plain:
                    title_key = known
                    break
        if title_key and title_key not in found:
            # Only keep table titles that map to table commands (or cable caption)
            cmd = dict(_TABLE_TITLE_COMMANDS).get(title_key)
            if cmd or title_key == "放射治疗机房电缆沟、风管穿墙示意图":
                found[title_key] = match.group(0)
    return found


def _anchors_body_to_rows(html: str, *, skip_header_rows: int) -> str:
    rows = parse_html_table(html)
    if len(rows) <= skip_header_rows:
        return ""
    body_rows = rows[skip_header_rows:]
    for row in body_rows:
        for cell in row:
            cell.text = _polish_sci(cell.text)
    bn, bc, ba = html_rows_to_anchors(body_rows)
    full = merged_anchors_to_latex(bn, bc, ba, caption=None)
    lines = full.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == r"\hline"), 0)
    out: list[str] = []
    for ln in lines[start + 1 :]:
        s = ln.strip()
        if s.startswith(r"\end{tabular}") or s.startswith(r"\end{table}"):
            break
        if s.startswith(r"\begin{"):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def _stafflist_rows(html: str) -> str:
    rows = parse_html_table(html)
    if len(rows) < 2:
        return ""
    out: list[str] = []
    for row in rows[1:]:
        cells = [convert_cell_content(c.text) for c in row]
        if not any(cells):
            continue
        while len(cells) < 7:
            cells.append("")
        seq, name, post, major, edu, title, remark = cells[:7]
        if "序号" in seq:
            continue
        out.append(
            f"{seq} & {name} & {edu} & {major} & {title} & {remark} & {post} \\\\\n\\hline"
        )
    return "\n".join(out)


def _workload_rows(html: str) -> str:
    rows = parse_html_table(html)
    if len(rows) < 2:
        return ""
    out: list[str] = []
    for row in rows[1:]:
        cells = [convert_cell_content(c.text) for c in row]
        if not any(cells):
            continue
        joined = "".join(cells)
        if joined.startswith("注") or "年最大工作量" in joined:
            continue
        while len(cells) < 9:
            cells.append("")
        out.append(" & ".join(cells[:9]) + " \\\\\n\\hline")
    return "\n".join(out)


def _workload4222_from_html(html: str) -> str:
    rows = parse_html_table(html)
    if len(rows) < 2:
        return ""
    cells = [c.text.strip() for c in rows[1]]
    if len(cells) < 8:
        return ""
    device = cells[1] or "后装治疗机"
    patients = cells[2] or "（　）"
    beam = cells[3] or "（　）"
    days = cells[4] or "（　）"
    weeks = cells[5] or "（　）"
    week_h = cells[6] or "（　）"
    year_h = cells[7] or "（　）"
    remark = cells[8] if len(cells) > 8 else ""
    day_n = re.search(r"([\d.]+)", patients)
    beam_n = re.search(r"([\d.]+)", beam)
    qc = ""
    if remark:
        m = re.search(r"质控[^\d]*([\d.]+)\s*分钟", remark)
        if m:
            qc = m.group(1)
    day_txt = day_n.group(1) if day_n else patients
    beam_txt = beam_n.group(1) if beam_n else beam
    qc_txt = qc or "（　）"
    return (
        f"根据建设单位提供的信息，{device}预计每天治疗{day_txt}人次，"
        f"每周工作{days}天，每年工作{weeks}周，单次放射治疗时间约{beam_txt}分钟，"
        f"设备质控时间约{qc_txt}分钟/次/周，则周工作负荷为{week_h}小时/周，"
        f"年工作负荷为{year_h}小时/年。"
    )


def _is_room_name(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t in {"东墙", "西墙", "南墙", "北墙", "顶盖", "地坪", "防护门"}:
        return False
    if any(x in t for x in ("墙", "顶盖", "地坪", "防护门", "迷路")):
        return False
    return True


def _is_shield_name(text: str) -> bool:
    t = (text or "").strip()
    return t in {"东墙", "西墙", "南墙", "北墙", "顶盖", "地坪", "防护门"} or t.endswith("墙")


def _tab421_from_html(html: str, extra_html: str | None = None) -> str:
    rows = parse_html_table(html)
    for row in rows:
        for cell in row:
            cell.text = _polish_sci(cell.text)
    nrows, ncols, anchors = html_rows_to_anchors(rows)

    body_lines: list[str] = []
    for ri in range(1, nrows):
        covered: set[int] = set()
        active: dict[int, MergeAnchor] = {}
        for a in anchors.values():
            if a.row < ri < a.row + a.rowspan:
                for c in range(a.col, a.col + a.colspan):
                    covered.add(c)
                    active[c] = a

        parts: list[tuple[MergeAnchor, str]] = []
        ci = 0
        while ci < ncols:
            if ci in covered:
                ci += 1
                continue
            a = anchors.get((ri, ci))
            if a is None:
                ci += 1
                continue
            parts.append((a, convert_cell_content(a.text)))
            ci += a.colspan
        if not parts:
            continue

        room_cell = ""
        name = pos = material = ""
        texts = [t for _, t in parts]
        anchors_here = [a for a, _ in parts]

        # Continuing rowspan of a wall name (e.g. 北墙 → 迷路外墙厚度)
        cont_name = ""
        for a in active.values():
            if _is_shield_name(a.text):
                cont_name = convert_cell_content(a.text)
                break

        if len(parts) >= 4 and _is_room_name(parts[0][0].text):
            a0 = anchors_here[0]
            room_cell = (
                rf"\multirow{{{a0.rowspan}}}{{=}}{{\centering {texts[0]}}}"
                if a0.rowspan > 1
                else texts[0]
            )
            name, pos, material = texts[1], texts[2], texts[3]
            a_name = anchors_here[1] if len(anchors_here) > 1 else None
            if a_name and a_name.rowspan > 1 and pos:
                name = rf"\multirow{{{a_name.rowspan}}}{{=}}{{{name}}}"
        elif len(texts) == 3:
            name, pos, material = texts
            a_name = anchors_here[0] if anchors_here else None
            if a_name and a_name.rowspan > 1 and pos:
                name = rf"\multirow{{{a_name.rowspan}}}{{=}}{{{name}}}"
        elif len(texts) == 2:
            if cont_name:
                # Under an active wall multirow: leave 分区 empty
                name, pos, material = "", texts[0], texts[1]
            else:
                name, pos, material = texts[0], "", texts[1]
        elif len(texts) >= 4:
            name, pos, material = texts[0], texts[1], texts[2]
        else:
            continue

        # Vertical merge on 机房名称 / 分区: partial clines
        still_merged = False
        for a in anchors.values():
            if a.col == 0 and a.rowspan > 1 and a.row <= ri < a.row + a.rowspan - 1:
                still_merged = True
                break
        # Same wall name continuing into next detail row → only free cols 3-5
        wall_cont = False
        for a in anchors.values():
            if (
                a.col == 1
                and a.rowspan > 1
                and a.row <= ri < a.row + a.rowspan - 1
            ):
                wall_cont = True
                break
        if wall_cont:
            rule = "\\cline{3-5}"
        elif still_merged:
            rule = "\\cline{2-5}"
        else:
            rule = "\\hline"

        remark = "利旧"
        if not (pos or "").strip():
            name_pos = rf"\multicolumn{{2}}{{c|}}{{{name}}}"
            mat = material
            if "防护门" in name or "Pb" in material or "类型" in material:
                # Prefer makecell when thickness + type appear together
                parts_m = [p.strip() for p in re.split(r"[；;]\s*|\s{2,}", material) if p.strip()]
                if len(parts_m) >= 2 or "类型" in material:
                    if "\\\\" not in material and "类型" in material:
                        mat = material.replace("类型", r"\\类型")
                    mat = rf"\makecell{{{mat}}}"
            body_lines.append(
                f"{room_cell} & {name_pos} & {mat} & {remark} \\\\\n{rule}"
            )
        else:
            body_lines.append(
                f"{room_cell} & {name} & {pos} & {material} & {remark} \\\\\n{rule}"
            )

    note = ""
    if extra_html:
        erows = parse_html_table(extra_html)
        for row in erows[1:]:
            cells = [c.text.strip() for c in row]
            if len(cells) >= 3 and "距离" in "".join(cells):
                note = (
                    "\n\n\\noindent 注："
                    + convert_inline(f"{cells[0]}{cells[1]}为{cells[2]}。")
                )
                break

    body = "\n".join(body_lines) if body_lines else (
        r"\multicolumn{5}{|c|}{（请按本项目填写屏蔽设计参数）} \\" + "\n\\hline"
    )
    return (
        "% 个性化：tab421 — 表体可改；题注固定\n"
        "\\begin{table}[H]\n"
        "\\centering\n"
        "\\caption{放射治疗项目机房屏蔽设计情况表}\n"
        "\\label{tab:ch4-shield}\n"
        "\\setlength{\\tabcolsep}{4pt}\n"
        "\\begin{tabular}{|\n"
        ">{\\centering\\arraybackslash}m{2cm}|\n"
        ">{\\centering\\arraybackslash}m{1cm}|\n"
        ">{\\centering\\arraybackslash}m{3cm}|\n"
        ">{\\centering\\arraybackslash}m{5cm}|\n"
        ">{\\centering\\arraybackslash}m{2cm}|\n"
        "}\n"
        "\\hline\n"
        "机房名称\n"
        "& \\multicolumn{2}{c|}{屏蔽体}\n"
        "& 屏蔽设计材料及厚度\n"
        "& 备注 \\\\\n"
        "\\cline{2-3}\n"
        "& 分区 & 细部名称 & & \\\\\n"
        "\\hline\n"
        f"{body}\n"
        "\\end{tabular}\n"
        "\\end{table}"
        f"{note}"
    )


def _anchor_text(
    anchors: dict[tuple[int, int], MergeAnchor], row: int, col: int
) -> str:
    a = anchors.get((row, col))
    return (a.text or "").strip() if a else ""


def _find_param_pairs(
    anchors: dict[tuple[int, int], MergeAnchor],
) -> list[tuple[str, str]]:
    """Collect (label, value) rows under 放射源 from free cells."""
    labels = ("源类型", "物理状态", "半衰期", "平均能量", "周围剂量当量率")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    items = sorted(anchors.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    for (r, c), a in items:
        t = (a.text or "").strip()
        if not t:
            continue
        hit = next((lb for lb in labels if lb in t.replace(" ", "")), None)
        if not hit or hit in seen:
            continue
        # Prefer right-neighbor as value
        val = _anchor_text(anchors, r, c + 1) or _anchor_text(anchors, r, c + a.colspan)
        if not val:
            for (r2, c2), b in items:
                if r2 == r and c2 > c and (b.text or "").strip():
                    val = (b.text or "").strip()
                    break
        found.append((t, val or "—"))
        seen.add(hit)
    return found


def _format_sourceoverview_table(
    *,
    seq: str,
    device: str,
    vendor: str,
    model: str,
    activity_label: str,
    activity: str,
    site: str,
    origin: str,
    category: str,
    param_rows: list[tuple[str, str]],
) -> str:
    """Fixed 11-col layout matching the known-good 表3.1-1 sample."""
    if not param_rows:
        param_rows = [
            ("源类型", "$^{192}\\mathrm{Ir}$"),
            ("物理状态", "固体"),
            ("半衰期", "74.0 d"),
            ("平均能量", "0.37 MeV（$\\gamma$）"),
            (
                "周围剂量当量率\\\\常数（裸源）",
                "$0.111\\mu \\mathrm{Sv}\\cdot m^2/\\mathrm{MBq}\\cdot \\mathrm{h}$",
            ),
        ]
    # Ensure 5 param rows for rowspan alignment with left multirows
    while len(param_rows) < 5:
        param_rows.append(("—", "—"))
    param_rows = param_rows[:5]

    def _mr(text: str) -> str:
        return rf"\multirow{{5}}{{=}}{{\centering {text}}}"

    def _pair(label: str, value: str) -> str:
        # Long bare-source constant label: allow explicit break like the sample
        lab = label
        if "周围剂量当量率" in lab.replace(" ", "") and "\\\\" not in lab:
            lab = "周围剂量当量率\\\\常数（裸源）"
        return rf"\centering {lab} & \centering {value}"

    first_lab, first_val = param_rows[0]
    body_first = (
        f"{_mr(seq)} & {_mr(device)} & {_mr(vendor)} & {_mr(model)} & "
        f"{_mr(activity_label)} & {_mr(activity)} & "
        f"{_pair(first_lab, first_val)} & {_mr(site)} & {_mr(origin)} & {_mr(category)} \\\\"
    )
    cont: list[str] = []
    for lab, val in param_rows[1:]:
        cont.append("\\cline{7-8}")
        cont.append(f"& & & & & & {_pair(lab, val)} & & & \\\\")

    return "\n".join(
        [
            "\\begin{table}[H]",
            "\\centering",
            "\\caption{放射治疗项目含密封源装置一览表}",
            "\\label{tab:ch3-sealed-src}",
            "\\renewcommand{\\arraystretch}{1.3}",
            "\\setlength{\\tabcolsep}{2.5pt}",
            "\\begin{tabular}{|",
            ">{\\centering\\arraybackslash}m{1cm}|",
            ">{\\centering\\arraybackslash}m{1cm}|",
            ">{\\centering\\arraybackslash}m{1cm}|",
            ">{\\centering\\arraybackslash}m{1cm}|",
            ">{\\centering\\arraybackslash}m{1cm}|",
            ">{\\centering\\arraybackslash}m{1.5cm}|",
            ">{\\centering\\arraybackslash}m{2cm}|",
            ">{\\centering\\arraybackslash}m{2cm}|",
            ">{\\centering\\arraybackslash}m{1.5cm}|",
            ">{\\centering\\arraybackslash}m{1cm}|",
            ">{\\centering\\arraybackslash}m{1cm}|",
            "}",
            "\\hline",
            "\\multirow{2}{=}{\\centering 序号}",
            "& \\multirow{2}{=}{\\centering 装置名称}",
            "& \\multirow{2}{=}{\\centering 生产厂商}",
            "& \\multirow{2}{=}{\\centering 型号}",
            "& \\multicolumn{4}{c|}{\\centering 主要参数}",
            "& \\multirow{2}{=}{\\centering 拟安装场所}",
            "& \\multirow{2}{=}{\\centering 来源}",
            "& \\multirow{2}{=}{\\centering 源分类} \\\\",
            "\\cline{5-8}",
            "& & &",
            "& \\multicolumn{2}{c|}{\\centering 含源装置}",
            "& \\multicolumn{2}{c|}{\\centering 放射源}",
            "& & & \\\\",
            "\\hline",
            body_first,
            *cont,
            "\\hline",
            "\\end{tabular}",
            "\\end{table}",
        ]
    )


def _sourceoverview_from_html(html: str) -> str:
    rows = parse_html_table(html)
    for row in rows:
        for cell in row:
            cell.text = _polish_sci(cell.text)
    _nrows, _ncols, anchors = html_rows_to_anchors(rows)

    # Data row: first anchor in col0 with rowspan>=2 below header band
    data_r = 2
    for (r, c), a in sorted(anchors.items()):
        if c == 0 and a.rowspan >= 2 and r >= 1:
            data_r = r
            break

    def tx(c: int, default: str = "") -> str:
        raw = _anchor_text(anchors, data_r, c)
        return convert_cell_content(raw) if raw else default

    seq = tx(0, "1")
    device = tx(1, "后装治疗机")
    vendor = tx(2, "待定")
    model = tx(3, "待定")
    activity_label = tx(4, "最大装载活度")
    activity = tx(5, r"$3.7\times 10^{11}\mathrm{Bq}$（10Ci）")
    # site / origin / category are often on the right of an 11-col grid
    site = tx(8, "肿瘤大楼一层后装治疗机房")
    origin = tx(9, "拟新购")
    category = tx(10, "III类")
    if category and "Ⅲ" in category:
        category = category.replace("Ⅲ", "III")

    param_rows = _find_param_pairs(anchors)
    polished: list[tuple[str, str]] = []
    for lab, val in param_rows:
        polished.append((convert_cell_content(lab), convert_cell_content(val)))

    table = _format_sourceoverview_table(
        seq=seq,
        device=device,
        vendor=vendor,
        model=model,
        activity_label=activity_label,
        activity=activity,
        site=site,
        origin=origin,
        category=category,
        param_rows=polished,
    )
    intro = (
        "本项目拟涉及含密封源装置（后装治疗机）的使用，"
        "主要技术参数及拟安装场所见表~\\ref{tab:ch3-sealed-src}。"
    )
    return intro + "\n\n" + table


def _collect_figure_images(markdown: str) -> dict[str, list[str]]:
    """Associate images with figure keys.

    评价信息表常见版式是「组合图在上、图题在下」；合成后的 ``figpage_*.png``
    优先。若只有散图，则取图题**上方**连续图片为一组。
    """
    result: dict[str, list[str]] = {k: [] for _, k in _FIG_CAPTIONS}
    title_to_key = dict(_FIG_CAPTIONS)
    lines = [ln.rstrip("\n") for ln in markdown.splitlines()]

    # Prefer already-composited page figures
    for i, line in enumerate(lines):
        imgs = _MD_IMAGE_RE.findall(line)
        page_imgs = [p for p in imgs if "figpage_" in p]
        if not page_imgs:
            continue
        # Caption on same / nearby lines
        window = " ".join(lines[max(0, i - 2) : min(len(lines), i + 3)])
        known = _match_known_title(window) or _match_known_title(line)
        # Also match by filename key
        for p in page_imgs:
            m = re.search(r"figpage_([a-z]+)", p)
            if m and m.group(1) in result:
                result[m.group(1)] = [p]
                continue
            key = title_to_key.get(known or "")
            if key:
                result[key] = [p]

    # Caption-below association for remaining keys
    for i, line in enumerate(lines):
        known = _match_known_title(line)
        fig_key = title_to_key.get(known or "")
        if not fig_key or result[fig_key]:
            continue
        # Walk upward for images
        collected: list[str] = []
        j = i - 1
        while j >= 0:
            s = lines[j].strip()
            if not s:
                j -= 1
                continue
            if _match_known_title(s):
                break
            if s.lower().startswith("<table"):
                # Find table start
                start = j
                while start >= 0 and not lines[start].strip().lower().startswith("<table"):
                    start -= 1
                block = "\n".join(lines[start : i])
                collected = _MD_IMAGE_RE.findall(block) + collected
                break
            imgs = _MD_IMAGE_RE.findall(s)
            if imgs:
                collected = imgs + collected
                j -= 1
                continue
            break
        # Also images in same line / following table (cable)
        k = i + 1
        while k < len(lines):
            s = lines[k].strip()
            if not s:
                k += 1
                continue
            if s.lower().startswith("<table"):
                block = lines[k]
                k += 1
                while k < len(lines) and "</table>" not in block.lower():
                    block += "\n" + lines[k]
                    k += 1
                collected.extend(_MD_IMAGE_RE.findall(block))
            break
        if collected:
            # Prefer figpage if any
            page = [p for p in collected if "figpage_" in p]
            result[fig_key] = page[:1] if page else collected

    for key, paths in list(result.items()):
        seen: set[str] = set()
        uniq: list[str] = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        # Composite page figures are single wholes — never keep extras
        page = [p for p in uniq if "figpage_" in p]
        result[key] = page[:1] if page else uniq
    return result


def extract_eval_form_bodies(markdown_text: str) -> dict[str, str]:
    """Return file-backed command → LaTeX body extracted from 评价信息表 markdown."""
    collapsed = _norm_title(markdown_text)
    if not any(t in collapsed for t in _KNOWN_TITLES) and "评价类型" not in markdown_text:
        return {}

    out: dict[str, str] = {}
    tables = _find_titled_html_tables(markdown_text)

    html = tables.get("放射治疗项目含密封源装置一览表")
    if html:
        out["sourceoverview"] = _sourceoverview_from_html(html)

    html = tables.get("建设射线装置和含密封源装置预期运行工作量")
    if html:
        rows = _workload_rows(html)
        if rows:
            out["workloadrows"] = rows
        prose = _workload4222_from_html(html)
        if prose:
            out["workload4222"] = prose

    html = tables.get("放射治疗项目机房屏蔽设计情况表")
    if html:
        pos = markdown_text.find(html)
        rest = markdown_text[pos + len(html) : pos + len(html) + 800]
        extra = None
        m = _HTML_TABLE_RE.search(rest)
        if m and ("源离" in m.group(0) or "距离" in m.group(0)):
            extra = m.group(0)
        out["tab421"] = _tab421_from_html(html, extra)

    html = tables.get("建设项目放射工作人员清单")
    if html:
        rows = _stafflist_rows(html)
        if rows:
            out["stafflist"] = rows

    html = tables.get("本项目放射工作人员配备情况")
    if html:
        rows = _anchors_body_to_rows(html, skip_header_rows=1)
        if rows:
            out["staffconfig"] = rows
        duties = _staffduties_from_config_html(html)
        if duties:
            out["staffduties"] = duties

    html = tables.get("本项目职业人员健康管理一览表")
    if html:
        rows = _anchors_body_to_rows(html, skip_header_rows=2)
        if rows:
            out["healthstaffrows"] = rows

    figs = _collect_figure_images(markdown_text)
    out["fig41images"] = "\n\n".join(
        [
            _figure_block(
                figs.get("plan", []),
                "后装治疗机机房平面布局及尺寸图",
                "fig:ch4-room-plan",
            ),
            _figure_block(
                figs.get("section", []),
                "后装治疗机机房剖面图",
                "fig:ch4-room-section",
            ),
            _figure_block(
                figs.get("zoning", []),
                "本项目放射分区示意图",
                "fig:ch4-zoning",
            ),
        ]
    )
    out["figsafetypos"] = _figure_block(
        figs.get("safety", []),
        "放射治疗机房防护安全措施拟安装位置图",
        "fig:ch4-safety-pos",
    )
    out["figvent"] = _figure_block(
        figs.get("vent", []),
        "后装治疗机机房通风布局图",
        "fig:ch4-vent",
    )
    cable_imgs = figs.get("cable", [])
    # Page composites are already wholes — never split into dual minipages
    use_dual = (
        len(cable_imgs) >= 2
        and not any("figpage_" in p for p in cable_imgs)
    )
    cable = _figure_block(
        cable_imgs,
        "放射治疗机房电缆沟、风管穿墙示意图",
        "fig:ch4-cable",
        dual=use_dual,
    )
    cable = cable.replace(
        r"\label{fig:ch4-cable}",
        "\\label{fig:ch4-cable}\n\\label{fig:cable_wind}",
        1,
    )
    out["figcable"] = cable

    return {k: v for k, v in out.items() if v and str(v).strip()}
