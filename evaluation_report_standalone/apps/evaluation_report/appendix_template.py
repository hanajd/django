"""从 LaTeX 模板 chapters/13-appendix.tex 解析附件 section 清单。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from .constants import REPORT_TEMPLATES, UploadSlotDef
from .models import EvaluationReport

APPENDIX_REL_PATH = Path("chapters") / "13-appendix.tex"
SECTION_RE = re.compile(r"\\section\s*\{((?:[^{}]|\\.)*)\}\s*", re.MULTILINE)


@dataclass(frozen=True)
class AppendixSection:
    title: str
    anchor: str = "section"
    order: int = 0


def appendix_tex_path_for_template(template_key: str) -> Path:
    from .latex_template_service import template_storage_root

    root = template_storage_root(template_key)
    path = root / APPENDIX_REL_PATH
    if not path.is_file():
        raise FileNotFoundError(f"附录模板不存在：{path}")
    return path


def parse_appendix_sections(tex: str) -> list[AppendixSection]:
    """按出现顺序解析 \\section{标题}。"""
    sections: list[AppendixSection] = []
    for index, match in enumerate(SECTION_RE.finditer(tex)):
        title = match.group(1).strip()
        if not title:
            continue
        sections.append(AppendixSection(title=title, order=index))
    return sections


def appendix_sections_for_template(template_key: str) -> list[AppendixSection]:
    path = appendix_tex_path_for_template(template_key)
    return parse_appendix_sections(path.read_text(encoding="utf-8"))


def attachment_slot_key(index: int) -> str:
    return f"attachment_{index:02d}"


def upload_slot_defs_for_report(report: EvaluationReport) -> tuple[UploadSlotDef, ...]:
    """评价信息表 + 模板 appendix 中各 section 对应附件槽位。"""
    slots: list[UploadSlotDef] = [
        UploadSlotDef(
            "evaluation_form",
            "评价信息表",
            "evaluation_form",
            required=False,
        ),
    ]
    try:
        sections = appendix_sections_for_template(report.latex_template_key)
    except (ValueError, FileNotFoundError):
        return tuple(slots)

    for index, section in enumerate(sections, start=1):
        key = attachment_slot_key(index)
        slots.append(
            UploadSlotDef(
                key,
                f"附件 {index} {section.title}",
                "attachment",
                appendix_section=section.title,
                appendix_anchor=section.anchor,
            )
        )
    return tuple(slots)
