"""
根据 export-frontend-json / auxiliary 前端模板 JSON，生成模拟检测提交 JSON（用于回填联调）。
"""
from __future__ import annotations

import copy
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_F_ID_RE = re.compile(r"^f\d+$", re.IGNORECASE)
_MOCK_DEFAULT_PRECISION = 2
_RP_ROW_INDEX_RE = re.compile(r"_r(\d+)(?:_(.+))?$", re.IGNORECASE)
# chapter5_field_sync 按 PDF 行坐标聚合时 radiationPoint 形如 rp_p5_y384.5
_RP_COORD_POINT_RE = re.compile(r"^rp_p(\d+)_y([\d.]+)$", re.IGNORECASE)
_SLASH = "/"
_ASSIGNED_SEQ_VALUE_RE = re.compile(r"^\d+(?:~\d+)?$")
_RP_BINDING_G1_READING_KEYS = ("reading_1", "reading_2", "reading_3")
_RP_BINDING_G2_READING_KEYS = ("reading_1_2", "reading_2_2", "reading_3_2")
_RP_BINDING_G1_DATA_KEYS = _RP_BINDING_G1_READING_KEYS + ("mean_m", "report_d")
_RP_BINDING_G2_DATA_KEYS = _RP_BINDING_G2_READING_KEYS + ("mean_m_2", "report_d_2")
_RP_BINDING_READING_KEYS = _RP_BINDING_G1_READING_KEYS + _RP_BINDING_G2_READING_KEYS
_RP_BINDING_DATA_KEYS = _RP_BINDING_G1_DATA_KEYS + _RP_BINDING_G2_DATA_KEYS + ("remark",)
_RP_LOCATION_COLUMN_NAMES = frozenset({"检测点位置", "位置细分"})
_RP_CHAPTER_SECTION_KEY = "site_radiation_protection"
_RP_PDF_DATA_LABEL_SKIP_RE = re.compile(
    r"读数|均值|报出|测量值|序号|校准因子|修正系数|本底",
    re.IGNORECASE,
)


def _binding_data_keys_for_group(group: int | None) -> tuple[str, ...]:
    if group == 1:
        return _RP_BINDING_G1_DATA_KEYS
    if group == 2:
        return _RP_BINDING_G2_DATA_KEYS
    return _RP_BINDING_DATA_KEYS


def _binding_reading_keys_for_group(group: int | None) -> tuple[str, ...]:
    if group == 1:
        return _RP_BINDING_G1_READING_KEYS
    if group == 2:
        return _RP_BINDING_G2_READING_KEYS
    return _RP_BINDING_READING_KEYS


def _binding_has_group_slots(binding: dict, group: int) -> bool:
    keys = _binding_reading_keys_for_group(group)
    return any(str(binding.get(key) or "").strip() for key in keys)


def _binding_is_dual_capable(binding: dict) -> bool:
    try:
        from radiation_detection_report.chapter5_field_sync import _binding_has_dual_group

        return _binding_has_dual_group(binding)
    except ImportError:
        return _binding_has_group_slots(binding, 2)


def _rp_chapter_from_frontend(frontend_obj: dict) -> dict | None:
    chapter = frontend_obj.get("radiationProtectionChapter")
    if isinstance(chapter, dict):
        return chapter
    for key in ("formSchema", "form_schema"):
        container = frontend_obj.get(key)
        if isinstance(container, dict):
            nested = container.get("radiationProtectionChapter")
            if isinstance(nested, dict):
                return nested
    return None


def _rp_bindings_indicate_dual_group(bindings: list[dict], frontend_obj: dict) -> bool:
    if sum(1 for binding in bindings if _binding_is_dual_capable(binding)) >= 3:
        return True
    chapter = _rp_chapter_from_frontend(frontend_obj)
    if not isinstance(chapter, dict):
        return False
    mf = chapter.get("meanFormula")
    if isinstance(mf, dict):
        if str(mf.get("mode") or "") == "per_row_avg_dual":
            return True
        try:
            if int(mf.get("groups") or 1) >= 2:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _is_assigned_seq_value(value: Any) -> bool:
    if value in (None, "", _SLASH):
        return False
    return bool(_ASSIGNED_SEQ_VALUE_RE.match(str(value).strip()))


def _payload_pid_value(payload: dict, pid: str) -> Any:
    pid = str(pid or "").strip().lower()
    dd = payload.get("dynamicData") if isinstance(payload.get("dynamicData"), dict) else {}
    tr = payload.get("testResult") if isinstance(payload.get("testResult"), dict) else {}
    if pid in dd and dd[pid] not in (None, ""):
        return dd[pid]
    return tr.get(pid)


def _form_schema_block(frontend_obj: dict) -> dict:
    block = frontend_obj.get("formSchema")
    return block if isinstance(block, dict) else {}


def _frontend_steps(frontend_obj: dict) -> list:
    steps = frontend_obj.get("steps")
    if isinstance(steps, list):
        return steps
    return _form_schema_block(frontend_obj).get("steps") or []


def _load_library_task_template_json(library_task) -> dict | None:
    if library_task is None:
        return None
    try:
        from apps.core import pipeline_service
        from apps.core.library_file_service import library_file_exists_on_disk
        from apps.core.models import LibraryFile

        for lf in library_task.library_files.filter(
            category=LibraryFile.CATEGORY_TEMPLATE
        ).order_by("-created_at", "-id"):
            if not library_file_exists_on_disk(lf):
                continue
            if not (lf.original_name or "").lower().endswith(".json"):
                continue
            if (lf.original_name or "").endswith("_frontend.json"):
                continue
            path = pipeline_service.library_absolute_path(lf.relative_path)
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            return obj if isinstance(obj, dict) else None
    except Exception:
        return None
    return None


def _merged_pdf_field_items(
    frontend_obj: dict,
    *,
    library_task=None,
) -> list[dict]:
    """合并 runtime / formSchema / 库任务模板上的 pdf.fields（按 pdfFieldId 补缺）。"""
    by_pid: dict[str, dict] = {}
    sources: list[list[dict]] = []

    for block_key in ("pdf",):
        block = frontend_obj.get(block_key)
        if isinstance(block, dict) and isinstance(block.get("fields"), list):
            sources.append(block["fields"])

    form_pdf = _form_schema_block(frontend_obj).get("pdf")
    if isinstance(form_pdf, dict) and isinstance(form_pdf.get("fields"), list):
        sources.append(form_pdf["fields"])

    disk = _load_library_task_template_json(library_task)
    if isinstance(disk, dict):
        disk_pdf = disk.get("pdf")
        if isinstance(disk_pdf, dict) and isinstance(disk_pdf.get("fields"), list):
            sources.append(disk_pdf["fields"])

    for items in sources:
        for item in items:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("pdfFieldId") or "").strip().lower()
            if not pid or not _F_ID_RE.match(pid):
                continue
            if pid not in by_pid:
                by_pid[pid] = copy.deepcopy(item)

    return list(by_pid.values())


def _enrich_frontend_pdf_fields_for_mock(
    frontend_obj: dict,
    *,
    library_task=None,
) -> dict:
    merged_items = _merged_pdf_field_items(frontend_obj, library_task=library_task)
    if not merged_items:
        return frontend_obj
    pdf_block = frontend_obj.get("pdf")
    if not isinstance(pdf_block, dict):
        pdf_block = {}
        frontend_obj["pdf"] = pdf_block
    existing = pdf_block.get("fields")
    if not isinstance(existing, list):
        existing = []
        pdf_block["fields"] = existing
    have = {
        str(item.get("pdfFieldId") or "").strip().lower()
        for item in existing
        if isinstance(item, dict) and str(item.get("pdfFieldId") or "").strip()
    }
    for item in merged_items:
        pid = str(item.get("pdfFieldId") or "").strip().lower()
        if pid and pid not in have:
            existing.append(copy.deepcopy(item))
            have.add(pid)
    return frontend_obj


def _iter_form_fields(frontend_obj: dict) -> Iterable[dict]:
    """遍历 steps.sections 下 fields / matrix 全部控件。"""
    steps = _frontend_steps(frontend_obj)
    if not isinstance(steps, list):
        return

    def _walk_fields(fields: list | None):
        if not isinstance(fields, list):
            return
        for field in fields:
            if isinstance(field, dict):
                yield field

    for step in steps:
        if not isinstance(step, dict):
            continue
        yield from _walk_fields(step.get("fields"))
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            yield from _walk_fields(sec.get("fields"))
            matrix = sec.get("matrix")
            if not isinstance(matrix, dict):
                continue
            yield from _walk_fields(matrix.get("headerFields"))
            for row in matrix.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells")
                if isinstance(cells, dict):
                    for cell in cells.values():
                        if isinstance(cell, dict):
                            yield cell


def _field_from_pdf_entry(item: dict) -> dict:
    """将 pdf.fields 条目转为可参与模拟填写的最小栏位结构。"""
    pid = str(item.get("pdfFieldId") or "").strip()
    label = str(item.get("id") or pid).strip()
    ftype = "number"
    if not re.search(r"读数|均值|报出|测量值", label, re.I):
        ftype = "text"
    out: dict[str, Any] = {
        "id": pid or label,
        "pdfFieldId": pid,
        "label": label,
        "hierarchyKey": label,
        "type": ftype,
        "submitPath": f"testResult.{pid}" if pid else "",
        "submitBucket": "testResult",
    }
    sk = str(item.get("templateSectionKey") or "").strip()
    if sk:
        out["templateSectionKey"] = sk
    rect = item.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) >= 5:
        try:
            page = int(rect[0])
            x, y, w, h = float(rect[1]), float(rect[2]), float(rect[3]), float(rect[4])
            out["pdfAnchor"] = {
                "page": page,
                "rect": [round(x, 2), round(y, 2), round(x + w, 2), round(y + h, 2)],
            }
        except (TypeError, ValueError):
            pass
    for key in (
        "fieldExpression",
        "formula",
        "fieldExpressionRules",
        "formulaRules",
        "fieldFormulaUserOverride",
        "precision",
    ):
        if item.get(key) not in (None, "", []):
            out[key] = copy.deepcopy(item.get(key))
    return out


def _iter_all_template_fields(frontend_obj: dict) -> Iterable[dict]:
    """steps/matrix 栏位 + 仅存在于 pdf.fields 的栏位（避免漏填）。"""
    seen: set[str] = set()
    for field in _iter_form_fields(frontend_obj):
        if not isinstance(field, dict):
            continue
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip().lower()
        if pid and _F_ID_RE.match(pid):
            seen.add(pid)
        yield field

    pdf_items = _merged_pdf_field_items(frontend_obj)
    for item in pdf_items:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("pdfFieldId") or "").strip().lower()
        if not pid or not _F_ID_RE.match(pid) or pid in seen:
            continue
        seen.add(pid)
        yield _field_from_pdf_entry(item)


def _merge_rp_binding_lists(*groups: list[dict]) -> list[dict]:
    """按 radiationPoint 合并 binding，保留单元格 pid 更完整的一行。"""
    by_point: dict[str, dict] = {}

    def _score(binding: dict) -> int:
        if not isinstance(binding, dict):
            return 0
        return sum(
            1
            for key in _RP_BINDING_DATA_KEYS + ("seq_no",)
            if str(binding.get(key) or "").strip()
        )

    for bindings in groups:
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            pt = str(binding.get("radiationPoint") or "").strip()
            if not pt:
                continue
            prev = by_point.get(pt)
            if prev is None or _score(binding) > _score(prev):
                by_point[pt] = copy.deepcopy(binding)
    return list(by_point.values())


def _set_nested(obj: dict, path: str, value: Any) -> None:
    """按 a.b.c 写入嵌套 dict（仅 dict 路径）。"""
    parts = [p for p in str(path or "").split(".") if p]
    if not parts:
        return
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _field_has_formula(field: dict) -> bool:
    if str(field.get("type") or "").strip().lower() == "computed":
        return True
    if str(field.get("formula") or field.get("fieldExpression") or "").strip():
        return True
    rules = field.get("formulaRules") or field.get("fieldExpressionRules")
    return isinstance(rules, list) and bool(rules)


def _field_is_verdict(field: dict) -> bool:
    ftype = str(field.get("type") or "").strip().lower()
    if ftype == "verdict":
        return True
    if str(field.get("rule") or "").strip():
        return True
    if isinstance(field.get("fieldVerdict"), dict) and field.get("fieldVerdict"):
        return True
    label = str(field.get("label") or "")
    return "单项判定" in label or label.strip() in ("判定", "合格", "不合格")


