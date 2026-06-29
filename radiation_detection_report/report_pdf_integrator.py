"""
将「工作场所放射防护检测结果」表格页插入自动生成的报告 PDF，并重写页眉页码。
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any, Dict, Mapping, Optional, Sequence

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
# 动态 DR 等模板无「1.1」编号，仅用「摄影部分/透视部分…质量控制检测项目及结果」作小节标题
_QC_PLAIN_SECTION_RE = re.compile(
    r"(摄影部分|透视部分|透视摄影部分).*?质量控制(?:检测)?项目及结果",
    re.I,
)
_QC_TABLE_PAGE_RE = re.compile(r"序号.*?检测项目", re.I)
_RP_SUBSECTION_RE = re.compile(
    r"(\d{1,2})\s*[.．]\s*(\d+)\s*.*?工作场所放射防护",
    re.I,
)
_FLOOR_SUBSECTION_RE = re.compile(
    r"(\d{1,2})\s*[.．]\s*(\d+)\s*.*?平面布局",
    re.I,
)
_NEXT_DEVICE_RE = re.compile(r"[（(]\s*2\s*[）)]")
_NEXT_MAJOR_RE = re.compile(r"^(\d+)\s*[.．]\s*1\s", re.M)
_DEVICE_HEADER_RE = re.compile(r"[（(]\s*(\d+)\s*[）)]\s*受检编号")


def _norm_compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _page_is_qc_table_continuation(compact: str) -> bool:
    """质控结果表续页（无小节标题，含表头「序号/检测项目」）。"""
    if "质量控制" in compact and _QC_PLAIN_SECTION_RE.search(compact):
        return True
    return bool(_QC_TABLE_PAGE_RE.search(compact))


def _scan_results_qc_pages(doc: fitz.Document) -> tuple[Optional[int], int, int]:
    """
    在「三、检测结果」之后扫描质控小节页。
    返回 (末页 0-based, device_major, 末个小节号 minor)。
    支持编号小节（1.1…）与无编号「摄影部分/透视部分…质量控制」标题。
    """
    in_results = False
    device_major: Optional[int] = None
    max_qc_minor = 0
    last_qc_page: Optional[int] = None
    plain_section_count = 0

    for pi in range(doc.page_count):
        text = doc[pi].get_text("text")
        compact = _norm_compact(text)
        if _page_enters_results_section(compact):
            in_results = True
        if not in_results:
            continue

        if device_major is not None:
            for maj, _minor in _iter_rp_subsections(compact):
                if maj == device_major:
                    return last_qc_page, device_major, max_qc_minor

        numbered_on_page = False
        for maj, minor in _iter_qc_subsections(compact):
            numbered_on_page = True
            if device_major is None:
                device_major = maj
            if maj != device_major:
                continue
            if minor > max_qc_minor:
                max_qc_minor = minor
            last_qc_page = pi

        if not numbered_on_page and _QC_PLAIN_SECTION_RE.search(compact):
            plain_section_count += 1
            if device_major is None:
                device_major = 1
            if plain_section_count > max_qc_minor:
                max_qc_minor = plain_section_count
            last_qc_page = pi
        elif not numbered_on_page and last_qc_page is not None and _page_is_qc_table_continuation(compact):
            last_qc_page = pi

        if last_qc_page is not None and pi > last_qc_page:
            if re.search(r"^[三四五六七八九十]+、", text.strip()):
                break
            if _NEXT_DEVICE_RE.search(text) and "受检编号" in compact:
                break

    if device_major is None:
        device_major = 1
    return last_qc_page, device_major, max_qc_minor


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


def _page_enters_results_section(compact: str) -> bool:
    """进入「检测结果」区：标准第三节标题、受检设备头或编号质控小节（drct9 等无「三、检测结果」）。"""
    if "三、检测结果" in compact or "三.检测结果" in compact:
        return True
    if _DEVICE_HEADER_RE.search(compact):
        return True
    if _iter_qc_subsections(compact):
        return True
    if _QC_PLAIN_SECTION_RE.search(compact):
        return True
    return False


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


def _iter_floor_subsections(compact: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for m in _FLOOR_SUBSECTION_RE.finditer(compact):
        try:
            major = int(m.group(1))
            minor = int(m.group(2))
        except ValueError:
            continue
        if not (1 <= major <= 9 and 1 <= minor <= 30):
            continue
        out.append((major, minor))
    return out


def _page_has_rp_header(compact: str, device_major: int, rp_minor: int) -> bool:
    if "工作场所放射防护" not in compact:
        return False
    return any(
        maj == device_major and minor == rp_minor for maj, minor in _iter_rp_subsections(compact)
    )


def _page_has_floor_plan_header(compact: str, device_major: int, floor_minor: int) -> bool:
    if "平面布局" not in compact:
        return False
    return any(
        maj == device_major and minor == floor_minor
        for maj, minor in _iter_floor_subsections(compact)
    )


def _resolve_qc_followup_insert_anchor(doc: fitz.Document) -> tuple[Optional[int], int, int]:
    """
    质控末页之后插入防护/平面图：以编号小节扫描为准（避免「体层摄影…质量控制」误当 1.1）。
    返回 (insert_index_0based, device_major, rp_section_minor)。
    """
    last_qc_page, device_major, max_qc_minor = _scan_results_qc_pages(doc)
    major = int(device_major or 1)
    rp_minor = max(1, int(max_qc_minor or 0) + 1)
    if last_qc_page is None:
        return None, major, rp_minor
    return int(last_qc_page) + 1, major, rp_minor


def _remove_rp_and_floor_pages_wrong_major(doc: fitz.Document, expected_major: int) -> None:
    """删除 major 与质控小节不一致的预印/错误防护表、平面布局页（如质控 2.1 却残留 1.2）。"""
    exp = int(expected_major)
    to_delete: set[int] = set()
    from_pi = _find_results_section_start_page(doc)
    for pi in range(from_pi, doc.page_count):
        compact = _norm_compact(doc[pi].get_text("text"))
        for maj, _minor in _iter_rp_subsections(compact):
            if maj != exp and _page_has_rp_table_body(compact):
                to_delete.add(pi)
                break
        for maj, _minor in _iter_floor_subsections(compact):
            if maj != exp:
                to_delete.add(pi)
                break
    for pi in sorted(to_delete, reverse=True):
        if 0 <= pi < doc.page_count:
            doc.delete_page(pi)


def _find_results_section_start_page(doc: fitz.Document) -> int:
    for pi in range(doc.page_count):
        compact = _norm_compact(doc[pi].get_text("text"))
        if _page_enters_results_section(compact):
            return pi
    return 0


def _page_has_rp_table_body(compact: str) -> bool:
    return "检测点位置" in compact or ("检测点" in compact and "编号" in compact)


def _detect_device_rp_minor(doc: fitz.Document, device_major: int) -> int:
    """模板中设备 device_major 的首个工作场所放射防护小节号（如 1.1 → 1）。"""
    from_pi = _find_results_section_start_page(doc)
    for pi in range(from_pi, doc.page_count):
        compact = _norm_compact(doc[pi].get_text("text"))
        if not _page_has_rp_table_body(compact):
            continue
        for maj, minor in _iter_rp_subsections(compact):
            if maj == device_major and "工作场所放射防护" in compact:
                return minor
    return 1


def _page_is_rp_table_continuation(
    text: str,
    compact: str,
    device_major: int,
    rp_minor: int,
) -> bool:
    """防护表续页：含检测点表格正文，且不含下一小节标题。"""
    if _page_has_floor_plan_header(compact, device_major, rp_minor + 1):
        return False
    for maj, minor in _iter_rp_subsections(compact):
        if maj == device_major and minor != rp_minor:
            return False
        if maj > device_major:
            return False
    for maj, minor in _iter_floor_subsections(compact):
        if maj == device_major and minor > rp_minor:
            return False
        if maj > device_major:
            return False
    if _NEXT_DEVICE_RE.search(text) and "受检编号" in compact and device_major == 1:
        return False
    return (
        "检测点" in compact
        and ("检测点位置" in compact or "检测结果" in compact or "编号" in compact)
    )


def _find_device_rp_page_span(
    doc: fitz.Document,
    device_major: int,
    rp_minor: int,
) -> tuple[Optional[int], Optional[int]]:
    """返回设备防护表页范围 [start, end)（0-based）。"""
    start: Optional[int] = None
    end: Optional[int] = None
    from_pi = _find_results_section_start_page(doc)
    for pi in range(from_pi, doc.page_count):
        text = doc[pi].get_text("text")
        compact = _norm_compact(text)
        if start is None:
            if _page_has_rp_header(compact, device_major, rp_minor) and _page_has_rp_table_body(
                compact
            ):
                start = pi
                end = pi + 1
            continue
        if _page_has_floor_plan_header(compact, device_major, rp_minor + 1):
            break
        for maj, _minor in _iter_rp_subsections(compact):
            if maj > device_major:
                return start, end
            if maj == device_major and _minor != rp_minor:
                return start, end
        if _page_is_rp_table_continuation(text, compact, device_major, rp_minor):
            end = pi + 1
            continue
        break
    return start, end


def _find_device_floor_plan_page_span(
    doc: fitz.Document,
    device_major: int,
    floor_minor: int,
) -> tuple[Optional[int], Optional[int]]:
    """返回设备平面布局页范围 [start, end)（0-based）。"""
    start: Optional[int] = None
    end: Optional[int] = None
    from_pi = _find_results_section_start_page(doc)
    for pi in range(from_pi, doc.page_count):
        compact = _norm_compact(doc[pi].get_text("text"))
        if start is None:
            if _page_has_floor_plan_header(compact, device_major, floor_minor):
                start = pi
                end = pi + 1
            continue
        if _page_has_floor_plan_header(compact, device_major, floor_minor):
            end = pi + 1
            continue
        break
    return start, end


def _remove_template_device_rp_and_floor_plan_pages(
    doc: fitz.Document,
    device_major: int,
) -> tuple[Optional[int], Optional[int]]:
    """
    删除模板预印的「N.M 工作场所放射防护检测结果」及「N.(M+1) 平面布局」页组。
    返回 (insert_at, rp_minor)；无预印内容时返回 (None, None)。
    """
    rp_minor = _detect_device_rp_minor(doc, device_major)
    floor_minor = rp_minor + 1
    rp_start, rp_end = _find_device_rp_page_span(doc, device_major, rp_minor)
    fp_start, fp_end = _find_device_floor_plan_page_span(doc, device_major, floor_minor)

    to_delete: set[int] = set()
    if rp_start is not None and rp_end is not None:
        to_delete.update(range(rp_start, rp_end))
    if fp_start is not None and fp_end is not None:
        to_delete.update(range(fp_start, fp_end))
    if not to_delete:
        return None, None

    insert_at = min(to_delete)
    for pi in sorted(to_delete, reverse=True):
        doc.delete_page(pi)
    return insert_at, rp_minor


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
        if _page_enters_results_section(compact):
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

    return _resolve_qc_followup_insert_anchor(doc)


def _find_page_after_last_qc(doc: fitz.Document) -> Optional[int]:
    """当前受检设备末个质控小节所在页的下一页（0-based）。"""
    last_qc_page, _major, _minor = _scan_results_qc_pages(doc)
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
    return _resolve_site_template_parsed_at_index(task_no, project, report_task, 0)


def _resolve_site_template_for_submit(
    task_no: str,
    project,
    report_task,
    submit_payload: Optional[Mapping[str, Any]],
) -> Optional[dict]:
    """按现场提交 taskNo 匹配对应现场记录模板 JSON（勿按枚举序号硬对齐）。"""
    try:
        from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no
        from apps.api.inspection_report_make import _iter_site_record_parsed_templates_for_report

        site_task_no = str((submit_payload or {}).get("taskNo") or "").strip()
        site_task = (
            _resolve_library_task_for_task_no(site_task_no, project) if site_task_no else None
        )
        if site_task is not None:
            from apps.api.inspection_report_make import _load_first_parsed_template_json_for_task

            parsed_direct = _load_first_parsed_template_json_for_task(site_task)
            if isinstance(parsed_direct, dict):
                return parsed_direct
        for st, parsed in _iter_site_record_parsed_templates_for_report(
            task_no, project, report_task
        ):
            if site_task is not None and int(st.pk) == int(site_task.pk):
                return parsed if isinstance(parsed, dict) else None
            if site_task is None and site_task_no and (st.code or "").strip() == site_task_no:
                return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        logger.debug("resolve site template for submit: %s", exc)
    return _resolve_site_template_parsed_at_index(task_no, project, report_task, 0)


def _resolve_site_template_parsed_at_index(
    task_no: str, project, report_task, index: int,
) -> Optional[dict]:
    try:
        from apps.api.inspection_report_make import _iter_site_record_parsed_templates_for_report

        for i, (_st, parsed) in enumerate(
            _iter_site_record_parsed_templates_for_report(task_no, project, report_task)
        ):
            if i == int(index) and isinstance(parsed, dict):
                return parsed
    except Exception as exc:
        logger.debug("resolve site template at %s: %s", index, exc)
    return None


def _select_submit_payload_for_device_section(
    source_payload: Optional[Mapping[str, Any]],
    ordered_submit_payloads: Optional[Sequence[Mapping[str, Any]]],
    section_major: int,
) -> Optional[Mapping[str, Any]]:
    """多设备报告：按受检设备序号选取对应现场提交（避免合并 payload 串位）。"""
    if ordered_submit_payloads:
        idx = max(0, int(section_major) - 1)
        if idx < len(ordered_submit_payloads) and isinstance(ordered_submit_payloads[idx], dict):
            return ordered_submit_payloads[idx]
    return source_payload if isinstance(source_payload, dict) else None


def _resolve_site_pdf_path_for_submit(
    project,
    submit_payload: Optional[Mapping[str, Any]],
) -> Optional[str]:
    if not isinstance(submit_payload, dict) or project is None:
        return None
    try:
        from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no
        from apps.core.models import InspectionCase, LibraryFile
        from apps.core import pipeline_service

        task_no = str(submit_payload.get("taskNo") or "").strip()
        if not task_no:
            return None
        site_task = _resolve_library_task_for_task_no(task_no, project)
        if site_task is None:
            return None
        case = InspectionCase.objects.filter(
            library_project=project, case_no=task_no
        ).first()
        if case is None:
            return None
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
        logger.debug("resolve site pdf for submit: %s", exc)
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


# 嵌入报告 PDF 时预留页眉带（rewrite_merged_report_page_headers 约 46–82pt）
_REPORT_RP_EMBED_TITLE_Y_TOP = 76.0
_REPORT_RP_EMBED_TABLE_Y_TOP = 112.0
_REPORT_RP_EMBED_CONTINUATION_Y_TOP = 96.0
_REPORT_RP_EMBED_CONTENT_BOTTOM = 728.0


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
        title_y_top=_REPORT_RP_EMBED_TITLE_Y_TOP,
        table_y_top=_REPORT_RP_EMBED_TABLE_Y_TOP,
        continuation_y_top=_REPORT_RP_EMBED_CONTINUATION_Y_TOP,
        content_bottom=_REPORT_RP_EMBED_CONTENT_BOTTOM,
    )
    table_doc = RadiationTableBuilder(layout, dict(report_data)).build()
    try:
        return table_doc.tobytes(deflate=True, garbage=4, clean=True)
    finally:
        table_doc.close()


def _insert_radiation_table_at(
    doc: fitz.Document,
    *,
    insert_at: int,
    device_major: int,
    minor: int,
    report_data: Optional[Mapping[str, Any]],
    source_payload: Optional[Mapping[str, Any]],
    ordered_submit_payloads: Optional[Sequence[Mapping[str, Any]]],
    site_template_parsed: Optional[dict],
    task_no: str,
    project,
    report_task,
) -> tuple[Optional[int], int]:
    """在 insert_at 插入防护表，返回 (cursor, floor_minor)。"""
    section_payload = _select_submit_payload_for_device_section(
        source_payload, ordered_submit_payloads, device_major
    )
    section_template = (
        _resolve_site_template_for_submit(task_no, project, report_task, section_payload)
        if task_no and project is not None
        else site_template_parsed
    ) or site_template_parsed
    section_site_pdf = _resolve_site_pdf_path_for_submit(project, section_payload)
    section_report_data: Optional[Dict[str, Any]] = None
    if isinstance(section_payload, dict):
        built = try_build_report_data(
            section_payload,
            site_template_parsed=section_template,
            site_pdf_path=section_site_pdf,
        )
        if isinstance(built, dict) and built.get("points"):
            section_report_data = built
    if section_report_data is None:
        section_report_data = report_data if isinstance(report_data, dict) else {}
    table_bytes = build_radiation_table_pdf_bytes(
        section_report_data,
        section_major=device_major,
        section_minor=minor,
    )
    table_doc = fitz.open(stream=table_bytes, filetype="pdf")
    try:
        if table_doc.page_count <= 0:
            return None, minor + 1
        doc.insert_pdf(
            table_doc,
            start_at=insert_at,
            from_page=0,
            to_page=table_doc.page_count - 1,
        )
        return insert_at + table_doc.page_count, minor + 1
    finally:
        table_doc.close()


def enrich_report_pdf_with_qc_followups(
    pdf_bytes: bytes,
    *,
    report_data: Optional[Mapping[str, Any]] = None,
    source_payload: Optional[Mapping[str, Any]] = None,
    site_template_parsed: Optional[dict] = None,
    ordered_submit_payloads: Optional[Sequence[Mapping[str, Any]]] = None,
    task_no: str = "",
    project=None,
    report_task=None,
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
        insert_at, major, minor = find_radiation_table_insert_page_0based(doc)
        device_major = major or 1
        _remove_rp_and_floor_pages_wrong_major(doc, device_major)
        insert_at, major, minor = _resolve_qc_followup_insert_anchor(doc)
        device_major = major or 1
        minor = minor or 2

        removed_insert, removed_minor = _remove_template_device_rp_and_floor_plan_pages(
            doc, device_major
        )
        template_replaced = removed_insert is not None
        if template_replaced:
            insert_at = removed_insert
            minor = removed_minor or minor or 1
        if insert_at is None:
            insert_at, device_major, minor = _resolve_qc_followup_insert_anchor(doc)
            device_major = device_major or major or 1

        cursor: Optional[int] = None
        floor_minor = (minor or 1) + 1

        if has_radiation and (insert_at is not None or template_replaced):
            cursor, floor_minor = _insert_radiation_table_at(
                doc,
                insert_at=insert_at if insert_at is not None else removed_insert,
                device_major=device_major,
                minor=minor or 1,
                report_data=report_data,
                source_payload=source_payload,
                ordered_submit_payloads=ordered_submit_payloads,
                site_template_parsed=site_template_parsed,
                task_no=task_no,
                project=project,
                report_task=report_task,
            )
        elif has_radiation and insert_at is None:
            after_qc = _find_page_after_last_qc(doc)
            if after_qc is not None:
                cursor, floor_minor = _insert_radiation_table_at(
                    doc,
                    insert_at=after_qc,
                    device_major=device_major,
                    minor=minor or 1,
                    report_data=report_data,
                    source_payload=source_payload,
                    ordered_submit_payloads=ordered_submit_payloads,
                    site_template_parsed=site_template_parsed,
                    task_no=task_no,
                    project=project,
                    report_task=report_task,
                )
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
                _select_submit_payload_for_device_section(
                    source_payload, ordered_submit_payloads, device_major
                ) or source_payload,
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
    ordered_submit_payloads: Optional[Sequence[Mapping[str, Any]]] = None,
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
            ordered_submit_payloads=ordered_submit_payloads,
            task_no=task_no,
            project=project,
            report_task=report_task,
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
