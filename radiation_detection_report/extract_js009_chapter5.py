"""
从 JXFS/JS-009 现场记录 PDF 提取第五章，并输出 field_record_data / report_data。
"""

from __future__ import annotations

import os
from typing import Any, Dict

from radiation_detection_report.extract_tabletest_chapter5 import extract_chapter5_full
from radiation_detection_report.report_data_builder import build_report_data_from_extracted


def extract_js009_chapter5(pdf_path: str) -> Dict[str, Any]:
    if not pdf_path or not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF 不存在: {pdf_path}")
    data = extract_chapter5_full(pdf_path)
    report_data = build_report_data_from_extracted(data)
    field_record_data = _to_field_record_data(data)
    return {
        "schema": "js009_chapter5_extract/v1",
        "source_pdf": os.path.basename(pdf_path),
        "chapter_range": data.get("chapter_range") or {},
        "preface": data.get("preface"),
        "points": _grouped_points_from_raw(data),
        "background": data.get("table_template", {}).get("background") if isinstance(data.get("table_template"), dict) else None,
        "notes": _notes_from_raw(data),
        "field_record_data": field_record_data,
        "report_data": report_data or {},
        "summary": data.get("summary") or {},
    }


def _notes_from_raw(data: Dict[str, Any]) -> list:
    tt = data.get("table_template")
    if isinstance(tt, dict) and isinstance(tt.get("notes"), dict):
        t = tt["notes"].get("text")
        if t:
            return [str(t)]
    raw = data.get("raw_rows") or []
    out = []
    for row in raw:
        if isinstance(row, dict) and row.get("kind") == "notes":
            t = str(row.get("text") or "").strip()
            if t:
                out.append(t)
    return out


def _grouped_points_from_raw(data: Dict[str, Any]) -> list:
    tt = data.get("table_template")
    if isinstance(tt, dict) and isinstance(tt.get("points"), list):
        return tt["points"]
    return []


def _to_field_record_data(data: Dict[str, Any]) -> Dict[str, Any]:
    preface = data.get("preface") if isinstance(data.get("preface"), dict) else {}
    instrument = ""
    condition = ""
    if isinstance(preface.get("instrument_info"), dict):
        instrument = str(preface["instrument_info"].get("text") or "")
    if isinstance(preface.get("condition"), dict):
        condition = str(preface["condition"].get("text") or "")
    tt = data.get("table_template") if isinstance(data.get("table_template"), dict) else {}
    return {
        "instrument_info": instrument,
        "condition": condition,
        "points": tt.get("points") or [],
        "background": tt.get("background"),
        "notes": _notes_from_raw(data),
    }
