#!/usr/bin/env python3
"""按胃肠机状态模板，修复验收现场记录：第五章输入框/绑定/公式 + 拟合配置（含补 f1242）。"""
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
    / "002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current"
    / "d8e41bc293784246b68e648d8a043a10.json"
)
ACCEPT_DIR = MEDIA / "001-验收检测/05-胃肠机/site/jxfs-js115-v20-x-202641"
ACCEPT_JSON = ACCEPT_DIR / "current" / "0ab0bf5b8b7b48b8b6f1f07070617296.json"
TASK_JSON = ACCEPT_DIR / "current" / "_task.json"
HIST = ACCEPT_DIR / "history"

# 验收末两行误偏移 +3/+3、且误标「本底水平」的测量格（状态模板为正确版式）
ALIGN_PIDS = {
    "f995",
    "f996",
    "f1002",
    "f1003",
    "f1004",
    "f1005",
    "f1006",
    "f1007",
    "f1008",
    "f1009",
}

READING_KEYS = (
    "reading_1",
    "reading_2",
    "reading_3",
    "reading_1_2",
    "reading_2_2",
    "reading_3_2",
)


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
    n = 0
    for o in _iter_step_fields(form_schema):
        src = o.get("source") if isinstance(o.get("source"), dict) else None
        pid = str(o.get("pdfFieldId") or (src or {}).get("pdfFieldId") or "").lower()
        if not pid:
            continue
        pf = by_pid.get(pid)
        if pf is None:
            continue
        # 拟合元数据
        for key in ("fitBinding", "formulaRules", "displayFormat", "fitConfigRef"):
            if key in pf:
                o[key] = deepcopy(pf[key])
            elif key in o:
                o.pop(key, None)
        expr = str(pf.get("fieldExpression") or "").strip()
        if expr:
            o["fieldExpression"] = expr
            o["formula"] = expr
            if src is None:
                src = {}
                o["source"] = src
            src["pdfFieldExpression"] = expr
            src.setdefault("pdfFieldId", pid)
            n += 1
        else:
            if o.get("fieldExpression") or o.get("formula"):
                o["fieldExpression"] = ""
                o.pop("formula", None)
                if src is not None:
                    src.pop("pdfFieldExpression", None)
                n += 1
            if pid in bg_pids and pid != report_bg:
                fe = str(o.get("fieldExpression") or o.get("formula") or "")
                if fe.lower().startswith("avg("):
                    o["fieldExpression"] = ""
                    o.pop("formula", None)
                    if src is not None:
                        src.pop("pdfFieldExpression", None)
                    n += 1
        # R² / 拟合主格 type
        if isinstance(pf.get("fitBinding"), dict) and pf["fitBinding"].get("r2FieldId"):
            o["type"] = "computed"
            if not str(o.get("displayFormat") or "").strip():
                o["displayFormat"] = "latex"
        elif any(
            isinstance(r, dict) and (
                str(r.get("expression") or "").startswith("fit(")
                or r.get("fitKind")
            ) and str(r.get("expression") or "") not in ("", "fit_eq()")
            for r in (pf.get("formulaRules") or [])
        ):
            o["type"] = "computed"
    return n


def clear_placeholder_and_reading_junk(fields: list, chapter: dict) -> int:
    """清掉 {mean}/{mean2} 残留，以及误写在读数格上的公式。"""
    reading_pids = set()
    for b in chapter.get("fieldBindings") or []:
        if not isinstance(b, dict):
            continue
        for k in READING_KEYS:
            pid = str(b.get(k) or "").strip().lower()
            if pid:
                reading_pids.add(pid)
    n = 0
    for f in fields:
        if not isinstance(f, dict):
            continue
        pid = export_field_pdf_id(f)
        fe = str(f.get("fieldExpression") or "").strip()
        if not fe:
            continue
        if "{mean" in fe or "{report" in fe:
            f.pop("fieldExpression", None)
            f.pop("pdfFieldExpression", None)
            n += 1
            continue
        if pid in reading_pids:
            f.pop("fieldExpression", None)
            f.pop("pdfFieldExpression", None)
            n += 1
    return n


