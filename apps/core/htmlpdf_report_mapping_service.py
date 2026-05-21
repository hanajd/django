"""报告模板 ↔ 现场记录模板：可视化字段映射（bindings.report_site_field_map / report_site_field_configs）。"""

from __future__ import annotations

from typing import Any

from apps.core.htmlpdf_service import parse_template_json
from apps.core.library_access import library_file_access_allowed, library_user_may_edit_library_task
from apps.core.library_task_template_binding_service import get_task_template_pair
from apps.core.models import LibraryFile, LibraryTask
from apps.core import pipeline_service
from utils.unified_template_fields import materialize_unified_pdf_fields


def _first_template_files_for_task(task: LibraryTask) -> tuple[LibraryFile | None, LibraryFile | None]:
    return get_task_template_pair(task)


def resolve_library_task_for_template_pdf(
    pdf_file_id: int,
    *,
    preferred_task: LibraryTask | None = None,
) -> LibraryTask | None:
    """解析 PDF 所属任务。URL/请求显式传入的任务优先；未指定时再优先报告类（映射配置用）。"""
    if preferred_task is not None and preferred_task.library_files.filter(pk=pdf_file_id).exists():
        return preferred_task
    report_task = (
        LibraryTask.objects.filter(
            library_files__pk=pdf_file_id,
            output_target=LibraryTask.OUTPUT_REPORT,
        )
        .distinct()
        .order_by("code", "id")
        .first()
    )
    if report_task is not None:
        return report_task
    return (
        LibraryTask.objects.filter(library_files__pk=pdf_file_id)
        .order_by("code", "id")
        .first()
    )


