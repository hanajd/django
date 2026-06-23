#!/usr/bin/env python3
"""Audit report_site_field_configs: sitePdfFieldId vs configured label on site template."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "media" / "file_library" / "templates"


def parse_template_fields(raw_text: str) -> dict:
    """Minimal parse: unified v2 pdf.fields 或 legacy top-level fields。"""
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        return {"fields": [], "steps": [], "bindings": {}}
    pdf_block = data.get("pdf")
    if isinstance(pdf_block, dict) and isinstance(pdf_block.get("fields"), list):
        fields = pdf_block.get("fields") or []
        steps = data.get("steps") if isinstance(data.get("steps"), list) else []
        form_schema = data.get("formSchema") if isinstance(data.get("formSchema"), dict) else {}
        if not steps and isinstance(form_schema.get("steps"), list):
            steps = form_schema["steps"]
    else:
        fields = data.get("fields") if isinstance(data.get("fields"), list) else []
        steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    return {
        "fields": [f for f in fields if isinstance(f, dict)],
        "steps": steps,
        "bindings": data.get("bindings") if isinstance(data.get("bindings"), dict) else {},
    }


def normalize_label_key(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def pdf_field_labels(field: dict) -> set[str]:
    out: set[str] = set()
    for k in ("id", "placeholder", "title", "label", "hierarchyKey"):
        nk = normalize_label_key(str(field.get(k) or ""))
        if nk:
            out.add(nk)
    return out


def build_site_label_index(site_parsed: dict) -> dict[str, set[str]]:
    """normalized label -> set of pdfFieldIds"""
    idx: dict[str, set[str]] = {}

    def _add_field(sf: dict) -> None:
        if not isinstance(sf, dict):
            return
        src = sf.get("source") if isinstance(sf.get("source"), dict) else {}
        pid = str(sf.get("pdfFieldId") or src.get("pdfFieldId") or "").strip()
        if not pid:
            return
        for lk in pdf_field_labels(sf):
            idx.setdefault(lk, set()).add(pid)

    for sf in site_parsed.get("fields") or []:
        _add_field(sf)
    for step in site_parsed.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            for sf in sec.get("fields") or []:
                _add_field(sf)
    return idx


def site_field_by_pid(site_parsed: dict, pid: str) -> dict | None:
    for sf in site_parsed.get("fields") or []:
        if isinstance(sf, dict) and str(sf.get("pdfFieldId") or "").strip() == pid:
            return sf
    return None


def find_site_template(site_task_code: str) -> Path | None:
    code = (site_task_code or "").strip().lower()
    if not code:
        return None
    for task_json in TEMPLATES.rglob("_task.json"):
        try:
            task = json.loads(task_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        tc = str(task.get("task_code") or task.get("code") or "").strip().lower()
        if tc != code:
            continue
        current = task_json.parent
        jsons = sorted(
            p for p in current.glob("*.json") if p.name != "_task.json" and p.name != task_json.name
        )
        if jsons:
            return jsons[0]
    return None


def audit_report_json(report_path: Path) -> list[dict]:
    try:
        parsed = parse_template_fields(report_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return [{"file": str(report_path), "error": str(e)}]

    bindings = parsed.get("bindings") if isinstance(parsed.get("bindings"), dict) else {}
    configs = bindings.get("report_site_field_configs") or []
    if not isinstance(configs, list) or not configs:
        return []

    site_cache: dict[str, dict] = {}
    issues: list[dict] = []

    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        report_pid = str(cfg.get("reportPdfFieldId") or "").strip()
        sources = cfg.get("sources") if isinstance(cfg.get("sources"), list) else []
        for src in sources:
            if not isinstance(src, dict):
                continue
            site_code = str(src.get("siteTaskCode") or "").strip()
            site_pid = str(src.get("sitePdfFieldId") or "").strip()
            label = str(src.get("label") or "").strip()
            if not site_pid or not label or not site_code:
                continue
            if site_code not in site_cache:
                site_path = find_site_template(site_code)
                if site_path is None:
                    site_cache[site_code] = {}
                    issues.append(
                        {
                            "file": str(report_path.relative_to(ROOT)),
                            "reportPdfFieldId": report_pid,
                            "siteTaskCode": site_code,
                            "sitePdfFieldId": site_pid,
                            "label": label[:80],
                            "problem": "site_template_not_found",
                        }
                    )
                    continue
                try:
                    site_cache[site_code] = parse_template_fields(
                        site_path.read_text(encoding="utf-8", errors="replace")
                    )
                except Exception as e:
                    site_cache[site_code] = {}
                    issues.append(
                        {
                            "file": str(report_path.relative_to(ROOT)),
                            "problem": f"site_template_read_error: {e}",
                        }
                    )
                    continue
            site_parsed = site_cache[site_code]
            if not site_parsed:
                continue
            want = normalize_label_key(label)
            idx = build_site_label_index(site_parsed)
            at_field = site_field_by_pid(site_parsed, site_pid)
            at_labels = pdf_field_labels(at_field) if at_field else set()
            if want in at_labels:
                continue
            candidates = sorted(idx.get(want, ()))
            issues.append(
                {
                    "file": str(report_path.relative_to(ROOT)),
                    "reportPdfFieldId": report_pid,
                    "siteTaskCode": site_code,
                    "sitePdfFieldId": site_pid,
                    "siteFieldAtPid": str((at_field or {}).get("id") or "")[:80],
                    "label": label[:80],
                    "suggestedSitePdfFieldIds": candidates[:5],
                    "problem": "sitePdfFieldId_label_mismatch",
                }
            )
    return issues


def apply_fixes(report_path: Path, mismatches: list[dict]) -> int:
    """将 sitePdfFieldId 更正为 label 唯一匹配的现场域（仅 suggested 唯一时）。"""
    fixable = [
        m
        for m in mismatches
        if m.get("problem") == "sitePdfFieldId_label_mismatch"
        and len(m.get("suggestedSitePdfFieldIds") or []) == 1
    ]
    if not fixable:
        return 0
    data = json.loads(report_path.read_text(encoding="utf-8"))
    bindings = data.get("bindings") if isinstance(data.get("bindings"), dict) else {}
    if not bindings:
        return 0
    configs = bindings.get("report_site_field_configs")
    flat = bindings.get("report_site_field_map")
    changed = 0
    for m in fixable:
        report_pid = m["reportPdfFieldId"]
        old_site = m["sitePdfFieldId"]
        new_site = m["suggestedSitePdfFieldIds"][0]
        label = m.get("label") or ""
        if isinstance(configs, list):
            for cfg in configs:
                if str(cfg.get("reportPdfFieldId") or "").strip() != report_pid:
                    continue
                for src in cfg.get("sources") or []:
                    if (
                        isinstance(src, dict)
                        and str(src.get("sitePdfFieldId") or "").strip() == old_site
                        and str(src.get("label") or "").strip() == label
                    ):
                        src["sitePdfFieldId"] = new_site
                        changed += 1
        if isinstance(flat, list):
            for row in flat:
                if (
                    isinstance(row, dict)
                    and str(row.get("reportPdfFieldId") or "").strip() == report_pid
                    and str(row.get("sitePdfFieldId") or "").strip() == old_site
                    and str(row.get("label") or "").strip() == label
                ):
                    row["sitePdfFieldId"] = new_site
                    changed += 1
    if not changed:
        return 0
    data["bindings"] = bindings
    report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    only_current = "--all" not in sys.argv
    do_fix = "--fix" in sys.argv
    paths = sorted(TEMPLATES.rglob("current/*.json" if only_current else "*.json"))
    all_issues: list[dict] = []
    seen_files: set[str] = set()
    for p in paths:
        if "report" not in str(p).lower():
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "report_site_field_configs" not in raw:
            continue
        rel = str(p.relative_to(ROOT))
        if rel in seen_files:
            continue
        seen_files.add(rel)
        all_issues.extend(audit_report_json(p))

    mismatches = [i for i in all_issues if i.get("problem") == "sitePdfFieldId_label_mismatch"]
    if do_fix:
        total_fixed = 0
        by_path: dict[str, list] = {}
        for m in mismatches:
            full = ROOT / m["file"]
            by_path.setdefault(str(full), []).append(m)
        for fpath, rows in by_path.items():
            n = apply_fixes(Path(fpath), rows)
            if n:
                print(f"fixed {n} mapping(s) in {Path(fpath).relative_to(ROOT)}")
                total_fixed += n
        print(f"\nTotal sitePdfFieldId corrections written: {total_fixed}")
        if total_fixed:
            print("Re-run without --fix to verify remaining mismatches.")
            return 0

    mismatches = [i for i in all_issues if i.get("problem") == "sitePdfFieldId_label_mismatch"]
    by_file: dict[str, list] = {}
    for m in mismatches:
        by_file.setdefault(m["file"], []).append(m)

    print(f"Audited report templates with mappings: {len(seen_files)}")
    print(f"Label/sitePdfFieldId mismatches: {len(mismatches)}")
    print()
    for fname in sorted(by_file.keys()):
        rows = by_file[fname]
        print(f"=== {fname} ({len(rows)} mismatches) ===")
        for r in rows[:15]:
            sug = ", ".join(r.get("suggestedSitePdfFieldIds") or []) or "?"
            print(
                f"  report {r['reportPdfFieldId']} -> site {r['sitePdfFieldId']} "
                f"(actual: {r.get('siteFieldAtPid') or '?'}) "
                f"| label: {r['label']}"
            )
            print(f"    suggested: {sug}")
        if len(rows) > 15:
            print(f"  ... and {len(rows) - 15} more")
        print()

    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
