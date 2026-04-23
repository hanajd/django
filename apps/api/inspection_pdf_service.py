"""检测提交 -> 模板填充 -> PDF 落库服务（与接口流水线解耦）。"""
from __future__ import annotations

import json as json_std
import os
import re
from datetime import datetime

from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.api.inspection_submit_placeholder_maps import (
    DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID,
    SUBMIT_PLACEHOLDER_MAPS,
    build_submit_value_mapping,
    resolve_mapping_rules,
)
from apps.core import htmlpdf_service, pipeline_service
from apps.core.library_file_service import attach_files_to_tasks, save_library_binary_uploads
from apps.core.models import InspectionCase, LibraryFile, LibraryProject, LibraryTask, LibraryTaskAssignment, SiteRecord

AUTO_TASK_PREFIX = "ASG-"


def _resolve_library_task_for_task_no(task_no: str, project=None):
    assignment_id = _parse_assignment_task_no(task_no)
    if assignment_id:
        qs = LibraryTaskAssignment.objects.select_related("library_task").filter(pk=assignment_id)
        if project is not None and getattr(project, "pk", None):
            qs = qs.filter(project_id=project.pk)
        row = qs.first()
        if not row:
            return None
        return row.library_task if row.library_task_id else None
    # InspectionCase.case_no 常与 _InspectionTaskAccessMixin._build_project_task_no 一致，为项目内任务序号 "01"…
    if project is not None and getattr(project, "pk", None):
        raw = str(task_no or "").strip()
        try:
            idx = int(raw)
        except ValueError:
            return None
        if idx > 0:
            tasks = list(project.library_tasks.order_by("code", "id"))
            if idx <= len(tasks):
                return tasks[idx - 1]
    return None


def _parse_assignment_task_no(task_no: str):
    if not (task_no or "").startswith(AUTO_TASK_PREFIX):
        return None
    raw = (task_no or "")[len(AUTO_TASK_PREFIX) :].strip()
    try:
        aid = int(raw)
    except ValueError:
        return None
    return aid if aid > 0 else None


def _display_task_no(task_no: str, project=None) -> str:
    raw = str(task_no or "").strip()
    if raw and raw.isdigit() and len(raw) <= 2:
        return f"{int(raw):02d}"
    task_obj = _resolve_library_task_for_task_no(raw, project)
    if task_obj is None or project is None:
        return raw
    task_ids = list(project.library_tasks.order_by("code", "id").values_list("id", flat=True))
    try:
        idx = task_ids.index(task_obj.id) + 1
    except ValueError:
        return raw
    return f"{idx:02d}"


