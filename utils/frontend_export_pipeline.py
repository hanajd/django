"""
现场 ``export-frontend-json`` 与 HTMLPDF 保存 ``*_frontend.json`` 共用导出管线。

前端运行态 JSON 必须以任务绑定的**主坐标模板**（``current/*.json``，含 ``pdf.fields``）为真源，
经规则引擎重算 ``steps``；不得直接落盘编辑器内存里的 ``steps`` / 未编译章节公式。
"""
from __future__ import annotations

import copy
import json as json_std
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def resolve_task_template_pdf_path(task_obj) -> str | None:
    if task_obj is None:
        return None
    from apps.api.inspection_views import _resolve_task_template_pdf
    from apps.core import pipeline_service
    from apps.core.models import LibraryFile

    pdf_id, _, pdf_err = _resolve_task_template_pdf(task_obj)
    if pdf_err or not pdf_id:
        return None
    lf = LibraryFile.objects.filter(pk=pdf_id).first()
    if lf is None:
        return None
    try:
        return str(pipeline_service.library_absolute_path(lf.relative_path))
    except Exception:
        return None


def read_bound_primary_template_json(
    request,
    *,
    library_task=None,
    data: Optional[dict] = None,
    meta: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """
    读取任务绑定的主坐标模板 JSON（排除 auxiliary / *_frontend.json）。
    优先 ``library_task`` → ``pick_task_primary_json_template``；否则按请求中的模板文件 id 解析。
    """
    from apps.core import pipeline_service
    from apps.core.library_access import library_file_access_allowed
    from apps.core.models import LibraryFile, LibraryTask

    def _load_lf(lf: LibraryFile | None) -> Optional[Dict[str, Any]]:
        if lf is None or not library_file_access_allowed(request.user, lf):
            return None
        try:
            path = pipeline_service.library_absolute_path(lf.relative_path)
            obj = json_std.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            logger.debug("read_bound_primary_template_json failed: %s", exc)
            return None
        return obj if isinstance(obj, dict) else None

    if library_task is not None and isinstance(library_task, LibraryTask):
        from apps.core.library_task_template_binding_service import pick_task_primary_json_template

        primary = pick_task_primary_json_template(library_task)
        blob = _load_lf(primary)
        if blob is not None:
            return blob

    data = data if isinstance(data, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    tf_raw = (
        data.get("library_template_file_id")
        or data.get("libraryTemplateFileId")
        or data.get("template_file_id")
        or meta.get("library_template_file_id")
        or meta.get("libraryTemplateFileId")
        or meta.get("template_file_id")
    )
    if tf_raw is None or str(tf_raw).strip() == "":
        return None
    try:
        fid = int(tf_raw)
    except (TypeError, ValueError):
        return None
    lf = LibraryFile.objects.filter(pk=fid, category=LibraryFile.CATEGORY_TEMPLATE).first()
    if lf is None:
        return None
    from apps.core.library_task_template_binding_service import (
        is_auxiliary_template_json_file,
        pick_task_primary_json_template,
    )

    read_lf = lf
    if is_auxiliary_template_json_file(lf):
        tasks = list(LibraryTask.objects.filter(library_files=lf).distinct().order_by("code"))
        for t in tasks:
            primary = pick_task_primary_json_template(t)
            if primary is not None:
                read_lf = primary
                break
        else:
            return None
    return _load_lf(read_lf)


def pdf_fields_from_template_blob(template_blob: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从主坐标模板 ``pdf.fields`` 展开栏位（不重编号，保持库内 f 号）。"""
    from utils.unified_template_fields import materialize_unified_pdf_fields

    pdf = template_blob.get("pdf") if isinstance(template_blob.get("pdf"), dict) else {}
    raw = pdf.get("fields") if isinstance(pdf.get("fields"), list) else []
    return materialize_unified_pdf_fields(raw, reindex_pdf_field_ids=False)


def radiation_chapter_from_template_blob(template_blob: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    fs = template_blob.get("formSchema") if isinstance(template_blob.get("formSchema"), dict) else {}
    ch = fs.get("radiationProtectionChapter") if isinstance(fs, dict) else None
    if isinstance(ch, dict):
        return copy.deepcopy(ch)
    ch = template_blob.get("radiationProtectionChapter")
    return copy.deepcopy(ch) if isinstance(ch, dict) else None


def template_obj_for_frontend_export(template_blob: Dict[str, Any]) -> Dict[str, Any]:
    """以主坐标模板为壳，供 ``build_frontend_schema_by_rules`` 消费。"""
    obj = copy.deepcopy(template_blob)
    for key in ("bindings", "pdfBindings", "content"):
        obj.pop(key, None)
    return obj


def assign_pdf_template_sections(pdf_path: str | None, fields: List[Dict[str, Any]]) -> None:
    if not pdf_path or not fields:
        return
    try:
        from htmlpdf.full_text_coordinate_boxing import assign_template_sections_to_fields

        assign_template_sections_to_fields(str(pdf_path), fields)
    except Exception as exc:
        logger.debug("assign_template_sections_to_fields skipped: %s", exc)


def build_runtime_frontend_schema(
    template_obj: Dict[str, Any],
    pdf_fields: List[Dict[str, Any]],
    *,
    pdf_path: str | None = None,
    payload_for_defaults: Optional[Dict[str, Any]] = None,
    project_id: str = "",
    task_no: str = "",
    inspected_display_no: str | None = None,
    task_obj=None,
    project_obj=None,
    extra_formula_source: Any = None,
) -> Dict[str, Any]:
    """
    与 ``InspectionTaskFrontendJsonExportAPIView`` 默认 runtime 导出一致：
    规则引擎 → 任务预填（可选）→ PDF 锚点 → ``finalize_runtime_frontend_export``。
    """
    template_obj = copy.deepcopy(template_obj) if isinstance(template_obj, dict) else {}
    fields_work = [f for f in (pdf_fields or []) if isinstance(f, dict)]
    assign_pdf_template_sections(pdf_path, fields_work)

    pdf_block = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
    pdf_block = dict(pdf_block)
    pdf_block["fields"] = fields_work
    template_obj["pdf"] = pdf_block

    if task_obj is not None:
        try:
            from apps.core.library_task_template_binding_service import (
                apply_task_library_mount_to_template_obj,
            )

            apply_task_library_mount_to_template_obj(template_obj, task_obj)
        except Exception as exc:
            logger.debug("apply library template mount skipped: %s", exc)

    try:
        from utils.frontend_schema_rule_engine import build_frontend_schema_by_rules

        frontend_obj = build_frontend_schema_by_rules(template_obj)
    except Exception as exc:
        logger.warning("build_frontend_schema_by_rules failed, fallback extract: %s", exc)
        from utils.extract_frontend_template import extract_frontend_template

        frontend_obj = extract_frontend_template(template_obj)

    if extra_formula_source is not None:
        from utils.pdf_field_formulas import merge_field_formulas_into_frontend

        frontend_obj = merge_field_formulas_into_frontend(frontend_obj, extra_formula_source)

    if project_id or task_no:
        from apps.api.inspection_views import _inject_frontend_context_defaults

        frontend_obj = _inject_frontend_context_defaults(
            frontend_obj,
            project_id=project_id,
            task_no=task_no,
            inspected_display_no=inspected_display_no,
        )

    payload = dict(payload_for_defaults) if isinstance(payload_for_defaults, dict) else {}
    if task_obj is not None or project_obj is not None or payload:
        from apps.api.inspection_report_make import build_instruments_root_for_frontend_export
        from apps.api.inspection_views import (
            _inject_frontend_payload_defaults,
            _inject_instruments_root_into_frontend_export,
        )

        instrument_root = build_instruments_root_for_frontend_export(
            task_obj=task_obj, project_obj=project_obj, payload=payload
        )
        if isinstance(instrument_root, dict):
            payload = {**payload, **instrument_root}
        if payload:
            from apps.core.site_record_hospital_prefill import (
                enrich_payload_hospital_info_from_frontend_chapter,
            )

            enrich_payload_hospital_info_from_frontend_chapter(payload, frontend_obj)
            frontend_obj = _inject_frontend_payload_defaults(frontend_obj, payload)
        frontend_obj = _inject_instruments_root_into_frontend_export(
            frontend_obj, payload, task_obj=task_obj, project_obj=project_obj
        )

    from utils.unified_template_fields import enrich_frontend_steps_rect_from_pdf_fields
    from utils.frontend_runtime_export import finalize_runtime_frontend_export

    frontend_obj = enrich_frontend_steps_rect_from_pdf_fields(frontend_obj, fields_work)
    if isinstance(frontend_obj, dict):
        frontend_obj.pop("bindings", None)
        frontend_obj.pop("pdfBindings", None)
    chapter_state = template_obj.get("radiationProtectionChapter")
    if not isinstance(chapter_state, dict):
        fs = template_obj.get("formSchema")
        if isinstance(fs, dict) and isinstance(fs.get("radiationProtectionChapter"), dict):
            chapter_state = fs.get("radiationProtectionChapter")
    return finalize_runtime_frontend_export(
        frontend_obj,
        pdf_fields=fields_work,
        radiation_chapter_state=chapter_state if isinstance(chapter_state, dict) else None,
    )


def build_editor_saved_frontend_json(
    template_obj: Dict[str, Any],
    pdf_fields: List[Dict[str, Any]],
    *,
    pdf_path: str | None = None,
    extra_formula_source: Any = None,
    task_obj=None,
    project_obj=None,
    radiation_chapter_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    HTMLPDF 编辑器「保存前端 JSON」专用：单次规则引擎 + runtime 压平。
    不走任务提交回填（``build_runtime_frontend_for_inspection_export``），避免重复读库/填表。
  结构与现场 ``export-frontend-json`` 一致；预填值请在现场接口获取。
    """
    template_obj = copy.deepcopy(template_obj) if isinstance(template_obj, dict) else {}
    fields_work = [dict(f) for f in (pdf_fields or []) if isinstance(f, dict)]
    assign_pdf_template_sections(pdf_path, fields_work)

    pdf_block = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
    pdf_block = dict(pdf_block)
    pdf_block["fields"] = fields_work
    template_obj["pdf"] = pdf_block

    if task_obj is not None:
        try:
            from apps.core.library_task_template_binding_service import (
                apply_task_library_mount_to_template_obj,
            )

            apply_task_library_mount_to_template_obj(template_obj, task_obj)
        except Exception as exc:
            logger.debug("editor apply library template mount skipped: %s", exc)

    try:
        from utils.frontend_schema_rule_engine import build_frontend_schema_by_rules

        frontend_obj = build_frontend_schema_by_rules(template_obj)
    except Exception as exc:
        logger.warning("editor frontend export: rule engine failed: %s", exc)
        from utils.extract_frontend_template import extract_frontend_template

        frontend_obj = extract_frontend_template(template_obj)

    if extra_formula_source is not None:
        from utils.pdf_field_formulas import merge_field_formulas_into_frontend

        frontend_obj = merge_field_formulas_into_frontend(frontend_obj, extra_formula_source)

    if task_obj is not None or project_obj is not None:
        from apps.api.inspection_report_make import build_instruments_root_for_frontend_export
        from apps.api.inspection_views import _inject_instruments_root_into_frontend_export

        inst_root = build_instruments_root_for_frontend_export(
            task_obj=task_obj, project_obj=project_obj, payload={}
        )
        frontend_obj = _inject_instruments_root_into_frontend_export(
            frontend_obj, inst_root, task_obj=task_obj, project_obj=project_obj
        )

    from utils.unified_template_fields import enrich_frontend_steps_rect_from_pdf_fields
    from utils.frontend_runtime_export import finalize_runtime_frontend_export

    frontend_obj = enrich_frontend_steps_rect_from_pdf_fields(frontend_obj, fields_work)
    if isinstance(frontend_obj, dict):
        frontend_obj.pop("bindings", None)
        frontend_obj.pop("pdfBindings", None)
    chapter_state = radiation_chapter_state
    if not isinstance(chapter_state, dict):
        chapter_state = template_obj.get("radiationProtectionChapter")
    if not isinstance(chapter_state, dict):
        fs = template_obj.get("formSchema")
        if isinstance(fs, dict) and isinstance(fs.get("radiationProtectionChapter"), dict):
            chapter_state = fs.get("radiationProtectionChapter")
    return finalize_runtime_frontend_export(
        frontend_obj,
        pdf_fields=fields_work,
        radiation_chapter_state=chapter_state if isinstance(chapter_state, dict) else None,
    )
