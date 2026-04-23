#!/usr/bin/env python3
"""
Migrate legacy HTMLPDF field JSON:
- id: always unique in a field list (semantic key + numeric suffix when duplicated)
- placeholder: semantic key for backend matching
- title: Chinese display text for frontend
- pdfFieldId: original PDF anchor id (f1/f2/...)

Writes NEW files next to sources (does not overwrite).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_TPL = ROOT / "media" / "file_library" / "templates"

REF_PATH = MEDIA_TPL / "d33dfd62251542f693b4145169605507_xray_fluoroscopy_ws76_unified_v2.generated.json"
SRC_JS001 = MEDIA_TPL / "02176bece3c945e2880a1f56c7150714_JS-001 (1).json"
OUT_GENERATED = MEDIA_TPL / "d33dfd62251542f693b4145169605507_xray_fluoroscopy_ws76_unified_v2.formatted.json"
OUT_JS001 = MEDIA_TPL / "02176bece3c945e2880a1f56c7150714_JS-001_unified_v2.json"

_FID_RE = re.compile(r"^f\d+$", re.I)

# English semantic key -> Chinese title (when reference has no better title)
_TITLE_FALLBACK: dict[str, str] = {
    "commissionNo": "委托编号",
    "testnumber": "受检编号",
    "year": "年",
    "month": "月",
    "day": "日",
    "temperature": "环境温度",
    "RH": "相对湿度",
    "hospitalname": "医院名称",
    "commit_name": "委托方",
    "hospital_address": "地址",
    "contact": "联系人",
    "model": "型号",
    "ratedkV": "额定 kV",
    "ratedmA": "额定 mA",
    "deviceName": "设备名称",
    "serialNo": "设备编号",
    "location": "设备场所",
    "manufacturer": "生产厂家",
    "author": "编制人签名",
    "reviewer": "审核人签名",
    "approver": "批准人签名",
}


def _build_ref_title_maps(ref: dict) -> tuple[dict[str, str], dict[str, str]]:
    by_fid: dict[str, str] = {}
    by_key: dict[str, str] = {}
    pdf_fields = []
    if isinstance(ref.get("pdf"), dict) and isinstance(ref["pdf"].get("fields"), list):
        pdf_fields = ref["pdf"]["fields"]
    for f in pdf_fields:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("pdfFieldId") or "").strip()
        kid = str(f.get("id") or f.get("placeholder") or "").strip()
        tit = str(f.get("title") or "").strip()
        if not tit:
            continue
        if fid:
            by_fid.setdefault(fid, tit)
        if kid and not _FID_RE.match(kid):
            by_key.setdefault(kid, tit)
    return by_fid, by_key


def _ref_title_usable(t: str, semantic: str) -> bool:
    if not t:
        return False
    if t == semantic:
        return False
    if t.isascii() and semantic.isascii() and t.replace(" ", "").lower() == semantic.replace(" ", "").lower():
        return False
    return True


def _pick_title(semantic: str, pdf_fid: str, by_fid: dict[str, str], by_key: dict[str, str]) -> str:
    for t in (by_fid.get(pdf_fid), by_key.get(semantic)):
        if t and _ref_title_usable(t, semantic):
            return t
    if re.search(r"[\u4e00-\u9fff]", semantic):
        return semantic
    if semantic in _TITLE_FALLBACK:
        return _TITLE_FALLBACK[semantic]
    try:
        from utils.frontend_schema_rule_engine import _underscore_field_label

        hint = _underscore_field_label(semantic, semantic)
        if hint and hint != semantic:
            return hint
    except Exception:
        pass
    return semantic


def _unique_id(base: str, seen: dict[str, int]) -> str:
    """Ensure id uniqueness while keeping a stable semantic prefix."""
    key = base or "field"
    n = seen.get(key, 0) + 1
    seen[key] = n
    if n == 1:
        return key
    return f"{key}_{n}"


def _normalize_one_field(
    fld: dict,
    by_fid: dict[str, str],
    by_key: dict[str, str],
    seen_ids: dict[str, int],
) -> dict:
    old_id = str(fld.get("id") or "").strip()
    ph = str(fld.get("placeholder") or "").strip()
    existing_pdf = str(fld.get("pdfFieldId") or "").strip()

    if existing_pdf and not _FID_RE.match(old_id) and old_id:
        semantic = old_id
        pdf_fid = existing_pdf if _FID_RE.match(existing_pdf) else old_id
    elif _FID_RE.match(old_id):
        semantic = ph or old_id
        pdf_fid = old_id
    else:
        semantic = ph or old_id
        pdf_fid = existing_pdf or old_id

    # 统一用规则选 title，避免旧文件里 title 与 id 相同（如 testnumber）挡住中文配对
    title = _pick_title(semantic, pdf_fid, by_fid, by_key)

    out = {k: v for k, v in fld.items() if k not in ("id", "placeholder", "title", "pdfFieldId")}
    out["id"] = _unique_id(semantic, seen_ids)
    out["placeholder"] = semantic
    out["pdfFieldId"] = pdf_fid
    out["title"] = title
    return out


def migrate_field_list(fields: list, by_fid: dict[str, str], by_key: dict[str, str]) -> list:
    out: list[dict] = []
    seen_ids: dict[str, int] = {}
    for fld in fields:
        if not isinstance(fld, dict):
            continue
        out.append(_normalize_one_field(fld, by_fid, by_key, seen_ids))
    return out


def clean_unified_template(obj: dict, by_fid: dict[str, str], by_key: dict[str, str]) -> dict:
    data = json.loads(json.dumps(obj, ensure_ascii=False))
    pdf = data.get("pdf")
    if isinstance(pdf, dict) and isinstance(pdf.get("fields"), list):
        pdf["fields"] = migrate_field_list(pdf["fields"], by_fid, by_key)
    if isinstance(data.get("fields"), list):
        data["fields"] = migrate_field_list(data["fields"], by_fid, by_key)
    return data


def build_js001_unified(flat: dict, by_fid: dict[str, str], by_key: dict[str, str]) -> dict:
    fields_raw = flat.get("fields") if isinstance(flat.get("fields"), list) else []
    migrated = migrate_field_list(fields_raw, by_fid, by_key)
    return {
        "schema": "unified_form_template/v2",
        "templateId": "js001_ws76_legacy",
        "templateName": "JS-001 (HTMLPDF migrated)",
        "version": "1.0.0",
        "reportType": "xray_fluoroscopy",
        "standard": "WS76-2020",
        "pdfUrl": "",
        "locale": "zh-CN",
        "constants": {},
        "enums": {},
        "steps": [],
        "pdf": {"source_pdf": {}, "fields": migrated},
        "formSchema": {"constants": {}, "enums": {}, "steps": []},
    }


def main() -> None:
    ref = json.loads(REF_PATH.read_text(encoding="utf-8"))
    by_fid, by_key = _build_ref_title_maps(ref)

    gen_clean = clean_unified_template(ref, by_fid, by_key)
    OUT_GENERATED.write_text(
        json.dumps(gen_clean, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    js = json.loads(SRC_JS001.read_text(encoding="utf-8"))
    js_uni = build_js001_unified(js, by_fid, by_key)
    OUT_JS001.write_text(
        json.dumps(js_uni, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Wrote:", OUT_GENERATED)
    print("Wrote:", OUT_JS001)


if __name__ == "__main__":
    main()