def _display_project_id(project) -> str:
    if project is None:
        return ""
    code = str(getattr(project, "code", "") or "").strip()
    if re.fullmatch(r"\d{8}", code):
        return code
    created_at = getattr(project, "created_at", None) or datetime.now()
    if timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at, timezone.get_current_timezone())
    dt = timezone.localtime(created_at)
    prefix = dt.strftime("%Y%m")
    month_start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if dt.month == 12:
        next_month_start = dt.replace(year=dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month_start = dt.replace(month=dt.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    ids = list(
        LibraryProject.objects.filter(created_at__gte=month_start, created_at__lt=next_month_start)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    try:
        idx = ids.index(project.id) + 1
    except ValueError:
        idx = 1
    return f"{prefix}{idx:02d}"


def _build_submit_value_mapping(source_data: dict, map_id: str | None = None):
    mid = (map_id or "").strip() or DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID
    if mid not in SUBMIT_PLACEHOLDER_MAPS:
        mid = DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID
    return build_submit_value_mapping(source_data, mid)


def _build_submit_derived_value_mapping(source_data: dict, project=None, task_no: str = ""):
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

    project_code = _display_project_id(project)
    task_no_val = _display_task_no(task_no, project=project)

    return {
        "projectId": project_code,
        "entrustNo": project_code,
        "委托编号": project_code,
        "commissionNo": project_code,
        "taskNo": task_no_val,
        "inspectedNo": task_no_val,
        "受检编号": task_no_val,
        "testnumber": task_no_val,
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


def _build_template_binding_value_mapping(source_data: dict, bindings: dict[str, object]) -> dict[str, object]:
    if not isinstance(bindings, dict):
        return {}
    rules = bindings.get("value_rules")
    if not isinstance(rules, list):
        return {}
    normalized_rules = []
    for row in rules:
        if not isinstance(row, dict):
            continue
        key = row.get("key") or row.get("id") or row.get("fieldId") or row.get("placeholder")
        if not key:
            continue
        normalized = {"key": key}
        for k, v in row.items():
            if k in ("key", "id", "fieldId", "placeholder"):
                continue
            normalized[k] = v
        normalized_rules.append(normalized)
    return resolve_mapping_rules(source_data, normalized_rules)


def _fill_template_fields_with_submit(
    source_data: dict,
    template_fields: list,
    map_id: str | None = None,
    project=None,
    task_no: str = "",
    bindings: dict[str, object] | None = None,
):
    # 允许通过环境变量强制使用旧版回填逻辑，便于问题回滚定位。
    if str(os.environ.get("INSPECTION_FILL_USE_LEGACY", "0")).strip().lower() in {"1", "true", "yes", "on"}:
        return _fill_template_fields_with_submit_legacy(
            source_data,
            template_fields,
            map_id=map_id,
            project=project,
            task_no=task_no,
            bindings=bindings,
        )
    return _fill_template_fields_with_submit_enhanced(
        source_data,
        template_fields,
        map_id=map_id,
        project=project,
        task_no=task_no,
        bindings=bindings,
    )


def _fill_template_fields_with_submit_legacy(
    source_data: dict,
    template_fields: list,
    map_id: str | None = None,
    project=None,
    task_no: str = "",
    bindings: dict[str, object] | None = None,
):
    """
    旧版回填逻辑（保留原行为）：
    - 文本/勾选仅按 field.id 或 field.placeholder 取值
    - 签名仅按 signature_map[field_key]，回退 author/reviewer/approver
    """
    signatures = source_data.get("signatures") or {}
    value_mapping = _build_template_binding_value_mapping(source_data, bindings or {})
    if not value_mapping:
        value_mapping = _build_submit_value_mapping(source_data, map_id)
    value_mapping.update(_build_submit_derived_value_mapping(source_data, project=project, task_no=task_no))
    signature_map = {}
    if isinstance(bindings, dict):
        raw_sig_map = bindings.get("signature_map")
        if isinstance(raw_sig_map, dict):
            signature_map = raw_sig_map

    for field in template_fields:
        if not isinstance(field, dict):
            continue
        field_key = field.get("id") or field.get("placeholder")
        field_type = (field.get("fieldType") or "").lower()
        if field_type == "text":
            field["content"] = value_mapping.get(field_key, "")
            continue
        if field_type == "check":
            field["checked"] = bool(value_mapping.get(field_key, False))
            continue
        if field_type == "image":
            sig_key = signature_map.get(field_key) if isinstance(signature_map, dict) else None
            if not isinstance(sig_key, str) or not sig_key:
                sig_key = field_key if field_key in ("author", "reviewer", "approver") else ""
            if sig_key:
                field["imageData"] = signatures.get(sig_key) or ""
    return template_fields


def _build_field_to_pdf_reverse_index(bindings: dict[str, object] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(bindings, dict):
        return out
    raw_field_to_pdf = bindings.get("field_to_pdf")
    if not isinstance(raw_field_to_pdf, list):
        return out
    for row in raw_field_to_pdf:
        if not isinstance(row, dict):
            continue
        field_id = str(row.get("fieldId") or "").strip()
        if not field_id:
            continue
        for key in (
            str(row.get("pdfFieldId") or "").strip(),
            str(row.get("placeholder") or "").strip(),
            str(row.get("title") or "").strip(),
        ):
            if key:
                out[key] = field_id
    return out


def _field_candidate_keys(field: dict) -> list[str]:
    return [
        str(field.get("id") or "").strip(),
        str(field.get("placeholder") or "").strip(),
        str(field.get("title") or "").strip(),
        str(field.get("pdfFieldId") or "").strip(),
    ]


def _fill_template_fields_with_submit_enhanced(
    source_data: dict,
    template_fields: list,
    map_id: str | None = None,
    project=None,
    task_no: str = "",
    bindings: dict[str, object] | None = None,
):
    """
    增强回填逻辑（兼容新版前端字段改名）：
    - 支持 id/placeholder/title/pdfFieldId 多键匹配
    - 支持 field_to_pdf 反查 fieldId
    - 签名支持 direct map + 结构化 signature_map
    """
    signatures = source_data.get("signatures") or {}
    value_mapping = _build_template_binding_value_mapping(source_data, bindings or {})
    if not value_mapping:
        value_mapping = _build_submit_value_mapping(source_data, map_id)
    value_mapping.update(_build_submit_derived_value_mapping(source_data, project=project, task_no=task_no))
    signature_map = {}
    if isinstance(bindings, dict):
        raw_sig_map = bindings.get("signature_map")
        if isinstance(raw_sig_map, dict):
            signature_map = raw_sig_map
    reverse_field_map = _build_field_to_pdf_reverse_index(bindings)

    def _pick_value_for_field(field: dict):
        candidates = _field_candidate_keys(field)
        for k in candidates:
            if k and k in value_mapping:
                return value_mapping.get(k, "")
        for k in candidates:
            mapped_fid = reverse_field_map.get(k)
            if mapped_fid and mapped_fid in value_mapping:
                return value_mapping.get(mapped_fid, "")
        return ""

    def _pick_signature_for_field(field: dict):
        candidates = _field_candidate_keys(field)
        if isinstance(signature_map, dict):
            for key in candidates:
                if not key:
                    continue
                mapped = signature_map.get(key)
                if isinstance(mapped, str) and mapped:
                    return signatures.get(mapped) or ""

        if isinstance(signature_map, dict):
            for _, row in signature_map.items():
                if not isinstance(row, dict):
                    continue
                image_path = str(row.get("image") or "").strip()
                name_path = str(row.get("name") or "").strip()
                if not image_path:
                    continue
                # 仅在字段名命中 role/name 关键词时采用该映射
                hit = False
                for key in candidates:
                    if not key:
                        continue
                    if key in ("author", "reviewer", "approver"):
                        hit = True
                    if "检测员" in key and ("preparedBy" in name_path or "author" in image_path):
                        hit = True
                    if "校核" in key and ("reviewedBy" in name_path or "reviewer" in image_path):
                        hit = True
                    if "批准" in key and ("approvedBy" in name_path or "approver" in image_path):
                        hit = True
                if not hit:
                    continue
                if image_path.startswith("signatures."):
                    sig_key = image_path.split(".", 1)[1]
                else:
                    sig_key = image_path
                if sig_key:
                    return signatures.get(sig_key) or ""

        for key in candidates:
            if key in ("author", "reviewer", "approver"):
                return signatures.get(key) or ""
        return ""

    for field in template_fields:
        if not isinstance(field, dict):
            continue
        field_type = (field.get("fieldType") or "").lower()
        if field_type == "text":
            field["content"] = _pick_value_for_field(field)
            continue
        if field_type == "check":
            field["checked"] = bool(_pick_value_for_field(field))
            continue
        if field_type == "image":
            field["imageData"] = _pick_signature_for_field(field)
    return template_fields


def _build_filled_template_fields_for_task(
    task_obj, payload: dict, map_id: str | None = None, project=None, task_no: str = ""
):
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
        source_pdf = parsed.get("source_pdf") or {}
        if isinstance(source_pdf, dict):
            raw_pdf_id = source_pdf.get("template_file_id")
            try:
                source_pdf_template_id = int(raw_pdf_id) if raw_pdf_id is not None else None
            except (TypeError, ValueError):
                source_pdf_template_id = None
        template_bindings = parsed.get("bindings") or {}
        candidates.append((template_json_lf, fields, source_pdf_template_id, template_bindings))
    if not candidates:
        return None, None, "任务下 JSON 模板不是 HTMLPDF 字段模板（fields 缺少 page 与坐标）", ""
    if len(candidates) > 1:
        return None, None, "任务下存在多个可用 HTMLPDF JSON 模板，请只保留一个", ""
    template_json_lf, fields, source_pdf_template_id, template_bindings = candidates[0]
    return (
        _fill_template_fields_with_submit(
            payload,
            fields,
            map_id,
            project=project,
            task_no=task_no,
            bindings=template_bindings,
        ),
        source_pdf_template_id,
        "",
        template_json_lf.original_name,
    )


def _build_filled_template_fields_from_submit(task_no: str, project, payload: dict, map_id: str | None = None):
    task_obj = _resolve_library_task_for_task_no(task_no, project)
    if task_obj is None:
        return None, None, "未找到对应任务，无法按任务模板导出", ""
    return _build_filled_template_fields_for_task(
        task_obj,
        payload,
        map_id=map_id,
        project=project,
        task_no=task_no,
    )


def _pick_submit_generation_tasks(task_no: str, project):
    assignment_task = _resolve_library_task_for_task_no(task_no, project)
    if assignment_task and assignment_task.output_target in (LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT):
        # 仅生成当前案件/提交所对应文件库任务的 PDF，避免挂到同项目其它 output 任务上
        return [assignment_task]
    picked = {}
    project_tasks = (
        project.library_tasks.filter(output_target__in=(LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT))
        .order_by("code", "id")
        .distinct()
    )
    for t in project_tasks:
        if t.output_target not in picked:
            picked[t.output_target] = t
    return [picked[k] for k in (LibraryTask.OUTPUT_SITE_RECORD, LibraryTask.OUTPUT_REPORT) if k in picked]


def _resolve_report_task_for_case(task_no: str, project):
    """
    解析当前案件应使用的报告任务。
    优先规则：
    1) taskNo 直接对应且输出目标=report 的任务；
    2) 项目中第一个输出目标=report 的任务。
    """
    task_obj = _resolve_library_task_for_task_no(task_no, project)
    if task_obj is not None and task_obj.output_target == LibraryTask.OUTPUT_REPORT:
        return task_obj
    return (
        project.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
        .order_by("code", "id")
        .first()
    )


def _load_site_record_payload_for_report(case: InspectionCase, project, report_task=None):
    """
    读取用于生成报告的现场记录 JSON。
    若报告任务配置了 report_source_tasks，则按来源任务逐个读取最新现场记录 JSON 并做深度整合。
    """
    def _deep_merge(base: dict, incoming: dict):
        for k, v in (incoming or {}).items():
            if isinstance(v, dict):
                cur = base.get(k) if isinstance(base.get(k), dict) else {}
                if not isinstance(cur, dict):
                    cur = {}
                base[k] = _deep_merge(cur, v)
                continue
            if isinstance(v, list):
                if v or k not in base:
                    base[k] = v
                continue
            if v in (None, "") and k in base:
                continue
            base[k] = v
        return base

    source_tasks = []
    if report_task is not None:
        source_tasks = list(
            report_task.report_source_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).order_by("code", "id")
        )
    if not source_tasks:
        source_tasks = list(
            project.library_tasks.filter(output_target=LibraryTask.OUTPUT_SITE_RECORD).order_by("code", "id")
        )
    qs = (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_SITE_RECORD,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            projects=project,
        )
        .order_by("-created_at", "-id")
        .distinct()
    )
    merged = {}
    used = []
    missing_codes = []
    for st in source_tasks:
        rows = [x for x in qs.filter(library_tasks=st) if (x.original_name or "").lower().endswith(".json")]
        if not rows:
            missing_codes.append(st.code)
            continue
        lf = rows[0]
        try:
            p = pipeline_service.library_absolute_path(lf.relative_path)
            payload = json_std.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            return None, f"现场记录 JSON 读取失败: {exc}"
        if not isinstance(payload, dict):
            return None, "现场记录 JSON 结构无效"
        merged = _deep_merge(merged, payload)
        used.append({"id": st.id, "code": st.code, "name": st.name})
    if not used:
        return None, "未找到可用的现场记录 JSON"
    if missing_codes:
        return merged, f"部分来源任务缺少现场记录 JSON: {', '.join(missing_codes[:10])}"
    return merged, ""


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
