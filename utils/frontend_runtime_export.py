"""
移动端 / App 运行态前端 JSON 导出：在规则引擎结果上剥编辑器元数据，补齐六大章节与 matrixTable。
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from django.http import HttpResponse

logger = logging.getLogger(__name__)

# 现场记录六大章节：PDF 章节键 -> 运行态 section 元数据
SITE_RECORD_RUNTIME_SECTIONS: List[Dict[str, Any]] = [
    {
        "chapter_key": "site_unit_basic",
        "id": "sec_client_basic",
        "sectionKey": "client_basic_info",
        "sectionType": "clientBasicInfo",
        "order": 10,
        "capabilities": None,
        "behavior": None,
    },
    {
        "chapter_key": "site_device_basic",
        "id": "sec_equipment_basic",
        "sectionKey": "equipment_basic_info",
        "sectionType": "equipmentBasicInfo",
        "order": 20,
        "capabilities": ["deviceOcr"],
        "behavior": None,
    },
    {
        "chapter_key": "site_instruments_staff",
        "id": "sec_instrument_personnel",
        "sectionKey": "instrument_personnel",
        "sectionType": "instrumentPersonnel",
        "order": 30,
        "capabilities": ["instrumentSelect"],
        "behavior": None,
    },
    {
        "chapter_key": "site_qc_performance",
        "id": "sec_qc",
        "sectionKey": "quality_control",
        "sectionType": "qualityControl",
        "order": 40,
        "capabilities": ["retainPhotos"],
        "behavior": None,
    },
    {
        "chapter_key": "site_radiation_protection",
        "id": "sec_radiation_protection",
        "sectionKey": "radiation_protection",
        "sectionType": "radiationProtection",
        "order": 50,
        "capabilities": ["editableMatrixRows"],
        "behavior": {"maxCustomRows": 22},
    },
    {
        "chapter_key": "site_layout_diagram",
        "id": "sec_floor_plan",
        "sectionKey": "floor_plan",
        "sectionType": "floorPlan",
        "order": 60,
        "capabilities": None,
        "behavior": None,
    },
]

_CHAPTER_KEY_BY_SECTION_TYPE = {
    str(s["sectionType"]): str(s["chapter_key"]) for s in SITE_RECORD_RUNTIME_SECTIONS
}
_CHAPTER_META_BY_KEY = {str(s["chapter_key"]): s for s in SITE_RECORD_RUNTIME_SECTIONS}
_PROTECTION_CUSTOM_ROW_COUNT = 22

_RUNTIME_STRIP_ROOT_KEYS = (
    "pdf",
    "pdfBindings",
    "sections",
    "templateSections",
    "instrumentCatalogOptions",
    "instrumentsByScope",
    "instrumentSetsByScope",
    "instrumentBindings",
    "formSchema",
    "fields",
    "meta",
)

_RUNTIME_STRIP_FIELD_KEYS = (
    "rect",
    "pdfAnchor",
    "page",
    "x",
    "y",
    "w",
    "h",
    "__order",
    "__bbox",
    "source",
    "templateSectionKey",
    "templateSectionTitle",
)


def _site_record_section_titles() -> Dict[str, str]:
    try:
        from htmlpdf.full_text_coordinate_boxing import SITE_RECORD_TEMPLATE_SECTIONS

        specs = SITE_RECORD_TEMPLATE_SECTIONS
    except Exception:
        specs = []
    out: Dict[str, str] = {}
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        key = str(spec.get("key") or "").strip()
        if key:
            out[key] = str(spec.get("title") or key).strip()
    return out


def _section_chapter_key(sec: Dict[str, Any]) -> str:
    sk = str(sec.get("sectionKey") or sec.get("templateSectionKey") or "").strip()
    st = str(sec.get("sectionType") or "").strip()
    sid = str(sec.get("id") or "").strip()
    if sk in _CHAPTER_META_BY_KEY:
        return sk
    if st in _CHAPTER_KEY_BY_SECTION_TYPE:
        return _CHAPTER_KEY_BY_SECTION_TYPE[st]
    if sid in _CHAPTER_META_BY_KEY:
        return sid
    for meta in SITE_RECORD_RUNTIME_SECTIONS:
        if sid == str(meta.get("id") or ""):
            return str(meta["chapter_key"])
    title = str(sec.get("title") or "")
    for ck, t in _site_record_section_titles().items():
        if title and (title in t or t in title):
            return ck
    return ""


def _compact_runtime_field(
    field: Dict[str, Any],
    section_key: str = "",
    *,
    keep_pdf_anchor: bool = False,
) -> Dict[str, Any]:
    from utils.frontend_schema_rule_engine import _compact_single_form_field

    out = _compact_single_form_field(
        field,
        section_key,
        keep_pdf_anchor=keep_pdf_anchor,
    )
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    bucket = str(field.get("submitBucket") or src.get("submitBucket") or "").strip()
    if bucket:
        out["submitBucket"] = bucket
    elif out.get("submitPath"):
        sp = str(out["submitPath"])
        if "." in sp:
            out["submitBucket"] = sp.split(".", 1)[0]
    if field.get("editable") is True:
        out["editable"] = True
    for key in ("instrumentScope", "registrySlot"):
        val = field.get(key)
        if val not in (None, ""):
            out[key] = val
    return out


def _compact_runtime_matrix(
    matrix: Dict[str, Any],
    section_key: str = "",
    *,
    keep_pdf_anchor: bool = False,
) -> Dict[str, Any]:
    from utils.frontend_schema_rule_engine import _compact_matrix_object

    compacted = _compact_matrix_object(
        matrix,
        section_key,
        keep_pdf_anchor=keep_pdf_anchor,
    )
    row_headers = matrix.get("rowHeaderColumns")
    if isinstance(row_headers, list) and row_headers:
        compacted["rowHeaderColumns"] = []
        for col in row_headers:
            if not isinstance(col, dict):
                continue
            row: Dict[str, Any] = {}
            for k in ("id", "title", "merge", "width", "editable", "submitPath", "submitBucket"):
                v = col.get(k)
                if v is not None and v != "":
                    row[k] = v
            if row:
                compacted["rowHeaderColumns"].append(row)
    value_cols = matrix.get("valueColumns")
    if isinstance(value_cols, list) and value_cols:
        compacted["valueColumns"] = [
            {k: v for k, v in col.items() if k in ("id", "title", "fieldType", "unit", "width")}
            for col in value_cols
            if isinstance(col, dict)
        ]
    compacted.pop("sourceTable", None)
    return compacted


def _compact_runtime_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    keep_pdf_anchor = _is_pdf_overlay_payload(payload)
    for key in _RUNTIME_STRIP_ROOT_KEYS:
        payload.pop(key, None)
    payload.pop("pdfBindings", None)

    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            ch = _section_chapter_key(sec)
            sk = str(sec.get("sectionKey") or ch or sec.get("id") or "").strip()
            layout = str(sec.get("layout") or "form").strip().lower()
            if layout == "form":
                sec.pop("layout", None)
            elif layout:
                sec["layout"] = layout

            matrix = sec.get("matrix")
            if isinstance(matrix, dict):
                sec["matrix"] = _compact_runtime_matrix(
                    matrix,
                    sk,
                    keep_pdf_anchor=keep_pdf_anchor,
                )
            fields = sec.get("fields")
            if isinstance(fields, list):
                compacted = []
                for f in fields:
                    if not isinstance(f, dict):
                        continue
                    if str(f.get("type") or "").lower() == "table" and isinstance(f.get("columns"), list):
                        tbl = _compact_runtime_field(
                            f,
                            sk,
                            keep_pdf_anchor=keep_pdf_anchor,
                        )
                        if f.get("columns"):
                            tbl["columns"] = f["columns"]
                        if f.get("initialRows"):
                            tbl["initialRows"] = f["initialRows"]
                        compacted.append(tbl)
                    else:
                        compacted.append(
                            _compact_runtime_field(
                                f,
                                sk,
                                keep_pdf_anchor=keep_pdf_anchor,
                            )
                        )
                if compacted:
                    sec["fields"] = compacted
                else:
                    sec.pop("fields", None)
    from utils.frontend_schema_rule_engine import _prune_enums_to_used

    return _prune_enums_to_used(payload)


def _is_pdf_overlay_payload(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    value = payload.get("pdfOverlay")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _strip_runtime_field_noise(field: Dict[str, Any], *, keep_pdf_anchor: bool = False) -> None:
    for key in _RUNTIME_STRIP_FIELD_KEYS:
        if keep_pdf_anchor and key == "pdfAnchor":
            continue
        field.pop(key, None)


def _iter_runtime_fields(payload: Dict[str, Any]):
    keep_pdf_anchor = _is_pdf_overlay_payload(payload)
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            for f in sec.get("fields") or []:
                if isinstance(f, dict):
                    _strip_runtime_field_noise(f, keep_pdf_anchor=keep_pdf_anchor)
                    yield sec, f
            matrix = sec.get("matrix")
            if not isinstance(matrix, dict):
                continue
            for f in matrix.get("headerFields") or []:
                if isinstance(f, dict):
                    _strip_runtime_field_noise(f, keep_pdf_anchor=keep_pdf_anchor)
                    yield sec, f
            for row in matrix.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells")
                if isinstance(cells, dict):
                    for f in cells.values():
                        if isinstance(f, dict):
                            _strip_runtime_field_noise(f, keep_pdf_anchor=keep_pdf_anchor)
                            yield sec, f


def _radiation_column_to_cell_key(col_name: str, reading_index: int = 0) -> str:
    col = str(col_name or "").strip()
    if "备注" in col:
        return "remark"
    if "报出" in col:
        return "reportValue"
    if "均值" in col or "平均" in col:
        return "meanValue"
    if "读数" in col or "测量" in col:
        idx = int(reading_index or 1)
        return f"reading{min(max(idx, 1), 3)}"
    return re.sub(r"[^a-zA-Z0-9_]+", "_", col).strip("_") or "value"


def _protection_fields_have_matrix_semantics(fields: List[Dict[str, Any]]) -> bool:
    """防护章仅当 PDF/规则已标注表格行号时才升级为 matrixTable，否则平铺 fields 保留全部 pdfFieldId。"""
    with_row = 0
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        tbl = f.get("table") if isinstance(f.get("table"), dict) else {}
        if tbl.get("row") is not None:
            with_row += 1
            continue
        src = f.get("source") if isinstance(f.get("source"), dict) else {}
        sem = src.get("autoSemantic") if isinstance(src.get("autoSemantic"), dict) else {}
        if sem.get("row") is not None:
            with_row += 1
    return with_row > 0


def _protection_matrix_upgrade_viable(
    fields: List[Dict[str, Any]],
    grouped: Dict[int, Dict[str, Dict[str, Any]]],
    *,
    min_coverage: float = 0.75,
) -> bool:
    """
    matrixTable 仅适用于少量标准列（读数/均值/报出/备注/点位）。
    复杂 PDF（双组读数、序号、测量值、表前参数等）合并后会大量丢栏位，应保留平铺 fields。
    """
    total = sum(1 for f in fields or [] if isinstance(f, dict))
    if total <= 0:
        return False
    covered = sum(len(cells) for cells in (grouped or {}).values())
    if covered < max(1, int(total * min_coverage)):
        return False
    return True


def _group_protection_fields(fields: List[Dict[str, Any]]) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """按 table.row 分组防护表平铺栏位 -> row_index -> cell_key -> field。"""
    grouped: Dict[int, Dict[str, Dict[str, Any]]] = {}
    overflow: List[Dict[str, Any]] = []

    def _table_sem(f: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(f.get("table"), dict):
            return f["table"]
        src = f.get("source") if isinstance(f.get("source"), dict) else {}
        sem = src.get("autoSemantic") if isinstance(src.get("autoSemantic"), dict) else {}
        return sem if isinstance(sem, dict) else {}

    for f in fields or []:
        if not isinstance(f, dict):
            continue
        sem = _table_sem(f)
        row_raw = sem.get("row")
        try:
            row_idx = int(row_raw) if row_raw is not None and row_raw != "" else None
        except (TypeError, ValueError):
            row_idx = None
        col_name = str(sem.get("radiationColumn") or f.get("label") or "").strip()
        reading_index = 0
        try:
            reading_index = int(sem.get("readingIndex") or 0)
        except (TypeError, ValueError):
            pass
        cell_key = _radiation_column_to_cell_key(col_name, reading_index)
        if row_idx is None:
            overflow.append(f)
            continue
        grouped.setdefault(row_idx, {})[cell_key] = f

    # 无 row 的栏位按顺序追加到自定义行 0..21
    slot = 0
    for f in overflow:
        while slot < _PROTECTION_CUSTOM_ROW_COUNT and slot in grouped and "point" in grouped[slot]:
            slot += 1
        if slot >= _PROTECTION_CUSTOM_ROW_COUNT:
            grouped.setdefault(1000 + len(grouped), {})["extra"] = f
        else:
            grouped.setdefault(slot, {})[_radiation_column_to_cell_key(str(f.get("label") or ""), 0)] = f
            slot += 1
    return grouped


def _build_protection_matrix_from_fields(
    fields: List[Dict[str, Any]], section_title: str
) -> Dict[str, Any]:
    grouped = _group_protection_fields(fields)
    value_columns = [
        {"id": "reading1", "title": "测量读数M", "fieldType": "number", "unit": "μSv/h", "width": 0.12},
        {"id": "reading2", "title": "测量读数M", "fieldType": "number", "unit": "μSv/h", "width": 0.12},
        {"id": "reading3", "title": "测量读数M", "fieldType": "number", "unit": "μSv/h", "width": 0.12},
        {"id": "meanValue", "title": "测量均值M̄", "fieldType": "number", "unit": "μSv/h", "width": 0.12},
        {"id": "reportValue", "title": "报出值D", "fieldType": "number", "unit": "μSv/h", "width": 0.12},
        {"id": "remark", "title": "备注", "fieldType": "text", "unit": "", "width": 0.14},
    ]

    row_header_columns = [
        {
            "id": "point",
            "title": "检测点位",
            "editable": True,
            "merge": "none",
            "width": 0.22,
            "submitBucket": "testResult",
        }
    ]

    matrix_rows: List[Dict[str, Any]] = []
    used_keys = set()

    def _append_row(row_index: int, cells_src: Dict[str, Dict[str, Any]] | None, point_label: str = "") -> None:
        cells_out: Dict[str, Any] = {}
        if cells_src:
            for cell_key, src_field in cells_src.items():
                if cell_key == "extra":
                    continue
                compacted = _compact_runtime_field(copy.deepcopy(src_field), "site_radiation_protection")
                if not compacted.get("submitPath"):
                    compacted["submitPath"] = (
                        f"testResult.protectionPoints[{row_index}].{cell_key}"
                    )
                if not compacted.get("submitBucket"):
                    compacted["submitBucket"] = "testResult"
                cells_out[cell_key] = compacted
        headers = {"point": point_label}
        matrix_rows.append(
            {
                "id": f"protection_row_{row_index}",
                "rowIndex": row_index,
                "headers": headers,
                "cells": cells_out,
            }
        )

    # 先铺 22 个可编辑空点位行
    for i in range(_PROTECTION_CUSTOM_ROW_COUNT):
        src_cells = grouped.get(i)
        point_label = ""
        if src_cells:
            for f in src_cells.values():
                sem = f.get("table") if isinstance(f.get("table"), dict) else {}
                if not sem:
                    src = f.get("source") if isinstance(f.get("source"), dict) else {}
                    sem = src.get("autoSemantic") if isinstance(src.get("autoSemantic"), dict) else {}
                pt = str(sem.get("radiationPoint") or "").strip()
                if pt:
                    point_label = pt
                    break
        _append_row(i, src_cells, point_label)
        used_keys.add(i)

    # PDF 固定行（row >= 22 或不在 0..21 内）追加在后
    for row_idx in sorted(k for k in grouped.keys() if k not in used_keys):
        cells_src = grouped[row_idx]
        point_label = ""
        for f in cells_src.values():
            sem = f.get("table") if isinstance(f.get("table"), dict) else {}
            point_label = str(sem.get("radiationPoint") or "").strip()
            if point_label:
                break
        _append_row(row_idx, cells_src, point_label)

    # 为 rowHeaderColumns 中 point 列写入各行 submitPath
    for i, row in enumerate(matrix_rows):
        if i < _PROTECTION_CUSTOM_ROW_COUNT:
            row_header_columns[0]["submitPath"] = f"testResult.protectionPoints[{i}].point"

    return {
        "id": "sec_radiation_protection",
        "title": section_title or "工作场所放射防护检测结果",
        "sectionKey": "radiation_protection",
        "sectionType": "radiationProtection",
        "layout": "matrixTable",
        "capabilities": ["editableMatrixRows"],
        "behavior": {"maxCustomRows": _PROTECTION_CUSTOM_ROW_COUNT},
        "matrix": {
            "rowHeaderColumns": row_header_columns,
            "valueColumns": value_columns,
            "rows": matrix_rows,
        },
    }


def _ensure_protection_matrix_custom_rows(matrix: Dict[str, Any]) -> Dict[str, Any]:
    """已有 matrixTable 时补齐 22 个可编辑点位行。"""
    if not isinstance(matrix, dict):
        return matrix
    rows = list(matrix.get("rows") or [])
    by_index: Dict[int, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("rowIndex") if row.get("rowIndex") is not None else row.get("id", "").split("_")[-1])
        except (TypeError, ValueError):
            extras.append(row)
            continue
        if 0 <= idx < _PROTECTION_CUSTOM_ROW_COUNT:
            by_index[idx] = row
        else:
            extras.append(row)

    row_header_columns = matrix.get("rowHeaderColumns")
    if not isinstance(row_header_columns, list) or not row_header_columns:
        row_header_columns = [
            {
                "id": "point",
                "title": "检测点位",
                "editable": True,
                "merge": "none",
                "width": 0.22,
                "submitBucket": "testResult",
            }
        ]
        matrix["rowHeaderColumns"] = row_header_columns

    point_col = None
    for col in row_header_columns:
        if isinstance(col, dict) and str(col.get("id") or "") == "point":
            point_col = col
            break
    if point_col is None:
        point_col = {
            "id": "point",
            "title": "检测点位",
            "editable": True,
            "merge": "none",
            "width": 0.22,
            "submitBucket": "testResult",
        }
        row_header_columns.insert(0, point_col)
    point_col["editable"] = True

    new_rows: List[Dict[str, Any]] = []
    for i in range(_PROTECTION_CUSTOM_ROW_COUNT):
        point_col["submitPath"] = f"testResult.protectionPoints[{i}].point"
        if i in by_index:
            row = by_index[i]
            headers = row.get("headers") if isinstance(row.get("headers"), dict) else {}
            headers.setdefault("point", "")
            row["headers"] = headers
            row["rowIndex"] = i
            new_rows.append(row)
        else:
            new_rows.append(
                {
                    "id": f"protection_row_{i}",
                    "rowIndex": i,
                    "headers": {"point": ""},
                    "cells": {},
                }
            )
    new_rows.extend(extras)
    matrix["rows"] = new_rows
    return matrix


def _upgrade_radiation_protection_section(sec: Dict[str, Any], titles: Dict[str, str]) -> Dict[str, Any]:
    meta = _CHAPTER_META_BY_KEY["site_radiation_protection"]
    title = titles.get("site_radiation_protection") or str(sec.get("title") or meta.get("sectionKey"))
    layout = str(sec.get("layout") or "").lower()
    matrix = sec.get("matrix")

    if layout == "matrixtable" and isinstance(matrix, dict):
        sec = copy.deepcopy(sec)
        sec["matrix"] = _ensure_protection_matrix_custom_rows(copy.deepcopy(matrix))
        sec["layout"] = "matrixTable"
        return sec

    fields = sec.get("fields") if isinstance(sec.get("fields"), list) else []
    if not fields and not (isinstance(matrix, dict) and matrix.get("rows")):
        out = {
            "id": str(meta["id"]),
            "sectionKey": str(meta["sectionKey"]),
            "sectionType": str(meta["sectionType"]),
            "title": title,
            "layout": "matrixTable",
            "capabilities": list(meta["capabilities"] or []),
            "behavior": dict(meta["behavior"] or {}),
            "matrix": _ensure_protection_matrix_custom_rows({"rows": []}),
        }
        return out

    if fields and _protection_fields_have_matrix_semantics(fields):
        grouped = _group_protection_fields(fields)
        if _protection_matrix_upgrade_viable(fields, grouped):
            return _build_protection_matrix_from_fields(fields, title)

    if fields:
        keep_pdf_anchor = _is_pdf_overlay_payload({"pdfOverlay": True})
        compacted: List[Dict[str, Any]] = []
        for f in fields:
            if not isinstance(f, dict):
                continue
            compacted.append(
                _compact_runtime_field(
                    copy.deepcopy(f),
                    "site_radiation_protection",
                    keep_pdf_anchor=keep_pdf_anchor,
                )
            )
        out = {
            "id": str(meta["id"]),
            "sectionKey": str(meta["sectionKey"]),
            "sectionType": str(meta["sectionType"]),
            "title": title,
            "fields": compacted,
        }
        if meta.get("capabilities"):
            out["capabilities"] = list(meta["capabilities"])
        if meta.get("behavior"):
            out["behavior"] = dict(meta["behavior"])
        return out

    return sec


def _ensure_site_record_sections(payload: Dict[str, Any]) -> Dict[str, Any]:
    """保证现场记录步内始终包含六大业务章节（允许 fields 为空）。"""
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload
    titles = _site_record_section_titles()
    site_step = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("id") or "") == "step_site_record":
            site_step = step
            break
    if site_step is None:
        site_step = {"id": "step_site_record", "title": "检测原始记录", "sections": []}
        steps.insert(0, site_step)

    sections = site_step.get("sections")
    if not isinstance(sections, list):
        sections = []
        site_step["sections"] = sections

    by_chapter: Dict[str, Dict[str, Any]] = {}
    misc: List[Dict[str, Any]] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        ch = _section_chapter_key(sec)
        if ch in _CHAPTER_META_BY_KEY:
            if ch not in by_chapter:
                by_chapter[ch] = sec
            else:
                prev = by_chapter[ch]
                prev_fields = prev.get("fields") if isinstance(prev.get("fields"), list) else []
                new_fields = sec.get("fields") if isinstance(sec.get("fields"), list) else []
                prev["fields"] = prev_fields + new_fields
                if isinstance(sec.get("matrix"), dict) and not isinstance(prev.get("matrix"), dict):
                    prev["matrix"] = sec["matrix"]
        else:
            misc.append(sec)

    merged: List[Dict[str, Any]] = []
    for meta in sorted(SITE_RECORD_RUNTIME_SECTIONS, key=lambda m: int(m.get("order") or 0)):
        ch = str(meta["chapter_key"])
        title = titles.get(ch) or ch
        if ch == "site_radiation_protection":
            sec = by_chapter.get(ch)
            if sec:
                merged.append(_upgrade_radiation_protection_section(sec, titles))
            else:
                merged.append(_upgrade_radiation_protection_section({"fields": []}, titles))
            continue
        if ch in by_chapter:
            sec = copy.deepcopy(by_chapter[ch])
        else:
            sec = {
                "id": str(meta["id"]),
                "sectionKey": str(meta["sectionKey"]),
                "sectionType": str(meta["sectionType"]),
                "title": title,
                "fields": [],
            }
        sec["id"] = str(meta["id"])
        sec["sectionKey"] = str(meta["sectionKey"])
        sec["sectionType"] = str(meta["sectionType"])
        sec["title"] = title
        if meta.get("capabilities"):
            sec["capabilities"] = list(meta["capabilities"])
        if meta.get("behavior"):
            sec["behavior"] = dict(meta["behavior"])
        if not sec.get("fields") and ch != "site_layout_diagram":
            sec["fields"] = []
        merged.append(sec)

    site_step["sections"] = merged + [s for s in misc if s not in merged]
    payload["steps"] = [site_step] + [st for st in steps if st is not site_step]
    return payload


def _apply_floor_plan_runtime_types(payload: Dict[str, Any]) -> Dict[str, Any]:
    from utils.frontend_schema_rule_engine import (
        _field_is_floor_plan_image,
        _layout_section_field_targets,
        _section_is_layout_diagram,
    )

    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict) or not _section_is_layout_diagram(sec):
                continue
            for f in _layout_section_field_targets(sec):
                if not isinstance(f, dict):
                    continue
                if _field_is_floor_plan_image(f, in_layout_section=True):
                    f["type"] = "floorPlan"
                    sp = str(f.get("submitPath") or "").strip()
                    bucket = str(f.get("submitBucket") or "").strip().lower()
                    if not sp and bucket in ("signatures",):
                        f["submitPath"] = "signatures.floorPlan"
                        f.setdefault("submitBucket", "signatures")
                    if not f.get("pdfFieldId"):
                        pid = str(f.get("id") or "f637")
                        if pid.startswith("f") or pid.isdigit():
                            f["pdfFieldId"] = pid if pid.startswith("f") else f"f{pid}"
                    continue
                if str(f.get("type") or "").lower() == "number":
                    f["type"] = "text"
                    f.pop("precision", None)
                    f.pop("unit", None)
    return payload


def prepare_library_frontend_json_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    仅对「已有 steps、未走任务导出管线」的矩阵模板等做运行态压平。
    现场记录类模板请用 ``utils.frontend_export_pipeline.build_runtime_frontend_schema``。
    """
    if not isinstance(payload, dict):
        return payload
    out = copy.deepcopy(payload)
    out.pop("bindings", None)
    out.pop("pdfBindings", None)
    return finalize_runtime_frontend_export(out)