def ensure_fit_from_status(accept_fields: list, status_by_pid: dict) -> tuple[int, bool]:
    """从状态模板拷贝拟合主格/从格；若验收缺 f1242 则追加。"""
    copied = 0
    added_master = False
    accept_by = _index_by_pid(accept_fields)

    # 状态侧拟合相关栏位
    fit_pids = []
    for pid, sf in status_by_pid.items():
        if not isinstance(sf, dict):
            continue
        if isinstance(sf.get("fitBinding"), dict) and (
            sf["fitBinding"].get("x") or sf["fitBinding"].get("y") or sf["fitBinding"].get("r2FieldId")
        ):
            fit_pids.append(pid)
        elif any(isinstance(r, dict) and r.get("fitKind") for r in (sf.get("formulaRules") or [])):
            fit_pids.append(pid)
    fit_pids = sorted(set(fit_pids), key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)

    for pid in fit_pids:
        sf = status_by_pid[pid]
        af = accept_by.get(pid)
        if af is None:
            # 追加缺失主格（状态有、验收无，如 f1242）
            row = deepcopy(sf)
            accept_fields.append(row)
            accept_by[pid] = row
            added_master = True
            copied += 1
            _log(f"added missing field {pid} from status")
            continue
        for key in (
            "fitBinding",
            "formulaRules",
            "fieldExpression",
            "displayFormat",
            "fitConfigRef",
        ):
            if key in sf:
                af[key] = deepcopy(sf[key])
            elif key in af and key in ("fitConfigRef",):
                af.pop(key, None)
        # 主格不要残留空 expression；从格保留 Pearson
        if isinstance(sf.get("fitBinding"), dict) and sf["fitBinding"].get("r2FieldId"):
            if not str(sf.get("fieldExpression") or "").strip():
                af.pop("fieldExpression", None)
        copied += 1
    return copied, added_master


