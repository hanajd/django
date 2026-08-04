"""
工作场所放射防护（第五章）模板栏位同步与章节公式编译。

- 从模板编辑器 pdf.fields 提取 autoSemantic，生成 field_bindings（含 pdfFieldId）
- 章节公式：测量均值自动 avg(三次读数)；报出值按条件规则复用到各行
- 额外元数据写入 formSchema.radiationProtectionChapter，并同步到 tabletest 版式 JSON
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from utils.conditional_field_rules import (
    compile_conditional_expression,
    export_formula_rules_list,
    export_rule_for_frontend,
    normalize_rule_list,
)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_LAYOUT_PATH = PACKAGE_DIR / "layout" / "tabletest_chapter5_full.json"

CHAPTER_KEY = "site_radiation_protection"
SCHEMA_KEY = "radiationProtectionChapter"

ROW_TOKEN_MEAN = "__ROW_MEAN__"
ROW_TOKEN_MEAN2 = "__ROW_MEAN2__"

_DUAL_GROUP_SLOT_KEYS: tuple[str, ...] = (
    "reading_1",
    "reading_2",
    "reading_3",
    "mean_m",
    "reading_1_2",
    "reading_2_2",
    "reading_3_2",
    "mean_m_2",
    "report_d",
    "report_d_2",
    "annual_dose_msv",
)

_SINGLE_GROUP_SLOT_KEYS: tuple[str, ...] = (
    "reading_1",
    "reading_2",
    "reading_3",
    "mean_m",
    "report_d",
)
ROW_TOKEN_READING = (
    ("reading1", "__ROW_READING1__", "测量读数M", 1),
    ("reading2", "__ROW_READING2__", "测量读数M", 2),
    ("reading3", "__ROW_READING3__", "测量读数M", 3),
)
ROW_TOKEN_REPORT = "__ROW_REPORT__"

_COLUMN_READING = "测量读数M"
_COLUMN_MEAN = "测量均值Mbar"
_COLUMN_REPORT = "报出值D"
_COLUMN_ANNUAL_DOSE = "年剂量估算"
_COLUMN_SEQ = "序号"
_SEQ_LABEL_RE = re.compile(r"^序号_r\d+", re.I)
_F_ID_RE = re.compile(r"^f\d+$", re.I)
_RP_LOCATION_COLUMN_ROLES = frozenset({"检测点位置", "位置细分"})
PROTECTION_NUMBER_PRECISION = 2
# 第五章表格：测量均值固定 3 位；报出值按量级分档（见 format_protection_report_value_display）
# 实际运行时以 apps.core.decimal_precision_runtime_config 为准（可在调试设置热更新）。
PROTECTION_MEAN_PRECISION = 3
PROTECTION_REPORT_PRECISION_RULE = "magnitude_tiered"
_HEADER_READING_RE = re.compile(r"测量读数|读数\s*M", re.I)
_HEADER_MEAN_RE = re.compile(r"测量均值|均值\s*M", re.I)
_HEADER_REPORT_RE = re.compile(r"报出值", re.I)
_HEADER_ANNUAL_DOSE_RE = re.compile(r"年剂量|估算\s*mSv", re.I)


def _decimal_precision_cfg():
    """读取小数精度热更新配置；失败时返回 None（调用方用模块常量兜底）。"""
    try:
        from apps.core.decimal_precision_runtime_config import (
            get_decimal_precision_runtime_config,
        )

        return get_decimal_precision_runtime_config()
    except Exception:
        return None


def _cfg_global_precision() -> int:
    cfg = _decimal_precision_cfg()
    if cfg is None:
        return PROTECTION_NUMBER_PRECISION
    return max(0, int(cfg.global_precision))


def _cfg_mean_precision() -> int:
    cfg = _decimal_precision_cfg()
    if cfg is None or not cfg.chapter5_enabled:
        return _cfg_global_precision()
    return max(0, int(cfg.chapter5_mean_precision))


def _cfg_reading_precision() -> int:
    cfg = _decimal_precision_cfg()
    if cfg is None or not cfg.chapter5_enabled:
        return _cfg_global_precision()
    return max(0, int(cfg.chapter5_reading_precision))


def _cfg_chapter5_enabled() -> bool:
    cfg = _decimal_precision_cfg()
    return True if cfg is None else bool(cfg.chapter5_enabled)


def _cfg_report_precision_for_magnitude(num: float) -> int:
    cfg = _decimal_precision_cfg()
    if cfg is None:
        av = abs(float(num))
        if av < 10:
            return 2
        if av < 100:
            return 1
        return 0
    if not cfg.chapter5_enabled:
        return max(0, int(cfg.global_precision))
    if not cfg.chapter5_report_tiered:
        return max(0, int(cfg.chapter5_report_fixed_precision))
    av = abs(float(num))
    if av < 10:
        return max(0, int(cfg.chapter5_report_lt10_precision))
    if av < 100:
        return max(0, int(cfg.chapter5_report_lt100_precision))
    return max(0, int(cfg.chapter5_report_gte100_precision))


def _cfg_report_precision_cap() -> int:
    """写入 JSON 的报出值 precision 上限（分档时取各档最大值）。"""
    cfg = _decimal_precision_cfg()
    if cfg is None:
        return 2
    if not cfg.chapter5_enabled:
        return max(0, int(cfg.global_precision))
    if not cfg.chapter5_report_tiered:
        return max(0, int(cfg.chapter5_report_fixed_precision))
    return max(
        0,
        int(cfg.chapter5_report_lt10_precision),
        int(cfg.chapter5_report_lt100_precision),
        int(cfg.chapter5_report_gte100_precision),
    )


def _cfg_apply_on_backfill() -> bool:
    cfg = _decimal_precision_cfg()
    return True if cfg is None else bool(cfg.apply_on_backfill)


def _is_annual_dose_field_label(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if "年剂量" in compact:
        return True
    if "估算mSv" in compact or "估算mSv" in compact.replace("（", "(").replace("）", ")"):
        return True
    return "估算" in compact and "mSv" in compact


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def field_table_semantic(field: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(field, dict):
        return {}
    sem = field.get("autoSemantic")
    if isinstance(sem, dict):
        return sem
    return {}


def field_pdf_field_id(field: Mapping[str, Any]) -> str:
    pid = _norm(field.get("pdfFieldId") or field.get("id") or "")
    m = re.match(r"^f\d+$", pid, re.I)
    return pid.lower() if m else ""


_RP_SECTION_KEYS = frozenset({CHAPTER_KEY, "radiation_protection"})


def _field_label_blob(field: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("placeholder", "title", "label", "hierarchyKey", "id"):
        v = _norm(field.get(key) or "")
        if v:
            parts.append(v)
    return " ".join(parts)


def field_looks_like_protection_table_cell(field: Mapping[str, Any]) -> bool:
    """steps 栏位无 sectionKey 时，用标签识别防护表数据格（读数/均值/报出值）。"""
    if not isinstance(field, dict):
        return False
    blob = _field_label_blob(field)
    if not blob or "本底" in blob or "序号本底水平" in blob:
        return False
    # 须含表格行号 _rN，避免性能章节「均匀性_报出值_HU」等误入防护表绑定
    if not re.search(r"_r\d+", blob, re.I):
        return False
    if re.search(r"_测量读数M\d*|_测量均值Mbar|_测量均值|_测量值|_报出值D|_报出值", blob, re.I):
        return True
    return False


# 表体数据行：读数～均值列带内至少这么多格，才参与「末两行」排序（过滤零散噪声格）
_BACKGROUND_TABLE_BODY_MIN_CELLS = 3


def _background_data_x_band(column_layout: Mapping[str, Any]) -> tuple[float, float]:
    """读数～均值列带（不含报出值）。"""
    cols = column_layout.get("columns") if isinstance(column_layout.get("columns"), dict) else {}
    r1 = cols.get("reading_1") if isinstance(cols.get("reading_1"), dict) else {}
    report = cols.get("report_d") if isinstance(cols.get("report_d"), dict) else {}
    x_lo = float(r1.get("x0") or column_layout.get("reading_start_x") or 232.0)
    x_hi = float(report.get("x0") or 668.5)
    if x_hi <= x_lo:
        x_hi = x_lo + 400.0
    return x_lo, x_hi


def infer_background_geometry_from_fields(
    fields: Sequence[Any],
    *,
    column_layout: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    按 PDF 表几何识别本底：第五章防护表**末页最后两行**（①–⑤ / ⑥–⑩ 共十格）+ 报出值。
    不依赖 placeholder/label 命名，也不用「密排 ≥5」之类额外启发式。
    """
    field_list = [f for f in (fields or []) if isinstance(f, dict)]
    if not field_list:
        return {}
    layout = column_layout if isinstance(column_layout, dict) and column_layout.get("columns") else None
    if layout is None:
        layout = resolve_protection_table_layout(field_list)
    x_lo, x_hi = _background_data_x_band(layout)
    preface = float(layout.get("preface_y_cutoff") or 360.0)

    by_row: Dict[str, List[Dict[str, Any]]] = {}
    report_cells: List[Dict[str, Any]] = []
    for field in field_list:
        if not is_protection_pdf_field(field):
            continue
        if not _is_rp_table_data_row_field(field, preface_y_cutoff=preface):
            continue
        pid = export_field_pdf_id(field)
        if not pid:
            continue
        page, y0, y1 = _field_pdf_anchor_y_bounds(field)
        if page < 5:
            continue
        x0, x1 = _field_pdf_x_bounds(field)
        xc = (x0 + x1) / 2.0
        pt = _protection_row_coordinate_key(field)
        item = {
            "field": field,
            "pid": pid,
            "page": page,
            "x0": x0,
            "y0": y0,
            "y1": y1,
            "xc": xc,
            "pt": pt,
        }
        if xc >= x_hi - 2.0:
            report_cells.append(item)
            continue
        if x_lo - 2.0 <= xc < x_hi + 2.0:
            by_row.setdefault(pt, []).append(item)

    # 表体行：读数～均值带内格数足够，按 page、y 排序后取末页最后两行
    body_rows: List[tuple[str, List[Dict[str, Any]]]] = []
    for pt, items in by_row.items():
        if len(items) < _BACKGROUND_TABLE_BODY_MIN_CELLS:
            continue
        body_rows.append((pt, sorted(items, key=lambda r: (float(r["x0"]), r["pid"]))))
    if not body_rows:
        return {}
    body_rows.sort(
        key=lambda pair: (
            int(pair[1][0]["page"]),
            float(pair[1][0]["y0"]),
            pair[0],
        )
    )
    last_page = int(body_rows[-1][1][0]["page"])
    page_rows = [pair for pair in body_rows if int(pair[1][0]["page"]) == last_page]
    if not page_rows:
        return {}
    cluster = page_rows[-2:] if len(page_rows) >= 2 else page_rows[-1:]

    reading_rows: List[Dict[str, Any]] = []
    row_keys: List[str] = []
    for pt, items in cluster:
        row_keys.append(pt)
        reading_rows.extend(items)
    reading_rows.sort(key=lambda r: (float(r["y0"]), float(r["x0"]), r["pid"]))
    reading_rows = reading_rows[:10]
    if not reading_rows:
        return {}

    y0 = min(float(r["y0"]) for r in reading_rows)
    y1 = max(float(r["y1"]) for r in reading_rows)
    page = int(reading_rows[0]["page"])

    # 报出值：同页、落在 report 列带、y 与末两行相交
    report_pid = ""
    report_candidates: List[tuple[float, str]] = []
    y_mid = (y0 + y1) / 2.0
    for item in report_cells:
        if int(item["page"]) != page:
            continue
        iy0, iy1 = float(item["y0"]), float(item["y1"])
        if iy1 < y0 - 4.0 or iy0 > y1 + 4.0:
            continue
        dist = abs((iy0 + iy1) / 2.0 - y_mid)
        report_candidates.append((dist, item["pid"]))
    if report_candidates:
        report_candidates.sort(key=lambda t: (t[0], t[1]))
        report_pid = report_candidates[0][1]

    pids = {r["pid"] for r in reading_rows}
    if report_pid:
        pids.add(report_pid)
    return {
        "page": page,
        "bbox": {
            "x0": round(x_lo, 2),
            "y0": round(y0 - 2.0, 2),
            "x1": round(float((layout.get("columns") or {}).get("report_d", {}).get("x1") or 810.82), 2),
            "y1": round(y1 + 2.0, 2),
        },
        "rowKeys": row_keys,
        "pids": sorted(pids),
        "readings": reading_rows,
        "report_d": report_pid,
    }



