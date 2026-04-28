"""
检测提交 JSON → 模板 placeholder 的声明式映射表。

新增一套映射：在 SUBMIT_PLACEHOLDER_MAPS 里增加 map_id → 规则列表，
并在提交或填充入口传入相同的 map_id（或请求体字段 placeholderMapId）。

单条规则（除 key 外）由 kind 决定：
- path: path=[...] 嵌套读取；可选 cast=str|round|eq|not|bool
- const: value=固定字符串
- 模板键 commissionNo（委托编号）、testnumber（受检编号）不在此表配置，由 PDF 填充时的派生映射写入（与任务/项目 API 一致）
- test_date: part=year|month|day；可选 from=testDate（默认，testResult.testDate）或 updatedAt（根字段 updatedAt ISO）
  来自 updatedAt 时：月、日为该日期的月、日；年为公历四位去掉前缀「202」（如 2026→6）；非 202 开头年份则取末两位
- concat: parts=[子规则, ...] 按顺序拼接为字符串
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID = "default"


def _nested_get(data: Any, path: list, default: str = ""):
    cur = data
    for key in path:
        if isinstance(key, int):
            if not isinstance(cur, list) or key < 0 or key >= len(cur):
                return default
            cur = cur[key]
            continue
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _str_val(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _fmt_round(v: Any, ndigits: int = 2) -> str:
    try:
        return str(round(float(v), ndigits))
    except (TypeError, ValueError):
        return ""


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if v is None:
        return False
    s = str(v).strip().lower()
    if s in ("", "0", "false", "no", "off", "none", "null", "nan"):
        return False
    return True


def _parse_source_test_date(source_data: dict) -> datetime | None:
    raw = _nested_get(source_data, ["testResult", "testDate"], "")
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_source_updated_at(source_data: dict) -> datetime | None:
    """解析提交 JSON 根字段 updatedAt（如 2026-04-20T17:08:17.696570）。"""
    raw = source_data.get("updatedAt")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _resolve_spec(
    source_data: dict,
    test_date: datetime | None,
    updated_at: datetime | None,
    spec: dict[str, Any],
) -> Any:
    kind = spec["kind"]
    if kind == "path":
        raw = _nested_get(source_data, spec["path"], "")
        c = spec.get("cast")
        if c == "str":
            return _str_val(raw)
        if c == "round":
            return _fmt_round(raw)
        if c == "eq":
            return raw == spec.get("value")
        if c == "not":
            return not _to_bool(raw)
        if c == "bool":
            return _to_bool(raw)
        return raw
    if kind == "const":
        return spec.get("value", "")
    if kind == "test_date":
        src = spec.get("from") or "testDate"
        if src == "updatedAt":
            dt = updated_at
            use_short_year = True
        elif src in ("testDate", ""):
            dt = test_date
            use_short_year = False
        else:
            raise ValueError(f"unknown test_date from: {src!r}")
        if not dt:
            return ""
        part = spec["part"]
        if part == "year":
            if use_short_year:
                ys = str(dt.year)
                if ys.startswith("202"):
                    return ys[3:]
                return f"{dt.year % 100:02d}"
            return str(dt.year)
        if part == "month":
            return str(dt.month)
        if part == "day":
            return str(dt.day)
        raise ValueError(f"unknown test_date part: {part!r}")
    if kind == "concat":
        pieces = []
        for p in spec.get("parts") or []:
            v = _resolve_spec(source_data, test_date, updated_at, p)
            pieces.append("" if v is None else str(v))
        return "".join(pieces)
    raise ValueError(f"unknown mapping kind: {kind!r}")


def resolve_mapping_rules(source_data: dict, rules: list[dict[str, Any]]) -> dict[str, Any]:
    test_date = _parse_source_test_date(source_data)
    updated_at = _parse_source_updated_at(source_data)
    out: dict[str, Any] = {}
    for row in (rules or []):
        if not isinstance(row, dict):
            continue
        key = row["key"]
        spec = {k: v for k, v in row.items() if k != "key"}
        out[key] = _resolve_spec(source_data, test_date, updated_at, spec)
    return out


def build_submit_value_mapping(source_data: dict, map_id: str = DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID) -> dict[str, Any]:
    rules = SUBMIT_PLACEHOLDER_MAPS[map_id]
    return resolve_mapping_rules(source_data, rules)


def build_dynamic_value_mapping(source_data: dict) -> dict[str, Any]:
    """
    动态载荷直连映射：
    - 直接读取 submit 顶层 dynamicData
    - key/value 原样进入 value_mapping
    - 用于新版本「前端 source.pdfFieldId -> submit.dynamicData -> 后端模板字段」直连回填
    """
    raw = source_data.get("dynamicData")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        out[key] = v
    return out


# # 原 inspection_views._build_submit_value_mapping 的默认映射（逐条等价）
# _RULES_DEFAULT: list[dict[str, Any]] = [
#     {"key": "commissionNo", "kind": "path", "path": ["reportInfo", "commissionNo"]},
#     {"key": "testnumber", "kind": "path", "path": ["reportInfo", "reportNo"]},
#     {"key": "year", "kind": "test_date", "part": "year"},
#     {"key": "month", "kind": "test_date", "part": "month"},
#     {"key": "day", "kind": "test_date", "part": "day"},
#     {"key": "temperature", "kind": "path", "path": ["testResult", "temperature"]},
#     {"key": "RH","kind": "path", "path": ["testResult", "humidity"]},
#     {"key": "hospitalname", "kind": "path", "path": ["hospitalInfo", "name"]},
#     {"key": "commit_name", "kind": "const", "value": ""},
#     {"key": "hospital_address", "kind": "path", "path": ["hospitalInfo", "address"]},
#     {"key": "contact", "kind": "concat", "parts": [{"kind": "path", "path": ["hospitalInfo", "contactPerson"]}, {"kind": "path", "path": ["hospitalInfo", "contactPhone"]}]},
#     {"key": "model", "kind": "path", "path": ["equipmentInfo", "model"]},
#     {"key": "ratedkV", "kind": "const", "value": ""},
#     {"key": "ratedmA", "kind": "const", "value": ""},
#     {"key": "deviceName", "kind": "path", "path": ["equipmentInfo", "deviceName"]},
#     {"key": "serialNo", "kind": "path", "path": ["equipmentInfo", "serialNo"]},
#     {"key": "location", "kind": "path", "path": ["equipmentInfo", "location"]},
#     {"key": "manufacturer", "kind": "path", "path": ["equipmentInfo", "manufacturer"]},
#     {"key": "kerma_kv", "kind": "path", "path": ["testResult", "kermaTypical", "kv"], "cast": "str"},
#     {"key": "kerma_ma", "kind": "path", "path": ["testResult", "kermaTypical", "ma"], "cast": "str"},
#     {"key": "kerma_calc", "kind": "path", "path": ["testResult", "kermaTypical", "calcValue"]},
#     {"key": "kerma_report", "kind": "path", "path": ["testResult", "kermaTypical", "reportValue"]},
#     {"key": "kerma_size", "kind": "path", "path": ["testResult", "kermaTypical", "size"], "cast": "str"},
#     {"key": "kerma_nk", "kind": "path", "path": ["testResult", "kermaTypical", "nk"], "cast": "str"},
#     {"key": "kerma_kValue", "kind": "path", "path": ["testResult", "kermaTypical", "kValue"], "cast": "str"},
#     {"key": "kermaMaxNormal_kv", "kind": "path", "path": ["testResult", "kermaMaxNormal", "kv"], "cast": "str"},
#     {"key": "kermaMaxNormal_ma", "kind": "path", "path": ["testResult", "kermaMaxNormal", "ma"], "cast": "str"},
#     {"key": "kermaMaxNormal_calc", "kind": "path", "path": ["testResult", "kermaMaxNormal", "calcValue"]},
#     {"key": "kermaMaxNormal_report", "kind": "path", "path": ["testResult", "kermaMaxNormal", "reportValue"]},
#     {"key": "kermaMaxNormal_size", "kind": "path", "path": ["testResult", "kermaMaxNormal", "size"], "cast": "str"},
#     {"key": "kermaMaxNormal_nk", "kind": "path", "path": ["testResult", "kermaMaxNormal", "nk"], "cast": "str"},
#     {"key": "kermaMaxNormal_kValue", "kind": "path", "path": ["testResult", "kermaMaxNormal", "kValue"], "cast": "str"},
#     {"key": "kermaMaxHigh_kv", "kind": "path", "path": ["testResult", "kermaMaxHigh", "kv"], "cast": "str"},
#     {"key": "kermaMaxHigh_ma", "kind": "path", "path": ["testResult", "kermaMaxHigh", "ma"], "cast": "str"},
#     {"key": "kermaMaxHigh_calc", "kind": "path", "path": ["testResult", "kermaMaxHigh", "calcValue"]},
#     {"key": "kermaMaxHigh_report", "kind": "path", "path": ["testResult", "kermaMaxHigh", "reportValue"]},
#     {"key": "kermaMaxHigh_size", "kind": "path", "path": ["testResult", "kermaMaxHigh", "size"], "cast": "str"},
#     {"key": "kermaMaxHigh_nk", "kind": "path", "path": ["testResult", "kermaMaxHigh", "nk"], "cast": "str"},
#     {"key": "kermaMaxHigh_kValue", "kind": "path", "path": ["testResult", "kermaMaxHigh", "kValue"], "cast": "str"},
#     {"key": "highContrast_kv", "kind": "path", "path": ["testResult", "highContrast", "kv"], "cast": "str"},
#     {"key": "highContrast_ma", "kind": "path", "path": ["testResult", "highContrast", "ma"], "cast": "str"},
#     {"key": "highContrast_size", "kind": "path", "path": ["testResult", "highContrast", "size"], "cast": "str"},
#     {"key": "highContrast_result", "kind": "path", "path": ["testResult", "highContrast", "result"], "cast": "str"},
#     {
#         "key": "highContrast_accept",
#         "kind": "path",
#         "path": ["testResult", "highContrast", "acceptanceRequirement"],
#         "cast": "str",
#     },
#     {
#         "key": "highContrast_status",
#         "kind": "path",
#         "path": ["testResult", "highContrast", "statusRequirement"],
#         "cast": "str",
#     },
#     {"key": "lowContrast_kv", "kind": "path", "path": ["testResult", "lowContrast", "kv"], "cast": "str"},
#     {"key": "lowContrast_ma", "kind": "path", "path": ["testResult", "lowContrast", "ma"], "cast": "str"},
#     {"key": "lowContrast_result", "kind": "path", "path": ["testResult", "lowContrast", "result"], "cast": "str"},
#     {"key": "screenKerma_kv", "kind": "path", "path": ["testResult", "screenKerma", "kv"], "cast": "str"},
#     {"key": "screenKerma_ma", "kind": "path", "path": ["testResult", "screenKerma", "ma"], "cast": "str"},
#     {"key": "screenKerma_size", "kind": "path", "path": ["testResult", "screenKerma", "size"], "cast": "str"},
#     {"key": "screenKerma_d1", "kind": "path", "path": ["testResult", "screenKerma", "d1"], "cast": "str"},
#     {"key": "screenKerma_d2", "kind": "path", "path": ["testResult", "screenKerma", "d2"], "cast": "str"},
#     {"key": "screenKerma_nk", "kind": "path", "path": ["testResult", "screenKerma", "nk"], "cast": "str"},
#     {"key": "screenKerma_kValue", "kind": "path", "path": ["testResult", "screenKerma", "kValue"], "cast": "str"},
#     {"key": "screenKerma_result", "kind": "path", "path": ["testResult", "screenKerma", "calcValue"]},
#     {"key": "screenKerma_report", "kind": "path", "path": ["testResult", "screenKerma", "reportValue"]},
#     {"key": "screenKerma_standard", "kind": "path", "path": ["testResult", "screenKerma", "standardLimit"], "cast": "str"},
#     {"key": "abc_c1Readings_1", "kind": "path", "path": ["testResult", "abc", "c1Readings", 0], "cast": "str"},
#     {"key": "abc_c1Readings_2", "kind": "path", "path": ["testResult", "abc", "c1Readings", 1], "cast": "str"},
#     {"key": "abc_c1Readings_3", "kind": "path", "path": ["testResult", "abc", "c1Readings", 2], "cast": "str"},
#     {"key": "abc_c2Readings_1", "kind": "path", "path": ["testResult", "abc", "c2Readings", 0], "cast": "str"},
#     {"key": "abc_c2Readings_2", "kind": "path", "path": ["testResult", "abc", "c2Readings", 1], "cast": "str"},
#     {"key": "abc_c2Readings_3", "kind": "path", "path": ["testResult", "abc", "c2Readings", 2], "cast": "str"},
#     {"key": "abc_c1Average", "kind": "path", "path": ["testResult", "abc", "c1Average"], "cast": "round"},
#     {"key": "abc_c2Average", "kind": "path", "path": ["testResult", "abc", "c2Average"], "cast": "round"},
#     {"key": "abc_cAverage", "kind": "path", "path": ["testResult", "abc", "cAverage"], "cast": "str"},
#     {"key": "abc_calc", "kind": "path", "path": ["testResult", "abc", "calcValue"]},
#     {"key": "abc_report", "kind": "path", "path": ["testResult", "abc", "reportValue"]},
# ]

# 原文本映射 + 新增勾选框映射（完整版）
_RULES_DEFAULT: list[dict[str, Any]] = [
    # ==================== 原有文本字段映射（已完成） ====================
    # commissionNo / testnumber 由服务端 _build_submit_derived_value_mapping 注入（项目 API 编号与任务号，非上传 JSON）
    {"key": "year", "kind": "test_date", "from": "updatedAt", "part": "year"},
    {"key": "month", "kind": "test_date", "from": "updatedAt", "part": "month"},
    {"key": "day", "kind": "test_date", "from": "updatedAt", "part": "day"},
    {"key": "temperature", "kind": "path", "path": ["testResult", "temperature"]},
    {"key": "RH","kind": "path", "path": ["testResult", "humidity"]},
    {"key": "hospitalname", "kind": "path", "path": ["hospitalInfo", "name"]},
    {"key": "commit_name", "kind": "const", "value": ""},
    {"key": "hospital_address", "kind": "path", "path": ["hospitalInfo", "address"]},
    {"key": "contact", "kind": "concat", "parts": [
        {"kind": "path", "path": ["hospitalInfo", "contactPerson"]},
        {"kind": "path", "path": ["hospitalInfo", "contactPhone"]}
    ]},
    {"key": "contactPerson", "kind": "path", "path": ["hospitalInfo", "contactPerson"]},
    {"key": "contactPhone", "kind": "path", "path": ["hospitalInfo", "contactPhone"]},
    {"key": "model", "kind": "path", "path": ["equipmentInfo", "model"]},
    {"key": "ratedkV", "kind": "const", "value": ""},
    {"key": "ratedmA", "kind": "const", "value": ""},
    {"key": "deviceName", "kind": "path", "path": ["equipmentInfo", "deviceName"]},
    {"key": "serialNo", "kind": "path", "path": ["equipmentInfo", "serialNo"]},
    {"key": "location", "kind": "path", "path": ["equipmentInfo", "location"]},
    {"key": "manufacturer", "kind": "path", "path": ["equipmentInfo", "manufacturer"]},
    {"key": "ratedParams", "kind": "path", "path": ["equipmentInfo", "ratedParams"]},
    # 比释动能（常规）
    {"key": "kerma_kv", "kind": "path", "path": ["testResult", "kermaTypical", "kv"], "cast": "str"},
    {"key": "kerma_ma", "kind": "path", "path": ["testResult", "kermaTypical", "ma"], "cast": "str"},
    {"key": "kerma_calc", "kind": "path", "path": ["testResult", "kermaTypical", "calcValue"]},
    {"key": "kerma_report", "kind": "path", "path": ["testResult", "kermaTypical", "reportValue"]},
    {"key": "kerma_size", "kind": "path", "path": ["testResult", "kermaTypical", "size"], "cast": "str"},
    {"key": "kerma_nk", "kind": "path", "path": ["testResult", "kermaTypical", "nk"], "cast": "str"},
    {"key": "kerma_kValue", "kind": "path", "path": ["testResult", "kermaTypical", "kValue"], "cast": "str"},
    # 比释动能（最大常规）
    {"key": "kermaMaxNormal_kv", "kind": "path", "path": ["testResult", "kermaMaxNormal", "kv"], "cast": "str"},
    {"key": "kermaMaxNormal_ma", "kind": "path", "path": ["testResult", "kermaMaxNormal", "ma"], "cast": "str"},
    {"key": "kermaMaxNormal_calc", "kind": "path", "path": ["testResult", "kermaMaxNormal", "calcValue"]},
    {"key": "kermaMaxNormal_report", "kind": "path", "path": ["testResult", "kermaMaxNormal", "reportValue"]},
    {"key": "kermaMaxNormal_size", "kind": "path", "path": ["testResult", "kermaMaxNormal", "size"], "cast": "str"},
    {"key": "kermaMaxNormal_nk", "kind": "path", "path": ["testResult", "kermaMaxNormal", "nk"], "cast": "str"},
    {"key": "kermaMaxNormal_kValue", "kind": "path", "path": ["testResult", "kermaMaxNormal", "kValue"], "cast": "str"},
    # 比释动能（最大高剂量）
    {"key": "kermaMaxHigh_kv", "kind": "path", "path": ["testResult", "kermaMaxHigh", "kv"], "cast": "str"},
    {"key": "kermaMaxHigh_ma", "kind": "path", "path": ["testResult", "kermaMaxHigh", "ma"], "cast": "str"},
    {"key": "kermaMaxHigh_calc", "kind": "path", "path": ["testResult", "kermaMaxHigh", "calcValue"]},
    {"key": "kermaMaxHigh_report", "kind": "path", "path": ["testResult", "kermaMaxHigh", "reportValue"]},
    {"key": "kermaMaxHigh_size", "kind": "path", "path": ["testResult", "kermaMaxHigh", "size"], "cast": "str"},
    {"key": "kermaMaxHigh_nk", "kind": "path", "path": ["testResult", "kermaMaxHigh", "nk"], "cast": "str"},
    {"key": "kermaMaxHigh_kValue", "kind": "path", "path": ["testResult", "kermaMaxHigh", "kValue"], "cast": "str"},
    # 高对比度
    {"key": "highContrast_kv", "kind": "path", "path": ["testResult", "highContrast", "kv"], "cast": "str"},
    {"key": "highContrast_ma", "kind": "path", "path": ["testResult", "highContrast", "ma"], "cast": "str"},
    {"key": "highContrast_size", "kind": "path", "path": ["testResult", "highContrast", "size"], "cast": "str"},
    {"key": "highContrast_result", "kind": "path", "path": ["testResult", "highContrast", "result"], "cast": "str"},
    {
        "key": "highContrast_accept",
        "kind": "path",
        "path": ["testResult", "highContrast", "acceptanceRequirement"],
        "cast": "str",
    },
    {
        "key": "highContrast_status",
        "kind": "path",
        "path": ["testResult", "highContrast", "statusRequirement"],
        "cast": "str",
    },
    # 低对比度
    {"key": "lowContrast_kv", "kind": "path", "path": ["testResult", "lowContrast", "kv"], "cast": "str"},
    {"key": "lowContrast_ma", "kind": "path", "path": ["testResult", "lowContrast", "ma"], "cast": "str"},
    {"key": "lowContrast_result", "kind": "path", "path": ["testResult", "lowContrast", "measuredValue"], "cast": "str"},
    # 屏幕比释动能
    {"key": "screenKerma_kv", "kind": "path", "path": ["testResult", "screenKerma", "kv"], "cast": "str"},
    {"key": "screenKerma_ma", "kind": "path", "path": ["testResult", "screenKerma", "ma"], "cast": "str"},
    {"key": "screenKerma_size", "kind": "path", "path": ["testResult", "screenKerma", "size"], "cast": "str"},
    {"key": "screenKerma_d1", "kind": "path", "path": ["testResult", "screenKerma", "d1"], "cast": "str"},
    {"key": "screenKerma_d2", "kind": "path", "path": ["testResult", "screenKerma", "d2"], "cast": "str"},
    {"key": "screenKerma_nk", "kind": "path", "path": ["testResult", "screenKerma", "nk"], "cast": "str"},
    {"key": "screenKerma_kValue", "kind": "path", "path": ["testResult", "screenKerma", "kValue"], "cast": "str"},
    {"key": "screenKerma_result", "kind": "path", "path": ["testResult", "screenKerma", "measuredValue"], "cast": "str"},
    {"key": "screenKerma_report", "kind": "path", "path": ["testResult", "screenKerma", "reportValue"]},
    {"key": "screenKerma_standard", "kind": "path", "path": ["testResult", "screenKerma", "standardLimit"], "cast": "str"},
    # ABC
    {"key": "abc_c1Readings_1", "kind": "path", "path": ["testResult", "abc", "c1Readings", 0], "cast": "str"},
    {"key": "abc_c1Readings_2", "kind": "path", "path": ["testResult", "abc", "c1Readings", 1], "cast": "str"},
    {"key": "abc_c1Readings_3", "kind": "path", "path": ["testResult", "abc", "c1Readings", 2], "cast": "str"},
    {"key": "abc_c2Readings_1", "kind": "path", "path": ["testResult", "abc", "c2Readings", 0], "cast": "str"},
    {"key": "abc_c2Readings_2", "kind": "path", "path": ["testResult", "abc", "c2Readings", 1], "cast": "str"},
    {"key": "abc_c2Readings_3", "kind": "path", "path": ["testResult", "abc", "c2Readings", 2], "cast": "str"},
    {"key": "abc_c1Average", "kind": "path", "path": ["testResult", "abc", "c1Average"], "cast": "round"},
    {"key": "abc_c2Average", "kind": "path", "path": ["testResult", "abc", "c2Average"], "cast": "round"},
    {"key": "abc_cAverage", "kind": "path", "path": ["testResult", "abc", "cAverage"], "cast": "str"},
    {"key": "abc_calc", "kind": "path", "path": ["testResult", "abc", "calcValue"]},
    {"key": "abc_report", "kind": "path", "path": ["testResult", "abc", "reportValue"]},

    # ==================== ✅ 完整版新增：DSA 全字段映射（你要的就是这个） ====================
    # DSA - doseFirst 基础参数
    {"key": "dsa_doseFirst_kv", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "kv"], "cast": "str"},
    {"key": "dsa_doseFirst_ma", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "ma"], "cast": "str"},
    {"key": "dsa_doseFirst_time", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "time"], "cast": "str"},
    {"key": "dsa_doseFirst_cf", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "cf"], "cast": "str"},
    {"key": "dsa_doseFirst_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "reportValue"], "cast": "str"},

    # DSA - doseFirst 各部位测量值
    {"key": "dsa_doseFirst_foot_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 0, "measuredValue"], "cast": "str"},
    {"key": "dsa_doseFirst_leg_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 1, "measuredValue"], "cast": "str"},
    {"key": "dsa_doseFirst_abdomen_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 2, "measuredValue"], "cast": "str"},
    {"key": "dsa_doseFirst_chest_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 3, "measuredValue"], "cast": "str"},
    {"key": "dsa_doseFirst_head_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 4, "measuredValue"], "cast": "str"},
    # 与模板 *_result 占位符保持一致（结果 = measuredValue）
    {"key": "dsa_doseFirst_foot_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 0, "reportValue"], "cast": "str"},
    {"key": "dsa_doseFirst_leg_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 1, "reportValue"], "cast": "str"},
    {"key": "dsa_doseFirst_abdomen_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 2, "reportValue"], "cast": "str"},
    {"key": "dsa_doseFirst_chest_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 3, "reportValue"], "cast": "str"},
    {"key": "dsa_doseFirst_head_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "measurements", 4, "reportValue"], "cast": "str"},

    # DSA - doseFirst 报告值（头部为主）
    # {"key": "dsa_doseFirst_head_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseFirst", "reportValue"], "cast": "str"},

    # DSA - doseSecond 各部位测量值
    {"key": "dsa_doseSecond_foot_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 0, "measuredValue"], "cast": "str"},
    {"key": "dsa_doseSecond_leg_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 1, "measuredValue"], "cast": "str"},
    {"key": "dsa_doseSecond_abdomen_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 2, "measuredValue"], "cast": "str"},
    {"key": "dsa_doseSecond_chest_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 3, "measuredValue"], "cast": "str"},
    {"key": "dsa_doseSecond_head_measuredValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 4, "measuredValue"], "cast": "str"},
    # 与模板 *_result 占位符保持一致（结果 = measuredValue）
    {"key": "dsa_doseSecond_foot_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 0, "reportValue"], "cast": "str"},
    {"key": "dsa_doseSecond_leg_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 1, "reportValue"], "cast": "str"},
    {"key": "dsa_doseSecond_abdomen_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 2, "reportValue"], "cast": "str"},
    {"key": "dsa_doseSecond_chest_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 3, "reportValue"], "cast": "str"},
    {"key": "dsa_doseSecond_head_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "measurements", 4, "reportValue"], "cast": "str"},

    # DSA - doseSecond 报告值
    # {"key": "dsa_doseSecond_head_reportValue", "kind": "path", "path": ["testResult", "dsa", "doseSecond", "reportValue"], "cast": "str"},

    # DSA - dynamicRange
    {"key": "dsa_dynamicRange_kv", "kind": "path", "path": ["testResult", "dsa", "dynamicRange", "kv"], "cast": "str"},
    {"key": "dsa_dynamicRange_ma", "kind": "path", "path": ["testResult", "dsa", "dynamicRange", "ma"], "cast": "str"},
    {"key": "dsa_dynamicRange_fps", "kind": "path", "path": ["testResult", "dsa", "dynamicRange", "fps"], "cast": "str"},
    {"key": "dsa_grayscale04_measuredValue", "kind": "path", "path": ["testResult", "dsa", "dynamicRange", "result04mm"], "cast": "str"},

    # DSA - contrast
    {"key": "dsa_contrast_fps", "kind": "path", "path": ["testResult", "dsa", "contrast", "fps"], "cast": "str"},
    {"key": "dsa_grayscale02_measuredValue", "kind": "path", "path": ["testResult", "dsa", "contrast", "result02mm"], "cast": "str"},

    # DSA - artifact
    {"key": "dsa_artifact_fps", "kind": "path", "path": ["testResult", "dsa", "artifact", "fps"], "cast": "str"},   

    # ==================== 新增：勾选框（check类型）映射 ====================
    # 1. 医院类型
    {"key": "sameHospital", "kind": "const", "value": True},
    {"key": "diffHospital", "kind": "const", "value": False},
    # 2. 检测类型
    {"key": "isStatusTest", "kind": "path", "path": ["hospitalInfo", "testType"], "cast": "eq", "value": "status"},
    {"key": "isAcceptanceTest", "kind": "path", "path": ["hospitalInfo", "testType"], "cast": "eq", "value": "acceptance"},
    # 3. 测试项目勾选
    {"key": "isTest1", "kind": "const", "value": True},
    {"key": "isTest2", "kind": "const", "value": False},
    {"key": "isTest3", "kind": "const", "value": False},
    # 4. 检测仪器（无仪器，全不选）
    {"key": "hasInstrument1", "kind": "const", "value": False},
    {"key": "hasInstrument2", "kind": "const", "value": False},
    {"key": "hasInstrument3", "kind": "const", "value": False},
    {"key": "hasInstrument4", "kind": "const", "value": False},
    {"key": "hasInstrument5", "kind": "const", "value": False},
    # 5. 常规比释动能 控制模式/单位/模式
    {"key": "is_kerma_autocontrol", "kind": "path", "path": ["testResult", "kermaTypical", "controlMode"], "cast": "eq", "value": "自动控制"},
    {"key": "is_kerma_manualcontrol", "kind": "path", "path": ["testResult", "kermaTypical", "controlMode"], "cast": "eq", "value": "手动控制"},
    {"key": "is_kerma_normalmode", "kind": "const", "value": True},
    {"key": "is_kerma_unit1", "kind": "path", "path": ["testResult", "kermaTypical", "unit"], "cast": "eq", "value": "μGy/s"},
    {"key": "is_kerma_unit2", "kind": "path", "path": ["testResult", "kermaTypical", "unit"], "cast": "eq", "value": "μGy/min"},
    {"key": "is_kerma_unit3", "kind": "path", "path": ["testResult", "kermaTypical", "unit"], "cast": "eq", "value": "mGy/min"},
    # 6. 最大常规比释动能 单位/模式
    {"key": "is_kermaMaxNormal_unit1", "kind": "path", "path": ["testResult", "kermaMaxNormal", "unit"], "cast": "eq", "value": "μGy/s"},
    {"key": "is_kermaMaxNormal_unit2", "kind": "path", "path": ["testResult", "kermaMaxNormal", "unit"], "cast": "eq", "value": "μGy/min"},
    {"key": "is_kermaMaxNormal_unit3", "kind": "path", "path": ["testResult", "kermaMaxNormal", "unit"], "cast": "eq", "value": "mGy/min"},
    {"key": "is_kermaMaxNormal_normalmode", "kind": "const", "value": True},
    # 7. 最大高剂量比释动能 高剂量模式/单位
    {"key": "is_kermaMaxHigh_highdosemode", "kind": "path", "path": ["testResult", "kermaMaxHigh", "hasHighDoseMode"]},
    {"key": "isnot_kermaMaxHigh_highdosemode", "kind": "path", "path": ["testResult", "kermaMaxHigh", "hasHighDoseMode"], "cast": "not"},
    {"key": "is_kermaMaxHigh_unit1", "kind": "path", "path": ["testResult", "kermaMaxHigh", "unit"], "cast": "eq", "value": "μGy/s"},
    {"key": "is_kermaMaxHigh_unit2", "kind": "path", "path": ["testResult", "kermaMaxHigh", "unit"], "cast": "eq", "value": "μGy/min"},
    {"key": "is_kermaMaxHigh_unit3", "kind": "path", "path": ["testResult", "kermaMaxHigh", "unit"], "cast": "eq", "value": "mGy/min"},
    # 8. 高对比度 控制模式/铝片/结果状态
    {"key": "is_highContrast_autocontrol", "kind": "path", "path": ["testResult", "highContrast", "controlMode"], "cast": "eq", "value": "自动控制"},
    {"key": "is_highContrast_manualcontrol", "kind": "path", "path": ["testResult", "highContrast", "controlMode"], "cast": "eq", "value": "手动控制"},
    {"key": "is_highContrast_useAlumium", "kind": "path", "path": ["testResult", "highContrast", "useAluminum"]},
    {"key": "is_highContrast_status1", "kind": "path", "path": ["testResult", "highContrast", "acceptancePassed"]},
    {"key": "is_highContrast_status2", "kind": "path", "path": ["testResult", "highContrast", "statusPassed"]},
    # 9. 低对比度 控制模式
    {"key": "is_lowContrast_autocontrol", "kind": "path", "path": ["testResult", "lowContrast", "controlMode"], "cast": "eq", "value": "自动控制"},
    {"key": "is_lowContrast_manualcontrol", "kind": "path", "path": ["testResult", "lowContrast", "controlMode"], "cast": "eq", "value": "手动控制"},
    # 10. 屏幕比释动能 控制模式/滤线栅/单位
    {"key": "is_screenKerma_autocontrol", "kind": "path", "path": ["testResult", "screenKerma", "controlMode"], "cast": "eq", "value": "自动控制"},
    {"key": "is_screenKerma_manualcontrol", "kind": "path", "path": ["testResult", "screenKerma", "controlMode"], "cast": "eq", "value": "手动控制"},
    {"key": "is_screenKerma_grid", "kind": "path", "path": ["testResult", "screenKerma", "hasGrid"]},
    {"key": "isnot_screenKerma_grid", "kind": "path", "path": ["testResult", "screenKerma", "hasGrid"], "cast": "not"},
    {"key": "is_screenKerma_unit1", "kind": "path", "path": ["testResult", "screenKerma", "unit"], "cast": "eq", "value": "μGy/s"},
    {"key": "is_screenKerma_unit2", "kind": "path", "path": ["testResult", "screenKerma", "unit"], "cast": "eq", "value": "μGy/min"},
    {"key": "is_screenKerma_unit3", "kind": "path", "path": ["testResult", "screenKerma", "unit"], "cast": "eq", "value": "mGy/min"},
    # 11. ABC 有无ABC功能
    {"key": "is_abc_hasAbc", "kind": "path", "path": ["testResult", "abc", "hasAbc"]},
    {"key": "isnot_abc_hasAbc", "kind": "path", "path": ["testResult", "abc", "hasAbc"], "cast": "not"},
    # 12. DSA 设备类型/伪影
    {"key": "is_dsa", "kind": "path", "path": ["testResult", "isDsaDevice"]},
    {"key": "isnot_dsa", "kind": "path", "path": ["testResult", "isDsaDevice"], "cast": "not"},
    {"key": "is_dsa_artifact_present", "kind": "path", "path": ["testResult", "dsa", "artifact", "present"]},
    {"key": "isnot_dsa_artifact_present", "kind": "path", "path": ["testResult", "dsa", "artifact", "present"], "cast": "not"},
]

SUBMIT_PLACEHOLDER_MAPS: dict[str, list[dict[str, Any]]] = {
    DEFAULT_SUBMIT_PLACEHOLDER_MAP_ID: _RULES_DEFAULT,
}
