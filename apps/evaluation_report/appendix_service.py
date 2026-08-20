"""附件材料：同步 appendix/ 目录，并在 13-appendix.tex 用 \\input{} 引用。

命名约定：
- 文件库（LibraryFile.original_name）：保留用户上传时的原始文件名，便于在文件库中识别与管理。
- LaTeX 工作区（appendix/、images/）：使用标准化文件名（如 attachment_02_01.png），便于编译引用与定位。
  同步时从文件库复制内容，仅在工作区侧重命名，不修改文件库记录。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from apps.core import pipeline_service

from .models import EvaluationReport

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
PDF_SUFFIXES = {".pdf"}

APPENDIX_ASSET_NAME_RE = re.compile(r"^(attachment_\d{2})_(\d{2})(\.[^.]+)$", re.IGNORECASE)

ANCHOR_PATTERNS: dict[str, str] = {
    "section": r"(\\section\{{{title}\}}\s*\n)",
    "subsection": r"(\\subsection\{{{title}\}}\s*\n)",
    "subsubsection": r"(\\subsubsection\{{{title}\}}\s*\n)",
}


def _library_file_path(library_file) -> Path | None:
    path = Path(pipeline_service.library_absolute_path(library_file.relative_path))
    return path if path.is_file() else None


def appendix_asset_name(slot_key: str, seq: int, suffix: str) -> str:
    """LaTeX 工作区内的标准化附件文件名（槽位内 01–99）。"""
    if seq < 1 or seq > 99:
        raise ValueError(f"附件序号须在 01–99：{slot_key} seq={seq}")
    return f"{slot_key}_{seq:02d}{suffix}"


def parse_appendix_asset_name(filename: str) -> tuple[str, int, str] | None:
    """解析 attachment_XX_YY.ext，返回 (slot_key, seq, suffix)。"""
    match = APPENDIX_ASSET_NAME_RE.match(filename)
    if not match:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def library_original_name_for_latex_asset(
    report: EvaluationReport,
    asset_filename: str,
) -> str | None:
    """由 LaTeX 标准化文件名反查文件库中的原始上传名。"""
    parsed = parse_appendix_asset_name(asset_filename)
    if not parsed:
        return None
    slot_key, seq, _suffix = parsed
    upload = report.uploads.filter(slot_key=slot_key).first()
    if not upload:
        return None
    files = list(upload.ordered_files())
    if seq < 1 or seq > len(files):
        return None
    lf = files[seq - 1].library_file
    return lf.original_name if lf else None


def report_latex_dir(report: EvaluationReport) -> Path:
    work = report.ensure_work_dir()
    latex = work / "latex"
    latex.mkdir(parents=True, exist_ok=True)
    return latex


def _render_asset_body(
    asset_filename: str,
    suffix: str,
    *,
    page_mode: str = "",
    rotate: int = 0,
    width_percent: int = 100,
    a3_role: str = "only",
) -> str:
    """单个图片/PDF 在槽位 tex 内的 LaTeX 片段。"""
    asset_path = f"appendix/{asset_filename}"
    if suffix in PDF_SUFFIXES:
        opts = ["pages=-", "fitpaper=true"]
        if rotate:
            opts.append(f"angle={int(rotate)}")
        return f"\\includepdf[{','.join(opts)}]{{{asset_path}}}\n"
    if suffix in IMAGE_SUFFIXES:
        return _render_image_body(
            asset_path,
            page_mode=page_mode,
            rotate=rotate,
            width_percent=width_percent,
            a3_role=a3_role,
        )
    return f"% 不支持的附件格式：{asset_filename}\n"


def _render_image_body(
    asset_path: str,
    *,
    page_mode: str = "",
    rotate: int = 0,
    width_percent: int = 100,
    a3_role: str = "only",
) -> str:
    rotate = int(rotate or 0) % 360
    if rotate not in (0, 90, 180, 270):
        rotate = 0
    try:
        width_percent = max(30, min(100, int(width_percent or 100)))
    except (TypeError, ValueError):
        width_percent = 100
    page_mode = (page_mode or "").strip()

    if page_mode == "a3landscape":
        role = (a3_role or "only").strip().lower()
        if role not in {"only", "first", "middle", "last"}:
            role = "only"
        angle_opt = f"angle={rotate}," if rotate else ""
        lines: list[str] = []
        if role in {"middle", "last"}:
            lines.append(r"\clearpage")
        if role in {"only", "first"}:
            lines.append(r"\BeginAThreeLandscapePage")
        lines.extend(
            [
                r"\begin{figure}[H]",
                r"\centering",
                rf"\includegraphics[{angle_opt}width=\textwidth,height=\dimexpr\textheight-3\baselineskip\relax,keepaspectratio]{{{asset_path}}}",
                r"\end{figure}",
            ]
        )
        if role in {"only", "last"}:
            lines.append(r"\EndAThreeLandscapePage")
        return "\n".join(lines) + "\n"

    if width_percent >= 100:
        width_opt = r"width=\linewidth"
    else:
        width_opt = rf"width={width_percent / 100:.2f}\linewidth"
    angle_opt = f"angle={rotate}," if rotate else ""
    return (
        "\\begin{figure}[H]\n"
        "\\centering\n"
        f"\\includegraphics[{angle_opt}{width_opt},keepaspectratio]{{{asset_path}}}\n"
        "\\end{figure}\n"
    )


def _a3_roles_for_sequence(flags: list[bool]) -> list[str]:
    """为连续 A3 图片分配 only/first/middle/last。"""
    roles = ["only"] * len(flags)
    i = 0
    while i < len(flags):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < len(flags) and flags[j]:
            j += 1
        length = j - i
        if length == 1:
            roles[i] = "only"
        else:
            roles[i] = "first"
            for k in range(i + 1, j - 1):
                roles[k] = "middle"
            roles[j - 1] = "last"
        i = j
    return roles


def _render_input_line(slot_key: str) -> str:
    return f"\\input{{appendix/{slot_key}}}\n"


def _anchor_pattern(anchor_type: str, title: str) -> str:
    template = ANCHOR_PATTERNS.get(anchor_type)
    if not template:
        raise ValueError(f"未知附录锚点类型：{anchor_type}")
    return template.format(title=re.escape(title))


def _inject_after_anchor(tex: str, title: str, anchor_type: str, body: str) -> str:
    if not body.strip():
        return tex

    for try_type in (anchor_type, "section", "subsection", "subsubsection"):
        pattern = _anchor_pattern(try_type, title)
        match = re.search(pattern, tex)
        if match:
            insert_at = match.end()
            return tex[:insert_at] + body + tex[insert_at:]

    raise ValueError(f"附录中未找到 \\section{{{title}}}（请核对 13-appendix.tex 模板标题）")


def _copy_fresh_appendix_tex(report: EvaluationReport, latex_dir: Path) -> Path:
    from .template_preview import template_root_for_report

    src = template_root_for_report(report) / "chapters" / "13-appendix.tex"
    if not src.is_file():
        raise FileNotFoundError(f"模板附录不存在：{src}")
    dest = latex_dir / "chapters" / "13-appendix.tex"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def ensure_pdfpages_package(main_tex: Path) -> None:
    if not main_tex.is_file():
        return
    content = main_tex.read_text(encoding="utf-8")
    if "pdfpages" in content:
        return
    marker = r"\usepackage{graphicx}"
    if marker not in content:
        raise ValueError("main.tex 中未找到 graphicx 宏包，无法插入 pdfpages")
    content = content.replace(marker, marker + "\n\\usepackage{pdfpages}")
    main_tex.write_text(content, encoding="utf-8")


def sync_appendix_assets(report: EvaluationReport, latex_dir: Path | None = None) -> list[str]:
    """
    将附件从文件库复制到 latex_dir/appendix/。
    每个槽位（attachment_XX）生成一个包装 tex，内含该槽位全部图片/PDF。
    """
    logs: list[str] = []
    latex_dir = latex_dir or report_latex_dir(report)
    appendix_dir = latex_dir / "appendix"
    appendix_dir.mkdir(parents=True, exist_ok=True)

    active_slot_keys: set[str] = set()
    active_asset_names: set[str] = set()
    has_pdf = False

    uploads = (
        report.uploads.filter(slot_key__startswith="attachment_")
        .prefetch_related("files__library_file")
        .order_by("sort_order", "pk")
    )
    for upload in uploads:
        prepared: list[dict] = []
        for seq, uf in enumerate(upload.ordered_files(), start=1):
            lf = uf.library_file
            src = _library_file_path(lf)
            if not src:
                continue

            suffix = src.suffix.lower()
            if suffix not in PDF_SUFFIXES and suffix not in IMAGE_SUFFIXES:
                logs.append(f"{upload.label}（{lf.original_name}）：跳过（格式 {suffix} 不支持）")
                continue

            asset_name = appendix_asset_name(upload.slot_key, seq, suffix)
            shutil.copy2(src, appendix_dir / asset_name)
            active_asset_names.add(asset_name)
            display = uf.normalized_display() if hasattr(uf, "normalized_display") else {
                "page_mode": getattr(uf, "page_mode", "") or "",
                "rotate": int(getattr(uf, "rotate", 0) or 0),
                "width_percent": int(getattr(uf, "width_percent", 100) or 100),
            }
            prepared.append(
                {
                    "asset_name": asset_name,
                    "suffix": suffix,
                    "original_name": lf.original_name,
                    **display,
                    "is_a3_image": (
                        suffix in IMAGE_SUFFIXES
                        and display.get("page_mode") == "a3landscape"
                    ),
                }
            )
            logs.append(f"{lf.original_name} → appendix/{asset_name}")
            if suffix in PDF_SUFFIXES:
                has_pdf = True

        if prepared:
            roles = _a3_roles_for_sequence([bool(p["is_a3_image"]) for p in prepared])
            slot_parts: list[str] = []
            for item, role in zip(prepared, roles):
                slot_parts.append(
                    _render_asset_body(
                        item["asset_name"],
                        item["suffix"],
                        page_mode=item.get("page_mode") or "",
                        rotate=int(item.get("rotate") or 0),
                        width_percent=int(item.get("width_percent") or 100),
                        a3_role=role if item["is_a3_image"] else "only",
                    )
                )
            active_slot_keys.add(upload.slot_key)
            wrapper_path = appendix_dir / f"{upload.slot_key}.tex"
            wrapper_path.write_text("".join(slot_parts), encoding="utf-8")
            logs.append(
                f"{upload.label} → appendix/{upload.slot_key}.tex（{len(slot_parts)} 个文件）"
            )

    for orphan in list(appendix_dir.iterdir()):
        if not orphan.is_file() or orphan.name == ".gitkeep":
            continue
        if orphan.suffix == ".tex":
            if orphan.stem not in active_slot_keys:
                orphan.unlink(missing_ok=True)
        elif orphan.name not in active_asset_names:
            orphan.unlink(missing_ok=True)

    return logs


def apply_attachments_to_latex(report: EvaluationReport, latex_dir: Path) -> list[str]:
    """同步 appendix/ 并在 13-appendix.tex 各 section 下 \\input 对应槽位 tex。"""
    logs = sync_appendix_assets(report, latex_dir)

    _copy_fresh_appendix_tex(report, latex_dir)
    appendix_tex = latex_dir / "chapters" / "13-appendix.tex"

    tex_content = appendix_tex.read_text(encoding="utf-8")
    has_pdf = False
    injected_slots = 0
    injected_files = 0

    uploads = (
        report.uploads.filter(slot_key__startswith="attachment_")
        .exclude(appendix_section="")
        .prefetch_related("files__library_file")
        .order_by("sort_order", "pk")
    )
    for upload in uploads:
        section_title = upload.appendix_section.strip()
        if not section_title:
            continue
        anchor_type = upload.appendix_anchor or "section"

        file_count = 0
        for uf in upload.ordered_files():
            lf = uf.library_file
            src = _library_file_path(lf)
            if not src:
                continue
            suffix = src.suffix.lower()
            if suffix not in PDF_SUFFIXES and suffix not in IMAGE_SUFFIXES:
                continue
            file_count += 1
            if suffix in PDF_SUFFIXES:
                has_pdf = True

        if file_count:
            tex_content = _inject_after_anchor(
                tex_content,
                section_title,
                anchor_type,
                _render_input_line(upload.slot_key),
            )
            injected_slots += 1
            injected_files += file_count

    appendix_tex.write_text(tex_content, encoding="utf-8")

    if has_pdf:
        ensure_pdfpages_package(latex_dir / "main.tex")

    if injected_slots:
        logs.insert(
            0,
            f"已在附录中 \\input 注入 {injected_slots} 个附件 tex（共 {injected_files} 个文件）",
        )
    elif not logs:
        logs.append("未上传附件，附录保持空 section")

    return logs
