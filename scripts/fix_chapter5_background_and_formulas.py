#!/usr/bin/env python3
"""批量修复现场记录第五章：本底误写 avg、fieldBindings 混入本底、均值/本底公式重刷。

安全策略：
- 修改前把当前 role:json 复制到同任务 ``history/``（带 ``.pre-ch5fix-YYYYMMDD`` 后缀）
- 并在 ``_task.json`` 的 ``files.history`` 登记，便于从库内历史复原
- 默认只处理活动模板（``current/_task.json`` 中 ``role=json``）

用法：
  DJANGO_SETTINGS_MODULE=tablet_backend.settings \\
    python scripts/fix_chapter5_background_and_formulas.py --dry-run
  DJANGO_SETTINGS_MODULE=tablet_backend.settings \\
    python scripts/fix_chapter5_background_and_formulas.py --apply
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import django

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
django.setup()

from radiation_detection_report.chapter5_field_sync import (  # noqa: E402
    _binding_contains_background_pids,
    _clear_mistaken_avg_on_field,
    apply_background_formulas_to_pdf_fields,
    apply_mean_formulas_to_pdf_fields,
    apply_report_formulas_to_pdf_fields,
    background_field_pids_from_geometry,
    build_field_bindings_from_pdf_fields,
    export_field_pdf_id,
    infer_background_geometry_from_fields,
    resolve_background_row_binding,
    resolve_chapter5_layout_for_fields,
)

MEDIA = ROOT / "media" / "file_library"
STAMP = date.today().strftime("%Y%m%d")


def _norm(v: Any) -> str:
    return str(v or "").strip()


def iter_active_json_paths() -> List[Tuple[Path, Path]]:
    """返回 [(json_path, task_json_path), ...]。"""
    out: List[Tuple[Path, Path]] = []
    for task_path in sorted(MEDIA.glob("templates/**/site/**/current/_task.json")):
        try:
            meta = json.loads(task_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        current = (meta.get("files") or {}).get("current") or []
        if not isinstance(current, list):
            continue
        for row in current:
            if not isinstance(row, dict) or row.get("role") != "json":
                continue
            rel = _norm(row.get("relative_path") or "")
            if not rel:
                disk = _norm(row.get("disk_name") or "")
                if disk:
                    cand = task_path.parent / disk
                    if cand.is_file():
                        out.append((cand, task_path))
                continue
            full = MEDIA / rel
            if full.is_file():
                out.append((full, task_path))
    return out


def get_chapter(data: Dict[str, Any]) -> Dict[str, Any]:
    fs = data.get("formSchema")
    if isinstance(fs, dict):
        ch = fs.get("radiationProtectionChapter")
        if isinstance(ch, dict) and ch:
            return copy.deepcopy(ch)
    ch = data.get("radiationProtectionChapter")
    if isinstance(ch, dict) and ch:
        return copy.deepcopy(ch)
    return {}


def set_chapter(data: Dict[str, Any], chapter: Dict[str, Any]) -> None:
    """规范为 formSchema.radiationProtectionChapter；根级若曾存在则同步一份便于旧工具读。"""
    fs = data.get("formSchema")
    if not isinstance(fs, dict):
        fs = {}
        data["formSchema"] = fs
    fs["radiationProtectionChapter"] = chapter
    # 根级保留同步副本（与编辑器曾双写兼容）；内容一致，避免读旧路径丢配置
    data["radiationProtectionChapter"] = copy.deepcopy(chapter)


def clear_avg_on_background_fields(fields: List[Dict[str, Any]], bg_pids: set[str]) -> int:
    cleared = 0
    for field in fields:
        pid = export_field_pdf_id(field)
        if not pid or pid not in bg_pids:
            continue
        before = _norm(field.get("fieldExpression") or field.get("formula") or "")
        _clear_mistaken_avg_on_field(field)
        rules = field.get("formulaRules")
        if isinstance(rules, list):
            kept = []
            changed = False
            for r in rules:
                if not isinstance(r, dict):
                    continue
                expr = _norm(r.get("expression") if r.get("expression") is not None else r.get("formula"))
                if expr.lower().startswith("avg("):
                    changed = True
                    continue
                kept.append(r)
            if changed:
                if kept:
                    field["formulaRules"] = kept
                else:
                    field.pop("formulaRules", None)
                    field.pop("fieldExpressionRules", None)
                cleared += 1
        after = _norm(field.get("fieldExpression") or field.get("formula") or "")
        if before.lower().startswith("avg(") and not after.lower().startswith("avg("):
            cleared += 1
        # 空 override 挡清理
        if not after and not field.get("formulaRules"):
            field.pop("fieldFormulaUserOverride", None)
    return cleared


def archive_current_to_history(json_path: Path, task_path: Path) -> Optional[Path]:
    """复制当前 JSON 到 history/，并登记 _task.json。"""
    history_dir = json_path.parent.parent / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    backup_name = f"{json_path.stem}.pre-ch5fix-{STAMP}{json_path.suffix}"
    backup_path = history_dir / backup_name
    if not backup_path.exists():
        shutil.copy2(json_path, backup_path)

    try:
        meta = json.loads(task_path.read_text(encoding="utf-8"))
    except Exception:
        return backup_path

    files = meta.setdefault("files", {})
    hist = files.setdefault("history", [])
    if not isinstance(hist, list):
        hist = []
        files["history"] = hist

    rel = f"templates/{backup_path.relative_to(MEDIA / 'templates').as_posix()}"
    # relative_path 习惯以 templates/ 开头
    rel = str(backup_path.relative_to(MEDIA)).replace("\\", "/")
    already = any(
        isinstance(r, dict) and (
            _norm(r.get("relative_path")) == rel
            or _norm(r.get("disk_name")) == backup_name
        )
        for r in hist
    )
    if not already:
        hist.append(
            {
                "relative_path": rel,
                "disk_name": backup_name,
                "size": backup_path.stat().st_size,
                "library_file_id": None,
                "original_name": f"{json_path.name} (pre chapter5 fix {STAMP})",
                "role": "json",
                "note": "auto-backup before chapter5 background/formula fix",
            }
        )
        task_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return backup_path


def template_needs_rp_fix(data: Dict[str, Any]) -> bool:
    fields = (data.get("pdf") or {}).get("fields")
    if not isinstance(fields, list) or not fields:
        return False
    chapter = get_chapter(data)
    if chapter:
        return True
    # 无章节对象但能推出本底几何
    geo = infer_background_geometry_from_fields(fields)
    return bool(geo.get("report_d") or geo.get("reading_pids"))


def diagnose(data: Dict[str, Any]) -> Dict[str, Any]:
    fields = [f for f in ((data.get("pdf") or {}).get("fields") or []) if isinstance(f, dict)]
    chapter = get_chapter(data)
    bg_pids = background_field_pids_from_geometry(fields, chapter=chapter or None)
    avg_on_bg = 0
    for f in fields:
        pid = export_field_pdf_id(f)
        if not pid or pid not in bg_pids:
            continue
        fe = _norm(f.get("fieldExpression") or f.get("formula") or "")
        if fe.lower().startswith("avg("):
            avg_on_bg += 1
        for r in (f.get("formulaRules") or []):
            if not isinstance(r, dict):
                continue
            ex = _norm(r.get("expression") if r.get("expression") is not None else r.get("formula"))
            if ex.lower().startswith("avg("):
                avg_on_bg += 1
                break
    stored = chapter.get("fieldBindings") if isinstance(chapter.get("fieldBindings"), list) else []
    mixed = sum(
        1 for b in stored
        if isinstance(b, dict) and _binding_contains_background_pids(b, bg_pids)
    )
    geo = infer_background_geometry_from_fields(fields, column_layout=resolve_chapter5_layout_for_fields(fields))
    return {
        "bg_pids": len(bg_pids),
        "avg_on_bg": avg_on_bg,
        "bindings_mixed": mixed,
        "reading_n": len(geo.get("reading_pids") or []),
        "report_d": geo.get("report_d") or "",
        "has_rules": bool(chapter.get("reportValueRules")),
    }


def fix_one(data: Dict[str, Any]) -> Dict[str, Any]:
    fields = [f for f in ((data.get("pdf") or {}).get("fields") or []) if isinstance(f, dict)]
    chapter = get_chapter(data)
    if not chapter:
        chapter = {
            "chapterKey": "site_radiation_protection",
            "meanFormula": {"mode": "per_row_avg", "groups": 1},
            "reportValueRules": [],
            "annualDoseRules": [],
            "fieldBindings": [],
        }

    layout = resolve_chapter5_layout_for_fields(fields)
    bg_pids = background_field_pids_from_geometry(fields, chapter=chapter)
    cleared = clear_avg_on_background_fields(fields, bg_pids)

    rebuilt = build_field_bindings_from_pdf_fields(
        fields,
        chapter=chapter,
        template_payload=data,
    )
    rebuilt = [
        b for b in (rebuilt or [])
        if isinstance(b, dict) and not _binding_contains_background_pids(b, bg_pids)
    ]
    # 保留旧绑定中的 seq_no / remark
    old_by_rp = {
        _norm(b.get("radiationPoint")): b
        for b in (chapter.get("fieldBindings") or [])
        if isinstance(b, dict) and _norm(b.get("radiationPoint"))
    }
    for b in rebuilt:
        old = old_by_rp.get(_norm(b.get("radiationPoint")))
        if not old:
            continue
        if old.get("seq_no") and not b.get("seq_no"):
            b["seq_no"] = old["seq_no"]
        if old.get("remark") and not b.get("remark"):
            b["remark"] = old["remark"]

    chapter["fieldBindings"] = rebuilt
    chapter["backgroundBinding"] = resolve_background_row_binding(
        fields,
        chapter=chapter,
        template_payload=data,
    )

    # 双组 mode 与版式对齐（不改用户已设 mode，若缺省再推断）
    mean_cfg = chapter.get("meanFormula") if isinstance(chapter.get("meanFormula"), dict) else {}
    if not mean_cfg:
        chapter["meanFormula"] = {"mode": "per_row_avg", "groups": 1}
        mean_cfg = chapter["meanFormula"]
    dual_hits = sum(1 for b in rebuilt if _norm(b.get("mean_m_2") or b.get("report_d_2")))
    if dual_hits >= 3 and int(mean_cfg.get("groups") or 1) < 2:
        mean_cfg["mode"] = "per_row_avg_dual"
        mean_cfg["groups"] = 2

    apply_mean_formulas_to_pdf_fields(fields, chapter=chapter)
    # 再次清本底 avg（均值刷写后兜底）
    cleared += clear_avg_on_background_fields(fields, bg_pids)
    apply_background_formulas_to_pdf_fields(fields, chapter=chapter)
    if chapter.get("reportValueRules"):
        apply_report_formulas_to_pdf_fields(fields, chapter=chapter)

    set_chapter(data, chapter)
    data["pdf"]["fields"] = fields

    after = diagnose(data)
    return {
        "cleared_avg": cleared,
        "bindings": len(rebuilt),
        "bg_pids": len(bg_pids),
        "layout": layout,
        "after": after,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fix chapter5 background bindings/formulas")
    ap.add_argument("--dry-run", action="store_true", help="只诊断，不写盘")
    ap.add_argument("--apply", action="store_true", help="归档历史后写回修复结果")
    ap.add_argument("--only", action="append", default=[], help="只处理路径子串（可多次）")
    args = ap.parse_args()
    if not args.dry_run and not args.apply:
        print("请指定 --dry-run 或 --apply")
        return 2

    pairs = iter_active_json_paths()
    if args.only:
        pairs = [
            (jp, tp) for jp, tp in pairs
            if any(s in str(jp) for s in args.only)
        ]

    stats = {
        "scanned": 0,
        "skipped_no_rp": 0,
        "needs_fix": 0,
        "fixed": 0,
        "unchanged": 0,
        "errors": 0,
    }
    rows_out: List[Dict[str, Any]] = []

    for json_path, task_path in pairs:
        stats["scanned"] += 1
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            stats["errors"] += 1
            print(f"ERROR read {json_path}: {exc}")
            continue
        if not template_needs_rp_fix(data):
            stats["skipped_no_rp"] += 1
            continue

        before = diagnose(data)
        needs = (
            before["avg_on_bg"] > 0
            or before["bindings_mixed"] > 0
            or before["bg_pids"] > 0
        )
        # 有防护章节就刷一遍均值/本底公式（即使当前诊断为 0，也纠正绑定）
        if not needs and not get_chapter(data):
            stats["skipped_no_rp"] += 1
            continue

        stats["needs_fix"] += 1
        rel = str(json_path.relative_to(MEDIA))
        if args.dry_run:
            print(
                f"[dry] {rel} avg_on_bg={before['avg_on_bg']} "
                f"mixed={before['bindings_mixed']} bg={before['bg_pids']} "
                f"readings={before['reading_n']}"
            )
            rows_out.append({"path": rel, "before": before, "action": "dry"})
            continue

        backup = archive_current_to_history(json_path, task_path)
        result = fix_one(data)
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        after = result["after"]
        changed = (
            before["avg_on_bg"] != after["avg_on_bg"]
            or before["bindings_mixed"] != after["bindings_mixed"]
            or result["cleared_avg"] > 0
        )
        if changed:
            stats["fixed"] += 1
        else:
            stats["unchanged"] += 1
        print(
            f"[fix] {rel} cleared_avg={result['cleared_avg']} "
            f"avg {before['avg_on_bg']}→{after['avg_on_bg']} "
            f"mixed {before['bindings_mixed']}→{after['bindings_mixed']} "
            f"backup={backup.name if backup else '-'}"
        )
        rows_out.append(
            {
                "path": rel,
                "before": before,
                "after": after,
                "cleared_avg": result["cleared_avg"],
                "backup": str(backup) if backup else None,
            }
        )

    print("---")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    report = ROOT / "docs" / f"chapter5-fix-report-{STAMP}.json"
    if args.apply:
        report.write_text(json.dumps({"stats": stats, "rows": rows_out}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"report → {report}")
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