def main() -> int:
    if not STATUS_JSON.is_file():
        _log(f"ERROR: missing status {STATUS_JSON}")
        return 1
    if not ACCEPT_JSON.is_file():
        _log(f"ERROR: missing accept {ACCEPT_JSON}")
        return 1

    status = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    accept = json.loads(ACCEPT_JSON.read_text(encoding="utf-8"))

    HIST.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = HIST / f"{ACCEPT_JSON.stem}.pre-gi-status-align-{stamp}.json"
    backup.write_text(json.dumps(accept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _log(f"archived {backup.name}")

    status_by_pid = _index_by_pid((status.get("pdf") or {}).get("fields") or [])
    fields = (accept.get("pdf") or {}).get("fields") or []
    if not isinstance(fields, list):
        _log("ERROR: accept pdf.fields missing")
        return 1

    # 0) 对齐末两行偏移输入框的 rect / 标签
    aligned = 0
    for f in fields:
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
    _log(f"aligned_boxes={aligned}")

    # 1) 拟合：补 f1242 + 同步 f200 等
    n_fit, added = ensure_fit_from_status(fields, status_by_pid)
    _log(f"fit_copied={n_fit} added_master={added}")

    fs = accept.setdefault("formSchema", {})
    ch_existing = dict(fs.get(SCHEMA_KEY) or {}) if isinstance(fs.get(SCHEMA_KEY), dict) else {}
    ch_status = (status.get("formSchema") or {}).get(SCHEMA_KEY) or {}
    if isinstance(ch_status.get("reportValueRules"), list) and ch_status["reportValueRules"]:
        ch_existing["reportValueRules"] = deepcopy(ch_status["reportValueRules"])
    if isinstance(ch_status.get("meanFormula"), dict):
        ch_existing["meanFormula"] = deepcopy(ch_status["meanFormula"])
    if isinstance(ch_status.get("annualDoseRules"), list) and ch_status["annualDoseRules"]:
        ch_existing["annualDoseRules"] = deepcopy(ch_status["annualDoseRules"])
    # 状态第五章绑定直接采用，避免末行偏移再被误分类
    if isinstance(ch_status.get("fieldBindings"), list) and ch_status["fieldBindings"]:
        ch_existing["fieldBindings"] = deepcopy(ch_status["fieldBindings"])
    if isinstance(ch_status.get("backgroundBinding"), dict) and ch_status["backgroundBinding"]:
        ch_existing["backgroundBinding"] = deepcopy(ch_status["backgroundBinding"])

    for f in fields:
        if isinstance(f, dict):
            f.pop("fieldFormulaUserOverride", None)

    _log("build_chapter_state…")
    ch = build_chapter_state(fields, ch_existing, template_payload=accept)
    if isinstance(ch_status.get("fieldBindings"), list) and ch_status["fieldBindings"]:
        ch["fieldBindings"] = deepcopy(ch_status["fieldBindings"])
    if isinstance(ch_status.get("backgroundBinding"), dict) and ch_status["backgroundBinding"]:
        ch["backgroundBinding"] = deepcopy(ch_status["backgroundBinding"])
    if isinstance(ch_existing.get("reportValueRules"), list):
        ch["reportValueRules"] = ch_existing["reportValueRules"]
    if isinstance(ch_existing.get("annualDoseRules"), list):
        ch["annualDoseRules"] = ch_existing["annualDoseRules"]

    _log("apply chapter formulas…")
    apply_mean_formulas_to_pdf_fields(fields, chapter=ch)
    apply_report_formulas_to_pdf_fields(fields, chapter=ch)
    apply_annual_dose_formulas_to_pdf_fields(fields, chapter=ch)
    apply_background_formulas_to_pdf_fields(fields, chapter=ch)

    cleared = clear_placeholder_and_reading_junk(fields, ch)
    _log(f"cleared_placeholder_or_reading_expr={cleared}")

    # 拟合字段在章节刷公式后可能被误清，再刷一次拟合
    ensure_fit_from_status(fields, status_by_pid)

    bg_pids = background_field_pids_from_geometry(fields, chapter=ch)
    report_bg = str((ch.get("backgroundBinding") or {}).get("report_d") or "").lower()
    by_pid = _index_by_pid(fields)

    # 本底读数格不得残留 avg
    for pid in bg_pids:
        if pid == report_bg:
            continue
        f = by_pid.get(pid)
        if not f:
            continue
        fe = str(f.get("fieldExpression") or "").strip()
        if fe.lower().startswith("avg("):
            f.pop("fieldExpression", None)

    synced = sync_steps_from_pdf(fs, by_pid, bg_pids, report_bg)
    _log(f"steps_synced={synced}")

    # steps 里若仍无 f1242，从 pdf 追加到合适 section（简单：挂到第一个含 f200 的 section）
    if added and "f1242" in by_pid:
        has_f1242 = any(
            str(o.get("pdfFieldId") or (o.get("source") or {}).get("pdfFieldId") or "").lower() == "f1242"
            for o in _iter_step_fields(fs)
        )
        if not has_f1242:
            pf = by_pid["f1242"]
            new_fld = {
                "id": "f1242",
                "type": "computed",
                "label": str(pf.get("id") or "拟合公式"),
                "pdfFieldId": "f1242",
                "required": False,
                "defaultValue": None,
                "displayFormat": str(pf.get("displayFormat") or "latex"),
                "fitBinding": deepcopy(pf.get("fitBinding")),
                "formulaRules": deepcopy(pf.get("formulaRules") or []),
                "formula": str(pf.get("fieldExpression") or "fit_eq()"),
                "fieldExpression": str(pf.get("fieldExpression") or "fit_eq()"),
                "source": {"pdfFieldId": "f1242", "pdfFieldExpression": str(pf.get("fieldExpression") or "fit_eq()")},
            }
            inserted = False
            for step in fs.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                for sec in step.get("sections") or []:
                    if not isinstance(sec, dict):
                        continue
                    fl = sec.get("fields")
                    if not isinstance(fl, list):
                        continue
                    for i, fld in enumerate(fl):
                        if isinstance(fld, dict) and str(fld.get("pdfFieldId") or "").lower() == "f200":
                            fl.insert(i, new_fld)
                            inserted = True
                            break
                    if inserted:
                        break
                if inserted:
                    break
            if not inserted:
                # fallback
                for step in fs.get("steps") or []:
                    for sec in step.get("sections") or []:
                        if isinstance(sec, dict) and isinstance(sec.get("fields"), list):
                            sec["fields"].append(new_fld)
                            inserted = True
                            break
                    if inserted:
                        break
            _log(f"steps_inserted_f1242={inserted}")

    fs[SCHEMA_KEY] = ch
    accept.pop(SCHEMA_KEY, None)
    accept["pdf"]["fields"] = fields

    ACCEPT_JSON.write_text(json.dumps(accept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if TASK_JSON.is_file():
        task = json.loads(TASK_JSON.read_text(encoding="utf-8"))
        for item in (task.get("files") or {}).get("current") or []:
            if isinstance(item, dict) and item.get("disk_name") == ACCEPT_JSON.name:
                item["size"] = ACCEPT_JSON.stat().st_size
                break
        task["updated_at"] = datetime.now(timezone.utc).isoformat()
        TASK_JSON.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dual = sum(1 for b in ch.get("fieldBindings") or [] if b.get("reading_1_2"))
    _log(f"bindings={len(ch.get('fieldBindings') or [])} dual={dual}")
    for pid in ("f759", "f761", "f749", "f200", "f1242", "f1010", "f999", "f1000"):
        f = by_pid.get(pid)
        if not f:
            _log(f"{pid} MISSING")
            continue
        _log(
            f"{pid} expr={str(f.get('fieldExpression') or '')[:70]!r} "
            f"fit={bool(f.get('fitBinding'))} rules={len(f.get('formulaRules') or [])}"
        )
    _log("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