def background_field_pids_from_geometry(
    fields: Sequence[Any],
    *,
    column_layout: Optional[Mapping[str, Any]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> set[str]:
    """本底相关 pdfFieldId 集合（读数①–⑩ + 报出值）。"""
    pids: set[str] = set()
    if isinstance(chapter, dict):
        bb = chapter.get("backgroundBinding")
        if isinstance(bb, dict):
            for i in range(1, 11):
                p = _norm(bb.get(f"bg_reading_{i}") or "").lower()
                if p:
                    pids.add(p)
            rp = _norm(bb.get("report_d") or "").lower()
            if rp:
                pids.add(rp)
    geo = infer_background_geometry_from_fields(fields, column_layout=column_layout)
    for p in geo.get("pids") or []:
        pids.add(_norm(p).lower())
    return {p for p in pids if p}


def is_background_reading_pdf_field(
    field: Mapping[str, Any],
    *,
    background_pids: Optional[set[str]] = None,
    column_layout: Optional[Mapping[str, Any]] = None,
    fields: Optional[Sequence[Any]] = None,
) -> bool:
    """本底行 ①–⑩ 填写格：优先 PDF 表几何（密排读数带），不靠 label。"""
    if not isinstance(field, dict) or not is_protection_pdf_field(field):
        return False
    pid = export_field_pdf_id(field)
    if not pid:
        return False
    pids = background_pids
    if pids is None and fields is not None:
        pids = background_field_pids_from_geometry(fields, column_layout=column_layout)
    if pids is not None:
        if pid not in pids:
            return False
        # 报出值汇总格不算读数格
        geo = infer_background_geometry_from_fields(fields or [field], column_layout=column_layout)
        if pid == _norm(geo.get("report_d") or ""):
            return False
        return True
    # 无上下文时：单格无法判定密排，返回 False（避免靠命名误判）
    return False


def is_background_range_pdf_field(
    field: Mapping[str, Any],
    *,
    background_pids: Optional[set[str]] = None,
    column_layout: Optional[Mapping[str, Any]] = None,
    fields: Optional[Sequence[Any]] = None,
) -> bool:
    """本底报出值/范围汇总格：几何上落在本底带 report_d。"""
    if not isinstance(field, dict):
        return False
    pid = export_field_pdf_id(field)
    if not pid:
        return False
    if fields is not None or background_pids is not None:
        geo = infer_background_geometry_from_fields(
            fields if fields is not None else [field],
            column_layout=column_layout,
        )
        return pid == _norm(geo.get("report_d") or "")
    return False


def is_background_level_pdf_field(
    field: Mapping[str, Any],
    *,
    background_pids: Optional[set[str]] = None,
    column_layout: Optional[Mapping[str, Any]] = None,
    fields: Optional[Sequence[Any]] = None,
) -> bool:
    """本底读数或本底报出：一律按表几何 pid 集合判定。"""
    if not isinstance(field, dict):
        return False
    pid = export_field_pdf_id(field)
    if not pid:
        return False
    pids = background_pids
    if pids is None and fields is not None:
        pids = background_field_pids_from_geometry(fields, column_layout=column_layout)
    if pids is not None:
        return pid in pids
    return False


def _background_reading_fields_sorted(
    fields: List[Any],
    *,
    column_layout: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    geo = infer_background_geometry_from_fields(fields, column_layout=column_layout)
    return list(geo.get("readings") or [])


def _background_binding_is_complete(binding: Optional[Mapping[str, Any]]) -> bool:
    """已保存本底绑定是否含报出格 + 至少 2 个读数（可写 min~max）。"""
    if not isinstance(binding, dict):
        return False
    if not _norm(binding.get("report_d") or ""):
        return False
    n = sum(1 for i in range(1, 11) if _norm(binding.get(f"bg_reading_{i}") or ""))
    return n >= 2


def resolve_background_row_binding(
    fields: Sequence[Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
    layout: Optional[Mapping[str, Any]] = None,
    template_payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    第五章本底行表结构绑定：①–⑩ 读数格 + 报出值 D 汇总格 + 校准因子。
    按 PDF 表列带密排几何定位，不扫描 placeholder 标签。

    模板已保存且完整的 ``backgroundBinding`` 优先：导出 steps 几何易把末行测量
    报出格误判为本底（如 f465），不可覆盖编辑器已确认的 f494 等本底报出格。
    """
    if isinstance(chapter, dict) and _background_binding_is_complete(chapter.get("backgroundBinding")):
        return dict(chapter["backgroundBinding"])

    field_list = list(fields or [])
    if not field_list and isinstance(template_payload, dict):
        field_list = list(iter_frontend_export_fields(template_payload))
    if not field_list:
        return {}

    column_layout = resolve_chapter5_layout_for_fields(
        field_list,
        template_payload=template_payload,
        chapter=chapter,
    )
    if isinstance(layout, dict) and layout.get("columns"):
        column_layout = {**column_layout, "columns": {**(column_layout.get("columns") or {}), **layout.get("columns")}}

    geo = infer_background_geometry_from_fields(field_list, column_layout=column_layout)
    readings = list(geo.get("readings") or [])
    if not readings:
        return {}

    page = int(geo.get("page") or readings[0].get("page") or 0)
    y_vals = [float(r.get("y0") or 0.0) for r in readings]
    row_y = round(sum(y_vals) / max(len(y_vals), 1), 1)
    out: Dict[str, Any] = {
        "kind": "background",
        "radiationPoint": f"rp_p{page}_y{row_y}",
        "report_d": _norm(geo.get("report_d") or ""),
        "calibration_factor": resolve_background_calibration_factor_pid(chapter, field_list),
        "bbox": geo.get("bbox"),
        "rowKeys": list(geo.get("rowKeys") or []),
    }
    for idx, row in enumerate(readings[:10], start=1):
        pid = _norm(row.get("pid") or "")
        if pid:
            out[f"bg_reading_{idx}"] = pid

    return out if _norm(out.get("report_d") or "") or any(_norm(out.get(f"bg_reading_{i}") or "") for i in range(1, 11)) else {}


def _chapter_rule_calibration_factor_pid(expr: str, *, mean_token: str) -> str:
    """从章节报出值表达式提取校准因子 f 号（如 {mean}*f927）。"""
    expr = _norm(expr)
    if not expr or mean_token not in expr:
        return ""
    m = re.search(r"\*\s*(f\d+)", expr, re.I)
    return m.group(1).lower() if m else ""


def _chapter_g1_calibration_factor_pid(chapter: Optional[Mapping[str, Any]]) -> str:
    """从章节报出值规则第一组表达式提取校准因子 f 号（如 {mean}*f927）。"""
    chapter = chapter if isinstance(chapter, dict) else {}
    rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        expr = _norm(rule.get("expression") or rule.get("formula") or "")
        if not expr or "{mean2}" in expr:
            continue
        pid = _chapter_rule_calibration_factor_pid(expr, mean_token="{mean}")
        if pid:
            return pid
    return ""


def _chapter_g2_calibration_factor_pid(chapter: Optional[Mapping[str, Any]]) -> str:
    """从章节报出值规则第二组表达式提取校准因子 f 号（如 {mean2}*f158）。"""
    chapter = chapter if isinstance(chapter, dict) else {}
    rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        expr = _norm(rule.get("expression") or rule.get("formula") or "")
        if not expr or "{mean2}" not in expr:
            continue
        pid = _chapter_rule_calibration_factor_pid(expr, mean_token="{mean2}")
        if pid:
            return pid
    return ""


def resolve_background_calibration_factor_pid(
    chapter: Optional[Mapping[str, Any]],
    fields: Optional[Sequence[Any]] = None,
) -> str:
    """本底范围公式校准因子：章节规则 → 防护表报出值公式 *fNNN → 表前「校准因子」栏。"""
    pid = _chapter_g1_calibration_factor_pid(chapter)
    if pid:
        return pid
    for field in fields or []:
        if not isinstance(field, dict) or not is_protection_pdf_field(field):
            continue
        blob = _field_label_blob(field)
        if "报出值" not in blob and "_报出值" not in blob:
            continue
        if "本底" in blob or "序号本底" in blob:
            continue
        expr = _norm(
            field.get("fieldExpression")
            or field.get("formula")
            or field.get("pdfFieldExpression")
            or ""
        )
        m = re.search(r"\*\s*(f\d+)\b", expr, re.I)
        if m:
            return m.group(1).lower()
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        blob = _field_label_blob(field)
        if "校准因子" in blob or "137Cs" in blob:
            out = export_field_pdf_id(field) or _norm(field.get("pdfFieldId") or "")
            if out:
                return out.lower()
    return ""


def background_range_expression(reading_pids: Sequence[str], factor_pid: str) -> str:
    """兼容旧脚本：拼本底范围式。新逻辑不再自动写入栏位。"""
    refs = [_norm(pid) for pid in reading_pids if _norm(pid)]
    if len(refs) < 2 or not _norm(factor_pid):
        return ""
    joined = ",".join(refs)
    factor = _norm(factor_pid)
    return f"{factor}*min({joined})~{factor}*max({joined})"


def apply_background_formulas_to_pdf_fields(
    fields: List[Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
    geometry_fields: Optional[Sequence[Any]] = None,
) -> None:
    """
    本底相关：不再自动提取校准因子、不自动写本底报出公式。

    本底报出值由编辑器章节/栏位公式人工配置。此处仅清掉误套到本底
    **读数格**上的 avg（忽略 override），避免把十格读数当成测量均值。
    """
    chapter = chapter if isinstance(chapter, dict) else {}
    field_list = [f for f in (fields or []) if isinstance(f, dict)]
    geo_source = [f for f in (geometry_fields or []) if isinstance(f, dict)]
    if len(geo_source) < 2:
        geo_source = field_list
    reading_pids: set[str] = set()
    saved_bb = chapter.get("backgroundBinding") if isinstance(chapter.get("backgroundBinding"), dict) else {}
    for i in range(1, 11):
        p = _norm(saved_bb.get(f"bg_reading_{i}") or "")
        if p:
            reading_pids.add(p)
    if len(reading_pids) < 1:
        geo = infer_background_geometry_from_fields(geo_source)
        reading_pids = {
            _norm(r.get("pid") or "")
            for r in (geo.get("readings") or [])
            if isinstance(r, dict) and _norm(r.get("pid") or "")
        }
    if not reading_pids:
        return
    for field in field_list:
        if not isinstance(field, dict):
            continue
        pid = export_field_pdf_id(field)
        if not pid or pid not in reading_pids:
            continue
        expr = _norm(
            field.get("fieldExpression")
            or field.get("formula")
            or field.get("pdfFieldExpression")
            or ""
        )
        if not re.match(r"^avg\s*\(", expr, re.I):
            continue
        field["fieldExpression"] = ""
        field["formula"] = ""
        field.pop("fieldFormulaUserOverride", None)
        src = field.get("source") if isinstance(field.get("source"), dict) else None
        if isinstance(src, dict) and "pdfFieldExpression" in src:
            field["source"] = {**src, "pdfFieldExpression": ""}


def is_protection_pdf_field(field: Mapping[str, Any]) -> bool:
    if not isinstance(field, dict):
        return False
    sk = _norm(field.get("templateSectionKey") or field.get("sectionKey") or "")
    if sk in _RP_SECTION_KEYS:
        return True
    if _norm(field.get("sectionType") or "") == "radiationProtection":
        return True
    sem = protection_field_semantic_unified(field)
    if sem.get("tableId") or sem.get("radiationPoint") or sem.get("radiationColumn"):
        return True
    if field_looks_like_protection_table_cell(field):
        return True
    ph = _field_label_blob(field)
    return "放射防护" in ph or "工作场所" in ph


def _field_display_label(field: Mapping[str, Any]) -> str:
    for key in ("placeholder", "title", "label", "id"):
        v = _norm(field.get(key) or "")
        if v:
            return v
    return ""


_PROTECTION_ROW_SUFFIX_PATTERNS = (
    re.compile(r"_测量读数M(\d+)$", re.I),
    re.compile(r"_测量读数M$", re.I),
    re.compile(r"_测量均值Mbar$", re.I),
    re.compile(r"_测量均值.*$", re.I),
    re.compile(r"_测量值$", re.I),
    re.compile(r"_报出值D$", re.I),
    re.compile(r"_报出值$", re.I),
    re.compile(r"_备注$", re.I),
)


def _protection_row_point_key(field: Mapping[str, Any], sem: Mapping[str, Any]) -> str:
    """同一检测点位：工作人员操作位K_r1_测量读数M1 → 工作人员操作位K_r1。"""
    pt = _norm(sem.get("radiationPoint") or "")
    if pt:
        return pt
    label = _field_display_label(field)
    if not label:
        return ""
    for pat in _PROTECTION_ROW_SUFFIX_PATTERNS:
        m = pat.search(label)
        if m:
            return _norm(label[: m.start()])
    return label


def _reading_index_from_field(sem: Mapping[str, Any], field: Mapping[str, Any]) -> int:
    try:
        idx = int(sem.get("readingIndex") or 0)
    except (TypeError, ValueError):
        idx = 0
    if idx:
        return idx
    label = _field_display_label(field)
    m = re.search(r"读数M?(\d+)", label, re.I)
    if m:
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            pass
    return 0


def _field_pdf_anchor_rect(field: Mapping[str, Any]) -> tuple[int, float, float]:
    page, x0, y0, _y1 = _field_pdf_anchor_bounds(field)
    return page, x0, y0


def _field_pdf_anchor_bounds(field: Mapping[str, Any]) -> tuple[int, float, float, float]:
    """统一 steps.pdfAnchor 与 pdf.fields 顶层 rect（page,x0,y0,w,h）。"""
    anchor = field.get("pdfAnchor")
    if isinstance(anchor, dict):
        rect = anchor.get("rect")
        try:
            page = int(anchor.get("page") or 5)
        except (TypeError, ValueError):
            page = 5
        if isinstance(rect, (list, tuple)) and len(rect) >= 4:
            try:
                x0 = float(rect[0])
                y0 = float(rect[1])
                x1 = float(rect[2])
                y1 = float(rect[3])
                if y1 < y0:
                    y0, y1 = y1, y0
                return page, x0, y0, y1
            except (TypeError, ValueError):
                pass
        if isinstance(rect, (list, tuple)) and len(rect) >= 2:
            try:
                return page, float(rect[0]), float(rect[1]), float(rect[1])
            except (TypeError, ValueError):
                pass
    rect = field.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) >= 5:
        try:
            page = int(rect[0])
            x0 = float(rect[1])
            y0 = float(rect[2])
            height = float(rect[4])
            return page, x0, y0, y0 + height
        except (TypeError, ValueError):
            pass
    try:
        page = int(field.get("page") or 5)
        if all(k in field for k in ("x0", "y0", "x1", "y1")):
            x0 = float(field.get("x0"))
            y0 = float(field.get("y0"))
            x1 = float(field.get("x1"))
            y1 = float(field.get("y1"))
            if y1 < y0:
                y0, y1 = y1, y0
            return page, x0, y0, y1
        x0 = float(field.get("x") or 0.0)
        y0 = float(field.get("y") or 0.0)
        height = float(field.get("h") or 0.0)
        if x0 or y0 or height:
            return page, x0, y0, y0 + height
    except (TypeError, ValueError):
        pass
    return 5, 0.0, 0.0, 0.0


def _field_pdf_anchor_y_bounds(field: Mapping[str, Any]) -> tuple[int, float, float]:
    page, _x0, y0, y1 = _field_pdf_anchor_bounds(field)
    return page, y0, y1


def _field_pdf_anchor_y_center(field: Mapping[str, Any]) -> tuple[int, float]:
    page, y0, y1 = _field_pdf_anchor_y_bounds(field)
    return page, (y0 + y1) / 2.0


def _seq_field_anchor_height(field: Mapping[str, Any]) -> float:
    _, y0, y1 = _field_pdf_anchor_y_bounds(field)
    return abs(y1 - y0)


def is_rp_table_seq_field(field: Mapping[str, Any]) -> bool:
    """
    第五章表格序号列（含续页与跨行合并格；排除表前误标为「序号_r*」的校准因子等）。
    """
    if not isinstance(field, dict):
        return False
    label = _field_display_label(field)
    if not _SEQ_LABEL_RE.match(label):
        return False
    page, x0, _y0 = _field_pdf_anchor_rect(field)
    if page < 5 or x0 > 80.0:
        return False
    height = _seq_field_anchor_height(field)
    if page > 5:
        return True
    if height >= 36.0:
        return True
    _, _, y0 = _field_pdf_anchor_rect(field)
    return y0 >= 190.0


def _column_role(sem: Mapping[str, Any], field: Mapping[str, Any]) -> str:
    col = _norm(sem.get("radiationColumn") or "")
    if col:
        if _is_annual_dose_field_label(col):
            return _COLUMN_ANNUAL_DOSE
        if col in ("测量读数M", "测量读数") or col.startswith("测量读数"):
            return _COLUMN_READING
        if col == "测量值":
            return _COLUMN_READING
        if col in ("测量均值Mbar", "测量均值") or "均值" in col:
            return _COLUMN_MEAN
        if col in ("报出值D", "报出值") or "报出" in col:
            return _COLUMN_REPORT
        return col
    if sem.get("meanOfReadings") or field.get("mean"):
        return _COLUMN_MEAN
    ph = _field_display_label(field)
    if _SEQ_LABEL_RE.match(ph):
        return _COLUMN_SEQ if is_rp_table_seq_field(field) else ""
    if "报出" in ph:
        if _is_annual_dose_field_label(ph):
            return _COLUMN_ANNUAL_DOSE
        return _COLUMN_REPORT
    if "均值" in ph or "平均" in ph:
        return _COLUMN_MEAN
    if "读数" in ph:
        return _COLUMN_READING
    if "测量值" in ph and "报出" not in ph:
        return _COLUMN_READING
    return ""


def _field_sort_x(field: Mapping[str, Any]) -> float:
    _page, x0, _y0 = _field_pdf_anchor_rect(field)
    return float(x0)


def _binding_reading_keys(*, group: int = 1) -> tuple[str, str, str]:
    if group == 2:
        return ("reading_1_2", "reading_2_2", "reading_3_2")
    return ("reading_1", "reading_2", "reading_3")


def _binding_mean_key(*, group: int = 1) -> str:
    return "mean_m_2" if group == 2 else "mean_m"


def _binding_report_key(*, group: int = 1) -> str:
    return "report_d_2" if group == 2 else "report_d"


def _pdf_item_x_bounds(item: Mapping[str, Any]) -> tuple[float, float, float]:
    x0 = float(item.get("x") or 0.0)
    w = float(item.get("w") or 0.0)
    x1 = x0 + w
    return x0, x1, (x0 + x1) / 2.0


_LAYOUT_COLUMN_BAND_KEYS = (
    "point_id",
    "location_main",
    "location_sub",
    "location_full",
    "reading_1",
    "reading_2",
    "reading_3",
    "readings_merged",
    "mean_m",
    "report_d",
    "mean_m_2",
    "report_d_2",
    "annual_dose_msv",
)

_BINDING_SLOT_KEYS = frozenset(_SINGLE_GROUP_SLOT_KEYS + _DUAL_GROUP_SLOT_KEYS)

_PROTECTION_DATA_COLUMN_SLOTS = frozenset(
    {
        "reading_1",
        "reading_2",
        "reading_3",
        "mean_m",
        "report_d",
        "reading_1_2",
        "reading_2_2",
        "reading_3_2",
        "mean_m_2",
        "report_d_2",
        "annual_dose_msv",
    }
)

_SLOT_TO_COLUMN_ROLE: Dict[str, str] = {
    "reading_1": _COLUMN_READING,
    "reading_2": _COLUMN_READING,
    "reading_3": _COLUMN_READING,
    "reading_1_2": _COLUMN_READING,
    "reading_2_2": _COLUMN_READING,
    "reading_3_2": _COLUMN_READING,
    "mean_m": _COLUMN_MEAN,
    "mean_m_2": _COLUMN_MEAN,
    "report_d": _COLUMN_REPORT,
    "report_d_2": _COLUMN_REPORT,
    "annual_dose_msv": _COLUMN_ANNUAL_DOSE,
}


def _layout_columns_from_json(layout: Mapping[str, Any]) -> Dict[str, Dict[str, float]]:
    table = layout.get("table") if isinstance(layout.get("table"), dict) else {}
    cols = table.get("columns") if isinstance(table.get("columns"), dict) else {}
    if not cols and isinstance(layout.get("columns"), dict):
        cols = layout.get("columns")  # type: ignore[assignment]
    out: Dict[str, Dict[str, float]] = {}
    for key in _LAYOUT_COLUMN_BAND_KEYS:
        band = cols.get(key)
        if isinstance(band, dict) and band.get("x0") is not None and band.get("x1") is not None:
            out[key] = {"x0": float(band["x0"]), "x1": float(band["x1"])}
    return out


def _default_layout_column_bands() -> Dict[str, Dict[str, float]]:
    if not DEFAULT_LAYOUT_PATH.is_file():
        return {}
    try:
        with DEFAULT_LAYOUT_PATH.open("r", encoding="utf-8") as f:
            blob = json.load(f)
        return _layout_columns_from_json(blob.get("layout") or blob)
    except Exception:
        return {}


def _field_as_layout_item(field: Mapping[str, Any]) -> Dict[str, Any]:
    page, x0, y0, y1 = _field_pdf_anchor_bounds(field)
    x0b, x1b = _field_pdf_x_bounds(field)
    w = float(field.get("w") or 0.0) or max(0.0, x1b - x0b)
    h = float(field.get("h") or 0.0) or max(0.0, y1 - y0)
    return {
        "page": page,
        "x": x0,
        "y": y0,
        "w": w,
        "h": h,
        "templateSectionKey": str(field.get("templateSectionKey") or ""),
    }


def _field_pdf_x_bounds(field: Mapping[str, Any]) -> tuple[float, float]:
    page, x0, y0, y1 = _field_pdf_anchor_bounds(field)
    del page, y0, y1
    try:
        if all(k in field for k in ("x0", "x1")):
            fx1 = float(field.get("x1"))
            if fx1 > x0:
                return x0, fx1
    except (TypeError, ValueError):
        pass
    anchor = field.get("pdfAnchor")
    if isinstance(anchor, dict):
        rect = anchor.get("rect")
        if isinstance(rect, (list, tuple)) and len(rect) >= 4:
            try:
                ax0 = float(rect[0])
                ax1 = float(rect[2])
                if ax1 > ax0:
                    return ax0, ax1
            except (TypeError, ValueError):
                pass
    try:
        w = float(field.get("w") or 0.0)
        if w > 0:
            return x0, x0 + w
    except (TypeError, ValueError):
        pass
    rect = field.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) >= 4:
        try:
            w = float(rect[3])
            if w > 0:
                return x0, x0 + w
        except (TypeError, ValueError):
            pass
    return x0, x0


def _ensure_layout_data_columns(layout: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(layout)
    cols = dict(out.get("columns") or {})
    defaults = _default_layout_column_bands()
    for key, band in defaults.items():
        cols.setdefault(key, band)
    out["columns"] = cols
    return out


def resolve_protection_table_layout(
    fields: List[Mapping[str, Any]],
    *,
    layout_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """第五章表列带：版式 JSON + tabletest 默认 + 栏位推断（检测点/读数左界）。"""
    items = [_field_as_layout_item(f) for f in fields or [] if isinstance(f, dict)]
    root = layout_json.get("layout") if isinstance(layout_json, dict) and isinstance(layout_json.get("layout"), dict) else layout_json
    base = infer_protection_table_column_layout(items, layout_json=root if isinstance(root, dict) else None)
    merged_cols = dict(_default_layout_column_bands())
    merged_cols.update(base.get("columns") or {})
    if isinstance(root, dict):
        merged_cols.update(_layout_columns_from_json(root))
    base["columns"] = merged_cols
    return _ensure_layout_data_columns(base)


def _is_rp_table_data_row_field(field: Mapping[str, Any], *, preface_y_cutoff: float = 360.0) -> bool:
    page, _x0, y0, _y1 = _field_pdf_anchor_bounds(field)
    if page < 5:
        return False
    if page == 5 and y0 < preface_y_cutoff:
        return False
    return True


def _protection_row_coordinate_key(field: Mapping[str, Any]) -> str:
    """同行数据格共用 page + 行心 y（不解析 placeholder 标签）。"""
    page, y0, y1 = _field_pdf_anchor_y_bounds(field)
    y_mid = round((y0 + y1) / 2.0, 1)
    return f"rp_p{page}_y{y_mid}"


def _protection_data_column_slot_keys() -> tuple[str, ...]:
    return tuple(
        key
        for key in _LAYOUT_COLUMN_BAND_KEYS
        if key not in ("point_id", "location_main", "location_sub", "location_full", "readings_merged")
    )


def _is_rp_measurement_data_cell(
    field: Mapping[str, Any],
    column_layout: Mapping[str, Any],
) -> bool:
    """第五章数据行内、检测点列右侧的读数/均值/报出格（列带未命中时仍纳入绑定）。"""
    if not _is_rp_table_data_row_field(field, preface_y_cutoff=float(column_layout.get("preface_y_cutoff") or 360.0)):
        return False
    x0, x1 = _field_pdf_x_bounds(field)
    cols = column_layout.get("columns") if isinstance(column_layout.get("columns"), dict) else {}
    for loc_key in ("location_full", "location_sub", "location_main"):
        band = cols.get(loc_key) or column_layout.get(loc_key)
        if isinstance(band, dict):
            loc_x1 = float(band.get("x1") or 0.0)
            if loc_x1 > 0 and x1 <= loc_x1 + 2.5:
                return False
    reading_start = column_layout.get("reading_start_x")
    if reading_start is None:
        loc_full = cols.get("location_full") or column_layout.get("location_full")
        if isinstance(loc_full, dict):
            reading_start = float(loc_full.get("x1") or 0.0)
    if reading_start is not None and x0 < float(reading_start) - 5.0:
        return False
    return True


def _dual_binding_assignment_complete(out: Mapping[str, Any]) -> bool:
    """双组表：每组三次读数 + 两个均值均已绑定。"""
    keys = (
        "reading_1",
        "reading_2",
        "reading_3",
        "reading_1_2",
        "reading_2_2",
        "reading_3_2",
        "mean_m",
        "mean_m_2",
    )
    return all(_norm(out.get(k) or "") for k in keys)


# 窄列双组表（胃肠机 / 口腔 CBCT 等）：10～11 列固定 x 带（分界取列心间隙中点，不依赖 id/label）。
# 实测列心约：225 / 269 / 317 / 360 / 420 / 471 / 516 / 568 / 632 / 690 / 747
_NARROW_DUAL_X_BANDS: tuple[tuple[str, float, float], ...] = (
    ("reading_1", 210.0, 247.0),
    ("reading_2", 247.0, 293.0),
    ("reading_3", 293.0, 338.0),
    ("mean_m", 338.0, 390.0),
    ("reading_1_2", 390.0, 445.0),
    ("reading_2_2", 445.0, 493.0),
    ("reading_3_2", 493.0, 542.0),
    ("mean_m_2", 542.0, 600.0),
    ("report_d", 600.0, 661.0),
    ("report_d_2", 661.0, 718.0),
    ("annual_dose_msv", 718.0, 820.0),
)


def _narrow_dual_slot_for_x(x: float) -> str:
    for slot, lo, hi in _NARROW_DUAL_X_BANDS:
        if lo <= x < hi:
            return slot
    return ""


def _field_rect_x0(field: Mapping[str, Any]) -> float:
    rect = field.get("rect") if isinstance(field.get("rect"), list) else None
    return float(rect[1]) if rect and len(rect) > 1 else _field_sort_x(field)


def _field_rect_y0(field: Mapping[str, Any]) -> float:
    rect = field.get("rect") if isinstance(field.get("rect"), list) else None
    return float(rect[2]) if rect and len(rect) > 2 else 0.0


def _field_rect_w(field: Mapping[str, Any]) -> float:
    rect = field.get("rect") if isinstance(field.get("rect"), list) else None
    return float(rect[3]) if rect and len(rect) > 3 else 0.0


def _field_rect_h(field: Mapping[str, Any]) -> float:
    rect = field.get("rect") if isinstance(field.get("rect"), list) else None
    return float(rect[4]) if rect and len(rect) > 4 else 0.0


def _field_y_mid(field: Mapping[str, Any]) -> float:
    return round(_field_rect_y0(field) + _field_rect_h(field) / 2.0, 1)


def snap_narrow_dual_complementary_row_fragments(
    fields: List[Any],
    *,
    max_dy: float = 5.0,
    min_dy: float = 2.0,
    min_union_slots: int = 8,
) -> int:
    """
    窄列双组防护表：同行测量格因坐标漂移拆成两簇时，按**列槽互补**合并回一行。

    判定只依赖：
    - 窄列 x 列带（读数/均值/报出）
    - 行心 y 聚类
    - 异常碎片特征（误标「本底」或列宽明显偏窄）

    不硬编码 pdfFieldId，也不按设备类型分支。胃肠机/动态 DR 等同版 JS115 可共用。
    """
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for field in fields or []:
        if not isinstance(field, dict) or not is_protection_pdf_field(field):
            continue
        page, _x0, _y0, _y1 = _field_pdf_anchor_bounds(field)
        if page < 5:
            continue
        if not _narrow_dual_slot_for_x(_field_rect_x0(field)):
            continue
        by_page.setdefault(int(page), []).append(field)

    snapped = 0
    for _page, meas in by_page.items():
        buckets: Dict[float, List[Dict[str, Any]]] = {}
        for field in meas:
            key = round(_field_y_mid(field) * 2) / 2.0
            buckets.setdefault(key, []).append(field)
        keys = sorted(buckets)
        clusters: List[Dict[str, Any]] = []
        used: set[float] = set()
        for i, key in enumerate(keys):
            if key in used:
                continue
            group = list(buckets[key])
            used.add(key)
            ym = sum(_field_y_mid(f) for f in group) / len(group)
            for key2 in keys[i + 1 :]:
                if key2 - ym <= 1.2:
                    group.extend(buckets[key2])
                    used.add(key2)
                    ym = sum(_field_y_mid(f) for f in group) / len(group)
                else:
                    break
            slots = {
                sl
                for f in group
                for sl in (_narrow_dual_slot_for_x(_field_rect_x0(f)),)
                if sl
            }
            clusters.append({"ym": round(ym, 1), "fields": group, "slots": slots})

        ref_clusters = [c for c in clusters if len(c["fields"]) >= 8] or clusters
        slot_x: Dict[str, List[float]] = {}
        slot_w: Dict[str, List[float]] = {}
        for cluster in ref_clusters:
            for field in cluster["fields"]:
                slot = _narrow_dual_slot_for_x(_field_rect_x0(field))
                if not slot:
                    continue
                slot_x.setdefault(slot, []).append(_field_rect_x0(field))
                slot_w.setdefault(slot, []).append(_field_rect_w(field))
        med_x = {s: sorted(vs)[len(vs) // 2] for s, vs in slot_x.items() if vs}
        med_w = {s: sorted(vs)[len(vs) // 2] for s, vs in slot_w.items() if vs}

        def _is_outlier(cluster: Mapping[str, Any]) -> bool:
            for field in cluster.get("fields") or []:
                if "本底" in _field_label_blob(field):
                    return True
                slot = _narrow_dual_slot_for_x(_field_rect_x0(field))
                if slot in med_w and med_w[slot] - _field_rect_w(field) >= 3.0:
                    return True
            return False

        def _quality(cluster: Mapping[str, Any]) -> int:
            score = 0
            for field in cluster.get("fields") or []:
                slot = _narrow_dual_slot_for_x(_field_rect_x0(field))
                if slot and slot in med_w and abs(_field_rect_w(field) - med_w[slot]) <= 1.0:
                    score += 2
                if "本底" in _field_label_blob(field):
                    score -= 3
                if slot in ("report_d", "report_d_2", "annual_dose_msv"):
                    score += 1
            return score

        merged: set[int] = set()
        for i in range(len(clusters)):
            if i in merged:
                continue
            for j in range(i + 1, len(clusters)):
                if j in merged:
                    continue
                left, right = clusters[i], clusters[j]
                dy = abs(float(left["ym"]) - float(right["ym"]))
                if dy < min_dy or dy > max_dy:
                    continue
                if left["slots"] & right["slots"]:
                    continue
                union = left["slots"] | right["slots"]
                if len(union) < min_union_slots:
                    continue
                if not (union & {"reading_1", "reading_2", "reading_3", "reading_1_2"}):
                    continue
                if not (union & {"mean_m", "mean_m_2", "report_d", "report_d_2"}):
                    continue
                if not (_is_outlier(left) or _is_outlier(right)):
                    continue
                if _quality(left) >= _quality(right):
                    target, source = left, right
                else:
                    target, source = right, left
                if not _is_outlier(source):
                    target, source = source, target
                target_fields = list(target.get("fields") or [])
                if not target_fields:
                    continue
                target_y0 = sorted(_field_rect_y0(f) for f in target_fields)[
                    len(target_fields) // 2
                ]
                for field in source.get("fields") or []:
                    slot = _narrow_dual_slot_for_x(_field_rect_x0(field))
                    rect = field.get("rect")
                    if not slot or not isinstance(rect, list) or len(rect) < 5:
                        continue
                    new_rect = list(rect)
                    new_rect[2] = round(float(target_y0), 2)
                    if slot in med_x:
                        new_rect[1] = round(float(med_x[slot]), 2)
                    if slot in med_w:
                        new_rect[3] = round(float(med_w[slot]), 2)
                    field["rect"] = new_rect
                    for key in ("id", "placeholder", "originalPlaceholder", "title", "label"):
                        val = _norm(field.get(key) or "")
                        if "本底" not in val:
                            continue
                        pid = export_field_pdf_id(field)
                        field[key] = f"栏位{pid[1:]}" if pid.startswith("f") and pid[1:].isdigit() else "栏位"
                    snapped += 1
                merged.add(i)
                merged.add(j)
    return snapped


def _assign_dual_group_slots_by_narrow_x(items: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    窄列双组表：只按同行格子的列几何分配槽位，不读 id / placeholder / label。

    - 满列（≥10）：从左到右按表列序 zip（读数×3+均值）×2 + 报出×2 [+年剂量]
    - 不满列：落入预置 x 列带；仍不看文字
    """
    ordered = sorted(items, key=lambda it: float(it.get("x0") or 0.0))
    if not ordered:
        return {}

    # 满行：列序即槽位（与 PDF 表头两组测量列一致）
    if len(ordered) >= 10:
        use = ordered[: len(_DUAL_GROUP_SLOT_KEYS)]
        out: Dict[str, str] = {}
        for slot, it in zip(_DUAL_GROUP_SLOT_KEYS, use):
            pid = _norm(it.get("pid") or "")
            if pid:
                out[slot] = pid
        return out

    # 缺格行：仅用 x 列带
    out = {}
    for it in ordered:
        pid = _norm(it.get("pid") or "")
        if not pid:
            continue
        x = float(it.get("x0") or 0.0)
        slot = _narrow_dual_slot_for_x(x)
        if not slot or slot not in _BINDING_SLOT_KEYS:
            continue
        if slot in out and out[slot] != pid:
            continue
        out[slot] = pid
    return out


def _assign_dual_group_slots_by_x(items: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    双组表按 x 从左到右：组1(读数×3+均值) + 组2(读数×3+均值) + 报出值。
    与窄列满行列序一致，不依赖 id/label。
    """
    ordered = sorted(items, key=lambda it: float(it.get("x0") or 0.0))
    out: Dict[str, str] = {}
    for slot, it in zip(_DUAL_GROUP_SLOT_KEYS, ordered):
        pid = _norm(it.get("pid") or "")
        if pid:
            out[slot] = pid
    return out


def _binding_item_column_role(item: Mapping[str, Any]) -> str:
    """行内格子的列角色：优先已写入的几何 slot/role，不再回落解析 label。"""
    role = _norm(item.get("role") or "")
    if role in (_COLUMN_READING, _COLUMN_MEAN, _COLUMN_REPORT, _COLUMN_ANNUAL_DOSE):
        return role
    slot = _norm(item.get("slot") or "")
    return _SLOT_TO_COLUMN_ROLE.get(slot, role)


def _assign_dual_group_slots_by_role(items: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    双组表按列角色分配槽位：读数/均值/报出分列归位，再按 x 在组内填入 reading_1…3。
    修正「仅 2 次读数 + 均值」或「均值被 x 排序塞进 reading_3」导致的错位。
    """
    ordered = sorted(items, key=lambda it: float(it.get("x0") or 0.0))
    readings: List[Dict[str, Any]] = []
    means: List[Dict[str, Any]] = []
    reports: List[Dict[str, Any]] = []
    annual_reports: List[Dict[str, Any]] = []
    for it in ordered:
        role = _binding_item_column_role(it)
        field = it.get("field")
        sem = it.get("sem") if isinstance(it.get("sem"), dict) else {}
        label_blob = ""
        if isinstance(field, dict):
            label_blob = f"{_field_display_label(field)} {field.get('id') or ''}"
        if role == _COLUMN_ANNUAL_DOSE or _is_annual_dose_field_label(label_blob):
            annual_reports.append(it)
        elif role == _COLUMN_MEAN:
            means.append(it)
        elif role == _COLUMN_REPORT:
            reports.append(it)
        else:
            readings.append(it)

    out: Dict[str, str] = {}
    if not readings and not means:
        return out

    split = max(1, len(readings) // 2) if readings else 0
    g1_readings = readings[:split]
    g2_readings = readings[split:]

    for slot, it in zip(_binding_reading_keys(group=1), g1_readings):
        pid = _norm(it.get("pid") or "")
        if pid:
            out[slot] = pid
    for slot, it in zip(_binding_reading_keys(group=2), g2_readings):
        pid = _norm(it.get("pid") or "")
        if pid:
            out[slot] = pid

    if means:
        pid = _norm(means[0].get("pid") or "")
        if pid:
            out[_binding_mean_key(group=1)] = pid
    if len(means) > 1:
        pid = _norm(means[-1].get("pid") or "")
        if pid:
            out[_binding_mean_key(group=2)] = pid

    report_items = list(reports)

    if report_items:
        pid = _norm(report_items[0].get("pid") or "")
        if pid:
            out[_binding_report_key(group=1)] = pid
    if len(report_items) > 1:
        pid = _norm(report_items[1].get("pid") or "")
        if pid:
            out[_binding_report_key(group=2)] = pid

    # 仅当列角色/标签明确为年剂量或估算 mSv 时绑定，不按位置硬推
    if annual_reports:
        pid = _norm(annual_reports[-1].get("pid") or "")
        if pid:
            out["annual_dose_msv"] = pid

    return out


def _assign_single_group_slots_by_x(items: List[Dict[str, Any]]) -> Dict[str, str]:
    ordered = sorted(items, key=lambda it: float(it.get("x0") or 0.0))
    slots = _SINGLE_GROUP_SLOT_KEYS + ("report_d_2",)
    out: Dict[str, str] = {}
    for slot, it in zip(slots, ordered):
        pid = _norm(it.get("pid") or "")
        if pid:
            out[slot] = pid
    # 仅标签明确为年剂量/估算 mSv 的栏位才绑定
    for it in reversed(ordered):
        field = it.get("field")
        label = _field_display_label(field) if isinstance(field, dict) else ""
        role = _binding_item_column_role(it)
        if role == _COLUMN_ANNUAL_DOSE or _is_annual_dose_field_label(label):
            pid = _norm(it.get("pid") or "")
            if pid:
                for key in list(out.keys()):
                    if _norm(out.get(key) or "") == pid:
                        out[key] = ""
                out["annual_dose_msv"] = pid
                break
    return out


def protection_data_column_slot(
    field: Mapping[str, Any],
    column_layout: Mapping[str, Any],
) -> str:
    """按 PDF x 坐标落入版式列带，返回 binding 槽位名（reading_1 / mean_m / …）。"""
    preface = float(column_layout.get("preface_y_cutoff") or 360.0)
    if not _is_rp_table_data_row_field(field, preface_y_cutoff=preface):
        return ""
    x0, x1 = _field_pdf_x_bounds(field)
    cols = column_layout.get("columns") if isinstance(column_layout.get("columns"), dict) else {}
    xc = (x0 + x1) / 2.0
    matches: list[tuple[float, str]] = []
    for key in _protection_data_column_slot_keys():
        band = cols.get(key)
        if not isinstance(band, dict):
            continue
        if not _x_band_contains(band, x0, x1, tol=2.5):
            continue
        band_center = (float(band.get("x0") or 0.0) + float(band.get("x1") or 0.0)) / 2.0
        matches.append((abs(xc - band_center), key))
    if not matches:
        return ""
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def _column_role_from_semantics(sem: Mapping[str, Any], field: Mapping[str, Any]) -> str:
    col = _norm(sem.get("radiationColumn") or "")
    if col:
        return col
    if sem.get("meanOfReadings") or field.get("mean"):
        return _COLUMN_MEAN
    return ""


def _column_role_for_binding(
    field: Mapping[str, Any],
    sem: Mapping[str, Any],
    *,
    column_layout: Optional[Mapping[str, Any]] = None,
) -> str:
    """第五章 binding 列角色：仅由 PDF 列带 x 坐标决定，不解析 placeholder/label。"""
    del sem
    layout = column_layout if isinstance(column_layout, dict) else {}
    if not layout:
        layout = resolve_protection_table_layout([field])
    slot = protection_data_column_slot(field, layout)
    return _SLOT_TO_COLUMN_ROLE.get(slot, "")


def _derive_radiation_point_label(
    row_items: List[Dict[str, Any]],
    fields: List[Mapping[str, Any]],
    column_layout: Mapping[str, Any],
) -> str:
    """binding 行标识：page + 行心 y 坐标键（不用 label 拼接点位名）。"""
    del fields, column_layout
    if not row_items:
        return ""
    anchor = row_items[0].get("field")
    if isinstance(anchor, dict):
        return _protection_row_coordinate_key(anchor)
    return ""


def _is_rp_table_data_row_item(item: Mapping[str, Any], *, preface_y_cutoff: float) -> bool:
    page = int(item.get("page") or 0)
    if page < 5:
        return False
    y0 = float(item.get("y") or 0.0)
    if page == 5 and y0 < preface_y_cutoff:
        return False
    return True


def infer_protection_table_column_layout(
    items: List[Mapping[str, Any]],
    *,
    layout_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    推断第五章防护表列带：检测点位置主列/子列与首列读数左边界。
    优先用版式 JSON 的 table.columns；否则从 site_radiation_protection 栏位 x 坐标聚类。
    """
    named_cols = _layout_columns_from_json(layout_json) if isinstance(layout_json, dict) else {}
    if not named_cols and DEFAULT_LAYOUT_PATH.is_file():
        try:
            with DEFAULT_LAYOUT_PATH.open("r", encoding="utf-8") as f:
                named_cols = _layout_columns_from_json(json.load(f).get("layout") or {})
        except Exception:
            named_cols = {}

    rp_items = [
        it
        for it in items or []
        if isinstance(it, dict) and str(it.get("templateSectionKey") or "") == CHAPTER_KEY
    ]
    if not rp_items:
        return {"columns": named_cols}

    preface_y = 360.0
    data_items = [it for it in rp_items if _is_rp_table_data_row_item(it, preface_y_cutoff=preface_y)]
    narrow_x0: list[float] = []
    for it in data_items:
        x0, x1, _xc = _pdf_item_x_bounds(it)
        width = x1 - x0
        if width <= 0:
            continue
        if 18.0 <= width <= 72.0 and x0 >= 180.0:
            narrow_x0.append(x0)
    reading_start_x = min(narrow_x0) if narrow_x0 else None

    point_x1 = None
    for it in data_items:
        x0, x1, _xc = _pdf_item_x_bounds(it)
        if x0 <= 45.0 and (x1 - x0) <= 48.0:
            point_x1 = max(point_x1 or x1, x1)

    if reading_start_x is None and named_cols.get("reading_1"):
        reading_start_x = float(named_cols["reading_1"]["x0"])
    if point_x1 is None and named_cols.get("point_id"):
        point_x1 = float(named_cols["point_id"]["x1"])

    location_x0 = float(named_cols.get("location_main", {}).get("x0") or point_x1 or 65.0)
    if point_x1 is not None:
        location_x0 = max(location_x0, float(point_x1))

    layout_out: Dict[str, Any] = {
        "columns": dict(named_cols),
        "location_x0": location_x0,
        "reading_start_x": float(reading_start_x) if reading_start_x is not None else None,
        "preface_y_cutoff": preface_y,
    }
    if named_cols.get("location_main"):
        layout_out["location_main"] = dict(named_cols["location_main"])
    if named_cols.get("location_sub"):
        layout_out["location_sub"] = dict(named_cols["location_sub"])
    if layout_out.get("reading_start_x") is not None:
        layout_out["location_full"] = {
            "x0": location_x0,
            "x1": float(layout_out["reading_start_x"]),
        }
    return layout_out


def _x_band_contains(band: Mapping[str, Any], x0: float, x1: float, *, tol: float = 1.5) -> bool:
    bx0 = float(band.get("x0") or 0.0) - tol
    bx1 = float(band.get("x1") or 0.0) + tol
    return x0 >= bx0 and x1 <= bx1


def protection_field_column_role(
    item: Mapping[str, Any],
    column_layout: Optional[Mapping[str, Any]] = None,
    *,
    auto_semantic: Optional[Mapping[str, Any]] = None,
) -> str:
    sem = auto_semantic if isinstance(auto_semantic, dict) else {}
    if not sem:
        raw = item.get("autoSemantic")
        sem = raw if isinstance(raw, dict) else {}
    for key in ("radiationColumn", "typeName"):
        role = _norm(sem.get(key) or "")
        if role:
            return role
    layout = column_layout if isinstance(column_layout, dict) else {}
    if not _is_rp_table_data_row_item(item, preface_y_cutoff=float(layout.get("preface_y_cutoff") or 360.0)):
        return ""
    x0, x1, _xc = _pdf_item_x_bounds(item)
    cols = layout.get("columns") if isinstance(layout.get("columns"), dict) else {}
    for band_key, role in (
        ("location_main", "检测点位置"),
        ("location_sub", "位置细分"),
        ("location_full", "检测点位置"),
    ):
        band = cols.get(band_key) or layout.get(band_key)
        if isinstance(band, dict) and _x_band_contains(band, x0, x1):
            return role
    loc_full = layout.get("location_full")
    if isinstance(loc_full, dict) and _x_band_contains(loc_full, x0, x1):
        if cols.get("location_sub") and _x_band_contains(cols["location_sub"], x0, x1):
            return "位置细分"
        return "检测点位置"
    reading_start = layout.get("reading_start_x")
    location_x0 = layout.get("location_x0")
    if reading_start is not None and location_x0 is not None:
        if float(location_x0) - 1.5 <= x0 and x1 <= float(reading_start) + 1.5:
            sub_band = cols.get("location_sub") or layout.get("location_sub")
            if isinstance(sub_band, dict) and _x_band_contains(sub_band, x0, x1):
                return "位置细分"
            return "检测点位置"
    return ""


def is_protection_location_cell(
    item: Mapping[str, Any],
    column_layout: Optional[Mapping[str, Any]] = None,
    *,
    auto_semantic: Optional[Mapping[str, Any]] = None,
) -> bool:
    role = protection_field_column_role(item, column_layout, auto_semantic=auto_semantic)
    return role in _RP_LOCATION_COLUMN_ROLES


def apply_protection_field_export_typing(
    field_obj: Dict[str, Any],
    item: Mapping[str, Any],
    *,
    column_layout: Optional[Mapping[str, Any]] = None,
) -> None:
    """第五章导出：检测点位置列 text；均值 precision=3；报出值分档规则；其余数值 precision=2。"""
    if str(item.get("templateSectionKey") or "") != CHAPTER_KEY:
        return
    src = field_obj.get("source") if isinstance(field_obj.get("source"), dict) else {}
    auto_sem = src.get("autoSemantic") if isinstance(src.get("autoSemantic"), dict) else item.get("autoSemantic")
    role = protection_field_column_role(item, column_layout, auto_semantic=auto_sem if isinstance(auto_sem, dict) else None)
    if role in _RP_LOCATION_COLUMN_ROLES:
        field_obj["type"] = "text"
        field_obj.pop("precision", None)
        field_obj.pop("precisionRule", None)
        field_obj.pop("unit", None)
        if isinstance(src, dict):
            sem = src.setdefault("autoSemantic", {})
            if isinstance(sem, dict):
                sem.setdefault("radiationColumn", role)
                sem.setdefault("typeName", role)
        return
    ftype = str(field_obj.get("type") or "").lower()
    if ftype not in ("number", "computed"):
        return
    # 合成语义后再判定均值/报出（仅第五章）
    probe = dict(field_obj)
    probe["templateSectionKey"] = CHAPTER_KEY
    probe["sectionKey"] = CHAPTER_KEY
    if isinstance(auto_sem, dict) and auto_sem:
        src2 = probe.setdefault("source", {})
        if isinstance(src2, dict):
            src2["autoSemantic"] = auto_sem
    if role and not ((probe.get("source") or {}).get("autoSemantic") or {}).get("radiationColumn"):
        src2 = probe.setdefault("source", {})
        if isinstance(src2, dict):
            sem = src2.setdefault("autoSemantic", {})
            if isinstance(sem, dict):
                sem["radiationColumn"] = role
    apply_chapter5_decimal_precision_to_field(field_obj, probe_field=probe)


def field_is_protection_chapter_numeric_cell(field: Mapping[str, Any]) -> bool:
    """现场记录第五章数值格：读数/均值/报出值/本底/表前因子等（非序号、非位置文字）。"""
    if not _field_belongs_to_chapter5_table(field):
        return False
    if is_rp_table_seq_field(field):
        return False
    tbl = field.get("table") if isinstance(field.get("table"), dict) else {}
    col = _norm(tbl.get("radiationColumn") or tbl.get("typeName") or "")
    if col in _RP_LOCATION_COLUMN_ROLES:
        return False
    blob = _field_label_blob(field)
    if "检测点位置" in blob and not any(
        kw in blob
        for kw in (
            "测量读数",
            "报出值",
            "测量均值",
            "本底",
            "μSv",
            "Mbar",
            "M1",
            "M2",
            "M3",
            "校准",
            "修正",
            "因子",
        )
    ):
        return False
    return True


def _field_belongs_to_chapter5_table(field: Mapping[str, Any]) -> bool:
    """严格限定第五章「工作场所放射防护」表，排除质控等章节的同名「报出值/均值」。"""
    if not isinstance(field, Mapping):
        return False
    sk = _norm(field.get("templateSectionKey") or field.get("sectionKey") or "")
    if sk in _RP_SECTION_KEYS:
        return True
    if _norm(field.get("sectionType") or "") == "radiationProtection":
        return True
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    if _norm(src.get("sectionKey") or src.get("templateSectionKey") or "") in _RP_SECTION_KEYS:
        return True
    if is_protection_pdf_field(field):
        return True
    return False


def protection_numeric_cell_kind(field: Optional[Mapping[str, Any]]) -> str:
    """
    第五章表格数值格种类：mean / report / reading / other / ''（非第五章或不适用）。
    """
    if not isinstance(field, Mapping):
        return ""
    if not _field_belongs_to_chapter5_table(field):
        return ""
    if is_rp_table_seq_field(field):
        return ""
    sem = protection_field_semantic_unified(field)
    role = _column_role(sem, field)
    blob = _field_label_blob(field)
    if role == _COLUMN_ANNUAL_DOSE or _is_annual_dose_field_label(blob) or _is_annual_dose_field_label(role):
        return "other"
    if role in _RP_LOCATION_COLUMN_ROLES or role in ("检测点位置", "位置细分"):
        return ""
    if role == _COLUMN_MEAN or ("均值" in role and "年" not in role):
        return "mean"
    if role == _COLUMN_REPORT or ("报出" in role and "年" not in role):
        return "report"
    if role == _COLUMN_READING or role.startswith("测量读数") or role == "测量值":
        return "reading"
    if "均值" in blob or "Mbar" in blob or "M̄" in blob:
        return "mean"
    if "报出" in blob and not _is_annual_dose_field_label(blob):
        return "report"
    if "读数" in blob or ("测量值" in blob and "报出" not in blob):
        return "reading"
    return "other"


def resolve_protection_report_value_precision(num: float) -> int:
    """报出值分档：默认 |x|<10 → 2 位；10≤|x|<100 → 1 位；|x|≥100 → 整数（可热更新）。"""
    return _cfg_report_precision_for_magnitude(num)


def format_protection_report_value_display(value: Any) -> str:
    """第五章报出值（含 a~b 范围）按量级分档保留小数。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(int(value))
    text = str(value).strip()
    if not text or text == "/":
        return text
    if text.startswith("模拟_") or text.startswith("mock_"):
        return text
    if "~" in text:
        parts = re.split(r"\s*~\s*", text, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            left = format_protection_report_value_display(parts[0].strip())
            right = format_protection_report_value_display(parts[1].strip())
            if left and right:
                return f"{left}~{right}"
        return text
    try:
        num = float(text)
    except (TypeError, ValueError):
        return text
    if math.isnan(num) or math.isinf(num):
        return text
    prec = resolve_protection_report_value_precision(num)
    rounded = round(num, prec)
    # 跨档后再判定一次（如 9.996→10.00 应按 10–100 规则变为 10.0）
    prec2 = resolve_protection_report_value_precision(rounded)
    return format_protection_numeric_display(rounded, precision=prec2, fixed=True)


def apply_chapter5_decimal_precision_to_field(
    field_obj: Dict[str, Any],
    *,
    probe_field: Optional[Mapping[str, Any]] = None,
) -> bool:
    """
    给第五章均值/报出栏写入 precision / precisionRule。返回是否有改动。
    仅处理 number/computed；其它类型不动。位数取自热更新配置。
    """
    if not isinstance(field_obj, dict):
        return False
    if not _cfg_chapter5_enabled():
        return False
    ftype = str(field_obj.get("type") or "").lower()
    if ftype not in ("number", "computed", ""):
        # pdf.fields 常无 type，允许空 type
        if ftype:
            return False
    probe = probe_field if isinstance(probe_field, Mapping) else field_obj
    kind = protection_numeric_cell_kind(probe)
    if not kind:
        # pdf 无 type 时：仅当能识别为第五章 mean/report 才写
        return False
    mean_prec = _cfg_mean_precision()
    reading_prec = _cfg_reading_precision()
    report_cap = _cfg_report_precision_cap()
    cfg = _decimal_precision_cfg()
    report_tiered = True if cfg is None else bool(cfg.chapter5_report_tiered)
    changed = False
    if kind == "mean":
        if field_obj.get("precision") != mean_prec:
            field_obj["precision"] = mean_prec
            changed = True
        if "precisionRule" in field_obj:
            field_obj.pop("precisionRule", None)
            changed = True
        if not ftype:
            field_obj["type"] = "number"
            changed = True
    elif kind == "report":
        if field_obj.get("precision") != report_cap:
            field_obj["precision"] = report_cap
            changed = True
        if report_tiered:
            if field_obj.get("precisionRule") != PROTECTION_REPORT_PRECISION_RULE:
                field_obj["precisionRule"] = PROTECTION_REPORT_PRECISION_RULE
                changed = True
        elif "precisionRule" in field_obj:
            field_obj.pop("precisionRule", None)
            changed = True
        if not ftype:
            field_obj["type"] = "number"
            changed = True
    elif kind in ("reading", "other"):
        if ftype in ("number", "computed") or not ftype:
            target = reading_prec if kind == "reading" else _cfg_global_precision()
            if field_obj.get("precision") != target:
                field_obj["precision"] = target
                changed = True
            if "precisionRule" in field_obj:
                field_obj.pop("precisionRule", None)
                changed = True
    return changed


def resolve_field_number_precision(
    field: Optional[Mapping[str, Any]] = None,
    *,
    default: Optional[int] = None,
) -> int:
    """栏位 `precision` 优先；第五章按热更新规则；其余用全局默认。"""
    fallback = _cfg_global_precision() if default is None else max(0, int(default))
    if isinstance(field, Mapping):
        kind = protection_numeric_cell_kind(field)
        if kind == "mean" and _cfg_chapter5_enabled():
            if field.get("precision") not in (None, ""):
                try:
                    return max(0, int(field.get("precision")))
                except (TypeError, ValueError):
                    pass
            return _cfg_mean_precision()
        if field.get("precision") not in (None, ""):
            try:
                return max(0, int(field.get("precision")))
            except (TypeError, ValueError):
                pass
        if kind == "reading" and _cfg_chapter5_enabled():
            return _cfg_reading_precision()
        if kind == "report" and _cfg_chapter5_enabled():
            return _cfg_report_precision_cap()
        if kind == "other" and _cfg_chapter5_enabled():
            return _cfg_global_precision()
    return fallback


def format_protection_numeric_display(
    value: Any,
    *,
    precision: int = PROTECTION_NUMBER_PRECISION,
    fixed: bool = True,
) -> str:
    """
    数值展示：先按 precision 四舍五入，避免 float 尾数如 81.38000000000001。

    - fixed=True（第五章读数格默认）：固定写出小数位，如 0.120
    - fixed=False（一般公式）：小数位不超过 precision，去掉末尾 0，如 3.5 / -3.333
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(int(value))
    text = str(value).strip()
    if not text or text == "/":
        return text
    if text.startswith("模拟_") or text.startswith("mock_"):
        return text
    # 范围串留给 format_protection_report_value_display / format_protection_cell_display
    if "~" in text:
        return text
    try:
        num = float(text)
    except (TypeError, ValueError):
        return text
    if math.isnan(num) or math.isinf(num):
        return text
    prec = max(0, int(precision))
    if prec == 0:
        return str(int(round(num)))
    rounded = round(num, prec)
    if fixed:
        return f"{rounded:.{prec}f}"
    if abs(rounded - int(rounded)) < 1e-12:
        return str(int(rounded))
    return f"{rounded:.{prec}f}".rstrip("0").rstrip(".")


def format_protection_cell_display(
    value: Any,
    field: Optional[Mapping[str, Any]] = None,
    *,
    fixed: bool = True,
) -> str:
    """
    按第五章栏位种类格式化：均值/报出值规则取自热更新配置；其它用全局或栏位 precision。
    非第五章栏位：按 resolve_field_number_precision + format_protection_numeric_display。
    """
    kind = protection_numeric_cell_kind(field) if isinstance(field, Mapping) else ""
    if kind == "mean" and _cfg_chapter5_enabled():
        return format_protection_numeric_display(
            value, precision=_cfg_mean_precision(), fixed=True
        )
    if kind == "report" and _cfg_chapter5_enabled():
        return format_protection_report_value_display(value)
    prec = resolve_field_number_precision(field if isinstance(field, Mapping) else None)
    use_fixed = bool(fixed)
    if isinstance(field, Mapping) and kind in ("reading", "other"):
        use_fixed = True
    return format_protection_numeric_display(value, precision=prec, fixed=use_fixed)


def _binding_has_dual_group(binding: Mapping[str, Any]) -> bool:
    return bool(_norm(binding.get("mean_m_2") or "") or _norm(binding.get("report_d_2") or ""))


def _header_role_counts_from_texts(texts: Mapping[Any, Any]) -> Dict[str, int]:
    """统计表头行中测量读数/均值/报出值列标题出现次数。"""
    counts = {"reading": 0, "mean": 0, "report": 0}
    for raw in texts.values():
        text = _norm(raw)
        if not text or "本底" in text:
            continue
        if _HEADER_REPORT_RE.search(text):
            counts["report"] += 1
            continue
        if _HEADER_MEAN_RE.search(text):
            counts["mean"] += 1
            continue
        if _HEADER_READING_RE.search(text):
            counts["reading"] += 1
    return counts


def _repeated_header_indicates_dual_group(counts: Mapping[str, int]) -> bool:
    """表头重复：测量均值或报出值（或测量读数块）出现 ≥2 次 → 双组表。"""
    if int(counts.get("mean") or 0) >= 2:
        return True
    if int(counts.get("report") or 0) >= 2:
        return True
    if int(counts.get("reading") or 0) >= 2:
        return True
    return False


def _is_protection_table_header_row(cells: Mapping[Any, Any]) -> bool:
    joined = "".join(_norm(v) for v in cells.values())
    if "序号" not in joined:
        return False
    return (
        "检测点位置" in joined
        or "检测点" in joined
        or "测量读数" in joined
        or "测量均值" in joined
        or "报出值" in joined
    )


def _protection_table_pages_from_fields(fields: List[Mapping[str, Any]]) -> List[int]:
    pages: set[int] = set()
    for field in fields or []:
        if not isinstance(field, dict) or not is_protection_unified_field(field):
            continue
        sem = protection_field_semantic_unified(field)
        role = _column_role(sem, field)
        if role not in (_COLUMN_READING, _COLUMN_MEAN, _COLUMN_REPORT):
            continue
        page, _x0, _y0 = _field_pdf_anchor_rect(field)
        if page >= 1:
            pages.add(int(page))
    return sorted(pages)


def detect_dual_group_from_layout_header_rows(
    layout: Optional[Mapping[str, Any]],
) -> Optional[bool]:
    """从版式 JSON 的 table_template.header_rows 读取表头重复。"""
    if not isinstance(layout, dict):
        return None
    table_tpl = layout.get("table_template")
    if not isinstance(table_tpl, dict):
        return None
    headers = table_tpl.get("header_rows")
    if not isinstance(headers, list):
        return None
    saw_header = False
    for row in headers:
        if not isinstance(row, dict) or row.get("kind") != "header":
            continue
        cells = row.get("cells")
        if not isinstance(cells, dict):
            continue
        saw_header = True
        counts = _header_role_counts_from_texts(cells)
        if _repeated_header_indicates_dual_group(counts):
            return True
        if int(counts.get("mean") or 0) == 1 and int(counts.get("report") or 0) == 1:
            return False
    return None if not saw_header else False


def _page_text_header_dual_group(page: Any) -> Optional[bool]:
    """从页面文本行扫描表头（表头列标题重复）。"""
    try:
        text = page.get_text("text")
    except Exception:
        return None
    saw = False
    for line in (text or "").splitlines():
        line = _norm(line)
        if "序号" not in line:
            continue
        if not (
            "测量读数" in line
            or "测量均值" in line
            or "报出值" in line
            or "检测点" in line
        ):
            continue
        counts = {
            "reading": len(_HEADER_READING_RE.findall(line)),
            "mean": len(_HEADER_MEAN_RE.findall(line)),
            "report": len(_HEADER_REPORT_RE.findall(line)),
        }
        saw = True
        if _repeated_header_indicates_dual_group(counts):
            return True
        if counts["mean"] == 1 and counts["report"] == 1:
            return False
    return None if not saw else False


def detect_dual_group_from_pdf_headers(
    pdf_path: str,
    *,
    pages: Optional[Sequence[int]] = None,
) -> Optional[bool]:
    """
    读取 PDF 表头行，根据列标题重复判定双组表。
    返回 True/False 表示已识别；None 表示无法解析（无 PDF 或未找到表头）。
    """
    path = Path(pdf_path)
    if not path.is_file():
        return None
    try:
        import fitz
    except Exception:
        return None
    try:
        from radiation_detection_report.extract_tabletest_layout import _table_rows
    except Exception:
        try:
            from extract_tabletest_layout import _table_rows
        except Exception:
            _table_rows = None

    doc = fitz.open(str(path))
    try:
        page_nos = list(pages or range(1, len(doc) + 1))
        for pno in page_nos:
            if pno < 1 or pno > len(doc):
                continue
            page = doc[pno - 1]
            if _table_rows is not None:
                for row_cells in _table_rows(page):
                    texts: Dict[int, str] = {}
                    for ci, rect in row_cells:
                        try:
                            texts[ci] = _norm(page.get_text("text", clip=fitz.Rect(rect)))
                        except Exception:
                            texts[ci] = ""
                    if not _is_protection_table_header_row(texts):
                        continue
                    counts = _header_role_counts_from_texts(texts)
                    if _repeated_header_indicates_dual_group(counts):
                        return True
                    if int(counts.get("mean") or 0) == 1 and int(counts.get("report") or 0) == 1:
                        return False
            line_result = _page_text_header_dual_group(page)
            if line_result is not None:
                return line_result
    finally:
        doc.close()
    return None


def _resolve_template_pdf_path_from_payload(
    payload: Optional[Mapping[str, Any]],
) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    pdf = payload.get("pdf")
    if not isinstance(pdf, dict):
        return None
    src = pdf.get("source_pdf")
    if not isinstance(src, dict):
        return None
    tid = src.get("template_file_id")
    if tid is not None:
        try:
            from apps.core import pipeline_service
            from apps.core.models import LibraryFile

            lf = LibraryFile.objects.filter(pk=int(tid)).first()
            if lf is not None:
                path = pipeline_service.library_absolute_path(lf.relative_path)
                if path.is_file():
                    return str(path)
        except Exception:
            pass
    name = _norm(src.get("template_file_name") or "")
    if name:
        media_root = PACKAGE_DIR.parent / "media" / "file_library"
        for candidate in (
            media_root / "templates" / name,
            media_root / name,
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def _report_rules_indicate_dual_group(chapter: Optional[Mapping[str, Any]]) -> bool:
    """章节报出值公式含 {mean2} 时，表明模板按双组表配置。"""
    if not isinstance(chapter, dict):
        return False
    rules = chapter.get("reportValueRules")
    if not isinstance(rules, list):
        return False
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        expr = _norm(rule.get("expression") or rule.get("formula"))
        if "{mean2}" in expr or "__ROW_MEAN2__" in expr.lower():
            return True
    return False


def detect_dual_group_from_field_column_layout(
    fields: List[Mapping[str, Any]],
    *,
    min_dual_points: int = 3,
) -> Optional[bool]:
    """
    从已标注防护表栏位推断双组布局：同 radiationPoint 下出现 ≥2 个均值列。
    PDF/版式表头不可读时（如仅 JSON、无 source_pdf）的兜底。
    """
    point_items = _collect_point_data_fields(fields)
    if not point_items:
        return None
    dual_pts = 0
    single_pts = 0
    for items in point_items.values():
        n_mean = sum(1 for it in items if str(it.get("role") or "") == _COLUMN_MEAN)
        n_cells = len(items)
        if n_mean >= 2 or n_cells >= 8:
            dual_pts += 1
        elif n_mean == 1 or (4 <= n_cells < 8):
            single_pts += 1
    if dual_pts >= min_dual_points:
        return True
    if single_pts > 0 and dual_pts == 0:
        return False
    return None


def _sync_dual_mean_formula_in_chapter(
    chapter: Dict[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    *,
    dual_group: Optional[bool] = None,
) -> None:
    """按绑定结果同步 meanFormula.mode/groups，避免章节 JSON 仍写 groups:1 导致导出只算一组。"""
    if dual_group is None:
        dual_group = sum(
            1 for b in (bindings or []) if isinstance(b, dict) and _norm(b.get("mean_m_2") or "")
        ) >= 3
    mf = chapter.setdefault("meanFormula", {})
    if not isinstance(mf, dict):
        mf = dict(default_chapter_config()["meanFormula"])
        chapter["meanFormula"] = mf
    if dual_group:
        mf["mode"] = "per_row_avg_dual"
        mf["groups"] = 2
        mf["description"] = "双组测量：每组三次读数分别取平均；报出值分别对应各组均值"
        mf["layoutSource"] = mf.get("layoutSource") or "field_column_layout"
    elif str(mf.get("mode") or "") == "per_row_avg_dual":
        mf["mode"] = "per_row_avg"
        mf["groups"] = 1
        if not _norm(mf.get("description")):
            mf["description"] = default_chapter_config()["meanFormula"]["description"]
        mf.pop("layoutSource", None)


def resolve_table_dual_group_layout(
    fields: List[Mapping[str, Any]],
    *,
    pdf_path: Optional[str] = None,
    chapter: Optional[Mapping[str, Any]] = None,
    layout: Optional[Mapping[str, Any]] = None,
    template_payload: Optional[Mapping[str, Any]] = None,
) -> bool:
    """
    表级双组判定：读取表头列标题是否重复（非数据行列数启发式）。
    优先级：PDF 表头 > 版式 header_rows > 栏位列布局 > 章节已保存 meanFormula.groups。
    """
    pages = _protection_table_pages_from_fields(fields)
    path = _norm(pdf_path or "") or _resolve_template_pdf_path_from_payload(template_payload)
    if path:
        header_dual = detect_dual_group_from_pdf_headers(path, pages=pages or None)
        if header_dual is not None:
            return header_dual

    layout_dual = detect_dual_group_from_layout_header_rows(layout)
    if layout_dual is not None:
        return layout_dual

    field_dual = detect_dual_group_from_field_column_layout(fields)
    if field_dual is not None:
        return field_dual

    if _report_rules_indicate_dual_group(chapter):
        return True

    if isinstance(chapter, dict):
        mf = chapter.get("meanFormula")
        if isinstance(mf, dict):
            mode = str(mf.get("mode") or "").strip()
            try:
                groups = int(mf.get("groups") or 1)
            except (TypeError, ValueError):
                groups = 1
            if mode == "per_row_avg_dual" or groups >= 2:
                return True

    return False


def _collect_point_data_fields(
    fields: List[Mapping[str, Any]],
    *,
    column_layout: Optional[Mapping[str, Any]] = None,
    dual_group: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    """按 PDF 行坐标 + 列带 x 聚合读数/均值/报出格（不用 placeholder 标签拆行）。"""
    layout = column_layout if isinstance(column_layout, dict) and column_layout.get("columns") else None
    if layout is None:
        layout = resolve_protection_table_layout(fields)
    bg_geo = infer_background_geometry_from_fields(fields, column_layout=layout)
    bg_pids = {_norm(p).lower() for p in (bg_geo.get("pids") or []) if _norm(p)}
    bg_row_keys = {str(k) for k in (bg_geo.get("rowKeys") or []) if k}
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for field in fields or []:
        if not is_protection_unified_field(field):
            continue
        sem = protection_field_semantic_unified(field)
        pid = export_field_pdf_id(field)
        if not pid:
            continue
        if pid in bg_pids:
            continue
        pt = _protection_row_coordinate_key(field)
        if pt and pt in bg_row_keys:
            continue
        slot = protection_data_column_slot(field, layout)
        role = _SLOT_TO_COLUMN_ROLE.get(slot, "")
        # 双组：列角色只来自窄列 x 几何，禁止 id/label 覆盖（脏拼接 id 含「均值」会把读数当成均值）
        if dual_group:
            band_slot = _narrow_dual_slot_for_x(_field_sort_x(field))
            if band_slot:
                slot = band_slot
                role = _SLOT_TO_COLUMN_ROLE.get(band_slot, role)
        else:
            label_role = _column_role(sem, field)
            if label_role in (_COLUMN_READING, _COLUMN_MEAN, _COLUMN_REPORT, _COLUMN_ANNUAL_DOSE):
                role = label_role
        if role not in (_COLUMN_READING, _COLUMN_MEAN, _COLUMN_REPORT, _COLUMN_ANNUAL_DOSE):
            if not _is_rp_measurement_data_cell(field, layout):
                continue
            role = _COLUMN_READING
            if dual_group:
                band_slot = _narrow_dual_slot_for_x(_field_sort_x(field))
                slot = band_slot if band_slot in _BINDING_SLOT_KEYS else ""
            else:
                slot = slot if slot in _SINGLE_GROUP_SLOT_KEYS else ""
        if not pt:
            continue
        rows.setdefault(pt, []).append(
            {
                "field": field,
                "sem": sem,
                "pid": pid,
                "role": role,
                "slot": slot,
                "x0": _field_sort_x(field),
            }
        )
    return rows

def _assign_slots_to_point_row(
    items: List[Dict[str, Any]],
    *,
    dual_group: bool = False,
) -> Dict[str, str]:
    """双组表按列几何分配；单组表优先列带槽位，不完整时按 x 排序。"""
    if not items:
        return {}

    if dual_group:
        # 仅几何：满行列序 / x 列带；不再走 id/label 角色分支
        geo_out = _assign_dual_group_slots_by_narrow_x(items)
        if geo_out.get("reading_1") and (
            geo_out.get("mean_m")
            or geo_out.get("reading_1_2")
            or geo_out.get("report_d")
        ):
            return geo_out
        return _assign_dual_group_slots_by_x(items)

    out: Dict[str, str] = {}
    for it in items or []:
        slot = _norm(it.get("slot") or "")
        pid = _norm(it.get("pid") or "")
        # 单组表只接受单组槽位，忽略双组 *_2 污染
        if slot and pid and slot in _SINGLE_GROUP_SLOT_KEYS and not out.get(slot):
            out[slot] = pid

    reading_n = sum(1 for k in ("reading_1", "reading_2", "reading_3") if out.get(k))
    if out.get("reading_1") and reading_n >= 2 and (out.get("mean_m") or out.get("report_d")):
        return out

    return _assign_single_group_slots_by_x(items)


def reading_anchor_field_for_binding(
    binding: Mapping[str, Any],
    fields_by_pid: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """首格读数可能为空，依次回退到其它读数/均值/报出值格。"""
    keys = (
        "reading_1",
        "reading_2",
        "reading_3",
        "reading_1_2",
        "reading_2_2",
        "reading_3_2",
        "mean_m",
        "mean_m_2",
        "report_d",
        "report_d_2",
        "annual_dose_msv",
    )
    for key in keys:
        pid = _norm(binding.get(key) or "").lower()
        field = fields_by_pid.get(pid)
        if isinstance(field, dict):
            return field
    return None


_RP_ROW_COORD_RE = re.compile(r"^rp_p(\d+)_y([\d.]+)$", re.I)
_RP_SUB_ROW_RE = re.compile(r"_r(\d+)(?:_(.+))?$", re.I)


def resolve_chapter5_layout_for_fields(
    fields: Sequence[Mapping[str, Any]],
    *,
    template_payload: Optional[Mapping[str, Any]] = None,
    chapter: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """第五章专用表结构：列带来自版式 JSON（table.columns），非模板编辑器 pdfFieldId。"""
    layout_json: Optional[Mapping[str, Any]] = None
    if isinstance(chapter, dict):
        nested = chapter.get("layout")
        layout_json = nested if isinstance(nested, dict) else chapter
    if layout_json is None and isinstance(template_payload, dict):
        ch = template_payload.get(SCHEMA_KEY)
        if isinstance(ch, dict):
            nested = ch.get("layout")
            layout_json = nested if isinstance(nested, dict) else None
    if layout_json is None and DEFAULT_LAYOUT_PATH.is_file():
        try:
            with DEFAULT_LAYOUT_PATH.open("r", encoding="utf-8") as f:
                root = json.load(f)
            layout_json = root.get("layout") if isinstance(root.get("layout"), dict) else root
        except Exception:
            layout_json = None
    result = resolve_protection_table_layout(list(fields or []), layout_json=layout_json)
    root = (
        layout_json.get("layout")
        if isinstance(layout_json, dict) and isinstance(layout_json.get("layout"), dict)
        else layout_json
    )
    if isinstance(root, dict):
        pag = root.get("pagination")
        if isinstance(pag, dict):
            result["pagination"] = dict(pag)
        table = root.get("table")
        if isinstance(table, dict):
            result["table"] = dict(table)
    return result


def _simple_row_height(column_layout: Mapping[str, Any]) -> float:
    table = column_layout.get("table")
    if isinstance(table, dict):
        rh = table.get("row_heights")
        if isinstance(rh, dict) and rh.get("simple") not in (None, ""):
            return float(rh["simple"])
    return 18.9


def _protection_data_row_y_min(page: int, column_layout: Mapping[str, Any]) -> float:
    """第五章防护表数据区行心 y 下限：首页表头以下，续页重复表头以下。"""
    table = column_layout.get("table") if isinstance(column_layout.get("table"), dict) else {}
    pag = column_layout.get("pagination") if isinstance(column_layout.get("pagination"), dict) else {}
    rh = table.get("row_heights") if isinstance(table.get("row_heights"), dict) else {}
    hdr_h = float(rh.get("header") or 20.69)
    hdr_n = int(table.get("header_row_count") or 2)

    if page == 5:
        table_top = float(
            pag.get("table_y_top_first_page")
            or table.get("y_top")
            or 101.6
        )
        table_data_y = table_top + hdr_n * hdr_h + 0.5
        preface = float(column_layout.get("preface_y_cutoff") or 360.0)
        # 版式表从 y~100 起时，360 是仪器/条件区误标，应以表头下沿为准
        if preface > table_data_y + 80.0:
            return table_data_y
        return max(table_data_y, preface)

    cont_hdr = float(pag.get("continuation_header_y") or 61.38)
    return cont_hdr + hdr_n * hdr_h + 0.5


def _seq_field_reaches_data_region(
    field: Mapping[str, Any],
    page: int,
    column_layout: Mapping[str, Any],
) -> bool:
    """序号格下沿进入数据区（续页表头合并格允许上口在表头区）。"""
    fp, _sy0, sy1 = _field_pdf_anchor_y_bounds(field)
    if fp != page:
        return False
    y_min = _protection_data_row_y_min(page, column_layout)
    return sy1 >= y_min - 1.0


def _point_id_field_usable_on_page(
    field: Mapping[str, Any],
    page: int,
    column_layout: Mapping[str, Any],
) -> bool:
    """序号格下沿进入数据区；第5页上口须在表头下沿以上。"""
    if not _seq_field_reaches_data_region(field, page, column_layout):
        return False
    _, sy0, _sy1 = _field_pdf_anchor_y_bounds(field)
    y_min = _protection_data_row_y_min(page, column_layout)
    return sy0 >= y_min - 1.0


def _point_id_field_is_writable_data_row(
    field: Mapping[str, Any],
    column_layout: Mapping[str, Any],
) -> bool:
    if not isinstance(field, dict) or is_background_level_pdf_field(field):
        return False
    if not _field_in_table_column(field, "point_id", column_layout):
        return False
    page, _y0, _y1 = _field_pdf_anchor_y_bounds(field)
    if page < 5:
        return False
    return _point_id_field_usable_on_page(field, page, column_layout)


def binding_table_row_anchor(binding: Mapping[str, Any]) -> tuple[int, float] | None:
    """binding 行锚点：radiationPoint 坐标键 rp_p{page}_y{y}（与第五章 fieldBindings 一致）。"""
    m = _RP_ROW_COORD_RE.match(_norm(binding.get("radiationPoint") or ""))
    if not m:
        return None
    try:
        return int(m.group(1)), float(m.group(2))
    except (TypeError, ValueError):
        return None


def _field_in_table_column(
    field: Mapping[str, Any],
    column_key: str,
    column_layout: Mapping[str, Any],
    *,
    tol: float = 3.0,
) -> bool:
    cols = column_layout.get("columns") if isinstance(column_layout.get("columns"), dict) else {}
    band = cols.get(column_key)
    if not isinstance(band, dict):
        return False
    x0, x1 = _field_pdf_x_bounds(field)
    return _x_band_contains(band, x0, x1, tol=tol)


def _seq_cell_geometry_key(field: Mapping[str, Any]) -> tuple[int, float, float, float, float]:
    page, ax0, ay0, ay1 = _field_pdf_anchor_bounds(field)
    x0, x1 = _field_pdf_x_bounds(field)
    return (
        int(page),
        round(float(x0), 1),
        round(float(x1), 1),
        round(float(ay0), 1),
        round(float(ay1), 1),
    )


def field_for_table_row_column(
    page: int,
    row_y: float,
    fields: Sequence[Mapping[str, Any]],
    column_layout: Mapping[str, Any],
    column_key: str,
    *,
    row_y_tolerance: float = 14.0,
) -> Mapping[str, Any] | None:
    """
    按第五章表列带 + 行心 y 定位栏位（不解析 序号_r* 标签、不依赖 binding.seq_no）。
    """
    y_min = _protection_data_row_y_min(page, column_layout)
    if page < 5 or row_y < y_min:
        return None
    cols = column_layout.get("columns") if isinstance(column_layout.get("columns"), dict) else {}
    if column_key not in cols:
        return None

    contained: list[Mapping[str, Any]] = []
    best: Mapping[str, Any] | None = None
    best_dist = 1e9
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if is_background_level_pdf_field(field):
            continue
        if not _field_in_table_column(field, column_key, column_layout):
            continue
        fp, sy0, sy1 = _field_pdf_anchor_y_bounds(field)
        if fp != page:
            continue
        if sy1 < y_min:
            continue
        if column_key == "point_id":
            if not _point_id_field_usable_on_page(field, fp, column_layout):
                continue
        if sy0 <= row_y <= sy1:
            if column_key == "point_id" and row_y < y_min:
                continue
            contained.append(field)
            continue
        dist = min(abs(row_y - sy0), abs(row_y - sy1))
        if dist < best_dist and dist <= row_y_tolerance:
            if column_key == "point_id" and row_y < y_min:
                continue
            if column_key == "point_id" and row_y < sy0 - 1.0:
                continue
            best_dist = dist
            best = field
    if contained:
        if column_key == "point_id":
            return min(
                contained,
                key=lambda f: (
                    _seq_field_anchor_height(f),
                    abs(_field_pdf_anchor_y_center(f)[1] - row_y),
                ),
            )
        return min(contained, key=lambda f: abs(_field_pdf_anchor_y_center(f)[1] - row_y))
    return best


def seq_field_for_binding_from_table_layout(
    binding: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
    column_layout: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    anchor = binding_table_row_anchor(binding)
    if not anchor:
        return None
    page, row_y = anchor
    return field_for_table_row_column(
        page, row_y, fields, column_layout, "point_id"
    )


def binding_row_has_location_sub(
    binding: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
    column_layout: Mapping[str, Any],
) -> bool:
    anchor = binding_table_row_anchor(binding)
    if not anchor:
        return False
    sub_field = field_for_table_row_column(
        anchor[0], anchor[1], fields, column_layout, "location_sub"
    )
    if not isinstance(sub_field, dict):
        return False
    label = _field_display_label(sub_field)
    return bool(label) and label not in ("检测点位置", "位置细分", "检测点", "/")


def binding_row_location_main_key(
    binding: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
    column_layout: Mapping[str, Any],
) -> str:
    anchor = binding_table_row_anchor(binding)
    if not anchor:
        return ""
    for col in ("location_main", "location_full"):
        main_field = field_for_table_row_column(
            anchor[0], anchor[1], fields, column_layout, col
        )
        if isinstance(main_field, dict):
            label = _field_display_label(main_field)
            if label and label not in ("检测点位置", "/"):
                return label
    return ""


def seq_field_for_binding_row(
    binding: Mapping[str, Any],
    fields: List[Mapping[str, Any]],
    fields_by_pid: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """兼容入口：按第五章表结构（列带 + 行坐标）定位序号格。"""
    del fields_by_pid
    column_layout = resolve_chapter5_layout_for_fields(fields)
    return seq_field_for_binding_from_table_layout(binding, fields, column_layout)


_RP_BINDING_G1_DATA_KEYS = ("reading_1", "reading_2", "reading_3", "mean_m", "report_d")
_RP_BINDING_G2_DATA_KEYS = ("reading_1_2", "reading_2_2", "reading_3_2", "mean_m_2", "report_d_2")


def binding_pdf_table_sort_key(binding: Mapping[str, Any]) -> tuple[int, float, str]:
    """防护表 binding 按 PDF 物理行（page + 行心 y）排序，勿用模板 row 序号。"""
    pt = _norm(binding.get("radiationPoint") or "")
    m = _RP_ROW_COORD_RE.match(pt)
    if m:
        try:
            return (int(m.group(1)), float(m.group(2)), pt)
        except (TypeError, ValueError):
            pass
    return (10**9, 0.0, pt)


def _payload_cell_value(payload: Mapping[str, Any], pid: str) -> str:
    pid = _norm(pid).lower()
    if not pid:
        return ""
    dd = payload.get("dynamicData") if isinstance(payload.get("dynamicData"), dict) else {}
    tr = payload.get("testResult") if isinstance(payload.get("testResult"), dict) else {}
    raw = dd.get(pid)
    if raw in (None, ""):
        raw = tr.get(pid)
    return _norm(raw)


def _write_payload_pid(payload: dict, pid: str, value: Any) -> None:
    pid = _norm(pid).lower()
    if not pid:
        return
    if not isinstance(payload.get("dynamicData"), dict):
        payload["dynamicData"] = {}
    if not isinstance(payload.get("testResult"), dict):
        payload["testResult"] = {}
    payload["dynamicData"][pid] = value
    payload["testResult"][pid] = value


def _binding_row_has_active_data(payload: Mapping[str, Any], binding: Mapping[str, Any]) -> bool:
    keys = _RP_BINDING_G1_DATA_KEYS + _RP_BINDING_G2_DATA_KEYS
    for key in keys:
        pid = _norm(binding.get(key) or "").lower()
        if not pid:
            continue
        val = _payload_cell_value(payload, pid)
        if val and val != "/":
            return True
    return False


def binding_is_detection_point_row(
    binding: Mapping[str, Any],
    fields_by_pid: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    column_layout: Mapping[str, Any] | None = None,
    preface_y_cutoff: float = 360.0,
) -> bool:
    """第五章检测点数据行：排除表前区、续页表头区、本底。"""
    del fields_by_pid
    pt = _norm(binding.get("radiationPoint") or "")
    if "本底" in pt or "序号本底" in pt:
        return False
    anchor = binding_table_row_anchor(binding)
    if anchor:
        page, y = anchor
        layout = column_layout if isinstance(column_layout, dict) else {"preface_y_cutoff": preface_y_cutoff}
        if page < 5 or y < _protection_data_row_y_min(page, layout):
            return False
        return True
    return False


def binding_qualifies_for_site_record_seq(
    binding: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
    column_layout: Mapping[str, Any],
) -> bool:
    """
    现场记录序号：数据行 + 可写序号格。
    第5页排除跨表前合并格（f244）；续页允许表头合并格（f438/f304）为数据行写号。
    """
    if not binding_is_detection_point_row(binding, column_layout=column_layout):
        return False
    anchor = binding_table_row_anchor(binding)
    if not anchor:
        return False
    page, row_y = anchor
    y_min = _protection_data_row_y_min(page, column_layout)
    if row_y < y_min:
        return False
    seq_field = field_for_table_row_column(
        page, row_y, fields, column_layout, "point_id"
    )
    if not isinstance(seq_field, dict):
        return False
    return _point_id_field_usable_on_page(seq_field, page, column_layout)


def active_rp_binding_indices(
    payload: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    fields: Sequence[Mapping[str, Any]] | None = None,
    *,
    column_layout: Mapping[str, Any] | None = None,
) -> set[int]:
    by_pid: Dict[str, Dict[str, Any]] = {}
    field_list = [f for f in fields or [] if isinstance(f, dict)]
    layout = column_layout
    if layout is None and field_list:
        layout = resolve_chapter5_layout_for_fields(field_list)
    for field in field_list:
        pid = export_field_pdf_id(field)
        if pid:
            by_pid[pid] = dict(field)
    if not by_pid:
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            for key in _RP_BINDING_G1_DATA_KEYS + _RP_BINDING_G2_DATA_KEYS + ("seq_no",):
                pid = _norm(binding.get(key) or "").lower()
                if pid:
                    by_pid.setdefault(pid, {"pdfFieldId": pid})
    return {
        bi
        for bi, binding in enumerate(bindings)
        if isinstance(binding, dict)
        and binding_qualifies_for_site_record_seq(binding, field_list, layout)
        and _binding_row_has_active_data(payload, binding)
    }


def _location_main_from_radiation_point(point: str) -> str:
    pt = _norm(point)
    m = re.match(r"^(.+?)_r\d+", pt, re.I)
    return m.group(1) if m else pt


def _has_sub_location_in_point(point: str) -> bool:
    m = _RP_SUB_ROW_RE.search(_norm(point))
    return bool(m and m.group(2))


def bindings_share_seq_merge(
    prev_binding: Mapping[str, Any],
    next_binding: Mapping[str, Any],
    *,
    fields: Sequence[Mapping[str, Any]],
    column_layout: Mapping[str, Any],
    fields_by_pid: Mapping[str, Mapping[str, Any]] | None = None,
) -> bool:
    """相邻两行是否属于同一跨行序号单元（同序号格几何 / 同 pdfFieldId / 同主位置子行）。"""
    del fields_by_pid
    a1 = binding_table_row_anchor(prev_binding)
    a2 = binding_table_row_anchor(next_binding)
    if not a1 or not a2:
        return False
    f1 = field_for_table_row_column(a1[0], a1[1], fields, column_layout, "point_id")
    f2 = field_for_table_row_column(a2[0], a2[1], fields, column_layout, "point_id")
    if isinstance(f1, dict) and isinstance(f2, dict):
        if _seq_cell_geometry_key(f1) == _seq_cell_geometry_key(f2):
            return True
        p1 = export_field_pdf_id(f1)
        p2 = export_field_pdf_id(f2)
        if p1 and p1 == p2:
            return True
    prev_pt = _norm(prev_binding.get("radiationPoint") or "")
    next_pt = _norm(next_binding.get("radiationPoint") or "")
    if not (_has_sub_location_in_point(prev_pt) and _has_sub_location_in_point(next_pt)):
        return False
    return _location_main_from_radiation_point(prev_pt) == _location_main_from_radiation_point(next_pt)


def bindings_may_use_seq_range(
    prev_binding: Mapping[str, Any],
    next_binding: Mapping[str, Any],
    *,
    fields: Sequence[Mapping[str, Any]],
    column_layout: Mapping[str, Any],
    fields_by_pid: Mapping[str, Mapping[str, Any]] | None = None,
) -> bool:
    """现场记录：相邻两行共用合并序号格时写 n~m（与 bindings_share_seq_merge 一致）。"""
    return bindings_share_seq_merge(
        prev_binding,
        next_binding,
        fields=fields,
        column_layout=column_layout,
        fields_by_pid=fields_by_pid,
    )


def normalize_site_record_rp_sequence(
    payload: dict,
    bindings: Sequence[Mapping[str, Any]],
    fields: Sequence[Mapping[str, Any]],
    *,
    active_indices: set[int] | None = None,
    template_payload: Optional[Mapping[str, Any]] = None,
) -> int:
    """
    现场记录防护表序号：仅对已填数据行按表顺序从 1 起编；跨行合并格写 n~m。
    未填行保持 /。单行点位（如工作人员操作位K）各写单号。
    报告导出勿调用（见 report_data_builder，单组/双组各用独立序号列）。
    """
    if not isinstance(payload, dict) or not bindings:
        return 0
    field_list = [f for f in fields or [] if isinstance(f, dict)]
    column_layout = resolve_chapter5_layout_for_fields(
        field_list, template_payload=template_payload
    )

    active = (
        active_indices
        if active_indices is not None
        else active_rp_binding_indices(payload, bindings, fields, column_layout=column_layout)
    )
    written = 0
    indexed = [(bi, b) for bi, b in enumerate(bindings) if isinstance(b, dict)]
    indexed.sort(key=lambda ib: binding_pdf_table_sort_key(ib[1]))

    for field in field_list:
        if not _point_id_field_is_writable_data_row(field, column_layout):
            continue
        pid = export_field_pdf_id(field)
        if pid:
            _write_payload_pid(payload, pid, "/")

    for _bi, binding in indexed:
        if binding_is_detection_point_row(binding, column_layout=column_layout):
            continue
        seq_field = seq_field_for_binding_from_table_layout(
            binding, field_list, column_layout
        )
        if isinstance(seq_field, dict):
            pid = export_field_pdf_id(seq_field)
            if pid:
                _write_payload_pid(payload, pid, "/")
                written += 1

    active_rows = [
        (bi, binding)
        for bi, binding in indexed
        if bi in active
        and binding_qualifies_for_site_record_seq(
            binding, field_list, column_layout
        )
    ]

    seq_no = 1
    i = 0
    while i < len(active_rows):
        group: list[tuple[int, Mapping[str, Any]]] = [active_rows[i]]
        j = i + 1
        while j < len(active_rows) and bindings_share_seq_merge(
            group[-1][1],
            active_rows[j][1],
            fields=field_list,
            column_layout=column_layout,
        ):
            group.append(active_rows[j])
            j += 1

        if len(group) > 1:
            seq_text = f"{seq_no}~{seq_no + len(group) - 1}"
            seq_no += len(group)
        else:
            seq_text = str(seq_no)
            seq_no += 1

        first_field = seq_field_for_binding_from_table_layout(
            group[0][1], field_list, column_layout
        )
        first_pid = export_field_pdf_id(first_field) if isinstance(first_field, dict) else ""
        if first_pid:
            _write_payload_pid(payload, first_pid, seq_text)
            written += 1
        if len(group) > 1:
            for _gbi, row_binding in group[1:]:
                extra_field = seq_field_for_binding_from_table_layout(
                    row_binding, field_list, column_layout
                )
                extra_pid = (
                    export_field_pdf_id(extra_field)
                    if isinstance(extra_field, dict)
                    else ""
                )
                if extra_pid and extra_pid != first_pid:
                    _write_payload_pid(payload, extra_pid, "/")
                    written += 1
        i = j

    return written


def attach_seq_no_to_bindings(
    bindings: List[Dict[str, Any]],
    fields: List[Mapping[str, Any]],
    *,
    column_layout: Optional[Mapping[str, Any]] = None,
) -> None:
    """为每行 fieldBindings 写入序号列 pdfFieldId（按第五章表 point_id 列带 + 行坐标）。"""
    if not bindings:
        return
    field_list = [f for f in fields or [] if isinstance(f, dict)]
    layout = column_layout if isinstance(column_layout, dict) else resolve_chapter5_layout_for_fields(field_list)
    for row in bindings:
        if not isinstance(row, dict):
            continue
        seq_field = seq_field_for_binding_from_table_layout(row, field_list, layout)
        if isinstance(seq_field, dict):
            pid = export_field_pdf_id(seq_field)
            if pid:
                row["seq_no"] = pid
        elif not _norm(row.get("seq_no") or ""):
            row.setdefault("seq_no", "")


def protection_field_semantic_unified(field: Mapping[str, Any]) -> Dict[str, Any]:
    """PDF 栏位或导出 steps 栏位上的防护表语义。"""
    sem = field_table_semantic(field)
    if sem:
        return sem
    if isinstance(field.get("table"), dict):
        return field["table"]
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    sem2 = src.get("autoSemantic")
    return sem2 if isinstance(sem2, dict) else {}


def export_field_pdf_id(field: Mapping[str, Any]) -> str:
    pid = field_pdf_field_id(field)
    if pid:
        return pid
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    alt = _norm(src.get("pdfFieldId") or field.get("id") or "")
    m = re.match(r"^f\d+$", alt, re.I)
    return alt.lower() if m else ""


def is_protection_unified_field(field: Mapping[str, Any]) -> bool:
    if is_protection_pdf_field(field):
        return True
    sem = protection_field_semantic_unified(field)
    if sem.get("tableId") or sem.get("radiationPoint") or sem.get("radiationColumn"):
        return True
    sk = _norm(field.get("templateSectionKey") or field.get("sectionKey") or "")
    return sk in _RP_SECTION_KEYS


def build_field_bindings_from_fields(
    fields: List[Mapping[str, Any]],
    *,
    dual_group: Optional[bool] = None,
    pdf_path: Optional[str] = None,
    chapter: Optional[Mapping[str, Any]] = None,
    layout: Optional[Mapping[str, Any]] = None,
    template_payload: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    按 PDF 表格行列坐标聚合第五章栏位，记录各列 pdfFieldId。
    行：page + 行心 y；列：版式列带 x（reading_1/2/3、mean_m、report_d）。
    均值公式由 mean_expression_for_binding 动态生成，不硬编码 f 号、不解析 label。
    """
    if dual_group is None:
        dual_group = resolve_table_dual_group_layout(
            fields,
            pdf_path=pdf_path,
            chapter=chapter,
            layout=layout,
            template_payload=template_payload,
        )
    col_layout = resolve_protection_table_layout(fields, layout_json=layout)
    point_items = _collect_point_data_fields(
        fields, column_layout=col_layout, dual_group=bool(dual_group)
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for pt, items in point_items.items():
        sem0 = items[0].get("sem") if items else {}
        row = rows.setdefault(
            pt,
            {
                "radiationPoint": _derive_radiation_point_label(items, fields, col_layout),
                "row": sem0.get("row") if isinstance(sem0, dict) else None,
                "seq_no": "",
                "reading_1": "",
                "reading_2": "",
                "reading_3": "",
                "mean_m": "",
                "reading_1_2": "",
                "reading_2_2": "",
                "reading_3_2": "",
                "mean_m_2": "",
                "report_d": "",
                "report_d_2": "",
                "annual_dose_msv": "",
                "remark": "",
            },
        )
        row.update(_assign_slots_to_point_row(items, dual_group=dual_group))
    out = list(rows.values())
    out.sort(key=binding_pdf_table_sort_key)
    attach_seq_no_to_bindings(out, fields, column_layout=col_layout)
    return out


def build_field_bindings_from_pdf_fields(
    fields: List[Mapping[str, Any]],
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    return build_field_bindings_from_fields(fields, **kwargs)


def export_chapter_config_for_frontend(chapter: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """前端 JSON 仅暴露章节公式配置，不含 fieldBindings（后端自行维护）。"""
    if not isinstance(chapter, dict):
        return default_chapter_config()
    out: Dict[str, Any] = {
        "chapterKey": _norm(chapter.get("chapterKey")) or CHAPTER_KEY,
        "meanFormula": copy.deepcopy(
            chapter.get("meanFormula") if isinstance(chapter.get("meanFormula"), dict) else default_chapter_config()["meanFormula"]
        ),
    }
    rules = chapter.get("reportValueRules")
    if isinstance(rules, list):
        out["reportValueRules"] = export_formula_rules_list(rules)
    else:
        out["reportValueRules"] = copy.deepcopy(default_chapter_config()["reportValueRules"])
    annual = chapter.get("annualDoseRules")
    if isinstance(annual, list):
        out["annualDoseRules"] = export_formula_rules_list(annual)
    else:
        out["annualDoseRules"] = []
    return out


def _chapter_rule_ids(rules: List[Mapping[str, Any]]) -> set[str]:
    return {
        _norm(r.get("id"))
        for r in normalize_rule_list(rules)
        if _norm(r.get("id"))
    }


def _field_rules_are_chapter_sourced(
    field_rules: Any,
    chapter_rules: List[Mapping[str, Any]],
) -> bool:
    """栏位上的条件公式是否与章节 reportValueRules 同源（导出曾写入栏位，非用户单格编辑）。"""
    if not isinstance(field_rules, list) or not field_rules:
        return False
    fr = {
        _norm(r.get("id"))
        for r in field_rules
        if isinstance(r, dict) and _norm(r.get("id"))
    }
    cr = _chapter_rule_ids(chapter_rules)
    return bool(fr) and fr == cr


def field_has_per_cell_formula_override(
    field: Mapping[str, Any],
    *,
    chapter_rules: Optional[List[Mapping[str, Any]]] = None,
) -> bool:
    """
    栏位是否被用户手动改过公式（``fieldFormulaUserOverride``）。

    仅该标记阻止章节均值/报出值/本底回写。旧逻辑把「已有 fieldExpression」
    也当成覆盖，导致章节公式改完后保存仍写不进编辑器栏位。
    ``chapter_rules`` 保留兼容参数。
    """
    del chapter_rules
    if not isinstance(field, dict):
        return False
    return field.get("fieldFormulaUserOverride") is True


def default_chapter_config() -> Dict[str, Any]:
    return {
        "chapterKey": CHAPTER_KEY,
        "meanFormula": {
            "mode": "per_row_avg",
            "description": "同点位三次测量读数取平均",
            "groups": 1,
        },
        "reportValueRules": [],
        "annualDoseRules": [],
        "fieldBindings": [],
    }


def build_chapter_state(
    fields: List[Mapping[str, Any]],
    existing: Optional[Mapping[str, Any]] = None,
    *,
    pdf_path: Optional[str] = None,
    layout: Optional[Mapping[str, Any]] = None,
    template_payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    dual_group = resolve_table_dual_group_layout(
        fields,
        pdf_path=pdf_path,
        chapter=existing,
        layout=layout,
        template_payload=template_payload,
    )
    state = copy.deepcopy(existing) if isinstance(existing, dict) else default_chapter_config()
    state["chapterKey"] = CHAPTER_KEY
    state["fieldBindings"] = build_field_bindings_from_fields(
        fields,
        dual_group=dual_group,
        pdf_path=pdf_path,
        chapter=existing,
        layout=layout,
        template_payload=template_payload,
    )
    state["backgroundBinding"] = resolve_background_row_binding(
        fields,
        chapter=existing,
        layout=layout,
        template_payload=template_payload,
    )
    state.setdefault("meanFormula", default_chapter_config()["meanFormula"])
    mf = state["meanFormula"]
    if not isinstance(mf, dict):
        mf = default_chapter_config()["meanFormula"]
        state["meanFormula"] = mf
    if dual_group:
        mf["mode"] = "per_row_avg_dual"
        mf["groups"] = 2
        mf["description"] = "双组测量：每组三次读数分别取平均；报出值分别对应各组均值"
        mf["layoutSource"] = "repeated_table_header"
    else:
        if str(mf.get("mode") or "") == "per_row_avg_dual":
            mf["mode"] = "per_row_avg"
        mf["groups"] = 1
        if not _norm(mf.get("description")):
            mf["description"] = default_chapter_config()["meanFormula"]["description"]
        mf.pop("layoutSource", None)
    rules = state.get("reportValueRules")
    if not isinstance(rules, list):
        state["reportValueRules"] = []
    else:
        state["reportValueRules"] = [
            r for r in rules
            if isinstance(r, dict) and (
                _norm(r.get("condition"))
                or _norm(r.get("expression") or r.get("formula"))
            )
        ]
    annual_rules = state.get("annualDoseRules")
    if not isinstance(annual_rules, list):
        state["annualDoseRules"] = []
    else:
        state["annualDoseRules"] = [
            r for r in annual_rules
            if isinstance(r, dict) and (
                _norm(r.get("condition"))
                or _norm(r.get("expression") or r.get("formula"))
            )
        ]
    return state


def _substitute_row_tokens(
    expr: str,
    binding: Mapping[str, Any],
    *,
    report_group: int = 1,
) -> str:
    out = str(expr or "")
    mean_key = _binding_mean_key(group=report_group)
    report_key = _binding_report_key(group=report_group)
    repl = {
        ROW_TOKEN_MEAN: _norm(binding.get(mean_key) or binding.get("mean_m") or ""),
        ROW_TOKEN_MEAN2: _norm(binding.get("mean_m_2") or ""),
        "__ROW_MEAN__": _norm(binding.get(mean_key) or binding.get("mean_m") or ""),
        "__ROW_MEAN2__": _norm(binding.get("mean_m_2") or ""),
        "{mean}": _norm(binding.get(mean_key) or binding.get("mean_m") or ""),
        "{mean2}": _norm(binding.get("mean_m_2") or ""),
        "{report}": _norm(binding.get(report_key) or binding.get("report_d") or ""),
        "{report2}": _norm(binding.get("report_d_2") or ""),
        "__ROW_REPORT__": _norm(binding.get(report_key) or binding.get("report_d") or ""),
        "__ROW_REPORT2__": _norm(binding.get("report_d_2") or ""),
    }
    for idx, key in enumerate(_binding_reading_keys(group=1), start=1):
        repl[f"__ROW_READING{idx}__"] = _norm(binding.get(key) or "")
        repl[f"{{reading{idx}}}"] = _norm(binding.get(key) or "")
    for idx, key in enumerate(_binding_reading_keys(group=2), start=1):
        repl[f"__ROW_READING{idx}_2__"] = _norm(binding.get(key) or "")
        repl[f"{{reading{idx}_2}}"] = _norm(binding.get(key) or "")
    for token, fid in repl.items():
        if token and fid:
            out = out.replace(token, fid)
    return out.strip()


def compile_report_expression(
    rules: List[Mapping[str, Any]],
    binding: Mapping[str, Any],
    *,
    default_formula: str = "",
) -> str:
    """将含 condition 的章节规则编译为 if 嵌套；condition 为空的规则不写入此表达式（前端人工选公式）。"""
    substituter: Callable[[str], str] = lambda s: _substitute_row_tokens(s, binding)
    normalized = normalize_rule_list(rules)
    return compile_conditional_expression(
        normalized,
        substituter=substituter,
        default_expression=_substitute_row_tokens(default_formula, binding),
    )


def report_expression_for_binding(
    rules: List[Mapping[str, Any]],
    binding: Mapping[str, Any],
    *,
    report_group: int = 1,
) -> str:
    """章节报出值规则套用到绑定行：{mean}/{mean2} 替换为该行均值 f 号（如 f177*f984）。"""
    manual = manual_report_value_rules_for_binding(rules, binding, report_group=report_group)
    if not manual:
        return ""
    return _norm(manual[0].get("expression") or manual[0].get("formula") or "")


def chapter_report_rules_for_field_group(
    rules: List[Mapping[str, Any]],
    *,
    report_group: int = 1,
) -> List[Dict[str, Any]]:
    """章节报出值规则（保留 {mean}/{mean2} 占位），按报出列组筛选，供栏位 formulaRules 展示。"""
    manual_rows: List[Dict[str, Any]] = []
    for rule in normalize_rule_list(rules):
        cond = _norm(rule.get("condition"))
        expr = _norm(rule.get("expression") or rule.get("formula"))
        if cond or not expr:
            continue
        if report_group == 1 and "{mean2}" in expr and "{mean}" not in expr:
            continue
        if report_group == 2 and "{mean}" in expr and "{mean2}" not in expr and "mean2" not in expr.lower():
            continue
        row = export_rule_for_frontend(rule)
        if row.get("expression") or row.get("formula"):
            manual_rows.append(row)
    return manual_rows


def manual_report_value_rules_for_binding(
    rules: List[Mapping[str, Any]],
    binding: Mapping[str, Any],
    *,
    report_group: int = 1,
) -> List[Dict[str, Any]]:
    """condition 为空的章节报出值规则 → 栏位级列表（{mean}/{mean2} 已替换为该行均值 f 号，仅后端求值）。"""
    manual_rows: List[Dict[str, Any]] = []
    for rule in normalize_rule_list(rules):
        cond = _norm(rule.get("condition"))
        expr = _norm(rule.get("expression") or rule.get("formula"))
        if cond or not expr:
            continue
        if report_group == 1 and "{mean2}" in expr and "{mean}" not in expr:
            continue
        if report_group == 2 and "{mean}" in expr and "{mean2}" not in expr and "mean2" not in expr.lower():
            continue
        row = export_rule_for_frontend(rule)
        row["expression"] = _substitute_row_tokens(
            row.get("expression") or "",
            binding,
            report_group=report_group,
        )
        if row["expression"]:
            manual_rows.append(row)
    return manual_rows


def _write_manual_report_rules_to_field(
    report_field: Dict[str, Any],
    manual: List[Dict[str, Any]],
    *,
    auto_expr: str = "",
    chapter_rules: Optional[List[Mapping[str, Any]]] = None,
    force: bool = False,
) -> None:
    """
    章节 condition 为空的规则写入栏位：
    - 单条默认式 → fieldExpression（已替换为该行均值 f 号，如 f177*f984）；
    - 多条备选 → formulaRules（同样为行内 f 号，不含 {mean}/{mean2} 占位）。
    """
    if not force and field_has_per_cell_formula_override(report_field, chapter_rules=chapter_rules):
        return
    if not manual:
        return
    if force:
        report_field.pop("fieldFormulaUserOverride", None)
    if len(manual) == 1 and not auto_expr:
        _set_chapter_field_expression(
            report_field,
            manual[0].get("expression") or "",
            chapter_rules=chapter_rules,
            force=force,
        )
        report_field.pop("formulaRules", None)
        report_field.pop("fieldExpressionRules", None)
        return
    report_field["formulaRules"] = copy.deepcopy(manual)
    report_field.pop("fieldExpressionRules", None)
    if not auto_expr and (
        force or not _field_has_user_cell_formula(report_field, chapter_rules=chapter_rules)
    ):
        report_field.pop("fieldExpression", None)
        report_field.pop("pdfFieldExpression", None)
        report_field.pop("formula", None)
    if str(report_field.get("type") or "").lower() in ("", "number"):
        report_field["type"] = "computed"


def _binding_with_mean_fallback(
    binding: Mapping[str, Any],
    report_field: Mapping[str, Any],
    *,
    report_group: int = 1,
) -> Dict[str, Any]:
    """绑定行补全 mean_m / mean_m_2：优先 fieldBindings，其次栏位已有 chapterMeanPdfFieldId。"""
    out = dict(binding) if isinstance(binding, dict) else {}
    mean_key = _binding_mean_key(group=report_group)
    mean_pid = _norm(out.get(mean_key) or "") or _norm(report_field.get("chapterMeanPdfFieldId") or "")
    if mean_pid:
        out[mean_key] = mean_pid
    report_key = _binding_report_key(group=report_group)
    report_pid = _norm(out.get(report_key) or "") or export_field_pdf_id(report_field)
    if report_pid:
        out[report_key] = report_pid
    return out


def apply_chapter_report_rules_to_field(
    report_field: Dict[str, Any],
    rules: List[Mapping[str, Any]],
    binding: Mapping[str, Any],
    *,
    force: bool = False,
    report_group: int = 1,
    background_pids: Optional[set[str]] = None,
) -> None:
    """
    报出值栏位（steps / pdf.fields 均可）：
    - condition 非空 → fieldExpression（if 嵌套，自动判定，仅后端/导出求值）；
    - condition 为空 → fieldExpression 写行内 f 号公式（如 f177*f984），章节占位 {mean} 仅保留在 reportValueRules。
    force=True：编辑器「完成」或显式重套时覆盖已有栏位公式。
    """
    pid = export_field_pdf_id(report_field)
    if background_pids and pid and pid in background_pids:
        return
    if is_background_range_pdf_field(report_field):
        return
    # 用户单格覆盖始终保留；force 仅用于「完成」时清掉 override 后再写
    if field_has_per_cell_formula_override(report_field, chapter_rules=rules) and not force:
        return
    if force:
        report_field.pop("fieldFormulaUserOverride", None)
    binding = _binding_with_mean_fallback(binding, report_field, report_group=report_group)
    mean_pid = _norm(binding.get(_binding_mean_key(group=report_group)) or "")
    if mean_pid:
        report_field["chapterMeanPdfFieldId"] = mean_pid
    auto_expr = compile_report_expression(rules, binding)
    if auto_expr:
        _set_chapter_field_expression(
            report_field,
            auto_expr,
            chapter_rules=rules,
            force=force,
        )
    manual = manual_report_value_rules_for_binding(rules, binding, report_group=report_group)
    _write_manual_report_rules_to_field(
        report_field,
        manual,
        auto_expr=auto_expr,
        chapter_rules=rules,
        force=force,
    )


def apply_report_formulas_to_pdf_fields(
    fields: List[Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
) -> None:
    """保存坐标模板时：章节报出值公式写入各「报出值」pdf.fields 栏位。"""
    chapter = chapter if isinstance(chapter, dict) else {}
    rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
    if not rules:
        return
    field_list = [f for f in (fields or []) if isinstance(f, dict)]
    bg_pids = background_field_pids_from_geometry(field_list, chapter=chapter)
    bindings = chapter.get("fieldBindings") if isinstance(chapter.get("fieldBindings"), list) else None
    if not bindings:
        bindings = build_field_bindings_from_pdf_fields(fields, chapter=chapter)
    bindings = [
        b for b in (bindings or [])
        if isinstance(b, dict) and not _binding_contains_background_pids(b, bg_pids)
    ]
    by_pid = {
        export_field_pdf_id(field): field
        for field in field_list
        if export_field_pdf_id(field)
    }
    for binding in bindings or []:
        for group in (1, 2):
            report_pid = _norm(binding.get(_binding_report_key(group=group)) or "")
            if not report_pid or report_pid not in by_pid:
                continue
            if report_pid in bg_pids:
                continue
            report_field = by_pid[report_pid]
            apply_chapter_report_rules_to_field(
                report_field,
                rules,
                binding,
                report_group=group,
                background_pids=bg_pids,
            )


def apply_annual_dose_formulas_to_pdf_fields(
    fields: List[Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
) -> None:
    """保存坐标模板时：仅当存在 annual_dose_msv 绑定且配置了章节规则时写入。"""
    chapter = chapter if isinstance(chapter, dict) else {}
    rules = chapter.get("annualDoseRules") if isinstance(chapter.get("annualDoseRules"), list) else []
    if not rules:
        return
    bindings = chapter.get("fieldBindings") if isinstance(chapter.get("fieldBindings"), list) else None
    if not bindings:
        bindings = build_field_bindings_from_pdf_fields(fields, chapter=chapter)
    if not any(_norm((b or {}).get("annual_dose_msv") or "") for b in (bindings or []) if isinstance(b, dict)):
        return
    by_pid = {
        export_field_pdf_id(field): field
        for field in (fields or [])
        if isinstance(field, dict) and export_field_pdf_id(field)
    }
    for binding in bindings or []:
        if not isinstance(binding, dict):
            continue
        pt = _norm(binding.get("radiationPoint") or "")
        if "本底" in pt or "序号本底" in pt:
            continue
        annual_pid = _norm(binding.get("annual_dose_msv") or "")
        if not annual_pid or annual_pid not in by_pid:
            continue
        annual_field = by_pid[annual_pid]
        apply_chapter_report_rules_to_field(
            annual_field,
            rules,
            binding,
            report_group=1,
        )


def export_report_value_rules_for_frontend(
    rules: List[Mapping[str, Any]],
    binding: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = normalize_rule_list(rules)
    exported = export_formula_rules_list(normalized)
    for row, src in zip(exported, normalized):
        expr = _norm(src.get("expression"))
        if binding and expr:
            row["compiledExpression"] = _substitute_row_tokens(expr, binding)
    return {"reportValueRules": exported}


def mean_expression_for_binding(binding: Mapping[str, Any], *, group: int = 1) -> str:
    refs = [_norm(binding.get(k) or "") for k in _binding_reading_keys(group=group)]
    refs = [r for r in refs if r]
    if len(refs) >= 2:
        return f"avg({','.join(refs[:3])})"
    if len(refs) == 1:
        return refs[0]
    return ""


def _binding_contains_background_pids(binding: Mapping[str, Any], bg_pids: set[str]) -> bool:
    if not bg_pids or not isinstance(binding, dict):
        return False
    data_keys = (
        "reading_1", "reading_2", "reading_3",
        "reading_1_2", "reading_2_2", "reading_3_2",
        "mean_m", "mean_m_2",
        "report_d", "report_d_2",
        "annual_dose_msv",
    )
    for key in data_keys:
        pid = _norm(binding.get(key) or "").lower()
        if pid and pid in bg_pids:
            return True
    return False


def _clear_mistaken_avg_on_field(field: Dict[str, Any]) -> None:
    """清掉误套到本底格的 avg；强制忽略 fieldFormulaUserOverride。"""
    fe = _norm(field.get("fieldExpression") or field.get("formula") or field.get("pdfFieldExpression") or "")
    if not fe.lower().startswith("avg("):
        return
    field.pop("fieldExpression", None)
    field.pop("pdfFieldExpression", None)
    field.pop("formula", None)
    field.pop("fieldFormulaUserOverride", None)
    src = field.get("source") if isinstance(field.get("source"), dict) else None
    if isinstance(src, dict):
        src.pop("pdfFieldExpression", None)
        src.pop("formula", None)


def apply_mean_formulas_to_pdf_fields(
    fields: List[Any],
    *,
    chapter: Optional[Mapping[str, Any]] = None,
) -> None:
    """保存坐标模板时：按 field_bindings 将 per_row_avg 均值公式写入 pdf.fields 均值列。"""
    chapter = chapter if isinstance(chapter, dict) else {}
    mean_cfg = chapter.get("meanFormula") if isinstance(chapter.get("meanFormula"), dict) else {}
    mode = str(mean_cfg.get("mode") or "per_row_avg").strip()
    if mode not in ("per_row_avg", "per_row_avg_dual"):
        return
    field_list = [f for f in (fields or []) if isinstance(f, dict)]
    bg_pids = background_field_pids_from_geometry(field_list, chapter=chapter)
    bindings = chapter.get("fieldBindings") if isinstance(chapter.get("fieldBindings"), list) else None
    if not bindings:
        bindings = build_field_bindings_from_pdf_fields(fields, chapter=chapter)
    # 过滤：本底密排行不得出现在测量点 fieldBindings
    bindings = [
        b for b in (bindings or [])
        if isinstance(b, dict) and not _binding_contains_background_pids(b, bg_pids)
    ]
    by_pid = {
        export_field_pdf_id(field): field
        for field in field_list
        if export_field_pdf_id(field)
    }
    dual = mode == "per_row_avg_dual" or int(mean_cfg.get("groups") or 1) >= 2
    reading_keys = (
        ("reading_1", "reading_2", "reading_3", "reading_1_2", "reading_2_2", "reading_3_2")
        if dual
        else ("reading_1", "reading_2", "reading_3")
    )
    for binding in bindings or []:
        for rk in reading_keys:
            read_pid = _norm(binding.get(rk) or "")
            if not read_pid or read_pid not in by_pid:
                continue
            read_field = by_pid[read_pid]
            if read_pid in bg_pids:
                continue
            if field_has_per_cell_formula_override(read_field):
                continue
            read_field.pop("fieldExpression", None)
            read_field.pop("pdfFieldExpression", None)
    groups = (1, 2) if dual else (1,)
    for binding in bindings or []:
        for group in groups:
            mean_pid = _norm(binding.get(_binding_mean_key(group=group)) or "")
            if not mean_pid or mean_pid not in by_pid:
                continue
            mean_field = by_pid[mean_pid]
            if mean_pid in bg_pids:
                _clear_mistaken_avg_on_field(mean_field)
                continue
            # 防御：报出值/估算列绝不能被写成均值 avg 公式（绑定错位时曾发生）
            mean_label = f"{_field_display_label(mean_field)} {mean_field.get('id') or ''}"
            if "报出" in mean_label or _is_annual_dose_field_label(mean_label):
                continue
            if field_has_per_cell_formula_override(mean_field):
                continue
            expr = mean_expression_for_binding(binding, group=group)
            if expr:
                mean_field["fieldExpression"] = expr
            else:
                mean_field.pop("fieldExpression", None)
                mean_field.pop("pdfFieldExpression", None)

    # 兜底：凡本底格上误写的 avg，一律清除（保留 min~max 本底范围公式）
    report_pid = ""
    if isinstance(chapter.get("backgroundBinding"), dict):
        report_pid = _norm(chapter["backgroundBinding"].get("report_d") or "")
    if not report_pid:
        report_pid = _norm(infer_background_geometry_from_fields(field_list).get("report_d") or "")
    for field in field_list:
        pid = export_field_pdf_id(field)
        if not pid or pid not in bg_pids:
            continue
        _clear_mistaken_avg_on_field(field)
        fe = _norm(field.get("fieldExpression") or field.get("formula") or "")
        # 读数格上的空 override 会挡住后续清理/重算，去掉
        if pid != report_pid and not fe:
            field.pop("fieldFormulaUserOverride", None)

def iter_frontend_export_fields(payload: Mapping[str, Any]):
    """遍历前端导出 JSON 中的栏位（含 matrixTable 单元格）。"""
    steps = payload.get("steps")
    if not isinstance(steps, list):
        form_schema = payload.get("formSchema")
        if isinstance(form_schema, dict):
            steps = form_schema.get("steps")
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            for fld in sec.get("fields") or []:
                if isinstance(fld, dict):
                    yield fld
            matrix = sec.get("matrix")
            if not isinstance(matrix, dict):
                continue
            for hf in matrix.get("headerFields") or []:
                if isinstance(hf, dict):
                    yield hf
            for row in matrix.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells")
                if not isinstance(cells, dict):
                    continue
                for cell in cells.values():
                    if isinstance(cell, dict):
                        yield cell


def index_export_fields_by_pdf_field_id(payload: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for field in iter_frontend_export_fields(payload):
        pid = export_field_pdf_id(field)
        if pid:
            out[pid] = field
    return out


def _bindings_look_stale(
    bindings: List[Mapping[str, Any]],
    *,
    background_pids: Optional[set[str]] = None,
) -> bool:
    """旧版绑定缺序号列、radiationPoint 含栏位后缀、或混入本底密排格时，需从 pdf.fields 重算。"""
    stale_markers = ("_测量读数", "_测量均值", "_报出值")
    data_keys = (
        "reading_1",
        "reading_2",
        "reading_3",
        "reading_1_2",
        "reading_2_2",
        "reading_3_2",
        "mean_m",
        "mean_m_2",
        "report_d",
        "report_d_2",
    )
    bg = background_pids or set()
    for row in bindings or []:
        if not isinstance(row, dict):
            continue
        if bg and _binding_contains_background_pids(row, bg):
            return True
        pt = _norm(row.get("radiationPoint") or "")
        if any(marker in pt for marker in stale_markers):
            return True
        has_data = any(_norm(row.get(k) or "") for k in data_keys)
        if has_data and not _norm(row.get("seq_no") or ""):
            return True
    return False


def resolve_chapter_field_bindings(
    chapter: Optional[Mapping[str, Any]],
    *,
    pdf_fields: Optional[List[Any]] = None,
    export_payload: Optional[Mapping[str, Any]] = None,
    pdf_path: Optional[str] = None,
    layout: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """解析第五章 field_bindings：优先 pdf.fields 重算；章节内仅存且未过期时沿用。"""
    bg_pids: set[str] = set()
    if pdf_fields:
        bg_pids = background_field_pids_from_geometry(pdf_fields, chapter=chapter)
        built = build_field_bindings_from_pdf_fields(
            pdf_fields,
            chapter=chapter,
            pdf_path=pdf_path,
            layout=layout,
            template_payload=export_payload,
        )
        if built:
            if isinstance(chapter, dict):
                saved = chapter.get("fieldBindings")
                if isinstance(saved, list) and saved and not _bindings_look_stale(saved, background_pids=bg_pids):
                    # 模板内手工校正过的绑定（含 seq_no）优先于 pdf 坐标重算，
                    # 避免窄列双组版式重算时读数列错位覆盖已保存绑定。
                    return [dict(x) for x in saved if isinstance(x, dict)]
            return built
    if isinstance(chapter, dict):
        raw = chapter.get("fieldBindings")
        if isinstance(raw, list) and raw and not _bindings_look_stale(raw, background_pids=bg_pids):
            return [dict(x) for x in raw if isinstance(x, dict)]
    if export_payload is not None:
        return build_field_bindings_from_fields(
            list(iter_frontend_export_fields(export_payload)),
            chapter=chapter,
            pdf_path=pdf_path,
            layout=layout,
            template_payload=export_payload,
        )
    return []


def _field_has_user_cell_formula(
    field: Mapping[str, Any],
    *,
    chapter_rules: Optional[List[Mapping[str, Any]]] = None,
) -> bool:
    return field_has_per_cell_formula_override(field, chapter_rules=chapter_rules)


def _set_chapter_field_expression(
    field: Dict[str, Any],
    expr: str,
    *,
    chapter_rules: Optional[List[Mapping[str, Any]]] = None,
    force: bool = False,
) -> None:
    """写入 fieldExpression；非 force 时若栏位已有公式则跳过。"""
    expr = _norm(expr)
    if not expr:
        return
    if not force and _field_has_user_cell_formula(field, chapter_rules=chapter_rules):
        return
    field["fieldExpression"] = expr
    if force:
        field.pop("fieldFormulaUserOverride", None)


def resolve_chapter_config_for_export(
    chapter: Optional[Mapping[str, Any]] = None,
    *,
    pdf_fields: Optional[List[Any]] = None,
    export_payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    导出前端 JSON 兜底：合并模板已有章节配置与默认 meanFormula/reportValueRules，
    并从 pdf.fields 或 steps 反推 field_bindings（仅内部使用，不写入导出 JSON）。
    """
    base = copy.deepcopy(chapter) if isinstance(chapter, dict) else {}
    state: Dict[str, Any] = {**default_chapter_config(), **base}
    state["chapterKey"] = CHAPTER_KEY
    if not isinstance(state.get("meanFormula"), dict):
        state["meanFormula"] = default_chapter_config()["meanFormula"]
    rules = state.get("reportValueRules")
    if not isinstance(rules, list) or not rules:
        state["reportValueRules"] = default_chapter_config()["reportValueRules"]
    state["fieldBindings"] = resolve_chapter_field_bindings(
        state,
        pdf_fields=pdf_fields,
        export_payload=export_payload,
    )
    state["backgroundBinding"] = resolve_background_row_binding(
        pdf_fields or [],
        chapter=state,
        template_payload=export_payload,
    )
    _sync_dual_mean_formula_in_chapter(state, state.get("fieldBindings") or [])
    return state


def apply_chapter_formulas_by_pdf_field_bindings(
    payload: Dict[str, Any],
    *,
    bindings: List[Mapping[str, Any]],
    report_value_rules: Optional[List[Mapping[str, Any]]] = None,
    mean_mode: str = "per_row_avg",
) -> Dict[str, Any]:
    """按 field_bindings 的 pdfFieldId 将章节均值/报出值公式写入导出 JSON 各栏位。"""
    if not isinstance(payload, dict) or not bindings:
        return payload
    by_pid = index_export_fields_by_pdf_field_id(payload)
    rules = report_value_rules if isinstance(report_value_rules, list) else []
    dual_group = mean_mode == "per_row_avg_dual"

    for binding in bindings or []:
        if not isinstance(binding, dict):
            continue
        pt = str(binding.get("radiationPoint") or "")
        if "本底" in pt or "序号本底" in pt:
            continue
        groups = (1, 2) if dual_group else (1,)
        for group in groups:
            mean_pid = _norm(binding.get(_binding_mean_key(group=group)) or "")
            if mean_mode in ("per_row_avg", "per_row_avg_dual") and mean_pid and mean_pid in by_pid:
                mean_field = by_pid[mean_pid]
                if not field_has_per_cell_formula_override(mean_field):
                    expr = mean_expression_for_binding(binding, group=group)
                    if expr:
                        mean_field["fieldExpression"] = expr

            report_pid = _norm(binding.get(_binding_report_key(group=group)) or "")
            if not report_pid or report_pid not in by_pid:
                continue
            report_field = by_pid[report_pid]
            if is_background_range_pdf_field(report_field):
                continue
            apply_chapter_report_rules_to_field(
                report_field,
                rules,
                binding,
                report_group=group,
            )

    report_pids = {
        _norm(b.get(_binding_report_key(group=g)) or "")
        for b in bindings
        if isinstance(b, dict)
        for g in (1, 2)
    }
    # 兜底：绑定表未覆盖但 steps 上已有 chapterMeanPdfFieldId 的报出值栏位
    for pid, report_field in by_pid.items():
        if not isinstance(report_field, dict):
            continue
        if pid in report_pids:
            continue
        if is_background_range_pdf_field(report_field):
            continue
        mean_pid = _norm(report_field.get("chapterMeanPdfFieldId") or "")
        if not mean_pid:
            continue
        sem = protection_field_semantic_unified(report_field)
        role = _column_role(sem, report_field)
        label = _field_display_label(report_field)
        if role != _COLUMN_REPORT and "报出" not in label:
            continue
        apply_chapter_report_rules_to_field(
            report_field,
            rules,
            {"report_d": pid, "mean_m": mean_pid},
        )

    return payload


def apply_chapter_formulas_to_export_payload(
    payload: Dict[str, Any],
    chapter: Optional[Mapping[str, Any]] = None,
    *,
    pdf_fields: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    导出前端 JSON 收尾（兜底）：按 pdfFieldId 写入章节均值 avg(...) 与报出值公式。
    模板无 ``radiationProtectionChapter`` 时仍从 pdf.fields / steps 反推绑定；
    栏位已有单元格公式（fieldExpression / formula / formulaRules 等）时不覆盖。
    导出 JSON 仅保留 ``radiationProtectionChapter`` 前端可见配置（不含 fieldBindings）。
    """
    if not isinstance(payload, dict):
        return payload
    chapter_in = chapter if isinstance(chapter, dict) else payload.get(SCHEMA_KEY)
    chapter = resolve_chapter_config_for_export(
        chapter_in if isinstance(chapter_in, dict) else None,
        pdf_fields=pdf_fields,
        export_payload=payload,
    )
    bindings = chapter.get("fieldBindings") if isinstance(chapter.get("fieldBindings"), list) else []
    if not bindings:
        return payload
    mean_cfg = chapter.get("meanFormula") if isinstance(chapter.get("meanFormula"), dict) else {}
    mean_mode = str(mean_cfg.get("mode") or "per_row_avg").strip()
    rules = chapter.get("reportValueRules") if isinstance(chapter.get("reportValueRules"), list) else []
    apply_chapter_formulas_by_pdf_field_bindings(
        payload,
        bindings=bindings,
        report_value_rules=rules,
        mean_mode=mean_mode,
    )
    export_fields = list(index_export_fields_by_pdf_field_id(payload).values())
    if isinstance(pdf_fields, list):
        seen = {export_field_pdf_id(f) for f in export_fields if isinstance(f, dict)}
        for item in pdf_fields:
            if isinstance(item, dict) and export_field_pdf_id(item) and export_field_pdf_id(item) not in seen:
                export_fields.append(item)
    # 本底：仅按 pdf.fields 几何清读数格误写 avg；本底报出公式由人工配置，不再自动写因子
    apply_background_formulas_to_pdf_fields(
        export_fields,
        chapter=chapter,
        geometry_fields=pdf_fields if isinstance(pdf_fields, list) else None,
    )
    # 根级章节配置：优先保留模板已保存的 reportValueRules（含 {mean} 占位），勿用默认空规则覆盖
    src_rules = None
    if isinstance(chapter_in, dict) and isinstance(chapter_in.get("reportValueRules"), list):
        src_rules = chapter_in.get("reportValueRules")
    exported = export_chapter_config_for_frontend(chapter)
    if isinstance(src_rules, list) and src_rules:
        exported["reportValueRules"] = export_formula_rules_list(src_rules)
    payload[SCHEMA_KEY] = exported
    return payload


def bindings_by_point(bindings: List[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in bindings or []:
        if not isinstance(row, dict):
            continue
        pt = _norm(row.get("radiationPoint") or "")
        if pt:
            out[pt] = dict(row)
    return out


def apply_chapter_formulas_to_frontend_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """规则引擎 finalize 阶段：按 pdfFieldId 写入章节公式（无章节配置时亦兜底）。"""
    if not isinstance(payload, dict):
        return payload
    chapter = payload.get(SCHEMA_KEY) if isinstance(payload.get(SCHEMA_KEY), dict) else None
    pdf_fields = None
    pdf = payload.get("pdf")
    if isinstance(pdf, dict) and isinstance(pdf.get("fields"), list):
        pdf_fields = pdf.get("fields")
    return apply_chapter_formulas_to_export_payload(payload, chapter, pdf_fields=pdf_fields)


def sync_table_template_bindings(
    layout: Dict[str, Any],
    bindings: List[Mapping[str, Any]],
    *,
    chapter_config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """把编辑器 pdfFieldId 写入 radiation_detection_report 表格模板。"""
    out = copy.deepcopy(layout) if isinstance(layout, dict) else {}
    table_tpl = out.setdefault("table_template", {})
    table_tpl["field_bindings"] = copy.deepcopy(list(bindings or []))
    if isinstance(chapter_config, dict):
        table_tpl["chapter_formulas"] = {
            "meanFormula": copy.deepcopy(chapter_config.get("meanFormula") or {}),
            "reportValueRules": copy.deepcopy(chapter_config.get("reportValueRules") or []),
        }
    return out


def update_reference_layout_file(
    chapter_state: Mapping[str, Any],
    layout_path: Optional[Path] = None,
) -> Path:
    path = Path(layout_path or DEFAULT_LAYOUT_PATH)
    if path.is_file():
        with path.open("r", encoding="utf-8") as f:
            layout = json.load(f)
    else:
        layout = {"schema": "workplace_radiation_js009_chapter5/v1", "table_template": {}}
    merged = sync_table_template_bindings(
        layout,
        chapter_state.get("fieldBindings") or [],
        chapter_config=chapter_state,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return path


def _collect_chapter5_slot_pids(payload: Mapping[str, Any]) -> Dict[str, set]:
    """从 radiationProtectionChapter.fieldBindings / backgroundBinding 收集均值与报出值 f 号。"""
    mean_pids: set = set()
    report_pids: set = set()
    fs = payload.get("formSchema") if isinstance(payload.get("formSchema"), dict) else {}
    chapter = fs.get(SCHEMA_KEY) if isinstance(fs.get(SCHEMA_KEY), dict) else {}
    if not chapter and isinstance(payload.get(SCHEMA_KEY), dict):
        chapter = payload.get(SCHEMA_KEY) or {}
    for binding in chapter.get("fieldBindings") or []:
        if not isinstance(binding, Mapping):
            continue
        for key in ("mean_m", "mean_m_2"):
            pid = _norm(binding.get(key) or "").lower()
            if _F_ID_RE.match(pid):
                mean_pids.add(pid)
        for key in ("report_d", "report_d_2"):
            pid = _norm(binding.get(key) or "").lower()
            if _F_ID_RE.match(pid):
                report_pids.add(pid)
    bb = chapter.get("backgroundBinding") if isinstance(chapter.get("backgroundBinding"), dict) else {}
    bg_report = _norm(bb.get("report_d") or "").lower()
    if _F_ID_RE.match(bg_report):
        report_pids.add(bg_report)
    return {"mean": mean_pids, "report": report_pids}


def _stamp_field_precision_by_kind(field_obj: Dict[str, Any], kind: str) -> bool:
    mean_prec = _cfg_mean_precision()
    report_cap = _cfg_report_precision_cap()
    cfg = _decimal_precision_cfg()
    report_tiered = True if cfg is None else bool(cfg.chapter5_report_tiered)
    if kind == "mean":
        changed = False
        if field_obj.get("precision") != mean_prec:
            field_obj["precision"] = mean_prec
            changed = True
        if field_obj.pop("precisionRule", None) is not None:
            changed = True
        return changed
    if kind == "report":
        changed = False
        if field_obj.get("precision") != report_cap:
            field_obj["precision"] = report_cap
            changed = True
        if report_tiered:
            if field_obj.get("precisionRule") != PROTECTION_REPORT_PRECISION_RULE:
                field_obj["precisionRule"] = PROTECTION_REPORT_PRECISION_RULE
                changed = True
        elif field_obj.pop("precisionRule", None) is not None:
            changed = True
        return changed
    return False


def apply_chapter5_decimal_precision_to_template_payload(
    payload: Dict[str, Any],
) -> Dict[str, int]:
    """
    就地更新模板 JSON：仅第五章工作场所放射防护表的测量均值 / 报出值栏位。
    位数与 precisionRule 取自热更新配置。
    返回统计 {mean, report, fields_touched}。
    """
    stats = {"mean": 0, "report": 0, "fields_touched": 0}
    if not isinstance(payload, dict):
        return stats
    if not _cfg_chapter5_enabled():
        return stats
    slots = _collect_chapter5_slot_pids(payload)
    mean_pids = slots["mean"]
    report_pids = slots["report"]

    def _touch(field_obj: Dict[str, Any], *, force_kind: str = "") -> None:
        nonlocal stats
        if not isinstance(field_obj, dict):
            return
        pid = export_field_pdf_id(field_obj) or _norm(field_obj.get("id") or "").lower()
        kind = force_kind
        if not kind:
            if pid in mean_pids:
                kind = "mean"
            elif pid in report_pids:
                kind = "report"
            else:
                # 无 binding 时：仅当栏位已归属第五章再按语义推断
                probe = dict(field_obj)
                if not _norm(probe.get("templateSectionKey") or ""):
                    probe["templateSectionKey"] = CHAPTER_KEY
                kind = protection_numeric_cell_kind(probe)
                if kind not in ("mean", "report"):
                    return
        if kind == "mean" and _stamp_field_precision_by_kind(field_obj, "mean"):
            stats["mean"] += 1
            stats["fields_touched"] += 1
        elif kind == "report" and _stamp_field_precision_by_kind(field_obj, "report"):
            stats["report"] += 1
            stats["fields_touched"] += 1

    pdf = payload.get("pdf") if isinstance(payload.get("pdf"), dict) else {}
    for field in pdf.get("fields") or []:
        if not isinstance(field, dict):
            continue
        if _norm(field.get("templateSectionKey") or "") not in _RP_SECTION_KEYS:
            # 仍可用 binding pid 命中
            pid = export_field_pdf_id(field) or _norm(field.get("id") or "").lower()
            if pid not in mean_pids and pid not in report_pids:
                continue
            force = "mean" if pid in mean_pids else "report"
            _touch(field, force_kind=force)
            continue
        _touch(field)

    fs = payload.get("formSchema") if isinstance(payload.get("formSchema"), dict) else None
    if isinstance(fs, dict):
        for step in fs.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for sec in step.get("sections") or []:
                if not isinstance(sec, dict):
                    continue
                sk = _norm(sec.get("sectionKey") or sec.get("key") or "")
                title = _norm(sec.get("title") or "")
                sec_is_ch5 = sk in _RP_SECTION_KEYS or (
                    "工作场所放射防护" in title and "仪器" not in title
                )
                for field in sec.get("fields") or []:
                    if not isinstance(field, dict):
                        continue
                    pid = export_field_pdf_id(field) or _norm(field.get("id") or "").lower()
                    if pid in mean_pids:
                        _touch(field, force_kind="mean")
                    elif pid in report_pids:
                        _touch(field, force_kind="report")
                    elif sec_is_ch5:
                        probe = dict(field)
                        probe.setdefault("templateSectionKey", CHAPTER_KEY)
                        probe.setdefault("sectionKey", CHAPTER_KEY)
                        kind = protection_numeric_cell_kind(probe)
                        if kind in ("mean", "report"):
                            _touch(field, force_kind=kind)
    return stats
