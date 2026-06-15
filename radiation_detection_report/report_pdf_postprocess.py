"""
单份自动生成报告 PDF 后处理：封面/基本情况叠印、页眉目录重绘、签名栏清空。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

import fitz

logger = logging.getLogger(__name__)

_SIGNATURE_LABELS = (
    "编制人",
    "审核人",
    "授权签字人",
    "授权人签字",
    "签发日期",
    "报告签发日期",
)

_TOC_MAJOR_LINE_RE = re.compile(r"^([一二三四五六七八九十百千]+)[、.．]\s*(.+)$")
_TOC_MINOR_LINE_RE = re.compile(r"^(\d+)\s*[.．]\s*(\d+)\s+(.+)$")


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def build_single_report_overlay_from_payload(
    payload: Mapping[str, Any],
    *,
    project=None,
    report_task=None,
    task_no: str = "",
    inspection_case=None,
    manual_device_count: int | None = None,
    has_radiation_protection: bool = False,
) -> Dict[str, str]:
    """
    从现场记录/提交数据构建封面与基本情况叠印字段。
    与多份报告合并 ``build_merged_report_overlay_fields`` 保持同一套文案规则。
    """
    if not isinstance(payload, dict):
        return {}
    try:
        from apps.api.inspection_report_make import (
            _build_submit_derived_value_mapping,
            _resolve_inspected_unit_name_from_submit,
            _resolve_inspection_type_display,
            merged_report_commission_project_basename,
        )
        from utils.pdf_merge import (
            build_merged_report_overlay_fields,
            extract_modality_abbr_from_title,
            format_gan_jcy_institute_report_no,
        )
    except Exception:
        return {}

    derived = _build_submit_derived_value_mapping(
        dict(payload),
        project=project,
        task_no=task_no,
        task_obj=report_task,
        inspection_case=inspection_case,
        manual_device_count=manual_device_count,
    )
    hi = payload.get("hospitalInfo") if isinstance(payload.get("hospitalInfo"), dict) else {}
    ei = payload.get("equipmentInfo") if isinstance(payload.get("equipmentInfo"), dict) else {}
    ri = payload.get("reportInfo") if isinstance(payload.get("reportInfo"), dict) else {}

    org = _norm(
        derived.get("受检单位名称")
        or derived.get("受检单位")
        or _resolve_inspected_unit_name_from_submit(dict(payload))
        or hi.get("name")
        or hi.get("inspectedOrgName")
    )
    device = _norm(ei.get("deviceName") or ei.get("name") or derived.get("设备名称"))
    model = _norm(ei.get("model") or ei.get("deviceModel") or derived.get("设备型号"))
    report_template_name = _norm(getattr(report_task, "name", "") if report_task is not None else "")
    title_for_abbr = report_template_name or device or model or _norm(derived.get("项目名称"))

    cover_title_override: Optional[str] = None
    com = _norm(ri.get("commissionNo") or derived.get("委托编号") or derived.get("projectId"))
    if project is not None:
        mn = merged_report_commission_project_basename(project, com)
        if mn and mn != "合并报告":
            cover_title_override = mn

    fake_row = {
        "title_line": title_for_abbr,
        "inspected_org_hint": org,
    }
    report_date = _norm(derived.get("报告日期") or derived.get("检测日期") or derived.get("testDate"))
    overlay = build_merged_report_overlay_fields(
        [fake_row],
        merge_date_str=report_date,
        cover_title_override=cover_title_override,
    )
    if not overlay:
        ab = extract_modality_abbr_from_title(title_for_abbr) or (device or model or "设备")
        cover_title = f"{ab}1台设备质量控制检测"
        overlay = {
            "merge_date_str": report_date,
            "cover_org_line": org,
            "cover_report_title": cover_title,
            "cover_inspected_org": org,
            "inspection_index_line": "01",
            "project_name_combined": f"{org}{cover_title}".strip(),
            "device_count_label": "1 台",
            "evaluation_body": _norm(derived.get("评价")),
        }

    report_type = _norm(
        derived.get("检测类型")
        or _resolve_inspection_type_display(dict(payload), project)
        or ri.get("inspectionType")
        or ri.get("reportType")
    )

    overlay["cover_inspected_org"] = org
    overlay["cover_inspection_type"] = report_type
    overlay["report_no_display"] = format_gan_jcy_institute_report_no(
        com,
        has_radiation_protection=has_radiation_protection,
    )
    return overlay


def finalize_single_report_pdf(
    pdf_bytes: bytes,
    *,
    source_payload: Optional[Mapping[str, Any]] = None,
    project=None,
    case=None,
    report_task=None,
    task_no: str = "",
    manual_device_count: int | None = None,
    has_radiation_protection: bool = False,
) -> bytes:
    """
    在 HTMLPDF 回填及（可选）放射防护表插入之后执行：
    封面/基本情况叠印、签名栏清空、目录重绘、页眉页码统一。
    """
    if not pdf_bytes:
        return pdf_bytes
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        overlay = build_single_report_overlay_from_payload(
            source_payload or {},
            project=project,
            report_task=report_task,
            task_no=task_no,
            inspection_case=case,
            manual_device_count=manual_device_count,
            has_radiation_protection=has_radiation_protection,
        )
        from utils.pdf_merge import (
            apply_merged_report_merge_overlay,
            apply_single_report_cover_overlay_extras,
            clear_report_signature_fields,
            redact_yixia_kongbai_except_last_page,
            regenerate_single_report_table_of_contents,
            rewrite_merged_report_page_headers,
        )

        template_parsed = None
        if report_task is not None:
            try:
                from apps.api.inspection_report_make import _load_first_parsed_template_json_for_task

                template_parsed = _load_first_parsed_template_json_for_task(report_task)
            except Exception:
                template_parsed = None

        if overlay:
            apply_merged_report_merge_overlay(
                doc,
                header_page_count=3,
                overlay=overlay,
                template_parsed=template_parsed,
                use_global_fixed_rects=False,
            )
            apply_single_report_cover_overlay_extras(
                doc, overlay, template_parsed=template_parsed
            )
        clear_report_signature_fields(doc)
        report_no = _norm((overlay or {}).get("report_no_display")) or _extract_report_no_from_doc(doc)
        regenerate_single_report_table_of_contents(doc, report_no=report_no)
        toc_idx = _find_toc_page_index(doc)
        rewrite_merged_report_page_headers(doc, report_no=report_no or "报告", toc_page_0based=toc_idx)
        redact_yixia_kongbai_except_last_page(doc)
        return doc.tobytes(deflate=True, garbage=4, clean=True)
    finally:
        doc.close()


def postprocess_single_report_pdf(
    pdf_bytes: bytes,
    *,
    source_payload: Optional[Mapping[str, Any]] = None,
    project=None,
    case=None,
    report_task=None,
    task_no: str = "",
    manual_device_count: int | None = None,
    has_radiation_protection: bool = False,
) -> bytes:
    """兼容别名：等同 finalize_single_report_pdf。"""
    return finalize_single_report_pdf(
        pdf_bytes,
        source_payload=source_payload,
        project=project,
        case=case,
        report_task=report_task,
        task_no=task_no,
        manual_device_count=manual_device_count,
        has_radiation_protection=has_radiation_protection,
    )


def _extract_report_no_from_doc(doc: fitz.Document) -> str:
    try:
        from utils.pdf_merge import _extract_report_number_from_doc

        return _extract_report_number_from_doc(doc)
    except Exception:
        return ""


def _find_toc_page_index(doc: fitz.Document) -> Optional[int]:
    for pi in range(2, min(doc.page_count, 8)):
        compact = re.sub(r"\s+", "", doc[pi].get_text("text"))
        if "目录" in compact:
            return pi
    return None
