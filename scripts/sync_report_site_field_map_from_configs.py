#!/usr/bin/env python3
"""Rebuild bindings.report_site_field_map from report_site_field_configs in report templates."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "media" / "file_library" / "templates"

_BARE_SLOT_CONCAT_TEMPLATE_RE = re.compile(r"^(\s*\{\d+\}\s*)+$")


def _coerce_value_template(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).replace("\r\n", "\n").replace("\r", "\n")


def _is_bare_slot_concat_template(template: str) -> bool:
    return bool(_BARE_SLOT_CONCAT_TEMPLATE_RE.match(str(template or "").strip()))


def _normalize_source_row(row: dict, *, default_slot: str = "") -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    site_pid = str(row.get("sitePdfFieldId") or "").strip()
    try:
        site_tid = int(row.get("siteTaskId") or 0)
    except (TypeError, ValueError):
        site_tid = 0
    if not site_pid or site_tid <= 0:
        return None
    slot = str(row.get("slot") or row.get("slotKey") or default_slot or "").strip()
    return {
        "siteTaskId": site_tid,
        "sitePdfFieldId": site_pid,
        "siteTaskCode": str(row.get("siteTaskCode") or "").strip(),
        "siteTaskName": str(row.get("siteTaskName") or "").strip(),
        "slot": slot,
        "label": str(row.get("label") or "").strip(),
    }


def normalize_report_site_field_configs(rows: list) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _ensure(report_pid: str) -> dict[str, Any]:
        key = report_pid.strip()
        if key not in grouped:
            grouped[key] = {
                "reportPdfFieldId": key,
                "sources": [],
                "valueTemplate": "",
                "mapSource": "",
                "mapRuleLabel": "",
            }
            order.append(key)
        return grouped[key]

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        report_pid = str(row.get("reportPdfFieldId") or "").strip()
        if not report_pid:
            continue
        cfg = _ensure(report_pid)
        if isinstance(row.get("sources"), list):
            for i, src in enumerate(row["sources"], start=1):
                norm = _normalize_source_row(src, default_slot=str(i))
                if norm:
                    cfg["sources"].append(norm)
            cfg["valueTemplate"] = _coerce_value_template(
                row.get("valueTemplate") or row.get("value_template")
            )
            cfg["mapSource"] = str(row.get("mapSource") or row.get("map_source") or "").strip()
            cfg["mapRuleLabel"] = str(row.get("mapRuleLabel") or row.get("map_rule_label") or "").strip()
        else:
            norm = _normalize_source_row(row)
            if norm:
                if not norm["slot"]:
                    norm["slot"] = str(len(cfg["sources"]) + 1)
                cfg["sources"].append(norm)
            if row.get("mapSource") or row.get("map_source"):
                cfg["mapSource"] = str(row.get("mapSource") or row.get("map_source") or "").strip()
            if row.get("mapRuleLabel") or row.get("map_rule_label"):
                cfg["mapRuleLabel"] = str(row.get("mapRuleLabel") or row.get("map_rule_label") or "").strip()
            vt = _coerce_value_template(row.get("valueTemplate") or row.get("value_template"))
            if vt:
                cur = str(cfg.get("valueTemplate") or "").strip()
                if not cur:
                    cfg["valueTemplate"] = vt
                elif _is_bare_slot_concat_template(cur) and not _is_bare_slot_concat_template(vt):
                    cfg["valueTemplate"] = vt
                elif not _is_bare_slot_concat_template(cur) and _is_bare_slot_concat_template(vt):
                    pass
                elif len(vt) > len(cur):
                    cfg["valueTemplate"] = vt

    out: list[dict[str, Any]] = []
    for key in order:
        cfg = grouped[key]
        seen_src: set[tuple[int, str]] = set()
        deduped: list[dict[str, Any]] = []
        for i, src in enumerate(cfg["sources"], start=1):
            if not src.get("slot"):
                src["slot"] = str(i)
            sk = (int(src["siteTaskId"]), str(src["sitePdfFieldId"]).lower())
            if sk in seen_src:
                continue
            seen_src.add(sk)
            deduped.append(src)
        cfg["sources"] = deduped
        if cfg["sources"]:
            out.append(cfg)
    return out


def configs_to_flat_rows(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for cfg in normalize_report_site_field_configs(configs):
        report_pid = cfg["reportPdfFieldId"]
        for src in cfg.get("sources") or []:
            if not isinstance(src, dict):
                continue
            flat.append(
                {
                    "reportPdfFieldId": report_pid,
                    "siteTaskId": src["siteTaskId"],
                    "sitePdfFieldId": src["sitePdfFieldId"],
                    "siteTaskCode": src.get("siteTaskCode") or "",
                    "siteTaskName": src.get("siteTaskName") or "",
                    "slot": src.get("slot") or "",
                    "label": src.get("label") or "",
                    "mapSource": cfg.get("mapSource") or "",
                    "mapRuleLabel": cfg.get("mapRuleLabel") or "",
                    "valueTemplate": cfg.get("valueTemplate") or "",
                }
            )
    return flat


def parse_report_site_field_configs_from_bindings(bindings: dict | None) -> list[dict[str, Any]]:
    if not isinstance(bindings, dict):
        return []
    raw_cfg = bindings.get("report_site_field_configs")
    if isinstance(raw_cfg, list) and raw_cfg:
        return normalize_report_site_field_configs(raw_cfg)
    raw_map = bindings.get("report_site_field_map")
    if isinstance(raw_map, list) and raw_map:
        return normalize_report_site_field_configs(raw_map)
    return []


def sync_bindings_report_site_field_sections(bindings: dict | None) -> dict:
    base = dict(bindings) if isinstance(bindings, dict) else {}
    configs = parse_report_site_field_configs_from_bindings(base)
    if configs or "report_site_field_configs" in base or "report_site_field_map" in base:
        base["report_site_field_configs"] = configs
        base["report_site_field_map"] = configs_to_flat_rows(configs)
    return base


def main() -> int:
    only_current = "--all" not in sys.argv
    if only_current:
        paths = sorted(TEMPLATES.rglob("*/report/**/current/*.json"))
    else:
        paths = sorted(TEMPLATES.rglob("*/report/**/*.json"))
    fixed: list[str] = []
    for p in paths:
        if p.name == "_task.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        bindings = data.get("bindings")
        if not isinstance(bindings, dict):
            continue
        if "report_site_field_configs" not in bindings and "report_site_field_map" not in bindings:
            continue
        old_flat = json.dumps(bindings.get("report_site_field_map") or [], ensure_ascii=False, sort_keys=True)
        synced = sync_bindings_report_site_field_sections(bindings)
        new_flat = json.dumps(synced.get("report_site_field_map") or [], ensure_ascii=False, sort_keys=True)
        if old_flat != new_flat:
            data["bindings"] = synced
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            fixed.append(str(p.relative_to(ROOT)))
    print(f"Synced {len(fixed)} report template(s)")
    for fp in fixed:
        print(f"  {fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
