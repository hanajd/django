"""
单份自动报告 PDF 终稿后处理：封面/基本情况叠印、目录与页眉统一。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Mapping, Optional, Sequence

import fitz

logger = logging.getLogger(__name__)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def _parse_loose_date_for_overlay(raw: Any) -> str:
    """将 ISO/常见日期串格式化为「YYYY年MM月DD日」供封面叠印。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y}年{mo:02d}月{d:02d}日"
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return s
    return ""


def _verdict_key_looks_like_fail(key: str, value: str) -> bool:
    k = str(key or "")
    v = (value or "").strip()
    if not v or "不合格" not in v:
        return False
    if "单项判定" in k or k.endswith("_判定") or k.endswith("判定"):
        return True
    if re.search(r"(?i)verdict", k):
        return True
    kl = k.lower()
    if kl in ("conclusiontext", "结论", "检测结论") or "conclusion" in kl:
        return True
    return v in ("不合格", "不符合", "未通过")


def _submit_conclusion_indicates_fail(payload: Mapping[str, Any] | dict) -> bool:
    if not isinstance(payload, dict):
        return False
    c = payload.get("conclusion")
    if isinstance(c, dict):
        if c.get("allPassed") is False:
            return True
        if "不合格" in str(c.get("conclusionText") or ""):
            return True
    return False


def _scan_payload_for_fail_verdict(node: Any, key_path: str = "") -> bool:
    if isinstance(node, dict):
        for k, v in node.items():
            sk = f"{key_path}.{k}" if key_path else str(k)
            if isinstance(v, str) and _verdict_key_looks_like_fail(str(k), v):
                return True
            if _scan_payload_for_fail_verdict(v, sk):
                return True
        return False
    if isinstance(node, list):
        return any(_scan_payload_for_fail_verdict(item, key_path) for item in node)
    return False


def probe_submit_has_fail_verdict(
    source_payload: Optional[Mapping[str, Any]],
    *,
    project=None,
    case=None,
    report_task=None,
    task_no: str = "",
) -> bool:
    """现场记录/提交中是否存在单项判定「不合格」。"""
    if not isinstance(source_payload, dict) or not source_payload:
        return False
    if _submit_conclusion_indicates_fail(source_payload):
        return True
    for bucket in (
        "dynamicData",
        "testResult",
        "hospitalInfo",
        "equipmentInfo",
        "reportInfo",
        "rawPayload",
    ):
        if _scan_payload_for_fail_verdict(source_payload.get(bucket)):
            return True
    if _scan_payload_for_fail_verdict(source_payload):
        return True
    try:
        from radiation_detection_report.report_pdf_integrator import (
            _resolve_site_pdf_path,
            _resolve_site_template_parsed,
        )
        from radiation_detection_report.report_data_builder import try_build_report_data
        from radiation_detection_report.report_evaluation import apply_report_evaluations

        site_template = _resolve_site_template_parsed(task_no, project, report_task)
        site_pdf = _resolve_site_pdf_path(case, project) if case is not None else None
        report_data = try_build_report_data(
            source_payload,
            site_template_parsed=site_template,
            site_pdf_path=site_pdf,
        )
        if report_data and report_data.get("points"):
            evaluated = apply_report_evaluations(report_data)
            for pt in evaluated.get("points") or []:
                if not isinstance(pt, dict):
                    continue
                if str(pt.get("evaluation") or "").strip() == "不合格":
                    return True
                for sub in pt.get("sub_rows") or []:
                    if isinstance(sub, dict) and str(sub.get("evaluation") or "").strip() == "不合格":
                        return True
    except Exception:
        pass
    return False


def resolve_report_overlay_radiation_protection(
    report_task,
    probe_has_radiation: bool,
) -> bool:
    """
    封面/基本情况叠印：提交含有效第五章防护点位时走 FJ-FH 与防护评价；
    无防护点位时走 FJ-ZK 与质控评价（与防护表是否插入同一套 probe 规则）。
    """
    del report_task
    return bool(probe_has_radiation)


def report_task_is_qc_performance_overlay(report_task) -> bool:
    """质控/状态/验收类报告（相对纯防护报告）：评价与封面标题可写「质控及防护」组合句。"""
    nm = f"{getattr(report_task, 'name', '') or ''} {getattr(report_task, 'code', '') or ''}"
    if any(x in nm for x in ("防护", "放射防护", "FJ-FH")):
        return False
    return any(x in nm for x in ("质量控制", "性能", "状态检测", "状态", "验收", "质控"))