def _field_should_skip_mock_fill(field: dict) -> bool:
    ftype = str(field.get("type") or "text").strip().lower()
    if ftype in (
        "computed",
        "verdict",
        "signature",
        "floorplan",
        "floorplan",
        "instrument_select",
        "image",
        "table",
    ):
        return True
    return _field_has_formula(field) or _field_is_verdict(field)


def _field_precision(field: dict | None = None) -> int:
    """非第五章默认两位；第五章数值格统一三位（与模板 precision=3 一致）。"""
    if isinstance(field, dict):
        try:
            from radiation_detection_report.chapter5_field_sync import (
                PROTECTION_NUMBER_PRECISION,
                field_is_protection_chapter_numeric_cell,
            )

            if field_is_protection_chapter_numeric_cell(field):
                try:
                    prec = int(field.get("precision"))
                except (TypeError, ValueError):
                    prec = PROTECTION_NUMBER_PRECISION
                return max(0, prec)
        except ImportError:
            pass
    return _MOCK_DEFAULT_PRECISION


def _format_mock_field_value(field: dict | None, value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        prec = _field_precision(field)
        if prec == 0:
            return int(round(float(value)))
        return round(float(value), prec)
    if value is None:
        return None
    return value if isinstance(value, (int, float, bool)) else str(value)


def _mock_random_number(rng: random.Random, field: dict) -> float | int:
    label = str(field.get("label") or "")
    prec = _field_precision(field)
    jct = field.get("judgmentCriteriaByTestType")
    if isinstance(jct, dict):
        crit = str(jct.get("acceptance") or jct.get("status") or "")
        m = re.search(r"±\s*([\d.]+)", crit)
        if m:
            tol = float(m.group(1))
            if rng.random() < 0.68:
                v = rng.uniform(-tol * 0.82, tol * 0.82)
            else:
                v = rng.choice(
                    [
                        rng.uniform(tol * 1.15, tol * 2.8),
                        rng.uniform(-tol * 2.8, -tol * 1.15),
                    ]
                )
            return _format_mock_field_value(field, v)
    upper = label.upper()
    if "KV" in upper:
        return rng.randint(90, 140)
    if "MA" in upper:
        return rng.randint(50, 420)
    if "%" in label or "率" in label:
        return _format_mock_field_value(field, rng.uniform(0.15, 18.6))
    if "mm" in label.lower() or "cm" in label.lower():
        return _format_mock_field_value(field, rng.uniform(0.2, 48.7))
    return _format_mock_field_value(field, rng.uniform(0.4, 127.3))


def _mock_scalar_for_field(
    field: dict,
    *,
    enums: dict,
    rng: random.Random,
) -> Any:
    ftype = str(field.get("type") or "text").strip().lower()
    pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
    label = str(field.get("label") or "").strip()

    if _field_should_skip_mock_fill(field):
        return None

    if ftype == "number":
        return _mock_random_number(rng, field)

    if ftype == "boolean":
        return rng.choice([True, False])

    if ftype in ("radio", "select"):
        enum_ref = str(field.get("enumRef") or "").strip()
        opts = enums.get(enum_ref) if enum_ref else None
        if isinstance(opts, list) and opts:
            pick = opts[rng.randint(0, len(opts) - 1)]
            if isinstance(pick, dict):
                return pick.get("value") or pick.get("id") or "yes"
            return str(pick)
        if "否" in label or label.endswith("无"):
            return "no"
        return rng.choice(["yes", "no"])

    if ftype == "date":
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        return f"2026-{month:02d}-{day:02d}"

    if ftype in ("text", "textarea", ""):
        if pid in ("f1",) or "委托编号" in label:
            return None
        if pid in ("f2",) or "受检编号" in label:
            return None
        if "编号" in label and "委托" not in label:
            return f"SN-{rng.randint(10000, 99999)}"
        if "名称" in label or "单位" in label:
            return f"模拟{label or pid}_{rng.randint(10, 99)}"
        if "kV" in label:
            return str(rng.randint(100, 130))
        if "mA" in label:
            return str(rng.randint(100, 350))
        return f"模拟_{pid or label or 'field'}_{rng.randint(100, 999)}"

    return f"mock_{pid or ftype}_{rng.randint(10, 9999)}"


def _read_field_value_from_payload(payload: dict, field: dict) -> Any:
    pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
    dd = payload.get("dynamicData")
    if isinstance(dd, dict) and pid and pid in dd:
        return dd[pid]
    submit_path = str(field.get("submitPath") or "").strip()
    if not submit_path:
        return None
    if submit_path.startswith("dynamicData."):
        leaf = submit_path.split(".", 1)[1]
        if isinstance(dd, dict):
            return dd.get(leaf)
        return None
    parts = submit_path.split(".")
    cur: Any = payload
    for part in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _is_background_level_field(field: dict) -> bool:
    blob = " ".join(
        str(field.get(k) or "")
        for k in ("label", "hierarchyKey", "id", "submitPath")
    )
    return "本底" in blob or "本底水平" in blob


def _field_pdf_anchor_rect(field: dict) -> tuple[int, float, float]:
    anchor = field.get("pdfAnchor")
    if not isinstance(anchor, dict):
        return 5, 0.0, 0.0
    rect = anchor.get("rect")
    try:
        page = int(anchor.get("page") or 5)
    except (TypeError, ValueError):
        page = 5
    if isinstance(rect, (list, tuple)) and len(rect) >= 2:
        try:
            return page, float(rect[0]), float(rect[1])
        except (TypeError, ValueError):
            pass
    return page, 0.0, 0.0


def _field_pdf_anchor_y_bounds(field: dict) -> tuple[int, float, float]:
    """返回 (page, y0, y1)。"""
    anchor = field.get("pdfAnchor")
    if not isinstance(anchor, dict):
        return 5, 0.0, 0.0
    rect = anchor.get("rect")
    try:
        page = int(anchor.get("page") or 5)
    except (TypeError, ValueError):
        page = 5
    if isinstance(rect, (list, tuple)) and len(rect) >= 4:
        try:
            y0 = float(rect[1])
            y1 = float(rect[3])
            if y1 < y0:
                y0, y1 = y1, y0
            return page, y0, y1
        except (TypeError, ValueError):
            pass
    _, _, y0 = _field_pdf_anchor_rect(field)
    return page, y0, y0


def _field_pdf_anchor_y_center(field: dict) -> tuple[int, float]:
    page, y0, y1 = _field_pdf_anchor_y_bounds(field)
    return page, (y0 + y1) / 2.0


def _seq_field_anchor_height(field: dict) -> float:
    _, y0, y1 = _field_pdf_anchor_y_bounds(field)
    return abs(y1 - y0)


def _is_rp_table_seq_field(field: dict) -> bool:
    from radiation_detection_report.chapter5_field_sync import is_rp_table_seq_field

    return is_rp_table_seq_field(field)


def _is_rp_preface_factor_field(field: dict) -> bool:
    """第五章表前区：校准因子 k1/k2、修正系数等（参与报出值公式，不是表格序号）。"""
    hk = str(field.get("hierarchyKey") or field.get("label") or "")
    if any(tok in hk for tok in ("校准因子", "修正系数", "探测下限", "响应时间", "仪器校准因子")):
        return True
    label = str(field.get("label") or "").strip()
    if re.match(r"^序号_r\d+", label, re.IGNORECASE) and not _is_rp_table_seq_field(field):
        return True
    return False


def _field_radiation_column(field: dict) -> str:
    tbl = field.get("table") if isinstance(field.get("table"), dict) else {}
    for key in ("radiationColumn", "typeName"):
        v = str(tbl.get(key) or "").strip()
        if v:
            return v
    try:
        from radiation_detection_report.chapter5_field_sync import protection_field_semantic_unified

        sem = protection_field_semantic_unified(field)
        if isinstance(sem, dict):
            for key in ("radiationColumn", "typeName"):
                v = str(sem.get(key) or "").strip()
                if v:
                    return v
    except ImportError:
        pass
    return ""


def _is_rp_location_field(field: dict) -> bool:
    """检测点位置列（含 table.radiationColumn，不依赖 label 含 _rN）。"""
    if _is_rp_preface_factor_field(field) or _is_background_level_field(field):
        return False
    col = _field_radiation_column(field)
    if col in _RP_LOCATION_COLUMN_NAMES:
        return True
    label = str(field.get("label") or field.get("hierarchyKey") or "")
    if "检测点位置" in label and re.search(r"_r\d+", label, re.IGNORECASE):
        return True
    return False


def _is_rp_location_pdf_field(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    sk = str(item.get("templateSectionKey") or "").strip()
    if sk != _RP_CHAPTER_SECTION_KEY:
        return False
    label = str(item.get("id") or item.get("pdfFieldId") or "")
    if not label or "本底" in label:
        return False
    if _RP_PDF_DATA_LABEL_SKIP_RE.search(label):
        return False
    return True


def _collect_rp_pdf_section_pids(
    frontend_obj: dict,
    bindings: list[dict] | None = None,
) -> set[str]:
    """pdf.fields 上标记为第五章的所有栏位 pid（弥补 steps 缺失 sectionKey）。"""
    pids: set[str] = set()
    for item in _merged_pdf_field_items(frontend_obj):
        if str(item.get("templateSectionKey") or "").strip() != _RP_CHAPTER_SECTION_KEY:
            continue
        pid = str(item.get("pdfFieldId") or "").strip().lower()
        if pid and _F_ID_RE.match(pid):
            pids.add(pid)
    if isinstance(bindings, list):
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            for pid in _binding_cell_pids(binding):
                pids.add(pid)
            seq_pid = str(binding.get("seq_no") or "").strip().lower()
            if seq_pid and _F_ID_RE.match(seq_pid):
                pids.add(seq_pid)
    return pids


def _resolve_field_protection_meta(
    field: dict,
    *,
    pid_to_field: dict[str, dict] | None = None,
    rp_pdf_pids: set[str] | None = None,
) -> dict:
    """合并 pdf.fields 元数据，供第五章识别使用。"""
    if not isinstance(field, dict):
        return {}
    pid = str(field.get("pdfFieldId") or field.get("id") or "").strip().lower()
    merged = copy.deepcopy(field)
    if pid_to_field and pid and pid in pid_to_field:
        src = pid_to_field[pid]
        if isinstance(src, dict):
            for key in (
                "templateSectionKey",
                "sectionKey",
                "table",
                "fieldExpression",
                "formula",
                "fieldExpressionRules",
                "formulaRules",
                "hierarchyKey",
                "label",
            ):
                if not str(merged.get(key) or "").strip() and src.get(key) not in (None, "", []):
                    merged[key] = copy.deepcopy(src.get(key))
            if rp_pdf_pids and pid in rp_pdf_pids and not str(merged.get("templateSectionKey") or "").strip():
                merged["templateSectionKey"] = _RP_CHAPTER_SECTION_KEY
    elif rp_pdf_pids and pid and pid in rp_pdf_pids:
        merged.setdefault("templateSectionKey", _RP_CHAPTER_SECTION_KEY)
    return merged


def _is_protection_section_field(
    field: dict,
    *,
    pid_to_field: dict[str, dict] | None = None,
    rp_pdf_pids: set[str] | None = None,
) -> bool:
    """第五章防护表栏位（含 steps / pdf.fields / matrixTable cells）。"""
    merged = _resolve_field_protection_meta(
        field,
        pid_to_field=pid_to_field,
        rp_pdf_pids=rp_pdf_pids,
    )
    pid = str(merged.get("pdfFieldId") or merged.get("id") or "").strip().lower()
    if rp_pdf_pids and pid and pid in rp_pdf_pids:
        return True
    if _field_radiation_column(merged) in _RP_LOCATION_COLUMN_NAMES:
        return True
    try:
        from radiation_detection_report.chapter5_field_sync import is_protection_pdf_field

        return is_protection_pdf_field(merged)
    except ImportError:
        sk = str(merged.get("templateSectionKey") or merged.get("sectionKey") or "").strip()
        if sk in (_RP_CHAPTER_SECTION_KEY, "radiation_protection"):
            return True
        if str(merged.get("sectionType") or "").strip() == "radiationProtection":
            return True
        label = str(merged.get("hierarchyKey") or merged.get("label") or "")
        if not re.search(r"_r\d+", label, re.I):
            return False
        return bool(re.search(r"_测量读数M\d*|_测量均值|_报出值D|_报出值", label, re.I))


def _is_rp_table_data_field(
    field: dict,
    *,
    pid_to_field: dict[str, dict] | None = None,
    rp_pdf_pids: set[str] | None = None,
) -> bool:
    """防护表数据格（读数/均值/报出），不含序号、检测点位置、表前因子。"""
    merged = _resolve_field_protection_meta(
        field,
        pid_to_field=pid_to_field,
        rp_pdf_pids=rp_pdf_pids,
    )
    if _is_background_level_field(merged) or _is_rp_preface_factor_field(merged):
        return False
    if _is_rp_table_seq_field(merged):
        return False
    if _is_rp_location_field(merged):
        return False
    label = str(merged.get("hierarchyKey") or merged.get("label") or "")
    if "检测点位置" in label:
        return False
    if not _is_protection_section_field(
        merged,
        pid_to_field=pid_to_field,
        rp_pdf_pids=rp_pdf_pids,
    ):
        return False
    try:
        from radiation_detection_report.chapter5_field_sync import field_looks_like_protection_table_cell

        if field_looks_like_protection_table_cell(merged):
            return True
    except ImportError:
        pass
    tbl = merged.get("table") if isinstance(merged.get("table"), dict) else {}
    col = str(tbl.get("radiationColumn") or "").strip()
    if col and any(tok in col for tok in ("读数", "均值", "报出", "测量值")):
        return True
    return bool(re.search(r"_测量读数M\d*|_测量均值|_报出值D|_报出值|_测量值", label, re.I))


def _is_rp_table_managed_field(
    field: dict,
    *,
    managed_pids: set[str] | None = None,
    rp_pdf_pids: set[str] | None = None,
    pid_to_field: dict[str, dict] | None = None,
) -> bool:
    merged = _resolve_field_protection_meta(
        field,
        pid_to_field=pid_to_field,
        rp_pdf_pids=rp_pdf_pids,
    )
    if _is_background_level_field(merged) or _is_rp_preface_factor_field(merged):
        return False
    if _is_rp_table_seq_field(merged):
        return True
    pid = str(merged.get("pdfFieldId") or merged.get("id") or "").strip().lower()
    if managed_pids and pid and pid in managed_pids:
        return True
    if rp_pdf_pids and pid and pid in rp_pdf_pids:
        if _is_rp_location_pdf_field({"id": str(merged.get("label") or merged.get("hierarchyKey") or ""), "templateSectionKey": _RP_CHAPTER_SECTION_KEY, "pdfFieldId": pid}):
            return True
        if _is_rp_table_data_field(merged, pid_to_field=pid_to_field, rp_pdf_pids=rp_pdf_pids):
            return True
        if _is_rp_location_field(merged):
            return True
    label = str(merged.get("hierarchyKey") or merged.get("label") or "")
    if _is_rp_location_field(merged):
        return True
    if "检测点位置" in label and re.search(r"_r\d+", label, re.I):
        return _is_protection_section_field(
            merged,
            pid_to_field=pid_to_field,
            rp_pdf_pids=rp_pdf_pids,
        )
    if _is_rp_table_data_field(merged, pid_to_field=pid_to_field, rp_pdf_pids=rp_pdf_pids):
        return True
    return _is_protection_section_field(
        merged,
        pid_to_field=pid_to_field,
        rp_pdf_pids=rp_pdf_pids,
    )


def _fill_rp_preface_factor_fields(
    payload: dict,
    frontend_obj: dict,
    rng: random.Random,
) -> int:
    """填写 k1/k2 等表前因子，供报出值公式 f223*f208 等使用。"""
    filled = 0
    seen: set[str] = set()
    for field in _iter_all_template_fields(frontend_obj):
        if not isinstance(field, dict) or not _is_rp_preface_factor_field(field):
            continue
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip().lower()
        if pid and pid in seen:
            continue
        if pid:
            seen.add(pid)
        val = _format_mock_field_value(field, rng.uniform(0.85, 1.35))
        _write_field_value_to_payload(payload, field, val)
        filled += 1
    return filled


def _row_index_from_radiation_point(point: str) -> int:
    m = _RP_ROW_INDEX_RE.search(str(point or ""))
    if not m:
        return 0
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return 0


def _location_main_from_radiation_point(point: str) -> str:
    pt = str(point or "").strip()
    m = re.match(r"^(.+?)_r\d+", pt, re.IGNORECASE)
    return m.group(1) if m else pt


def _has_sub_location_in_point(point: str) -> bool:
    m = _RP_ROW_INDEX_RE.search(str(point or ""))
    return bool(m and m.group(2))


def _is_rp_background_binding(binding: dict) -> bool:
    pt = str(binding.get("radiationPoint") or "")
    return "本底" in pt or "序号本底水平" in pt


def _binding_has_background_cells(binding: dict, pid_to_field: dict[str, dict]) -> bool:
    for key in _RP_BINDING_DATA_KEYS:
        pid = str(binding.get(key) or "").strip().lower()
        if not pid:
            continue
        field = pid_to_field.get(pid)
        if isinstance(field, dict) and _is_background_level_field(field):
            return True
    return False


def _binding_has_any_reading_pid(binding: dict) -> bool:
    return any(str(binding.get(k) or "").strip() for k in _RP_BINDING_READING_KEYS)


def _radiation_protection_chapter_config(frontend_obj: dict) -> dict:
    chapter = frontend_obj.get("radiationProtectionChapter")
    if isinstance(chapter, dict):
        return chapter
    form_schema = frontend_obj.get("formSchema")
    if isinstance(form_schema, dict):
        chapter = form_schema.get("radiationProtectionChapter")
        if isinstance(chapter, dict):
            return chapter
    return {}


def _binding_radiation_point_usable(point: str) -> bool:
    pt = str(point or "").strip()
    if not pt:
        return False
    if _RP_ROW_INDEX_RE.search(pt):
        return True
    return bool(_RP_COORD_POINT_RE.match(pt))


def _is_valid_rp_data_binding(binding: dict, pid_to_field: dict[str, dict]) -> bool:
    if not isinstance(binding, dict) or _is_rp_background_binding(binding):
        return False
    if _binding_has_background_cells(binding, pid_to_field):
        return False
    pt = str(binding.get("radiationPoint") or "")
    if not _binding_radiation_point_usable(pt):
        return False
    if not _binding_has_any_reading_pid(binding):
        return False
    for key in _RP_BINDING_DATA_KEYS:
        pid = str(binding.get(key) or "").strip().lower()
        if not pid:
            continue
        field = pid_to_field.get(pid)
        if isinstance(field, dict) and not _is_protection_section_field(field):
            return False
    return True


def _build_rp_table_bindings(frontend_obj: dict) -> list[dict]:
    from radiation_detection_report.chapter5_field_sync import (
        build_field_bindings_from_fields,
        build_field_bindings_from_pdf_fields,
    )

    pid_to_field = _index_fields_by_pdf_field_id(frontend_obj)
    chapter = _radiation_protection_chapter_config(frontend_obj)
    raw_bindings = chapter.get("fieldBindings")
    if isinstance(raw_bindings, list) and raw_bindings:
        bindings = [copy.deepcopy(row) for row in raw_bindings if isinstance(row, dict)]
    else:
        fields = [f for f in _iter_all_template_fields(frontend_obj) if isinstance(f, dict)]
        bindings = build_field_bindings_from_fields(
            fields,
            chapter=chapter,
            template_payload=frontend_obj,
        )
    pdf_bindings = build_field_bindings_from_pdf_fields(
        _merged_pdf_field_items(frontend_obj)
    )
    if pdf_bindings:
        bindings = _merge_rp_binding_lists(bindings, pdf_bindings)
    out: list[dict] = []
    for row in bindings:
        if _is_valid_rp_data_binding(row, pid_to_field):
            out.append(row)
    if not out and bindings:
        for row in bindings:
            if not isinstance(row, dict) or _is_rp_background_binding(row):
                continue
            pt = str(row.get("radiationPoint") or "")
            if not _binding_radiation_point_usable(pt):
                continue
            if not _binding_has_any_reading_pid(row):
                continue
            out.append(row)
    return _sort_rp_bindings_by_table_order(out, pid_to_field)


def _binding_table_sort_key(binding: dict, pid_to_field: dict[str, dict]) -> tuple:
    for key in _RP_BINDING_DATA_KEYS:
        pid = str(binding.get(key) or "").strip().lower()
        field = pid_to_field.get(pid)
        if isinstance(field, dict):
            page, x0, y0 = _field_pdf_anchor_rect(field)
            return (page, y0, x0, str(binding.get("radiationPoint") or ""))
    row_idx = _row_index_from_radiation_point(str(binding.get("radiationPoint") or ""))
    return (5, float(row_idx), 0.0, str(binding.get("radiationPoint") or ""))


def _sort_rp_bindings_by_table_order(
    bindings: list[dict],
    pid_to_field: dict[str, dict],
) -> list[dict]:
    return sorted(
        bindings,
        key=lambda row: _binding_table_sort_key(row, pid_to_field),
    )


def _collect_rp_data_field_pids(
    frontend_obj: dict,
    *,
    pid_to_field: dict[str, dict] | None = None,
    rp_pdf_pids: set[str] | None = None,
) -> set[str]:
    """扫描全部防护表数据格 pid（不依赖 fieldBindings 是否构建成功）。"""
    rp_pdf_pids = rp_pdf_pids if rp_pdf_pids is not None else _collect_rp_pdf_section_pids(frontend_obj)
    pid_to_field = pid_to_field or _index_fields_by_pdf_field_id(frontend_obj)
    pids: set[str] = set()
    pdf_block = frontend_obj.get("pdf")
    if isinstance(pdf_block, dict):
        for item in pdf_block.get("fields") or []:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("pdfFieldId") or "").strip().lower()
            if not pid or pid not in rp_pdf_pids:
                continue
            if _is_rp_location_pdf_field(item):
                continue
            field = pid_to_field.get(pid) or _field_from_pdf_entry(item)
            if _is_rp_preface_factor_field(field) or _is_background_level_field(field):
                continue
            if _is_rp_table_seq_field(field):
                continue
            pids.add(pid)
    for field in _iter_all_template_fields(frontend_obj):
        if not isinstance(field, dict):
            continue
        if not _is_rp_table_data_field(
            field,
            pid_to_field=pid_to_field,
            rp_pdf_pids=rp_pdf_pids,
        ):
            continue
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip().lower()
        if pid and _F_ID_RE.match(pid):
            pids.add(pid)
    return pids


def _collect_rp_managed_pids(
    frontend_obj: dict,
    bindings: list[dict],
    *,
    pid_to_field: dict[str, dict] | None = None,
    rp_pdf_pids: set[str] | None = None,
) -> set[str]:
    pid_to_field = pid_to_field or _index_fields_by_pdf_field_id(frontend_obj)
    rp_pdf_pids = rp_pdf_pids if rp_pdf_pids is not None else _collect_rp_pdf_section_pids(frontend_obj, bindings)
    pids = _collect_rp_table_pids(bindings)
    pids.update(
        _collect_rp_data_field_pids(
            frontend_obj,
            pid_to_field=pid_to_field,
            rp_pdf_pids=rp_pdf_pids,
        )
    )
    pids.update(rp_pdf_pids)
    for binding in bindings:
        seq_field = _seq_field_for_binding(binding, pid_to_field, frontend_obj)
        if isinstance(seq_field, dict):
            pid = str(seq_field.get("pdfFieldId") or seq_field.get("id") or "").strip().lower()
            if pid and _F_ID_RE.match(pid):
                pids.add(pid)
        loc_field = _location_field_for_binding(binding, pid_to_field, frontend_obj)
        if isinstance(loc_field, dict):
            pid = str(loc_field.get("pdfFieldId") or loc_field.get("id") or "").strip().lower()
            if pid and _F_ID_RE.match(pid):
                pids.add(pid)
    return pids


def _collect_rp_table_pids(bindings: list[dict]) -> set[str]:
    pids: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        for key in ("seq_no",) + _RP_BINDING_DATA_KEYS:
            pid = str(binding.get(key) or "").strip().lower()
            if pid and _F_ID_RE.match(pid):
                pids.add(pid)
    return pids


def _index_fields_by_pdf_field_id(frontend_obj: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for field in _iter_all_template_fields(frontend_obj):
        if not isinstance(field, dict):
            continue
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip().lower()
        if pid and _F_ID_RE.match(pid):
            out[pid] = field

    pdf_block = frontend_obj.get("pdf")
    if not isinstance(pdf_block, dict):
        pdf_block = {}
    for item in _merged_pdf_field_items(frontend_obj):
        if not isinstance(item, dict):
            continue
        pid = str(item.get("pdfFieldId") or "").strip().lower()
        if not pid or not _F_ID_RE.match(pid):
            continue
        existing = out.get(pid)
        if not isinstance(existing, dict):
            out[pid] = _field_from_pdf_entry(item)
            continue
        merged = copy.deepcopy(existing)
        sk = str(item.get("templateSectionKey") or "").strip()
        if sk and not str(merged.get("templateSectionKey") or merged.get("sectionKey") or "").strip():
            merged["templateSectionKey"] = sk
        if not str(merged.get("hierarchyKey") or "").strip():
            merged["hierarchyKey"] = str(item.get("id") or pid).strip()
        if not str(merged.get("label") or "").strip():
            merged["label"] = str(item.get("id") or pid).strip()
        for key in (
            "fieldExpression",
            "formula",
            "fieldExpressionRules",
            "formulaRules",
            "fieldFormulaUserOverride",
            "precision",
        ):
            if not str(merged.get(key) or "").strip() and item.get(key) not in (None, "", []):
                merged[key] = copy.deepcopy(item.get(key))
        out[pid] = merged
    return out


def _is_rp_chapter_managed_field(
    field: dict,
    *,
    pid_to_field: dict[str, dict] | None = None,
    rp_pdf_pids: set[str] | None = None,
) -> bool:
    """第五章防护表区栏位（除表前校准因子外）统一由专用逻辑处理。"""
    merged = _resolve_field_protection_meta(
        field,
        pid_to_field=pid_to_field,
        rp_pdf_pids=rp_pdf_pids,
    )
    if _is_rp_preface_factor_field(merged):
        return False
    pid = str(merged.get("pdfFieldId") or merged.get("id") or "").strip().lower()
    if rp_pdf_pids and pid and pid in rp_pdf_pids:
        return True
    sk = str(merged.get("templateSectionKey") or merged.get("sectionKey") or "").strip()
    if sk == _RP_CHAPTER_SECTION_KEY:
        return True
    if _is_protection_section_field(
        merged,
        pid_to_field=pid_to_field,
        rp_pdf_pids=rp_pdf_pids,
    ):
        return True
    try:
        page, _x0, _y0 = _field_pdf_anchor_rect(merged)
        if page >= 5 and _is_rp_table_data_field(
            merged,
            pid_to_field=pid_to_field,
            rp_pdf_pids=rp_pdf_pids,
        ):
            return True
    except Exception:
        pass
    return False


def _iter_mock_formula_fields(frontend_obj: dict) -> Iterable[dict]:
    """含 steps 与 pdf.fields 中带公式的栏位（防护表均值/报出值多在 pdf.fields）。"""
    seen: set[str] = set()
    for field in _iter_all_template_fields(frontend_obj):
        if not isinstance(field, dict) or not _field_has_formula(field):
            continue
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip().lower()
        if pid and pid in seen:
            continue
        if pid:
            seen.add(pid)
        yield field


def _field_pdf_page(field: dict) -> int:
    anchor = field.get("pdfAnchor")
    if isinstance(anchor, dict):
        try:
            return int(anchor.get("page") or 999)
        except (TypeError, ValueError):
            pass
    return 999


def _seq_fields_by_row_index(frontend_obj: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for field in _iter_form_fields(frontend_obj):
        if not isinstance(field, dict) or not _is_rp_table_seq_field(field):
            continue
        label = str(field.get("label") or field.get("hierarchyKey") or "")
        m = re.match(r"序号_r(\d+)", label, re.IGNORECASE)
        if not m:
            continue
        try:
            idx = int(m.group(1))
        except (TypeError, ValueError):
            continue
        prev = out.get(idx)
        if prev is None or _field_pdf_page(field) < _field_pdf_page(prev):
            out[idx] = field
    return out


def _reading_anchor_field_for_binding(
    binding: dict,
    pid_to_field: dict[str, dict],
) -> dict | None:
    """首格读数可能为空（如跨页续表 r1 仅 M2/M3），依次回退到其它读数/均值/报出值格。"""
    for key in ("reading_1", "reading_2", "reading_3", "mean_m", "report_d"):
        pid = str(binding.get(key) or "").strip().lower()
        field = pid_to_field.get(pid)
        if isinstance(field, dict):
            return field
    return None


def _seq_field_for_binding(
    binding: dict,
    pid_to_field: dict[str, dict],
    frontend_obj: dict,
) -> dict | None:
    """优先 fieldBindings.seq_no；否则按读数格 PDF 位置匹配序号栏。"""
    seq_pid = str(binding.get("seq_no") or "").strip().lower()
    if seq_pid and _F_ID_RE.match(seq_pid):
        field = pid_to_field.get(seq_pid)
        if isinstance(field, dict):
            return field
    from radiation_detection_report.chapter5_field_sync import seq_field_for_binding_row

    fields = [f for f in _iter_form_fields(frontend_obj) if isinstance(f, dict)]
    matched = seq_field_for_binding_row(binding, fields, pid_to_field)
    return dict(matched) if isinstance(matched, dict) else None


def _location_fields_by_row_index(frontend_obj: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for field in _iter_form_fields(frontend_obj):
        if not isinstance(field, dict) or _is_background_level_field(field):
            continue
        if _is_rp_location_field(field):
            label = str(field.get("label") or field.get("hierarchyKey") or "")
            m = re.search(r"_r(\d+)(?:_检测点位置|检测点位置)?", label, re.IGNORECASE)
            if m:
                try:
                    out[int(m.group(1))] = field
                except (TypeError, ValueError):
                    pass
            continue
        label = str(field.get("label") or field.get("hierarchyKey") or "")
        if "检测点位置" not in label:
            continue
        m = re.search(r"_r(\d+)(?:_检测点位置|检测点位置)", label, re.IGNORECASE)
        if not m:
            continue
        try:
            out[int(m.group(1))] = field
        except (TypeError, ValueError):
            continue
    return out


def _location_field_for_binding(
    binding: dict,
    pid_to_field: dict[str, dict],
    frontend_obj: dict,
) -> dict | None:
    """按 binding.radiationPoint 匹配检测点位置栏（含 table.radiationColumn 位置列）。"""
    pt = str(binding.get("radiationPoint") or "").strip()
    if not pt:
        return None
    row_idx = _row_index_from_radiation_point(pt)
    by_row = _location_fields_by_row_index(frontend_obj)
    if row_idx and row_idx in by_row:
        return by_row[row_idx]
    main = _location_main_from_radiation_point(pt)
    if not main:
        return None
    best: dict | None = None
    best_score = -1
    for field in _iter_form_fields(frontend_obj):
        if not isinstance(field, dict) or not _is_rp_location_field(field):
            continue
        label = str(field.get("label") or field.get("hierarchyKey") or "").strip()
        if not label:
            continue
        if label == main:
            return field
        score = 0
        if label.startswith(main) or main.startswith(label):
            score = min(len(label), len(main))
        elif main in label or label in main:
            score = max(len(label), len(main)) // 2
        if score > best_score:
            best_score = score
            best = field
    if best is not None:
        return best
    pdf_block = frontend_obj.get("pdf")
    if not isinstance(pdf_block, dict):
        return None
    for item in pdf_block.get("fields") or []:
        if not isinstance(item, dict) or not _is_rp_location_pdf_field(item):
            continue
        label = str(item.get("id") or "").strip()
        if label == main or label.startswith(main) or main.startswith(label):
            pid = str(item.get("pdfFieldId") or "").strip().lower()
            return pid_to_field.get(pid) or _field_from_pdf_entry(item)
    return None


def _write_pid_value_to_payload(payload: dict, pid: str, value: Any, *, field: dict | None = None) -> None:
    pid = str(pid or "").strip()
    if not pid or not _F_ID_RE.match(pid):
        return
    payload.setdefault("dynamicData", {})
    if not isinstance(payload["dynamicData"], dict):
        payload["dynamicData"] = {}
    payload["dynamicData"][pid] = value
    payload.setdefault("testResult", {})
    if not isinstance(payload["testResult"], dict):
        payload["testResult"] = {}
    payload["testResult"][pid] = value
    if isinstance(field, dict):
        submit_path = str(field.get("submitPath") or "").strip()
        if submit_path.startswith("testResult."):
            leaf = submit_path.split(".", 1)[1]
            payload["testResult"][leaf] = value


def _mock_rp_reading_value(rng: random.Random) -> float:
    from radiation_detection_report.chapter5_field_sync import PROTECTION_NUMBER_PRECISION

    return round(rng.uniform(0.12, 0.38), PROTECTION_NUMBER_PRECISION)


def _bindings_share_seq_merge(prev_binding: dict, next_binding: dict) -> bool:
    """表格顺序相邻两行是否属于同一跨行序号单元（同主检测点且带分表头）。"""
    prev_pt = str(prev_binding.get("radiationPoint") or "")
    next_pt = str(next_binding.get("radiationPoint") or "")
    if not (_has_sub_location_in_point(prev_pt) and _has_sub_location_in_point(next_pt)):
        return False
    return _location_main_from_radiation_point(prev_pt) == _location_main_from_radiation_point(next_pt)


def _assign_rp_sequence_numbers(
    payload: dict,
    bindings: list[dict],
    selected: set[int],
    pid_to_field: dict[str, dict],
    frontend_obj: dict,
) -> int:
    """按表格顺序为已填行编序号 1、2、3…；跨行合并格写 n~m。"""
    assigned = 0
    seq_no = 1
    i = 0
    n = len(bindings)
    while i < n:
        if i not in selected:
            i += 1
            continue
        group = [i]
        j = i + 1
        while j < n and j in selected and _bindings_share_seq_merge(bindings[j - 1], bindings[j]):
            group.append(j)
            j += 1
        if len(group) > 1:
            seq_text = f"{seq_no}~{seq_no + len(group) - 1}"
            seq_no += len(group)
        else:
            seq_text = str(seq_no)
            seq_no += 1
        first_seq_field = _seq_field_for_binding(bindings[group[0]], pid_to_field, frontend_obj)
        first_seq_pid = ""
        if isinstance(first_seq_field, dict):
            first_seq_pid = str(
                first_seq_field.get("pdfFieldId") or first_seq_field.get("id") or ""
            ).strip().lower()
            _write_field_value_to_payload(payload, first_seq_field, seq_text)
            assigned += 1
        if len(group) > 1:
            for gi in group[1:]:
                extra_seq = _seq_field_for_binding(bindings[gi], pid_to_field, frontend_obj)
                if not isinstance(extra_seq, dict):
                    continue
                extra_pid = str(
                    extra_seq.get("pdfFieldId") or extra_seq.get("id") or ""
                ).strip().lower()
                if extra_pid and extra_pid != first_seq_pid:
                    _write_field_value_to_payload(payload, extra_seq, _SLASH)
        i = j
    return assigned


def _binding_cell_pids(binding: dict, *, group: int | None = None) -> list[str]:
    pids: list[str] = []
    keys = _binding_data_keys_for_group(group)
    for key in keys:
        pid = str(binding.get(key) or "").strip().lower()
        if pid and _F_ID_RE.match(pid):
            pids.append(pid)
    return pids


def _binding_reading_pids(binding: dict, *, group: int | None = None) -> list[str]:
    pids: list[str] = []
    for key in _binding_reading_keys_for_group(group):
        pid = str(binding.get(key) or "").strip().lower()
        if pid and _F_ID_RE.match(pid):
            pids.append(pid)
    return pids


def _binding_group_has_active_readings(
    payload: dict,
    binding: dict,
    *,
    group: int,
) -> bool:
    for pid in _binding_reading_pids(binding, group=group):
        val = str(_payload_pid_value(payload, pid) or "").strip()
        if val and val != _SLASH:
            return True
    return False


def _binding_group_has_active_data(
    payload: dict,
    binding: dict,
    *,
    group: int,
) -> bool:
    for pid in _binding_cell_pids(binding, group=group):
        val = str(_payload_pid_value(payload, pid) or "").strip()
        if val and val != _SLASH:
            return True
    return False


def _binding_row_has_active_data(payload: dict, binding: dict) -> bool:
    if _binding_group_has_active_data(payload, binding, group=1):
        return True
    if _binding_is_dual_capable(binding) and _binding_group_has_active_data(
        payload, binding, group=2
    ):
        return True
    return False


def _active_rp_binding_indices(payload: dict, bindings: list[dict]) -> set[int]:
    return {
        bi
        for bi, binding in enumerate(bindings)
        if isinstance(binding, dict) and _binding_row_has_active_data(payload, binding)
    }


def _finalize_rp_location_and_seq_fields(
    payload: dict,
    bindings: list[dict],
    *,
    pid_to_field: dict[str, dict],
    frontend_obj: dict,
    location_fields: dict[int, dict] | None = None,
) -> int:
    """非空行保留序号；空行的序号/检测点位置写 /；清除误填的模拟位置文案。"""
    active = _active_rp_binding_indices(payload, bindings)
    slashed = 0
    for bi, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            continue
        loc_field = _location_field_for_binding(binding, pid_to_field, frontend_obj)
        if loc_field is None and location_fields:
            row_idx = _row_index_from_radiation_point(str(binding.get("radiationPoint") or ""))
            loc_field = location_fields.get(row_idx) if row_idx else None
        if bi not in active:
            seq_field = _seq_field_for_binding(binding, pid_to_field, frontend_obj)
            if isinstance(seq_field, dict):
                _write_field_value_to_payload(payload, seq_field, _SLASH)
            if isinstance(loc_field, dict):
                _write_field_value_to_payload(payload, loc_field, _SLASH)
            slashed += 1
        elif isinstance(loc_field, dict):
            loc_pid = str(loc_field.get("pdfFieldId") or loc_field.get("id") or "").strip().lower()
            cur = str(_payload_pid_value(payload, loc_pid) or "").strip()
            if cur.startswith("模拟_") or cur.startswith("mock_"):
                _write_field_value_to_payload(payload, loc_field, _SLASH)
    return slashed


def _slash_rp_binding_group(
    payload: dict,
    binding: dict,
    group: int,
    *,
    pid_to_field: dict[str, dict],
) -> None:
    for pid in _binding_cell_pids(binding, group=group):
        _write_pid_value_to_payload(payload, pid, _SLASH, field=pid_to_field.get(pid))


def _slash_rp_binding_row(
    payload: dict,
    binding: dict,
    *,
    pid_to_field: dict[str, dict],
    frontend_obj: dict,
    location_fields: dict[int, dict],
) -> None:
    if _binding_is_dual_capable(binding):
        if _binding_has_group_slots(binding, 1):
            _slash_rp_binding_group(
                payload, binding, 1, pid_to_field=pid_to_field
            )
        if _binding_has_group_slots(binding, 2):
            _slash_rp_binding_group(
                payload, binding, 2, pid_to_field=pid_to_field
            )
    else:
        for pid in _binding_cell_pids(binding):
            _write_pid_value_to_payload(payload, pid, _SLASH, field=pid_to_field.get(pid))
    seq_field = _seq_field_for_binding(binding, pid_to_field, frontend_obj)
    if isinstance(seq_field, dict):
        seq_pid = str(
            seq_field.get("pdfFieldId") or seq_field.get("id") or ""
        ).strip().lower()
        if not _is_assigned_seq_value(_payload_pid_value(payload, seq_pid)):
            _write_field_value_to_payload(payload, seq_field, _SLASH)
    loc_field = _location_field_for_binding(binding, pid_to_field, frontend_obj)
    if loc_field is None and location_fields:
        row_idx = _row_index_from_radiation_point(str(binding.get("radiationPoint") or ""))
        loc_field = location_fields.get(row_idx) if row_idx else None
    if isinstance(loc_field, dict):
        _write_field_value_to_payload(payload, loc_field, _SLASH)


def _fill_rp_binding_group_readings(
    payload: dict,
    binding: dict,
    group: int,
    *,
    pid_to_field: dict[str, dict],
    rng: random.Random,
) -> None:
    reading_pids = _binding_reading_pids(binding, group=group)
    if not reading_pids:
        return
    val = _mock_rp_reading_value(rng)
    for pid in reading_pids:
        field = pid_to_field.get(pid)
        write_val = _format_mock_field_value(field, val) if isinstance(field, dict) else val
        _write_pid_value_to_payload(payload, pid, write_val, field=field)


def _fill_rp_binding_readings(
    payload: dict,
    binding: dict,
    *,
    pid_to_field: dict[str, dict],
    rng: random.Random,
) -> None:
    _fill_rp_binding_group_readings(
        payload, binding, 1, pid_to_field=pid_to_field, rng=rng
    )
    if _binding_is_dual_capable(binding):
        _fill_rp_binding_group_readings(
            payload, binding, 2, pid_to_field=pid_to_field, rng=rng
        )


def _apply_radiation_protection_chapter_mock(
    payload: dict,
    frontend_obj: dict,
    rng: random.Random,
    *,
    select_ratio: float = 0.48,
) -> dict[str, int]:
    """
    第五章防护表：按表格顺序、以最小数据行为单位随机填写。
    单组模板：整行填或整行 /。
    双组模板：第一组、第二组读数分别独立随机（可一组有值、另一组 /）。
    序号从 1 起连续编号，跨多行合并格写 n~m；未填行全部写 /。
    """
    bindings = _build_rp_table_bindings(frontend_obj)
    if not bindings:
        return {
            "rows": 0,
            "selected": 0,
            "selectedG1": 0,
            "selectedG2": 0,
            "dualGroup": False,
            "slashed": 0,
            "slashedBindings": [],
            "filledPids": [],
            "bindingCount": 0,
            "seqAssigned": 0,
        }

    pid_to_field = _index_fields_by_pdf_field_id(frontend_obj)
    location_fields = _location_fields_by_row_index(frontend_obj)
    ratio = max(0.15, min(0.85, float(select_ratio)))
    dual_mode = _rp_bindings_indicate_dual_group(bindings, frontend_obj)

    selected_g1: set[int] = set()
    selected_g2: set[int] = set()
    selected: set[int] = set()

    full_fill = ratio >= 0.99

    if dual_mode:
        g2_ratio = ratio if full_fill else max(0.12, min(0.75, ratio * 0.55))
        dual_candidates = [
            bi for bi, binding in enumerate(bindings) if _binding_is_dual_capable(binding)
        ]
        for bi, binding in enumerate(bindings):
            if _binding_has_group_slots(binding, 1) and rng.random() < ratio:
                selected_g1.add(bi)
            if bi in dual_candidates and rng.random() < g2_ratio:
                selected_g2.add(bi)
        if not selected_g1:
            g1_candidates = [
                bi for bi, binding in enumerate(bindings) if _binding_has_group_slots(binding, 1)
            ]
            if g1_candidates:
                selected_g1.add(rng.choice(g1_candidates))
        if dual_candidates and not selected_g2:
            selected_g2.add(rng.choice(dual_candidates))
        if (
            not full_fill
            and len(selected_g2) >= len(dual_candidates)
            and len(dual_candidates) > 2
        ):
            selected_g2.discard(rng.choice(list(selected_g2)))
        active_seq = selected_g1 | selected_g2
    else:
        for bi in range(len(bindings)):
            if rng.random() < ratio:
                selected.add(bi)
        if not selected and len(bindings) > 1:
            selected.add(rng.randint(0, len(bindings) - 1))
        if not full_fill and len(selected) >= len(bindings) and len(bindings) > 2:
            selected.discard(rng.choice(list(selected)))
        selected_g1 = set(selected)
        active_seq = set(selected)

    slashed_rows = 0
    for bi, binding in enumerate(bindings):
        if dual_mode:
            g1_sel = bi in selected_g1
            g2_sel = bi in selected_g2 and _binding_is_dual_capable(binding)
            if not g1_sel and not g2_sel:
                _slash_rp_binding_row(
                    payload,
                    binding,
                    pid_to_field=pid_to_field,
                    frontend_obj=frontend_obj,
                    location_fields=location_fields,
                )
                slashed_rows += 1
                continue
            if g1_sel:
                _fill_rp_binding_group_readings(
                    payload,
                    binding,
                    1,
                    pid_to_field=pid_to_field,
                    rng=rng,
                )
            elif _binding_has_group_slots(binding, 1):
                _slash_rp_binding_group(
                    payload, binding, 1, pid_to_field=pid_to_field
                )
            if _binding_is_dual_capable(binding):
                if g2_sel:
                    _fill_rp_binding_group_readings(
                        payload,
                        binding,
                        2,
                        pid_to_field=pid_to_field,
                        rng=rng,
                    )
                else:
                    _slash_rp_binding_group(
                        payload, binding, 2, pid_to_field=pid_to_field
                    )
            continue

        if bi not in selected:
            _slash_rp_binding_row(
                payload,
                binding,
                pid_to_field=pid_to_field,
                frontend_obj=frontend_obj,
                location_fields=location_fields,
            )
            slashed_rows += 1
            continue
        _fill_rp_binding_readings(
            payload,
            binding,
            pid_to_field=pid_to_field,
            rng=rng,
        )

    seq_assigned = 0

    slashed_bindings: list[dict] = []
    filled_pids: set[str] = set()
    for bi, binding in enumerate(bindings):
        if dual_mode:
            if bi in selected_g1 or bi in selected_g2:
                if bi in selected_g1:
                    for pid in _binding_cell_pids(binding, group=1):
                        filled_pids.add(pid.lower())
                if bi in selected_g2:
                    for pid in _binding_cell_pids(binding, group=2):
                        filled_pids.add(pid.lower())
            else:
                slashed_bindings.append(binding)
        elif bi not in selected:
            slashed_bindings.append(binding)
        else:
            for pid in _binding_cell_pids(binding):
                filled_pids.add(pid.lower())
        if bi in active_seq:
            seq_field = _seq_field_for_binding(binding, pid_to_field, frontend_obj)
            if isinstance(seq_field, dict):
                spid = str(seq_field.get("pdfFieldId") or seq_field.get("id") or "").strip().lower()
                if spid:
                    filled_pids.add(spid)

    return {
        "rows": len(bindings),
        "selected": len(active_seq),
        "selectedG1": len(selected_g1),
        "selectedG2": len(selected_g2),
        "dualGroup": dual_mode,
        "slashed": slashed_rows,
        "slashedBindings": slashed_bindings,
        "filledPids": sorted(filled_pids),
        "bindingCount": len(bindings),
        "seqAssigned": seq_assigned,
    }


def _enforce_rp_binding_group_coherence(
    payload: dict,
    binding: dict,
    group: int,
    *,
    pid_to_field: dict[str, dict],
) -> bool:
    """同一组读数/报出要么全有值，要么全 /；混填则整组划掉。"""
    data_pids = _binding_cell_pids(binding, group=group)
    if not data_pids:
        return False
    normalized = [
        str(_payload_pid_value(payload, pid) or "").strip()
        for pid in data_pids
    ]
    has_slash = any(v == _SLASH for v in normalized)
    has_value = any(v and v != _SLASH for v in normalized)
    if has_slash and has_value:
        _slash_rp_binding_group(payload, binding, group, pid_to_field=pid_to_field)
        return True
    reading_pids = _binding_reading_pids(binding, group=group)
    if not reading_pids:
        return False
    reading_vals = [
        str(_payload_pid_value(payload, pid) or "").strip()
        for pid in reading_pids
    ]
    filled_readings = [v for v in reading_vals if v and v != _SLASH]
    if filled_readings and len(filled_readings) != len(reading_pids):
        _slash_rp_binding_group(payload, binding, group, pid_to_field=pid_to_field)
        return True
    return False


def _enforce_rp_binding_row_coherence(
    payload: dict,
    frontend_obj: dict,
    bindings: list[dict],
) -> int:
    """
    公式计算后校正防护表行。
    单组：整行要么全部有值，要么全部 /。
    双组：第一组、第二组分别校验，允许「一组有值、另一组 /」。
    """
    if not bindings:
        return 0
    pid_to_field = _index_fields_by_pdf_field_id(frontend_obj)
    dual_mode = _rp_bindings_indicate_dual_group(bindings, frontend_obj)
    fixed = 0
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        if dual_mode and _binding_is_dual_capable(binding):
            for group in (1, 2):
                if not _binding_has_group_slots(binding, group):
                    continue
                if _enforce_rp_binding_group_coherence(
                    payload,
                    binding,
                    group,
                    pid_to_field=pid_to_field,
                ):
                    fixed += 1
            continue
        data_pids = _binding_cell_pids(binding)
        if not data_pids:
            continue
        normalized = [
            str(_payload_pid_value(payload, pid) or "").strip()
            for pid in data_pids
        ]
        has_slash = any(v == _SLASH for v in normalized)
        has_value = any(v and v != _SLASH for v in normalized)
        if has_slash and has_value:
            _slash_rp_binding_row(
                payload,
                binding,
                pid_to_field=pid_to_field,
                frontend_obj=frontend_obj,
                location_fields=_location_fields_by_row_index(frontend_obj),
            )
            fixed += 1
            continue
        reading_pids = _binding_reading_pids(binding)
        if not reading_pids:
            continue
        reading_vals = [
            str(_payload_pid_value(payload, pid) or "").strip()
            for pid in reading_pids
        ]
        filled_readings = [v for v in reading_vals if v and v != _SLASH]
        if filled_readings and len(filled_readings) != len(reading_pids):
            _slash_rp_binding_row(
                payload,
                binding,
                pid_to_field=pid_to_field,
                frontend_obj=frontend_obj,
                location_fields=_location_fields_by_row_index(frontend_obj),
            )
            fixed += 1
    return fixed


def _finalize_rp_slashed_bindings(
    payload: dict,
    frontend_obj: dict,
    slashed_bindings: list[dict],
) -> None:
    """公式计算后再次划掉未选行，避免均值/报出值覆盖 /。"""
    if not slashed_bindings:
        return
    pid_to_field = _index_fields_by_pdf_field_id(frontend_obj)
    location_fields = _location_fields_by_row_index(frontend_obj)
    for binding in slashed_bindings:
        if isinstance(binding, dict):
            _slash_rp_binding_row(
                payload,
                binding,
                pid_to_field=pid_to_field,
                frontend_obj=frontend_obj,
                location_fields=location_fields,
            )


def _binding_group_reading_numbers(
    payload: dict,
    binding: dict,
    *,
    group: int,
) -> list[float] | None:
    vals: list[float] = []
    for pid in _binding_reading_pids(binding, group=group):
        raw = _payload_pid_value(payload, pid)
        text = str(raw or "").strip()
        if not text or text == _SLASH:
            return None
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            return None
    return vals if vals else None


def _apply_rp_binding_derived_values(
    payload: dict,
    frontend_obj: dict,
    bindings: list[dict],
) -> int:
    """按 fieldBindings 直接计算防护表均值/报出值（不依赖通用公式迭代是否命中）。"""
    from utils.conditional_field_rules import resolve_field_formula_for_eval
    from utils.dynamic_form_expression import eval_computed_formula

    if not bindings:
        return 0
    chapter = _radiation_protection_chapter_config(frontend_obj)
    constants = frontend_obj.get("constants") if isinstance(frontend_obj.get("constants"), dict) else {}
    enums = frontend_obj.get("enums") if isinstance(frontend_obj.get("enums"), dict) else {}
    lookup_tables = (
        frontend_obj.get("lookupTables") if isinstance(frontend_obj.get("lookupTables"), dict) else {}
    )
    pid_to_field = _index_fields_by_pdf_field_id(frontend_obj)
    dual_mode = _rp_bindings_indicate_dual_group(bindings, frontend_obj)
    applied = 0

    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        if _is_rp_background_binding(binding):
            continue
        groups = (1, 2) if dual_mode and _binding_is_dual_capable(binding) else (1,)
        for group in groups:
            if not _binding_group_has_active_readings(payload, binding, group=group):
                continue
            nums = _binding_group_reading_numbers(payload, binding, group=group)
            if not nums:
                continue
            mean_key = "mean_m" if group == 1 else "mean_m_2"
            mean_pid = str(binding.get(mean_key) or "").strip().lower()
            if mean_pid and _F_ID_RE.match(mean_pid):
                mean_field = pid_to_field.get(mean_pid)
                mean_prec = _field_precision(mean_field if isinstance(mean_field, dict) else None)
                mean_val = round(sum(nums) / len(nums), mean_prec)
                formatted_mean = (
                    _format_mock_field_value(mean_field, mean_val)
                    if isinstance(mean_field, dict)
                    else mean_val
                )
                _write_pid_value_to_payload(
                    payload,
                    mean_pid,
                    formatted_mean,
                    field=mean_field if isinstance(mean_field, dict) else None,
                )
                applied += 1

            report_key = "report_d" if group == 1 else "report_d_2"
            report_pid = str(binding.get(report_key) or "").strip().lower()
            if not report_pid or not _F_ID_RE.match(report_pid):
                continue
            report_field = pid_to_field.get(report_pid)
            vm = _build_mock_formula_value_mapping(payload, frontend_obj)
            formula = ""
            if isinstance(report_field, dict):
                formula = resolve_field_formula_for_eval(
                    report_field,
                    vm,
                    constants=constants,
                    enums=enums,
                    lookup_tables=lookup_tables,
                )
            if not formula:
                try:
                    from radiation_detection_report.chapter5_field_sync import (
                        manual_report_value_rules_for_binding,
                    )

                    rules = (
                        chapter.get("reportValueRules")
                        if isinstance(chapter.get("reportValueRules"), list)
                        else []
                    )
                    manual = manual_report_value_rules_for_binding(
                        rules,
                        binding,
                        report_group=group,
                    )
                    if manual:
                        formula = str(manual[0].get("expression") or "").strip()
                except ImportError:
                    formula = ""
            if not formula and mean_pid:
                factor_pid = "f927" if group == 1 else "f270"
                formula = f"{mean_pid}*{factor_pid}"
            if not formula:
                continue
            try:
                res = eval_computed_formula(
                    formula,
                    vm,
                    constants=constants,
                    enums=enums,
                    lookup_tables=lookup_tables,
                )
            except Exception:
                res = None
            if res is None:
                continue
            formatted_report = (
                _format_mock_field_value(report_field, res)
                if isinstance(report_field, dict)
                else _format_mock_field_value(None, res)
            )
            _write_pid_value_to_payload(
                payload,
                report_pid,
                formatted_report,
                field=report_field if isinstance(report_field, dict) else None,
            )
            applied += 1
    return applied


def _purge_rp_orphan_mock_values(
    payload: dict,
    frontend_obj: dict,
    bindings: list[dict],
    *,
    rp_pdf_pids: set[str] | None = None,
) -> int:
    """清除第五章中未被 binding 管理的误填值（含通用模拟 模拟_xxx / 超大随机数）。"""
    rp_pdf_pids = rp_pdf_pids if rp_pdf_pids is not None else _collect_rp_pdf_section_pids(frontend_obj, bindings)
    pid_to_field = _index_fields_by_pdf_field_id(frontend_obj)
    allowed: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        for pid in _binding_cell_pids(binding):
            allowed.add(pid.lower())
        seq_field = _seq_field_for_binding(binding, pid_to_field, frontend_obj)
        if isinstance(seq_field, dict):
            spid = str(seq_field.get("pdfFieldId") or seq_field.get("id") or "").strip().lower()
            if spid:
                allowed.add(spid)
        loc_field = _location_field_for_binding(binding, pid_to_field, frontend_obj)
        if isinstance(loc_field, dict):
            lpid = str(loc_field.get("pdfFieldId") or loc_field.get("id") or "").strip().lower()
            if lpid:
                allowed.add(lpid)
    for field in _iter_all_template_fields(frontend_obj):
        if _is_rp_preface_factor_field(field):
            pid = str(field.get("pdfFieldId") or field.get("id") or "").strip().lower()
            if pid:
                allowed.add(pid)
    cleared = 0
    for pid in sorted(rp_pdf_pids):
        if pid in allowed:
            continue
        field = pid_to_field.get(pid)
        if isinstance(field, dict) and (
            _is_rp_preface_factor_field(field)
            or _is_rp_table_seq_field(field)
            or _is_rp_location_field(field)
        ):
            continue
        cur = _payload_pid_value(payload, pid)
        if cur in (None, "", _SLASH):
            continue
        text = str(cur).strip()
        polluted = text.startswith("模拟_") or text.startswith("mock_")
        if not polluted:
            try:
                polluted = float(cur) > 5.0
            except (TypeError, ValueError):
                polluted = False
        if not polluted:
            continue
        if isinstance(field, dict):
            _write_field_value_to_payload(payload, field, _SLASH)
        else:
            _write_pid_value_to_payload(payload, pid, _SLASH)
        cleared += 1
    return cleared


def _slash_unfilled_rp_table_cells(
    payload: dict,
    frontend_obj: dict,
    *,
    managed_pids: set[str] | None = None,
    bindings: list[dict] | None = None,
) -> int:
    """未选中的防护表数据格若仍为空，统一写 /（公式栏位在读数有效时不强写 /）。"""
    if not managed_pids:
        managed_pids = _collect_rp_managed_pids(
            frontend_obj,
            bindings or _build_rp_table_bindings(frontend_obj),
        )
    bindings = bindings or _build_rp_table_bindings(frontend_obj)
    dd = payload.get("dynamicData") if isinstance(payload.get("dynamicData"), dict) else {}
    tr = payload.get("testResult") if isinstance(payload.get("testResult"), dict) else {}
    pid_to_field = _index_fields_by_pdf_field_id(frontend_obj)
    formula_output_pids: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        for group in (1, 2):
            mean_pids = [
                str(binding.get("mean_m" if group == 1 else "mean_m_2") or "").strip().lower()
            ]
            report_pids = [
                str(binding.get("report_d" if group == 1 else "report_d_2") or "").strip().lower()
            ]
            for pid in mean_pids + report_pids:
                if pid and _F_ID_RE.match(pid):
                    formula_output_pids.add(pid)
    slashed = 0
    for pid in sorted(managed_pids):
        if not pid or not _F_ID_RE.match(pid):
            continue
        cur = dd.get(pid)
        if cur is None or cur == "":
            cur = tr.get(pid)
        if cur not in (None, ""):
            continue
        if pid in formula_output_pids and _binding_row_has_active_data_for_pid(
            payload, bindings, pid
        ):
            continue
        field = pid_to_field.get(pid)
        if isinstance(field, dict):
            _write_field_value_to_payload(payload, field, _SLASH)
        else:
            _write_pid_value_to_payload(payload, pid, _SLASH)
        slashed += 1
    return slashed


def _binding_row_has_active_data_for_pid(
    payload: dict,
    bindings: list[dict],
    pid: str,
) -> bool:
    pid = str(pid or "").strip().lower()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        for group in (1, 2):
            mean_pid = str(
                binding.get("mean_m" if group == 1 else "mean_m_2") or ""
            ).strip().lower()
            report_pid = str(
                binding.get("report_d" if group == 1 else "report_d_2") or ""
            ).strip().lower()
            if pid not in (mean_pid, report_pid):
                continue
            if _binding_group_has_active_readings(payload, binding, group=group):
                return True
    return False


def _write_field_value_to_payload(payload: dict, field: dict, value: Any) -> None:
    if value is None:
        return
    pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
    if pid and _F_ID_RE.match(pid):
        payload.setdefault("dynamicData", {})
        if not isinstance(payload["dynamicData"], dict):
            payload["dynamicData"] = {}
        payload["dynamicData"][pid] = value
    submit_path = str(field.get("submitPath") or "").strip()
    if not submit_path:
        return
    if submit_path.startswith("dynamicData."):
        leaf = submit_path.split(".", 1)[1]
        payload.setdefault("dynamicData", {})
        if not isinstance(payload["dynamicData"], dict):
            payload["dynamicData"] = {}
        payload["dynamicData"][leaf] = value
    elif submit_path.startswith(
        ("reportInfo.", "hospitalInfo.", "equipmentInfo.", "testResult.", "conclusion.")
    ):
        _set_nested(payload, submit_path, value)


def _build_mock_formula_value_mapping(payload: dict, frontend_obj: dict) -> dict[str, Any]:
    from apps.api.inspection_submit_placeholder_maps import build_dynamic_value_mapping

    vm = dict(build_dynamic_value_mapping(payload))
    for bucket in ("reportInfo", "hospitalInfo", "equipmentInfo", "testResult", "conclusion"):
        block = payload.get(bucket)
        if not isinstance(block, dict):
            continue
        for k, v in block.items():
            key = str(k).strip()
            if _F_ID_RE.match(key) and vm.get(key) in (None, ""):
                vm[key] = v
    for field in _iter_form_fields(frontend_obj):
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
        if not pid or vm.get(pid) not in (None, ""):
            continue
        val = _read_field_value_from_payload(payload, field)
        if val not in (None, ""):
            vm[pid] = val
    return vm


def _apply_mock_formula_fields(payload: dict, frontend_obj: dict) -> int:
    from utils.conditional_field_rules import resolve_field_formula_for_eval
    from utils.dynamic_form_expression import eval_computed_formula

    constants = frontend_obj.get("constants") if isinstance(frontend_obj.get("constants"), dict) else {}
    enums = frontend_obj.get("enums") if isinstance(frontend_obj.get("enums"), dict) else {}
    lookup_tables = (
        frontend_obj.get("lookupTables") if isinstance(frontend_obj.get("lookupTables"), dict) else {}
    )
    formula_fields = [f for f in _iter_mock_formula_fields(frontend_obj) if _field_has_formula(f)]
    applied = 0
    for _ in range(32):
        changed = 0
        vm = _build_mock_formula_value_mapping(payload, frontend_obj)
        for field in formula_fields:
            formula = resolve_field_formula_for_eval(
                field,
                vm,
                constants=constants,
                enums=enums,
                lookup_tables=lookup_tables,
            )
            if not formula or "row." in formula:
                continue
            pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
            if not pid:
                continue
            if str(vm.get(pid) or "").strip() == _SLASH:
                continue
            try:
                res = eval_computed_formula(
                    formula,
                    vm,
                    constants=constants,
                    enums=enums,
                    lookup_tables=lookup_tables,
                )
            except Exception:
                res = None
            if res is None:
                continue
            formatted = _format_mock_field_value(field, res)
            if vm.get(pid) == formatted:
                continue
            _write_field_value_to_payload(payload, field, formatted)
            vm[pid] = formatted
            changed += 1
            applied += 1
        if not changed:
            break
    return applied


def _resolve_judgment_criterion_text(field: dict, report_type: str = "") -> str:
    jct = field.get("judgmentCriteriaByTestType")
    if isinstance(jct, dict):
        for key in ("acceptance", "status"):
            if report_type and key not in str(report_type).lower() and len(jct) > 1:
                continue
            text = str(jct.get(key) or "").strip()
            if text:
                return text
        for text in jct.values():
            s = str(text or "").strip()
            if s:
                return s
    return str(field.get("judgmentCriterionText") or "").strip()


def _find_peer_measurement_field(verdict_field: dict, frontend_obj: dict) -> dict | None:
    vlabel = str(verdict_field.get("label") or "")
    vkey = str(verdict_field.get("hierarchyKey") or "")
    vbase = vkey.replace("_单项判定", "").replace("单项判定", "").strip("_")
    candidates: list[dict] = []
    for field in _iter_form_fields(frontend_obj):
        if field is verdict_field:
            continue
        if _field_is_verdict(field):
            continue
        hkey = str(field.get("hierarchyKey") or "")
        label = str(field.get("label") or "")
        if vbase and (hkey.startswith(vbase) or vbase in hkey):
            if "报出值" in hkey or "报出值" in label or "检测结果" in hkey:
                candidates.append(field)
        elif vlabel and label and vlabel in label:
            candidates.append(field)
    if not candidates:
        return None
    for c in candidates:
        if "报出值" in str(c.get("hierarchyKey") or c.get("label") or ""):
            return c
    return candidates[0]


def _apply_mock_verdict_fields(payload: dict, frontend_obj: dict) -> int:
    from utils.dynamic_form_expression import evaluate_verdict_rule
    from utils.verdict_from_criterion import verdict_from_measurement

    constants = frontend_obj.get("constants") if isinstance(frontend_obj.get("constants"), dict) else {}
    enums = frontend_obj.get("enums") if isinstance(frontend_obj.get("enums"), dict) else {}
    lookup_tables = (
        frontend_obj.get("lookupTables") if isinstance(frontend_obj.get("lookupTables"), dict) else {}
    )
    report_type = str(payload.get("reportType") or "").strip()
    vm = _build_mock_formula_value_mapping(payload, frontend_obj)
    applied = 0
    for field in _iter_form_fields(frontend_obj):
        if not _field_is_verdict(field):
            continue
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
        if not pid:
            continue
        pass_label = str(field.get("passLabel") or "合格").strip() or "合格"
        fail_label = str(field.get("failLabel") or "不合格").strip() or "不合格"
        rule_fe = str(field.get("rule") or "").strip()
        verdict_text: str | None = None
        if rule_fe:
            vb = evaluate_verdict_rule(
                rule_fe,
                vm,
                constants=constants,
                enums=enums,
                lookup_tables=lookup_tables,
            )
            if vb is True:
                verdict_text = pass_label
            elif vb is False:
                verdict_text = fail_label
        if verdict_text is None:
            peer = _find_peer_measurement_field(field, frontend_obj)
            if peer is not None:
                measured = _read_field_value_from_payload(payload, peer)
                crit = _resolve_judgment_criterion_text(peer, report_type)
                if crit and measured not in (None, ""):
                    verdict_text = verdict_from_measurement(str(measured), crit)
            if verdict_text is None:
                crit = _resolve_judgment_criterion_text(field, report_type)
                if crit:
                    measured = _read_field_value_from_payload(payload, field)
                    if measured not in (None, ""):
                        verdict_text = verdict_from_measurement(str(measured), crit)
        if verdict_text:
            _write_field_value_to_payload(payload, field, verdict_text)
            vm[pid] = verdict_text
            applied += 1
    return applied


def apply_mock_derived_values(payload: dict, frontend_obj: dict) -> dict[str, int]:
    """公式栏位按表达式计算；条件判定栏位按标准推断合格/不合格（不随机填）。"""
    from apps.api.inspection_report_make import _apply_computed_fields_from_steps_to_mapping

    stats = {"formulas": 0, "verdicts": 0, "computedSteps": 0}
    steps = _frontend_steps(frontend_obj)
    constants = frontend_obj.get("constants") if isinstance(frontend_obj.get("constants"), dict) else {}
    enums = frontend_obj.get("enums") if isinstance(frontend_obj.get("enums"), dict) else {}
    lookup_tables = (
        frontend_obj.get("lookupTables") if isinstance(frontend_obj.get("lookupTables"), dict) else {}
    )
    vm_before = _build_mock_formula_value_mapping(payload, frontend_obj)
    _apply_computed_fields_from_steps_to_mapping(
        vm_before,
        steps,
        template_constants=constants,
        template_enums=enums,
        template_lookup_tables=lookup_tables,
    )
    for field in _iter_mock_formula_fields(frontend_obj):
        if not _field_has_formula(field):
            continue
        fid = str(field.get("id") or field.get("pdfFieldId") or "").strip()
        if fid and vm_before.get(fid) not in (None, ""):
            if str(vm_before.get(fid) or "").strip() == _SLASH:
                continue
            val = _format_mock_field_value(field, vm_before[fid])
            _write_field_value_to_payload(payload, field, val)
            stats["computedSteps"] += 1
    stats["formulas"] = _apply_mock_formula_fields(payload, frontend_obj)
    stats["verdicts"] = _apply_mock_verdict_fields(payload, frontend_obj)
    return stats


def _classify_filled_row_content(content: Any) -> tuple[str, str | None]:
    text = str(content or "").strip()
    if not text:
        return "input", None
    if text in ("合格", "符合", "通过"):
        return "verdict", "pass"
    if text in ("不合格", "不符合", "未通过"):
        return "verdict", "fail"
    if "不合格" in text:
        return "verdict", "fail"
    if "合格" in text:
        return "verdict", "pass"
    return "input", None


def build_backfill_preview_rows(
    filled_fields: list,
    frontend_obj: dict | None = None,
    *,
    max_input_rows: int = 180,
) -> dict[str, Any]:
    """将回填 filled_fields 整理为可展示行（区分公式计算 / 条件判定）。"""
    field_meta: dict[str, dict] = {}
    if isinstance(frontend_obj, dict):
        for field in _iter_form_fields(frontend_obj):
            pid = str(field.get("pdfFieldId") or field.get("id") or "").strip().lower()
            if pid:
                field_meta[pid] = field

    formula_rows: list[dict] = []
    verdict_rows: list[dict] = []
    input_rows: list[dict] = []

    for row in filled_fields or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("pdfFieldId") or row.get("id") or "").strip()
        if not pid:
            continue
        content = row.get("content")
        if content in (None, ""):
            content = row.get("imageData") or row.get("checked")
        if content in (None, ""):
            continue
        meta = field_meta.get(pid.lower()) or {}
        text = str(content).strip()
        kind, verdict_status = _classify_filled_row_content(text)
        if _field_has_formula(meta) or str(meta.get("type") or "").lower() == "computed":
            kind = "formula"
            verdict_status = None
        elif _field_is_verdict(meta):
            kind = "verdict"
            if verdict_status is None:
                _, verdict_status = _classify_filled_row_content(text)

        item = {
            "pdfFieldId": pid,
            "label": str(meta.get("label") or row.get("label") or pid).strip()[:120],
            "content": text,
            "kind": kind,
            "verdictStatus": verdict_status,
        }
        if kind == "formula":
            formula_rows.append(item)
        elif kind == "verdict":
            verdict_rows.append(item)
        else:
            input_rows.append(item)

    input_rows = input_rows[: max(0, int(max_input_rows))]
    rows = formula_rows + verdict_rows + input_rows
    return {
        "rows": rows,
        "total": len(rows),
        "formulaCount": len(formula_rows),
        "verdictCount": len(verdict_rows),
        "inputCount": len(input_rows),
    }


def _instruments_from_template_root(frontend_obj: dict) -> list[dict]:
    root_ins = frontend_obj.get("instruments")
    if isinstance(root_ins, list) and root_ins:
        return [copy.deepcopy(x) for x in root_ins if isinstance(x, dict)]
    kinds = frontend_obj.get("instrumentKinds")
    if not isinstance(kinds, dict):
        return []
    out: list[dict] = []
    for scope, slot in (("qualityControl", 1), ("radiationProtection", 2)):
        rows = kinds.get(scope)
        if not isinstance(rows, list):
            continue
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            model = str(row.get("model") or "").strip()
            out.append(
                {
                    "id": "",
                    "instrumentId": "",
                    "identifier": f"MOCK-{scope[:2].upper()}-{idx + 1:02d}",
                    "name": name,
                    "model": model,
                    "certificateNo": "",
                    "validUntil": "",
                    "enabled": True,
                    "instrumentScope": scope,
                    "registrySlot": slot,
                    "bindingSource": "templateKind",
                }
            )
    return out


def _scoped_lists_from_instruments(instruments: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"qualityControl": [], "radiationProtection": []}
    for it in instruments:
        if not isinstance(it, dict):
            continue
        scope = str(it.get("instrumentScope") or "").strip()
        if scope in out:
            out[scope].append(copy.deepcopy(it))
    return out


def build_mock_submit_from_frontend_template(
    frontend_obj: dict,
    *,
    project_id: str = "260001",
    task_no: str = "10",
    report_type: str = "ct_qc",
    fill_ratio: float = 1.0,
    seed: int = 1,
    library_task=None,
) -> dict:
    """
    由前端模板 JSON 生成模拟提交体（结构与 App POST submit 一致）。

    fill_ratio: 0~1，控制随机跳过部分栏位（1=全填）。
    """
    if not isinstance(frontend_obj, dict):
        raise ValueError("frontend_obj must be a dict")

    frontend_obj = _enrich_frontend_pdf_fields_for_mock(
        frontend_obj, library_task=library_task
    )

    from apps.core.inspection_report_type import normalize_submit_report_type

    report_type = normalize_submit_report_type(
        report_type,
        template_id=str(frontend_obj.get("templateId") or "").strip(),
        template_name=str(frontend_obj.get("templateName") or "").strip(),
    )
    enums = frontend_obj.get("enums") if isinstance(frontend_obj.get("enums"), dict) else {}
    rng = random.Random(int(seed))

    payload: dict[str, Any] = {
        "taskNo": task_no,
        "projectId": project_id,
        "reportType": report_type,
        "templateId": str(frontend_obj.get("templateId") or ""),
        "templateVersion": str(frontend_obj.get("version") or "1.0.0"),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "reportInfo": {},
        "hospitalInfo": {},
        "equipmentInfo": {},
        "testResult": {},
        "conclusion": {"allPassed": None, "conclusionText": None},
        "signatures": {
            "inspector": None,
            "checker": None,
            "accompanyingPerson": None,
        },
        "dynamicData": {},
        "rawPayload": {"instruments": {}},
    }

    if frontend_obj.get("instrumentKinds"):
        payload["instrumentKinds"] = copy.deepcopy(frontend_obj["instrumentKinds"])

    instruments = _instruments_from_template_root(frontend_obj)
    payload["instruments"] = instruments
    payload["rawPayload"]["instruments"] = _scoped_lists_from_instruments(instruments)

    payload["reportInfo"]["f1"] = project_id
    payload["reportInfo"]["f2"] = task_no
    payload["dynamicData"]["f1"] = project_id
    payload["dynamicData"]["f2"] = task_no

    rp_bindings = _build_rp_table_bindings(frontend_obj)
    rp_pdf_pids = _collect_rp_pdf_section_pids(frontend_obj, rp_bindings)
    pid_to_field = _index_fields_by_pdf_field_id(frontend_obj)
    rp_managed_pids = _collect_rp_managed_pids(
        frontend_obj,
        rp_bindings,
        pid_to_field=pid_to_field,
        rp_pdf_pids=rp_pdf_pids,
    )

    field_count = 0
    filled_count = 0
    for field in _iter_all_template_fields(frontend_obj):
        pid = str(field.get("pdfFieldId") or field.get("id") or "").strip()
        submit_path = str(field.get("submitPath") or "").strip()
        if not pid and not submit_path:
            continue
        field_count += 1
        if fill_ratio < 1.0:
            if (field_count + seed) % max(1, int(1 / fill_ratio)) != 0:
                continue

        if (
            _is_rp_table_managed_field(
                field,
                managed_pids=rp_managed_pids,
                rp_pdf_pids=rp_pdf_pids,
                pid_to_field=pid_to_field,
            )
            and not _is_background_level_field(field)
        ):
            continue

        if _is_rp_chapter_managed_field(
            field,
            pid_to_field=pid_to_field,
            rp_pdf_pids=rp_pdf_pids,
        ):
            continue

        if _field_should_skip_mock_fill(field):
            continue

        mock_val = _mock_scalar_for_field(field, enums=enums, rng=rng)
        if mock_val is None:
            continue
        filled_count += 1
        _write_field_value_to_payload(payload, field, mock_val)

    preface_factors = _fill_rp_preface_factor_fields(payload, frontend_obj, rng)
    rp_select_ratio = max(0.15, min(1.0, float(fill_ratio)))
    rp_stats = _apply_radiation_protection_chapter_mock(
        payload, frontend_obj, rng, select_ratio=rp_select_ratio
    )
    rp_stats["prefaceFactors"] = preface_factors
    rp_stats["bindingDerived"] = _apply_rp_binding_derived_values(
        payload,
        frontend_obj,
        rp_bindings,
    )
    derived_stats = apply_mock_derived_values(payload, frontend_obj)
    _finalize_rp_slashed_bindings(
        payload,
        frontend_obj,
        rp_stats.get("slashedBindings") if isinstance(rp_stats.get("slashedBindings"), list) else [],
    )
    rp_stats["rowCoherenceFixed"] = _enforce_rp_binding_row_coherence(
        payload,
        frontend_obj,
        rp_bindings,
    )
    location_fields = _location_fields_by_row_index(frontend_obj)
    active_indices = _active_rp_binding_indices(payload, rp_bindings)
    rp_stats["seqAssigned"] = _assign_rp_sequence_numbers(
        payload,
        rp_bindings,
        active_indices,
        pid_to_field,
        frontend_obj,
    )
    rp_stats["locationSlashed"] = _finalize_rp_location_and_seq_fields(
        payload,
        rp_bindings,
        pid_to_field=pid_to_field,
        frontend_obj=frontend_obj,
        location_fields=location_fields,
    )
    rp_stats["orphanPurged"] = _purge_rp_orphan_mock_values(
        payload,
        frontend_obj,
        rp_bindings,
        rp_pdf_pids=rp_pdf_pids,
    )
    rp_stats["gapSlashed"] = _slash_unfilled_rp_table_cells(
        payload,
        frontend_obj,
        managed_pids=rp_managed_pids,
        bindings=rp_bindings,
    )

    payload["_mockMeta"] = {
        "generator": "mock_inspection_submit",
        "fieldCount": field_count,
        "filledCount": filled_count,
        "fillRatio": fill_ratio,
        "derived": derived_stats,
        "radiationProtection": rp_stats,
    }
    return payload


def load_frontend_template_json(path: str | Path) -> dict:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("frontend template JSON root must be an object")
    return obj


def enrich_mock_submit_with_task_binding(
    payload: dict,
    *,
    project,
    library_task,
) -> dict:
    """若 Django 环境可用：按项目/任务模板合并仪器并规范化 signatures 块。"""
    from apps.api.inspection_report_make import finalize_submit_instruments_in_payload

    out, _bundle = finalize_submit_instruments_in_payload(
        copy.deepcopy(payload),
        project_obj=project,
        task_obj=library_task,
    )
    out.pop("_mockMeta", None)
    return out


def preview_backfill_fields(
    payload: dict,
    *,
    project,
    library_task,
    task_no: str,
    map_id: str | None = None,
) -> Tuple[list, str]:
    """调用与线上一致的回填，返回 (filled_fields, message)。"""
    from apps.api.inspection_report_make import _build_filled_template_fields_for_task

    filled, _pdf_id, reason, _json_name = _build_filled_template_fields_for_task(
        library_task,
        payload,
        map_id=map_id,
        project=project,
        task_no=task_no,
    )
    if not filled:
        return [], reason or "回填失败"
    return filled, "ok"


def summarize_filled_fields_for_instruments(
    filled_fields: list,
    *,
    pdf_field_ids: list[str] | None = None,
) -> dict[str, str]:
    """提取指定 pdfFieldId 的 content，便于肉眼核对仪器/样例格。"""
    want = {str(x).strip().lower() for x in (pdf_field_ids or ["f630", "f631", "f1", "f2"])}
    out: dict[str, str] = {}
    for row in filled_fields or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("pdfFieldId") or row.get("id") or "").strip().lower()
        if pid not in want:
            continue
        content = row.get("content")
        if content in (None, ""):
            content = row.get("imageData") or row.get("checked")
        if content not in (None, ""):
            out[pid] = str(content)
    return out


def _load_auxiliary_frontend_json_for_task(library_task) -> dict | None:
    """读取任务绑定的 *_frontend.json 辅助模板（含 steps）。"""
    from apps.core import pipeline_service
    from apps.core.library_file_service import library_file_exists_on_disk
    from apps.core.library_task_template_binding_service import is_auxiliary_template_json_file
    from apps.core.models import LibraryFile

    for lf in library_task.library_files.filter(
        category=LibraryFile.CATEGORY_TEMPLATE
    ).order_by("-created_at", "-id"):
        if not library_file_exists_on_disk(lf):
            continue
        if not is_auxiliary_template_json_file(lf):
            continue
        if not (lf.original_name or "").lower().endswith("_frontend.json"):
            continue
        try:
            path = pipeline_service.library_absolute_path(lf.relative_path)
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("steps"), list) and obj["steps"]:
            return obj
    return None


def resolve_frontend_template_for_mock_submit(
    *,
    project,
    library_task,
    task_no: str,
    request=None,
) -> Tuple[dict, str]:
    """
    解析用于模拟提交的前端模板 JSON。
    优先 export-frontend-json 运行态；否则回退 auxiliary *_frontend.json。
    返回 (frontend_obj, source_label)。
    """
    from apps.api.inspection_frontend_export_service import (
        InspectionFrontendExportError,
        build_runtime_frontend_for_inspection_export,
    )
    from apps.core.models import LibraryTaskAssignment

    has_assignment = LibraryTaskAssignment.objects.filter(
        project=project, library_task=library_task
    ).exists()
    if has_assignment:
        try:
            fe = build_runtime_frontend_for_inspection_export(
                request,
                project=project,
                library_task=library_task,
                display_task_no=task_no,
            )
            if isinstance(fe, dict) and isinstance(fe.get("steps"), list) and fe["steps"]:
                return fe, "export-frontend-json"
        except InspectionFrontendExportError:
            pass

    aux = _load_auxiliary_frontend_json_for_task(library_task)
    if aux is not None:
        return aux, "auxiliary-frontend-json"
    raise ValueError("未找到可用的前端模板（需任务已分配且可导出，或绑定 auxiliary *_frontend.json）")


def build_mock_submit_for_project_task(
    *,
    project,
    library_task,
    task_no: str,
    request=None,
    fill_ratio: float = 1.0,
    merge_task_binding: bool = True,
    include_backfill_preview: bool = False,
    placeholder_map_id: str | None = None,
) -> dict:
    """
    为项目内现场记录任务生成模拟提交 JSON（Web / 工作台用）。
    返回 { ok, payload, meta, backfillPreview?, source } 或抛出 ValueError。
    """
    from apps.api.inspection_report_make import merge_task_template_bound_instruments_into_payload
    from apps.core.models import LibraryTask
    from apps.core.project_numbering import project_public_id

    if library_task is None:
        raise ValueError("未找到任务")
    if getattr(library_task, "output_target", None) != LibraryTask.OUTPUT_SITE_RECORD:
        raise ValueError("仅支持现场记录任务")

    frontend_obj, source = resolve_frontend_template_for_mock_submit(
        project=project,
        library_task=library_task,
        task_no=task_no,
        request=request,
    )

    from apps.core.inspection_report_type import normalize_submit_report_type

    project_code = project_public_id(project)
    report_type = normalize_submit_report_type(
        frontend_obj.get("reportType"),
        template_id=str(frontend_obj.get("templateId") or "").strip(),
        template_name=str(frontend_obj.get("templateName") or "").strip(),
    )
    payload = build_mock_submit_from_frontend_template(
        frontend_obj,
        project_id=project_code,
        task_no=task_no,
        report_type=report_type,
        fill_ratio=float(fill_ratio),
        library_task=library_task,
    )
    meta = payload.pop("_mockMeta", {}) or {}

    if merge_task_binding:
        payload = enrich_mock_submit_with_task_binding(
            payload, project=project, library_task=library_task
        )
    else:
        payload = merge_task_template_bound_instruments_into_payload(
            payload, library_task, project_obj=project
        )

    result: dict[str, Any] = {
        "ok": True,
        "source": source,
        "taskNo": task_no,
        "projectId": project_code,
        "libraryTaskId": library_task.pk,
        "payload": payload,
        "meta": meta,
    }

    if include_backfill_preview:
        filled, msg = preview_backfill_fields(
            payload,
            project=project,
            library_task=library_task,
            task_no=task_no,
            map_id=placeholder_map_id,
        )
        if msg != "ok":
            raise ValueError(msg or "回填预览失败")
        result["backfillPreview"] = build_backfill_preview_rows(filled, frontend_obj)
        result["backfillPreview"]["samplePdfFields"] = summarize_filled_fields_for_instruments(
            filled
        )

    return result


def execute_mock_submit_for_equipment_link(
    *,
    project,
    link_id: int,
    user,
    request=None,
    fill_ratio: float = 1.0,
) -> dict:
    """
    按委托设备行：为关联现场记录任务生成模拟 JSON 并走完整提交流程（submit 库 + 回填 PDF）。
    """
    from apps.api.inspection_views import (
        InspectionSubmitExecutionError,
        ensure_case_for_project_library_task,
        execute_inspection_submit_for_task,
    )
    from apps.core.models import LibraryProjectEquipment, LibraryTask
    from apps.core.project_equipment_service import (
        effective_report_task,
        normalize_equipment_report_task,
        site_submit_tasks_for_report_task,
    )

    link = (
        LibraryProjectEquipment.objects.filter(project=project, pk=link_id)
        .select_related("equipment", "report_task")
        .first()
    )
    if link is None:
        raise ValueError("设备委托记录不存在")
    report_task = normalize_equipment_report_task(effective_report_task(link))
    site_rows = site_submit_tasks_for_report_task(project, report_task)
    if not site_rows:
        raise ValueError("该设备未挂载现场记录任务")

    submitted: list[dict] = []
    for row in site_rows:
        library_task = LibraryTask.objects.filter(
            pk=row["libraryTaskId"], output_target=LibraryTask.OUTPUT_SITE_RECORD
        ).first()
        if library_task is None:
            continue
        task_no = str(row["taskNo"]).strip()
        frontend_obj, _source = resolve_frontend_template_for_mock_submit(
            project=project,
            library_task=library_task,
            task_no=task_no,
            request=request,
        )
        built = build_mock_submit_for_project_task(
            project=project,
            library_task=library_task,
            task_no=task_no,
            request=request,
            fill_ratio=fill_ratio,
            merge_task_binding=True,
            include_backfill_preview=False,
        )
        payload = built.get("payload") if isinstance(built.get("payload"), dict) else {}
        case, resolved_no = ensure_case_for_project_library_task(project, library_task, user)
        try:
            submit_data = execute_inspection_submit_for_task(
                user,
                resolved_no,
                payload,
                case=case,
                project=project,
                sync_equipment_payload=payload,
                library_task=library_task,
            )
        except InspectionSubmitExecutionError as exc:
            raise ValueError(f"任务 {resolved_no}：{exc.message}") from exc
        submitted.append(
            {
                "taskNo": resolved_no,
                "taskCode": row.get("code") or "",
                "taskName": row.get("name") or "",
                "mockMeta": built.get("meta") or {},
                "submit": submit_data,
            }
        )
    if not submitted:
        raise ValueError("未能提交任何现场记录任务")
    return {
        "ok": True,
        "equipmentName": (link.equipment.name if link.equipment_id else "") or "",
        "linkId": link.pk,
        "submitted": submitted,
    }


def mock_submit_site_tasks_for_project(project) -> list[dict]:
    """项目工作台委托立项：列出可生成模拟 JSON 的现场记录任务。"""
    from apps.core.models import LibraryTask
    from apps.core.project_numbering import project_task_no_for_library_task, project_tasks_ordered

    if project is None:
        return []
    ordered = project_tasks_ordered(project)
    rows: list[dict] = []
    for lt in project.library_tasks.filter(
        output_target=LibraryTask.OUTPUT_SITE_RECORD
    ).order_by("code", "id"):
        task_no = project_task_no_for_library_task(lt, project, ordered_tasks=ordered)
        if not task_no:
            continue
        rows.append(
            {
                "taskNo": task_no,
                "libraryTaskId": lt.pk,
                "code": lt.code or "",
                "name": lt.name or "",
                "label": f"{task_no} · {lt.code} · {lt.name}",
            }
        )
    return rows
