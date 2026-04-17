"""检测提交 -> 模板填充 -> PDF 落库服务（与接口流水线解耦）。"""
from __future__ import annotations

import json as json_std
from datetime import datetime

from django.contrib.auth.models import User
from django.utils.dateparse import parse_datetime

from apps.api.inspection_submit_placeholder_maps import (
    DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID,
    SUBMIT_PLACEHOLDER_MAPS,
    build_submit_value_mapping,
)
from apps.core import htmlpdf_service, pipeline_service
from apps.core.library_file_service import attach_files_to_tasks, save_library_binary_uploads
from apps.core.models import InspectionCase, LibraryFile, LibraryTask, LibraryTaskAssignment, SiteRecord

AUTO_TASK_PREFIX = "ASG-"


def _resolve_library_task_for_task_no(task_no: str, project=None):
    assignment_id = _parse_assignment_task_no(task_no)
    if not assignment_id:
        return None
    qs = LibraryTaskAssignment.objects.select_related("library_task").filter(pk=assignment_id)
    if project is not None and getattr(project, "pk", None):
        qs = qs.filter(project_id=project.pk)
    row = qs.first()
    if not row:
        return None
    return row.library_task if row.library_task_id else None


def _parse_assignment_task_no(task_no: str):
    if not (task_no or "").startswith(AUTO_TASK_PREFIX):
        return None
    raw = (task_no or "")[len(AUTO_TASK_PREFIX) :].strip()
    try:
        aid = int(raw)
    except ValueError:
        return None
    return aid if aid > 0 else None


def _build_submit_value_mapping(source_data: dict, map_id: str | None = None):
    mid = (map_id or "").strip() or DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID
    if mid not in SUBMIT_PLACEHOLDER_MAPS:
        mid = DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID
    return build_submit_value_mapping(source_data, mid)


def _build_submit_derived_value_mapping(source_data: dict, project=None):
    def _s(v):
        return "" if v is None else str(v)

    report_info = source_data.get("reportInfo") or {}
    hospital_info = source_data.get("hospitalInfo") or {}
    equipment_info = source_data.get("equipmentInfo") or {}
    test_result = source_data.get("testResult") or {}
    hospital_name = str(hospital_info.get("name") or "")
    model = str(equipment_info.get("model") or "")
    device_name = str(equipment_info.get("deviceName") or "")
    year = str(report_info.get("year") or "")
    month = str(report_info.get("month") or "")
    day = str(report_info.get("day") or "")
    if not (year and month and day):
        td = test_result.get("testDate")
        if isinstance(td, str):
            d = parse_datetime(td.strip())
            if isinstance(d, datetime):
                year, month, day = str(d.year), str(d.month), str(d.day)

    def _test_line(block: dict):
        if not isinstance(block, dict):
            return ""
        return (
            f"{block.get('controlMode') or ''}：{block.get('kv') or ''}kV，{block.get('ma') or ''}mA，\n"
            f"平板探测器尺寸D={block.get('size') or ''}mm \n标准水模"
        )

    kerma_typical = test_result.get("kermaTypical") or {}
    kerma_max_normal = test_result.get("kermaMaxNormal") or {}
    kerma_max_high = test_result.get("kermaMaxHigh") or {}
    high_contrast = test_result.get("highContrast") or {}
    low_contrast = test_result.get("lowContrast") or {}
    screen_kerma = test_result.get("screenKerma") or {}
    abc = test_result.get("abc") or {}
    dsa = test_result.get("dsa") or {}

    def _mv(block: dict, *fallback_keys: str) -> str:
        if not isinstance(block, dict):
            return ""
        if "measuredValue" in block and block.get("measuredValue") not in (None, ""):
            return _s(block.get("measuredValue"))
        for k in fallback_keys:
            if k in block and block.get(k) not in (None, ""):
                return _s(block.get(k))
        return ""

    device_count = 0
    testman = ""
    if project is not None:
        device_count = SiteRecord.objects.filter(case__library_project=project).count()
        names = (
            User.objects.filter(site_records__case__library_project=project)
            .order_by("username")
            .values_list("username", flat=True)
            .distinct()
        )
        testman = "、".join([n for n in names if n])

    return {
        "deviceCount": str(device_count) if device_count else "",
        "testman": testman,
        "assessment": (
            f"应委托方要求，依据相关检测标准，对{hospital_name}放射诊疗设备（{model} 型{device_name}）"
            "进行了质量控制检测（验收检测），结果表明：\n所检设备的质量控制相关参数均符合相关标准要求。"
        ),
        "testDate": f"{year}年{month}月{day}日" if year and month and day else "",
        "kerma_test": _test_line(kerma_typical),
        "kermaMax_test": _test_line({"controlMode": "最大比释动能", **(kerma_max_normal if isinstance(kerma_max_normal, dict) else {})}),
        "kermaMaxNormal_test": _test_line(kerma_max_normal),
        "kermaMaxHigh_test": _test_line(kerma_max_high),
        "highContrast_test": _test_line(high_contrast),
        "lowContrast_test": _test_line(low_contrast),
        "screenKerma_test": _test_line(screen_kerma),
        "abc_test": _test_line(abc),
        "dsa_doseFirst_test": _test_line((dsa.get("doseFirst") or {}) if isinstance(dsa, dict) else {}),
        "dsa_doseSecond_test": _test_line((dsa.get("doseSecond") or {}) if isinstance(dsa, dict) else {}),
        "dsa_test": _test_line(dsa if isinstance(dsa, dict) else {}),
        "kerma_result": _s(kerma_typical.get("calcValue")) if isinstance(kerma_typical, dict) and kerma_typical.get("calcValue") not in (None, "") else _mv(kerma_typical, "measuredValue", "result"),
        "kermaMaxNormal_reult": _s(kerma_max_normal.get("calcValue")) if isinstance(kerma_max_normal, dict) and kerma_max_normal.get("calcValue") not in (None, "") else _mv(kerma_max_normal, "measuredValue", "result"),
        "kermaMaxHigh_result": _s(kerma_max_high.get("calcValue")) if isinstance(kerma_max_high, dict) and kerma_max_high.get("calcValue") not in (None, "") else _mv(kerma_max_high, "measuredValue", "result"),
        "abc_result": _s(abc.get("measuredValue", "")) if isinstance(abc, dict) else "",
        # *_result 按你的要求优先使用 calcValue（缺失时回退 measuredValue/result）
        "hightContrast_result": _s(high_contrast.get("calcValue")) if isinstance(high_contrast, dict) and high_contrast.get("calcValue") not in (None, "") else _mv(high_contrast, "measuredValue", "result"),
        "lowContrast_result": _s(low_contrast.get("calcValue")) if isinstance(low_contrast, dict) and low_contrast.get("calcValue") not in (None, "") else _mv(low_contrast, "measuredValue", "result"),
        "screenKerma_result": _s(screen_kerma.get("calcValue")) if isinstance(screen_kerma, dict) and screen_kerma.get("calcValue") not in (None, "") else _mv(screen_kerma, "measuredValue", "result"),
    }


