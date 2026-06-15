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
from radiation_detection_report.report_data_builder import try_build_report_data

logger = logging.getLogger(__name__)

_QC_HEAD_RE = re.compile(
    r"(\d+)\s*[.．]\s*1\s*质量控制",
    re.I,
)
_RP_HEAD_RE = re.compile(
    r"(\d+)\s*[.．]\s*2\s*工作场所放射防护",
    re.I,
)
_NEXT_DEVICE_RE = re.compile(r"[（(]\s*2\s*[）)]")
_NEXT_MAJOR_RE = re.compile(r"^(\d+)\s*[.．]\s*1\s", re.M)


def _norm_compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _detect_qc_section_major(page_text: str) -> Optional[int]:
    compact = _norm_compact(page_text)
    m = _QC_HEAD_RE.search(compact)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _page_has_existing_rp_section(page_text: str, major: int) -> bool:
    compact = _norm_compact(page_text)
    return bool(re.search(rf"{major}\s*[.．]\s*2\s*工作场所放射防护", compact))


def find_radiation_table_insert_page_0based(doc: fitz.Document) -> tuple[Optional[int], int]:
    """
    在「三、检测结果」中定位 1.k 质量控制小节之后、下一小节之前的插入页（0-based）。
    返回 (insert_index, section_major)；无 QC 小节时 insert_index 为 None。
    """
    in_results = False
    qc_major: Optional[int] = None
    last_qc_page: Optional[int] = None

    for pi in range(doc.page_count):
        text = doc[pi].get_text("text")
        compact = _norm_compact(text)
        if "三、检测结果" in compact or "三.检测结果" in compact:
            in_results = True
        if not in_results:
            continue

        major = _detect_qc_section_major(text)
        if major is not None:
            qc_major = major
            last_qc_page = pi
            continue

        if last_qc_page is None or qc_major is None:
            continue

        if _page_has_existing_rp_section(text, qc_major):
            return None, qc_major

        if _NEXT_DEVICE_RE.search(text) and "受检编号" in compact:
            return pi, qc_major
        m_next = _NEXT_MAJOR_RE.search(text.strip())
        if m_next:
            try:
                nxt = int(m_next.group(1))
            except ValueError:
                nxt = 0
            if nxt > qc_major:
                return pi, qc_major
        if re.search(rf"{qc_major}\s*[.．]\s*2", compact):
            return pi, qc_major
        if re.search(r"^[三四五六七八九十]+、", text.strip()):
            return pi, qc_major

    if last_qc_page is not None and qc_major is not None:
        return last_qc_page + 1, qc_major
    return None, qc_major or 1


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
        from utils.pdf_merge import redact_yixia_kongbai_except_last_page

        redact_yixia_kongbai_except_last_page(doc)
    except Exception as exc:
        logger.warning("strip blank markers failed: %s", exc)


def build_radiation_table_pdf_bytes(
    report_data: Mapping[str, Any],
    *,
    section_major: int = 1,
    section_minor: int = 2,
) -> bytes:
    layout = load_layout(default_layout_path())
    title = f"{section_major}.{section_minor}工作场所放射防护检测结果"
    layout = replace(layout, section_title=title)
    table_doc = RadiationTableBuilder(layout, dict(report_data)).build()
    try:
        return table_doc.tobytes(deflate=True, garbage=4, clean=True)
    finally:
        table_doc.close()


def enrich_report_pdf_with_radiation_table(
    pdf_bytes: bytes,
    report_data: Mapping[str, Any],
    *,
    section_major: int = 1,
    section_minor: int = 2,
) -> bytes:
    if not pdf_bytes or not report_data or not report_data.get("points"):
        return pdf_bytes

    table_bytes = build_radiation_table_pdf_bytes(
        report_data,
        section_major=section_major,
        section_minor=section_minor,
    )
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    table_doc = fitz.open(stream=table_bytes, filetype="pdf")
    try:
        insert_at, major = find_radiation_table_insert_page_0based(doc)
        if insert_at is None:
            return pdf_bytes
        if table_doc.page_count <= 0:
            return pdf_bytes
        doc.insert_pdf(table_doc, start_at=insert_at, from_page=0, to_page=table_doc.page_count - 1)
        return doc.tobytes(deflate=True, garbage=4, clean=True)
    finally:
        table_doc.close()
        doc.close()


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
    if isinstance(source_payload, dict) and source_payload:
        site_template = _resolve_site_template_parsed(task_no, project, report_task)
        site_pdf = _resolve_site_pdf_path(case, project) if case is not None else None
        report_data = try_build_report_data(
            source_payload,
            site_template_parsed=site_template,
            site_pdf_path=site_pdf,
        )
        if report_data and report_data.get("points"):
            doc = fitz.open(stream=out, filetype="pdf")
            try:
                _insert_at, major = find_radiation_table_insert_page_0based(doc)
            finally:
                doc.close()
            enriched = enrich_report_pdf_with_radiation_table(
                out,
                report_data,
                section_major=major or 1,
                section_minor=2,
            )
            if enriched != out:
                has_radiation_protection = True
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
