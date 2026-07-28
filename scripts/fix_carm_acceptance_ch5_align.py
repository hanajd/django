#!/usr/bin/env python3
"""仿照 C 形臂状态模板，对齐验收第五章偏移坐标并重刷绑定/公式。"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from radiation_detection_report.chapter5_field_sync import (  # noqa: E402
    SCHEMA_KEY,
    apply_annual_dose_formulas_to_pdf_fields,
    apply_background_formulas_to_pdf_fields,
    apply_mean_formulas_to_pdf_fields,
    apply_report_formulas_to_pdf_fields,
    background_field_pids_from_geometry,
    build_chapter_state,
    export_field_pdf_id,
)

MEDIA = ROOT / "media" / "file_library" / "templates"
STATUS_JSON = (
    MEDIA
    / "002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current"
    / "51f0f2a41abd4bd1a928072115be3b9a.json"
)
ACCEPT_DIR = MEDIA / "001-验收检测/04-C形臂/site/jxfs-js001-v30-x-202641"
ACCEPT_JSON = ACCEPT_DIR / "current" / "70cd495b12b64786b60228c1dc606efb.json"
TASK_JSON = ACCEPT_DIR / "current" / "_task.json"

# 验收里误偏移 +3/+3、且误标「本底水平_N」的测量格（状态模板为正确版式）
ALIGN_PIDS = {
    "f478",
    "f479",
    "f483",
    "f484",
    "f485",
    "f486",
    "f490",
    "f491",
    "f492",
    "f493",
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _index_by_pid(fields: list) -> dict:
    out = {}
    for f in fields or []:
        if isinstance(f, dict):
            pid = export_field_pdf_id(f)
            if pid:
                out[pid] = f
    return out


def _iter_step_fields(form_schema: dict):
    for step in form_schema.get("steps") or []:
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
            for fld in matrix.get("headerFields") or []:
                if isinstance(fld, dict):
                    yield fld
            for row in matrix.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells")
                if isinstance(cells, dict):
                    for cell in cells.values():
                        if isinstance(cell, dict):
                            yield cell


def sync_steps_from_pdf(form_schema: dict, by_pid: dict, bg_pids: set, report_bg: str) -> int:
    """只遍历 steps 栏位，避免全树 walk 卡死。"""
    n = 0
    for o in _iter_step_fields(form_schema):
        src = o.get("source") if isinstance(o.get("source"), dict) else None
        pid = str(o.get("pdfFieldId") or (src or {}).get("pdfFieldId") or "").lower()
        if not pid or pid not in by_pid:
            if pid in bg_pids and pid != report_bg:
                fe = str(o.get("fieldExpression") or o.get("formula") or "")
                if fe.lower().startswith("avg("):
                    o["fieldExpression"] = ""
                    o.pop("formula", None)
                    if src is not None:
                        src.pop("pdfFieldExpression", None)
                    n += 1
            continue
        pf = by_pid[pid]
        expr = pf.get("fieldExpression")
        if expr:
            o["fieldExpression"] = expr
            o["formula"] = expr
            if src is None:
                src = {}
                o["source"] = src
            src["pdfFieldExpression"] = expr
            src.setdefault("pdfFieldId", pid)
            n += 1
        elif pid in bg_pids and pid != report_bg:
            fe = str(o.get("fieldExpression") or o.get("formula") or "")
            if fe.lower().startswith("avg("):
                o["fieldExpression"] = ""
                o.pop("formula", None)
                if src is not None:
                    src.pop("pdfFieldExpression", None)
                n += 1
    return n


def main() -> int:
    backups = sorted((ACCEPT_DIR / "history").glob("70cd495b*.pre-ch5-align-*.json"))
    if not backups:
        _log("ERROR: no pre-ch5-align backup in history")
        return 1
    base_path = backups[-1]
    _log(f"base={base_path.name}")
    _log(f"status={STATUS_JSON.name}")

    status = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    accept = json.loads(base_path.read_text(encoding="utf-8"))
    status_by_pid = _index_by_pid((status.get("pdf") or {}).get("fields") or [])

    aligned = 0
    for f in (accept.get("pdf") or {}).get("fields") or []:
        if not isinstance(f, dict):
            continue
        pid = export_field_pdf_id(f)
        if pid not in ALIGN_PIDS:
            continue
        sf = status_by_pid.get(pid)
        if not sf:
            continue
        if isinstance(sf.get("rect"), list):
            f["rect"] = list(sf["rect"])
        for key in ("id", "placeholder", "originalPlaceholder", "title", "label"):
            if sf.get(key) not in (None, ""):
                f[key] = sf.get(key)
        aligned += 1
    _log(f"aligned_fields={aligned}")

    fs = accept.setdefault("formSchema", {})
    ch_existing = dict(fs.get(SCHEMA_KEY) or {}) if isinstance(fs.get(SCHEMA_KEY), dict) else {}
    ch_status = (status.get("formSchema") or {}).get(SCHEMA_KEY) or {}
    if isinstance(ch_status.get("reportValueRules"), list) and ch_status["reportValueRules"]:
        ch_existing["reportValueRules"] = deepcopy(ch_status["reportValueRules"])
    if isinstance(ch_status.get("meanFormula"), dict):
        ch_existing["meanFormula"] = deepcopy(ch_status["meanFormula"])

    fields = (accept.get("pdf") or {}).get("fields") or []
    for f in fields:
        if isinstance(f, dict):
            f.pop("fieldFormulaUserOverride", None)

    _log("build_chapter_state…")
    ch = build_chapter_state(fields, ch_existing, template_payload=accept)
    if isinstance(ch_existing.get("reportValueRules"), list):
        ch["reportValueRules"] = ch_existing["reportValueRules"]
    if isinstance(ch_existing.get("annualDoseRules"), list):
        ch["annualDoseRules"] = ch_existing.get("annualDoseRules")

    _log("apply formulas…")
    apply_mean_formulas_to_pdf_fields(fields, chapter=ch)
    apply_report_formulas_to_pdf_fields(fields, chapter=ch)
    apply_annual_dose_formulas_to_pdf_fields(fields, chapter=ch)
    apply_background_formulas_to_pdf_fields(fields, chapter=ch)

    bg_pids = background_field_pids_from_geometry(fields, chapter=ch)
    report_bg = str((ch.get("backgroundBinding") or {}).get("report_d") or "").lower()
    by_pid = _index_by_pid(fields)
    synced = sync_steps_from_pdf(fs, by_pid, bg_pids, report_bg)
    _log(f"steps_synced={synced}")

    fs[SCHEMA_KEY] = ch
    accept.pop(SCHEMA_KEY, None)

    _log("write current json…")
    ACCEPT_JSON.write_text(
        json.dumps(accept, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if TASK_JSON.is_file():
        task = json.loads(TASK_JSON.read_text(encoding="utf-8"))
        for item in task.get("files", {}).get("current") or []:
            if isinstance(item, dict) and item.get("disk_name") == ACCEPT_JSON.name:
                item["size"] = ACCEPT_JSON.stat().st_size
                break
        task["updated_at"] = datetime.now(timezone.utc).isoformat()
        TASK_JSON.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    row = next(b for b in ch["fieldBindings"] if b.get("mean_m") == "f476")
    _log(f"f476_binding={ {k: v for k, v in row.items() if v} }")
    for pid in (
        "f476",
        "f477",
        "f478",
        "f479",
        "f486",
        "f482",
        "f493",
        "f489",
        "f222",
        "f224",
        "f494",
        "f578",
    ):
        f = by_pid.get(pid)
        if not f:
            _log(f"{pid} MISSING")
            continue
        _log(f"{pid} rect={f.get('rect')} expr={f.get('fieldExpression')!r} id={f.get('id')!r}")

    bad = []
    for b in ch["fieldBindings"]:
        r = [b.get("reading_1"), b.get("reading_2"), b.get("reading_3")]
        n = sum(1 for x in r if x)
        dual = any(b.get(k) for k in ("reading_1_2", "mean_m_2", "report_d_2"))
        if (n and n < 3 and b.get("mean_m")) or dual:
            bad.append({k: v for k, v in b.items() if v})
    _log(f"bad_bindings={len(bad)}")
    for b in bad:
        _log(f"  {b}")
    _log(f"reportRules={ch.get('reportValueRules')}")
    _log("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
