#!/usr/bin/env python3
"""按胃肠机（JS115）第五章几何/公式，修复动态 DR 验收+状态现场记录第五章。

策略（不硬编码 pdfFieldId）：
1. ``snap_narrow_dual_complementary_row_fragments``：按窄列 x 列带 + 行心 y，
   合并因坐标漂移拆开的互补行碎片（误标本底 / 列宽偏窄）
2. 从胃肠机状态模板同步章节公式规则（``reportValueRules`` / ``annualDoseRules`` / ``meanFormula``）
3. ``build_chapter_state`` 按表格行列重算绑定，并重刷均值/报出/年剂量/本底公式
4. 同步 steps 中的公式文本

用法：
  python scripts/fix_ddr_ch5_from_gi_geometry.py
  python scripts/fix_ddr_ch5_from_gi_geometry.py --dry-run
"""
from __future__ import annotations

import argparse
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
    snap_narrow_dual_complementary_row_fragments,
)

MEDIA = ROOT / "media" / "file_library" / "templates"
GI_STATUS = (
    MEDIA
    / "002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current"
    / "d8e41bc293784246b68e648d8a043a10.json"
)
TARGETS = (
    MEDIA
    / "002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current"
    / "85a6888b0eb84b1499c06bc47d4983bc.json",
    MEDIA
    / "001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current"
    / "2fdb9b34455241b998f0d743b09c03fb.json",
)

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
        if not pid or pid not in by_pid:
            continue
        pf = by_pid[pid]
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
    return n


def clear_reading_formulas(fields: list, chapter: dict) -> int:
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
        if pid not in reading_pids:
            continue
        if str(f.get("fieldExpression") or "").strip():
            f.pop("fieldExpression", None)
            f.pop("pdfFieldExpression", None)
            n += 1
    return n


def archive(json_path: Path) -> Path:
    hist = json_path.parent.parent / "history"
    hist.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bak = hist / f"{json_path.stem}.pre-ddr-ch5-geom-{stamp}.json"
    bak.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def update_task_size(json_path: Path) -> None:
    task_path = json_path.parent / "_task.json"
    if not task_path.is_file():
        return
    task = json.loads(task_path.read_text(encoding="utf-8"))
    for item in (task.get("files") or {}).get("current") or []:
        if isinstance(item, dict) and item.get("disk_name") == json_path.name:
            item["size"] = json_path.stat().st_size
            break
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fix_one(target: Path, gi_chapter: dict, *, dry_run: bool) -> int:
    if not target.is_file():
        _log(f"ERROR missing {target}")
        return 1
    data = json.loads(target.read_text(encoding="utf-8"))
    fields = (data.get("pdf") or {}).get("fields") or []
    if not isinstance(fields, list):
        _log(f"ERROR no pdf.fields in {target}")
        return 1

    snapped = snap_narrow_dual_complementary_row_fragments(fields)
    _log(f"{target.name}: snapped_boxes={snapped}")

    fs = data.setdefault("formSchema", {})
    ch_existing = dict(fs.get(SCHEMA_KEY) or {}) if isinstance(fs.get(SCHEMA_KEY), dict) else {}
    for key in ("reportValueRules", "annualDoseRules", "meanFormula"):
        if gi_chapter.get(key):
            ch_existing[key] = deepcopy(gi_chapter[key])
    ch_existing.pop("fieldBindings", None)
    ch_existing.pop("backgroundBinding", None)

    for f in fields:
        if isinstance(f, dict):
            f.pop("fieldFormulaUserOverride", None)

    ch = build_chapter_state(fields, ch_existing, template_payload=data)
    if isinstance(ch_existing.get("reportValueRules"), list):
        ch["reportValueRules"] = ch_existing["reportValueRules"]
    if isinstance(ch_existing.get("annualDoseRules"), list):
        ch["annualDoseRules"] = ch_existing["annualDoseRules"]
    if isinstance(ch_existing.get("meanFormula"), dict):
        ch["meanFormula"] = ch_existing["meanFormula"]

    apply_mean_formulas_to_pdf_fields(fields, chapter=ch)
    apply_report_formulas_to_pdf_fields(fields, chapter=ch)
    apply_annual_dose_formulas_to_pdf_fields(fields, chapter=ch)
    apply_background_formulas_to_pdf_fields(fields, chapter=ch)
    cleared = clear_reading_formulas(fields, ch)

    bg_pids = background_field_pids_from_geometry(fields, chapter=ch)
    report_bg = str((ch.get("backgroundBinding") or {}).get("report_d") or "").lower()
    by_pid = _index_by_pid(fields)
    for pid in bg_pids:
        if pid == report_bg:
            continue
        f = by_pid.get(pid)
        if f and str(f.get("fieldExpression") or "").lower().startswith("avg("):
            f.pop("fieldExpression", None)

    synced = sync_steps_from_pdf(fs, by_pid, bg_pids, report_bg)
    dual = sum(1 for b in ch.get("fieldBindings") or [] if b.get("reading_1_2") and b.get("mean_m_2"))
    _log(
        f"{target.name}: bindings={len(ch.get('fieldBindings') or [])} "
        f"dual_complete={dual} reading_cleared={cleared} steps_synced={synced}"
    )
    for pid in ("f996", "f993", "f1005", "f1009", "f999", "f1000", "f1001", "f1010"):
        f = by_pid.get(pid)
        _log(f"  {pid} expr={str((f or {}).get('fieldExpression') or '')[:70]!r}")

    if dry_run:
        _log(f"{target.name}: dry-run, not written")
        return 0

    bak = archive(target)
    _log(f"archived {bak.name}")
    fs[SCHEMA_KEY] = ch
    data.pop(SCHEMA_KEY, None)
    data["pdf"]["fields"] = fields
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_task_size(target)
    _log(f"wrote {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not GI_STATUS.is_file():
        _log(f"ERROR missing GI status {GI_STATUS}")
        return 1
    gi = json.loads(GI_STATUS.read_text(encoding="utf-8"))
    gi_chapter = (gi.get("formSchema") or {}).get(SCHEMA_KEY) or {}
    if not isinstance(gi_chapter, dict) or not gi_chapter:
        _log("ERROR GI chapter empty")
        return 1

    rc = 0
    for path in TARGETS:
        rc |= fix_one(path, gi_chapter, dry_run=args.dry_run)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