def _load_parsed_template_json(lf: LibraryFile | None) -> dict[str, Any] | None:
    if lf is None:
        return None
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
        return parse_template_json(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _pdf_field_id_key(field: dict) -> str:
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    raw = str(src.get("pdfFieldId") or field.get("pdfFieldId") or field.get("id") or "").strip()
    if not raw:
        return ""
    return raw.lower() if raw.lower().startswith("f") and raw[1:].isdigit() else raw


def _build_pdf_field_meta_index(parsed: dict | None) -> dict[str, dict[str, Any]]:
    """pdfFieldId -> submitPath / placeholder 等（合并 steps 上的 source）。"""
    if not isinstance(parsed, dict):
        return {}
    from apps.api.inspection_report_make import _attach_step_schema_to_pdf_fields

    fields = materialize_unified_pdf_fields(parsed.get("fields") or [])
    if not fields:
        return {}
    steps = parsed.get("steps") if isinstance(parsed.get("steps"), list) else []
    field_rows = [dict(f) for f in fields if isinstance(f, dict)]
    _attach_step_schema_to_pdf_fields(field_rows, steps)
    out: dict[str, dict[str, Any]] = {}
    for f in field_rows:
        pid = _pdf_field_id_key(f)
        if not pid:
            continue
        src = f.get("source") if isinstance(f.get("source"), dict) else {}
        placeholder = str(f.get("placeholder") or "").strip()
        title = str(f.get("title") or "").strip()
        out[pid] = {
            "submitPath": str(src.get("submitPath") or "").strip(),
            "placeholder": placeholder,
            "title": title,
            "label": str(f.get("label") or "").strip(),
        }
    return out


def _normalize_label_key(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def _normalize_source_row(row: dict, *, default_slot: str = "") -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    site_pid = str(row.get("sitePdfFieldId") or "").strip()
    try:
        site_tid = int(row.get("siteTaskId") or 0)
    except (TypeError, ValueError):
        site_tid = 0
    if not site_pid or site_tid <= 0:
        return None
    slot = str(row.get("slot") or row.get("slotKey") or default_slot or "").strip()
    return {
        "siteTaskId": site_tid,
        "sitePdfFieldId": site_pid,
        "siteTaskCode": str(row.get("siteTaskCode") or "").strip(),
        "siteTaskName": str(row.get("siteTaskName") or "").strip(),
        "slot": slot,
        "label": str(row.get("label") or "").strip(),
    }


def _coerce_value_template(raw: Any) -> str:
    """保留模板内缩进与换行，仅统一换行符。"""
    if raw is None:
        return ""
    return str(raw).replace("\r\n", "\n").replace("\r", "\n")


def normalize_report_site_field_configs(rows: list) -> list[dict[str, Any]]:
    """
    统一为报告栏位映射配置（可多来源 + 文本模板）：
    - 新格式：{ reportPdfFieldId, sources: [...], valueTemplate }
    - 旧格式扁平行：每条视为一个来源，同报告栏位合并
    """
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _ensure(report_pid: str) -> dict[str, Any]:
        key = report_pid.strip()
        if key not in grouped:
            grouped[key] = {
                "reportPdfFieldId": key,
                "sources": [],
                "valueTemplate": "",
                "mapSource": "",
                "mapRuleLabel": "",
            }
            order.append(key)
        return grouped[key]

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        report_pid = str(row.get("reportPdfFieldId") or "").strip()
        if not report_pid:
            continue
        cfg = _ensure(report_pid)
        if isinstance(row.get("sources"), list):
            for i, src in enumerate(row["sources"], start=1):
                norm = _normalize_source_row(src, default_slot=str(i))
                if norm:
                    cfg["sources"].append(norm)
            cfg["valueTemplate"] = _coerce_value_template(
                row.get("valueTemplate") or row.get("value_template")
            )
            cfg["mapSource"] = str(row.get("mapSource") or row.get("map_source") or "").strip()
            cfg["mapRuleLabel"] = str(row.get("mapRuleLabel") or row.get("map_rule_label") or "").strip()
        else:
            norm = _normalize_source_row(row)
            if norm:
                if not norm["slot"]:
                    norm["slot"] = str(len(cfg["sources"]) + 1)
                cfg["sources"].append(norm)
            if row.get("mapSource") or row.get("map_source"):
                cfg["mapSource"] = str(row.get("mapSource") or row.get("map_source") or "").strip()
            if row.get("mapRuleLabel") or row.get("map_rule_label"):
                cfg["mapRuleLabel"] = str(row.get("mapRuleLabel") or row.get("map_rule_label") or "").strip()

    out: list[dict[str, Any]] = []
    for key in order:
        cfg = grouped[key]
        seen_src: set[tuple[int, str]] = set()
        deduped: list[dict[str, Any]] = []
        for i, src in enumerate(cfg["sources"], start=1):
            if not src.get("slot"):
                src["slot"] = str(i)
            sk = (int(src["siteTaskId"]), str(src["sitePdfFieldId"]).lower())
            if sk in seen_src:
                continue
            seen_src.add(sk)
            deduped.append(src)
        cfg["sources"] = deduped
        if cfg["sources"]:
            out.append(cfg)
    return out


def configs_to_flat_rows(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """兼容仍读取 report_site_field_map 扁平原型的调用方。"""
    flat: list[dict[str, Any]] = []
    for cfg in normalize_report_site_field_configs(configs):
        report_pid = cfg["reportPdfFieldId"]
        for src in cfg.get("sources") or []:
            if not isinstance(src, dict):
                continue
            flat.append(
                {
                    "reportPdfFieldId": report_pid,
                    "siteTaskId": src["siteTaskId"],
                    "sitePdfFieldId": src["sitePdfFieldId"],
                    "siteTaskCode": src.get("siteTaskCode") or "",
                    "siteTaskName": src.get("siteTaskName") or "",
                    "slot": src.get("slot") or "",
                    "label": src.get("label") or "",
                    "mapSource": cfg.get("mapSource") or "",
                    "mapRuleLabel": cfg.get("mapRuleLabel") or "",
                    "valueTemplate": cfg.get("valueTemplate") or "",
                }
            )
    return flat


def parse_report_site_field_configs_from_bindings(bindings: dict | None) -> list[dict[str, Any]]:
    if not isinstance(bindings, dict):
        return []
    raw_cfg = bindings.get("report_site_field_configs")
    if isinstance(raw_cfg, list) and raw_cfg:
        return normalize_report_site_field_configs(raw_cfg)
    raw_map = bindings.get("report_site_field_map")
    if isinstance(raw_map, list) and raw_map:
        return normalize_report_site_field_configs(raw_map)
    return []


def render_report_value_template(template: str, slot_values: dict[str, str]) -> str:
    """将 {1}、{2}、{f3} 等占位替换为来源取值。"""
    text = str(template or "")
    if not text.strip() or not slot_values:
        return text
    keys = sorted({str(k) for k in slot_values if str(k)}, key=lambda x: (-len(x), x))
    for key in keys:
        val = str(slot_values.get(key) or "")
        for pat in (f"{{{key}}}", f"{{{{{key}}}}}"):
            text = text.replace(pat, val)
    return text


def merge_effective_report_site_field_configs(
    explicit_configs: list[dict[str, Any]],
    inferred_configs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    explicit_norm = normalize_report_site_field_configs(explicit_configs)
    inferred_norm = normalize_report_site_field_configs(inferred_configs)
    explicit_ids = {str(c["reportPdfFieldId"]).strip().lower() for c in explicit_norm}
    out = [dict(c) for c in explicit_norm]
    for cfg in inferred_norm:
        rid = str(cfg.get("reportPdfFieldId") or "").strip().lower()
        if not rid or rid in explicit_ids:
            continue
        out.append(
            dict(
                cfg,
                mapSource=str(cfg.get("mapSource") or "inferred"),
            )
        )
    return out


def infer_report_site_field_configs(
    report_parsed: dict | None,
    site_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    按与报告回填一致的规则推断映射配置（每报告栏位一条，可含多来源时的首条匹配）。
    """
    report_index = _build_pdf_field_meta_index(report_parsed)
    if not report_index:
        return []

    flat: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    for st in site_tasks or []:
        if not isinstance(st, dict):
            continue
        try:
            site_tid = int(st.get("id") or 0)
        except (TypeError, ValueError):
            site_tid = 0
        if site_tid <= 0:
            continue
        json_id = st.get("templateJsonId")
        if not json_id:
            continue
        site_lf = LibraryFile.objects.filter(
            pk=int(json_id), category=LibraryFile.CATEGORY_TEMPLATE
        ).first()
        site_parsed = _load_parsed_template_json(site_lf)
        site_index = _build_pdf_field_meta_index(site_parsed)
        if not site_index:
            continue

        site_by_submit: dict[str, list[str]] = {}
        site_by_label: dict[str, list[str]] = {}
        for site_pid, meta in site_index.items():
            sp = meta.get("submitPath") or ""
            if sp:
                site_by_submit.setdefault(sp, []).append(site_pid)
            for lk in (meta.get("placeholder"), meta.get("title"), meta.get("label")):
                nk = _normalize_label_key(lk)
                if nk:
                    site_by_label.setdefault(nk, []).append(site_pid)

        st_code = str(st.get("code") or "").strip()
        st_name = str(st.get("name") or "").strip()

        for report_pid, rmeta in report_index.items():
            matched_site_pids: list[tuple[str, str, str]] = []

            rsp = rmeta.get("submitPath") or ""
            if rsp and rsp in site_by_submit:
                for site_pid in site_by_submit[rsp]:
                    matched_site_pids.append((site_pid, "submitPath", rsp))

            if not matched_site_pids:
                for lk in (rmeta.get("placeholder"), rmeta.get("title"), rmeta.get("label")):
                    nk = _normalize_label_key(lk)
                    if nk and nk in site_by_label:
                        for site_pid in site_by_label[nk]:
                            label = str(lk or "").strip()
                            matched_site_pids.append((site_pid, "placeholder", label))
                        break

            for site_pid, map_source, rule_label in matched_site_pids:
                key = (report_pid, site_tid, site_pid)
                if key in seen:
                    continue
                seen.add(key)
                flat.append(
                    {
                        "reportPdfFieldId": report_pid,
                        "siteTaskId": site_tid,
                        "siteTaskCode": st_code,
                        "siteTaskName": st_name,
                        "sitePdfFieldId": site_pid,
                        "mapSource": map_source,
                        "mapRuleLabel": rule_label,
                    }
                )
    configs = normalize_report_site_field_configs(flat)
    for cfg in configs:
        if not cfg.get("mapSource"):
            cfg["mapSource"] = "inferred"
    return configs


def merge_effective_report_site_field_map(
    explicit_rows: list[dict[str, Any]],
    inferred_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """扁平原型兼容：由 effective configs 展开。"""
    return configs_to_flat_rows(
        merge_effective_report_site_field_configs(explicit_rows, inferred_rows)
    )


def _load_report_site_field_configs_from_json_file(
    json_lf: LibraryFile | None,
) -> list[dict[str, Any]]:
    if json_lf is None:
        return []
    try:
        path = pipeline_service.library_absolute_path(json_lf.relative_path)
        parsed = parse_template_json(path.read_text(encoding="utf-8", errors="replace"))
        b = parsed.get("bindings")
        if isinstance(b, dict):
            return parse_report_site_field_configs_from_bindings(b)
    except Exception:
        pass
    return []


def _serialize_task_brief(task: LibraryTask) -> dict[str, Any]:
    pdf_lf, json_lf = _first_template_files_for_task(task)
    return {
        "id": task.pk,
        "code": task.code,
        "name": task.name,
        "outputTarget": task.output_target,
        "templatePdfId": pdf_lf.pk if pdf_lf else None,
        "templatePdfName": pdf_lf.original_name if pdf_lf else "",
        "templateJsonId": json_lf.pk if json_lf else None,
        "templateJsonName": json_lf.original_name if json_lf else "",
    }


def build_report_task_template_context(
    user,
    *,
    library_task_id: int | None = None,
    template_json_file_id: int | None = None,
    template_pdf_file_id: int | None = None,
) -> dict[str, Any]:
    preferred = (
        LibraryTask.objects.filter(pk=library_task_id).first()
        if library_task_id
        else None
    )
    task = preferred
    if template_pdf_file_id:
        task = resolve_library_task_for_template_pdf(
            int(template_pdf_file_id), preferred_task=preferred
        )
    if task is None:
        return {"ok": False, "error": "任务模板不存在"}
    if not library_user_may_edit_library_task(user, task):
        return {"ok": False, "error": "无权维护该任务模板"}
    site_tasks: list[dict[str, Any]] = []
    bindings: dict[str, Any] = {}
    report_site_field_configs: list[dict[str, Any]] = []

    is_report = task.output_target == LibraryTask.OUTPUT_REPORT
    if is_report:
        for st in task.report_source_tasks.filter(
            output_target=LibraryTask.OUTPUT_SITE_RECORD
        ).order_by("code", "id"):
            site_tasks.append(_serialize_task_brief(st))

    json_lf = None
    if template_json_file_id:
        json_lf = LibraryFile.objects.filter(
            pk=template_json_file_id, category=LibraryFile.CATEGORY_TEMPLATE
        ).first()
    if json_lf is None and is_report:
        _pdf_lf, json_lf = get_task_template_pair(task)
    report_parsed: dict | None = None
    if json_lf and library_file_access_allowed(user, json_lf):
        report_parsed = _load_parsed_template_json(json_lf)
        report_site_field_configs = _load_report_site_field_configs_from_json_file(json_lf)
        if report_site_field_configs:
            bindings = {
                "report_site_field_configs": report_site_field_configs,
                "report_site_field_map": configs_to_flat_rows(report_site_field_configs),
            }

    inferred_report_site_field_configs: list[dict[str, Any]] = []
    effective_report_site_field_configs: list[dict[str, Any]] = []
    if is_report and site_tasks and report_parsed:
        inferred_report_site_field_configs = infer_report_site_field_configs(
            report_parsed, site_tasks
        )
        effective_report_site_field_configs = merge_effective_report_site_field_configs(
            report_site_field_configs, inferred_report_site_field_configs
        )

    mapping_hint = ""
    if is_report and not site_tasks:
        mapping_hint = (
            "当前报告模板尚未关联现场记录来源。请在「任务模板库」编辑该报告，"
            "在「报告来源现场记录」中勾选对应的现场记录任务模板。"
        )

    return {
        "ok": True,
        "task": _serialize_task_brief(task),
        "siteTasks": site_tasks,
        "bindings": bindings,
        "reportSiteFieldConfigs": report_site_field_configs,
        "reportSiteFieldMap": configs_to_flat_rows(report_site_field_configs),
        "inferredReportSiteFieldConfigs": inferred_report_site_field_configs,
        "inferredReportSiteFieldMap": configs_to_flat_rows(inferred_report_site_field_configs),
        "effectiveReportSiteFieldConfigs": effective_report_site_field_configs,
        "effectiveReportSiteFieldMap": configs_to_flat_rows(effective_report_site_field_configs),
        "isReportTemplate": is_report,
        "mappingReady": is_report and bool(site_tasks),
        "mappingHint": mapping_hint,
        "resolvedLibraryTaskId": task.pk,
    }


def build_site_template_bundle(user, *, site_task_id: int) -> dict[str, Any]:
    task = LibraryTask.objects.filter(pk=site_task_id).first()
    if task is None:
        return {"ok": False, "error": "现场记录任务不存在"}
    if task.output_target != LibraryTask.OUTPUT_SITE_RECORD:
        return {"ok": False, "error": "所选任务不是现场记录模板"}
    if not library_user_may_edit_library_task(user, task):
        return {"ok": False, "error": "无权访问该现场记录模板"}

    pdf_lf, json_lf = _first_template_files_for_task(task)
    if pdf_lf is None or not library_file_access_allowed(user, pdf_lf):
        return {"ok": False, "error": "该现场记录任务尚未绑定模板 PDF"}
    fields: list[dict[str, Any]] = []
    if json_lf and library_file_access_allowed(user, json_lf):
        try:
            path = pipeline_service.library_absolute_path(json_lf.relative_path)
            parsed = parse_template_json(path.read_text(encoding="utf-8", errors="replace"))
            fields = materialize_unified_pdf_fields(parsed.get("fields") or [])
        except Exception:
            fields = []

    from django.urls import reverse

    return {
        "ok": True,
        "task": _serialize_task_brief(task),
        "pdfUrl": reverse("htmlpdf_template_file", kwargs={"pk": pdf_lf.pk}),
        "fields": [
            {
                "pdfFieldId": str(f.get("pdfFieldId") or ""),
                "page": int(f.get("page") or 1),
                "x": float(f.get("x") or 0),
                "y": float(f.get("y") or 0),
                "w": float(f.get("w") or 0),
                "h": float(f.get("h") or 0),
                "placeholder": str(f.get("placeholder") or f.get("title") or ""),
                "fieldType": str(f.get("fieldType") or "text"),
            }
            for f in fields
            if isinstance(f, dict) and str(f.get("pdfFieldId") or "").strip()
        ],
    }


def normalize_report_site_field_map(rows: list) -> list[dict[str, Any]]:
    return configs_to_flat_rows(normalize_report_site_field_configs(rows))


def merge_report_site_map_into_bindings(
    bindings: dict | None, mappings: list
) -> dict:
    base = dict(bindings) if isinstance(bindings, dict) else {}
    configs = normalize_report_site_field_configs(mappings)
    base["report_site_field_configs"] = configs
    base["report_site_field_map"] = configs_to_flat_rows(configs)
    return base


def apply_report_site_field_map_to_value_mapping(
    value_mapping: dict,
    source_data: dict,
    bindings: dict | None,
    *,
    report_parsed_template: dict | None = None,
) -> None:
    """
    报告回填：显式映射优先于名称模糊匹配。
    从提交 JSON 按现场记录栏位的 submitPath/语义键取值，写入报告栏位对应语义键。
    """
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    if not isinstance(bindings, dict):
        return
    configs = parse_report_site_field_configs_from_bindings(bindings)
    if not configs:
        return

    from apps.api.inspection_report_make import (
        _apply_template_field_sources_to_mapping,
        _field_semantic_candidate_keys,
    )

    report_by_pdf: dict[str, dict] = {}
    if isinstance(report_parsed_template, dict):
        for f in report_parsed_template.get("fields") or []:
            if isinstance(f, dict):
                pid = str(f.get("pdfFieldId") or "").strip()
                if pid:
                    report_by_pdf[pid] = f

    site_parsed_cache: dict[int, dict | None] = {}

    def _parsed_for_site_task(site_task_id: int) -> dict | None:
        if site_task_id in site_parsed_cache:
            return site_parsed_cache[site_task_id]
        task = LibraryTask.objects.filter(pk=site_task_id).first()
        if task is None:
            site_parsed_cache[site_task_id] = None
            return None
        _pdf, json_lf = _first_template_files_for_task(task)
        if json_lf is None:
            site_parsed_cache[site_task_id] = None
            return None
        try:
            path = pipeline_service.library_absolute_path(json_lf.relative_path)
            site_parsed_cache[site_task_id] = parse_template_json(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except Exception:
            site_parsed_cache[site_task_id] = None
        return site_parsed_cache[site_task_id]

    def _pick_site_field_value(site_parsed: dict, site_pid: str) -> str | None:
        site_field = None
        for sf in site_parsed.get("fields") or []:
            if isinstance(sf, dict) and str(sf.get("pdfFieldId") or "").strip() == site_pid:
                site_field = sf
                break
        if site_field is None:
            return None
        site_slice: dict = {}
        _apply_template_field_sources_to_mapping(site_slice, source_data, [site_field])
        if not site_slice:
            return None
        for sk in _field_semantic_candidate_keys(site_field):
            if sk in site_slice and site_slice[sk] not in (None, ""):
                return str(site_slice[sk])
        for v in site_slice.values():
            if v not in (None, ""):
                return str(v)
        return None

    for cfg in configs:
        report_pid = str(cfg.get("reportPdfFieldId") or "").strip()
        sources = cfg.get("sources") if isinstance(cfg.get("sources"), list) else []
        if not report_pid or not sources:
            continue
        report_field = report_by_pdf.get(report_pid)
        if report_field is None:
            for rf in (report_parsed_template or {}).get("fields") or []:
                if isinstance(rf, dict) and str(rf.get("pdfFieldId") or "").strip() == report_pid:
                    report_field = rf
                    break
        if report_field is None:
            continue

        slot_values: dict[str, str] = {}
        picked_parts: list[str] = []
        for i, src in enumerate(sources, start=1):
            if not isinstance(src, dict):
                continue
            site_tid = int(src.get("siteTaskId") or 0)
            site_pid = str(src.get("sitePdfFieldId") or "").strip()
            if site_tid <= 0 or not site_pid:
                continue
            site_parsed = _parsed_for_site_task(site_tid)
            if not site_parsed:
                continue
            val = _pick_site_field_value(site_parsed, site_pid)
            if val is None:
                continue
            slot = str(src.get("slot") or str(i)).strip() or str(i)
            slot_values[slot] = val
            slot_values[str(i)] = val
            slot_values[site_pid] = val
            picked_parts.append(val)

        if not picked_parts:
            continue
        template = _coerce_value_template(cfg.get("valueTemplate") or cfg.get("value_template"))
        if template.strip():
            final_value = render_report_value_template(template, slot_values)
        elif len(picked_parts) == 1:
            final_value = picked_parts[0]
        else:
            final_value = "\n".join(picked_parts)

        for rk in _field_semantic_candidate_keys(report_field):
            value_mapping[rk] = final_value