def build_single_report_overlay_from_payload(
    payload: Mapping[str, Any],
    *,
    project=None,
    report_task=None,
    task_no: str = "",
    inspection_case=None,
    manual_device_count: int | None = None,
    has_radiation_protection: bool = False,
    include_qc_content: bool = True,
) -> Dict[str, str]:
    """
    从现场记录/提交数据构建封面与基本情况叠印字段。
    单份报告：封面项目名称为两行（受检单位 + 报告名称），非合并报告的多设备命名。

    include_qc_content=False：仅防护结果册——项目名称/评价不含「质量控制检测」。
    has_radiation_protection=False：无防护结果册——不含「工作场所放射防护检测」。
    """
    if not isinstance(payload, dict):
        return {}
    has_radiation_protection = resolve_report_overlay_radiation_protection(
        report_task, has_radiation_protection
    )
    try:
        from apps.api.inspection_report_make import (
            _build_submit_derived_value_mapping,
            _is_plausible_inspected_unit_name,
            _resolve_inspected_unit_name_from_submit,
            _resolve_inspection_type_display,
        )
        from radiation_detection_report.report_pdf_integrator import _resolve_site_template_parsed
        from utils.pdf_merge import (
            build_single_report_cover_title_line,
            build_single_report_evaluation_body,
            build_single_report_toc_device_title,
            extract_modality_abbr_from_title,
            format_cover_report_number_line,
            format_gan_jcy_institute_report_no,
            format_qc_detection_for_evaluation,
            merged_device_phrase_for_evaluation,
        )
        from apps.api.inspection_pdf_service import resolve_report_device_count_for_project
        from apps.core.project_equipment_service import (
            effective_report_task,
            normalize_equipment_report_task,
            project_equipment_queryset,
        )
    except Exception:
        return {}

    site_template = _resolve_site_template_parsed(task_no, project, report_task)
    derived = _build_submit_derived_value_mapping(
        dict(payload),
        project=project,
        task_no=task_no,
        task_obj=report_task,
        inspection_case=inspection_case,
        manual_device_count=manual_device_count,
        has_radiation_protection=has_radiation_protection,
        site_template_parsed=site_template,
    )
    hi = payload.get("hospitalInfo") if isinstance(payload.get("hospitalInfo"), dict) else {}
    ei = payload.get("equipmentInfo") if isinstance(payload.get("equipmentInfo"), dict) else {}
    ri = payload.get("reportInfo") if isinstance(payload.get("reportInfo"), dict) else {}
    dd = payload.get("dynamicData") if isinstance(payload.get("dynamicData"), dict) else {}

    org = _norm(
        dd.get("受检单位")
        or dd.get("受检单位名称")
        or derived.get("受检单位名称")
        or derived.get("受检单位")
        or _resolve_inspected_unit_name_from_submit(dict(payload), site_template_parsed=site_template)
        or hi.get("name")
        or hi.get("inspectedOrgName")
        or hi.get("f6")
        or hi.get("f7")
    )
    if org and not _is_plausible_inspected_unit_name(org):
        org = _norm(
            _resolve_inspected_unit_name_from_submit(dict(payload), site_template_parsed=site_template)
            or derived.get("受检单位名称")
            or derived.get("受检单位")
        )
    from apps.api.inspection_report_make import _pick_ei_value_by_site_field_semantics

    device = _norm(
        _pick_ei_value_by_site_field_semantics(ei, site_template, "设备名称")
        or ei.get("f12")
        or ei.get("deviceName")
        or ei.get("name")
        or derived.get("设备名称")
    )
    model = _norm(
        _pick_ei_value_by_site_field_semantics(ei, site_template, "设备型号")
        or ei.get("f13")
        or ei.get("model")
        or ei.get("deviceModel")
        or derived.get("设备型号")
    )
    report_template_name = _norm(getattr(report_task, "name", "") if report_task is not None else "")

    device_type_label = ""
    rt_norm = normalize_equipment_report_task(report_task) if report_task is not None else None
    if project is not None and rt_norm is not None:
        for link in project_equipment_queryset(project):
            lt = normalize_equipment_report_task(effective_report_task(link))
            if lt is None or int(lt.pk) != int(rt_norm.pk):
                continue
            eq = link.equipment
            device_type_label = _norm(getattr(eq, "device_type", None) or "")
            if device_type_label:
                break

    title_for_abbr = device_type_label or device or model or report_template_name
    ab = extract_modality_abbr_from_title(title_for_abbr) or device_type_label or (device or model or "设备")
    qc_task = bool(include_qc_content) and report_task_is_qc_performance_overlay(report_task)
    qc_with_rp = bool(has_radiation_protection) and qc_task
    cover_title = build_single_report_cover_title_line(
        ab,
        has_radiation_protection=has_radiation_protection,
        qc_with_radiation_protection=qc_with_rp,
    )
    toc_device_title = build_single_report_toc_device_title(device, ab, model)
    project_name_combined = f"{org}{cover_title}".strip()

    report_date = _norm(derived.get("报告日期") or derived.get("检测日期") or derived.get("testDate"))
    if not report_date:
        for raw_dt in (dd.get("f606"), dd.get("f607"), ri.get("testDate")):
            d = _parse_loose_date_for_overlay(raw_dt)
            if d:
                report_date = d
                break
    report_type = _norm(
        derived.get("检测类型")
        or _resolve_inspection_type_display(dict(payload), project, report_task=report_task)
        or ri.get("inspectionType")
        or ri.get("reportType")
    )
    com = _norm(
        derived.get("commissionNo")
        or derived.get("委托编号")
        or ri.get("commissionNo")
        or ri.get("f1")
        or derived.get("projectId")
        or dd.get("f1")
    )
    addr = _norm(
        derived.get("受检单位地址")
        or derived.get("单位地址")
        or derived.get("地址")
    )
    if not addr:
        from apps.api.inspection_report_make import _resolve_inspected_unit_address_from_submit

        addr = _norm(_resolve_inspected_unit_address_from_submit(dict(payload)))
    if not addr:
        for blob in (hi, dd):
            if not isinstance(blob, dict):
                continue
            for key in ("f8", "f3", "f22", "address", "inspectionAddress"):
                cand = _norm(blob.get(key))
                if cand:
                    addr = cand
                    break
            if addr:
                break
    if addr:
        from apps.api.inspection_report_make import _is_plausible_inspected_unit_address

        if not _is_plausible_inspected_unit_address(addr):
            addr = ""
    has_fail = probe_submit_has_fail_verdict(
        payload,
        project=project,
        case=inspection_case,
        report_task=report_task,
        task_no=task_no,
    )
    device_count = resolve_report_device_count_for_project(
        project,
        task_no=task_no,
        report_task=report_task,
        manual_override=manual_device_count,
    )
    from apps.core.report_template_profiles import get_report_template_profile

    _prof = get_report_template_profile(report_task)
    _dc_val = str(max(0, int(device_count)))
    if _prof.device_count_include_unit_suffix and not _dc_val.endswith("台"):
        _dc_val += "台"
    abbrs: list[str] = []
    rt_norm = normalize_equipment_report_task(report_task) if report_task is not None else None
    if project is not None and rt_norm is not None:
        for link in project_equipment_queryset(project):
            lt = normalize_equipment_report_task(effective_report_task(link))
            if lt is None or int(lt.pk) != int(rt_norm.pk):
                continue
            eq = link.equipment
            title = (getattr(eq, "device_type", None) or eq.name or eq.model or "").strip()
            ab = extract_modality_abbr_from_title(title) or (eq.name or eq.model or "设备").strip()
            abbrs.append(ab or "设备")
    if device_count > 1 and abbrs:
        if has_radiation_protection and not include_qc_content:
            evaluation_body = build_single_report_evaluation_body(
                org,
                ab,
                has_radiation_protection=True,
                qc_with_radiation_protection=False,
                has_fail=has_fail,
                inspection_type=report_type,
            )
        elif has_radiation_protection and include_qc_content:
            evaluation_body = build_single_report_evaluation_body(
                org,
                ab,
                has_radiation_protection=True,
                qc_with_radiation_protection=qc_with_rp,
                has_fail=has_fail,
                inspection_type=report_type,
            )
        else:
            ok_tail = "所检设备的质量控制相关参数均符合相关标准要求。"
            fail_tail = "存在参数不符合相关标准。"
            tail = fail_tail if has_fail else ok_tail
            dev_eval = merged_device_phrase_for_evaluation(abbrs, len(abbrs))
            qc_phrase = format_qc_detection_for_evaluation(report_type)
            evaluation_body = (
                f"应委托方要求，依据相关检测标准，对{org}{dev_eval}进行了{qc_phrase}，结果表明：\n{tail}"
            )
    else:
        evaluation_body = build_single_report_evaluation_body(
            org,
            ab,
            has_radiation_protection=has_radiation_protection,
            qc_with_radiation_protection=qc_with_rp,
            has_fail=has_fail,
            inspection_type=report_type,
        )

    report_no = format_gan_jcy_institute_report_no(
        com,
        has_radiation_protection=has_radiation_protection,
    )
    inspected_no = _norm(
        derived.get("受检编号")
        or derived.get("inspectedNo")
        or derived.get("taskNo")
    )
    overlay = {
        "merge_date_str": report_date,
        "cover_inspected_org": org,
        "cover_org_line": org,
        "cover_report_title": cover_title,
        "toc_device_title": toc_device_title,
        "inspection_index_line": inspected_no or "01",
        "project_name_combined": project_name_combined,
        "device_count_label": _dc_val,
        "evaluation_body": evaluation_body,
        "cover_inspection_type": report_type,
        "summary_inspection_type": report_type,
        "report_no_display": report_no,
        "cover_report_no_line": format_cover_report_number_line(
            com,
            has_radiation_protection=has_radiation_protection,
        ),
        "commission_no": com,
        "inspected_org_address": addr,
    }
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
    toc_skip_qc_minors: bool = False,
    include_qc_content: bool = True,
    ordered_submit_payloads: Optional[Sequence[Mapping[str, Any]]] = None,
) -> bytes:
    """
    在 HTMLPDF 回填及（可选）放射防护表插入之后执行：
    封面/基本情况叠印、目录重绘、页眉页码统一。
    编制人/审核人/授权签字人/签发日期保持模板 PDF 原样，不做清空或叠印。

    include_qc_content=False：仅防护结果导出——项目名称/评价不含质控措辞。
    """
    if not pdf_bytes:
        return pdf_bytes
    from apps.core.report_template_profiles import get_report_template_profile, resolve_report_template_profile

    profile = get_report_template_profile(report_task)
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
            include_qc_content=include_qc_content,
        )
        from utils.pdf_merge import (
            apply_merged_report_merge_overlay,
            ensure_last_page_yixia_kongbai_marker,
            fill_results_section_inspection_number,
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

        template_fields = None
        if isinstance(template_parsed, dict):
            pdf_meta = template_parsed.get("pdf")
            if isinstance(pdf_meta, dict):
                template_fields = pdf_meta.get("fields")
        profile = resolve_report_template_profile(report_task, template_fields)

        if overlay:
            apply_merged_report_merge_overlay(
                doc,
                header_page_count=3,
                overlay=overlay,
                template_parsed=template_parsed,
                use_global_fixed_rects=False,
                profile=profile,
            )
        inspected_fill = _norm((overlay or {}).get("inspection_index_line"))
        if inspected_fill:
            fill_results_section_inspection_number(doc, inspected_fill)
        try:
            from radiation_detection_report.instrument_table_overlay import (
                apply_results_instrument_tables,
                resolve_results_instrument_scope,
            )

            apply_results_instrument_tables(
                doc,
                source_payload=source_payload if isinstance(source_payload, dict) else None,
                ordered_submit_payloads=ordered_submit_payloads,
                instrument_scope=resolve_results_instrument_scope(
                    has_radiation_protection=has_radiation_protection,
                    include_qc_content=include_qc_content,
                ),
            )
        except Exception:
            logger.exception("apply results instrument tables failed")
        report_no = _norm((overlay or {}).get("report_no_display")) or _extract_report_no_from_doc(doc)
        regenerate_single_report_table_of_contents(
            doc,
            report_no=report_no,
            has_radiation_protection=has_radiation_protection,
            device_title=_norm(
                (overlay or {}).get("toc_device_title")
                or (overlay or {}).get("cover_report_title")
            ),
            profile=profile,
            skip_qc_minors=toc_skip_qc_minors,
        )
        toc_idx = _find_toc_page_index(doc)
        rewrite_merged_report_page_headers(
            doc,
            report_no=report_no or "报告",
            toc_page_0based=toc_idx,
            profile=profile,
            summary_page_0based=2,
        )
        redact_yixia_kongbai_except_last_page(doc)
        ensure_last_page_yixia_kongbai_marker(doc)
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


def _find_toc_page_index(doc: fitz.Document) -> int | None:
    if not fitz or doc is None:
        return None
    for pi in range(2, min(doc.page_count, 8)):
        compact = re.sub(r"\s+", "", doc[pi].get_text("text"))
        if "目录" in compact:
            return pi
    return None
