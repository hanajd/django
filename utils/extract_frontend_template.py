"""Extract frontend-focused form JSON from unified template JSON.

Usage:
  python3 utils/extract_frontend_template.py \
    --input "media/file_library/templates/your_template.json" \
    --output "tmp/frontend_template.json"

This script supports:
1) Unified template format (`schema = unified_form_template/v1`)
2) Legacy htmlpdf template format (`fields` only)

For legacy templates without `formSchema`, it will auto-generate a simple
`formSchema` from placeholders in PDF fields.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from utils.ollama_extract import generate_frontend_template_with_ollama
from utils.unified_template_fields import frontend_rect_from_materialized, materialize_unified_pdf_fields


def _load_json(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"fields": data}
    raise ValueError("Unsupported JSON root type")


def _resolve_pdf_fields(template_obj: dict[str, Any]) -> list[dict[str, Any]]:
    pdf = template_obj.get("pdf")
    if isinstance(pdf, dict) and isinstance(pdf.get("fields"), list):
        return materialize_unified_pdf_fields([f for f in pdf.get("fields", []) if isinstance(f, dict)])
    fields = template_obj.get("fields")
    if isinstance(fields, list):
        return materialize_unified_pdf_fields([f for f in fields if isinstance(f, dict)])
    return []


def _to_frontend_type(field_type: str) -> str:
    ft = (field_type or "").strip().lower()
    if ft == "check":
        return "boolean"
    if ft == "image":
        return "signature"
    return "text"


def _build_frontend_field_items(pdf_fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    id_counter: dict[str, int] = {}
    for idx, f in enumerate(pdf_fields):
        field_id_raw = str(f.get("id") or "").strip()
        title = str(f.get("title") or "").strip()
        placeholder = str(f.get("placeholder") or "").strip()
        pdf_field_id = str(f.get("pdfFieldId") or f.get("id") or f"f{idx + 1}")
        base_id = field_id_raw or placeholder or pdf_field_id
        n = id_counter.get(base_id, 0) + 1
        id_counter[base_id] = n
        field_id = base_id if n == 1 else f"{base_id}_{n}"
        page_raw = f.get("page")
        try:
            page = int(page_raw)
        except (TypeError, ValueError):
            page = 1
        anchor_type = str(f.get("fieldType") or "text").lower() or "text"
        row: dict[str, Any] = {
            "id": field_id,
            "title": title or placeholder or field_id,
            "label": title or placeholder or field_id,
            "type": _to_frontend_type(anchor_type),
            "required": False,
            "defaultValue": None,
            "source": {
                "key": field_id_raw or placeholder or field_id,
                "pdfFieldId": pdf_field_id,
                "anchorType": anchor_type,
            },
        }
        rect = frontend_rect_from_materialized(f)
        if rect is not None:
            row["rect"] = rect
        out.append(row)
    return out


def _autogen_form_schema(pdf_fields: list[dict[str, Any]]) -> dict[str, Any]:
    # Keep order stable by first occurrence in PDF fields.
    dedup: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for f in pdf_fields:
        placeholder = str(f.get("placeholder") or "").strip()
        if not placeholder or placeholder in dedup:
            continue
        dedup[placeholder] = {
            "id": placeholder,
            "type": _to_frontend_type(str(f.get("fieldType") or "")),
            "label": placeholder,
            "required": False,
            "defaultValue": None,
            "pdfAnchorRef": placeholder,
        }

    return {
        "constants": {},
        "enums": {},
        "steps": [
            {
                "id": "auto_step_1",
                "title": "自动生成字段",
                "sections": [
                    {
                        "id": "auto_section_1",
                        "title": "模板字段",
                        "layout": "form",
                        "fields": list(dedup.values()),
                    }
                ],
            }
        ],
    }


def extract_frontend_template(template_obj: dict[str, Any]) -> dict[str, Any]:
    schema = str(template_obj.get("schema") or "").strip()
    meta = template_obj.get("meta") if isinstance(template_obj.get("meta"), dict) else {}
    report_type = str(template_obj.get("reportType") or meta.get("reportType") or "").strip()
    version = str(template_obj.get("version") or meta.get("version") or "1.0.0").strip()

    pdf_fields = _resolve_pdf_fields(template_obj)
    frontend_fields = _build_frontend_field_items(pdf_fields)

    form_schema = template_obj.get("formSchema") if isinstance(template_obj.get("formSchema"), dict) else {}
    if not form_schema:
        form_schema = _autogen_form_schema(pdf_fields)
    constants = form_schema.get("constants") if isinstance(form_schema.get("constants"), dict) else {}
    enums = form_schema.get("enums") if isinstance(form_schema.get("enums"), dict) else {}
    steps = form_schema.get("steps") if isinstance(form_schema.get("steps"), list) else []

    bindings = template_obj.get("bindings") if isinstance(template_obj.get("bindings"), dict) else {}

    # 前端模板 LLM 草稿默认关闭（有待完善）；需要时 export ENABLE_LLM_FRONTEND_TEMPLATE_DRAFT=1
    use_llm = str(os.environ.get("ENABLE_LLM_FRONTEND_TEMPLATE_DRAFT", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if use_llm and not steps and pdf_fields:
        llm_payload = generate_frontend_template_with_ollama(template_obj, source_file_hint=1)
        llm_steps = llm_payload.get("steps") if isinstance(llm_payload.get("steps"), list) else []
        if llm_steps:
            constants = llm_payload.get("constants") if isinstance(llm_payload.get("constants"), dict) else constants
            enums = llm_payload.get("enums") if isinstance(llm_payload.get("enums"), dict) else enums
            steps = llm_steps

    return {
        "schema": "dynamic_form_template/v0.2",
        "sourceSchema": schema or "legacy_htmlpdf",
        "templateId": str(template_obj.get("templateId") or meta.get("templateId") or ""),
        "templateName": str(template_obj.get("templateName") or meta.get("templateName") or ""),
        "version": version,
        "reportType": report_type,
        "standard": str(template_obj.get("standard") or meta.get("standard") or ""),
        "locale": str(template_obj.get("locale") or "zh-CN"),
        "pdfUrl": "",
        "pdfTemplate": {
            "templateFileId": "",
            "templateFileName": "",
        },
        "constants": constants,
        "enums": enums,
        "steps": steps,
        # One input box = one field item, no xywh/xyxy coordinates.
        "fields": frontend_fields,
        "signatureMap": bindings.get("signature_map", {}) if isinstance(bindings.get("signature_map"), dict) else {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract frontend-focused template JSON")
    parser.add_argument("--input", required=True, help="Path to unified/legacy template JSON")
    parser.add_argument("--output", required=True, help="Path to write frontend JSON")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    template_obj = _load_json(input_path)
    frontend_obj = extract_frontend_template(template_obj)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(frontend_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: wrote frontend template -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