def _fill_template_fields_with_submit(source_data: dict, template_fields: list, map_id: str | None = None, project=None):
    signatures = source_data.get("signatures") or {}
    value_mapping = _build_submit_value_mapping(source_data, map_id)
    value_mapping.update(_build_submit_derived_value_mapping(source_data, project=project))
    for field in template_fields:
        if not isinstance(field, dict):
            continue
        placeholder = field.get("placeholder")
        field_type = (field.get("fieldType") or "").lower()
        if field_type == "text":
            field["content"] = value_mapping.get(placeholder, "")
            continue
        if field_type == "check":
            field["checked"] = bool(value_mapping.get(placeholder, False))
            continue
        if field_type == "image":
            if placeholder == "author":
                field["imageData"] = signatures.get("author") or ""
            elif placeholder == "reviewer":
                field["imageData"] = signatures.get("reviewer") or ""
            elif placeholder == "approver":
                field["imageData"] = signatures.get("approver") or ""
    return template_fields


def _build_filled_template_fields_for_task(task_obj, payload: dict, map_id: str | None = None, project=None):
    if task_obj is None:
        return None, None, "未找到可用任务模板", ""
    template_qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE, library_tasks=task_obj)
        .order_by("-created_at")
        .distinct()
    )
    template_json_rows = [row for row in template_qs if row.original_name.lower().endswith(".json")]
    if not template_json_rows:
        return None, None, "任务下缺少 JSON 模板", ""
    candidates = []
    for template_json_lf in template_json_rows:
        try:
            template_path = pipeline_service.library_absolute_path(template_json_lf.relative_path)
            raw_text = template_path.read_text(encoding="utf-8", errors="replace")
            raw_obj = json_std.loads(raw_text)
            parsed = htmlpdf_service.parse_template_json(raw_text)
        except Exception:
            continue
        fields = parsed.get("fields") or []
        if not isinstance(fields, list) or not fields:
            continue
        has_layout_field = any(
            isinstance(f, dict)
            and "page" in f
            and (all(k in f for k in ("x0", "y0", "x1", "y1")) or all(k in f for k in ("x", "y", "w", "h")))
            for f in fields
        )
        if not has_layout_field:
            continue
        source_pdf_template_id = None
        if isinstance(raw_obj, dict):
            source_pdf = raw_obj.get("source_pdf") or {}
            if isinstance(source_pdf, dict):
                raw_pdf_id = source_pdf.get("template_file_id")
                try:
                    source_pdf_template_id = int(raw_pdf_id) if raw_pdf_id is not None else None
                except (TypeError, ValueError):
                    source_pdf_template_id = None
        candidates.append((template_json_lf, fields, source_pdf_template_id))
    if not candidates:
        return None, None, "任务下 JSON 模板不是 HTMLPDF 字段模板（fields 缺少 page 与坐标）", ""
    if len(candidates) > 1:
        return None, None, "任务下存在多个可用 HTMLPDF JSON 模板，请只保留一个", ""
    template_json_lf, fields, source_pdf_template_id = candidates[0]
    return (_fill_template_fields_with_submit(payload, fields, map_id, project=project), source_pdf_template_id, "", template_json_lf.original_name)


