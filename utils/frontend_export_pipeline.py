"""
现场 ``export-frontend-json`` 与 HTMLPDF 保存 ``*_frontend.json`` 共用导出管线。
"""
from __future__ import annotations

import copy
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
    return finalize_runtime_frontend_export(frontend_obj)


def build_editor_saved_frontend_json(
    template_obj: Dict[str, Any],
    pdf_fields: List[Dict[str, Any]],
    *,
    pdf_path: str | None = None,
    extra_formula_source: Any = None,
    task_obj=None,
    project_obj=None,
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
    return finalize_runtime_frontend_export(frontend_obj)