def _strip_radiation_protection_field_units(payload: Dict[str, Any]) -> Dict[str, Any]:
    """防护表：单位由 matrix 表头展示，各单元格/平铺栏位不写 unit。"""
    if not isinstance(payload, dict):
        return payload

    def _clear_unit(field: Dict[str, Any]) -> None:
        field.pop("unit", None)
        src = field.get("source")
        if isinstance(src, dict):
            src.pop("unit", None)

    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            ch = _section_chapter_key(sec)
            title = str(sec.get("title") or "")
            is_protection = (
                ch == "site_radiation_protection"
                or "放射防护" in title
                or "工作场所" in title
                or str(sec.get("sectionType") or "") == "radiationProtection"
            )
            if not is_protection:
                continue
            for fld in sec.get("fields") or []:
                if isinstance(fld, dict):
                    _clear_unit(fld)
            matrix = sec.get("matrix")
            if not isinstance(matrix, dict):
                continue
            for hf in matrix.get("headerFields") or []:
                if isinstance(hf, dict):
                    _clear_unit(hf)
            for row in matrix.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells")
                if isinstance(cells, dict):
                    for cell in cells.values():
                        if isinstance(cell, dict):
                            _clear_unit(cell)
    return payload


def finalize_runtime_frontend_export(
    payload: Dict[str, Any],
    *,
    pdf_fields: Optional[List[Any]] = None,
    radiation_chapter_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """移动端运行态 JSON：剥编辑器元数据，补齐六大章节、防护 matrixTable、sectionType。"""
    if not isinstance(payload, dict):
        return payload
    chapter_state = (
        radiation_chapter_state
        if isinstance(radiation_chapter_state, dict)
        else payload.get("radiationProtectionChapter")
    )
    result = copy.deepcopy(payload)
    for key in _RUNTIME_STRIP_ROOT_KEYS:
        result.pop(key, None)
    result.pop("instrumentCatalogOptions", None)

    from utils.frontend_schema_rule_engine import _strip_pdf_coords_from_form_schema

    result = _strip_pdf_coords_from_form_schema(result)

    result = _ensure_site_record_sections(result)
    result = _apply_floor_plan_runtime_types(result)
    try:
        from radiation_detection_report.chapter5_field_sync import apply_chapter_formulas_to_export_payload

        result = apply_chapter_formulas_to_export_payload(
            result,
            chapter_state,
            pdf_fields=pdf_fields,
        )
    except Exception as exc:
        logger.debug("apply_chapter_formulas_to_export_payload skipped: %s", exc)
    result = _compact_runtime_payload(result)
    result = _strip_radiation_protection_field_units(result)
    if isinstance(result, dict):
        result.setdefault("schema", "frontend_form_schema/v1")
    return result


def count_runtime_export_stats(payload: Dict[str, Any]) -> Dict[str, int]:
    stats = {
        "steps": 0,
        "sections": 0,
        "fields": 0,
        "matrixRows": 0,
        "matrixCells": 0,
    }
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        stats["steps"] += 1
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            stats["sections"] += 1
            stats["fields"] += len(sec.get("fields") or [])
            matrix = sec.get("matrix")
            if not isinstance(matrix, dict):
                continue
            stats["fields"] += len(matrix.get("headerFields") or [])
            for row in matrix.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                stats["matrixRows"] += 1
                cells = row.get("cells")
                if isinstance(cells, dict):
                    stats["matrixCells"] += len(cells)
    return stats


def serialize_runtime_frontend_json(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def runtime_export_etag(body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()[:32]
    return f'"{digest}"'


def build_runtime_json_http_response(
    payload: Dict[str, Any],
    *,
    request,
    task_no: str = "",
) -> HttpResponse:
    """紧凑 JSON + ETag；供 GZipMiddleware 压缩。"""
    body = serialize_runtime_frontend_json(payload)
    etag = runtime_export_etag(body)
    inm = str(request.META.get("HTTP_IF_NONE_MATCH") or "").strip()
    if inm and inm == etag:
        resp = HttpResponse(status=304)
        resp["ETag"] = etag
        resp["Cache-Control"] = "private, max-age=3600"
        return resp

    stats = count_runtime_export_stats(payload)
    logger.info(
        "export-frontend-json taskNo=%s bytes=%d steps=%d sections=%d fields=%d "
        "matrixRows=%d matrixCells=%d",
        task_no,
        len(body),
        stats["steps"],
        stats["sections"],
        stats["fields"],
        stats["matrixRows"],
        stats["matrixCells"],
    )

    resp = HttpResponse(body, content_type="application/json; charset=utf-8")
    resp["ETag"] = etag
    resp["Cache-Control"] = "private, max-age=3600"
    return resp
