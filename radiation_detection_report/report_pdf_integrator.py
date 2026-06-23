"""
将「工作场所放射防护检测结果」表格页插入自动生成的报告 PDF，并重写页眉页码。
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any, Dict, Mapping, Optional

import fitz

from radiation_detection_report.generator import RadiationTableBuilder, default_layout_path, load_layout
from radiation_detection_report.report_data_builder import (
    report_data_has_radiation_points,
    resolve_has_radiation_protection_from_submit,
    try_build_report_data,
)

logger = logging.getLogger(__name__)

_QC_SUBSECTION_RE = re.compile(
    r"(\d{1,2})\s*[.．]\s*(\d+)\s*.*?质量控制",
    re.I,
)
_RP_SUBSECTION_RE = re.compile(
    r"(\d{1,2})\s*[.．]\s*(\d+)\s*.*?工作场所放射防护",
    re.I,
)
_NEXT_DEVICE_RE = re.compile(r"[（(]\s*2\s*[）)]")
_NEXT_MAJOR_RE = re.compile(r"^(\d+)\s*[.．]\s*1\s", re.M)


def _norm_compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _iter_qc_subsections(compact: str) -> list[tuple[int, int]]:
    """解析页内「n.k …质量控制」小节号；过滤表格数字误匹配。"""
    out: list[tuple[int, int]] = []
    for m in _QC_SUBSECTION_RE.finditer(compact):
        try:
            major = int(m.group(1))
            minor = int(m.group(2))
        except ValueError:
            continue
        if not (1 <= major <= 9 and 1 <= minor <= 30):
            continue
        out.append((major, minor))
    return out


def _iter_rp_subsections(compact: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for m in _RP_SUBSECTION_RE.finditer(compact):
        try:
            major = int(m.group(1))
            minor = int(m.group(2))
        except ValueError:
            continue
        if not (1 <= major <= 9 and 1 <= minor <= 30):
            continue
        out.append((major, minor))
    return out


def find_radiation_table_insert_page_0based(doc: fitz.Document) -> tuple[Optional[int], int, int]:
    """
    在「三、检测结果」中，于**当前受检设备最后一个质量控制小节**之后插入防护表。
    返回 (insert_index, section_major, section_minor)；
    section_minor = 末个质控小节号 + 1（如 DSA 1.1–1.3 后为 1.4）。
    无质控小节时 insert_index 为 None。
    """
    in_results = False
    device_major: Optional[int] = None
    max_qc_minor = 0
    last_qc_page: Optional[int] = None

    for pi in range(doc.page_count):
        text = doc[pi].get_text("text")
        compact = _norm_compact(text)
        if "三、检测结果" in compact or "三.检测结果" in compact:
            in_results = True
        if not in_results:
            continue

        if device_major is not None:
            for maj, _minor in _iter_rp_subsections(compact):
                if maj == device_major:
                    return None, device_major, _minor

        for maj, minor in _iter_qc_subsections(compact):
            if device_major is None:
                device_major = maj
            if maj != device_major:
                continue
            if minor > max_qc_minor:
                max_qc_minor = minor
                last_qc_page = pi
            elif minor == max_qc_minor:
                last_qc_page = pi

        if device_major is None or last_qc_page is None:
            continue

        if pi <= last_qc_page:
            continue

        if _NEXT_DEVICE_RE.search(text) and "受检编号" in compact:
            return pi, device_major, max_qc_minor + 1
        for maj, _minor in _iter_qc_subsections(compact):
            if maj > device_major:
                return pi, device_major, max_qc_minor + 1
        m_next = _NEXT_MAJOR_RE.search(text.strip())
        if m_next:
            try:
                nxt = int(m_next.group(1))
            except ValueError:
                nxt = 0
            if nxt > device_major:
                return pi, device_major, max_qc_minor + 1
        if re.search(r"^[三四五六七八九十]+、", text.strip()):
            return pi, device_major, max_qc_minor + 1

    if last_qc_page is not None and device_major is not None:
        return last_qc_page + 1, device_major, max_qc_minor + 1
    return None, device_major or 1, max_qc_minor + 1 if max_qc_minor else 2


def _find_page_after_last_qc(doc: fitz.Document) -> Optional[int]:
    """当前受检设备末个质控小节所在页的下一页（0-based）。"""
    in_results = False
    device_major: Optional[int] = None
    max_qc_minor = 0
    last_qc_page: Optional[int] = None
    for pi in range(doc.page_count):
        text = doc[pi].get_text("text")
        compact = _norm_compact(text)
        if "三、检测结果" in compact or "三.检测结果" in compact:
            in_results = True
        if not in_results:
            continue
        for maj, minor in _iter_qc_subsections(compact):
            if device_major is None:
                device_major = maj
            if maj != device_major:
                continue
            if minor >= max_qc_minor:
                max_qc_minor = minor
                last_qc_page = pi
    if last_qc_page is not None:
        return last_qc_page + 1
    return None


def _find_page_after_existing_rp_section(doc: fitz.Document, device_major: int) -> Optional[int]:
    """文档中已有工作场所放射防护小节时，返回其末页下一页（0-based）。"""
    last_rp_page: Optional[int] = None
    for pi in range(doc.page_count):
        compact = _norm_compact(doc[pi].get_text("text"))
        for maj, _minor in _iter_rp_subsections(compact):
            if maj == device_major:
                last_rp_page = pi
    if last_rp_page is not None:
        return last_rp_page + 1
    return None


def _resolve_site_template_parsed(task_no: str, project, report_task) -> Optional[dict]:
    try:
        from apps.api.inspection_report_make import _iter_site_record_parsed_templates_for_report

        for _st, parsed in _iter_site_record_parsed_templates_for_report(task_no, project, report_task):
            if isinstance(parsed, dict):
                return parsed
    except Exception as exc:
        logger.debug("resolve site template: %s", exc)
    return None


def _resolve_site_pdf_path(case, project) -> Optional[str]:
    try:
        from apps.core.models import LibraryFile
        from apps.core import pipeline_service

        lf = (
            LibraryFile.objects.filter(
                category=LibraryFile.CATEGORY_SITE_RECORD,
                link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
                link_object_id=case.pk,
                projects=project,
            )
            .filter(original_name__iendswith=".pdf")
            .order_by("-created_at", "-id")
            .first()
        )
        if lf is None:
            return None
        path = pipeline_service.library_absolute_path(lf.relative_path)
        return str(path) if path and path.is_file() else None
    except Exception as exc:
        logger.debug("resolve site pdf: %s", exc)
        return None


def _extract_report_no(doc: fitz.Document) -> str:
    try:
        from utils.pdf_merge import _extract_report_number_from_pdf_bytes

        return _extract_report_number_from_pdf_bytes(doc.tobytes())
    except Exception:
        pass
    for i in range(min(4, doc.page_count)):
        t = doc[i].get_text("text")
        m = re.search(r"报告编号\s*[：:]\s*([^\r\n]+)", t)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def _rewrite_report_page_headers(doc: fitz.Document, *, report_no: str) -> None:
    try:
        from utils.pdf_merge import rewrite_merged_report_page_headers

        toc_idx: Optional[int] = None
        for pi in range(doc.page_count):
            t = doc[pi].get_text("text")
            if "目" in t and "录" in t and "目录" in re.sub(r"\s+", "", t):
                toc_idx = pi
                break
        rewrite_merged_report_page_headers(doc, report_no=report_no or "报告", toc_page_0based=toc_idx)
    except Exception as exc:
        logger.warning("rewrite report headers failed: %s", exc)


def _strip_blank_markers_except_last(doc: fitz.Document) -> None:
    try:
        from utils.pdf_merge import (
            ensure_last_page_yixia_kongbai_marker,
            redact_yixia_kongbai_except_last_page,
        )

        redact_yixia_kongbai_except_last_page(doc)
        ensure_last_page_yixia_kongbai_marker(doc)
    except Exception as exc:
        logger.warning("strip blank markers failed: %s", exc)


def build_radiation_table_pdf_bytes(
    report_data: Mapping[str, Any],
    *,
    section_major: int = 1,
    section_minor: int = 2,
) -> bytes:
    layout = load_layout(default_layout_path())
    title = f"{section_major}.{section_minor} 工作场所放射防护检测结果"
    layout = replace(
        layout,
        section_title=title,
        title_y_top=float(layout.title_y_top) + 4.0,
        table_y_top=float(layout.table_y_top) + 4.0,
        continuation_y_top=float(layout.continuation_y_top) + 4.0,
    )
    table_doc = RadiationTableBuilder(layout, dict(report_data)).build()
    try:
        return table_doc.tobytes(deflate=True, garbage=4, clean=True)
    finally:
        table_doc.close()


def _doc_has_floor_plan_section(doc: fitz.Document) -> bool:
    for pi in range(doc.page_count):
        compact = _norm_compact(doc[pi].get_text("text"))
        if "平面布局及检测点方位图" in compact:
            return True
    return False


def enrich_report_pdf_with_qc_followups(
    pdf_bytes: bytes,
    *,
    report_data: Optional[Mapping[str, Any]] = None,
    source_payload: Optional[Mapping[str, Any]] = None,
    site_template_parsed: Optional[dict] = None,
) -> bytes:
    """
    在末个质控/防护小节后插入放射防护表（若有）与平面布局节。
    平面布局及检测点方位图仅在工作场所放射防护章节（有效防护点位）存在时生成。
    """
    if not pdf_bytes:
        return pdf_bytes

    from radiation_detection_report.floor_plan_page_builder import (
        build_floor_plan_page_pdf_bytes,
        count_existing_figure_numbers,
        extract_floor_plan_image_bytes,
        resolve_equipment_venue,
    )

    has_radiation = report_data_has_radiation_points(report_data)
    if isinstance(source_payload, dict):
        has_radiation = resolve_has_radiation_protection_from_submit(
            source_payload,
            report_data if has_radiation else None,
        )
    include_floor_plan = has_radiation
    if not has_radiation:
        return pdf_bytes

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if _doc_has_floor_plan_section(doc):
            return pdf_bytes

        insert_at, major, minor = find_radiation_table_insert_page_0based(doc)
        device_major = major or 1
        cursor: Optional[int] = None
        floor_minor = (minor or 1) + 1

        if has_radiation:
            if insert_at is not None:
                table_bytes = build_radiation_table_pdf_bytes(
                    report_data,
                    section_major=device_major,
                    section_minor=minor,
                )
                table_doc = fitz.open(stream=table_bytes, filetype="pdf")
                try:
                    if table_doc.page_count > 0:
                        doc.insert_pdf(
                            table_doc,
                            start_at=insert_at,
                            from_page=0,
                            to_page=table_doc.page_count - 1,
                        )
                        cursor = insert_at + table_doc.page_count
                        floor_minor = minor + 1
                finally:
                    table_doc.close()
            else:
                after_rp = _find_page_after_existing_rp_section(doc, device_major)
                cursor = after_rp
                floor_minor = (minor or 1) + 1

        if include_floor_plan:
            if cursor is None:
                after_rp = _find_page_after_existing_rp_section(doc, device_major)
                after_qc = _find_page_after_last_qc(doc)
                cursor = after_rp or after_qc or insert_at or doc.page_count
                if after_rp is not None:
                    floor_minor = (minor or 1) + 1
                elif insert_at is not None:
                    floor_minor = (minor or 1) + (1 if has_radiation else 0)
                else:
                    floor_minor = (minor or 1) + (2 if has_radiation else 1)

            image_bytes = extract_floor_plan_image_bytes(
                source_payload,
                site_template_parsed=site_template_parsed,
            )
            fig_no = count_existing_figure_numbers(doc) + 1
            venue = resolve_equipment_venue(source_payload)
            floor_bytes = build_floor_plan_page_pdf_bytes(
                image_bytes,
                section_major=device_major,
                section_minor=floor_minor,
                venue=venue,
                figure_no=fig_no,
            )
            if floor_bytes:
                floor_doc = fitz.open(stream=floor_bytes, filetype="pdf")
                try:
                    doc.insert_pdf(
                        floor_doc,
                        start_at=cursor,
                        from_page=0,
                        to_page=floor_doc.page_count - 1,
                    )
                finally:
                    floor_doc.close()

        return doc.tobytes(deflate=True, garbage=4, clean=True)
    except Exception as exc:
        logger.warning("insert qc followup pages failed: %s", exc)
        return pdf_bytes
    finally:
        doc.close()


def enrich_report_pdf_with_radiation_table(
    pdf_bytes: bytes,
    report_data: Mapping[str, Any],
    *,
    section_major: int = 1,
    section_minor: int = 2,
    source_payload: Optional[Mapping[str, Any]] = None,
    site_template_parsed: Optional[dict] = None,
) -> bytes:
    if not pdf_bytes or not report_data or not report_data.get("points"):
        return pdf_bytes
    return enrich_report_pdf_with_qc_followups(
        pdf_bytes,
        report_data=report_data,
        source_payload=source_payload,
        site_template_parsed=site_template_parsed,
    )


def probe_has_radiation_protection_from_submit(
    source_payload: Optional[Mapping[str, Any]],
    *,
    project=None,
    case=None,
    report_task=None,
    task_no: str = "",
) -> bool:
    """
    判断现场记录/提交是否含第五章工作场所放射防护数据（用于 FJ-FH 编号与防护表插入）。
    与 try_enrich_report_pdf_with_radiation_table 使用同一套 try_build_report_data 规则。
    """
    if not isinstance(source_payload, dict) or not source_payload:
        return False
    site_template = _resolve_site_template_parsed(task_no, project, report_task)
    site_pdf = _resolve_site_pdf_path(case, project) if case is not None else None
    report_data = try_build_report_data(
        source_payload,
        site_template_parsed=site_template,
        site_pdf_path=site_pdf,
    )
    return resolve_has_radiation_protection_from_submit(source_payload, report_data)


def try_enrich_report_pdf_with_radiation_table(
    pdf_bytes: bytes,
    *,
    source_payload: Optional[Mapping[str, Any]] = None,
    project=None,
    case=None,
    report_task=None,
    task_no: str = "",
    manual_device_count: int | None = None,
) -> bytes:
    """插入放射防护表（若有第五章数据），并完成封面/目录/页眉等终稿后处理。"""
    if not pdf_bytes:
        return pdf_bytes

    out = pdf_bytes
    has_radiation_protection = False
    report_data: Optional[Dict[str, Any]] = None
    site_template: Optional[dict] = None
    if isinstance(source_payload, dict) and source_payload:
        site_template = _resolve_site_template_parsed(task_no, project, report_task)
        site_pdf = _resolve_site_pdf_path(case, project) if case is not None else None
        report_data = try_build_report_data(
            source_payload,
            site_template_parsed=site_template,
            site_pdf_path=site_pdf,
        )
        has_radiation_protection = resolve_has_radiation_protection_from_submit(
            source_payload,
            report_data,
        )
        enriched = enrich_report_pdf_with_qc_followups(
            out,
            report_data=report_data if has_radiation_protection else None,
            source_payload=source_payload,
            site_template_parsed=site_template,
        )
        if enriched != out:
            out = enriched

    from radiation_detection_report.report_pdf_postprocess import finalize_single_report_pdf

    return finalize_single_report_pdf(
        out,
        source_payload=source_payload if isinstance(source_payload, dict) else None,
        project=project,
        case=case,
        report_task=report_task,
        task_no=task_no,
        manual_device_count=manual_device_count,
        has_radiation_protection=has_radiation_protection,
    )