def _build_filled_template_fields_from_submit(task_no: str, project, payload: dict, map_id: str | None = None):
    task_obj = _resolve_library_task_for_task_no(task_no, project)
    if task_obj is None:
        return None, None, "未找到对应任务，无法按任务模板导出", ""
    return _build_filled_template_fields_for_task(task_obj, payload, map_id=map_id, project=project)


def _pick_submit_generation_tasks(task_no: str, project):
    picked = {}
    assignment_task = _resolve_library_task_for_task_no(task_no, project)
    if assignment_task and assignment_task.output_target in (LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT):
        picked[assignment_task.output_target] = assignment_task
    project_tasks = (
        project.library_tasks.filter(output_target__in=(LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT))
        .order_by("code", "id")
        .distinct()
    )
    for t in project_tasks:
        if t.output_target not in picked:
            picked[t.output_target] = t
    return [picked[k] for k in (LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT) if k in picked]


def _normalize_fields_for_htmlpdf(fields):
    normalized = []
    skipped = 0
    for f in (fields or []):
        if not isinstance(f, dict):
            skipped += 1
            continue
        page = f.get("page")
        if page in (None, ""):
            skipped += 1
            continue
        if all(k in f for k in ("x0", "y0", "x1", "y1")):
            try:
                x0 = float(f.get("x0")); y0 = float(f.get("y0")); x1 = float(f.get("x1")); y1 = float(f.get("y1"))
            except (TypeError, ValueError):
                skipped += 1
                continue
        elif all(k in f for k in ("x", "y", "w", "h")):
            try:
                x = float(f.get("x")); y = float(f.get("y")); w = float(f.get("w")); h = float(f.get("h"))
            except (TypeError, ValueError):
                skipped += 1
                continue
            if w <= 0 or h <= 0:
                skipped += 1
                continue
            x0, y0, x1, y1 = x, y, x + w, y + h
        else:
            skipped += 1
            continue
        text_val = f.get("value")
        if text_val in (None, ""):
            text_val = f.get("content", "")
        normalized.append(
            {
                "page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "fieldType": (f.get("fieldType") or "text"),
                "value": text_val or "",
                "checked": bool(f.get("checked", False)),
                "imageData": f.get("imageData") or "",
            }
        )
    return normalized, skipped


def _persist_filled_pdf_from_submit(
    user,
    task_no: str,
    case: InspectionCase,
    project,
    filled_fields: list,
    template_pdf_id=None,
    template_json_name: str = "",
    task_obj=None,
):
    if not isinstance(filled_fields, list) or not filled_fields:
        return False, "无可用填充字段"
    normalized_fields, skipped_fields = _normalize_fields_for_htmlpdf(filled_fields)
    if not normalized_fields:
        return False, "模板字段缺少有效页码或坐标（支持 x/y/w/h 或 x0/y0/x1/y1）"
    if task_obj is None:
        task_obj = _resolve_library_task_for_task_no(task_no, project)
    if task_obj is None:
        return False, "未找到对应任务模板，无法导出"
    template_pdf_lf = None
    if template_pdf_id:
        template_pdf_lf = (
            LibraryFile.objects.filter(pk=template_pdf_id, category=LibraryFile.CATEGORY_TEMPLATE, projects=project)
            .distinct()
            .first()
        )
    template_qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE, library_tasks=task_obj)
        .order_by("-created_at")
        .distinct()
    )
    if template_pdf_lf is None:
        template_pdf_rows = [row for row in template_qs if row.original_name.lower().endswith(".pdf")]
        if not template_pdf_rows:
            return False, "项目下缺少 PDF 模板"
        if len(template_pdf_rows) > 1:
            return False, "项目下存在多个 PDF 模板，请先保证每个任务项目仅有一个 PDF 模板"
        template_pdf_lf = template_pdf_rows[0]
    try:
        source_pdf = pipeline_service.library_absolute_path(template_pdf_lf.relative_path)
        pdf_bytes = htmlpdf_service.build_filled_pdf(normalized_fields, source_pdf)
    except Exception as exc:
        return False, f"PDF 渲染失败: {exc}"
    output_category = LibraryFile.CATEGORY_REPORT if task_obj.output_target == LibraryTask.OUTPUT_REPORT else LibraryFile.CATEGORY_SITE_RECORD
    safe_task_no = (task_no or "").replace("/", "_")
    filename = f"{safe_task_no}-报告.pdf" if output_category == LibraryFile.CATEGORY_REPORT else f"{safe_task_no}-现场记录.pdf"
    wrapped = type("UploadLike", (), {"read": lambda self: pdf_bytes, "name": filename})()
    created, _ = save_library_binary_uploads(
        user, [wrapped], output_category,
        link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
        link_object_id=case.pk, project_ids=[project.pk]
    )
    if task_obj and created:
        attach_files_to_tasks([row["id"] for row in created if row.get("id")], [task_obj.pk], user)
    if not created:
        return False, "PDF 保存失败（save_library_binary_uploads 未创建记录）"
    if skipped_fields:
        return True, f"导出成功，但已跳过 {skipped_fields} 个不合法模板字段"
    return True, ""
