"""报告模板 ↔ 现场记录模板：可视化字段映射（bindings.report_site_field_map / report_site_field_configs）。"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from apps.core.htmlpdf_service import parse_template_json
from apps.core.library_access import (
    library_template_file_accessible_for_task,
    library_user_may_edit_library_task,
)
from apps.core.library_task_template_binding_service import get_task_template_pair
from apps.core.models import LibraryFile, LibraryTask
from apps.core import pipeline_service
from utils.unified_template_fields import materialize_unified_pdf_fields


def _first_template_files_for_task(task: LibraryTask) -> tuple[LibraryFile | None, LibraryFile | None]:
    return get_task_template_pair(task)


def resolve_library_task_for_template_pdf(
    pdf_file_id: int,
    *,
    preferred_task: LibraryTask | None = None,
) -> LibraryTask | None:
    """解析 PDF 所属任务。URL/请求显式传入的任务优先；未指定时再优先报告类（映射配置用）。"""
    if preferred_task is not None and preferred_task.library_files.filter(pk=pdf_file_id).exists():
        return preferred_task
    report_task = (
        LibraryTask.objects.filter(
            library_files__pk=pdf_file_id,
            output_target=LibraryTask.OUTPUT_REPORT,
        )
        .distinct()
        .order_by("code", "id")
        .first()
    )
    if report_task is not None:
        return report_task
    return (
        LibraryTask.objects.filter(library_files__pk=pdf_file_id)
        .order_by("code", "id")
        .first()
    )


def _load_parsed_template_json(lf: LibraryFile | None) -> dict[str, Any] | None:
    if lf is None:
        return None
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
        return parse_template_json(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _pdf_field_id_key(field: dict) -> str:
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    raw = str(src.get("pdfFieldId") or field.get("pdfFieldId") or field.get("id") or "").strip()
    if not raw:
        return ""
    return raw.lower() if raw.lower().startswith("f") and raw[1:].isdigit() else raw


def _parsed_template_steps_list_extended(parsed: dict | None) -> list:
    """统一读取 formSchema.steps / form_schema.steps / 根级 steps。"""
    if not isinstance(parsed, dict):
        return []
    fs = parsed.get("formSchema")
    if isinstance(fs, dict) and isinstance(fs.get("steps"), list):
        return fs["steps"]
    from apps.api.inspection_report_make import _parsed_template_steps_list

    return _parsed_template_steps_list(parsed)


def _collect_template_pdf_field_rows(parsed: dict | None) -> list[dict[str, Any]]:
    """合并 pdf.fields、根级 fields 与 formSchema.steps 中的栏位定义。"""
    if not isinstance(parsed, dict):
        return []
    from apps.api.inspection_report_make import (
        _attach_step_schema_to_pdf_fields,
        _flatten_unified_form_steps_to_fields,
    )

    by_pid: dict[str, dict[str, Any]] = {}
    steps = _parsed_template_steps_list_extended(parsed)

    def _merge_field(raw: dict) -> None:
        if not isinstance(raw, dict):
            return
        pid = _pdf_field_id_key(raw)
        if not pid:
            return
        row = dict(raw)
        prev = by_pid.get(pid)
        if prev is None or len(str(row.get("label") or "")) > len(str(prev.get("label") or "")):
            by_pid[pid] = row

    for block in (
        parsed.get("fields") or [],
        (parsed.get("pdf") or {}).get("fields")
        if isinstance(parsed.get("pdf"), dict)
        else [],
    ):
        for f in materialize_unified_pdf_fields(block or []):
            _merge_field(f)

    for f in _flatten_unified_form_steps_to_fields(steps):
        _merge_field(f)

    rows = list(by_pid.values())
    if steps:
        _attach_step_schema_to_pdf_fields(rows, steps)
    return rows


def _build_report_field_by_pdf_id(parsed: dict | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in _collect_template_pdf_field_rows(parsed):
        pid = str(f.get("pdfFieldId") or "").strip()
        if pid:
            out[pid] = f
    return out


def _build_pdf_field_meta_index(parsed: dict | None) -> dict[str, dict[str, Any]]:
    """pdfFieldId -> submitPath / placeholder 等（合并 steps 上的 source）。"""
    if not isinstance(parsed, dict):
        return {}
    from apps.api.inspection_report_make import _attach_step_schema_to_pdf_fields

    steps = _parsed_template_steps_list_extended(parsed)
    field_rows = [dict(f) for f in _collect_template_pdf_field_rows(parsed) if isinstance(f, dict)]
    if steps:
        _attach_step_schema_to_pdf_fields(field_rows, steps)
    out: dict[str, dict[str, Any]] = {}
    for f in field_rows:
        pid = _pdf_field_id_key(f)
        if not pid:
            continue
        src = f.get("source") if isinstance(f.get("source"), dict) else {}
        placeholder = str(f.get("placeholder") or "").strip()
        title = str(f.get("title") or "").strip()
        out[pid] = {
            "submitPath": str(src.get("submitPath") or f.get("submitPath") or "").strip(),
            "placeholder": placeholder,
            "title": title,
            "label": str(f.get("label") or "").strip(),
        }
    return out


def _normalize_label_key(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def _canonical_pdf_field_id_casing(site_parsed: dict, pid: str) -> str:
    """返回模板中 pdfFieldId 的原始大小写（多为 fNN）。"""
    want = str(pid or "").strip()
    if not want:
        return want
    want_l = want.lower()
    for sf in site_parsed.get("fields") or []:
        if not isinstance(sf, dict):
            continue
        raw = str(sf.get("pdfFieldId") or "").strip()
        if raw.lower() == want_l:
            return raw
    return want


def _resolve_site_pdf_field_id_by_label(
    site_parsed: dict,
    site_pid: str,
    expected_label: str,
) -> str:
    """
    显式映射的 sitePdfFieldId 可能与现场模板 pdfFieldId 编号不一致（label 正确、ID 错位）。
    若配置 label 与 site_pid 处字段 id/label 不符，则按 label 在现场模板中重查 pdfFieldId。
    """
    site_pid = str(site_pid or "").strip()
    expected = str(expected_label or "").strip()
    if not site_pid or not expected or not isinstance(site_parsed, dict):
        return site_pid
    want = _normalize_label_key(expected)
    if not want:
        return site_pid

    pid_label_keys: dict[str, set[str]] = {}

    def _add(pid: str, *texts: str) -> None:
        p = str(pid or "").strip()
        if not p:
            return
        pk = p.lower() if p.lower().startswith("f") and p[1:].isdigit() else p
        bucket = pid_label_keys.setdefault(pk, set())
        for t in texts:
            nk = _normalize_label_key(t)
            if nk:
                bucket.add(nk)

    for sf in site_parsed.get("fields") or []:
        if not isinstance(sf, dict):
            continue
        _add(
            str(sf.get("pdfFieldId") or "").strip(),
            str(sf.get("id") or ""),
            str(sf.get("placeholder") or ""),
            str(sf.get("title") or ""),
        )

    for pid, meta in _build_pdf_field_meta_index(site_parsed).items():
        _add(
            pid,
            str(meta.get("label") or ""),
            str(meta.get("placeholder") or ""),
            str(meta.get("title") or ""),
        )

    try:
        from apps.api.inspection_report_make import (
            _flatten_unified_form_steps_to_fields,
        )

        for sf in _flatten_unified_form_steps_to_fields(_parsed_template_steps_list_extended(site_parsed)):
            if not isinstance(sf, dict):
                continue
            _add(
                str(sf.get("pdfFieldId") or (sf.get("source") or {}).get("pdfFieldId") or "").strip(),
                str(sf.get("label") or ""),
                str(sf.get("hierarchyKey") or ""),
                str(sf.get("id") or ""),
            )
    except Exception:
        pass

    cur_key = site_pid.lower() if site_pid.lower().startswith("f") and site_pid[1:].isdigit() else site_pid
    if want in pid_label_keys.get(cur_key, ()):
        return site_pid
    for pk, keys in pid_label_keys.items():
        if want in keys:
            return _canonical_pdf_field_id_casing(site_parsed, pk)
    return site_pid


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


def _coerce_value_template(raw: Any) -> str:
    """保留模板内缩进与换行，仅统一换行符。"""
    if raw is None:
        return ""
    return str(raw).replace("\r\n", "\n").replace("\r", "\n")


_VALUE_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")
_BARE_SLOT_CONCAT_TEMPLATE_RE = re.compile(r"^(\s*\{\d+\}\s*)+$")


def _value_template_has_slot_placeholders(template: str) -> bool:
    """valueTemplate 须含 {1}/{2}/{f28} 等占位；纯 label 文本不能当作模板。"""
    return bool(_VALUE_TEMPLATE_PLACEHOLDER_RE.search(str(template or "")))


def _is_bare_slot_concat_template(template: str) -> bool:
    """模板仅为 {1}{2}… 连写、无字面分隔符时，不宜直接 render（会变成 10491104115）。"""
    return bool(_BARE_SLOT_CONCAT_TEMPLATE_RE.match(str(template or "").strip()))


def _is_signature_path_mapped_value(val: str) -> bool:
    """现场签字图路径不应映射进报告文本/人员栏。"""
    s = str(val or "").strip()
    if not s:
        return False
    if s.startswith("data:image/"):
        return True
    norm = s.replace("\\", "/").lower()
    if "/signatures/" in norm:
        return True
    if norm.startswith("/media/") and norm.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return True
    return False


def _is_implausible_mapped_site_value(val: str, label: str = "") -> bool:
    """过滤误映射的 label 长句（非现场实测值）。"""
    s = str(val or "").strip()
    if not s:
        return True
    if _is_signature_path_mapped_value(s):
        return True
    lab = str(label or "").strip()
    if lab and s == lab:
        return True
    if len(s) > 48 and "检测条件" in s and "kV" in s and "mA" in s:
        return True
    return False


def _is_boolean_site_pick_value(val: str) -> bool:
    return str(val or "").strip().lower() in ("true", "false")


def _should_skip_boolean_site_pick(
    val: str,
    *,
    report_field_label: str = "",
    source_label: str = "",
) -> bool:
    """检测结果槽误绑 checkbox/radio 时勿把 True/False 串进报告格。"""
    if not _is_boolean_site_pick_value(val):
        return False
    rep = str(report_field_label or "")
    src = str(source_label or "")
    if "单项判定" in rep or (rep.endswith("判定") and "检测条件" not in rep):
        return False
    if "主要检测人员" in rep or "主要检测人员" in src:
        return True
    if "签字" in src or "签名" in src:
        return True
    if "检测结果" in rep or "计算结果" in rep or "报出值" in rep:
        return True
    if "检测条件" in rep:
        return True
    if "检测结果" in src and ("不能分辨" in src or "是否" in src):
        return True
    return False


def _date_slot_part_from_label(label: str, slot: str = "") -> str | None:
    lab = str(label or "").strip()
    if "年月日3" in lab or (slot == "3" and "检测日期" in lab):
        return "day"
    if "年月日2" in lab or (slot == "2" and "检测日期" in lab):
        return "month"
    if "年月日" in lab and "检测日期" in lab:
        return "year"
    return None


def _coerce_site_pick_value_for_date_slot(val: str, *, label: str = "", slot: str = "") -> str:
    """现场日期三格映射：把 ISO/完整中文日期拆成年/月/日片段再写入模板槽。"""
    s = str(val or "").strip()
    if not s:
        return s
    part = _date_slot_part_from_label(label, slot)
    if not part:
        return s
    try:
        from apps.api.inspection_report_make import (
            _format_test_date_ymd_slot_part,
            _parse_loose_datetime_for_submit,
        )
    except ImportError:
        return s
    dt = _parse_loose_datetime_for_submit(s)
    if dt is None:
        if part == "year" and s.isdigit() and len(s) == 4:
            return s
        if part in ("month", "day") and s.isdigit() and len(s) <= 2:
            return s
        return s
    return _format_test_date_ymd_slot_part(part, dt)


def _compose_chinese_test_date_from_parts(parts: list[str]) -> str:
    if len(parts) != 3:
        return ""
    y, m, d = (str(p or "").strip() for p in parts)
    if not (y and m and d):
        return ""
    if "年" in y or "月" in m or "日" in d:
        return "".join(parts)
    try:
        return f"{int(y):04d}年{int(m)}月{int(d)}日"
    except (TypeError, ValueError):
        return f"{y}年{m}月{d}日"


def _infer_slot_unit_suffix(label: str) -> str:
    lab = str(label or "").strip()
    if not lab:
        return ""
    if lab.endswith("kV") or "算法kV" in lab or lab in ("kVmAs", "kVmAs2", "kVmAs3"):
        return "kV"
    if "mAs" in lab and "kV" not in lab:
        return "mAs"
    if "mA" in lab:
        return "mA"
    if "层厚" in lab or lab.endswith("mm") or "smm" in lab or "s(T)mm" in lab:
        return "mm"
    if lab.endswith("s") or "(mAs)s" in lab or "s(T)" in lab:
        return "s"
    if lab.endswith("%"):
        return "%"
    return ""


def _format_bare_slot_concat_value(
    picked_parts: list[str],
    sources: list[dict] | None,
    *,
    report_field_label: str = "",
) -> str:
    """检测条件等多槽连写模板：按来源语义加单位/空格，避免数字粘成一串。"""
    srcs = sources if isinstance(sources, list) else []
    is_condition = "检测条件" in str(report_field_label or "") or any(
        "检测条件" in str((s or {}).get("label") or "") for s in srcs
    )
    is_rated = "额定参数" in str(report_field_label or "") or any(
        "额定参数" in str((s or {}).get("label") or "") for s in srcs
    )
    chunks: list[str] = []
    for i, raw in enumerate(picked_parts):
        val = str(raw or "").strip()
        if not val:
            continue
        lab = str((srcs[i] if i < len(srcs) else {}).get("label") or "")
        if _is_implausible_mapped_site_value(val, lab):
            continue
        if is_condition or is_rated:
            unit = _infer_slot_unit_suffix(lab)
            if unit and not val.endswith(unit):
                chunks.append(f"{val}{unit}")
            else:
                chunks.append(val)
        else:
            chunks.append(val)
    if is_rated and len(chunks) == 2:
        return f"{chunks[0]}/{chunks[1]}"
    if is_condition and len(chunks) >= 3:
        return " ".join(chunks[:3])
    return " ".join(chunks)


def _compose_report_site_field_final_value(
    picked_parts: list[str],
    slot_values: dict[str, str],
    template: str,
    *,
    sources: list[dict] | None = None,
    report_field_label: str = "",
    value_cases: list[dict] | None = None,
    site_parsed_resolver=None,
    inspection_type: str = "acceptance",
) -> tuple[str, str | None]:
    """
    多来源合并：有 valueCases 时按条件匹配回填文本；否则有合法占位模板则渲染；
    单来源且无模板时直接回填；多来源且既无条件也无模板时留空。
    返回 (回填文本, 同行单项判定) —— 后者仅在有测量值判定标准时给出。
    """
    cases_out, peer_verdict = _resolve_report_mapping_value_cases(
        value_cases,
        slot_values,
        sources=sources,
        site_parsed_resolver=site_parsed_resolver,
        inspection_type=inspection_type,
    )
    if cases_out is not None:
        return cases_out, peer_verdict

    tpl = _coerce_value_template(template)
    if tpl.strip() and _value_template_has_slot_placeholders(tpl):
        if _is_bare_slot_concat_template(tpl):
            formatted = _format_bare_slot_concat_value(
                picked_parts,
                sources,
                report_field_label=report_field_label,
            )
            if formatted:
                return formatted, None
            if "检测日期" in str(report_field_label or "") and len(picked_parts) == 3:
                date_cn = _compose_chinese_test_date_from_parts(picked_parts)
                if date_cn:
                    return date_cn, None
        return render_report_value_template(tpl, slot_values), None
    if len(picked_parts) == 1:
        return picked_parts[0], None
    return "", None


_MAPPING_CASE_SLOT_REF_RE = re.compile(r"\{([^{}]+)\}")


def _normalize_value_cases(rows: list | None) -> list[dict[str, Any]]:
    from utils.conditional_field_rules import normalize_rule_list

    return normalize_rule_list(rows if isinstance(rows, list) else [])


def _coerce_mapping_case_operand(raw: Any) -> Any:
    if isinstance(raw, bool):
        return raw
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in ("true", "1", "yes", "y", "on", "是", "勾选", "☑", "✓", "√"):
        return True
    if low in ("false", "0", "no", "n", "off", "否", "未勾选", "□", "☐"):
        return False
    try:
        if re.fullmatch(r"-?\d+\.?\d*", s):
            return float(s) if "." in s else int(s)
    except (TypeError, ValueError):
        pass
    return s


def _safe_slot_var_key(slot_key: str) -> str:
    sk = str(slot_key or "").strip()
    if not sk:
        return "_s0"
    if sk.isdigit():
        return f"_s{sk}"
    if re.fullmatch(r"f\d+", sk, re.I):
        return sk.lower()
    safe = re.sub(r"\W+", "_", sk).strip("_")
    if not safe:
        return "_sx"
    if safe[0].isdigit():
        return f"_s_{safe}"
    return safe


def _slot_values_to_case_vm(slot_values: Mapping[str, Any]) -> dict[str, Any]:
    vm: dict[str, Any] = {}
    for k, v in (slot_values or {}).items():
        sk = str(k or "").strip()
        if not sk:
            continue
        coerced = _coerce_mapping_case_operand(v)
        vm[_safe_slot_var_key(sk)] = coerced
    return vm


def _preprocess_mapping_case_condition(condition: str) -> str:
    text = str(condition or "")

    def _repl(m: re.Match) -> str:
        return _safe_slot_var_key(m.group(1))

    return _MAPPING_CASE_SLOT_REF_RE.sub(_repl, text)


_BARE_SLOT_COND_RE = re.compile(r"^\{([^{}]+)\}$")
_NEGATED_SLOT_COND_RE = re.compile(
    r"^(?:!\s*\{([^{}]+)\}|(?:not|非)\s+\{([^{}]+)\})$",
    re.IGNORECASE,
)


def _normalize_mapping_case_condition(condition: str) -> str:
    """
    将简写条件转为可求值表达式：
    - ``{1}`` → ``_s1 == true``（勾选/真值）
    - ``!{1}`` / ``not {1}`` / ``非{1}`` → ``_s1 == false``
    - 其它表达式仅替换 ``{slot}`` 为安全变量名
    """
    text = str(condition or "").strip()
    if not text:
        return ""
    m_neg = _NEGATED_SLOT_COND_RE.match(text)
    if m_neg:
        slot = (m_neg.group(1) or m_neg.group(2) or "").strip()
        if slot:
            return f"{_safe_slot_var_key(slot)} == false"
    m_pos = _BARE_SLOT_COND_RE.match(text)
    if m_pos:
        slot = str(m_pos.group(1) or "").strip()
        if slot:
            return f"{_safe_slot_var_key(slot)} == true"
    return _preprocess_mapping_case_condition(text)


def _mapping_case_rule_has_output(rule: dict) -> bool:
    if not isinstance(rule, dict):
        return False
    if str(rule.get("condition") or "").strip():
        return "expression" in rule or "formula" in rule
    return False


def _find_source_for_slot(sources: list | None, slot: str) -> dict | None:
    slot_s = str(slot or "").strip()
    if not slot_s:
        return None
    for src in sources or []:
        if not isinstance(src, dict):
            continue
        if str(src.get("slot") or "").strip() == slot_s:
            return src
    try:
        idx = int(slot_s) - 1
        if 0 <= idx < len(sources or []):
            row = sources[idx]
            return row if isinstance(row, dict) else None
    except (TypeError, ValueError):
        pass
    return None


def _inspection_type_from_payload(source_data: dict | None) -> str:
    if not isinstance(source_data, dict):
        return "acceptance"
    tt = str(
        source_data.get("testType")
        or (source_data.get("reportInfo") or {}).get("testType")
        or ""
    ).strip().lower()
    if tt in ("status", "state") or "状态" in tt:
        return "status"
    return "acceptance"


def _site_field_judgment_criterion(
    site_parsed: dict | None,
    site_pdf_field_id: str,
    *,
    inspection_type: str = "acceptance",
) -> str:
    if not site_parsed or not site_pdf_field_id:
        return ""
    from apps.api.inspection_report_make import _flatten_unified_form_steps_to_fields

    want = str(site_pdf_field_id).strip().lower()
    for sf in _flatten_unified_form_steps_to_fields(_parsed_template_steps_list_extended(site_parsed)):
        pid = str(sf.get("pdfFieldId") or "").strip().lower()
        if pid != want:
            continue
        jc = sf.get("judgmentCriteriaByTestType")
        if not isinstance(jc, dict):
            return ""
        it = str(inspection_type or "").strip().lower()
        if "状态" in it or it == "status":
            return str(jc.get("status") or jc.get("acceptance") or "").strip()
        return str(jc.get("acceptance") or jc.get("status") or "").strip()
    return ""


_SINGLE_SLOT_VALUE_CASE_EXPR = re.compile(r"^\{(\d+)\}$")


def _get_site_field_meta_from_parsed(site_parsed: dict | None, site_pdf_field_id: str) -> dict | None:
    if not site_parsed or not site_pdf_field_id:
        return None
    from apps.api.inspection_report_make import _flatten_unified_form_steps_to_fields

    want = str(site_pdf_field_id).strip().lower()
    for sf in _flatten_unified_form_steps_to_fields(_parsed_template_steps_list_extended(site_parsed)):
        pid = str(sf.get("pdfFieldId") or "").strip().lower()
        if pid == want:
            return sf
    return None


def _slot_mapping_value_is_truthy(val: object) -> bool:
    if val is True:
        return True
    if val is False or val is None:
        return False
    s = str(val).strip().lower()
    if s in ("false", "0", "no", "n", "off", "否", "未勾选", "□", "☐"):
        return False
    if s in ("true", "1", "yes", "y", "on", "是", "勾选", "☑", "✓", "√"):
        return True
    return bool(s)


def _is_checkbox_site_field(meta: dict | None, src: dict | None = None) -> bool:
    ftype = str((meta or {}).get("type") or "").lower()
    if ftype in ("boolean", "check"):
        return True
    src_ft = str((src or {}).get("fieldType") or "").lower()
    return src_ft == "check"


def _peer_verdict_from_checkbox_criterion(
    *,
    slot_asserts_true: bool,
    criterion: str,
) -> str | None:
    """勾选框判定公式 True/False：勾选即 True，未勾选即 False。"""
    crit = str(criterion or "").strip().lower()
    if crit not in ("true", "false"):
        return None
    expect_pass_when_checked = crit == "true"
    ok = slot_asserts_true if expect_pass_when_checked else not slot_asserts_true
    return "合格" if ok else "不合格"


def _peer_verdict_from_condition_slot(
    slot_ref: str,
    *,
    slot_asserts_true: bool,
    slot_values: Mapping[str, str],
    sources: list | None,
    site_parsed_resolver,
    inspection_type: str = "acceptance",
) -> str | None:
    from apps.api.inspection_report_make import _normalize_pass_fail_verdict_text

    src = _find_source_for_slot(sources, slot_ref)
    if not src:
        return None

    raw_val = slot_values.get(slot_ref) or slot_values.get(str(slot_ref))
    verdict_text = _normalize_pass_fail_verdict_text(raw_val)
    if verdict_text and slot_asserts_true:
        return verdict_text

    site_tid = int(src.get("siteTaskId") or 0)
    site_code = str(src.get("siteTaskCode") or "")
    site_parsed = site_parsed_resolver(site_tid, site_code) if site_parsed_resolver else None
    site_pid = str(src.get("sitePdfFieldId") or "").strip()
    meta = _get_site_field_meta_from_parsed(site_parsed, site_pid) or {}

    criterion = _site_field_judgment_criterion(site_parsed, site_pid, inspection_type=inspection_type)
    if _is_checkbox_site_field(meta, src):
        checkbox_verdict = _peer_verdict_from_checkbox_criterion(
            slot_asserts_true=slot_asserts_true,
            criterion=criterion or "true",
        )
        if checkbox_verdict in ("合格", "不合格"):
            return checkbox_verdict

    fv = meta.get("fieldVerdict")
    if isinstance(fv, dict):
        kind = str(fv.get("kind") or "").lower()
        if kind == "exclusivecheck":
            pid_low = site_pid.lower()
            pass_ids = {str(x or "").strip().lower() for x in (fv.get("passWhenCheckedPdfFieldIds") or [])}
            fail_ids = {str(x or "").strip().lower() for x in (fv.get("failWhenCheckedPdfFieldIds") or [])}
            if slot_asserts_true:
                if pid_low in pass_ids:
                    return "合格"
                if pid_low in fail_ids:
                    return "不合格"
            else:
                if pid_low in pass_ids:
                    return "不合格"
                if pid_low in fail_ids:
                    return "合格"

    if str(meta.get("type") or "").lower() in ("verdict",):
        if slot_asserts_true and verdict_text:
            return verdict_text

    if criterion and slot_asserts_true:
        try:
            from utils.verdict_from_criterion import parse_first_number, verdict_from_measurement
        except ImportError:
            return None
        measured = str(raw_val or "").strip()
        if parse_first_number(measured) is not None:
            verdict = verdict_from_measurement(measured, criterion)
            if verdict in ("合格", "不合格"):
                return verdict
    return None


_BARE_NEG_SLOT_COND_RE = re.compile(
    r"^(?:!\s*\{([^{}]+)\}|(?:not|非)\s+\{([^{}]+)\})$",
    re.IGNORECASE,
)


def _peer_verdict_from_value_case_condition(
    condition: str,
    *,
    slot_values: Mapping[str, str],
    sources: list | None,
    site_parsed_resolver,
    inspection_type: str = "acceptance",
) -> str | None:
    """从条件侧引用来源推断单项判定（勾选/真 → 合格或不合格）。"""
    cond = str(condition or "").strip()
    if not cond:
        return None

    m_neg = _BARE_NEG_SLOT_COND_RE.match(cond)
    if m_neg:
        slot_ref = str(m_neg.group(1) or m_neg.group(2) or "").strip()
        if slot_ref:
            return _peer_verdict_from_condition_slot(
                slot_ref,
                slot_asserts_true=False,
                slot_values=slot_values,
                sources=sources,
                site_parsed_resolver=site_parsed_resolver,
                inspection_type=inspection_type,
            )
        return None

    m_pos = _SINGLE_SLOT_VALUE_CASE_EXPR.match(cond)
    if m_pos:
        slot_ref = str(m_pos.group(1) or "").strip()
        if slot_ref:
            return _peer_verdict_from_condition_slot(
                slot_ref,
                slot_asserts_true=True,
                slot_values=slot_values,
                sources=sources,
                site_parsed_resolver=site_parsed_resolver,
                inspection_type=inspection_type,
            )
        return None

    slot_refs = re.findall(r"\{(\d+)\}", cond)
    if len(slot_refs) == 1 and not re.search(r"!\s*\{", cond) and not re.search(r"\bnot\b", cond, re.I):
        slot_ref = slot_refs[0]
        return _peer_verdict_from_condition_slot(
            slot_ref,
            slot_asserts_true=_slot_mapping_value_is_truthy(slot_values.get(slot_ref)),
            slot_values=slot_values,
            sources=sources,
            site_parsed_resolver=site_parsed_resolver,
            inspection_type=inspection_type,
        )
    return None


def _peer_verdict_from_value_case_expression(
    rendered: str,
    expression: str,
    *,
    slot_values: Mapping[str, str],
    sources: list | None,
    site_parsed_resolver,
    inspection_type: str = "acceptance",
) -> str | None:
    """从写入表达式中的 {N} 测量值 + judgmentCriteriaByTestType 推断单项判定。"""
    expr = str(expression or "").strip()
    if not expr:
        return None

    slot_refs: list[str] = []
    slot_match = _SINGLE_SLOT_VALUE_CASE_EXPR.match(expr)
    if slot_match:
        slot_refs = [slot_match.group(1)]
    else:
        slot_refs = re.findall(r"\{(\d+)\}", expr)
    if not slot_refs:
        return None

    try:
        from utils.verdict_from_criterion import verdict_from_measurement
    except ImportError:
        return None

    for slot_ref in slot_refs:
        measured = str(slot_values.get(slot_ref) or rendered or "").strip()
        src = _find_source_for_slot(sources, slot_ref)
        if not src:
            continue
        verdict_text = None
        try:
            from apps.api.inspection_report_make import _normalize_pass_fail_verdict_text
            verdict_text = _normalize_pass_fail_verdict_text(measured)
        except ImportError:
            pass
        if verdict_text:
            return verdict_text
        site_tid = int(src.get("siteTaskId") or 0)
        site_code = str(src.get("siteTaskCode") or "")
        site_parsed = site_parsed_resolver(site_tid, site_code) if site_parsed_resolver else None
        site_pid = str(src.get("sitePdfFieldId") or "").strip()
        criterion = _site_field_judgment_criterion(
            site_parsed,
            site_pid,
            inspection_type=inspection_type,
        )
        if not criterion:
            continue
        verdict = verdict_from_measurement(measured, criterion)
        if verdict in ("合格", "不合格"):
            return verdict
    return None


def _compute_peer_verdict_for_matched_value_case_rule(
    rule: dict,
    rendered: str,
    *,
    slot_values: Mapping[str, str],
    sources: list | None,
    site_parsed_resolver,
    inspection_type: str = "acceptance",
) -> str | None:
    """合并条件侧与表达式侧推断的单项判定。"""
    if not isinstance(rule, dict):
        return None
    expr = str(rule.get("expression") if rule.get("expression") is not None else rule.get("formula") or "")
    cond = str(rule.get("condition") or "")

    from_expr = _peer_verdict_from_value_case_expression(
        rendered,
        expr,
        slot_values=slot_values,
        sources=sources,
        site_parsed_resolver=site_parsed_resolver,
        inspection_type=inspection_type,
    )
    from_cond = _peer_verdict_from_value_case_condition(
        cond,
        slot_values=slot_values,
        sources=sources,
        site_parsed_resolver=site_parsed_resolver,
        inspection_type=inspection_type,
    )
    if from_expr in ("合格", "不合格"):
        return from_expr
    if from_cond in ("合格", "不合格"):
        return from_cond
    return None


def _compute_peer_verdict_for_value_case_expression(
    rendered: str,
    expression: str,
    *,
    slot_values: Mapping[str, str],
    sources: list | None,
    site_parsed_resolver,
    inspection_type: str = "acceptance",
) -> str | None:
    """兼容旧调用：仅表达式侧推断。"""
    return _peer_verdict_from_value_case_expression(
        rendered,
        expression,
        slot_values=slot_values,
        sources=sources,
        site_parsed_resolver=site_parsed_resolver,
        inspection_type=inspection_type,
    )


def _write_peer_verdict_for_report_result_field(
    value_mapping: dict,
    report_field: dict | None,
    report_pdf_field_id: str,
    peer_verdict: str,
    report_parsed_template: dict | None = None,
) -> None:
    """将合格/不合格写入报告同行「单项判定」语义键与合成 verdict 域。"""
    from apps.api.inspection_report_make import (
        _field_semantic_candidate_keys,
        _qc_result_row_base_from_field,
    )

    verdict = str(peer_verdict or "").strip()
    if verdict not in ("合格", "不合格") or not isinstance(value_mapping, dict):
        return
    pid = str(report_pdf_field_id or "").strip()
    if pid:
        value_mapping[f"_synVerdict_{pid}"] = verdict
    if isinstance(report_field, dict):
        base = _qc_result_row_base_from_field(report_field)
        if base:
            value_mapping[f"{base}_单项判定"] = verdict
    if isinstance(report_parsed_template, dict):
        for vf in _collect_template_pdf_field_rows(report_parsed_template):
            if str(vf.get("_peerResultPdfFieldId") or "").strip() != pid:
                continue
            vpid = str(vf.get("pdfFieldId") or "").strip()
            if vpid:
                value_mapping[vpid] = verdict
            for k in _field_semantic_candidate_keys(vf):
                if k:
                    value_mapping[k] = verdict


def _resolve_report_mapping_value_cases(
    value_cases: list[dict] | None,
    slot_values: Mapping[str, str],
    *,
    sources: list[dict] | None = None,
    site_parsed_resolver=None,
    inspection_type: str = "acceptance",
) -> tuple[str | None, str | None]:
    """
    按顺序匹配 valueCases：condition 为真时采用 expression 作为回填文本（支持 {1}/{2} 占位）。
    判断条件可简写为 ``{1}``（真/勾选）或 ``!{1}``（假/未勾选）；全部未命中时返回空字符串。
    """
    cases = _normalize_value_cases(value_cases)
    if not cases:
        return None, None
    vm = _slot_values_to_case_vm(slot_values)
    had_actionable = False
    try:
        from utils.dynamic_form_expression import eval_computed_formula
    except ImportError:
        eval_computed_formula = None  # type: ignore[assignment]

    for rule in cases:
        if not isinstance(rule, dict):
            continue
        cond_raw = str(rule.get("condition") or "").strip()
        if not cond_raw:
            continue
        if not _mapping_case_rule_has_output(rule):
            continue
        had_actionable = True
        expr = str(rule.get("expression") if rule.get("expression") is not None else rule.get("formula") or "")
        if eval_computed_formula is None:
            continue
        cond = _normalize_mapping_case_condition(cond_raw)
        try:
            matched = eval_computed_formula(cond, vm)
        except Exception:
            matched = False
        if matched is True or (matched not in (None, False) and bool(matched)):
            rendered = render_report_value_template(expr, dict(slot_values))
            peer_verdict = _compute_peer_verdict_for_matched_value_case_rule(
                rule,
                rendered,
                slot_values=slot_values,
                sources=sources,
                site_parsed_resolver=site_parsed_resolver,
                inspection_type=inspection_type,
            )
            return rendered, peer_verdict
    if had_actionable:
        return "", None
    return None, None


def normalize_report_site_field_configs(rows: list) -> list[dict[str, Any]]:
    """
    统一为报告栏位映射配置（可多来源 + 文本模板）：
    - 新格式：{ reportPdfFieldId, sources: [...], valueTemplate }
    - 旧格式扁平行：每条视为一个来源，同报告栏位合并
    """
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _ensure(report_pid: str) -> dict[str, Any]:
        key = report_pid.strip()
        if key not in grouped:
            grouped[key] = {
                "reportPdfFieldId": key,
                "sources": [],
                "valueTemplate": "",
                "valueCases": [],
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
            cfg["valueCases"] = _normalize_value_cases(row.get("valueCases") or row.get("value_cases"))
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
            vc = _normalize_value_cases(row.get("valueCases") or row.get("value_cases"))
            if vc:
                cfg["valueCases"] = vc

    out: list[dict[str, Any]] = []
    for key in order:
        cfg = grouped[key]
        if cfg.get("valueCases") is None:
            cfg["valueCases"] = []
        else:
            cfg["valueCases"] = _normalize_value_cases(cfg.get("valueCases"))
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
    """兼容仍读取 report_site_field_map 扁平原型的调用方。"""
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
                    "valueCases": cfg.get("valueCases") or [],
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


def render_report_value_template(template: str, slot_values: dict[str, str]) -> str:
    """将 {1}、{2}、{f3} 等占位替换为来源取值。"""
    text = str(template or "")
    if not text.strip() or not slot_values:
        return text
    keys = sorted({str(k) for k in slot_values if str(k)}, key=lambda x: (-len(x), x))
    for key in keys:
        val = str(slot_values.get(key) or "")
        for pat in (f"{{{key}}}", f"{{{{{key}}}}}"):
            text = text.replace(pat, val)
    return text


def merge_effective_report_site_field_configs(
    explicit_configs: list[dict[str, Any]],
    inferred_configs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    explicit_norm = normalize_report_site_field_configs(explicit_configs)
    inferred_norm = normalize_report_site_field_configs(inferred_configs)
    explicit_ids = {str(c["reportPdfFieldId"]).strip().lower() for c in explicit_norm}
    out = [dict(c) for c in explicit_norm]
    for cfg in inferred_norm:
        rid = str(cfg.get("reportPdfFieldId") or "").strip().lower()
        if not rid or rid in explicit_ids:
            continue
        out.append(
            dict(
                cfg,
                mapSource=str(cfg.get("mapSource") or "inferred"),
            )
        )
    return out


def infer_report_site_field_configs(
    report_parsed: dict | None,
    site_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    按与报告回填一致的规则推断映射配置（每报告栏位一条，可含多来源时的首条匹配）。
    """
    report_index = _build_pdf_field_meta_index(report_parsed)
    if not report_index:
        return []

    flat: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    for st in site_tasks or []:
        if not isinstance(st, dict):
            continue
        try:
            site_tid = int(st.get("id") or 0)
        except (TypeError, ValueError):
            site_tid = 0
        if site_tid <= 0:
            continue
        json_id = st.get("templateJsonId")
        if not json_id:
            continue
        site_lf = LibraryFile.objects.filter(
            pk=int(json_id), category=LibraryFile.CATEGORY_TEMPLATE
        ).first()
        site_parsed = _load_parsed_template_json(site_lf)
        site_index = _build_pdf_field_meta_index(site_parsed)
        if not site_index:
            continue

        site_by_submit: dict[str, list[str]] = {}
        site_by_label: dict[str, list[str]] = {}
        for site_pid, meta in site_index.items():
            sp = meta.get("submitPath") or ""
            if sp:
                site_by_submit.setdefault(sp, []).append(site_pid)
            for lk in (meta.get("placeholder"), meta.get("title"), meta.get("label")):
                nk = _normalize_label_key(lk)
                if nk:
                    site_by_label.setdefault(nk, []).append(site_pid)

        st_code = str(st.get("code") or "").strip()
        st_name = str(st.get("name") or "").strip()

        for report_pid, rmeta in report_index.items():
            matched_site_pids: list[tuple[str, str, str]] = []

            rsp = rmeta.get("submitPath") or ""
            if rsp and rsp in site_by_submit:
                for site_pid in site_by_submit[rsp]:
                    matched_site_pids.append((site_pid, "submitPath", rsp))

            if not matched_site_pids:
                for lk in (rmeta.get("placeholder"), rmeta.get("title"), rmeta.get("label")):
                    nk = _normalize_label_key(lk)
                    if nk and nk in site_by_label:
                        for site_pid in site_by_label[nk]:
                            label = str(lk or "").strip()
                            matched_site_pids.append((site_pid, "placeholder", label))
                        break

            for site_pid, map_source, rule_label in matched_site_pids:
                key = (report_pid, site_tid, site_pid)
                if key in seen:
                    continue
                seen.add(key)
                flat.append(
                    {
                        "reportPdfFieldId": report_pid,
                        "siteTaskId": site_tid,
                        "siteTaskCode": st_code,
                        "siteTaskName": st_name,
                        "sitePdfFieldId": site_pid,
                        "mapSource": map_source,
                        "mapRuleLabel": rule_label,
                    }
                )
    configs = normalize_report_site_field_configs(flat)
    for cfg in configs:
        if not cfg.get("mapSource"):
            cfg["mapSource"] = "inferred"
    return configs


def merge_effective_report_site_field_map(
    explicit_rows: list[dict[str, Any]],
    inferred_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """扁平原型兼容：由 effective configs 展开。"""
    return configs_to_flat_rows(
        merge_effective_report_site_field_configs(explicit_rows, inferred_rows)
    )


def _load_report_site_field_configs_from_json_file(
    json_lf: LibraryFile | None,
) -> list[dict[str, Any]]:
    if json_lf is None:
        return []
    try:
        path = pipeline_service.library_absolute_path(json_lf.relative_path)
        parsed = parse_template_json(path.read_text(encoding="utf-8", errors="replace"))
        b = parsed.get("bindings")
        if isinstance(b, dict):
            return parse_report_site_field_configs_from_bindings(b)
    except Exception:
        pass
    return []


def _serialize_task_brief(task: LibraryTask) -> dict[str, Any]:
    pdf_lf, json_lf = _first_template_files_for_task(task)
    return {
        "id": task.pk,
        "code": task.code,
        "name": task.name,
        "outputTarget": task.output_target,
        "templatePdfId": pdf_lf.pk if pdf_lf else None,
        "templatePdfName": pdf_lf.original_name if pdf_lf else "",
        "templateJsonId": json_lf.pk if json_lf else None,
        "templateJsonName": json_lf.original_name if json_lf else "",
    }


def build_report_task_template_context(
    user,
    *,
    library_task_id: int | None = None,
    template_json_file_id: int | None = None,
    template_pdf_file_id: int | None = None,
) -> dict[str, Any]:
    preferred = (
        LibraryTask.objects.filter(pk=library_task_id).first()
        if library_task_id
        else None
    )
    task = preferred
    if template_pdf_file_id:
        task = resolve_library_task_for_template_pdf(
            int(template_pdf_file_id), preferred_task=preferred
        )
    if task is None:
        return {"ok": False, "error": "任务模板不存在"}
    if not library_user_may_edit_library_task(user, task):
        return {"ok": False, "error": "无权维护该任务模板"}
    site_tasks: list[dict[str, Any]] = []
    bindings: dict[str, Any] = {}
    report_site_field_configs: list[dict[str, Any]] = []

    is_report = task.output_target == LibraryTask.OUTPUT_REPORT
    if is_report:
        for st in task.report_source_tasks.filter(
            output_target=LibraryTask.OUTPUT_SITE_RECORD
        ).order_by("code", "id"):
            site_tasks.append(_serialize_task_brief(st))

    json_lf = None
    if template_json_file_id:
        json_lf = LibraryFile.objects.filter(
            pk=template_json_file_id, category=LibraryFile.CATEGORY_TEMPLATE
        ).first()
    if json_lf is None and is_report:
        _pdf_lf, json_lf = get_task_template_pair(task)
    report_parsed: dict | None = None
    if json_lf and library_template_file_accessible_for_task(user, task, json_lf):
        report_parsed = _load_parsed_template_json(json_lf)
        report_site_field_configs = _load_report_site_field_configs_from_json_file(json_lf)
        if report_site_field_configs:
            bindings = {
                "report_site_field_configs": report_site_field_configs,
                "report_site_field_map": configs_to_flat_rows(report_site_field_configs),
            }

    inferred_report_site_field_configs: list[dict[str, Any]] = []
    effective_report_site_field_configs: list[dict[str, Any]] = []
    if is_report and site_tasks and report_parsed:
        inferred_report_site_field_configs = infer_report_site_field_configs(
            report_parsed, site_tasks
        )
        effective_report_site_field_configs = merge_effective_report_site_field_configs(
            report_site_field_configs, inferred_report_site_field_configs
        )

    mapping_hint = ""
    if is_report and not site_tasks:
        mapping_hint = (
            "当前报告模板尚未关联现场记录来源。请在「任务模板库」编辑该报告，"
            "在「报告来源现场记录」中勾选对应的现场记录任务模板。"
        )

    return {
        "ok": True,
        "task": _serialize_task_brief(task),
        "siteTasks": site_tasks,
        "bindings": bindings,
        "reportSiteFieldConfigs": report_site_field_configs,
        "reportSiteFieldMap": configs_to_flat_rows(report_site_field_configs),
        "inferredReportSiteFieldConfigs": inferred_report_site_field_configs,
        "inferredReportSiteFieldMap": configs_to_flat_rows(inferred_report_site_field_configs),
        "effectiveReportSiteFieldConfigs": effective_report_site_field_configs,
        "effectiveReportSiteFieldMap": configs_to_flat_rows(effective_report_site_field_configs),
        "isReportTemplate": is_report,
        "mappingReady": is_report and bool(site_tasks),
        "mappingHint": mapping_hint,
        "resolvedLibraryTaskId": task.pk,
    }


def build_site_template_bundle(user, *, site_task_id: int) -> dict[str, Any]:
    task = LibraryTask.objects.filter(pk=site_task_id).first()
    if task is None:
        return {"ok": False, "error": "现场记录任务不存在"}
    if task.output_target != LibraryTask.OUTPUT_SITE_RECORD:
        return {"ok": False, "error": "所选任务不是现场记录模板"}
    if not library_user_may_edit_library_task(user, task):
        return {"ok": False, "error": "无权访问该现场记录模板"}

    pdf_lf, json_lf = _first_template_files_for_task(task)
    if pdf_lf is None or not library_template_file_accessible_for_task(user, task, pdf_lf):
        return {"ok": False, "error": "该现场记录任务尚未绑定模板 PDF"}
    fields: list[dict[str, Any]] = []
    if json_lf and library_template_file_accessible_for_task(user, task, json_lf):
        try:
            path = pipeline_service.library_absolute_path(json_lf.relative_path)
            parsed = parse_template_json(path.read_text(encoding="utf-8", errors="replace"))
            fields = materialize_unified_pdf_fields(parsed.get("fields") or [])
        except Exception:
            fields = []

    from django.urls import reverse

    return {
        "ok": True,
        "task": _serialize_task_brief(task),
        "pdfUrl": reverse("htmlpdf_template_file", kwargs={"pk": pdf_lf.pk}),
        "fields": [
            {
                "pdfFieldId": str(f.get("pdfFieldId") or ""),
                "page": int(f.get("page") or 1),
                "x": float(f.get("x") or 0),
                "y": float(f.get("y") or 0),
                "w": float(f.get("w") or 0),
                "h": float(f.get("h") or 0),
                "placeholder": str(f.get("placeholder") or f.get("title") or ""),
                "fieldType": str(f.get("fieldType") or "text"),
            }
            for f in fields
            if isinstance(f, dict) and str(f.get("pdfFieldId") or "").strip()
        ],
    }


def normalize_report_site_field_map(rows: list) -> list[dict[str, Any]]:
    return configs_to_flat_rows(normalize_report_site_field_configs(rows))


def merge_report_site_map_into_bindings(
    bindings: dict | None, mappings: list
) -> dict:
    base = dict(bindings) if isinstance(bindings, dict) else {}
    configs = normalize_report_site_field_configs(mappings)
    base["report_site_field_configs"] = configs
    base["report_site_field_map"] = configs_to_flat_rows(configs)
    return base


def sync_bindings_report_site_field_sections(bindings: dict | None) -> dict:
    """
    以 report_site_field_configs 为权威源，重建扁平 report_site_field_map，
    避免两节 valueTemplate 不一致（编辑器改 configs 后 flat 仍留旧 {1}{2}…）。
    """
    base = dict(bindings) if isinstance(bindings, dict) else {}
    configs = parse_report_site_field_configs_from_bindings(base)
    if configs or "report_site_field_configs" in base or "report_site_field_map" in base:
        base["report_site_field_configs"] = configs
        base["report_site_field_map"] = configs_to_flat_rows(configs)
    return base


def apply_report_site_field_map_to_value_mapping(
    value_mapping: dict,
    source_data: dict,
    bindings: dict | None,
    *,
    report_parsed_template: dict | None = None,
    ordered_submit_payloads: Sequence[dict] | None = None,
    only_peer_verdicts: bool = False,
) -> None:
    """
    报告回填：显式映射优先于名称模糊匹配。
    从提交 JSON 按现场记录栏位的 submitPath/语义键取值，写入报告栏位对应语义键。
    """
    if not isinstance(value_mapping, dict) or not isinstance(source_data, dict):
        return
    if not isinstance(bindings, dict):
        return
    configs = parse_report_site_field_configs_from_bindings(bindings)
    if not configs:
        return

    from apps.api.inspection_report_make import (
        _apply_template_field_sources_to_mapping,
        _field_semantic_candidate_keys,
        _is_report_preserve_original_pdf_field,
        _REPORT_SUMMARY_OVERLAY_MANAGED_KEYS,
    )

    report_by_pdf = _build_report_field_by_pdf_id(report_parsed_template)

    site_parsed_cache: dict[tuple[int, str], dict | None] = {}
    site_payload_cache: dict[int, dict] = {}

    def _resolve_site_library_task(site_tid: int, site_code: str = ""):
        task = LibraryTask.objects.filter(pk=site_tid).first() if site_tid > 0 else None
        code = (site_code or "").strip()
        if task is None and code:
            task = LibraryTask.objects.filter(code=code).first()
        return task

    def _submit_payload_for_site_task(site_tid: int, site_code: str = "") -> dict:
        cache_key = site_tid if site_tid > 0 else hash(site_code)
        if cache_key in site_payload_cache:
            return site_payload_cache[cache_key]
        from apps.api.inspection_report_make import (
            _resolve_submit_payload_for_site_task,
            normalize_template_bind_key,
        )

        task = _resolve_site_library_task(site_tid, site_code)
        if not ordered_submit_payloads:
            site_payload_cache[cache_key] = source_data
            return source_data
        matched = _resolve_submit_payload_for_site_task(
            task,
            source_data=source_data,
            ordered_submit_payloads=ordered_submit_payloads,
            site_task_code=site_code,
        )
        if matched:
            site_payload_cache[cache_key] = matched
            return matched
        if site_code:
            from apps.api.inspection_report_make import normalize_template_bind_key

            code_key = normalize_template_bind_key(site_code.replace("-", "").replace("_", ""))
            code_js = ""
            m_js = re.search(r"js\d+", site_code, re.I)
            if m_js:
                code_js = m_js.group(0).lower()
            for pay in ordered_submit_payloads:
                if not isinstance(pay, dict):
                    continue
                ptid = str(pay.get("templateId") or "").strip().lower()
                pt_key = normalize_template_bind_key(ptid)
                pt_compact = pt_key.replace("-", "")
                if code_js and code_js in ptid:
                    site_payload_cache[cache_key] = pay
                    return pay
                if code_key and (code_key in pt_compact or pt_compact in code_key):
                    site_payload_cache[cache_key] = pay
                    return pay
        site_payload_cache[cache_key] = {} if len(ordered_submit_payloads) > 1 else source_data
        return site_payload_cache[cache_key]

    def _parsed_for_site_task(site_task_id: int, site_task_code: str = "") -> dict | None:
        cache_key = (int(site_task_id or 0), (site_task_code or "").strip().lower())
        if cache_key in site_parsed_cache:
            return site_parsed_cache[cache_key]
        task = _resolve_site_library_task(int(site_task_id or 0), site_task_code)
        if task is None:
            site_parsed_cache[cache_key] = None
            return None
        _pdf, json_lf = _first_template_files_for_task(task)
        if json_lf is None:
            site_parsed_cache[site_task_id] = None
            return None
        try:
            path = pipeline_service.library_absolute_path(json_lf.relative_path)
            site_parsed_cache[cache_key] = parse_template_json(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except Exception:
            site_parsed_cache[cache_key] = None
        return site_parsed_cache[cache_key]

    def _pick_site_field_value(
        site_parsed: dict, site_pid: str, *, site_tid: int, site_code: str = ""
    ) -> str | None:
        payload = _submit_payload_for_site_task(site_tid, site_code)
        if not isinstance(payload, dict) or not payload:
            return None
        dd = payload.get("dynamicData") if isinstance(payload.get("dynamicData"), dict) else {}
        raw_dd = dd.get(site_pid)
        if raw_dd not in (None, "") and not isinstance(raw_dd, (dict, list)):
            s_dd = str(raw_dd).strip()
            if _is_signature_path_mapped_value(s_dd):
                return None
            return s_dd
        tr = payload.get("testResult") if isinstance(payload.get("testResult"), dict) else {}
        sp_key = f"f{site_pid[1:]}" if site_pid.lower().startswith("f") and site_pid[1:].isdigit() else site_pid
        raw_tr = tr.get(site_pid) if site_pid in tr else tr.get(sp_key)
        if raw_tr not in (None, "") and not isinstance(raw_tr, (dict, list)):
            s_tr = str(raw_tr).strip()
            if _is_signature_path_mapped_value(s_tr):
                return None
            return s_tr
        if not site_parsed:
            return None
        meta = _build_pdf_field_meta_index(site_parsed).get(
            site_pid.lower() if site_pid.lower().startswith("f") and site_pid[1:].isdigit() else site_pid
        )
        if meta and meta.get("submitPath"):
            from apps.api.inspection_report_make import _nested_get_for_submit_with_rated_fallback

            raw_sp = _nested_get_for_submit_with_rated_fallback(payload, str(meta["submitPath"]))
            if raw_sp not in (None, "") and not isinstance(raw_sp, (dict, list)):
                s_sp = str(raw_sp).strip()
                if _is_signature_path_mapped_value(s_sp):
                    return None
                return s_sp
        site_field = None
        for sf in site_parsed.get("fields") or []:
            if isinstance(sf, dict) and str(sf.get("pdfFieldId") or "").strip() == site_pid:
                site_field = sf
                break
        if site_field is None:
            from apps.api.inspection_report_make import (
                _flatten_unified_form_steps_to_fields,
            )

            for sf in _flatten_unified_form_steps_to_fields(_parsed_template_steps_list_extended(site_parsed)):
                pid = str(sf.get("pdfFieldId") or (sf.get("source") or {}).get("pdfFieldId") or "").strip()
                if pid == site_pid:
                    site_field = sf
                    break
        if site_field is None:
            return None
        site_slice: dict = {}
        _apply_template_field_sources_to_mapping(
            site_slice, payload, [site_field], pdf_field_id_only=True
        )
        if site_pid in site_slice and site_slice[site_pid] not in (None, ""):
            return str(site_slice[site_pid]).strip()
        return None

    for cfg in configs:
        report_pid = str(cfg.get("reportPdfFieldId") or "").strip()
        sources = cfg.get("sources") if isinstance(cfg.get("sources"), list) else []
        if not report_pid or not sources:
            continue
        report_field = report_by_pdf.get(report_pid)
        if report_field is None:
            for rf in _collect_template_pdf_field_rows(report_parsed_template):
                if str(rf.get("pdfFieldId") or "").strip() == report_pid:
                    report_field = rf
                    break
        if report_field is None:
            continue
        if _is_report_preserve_original_pdf_field(report_field):
            continue
        if any(
            (k or "").strip() in _REPORT_SUMMARY_OVERLAY_MANAGED_KEYS
            or "受检设备台数" in (k or "")
            or (k or "").strip() in ("受检工作场所",)
            or str(k or "").startswith("项目名称")
            or (k or "").strip() in ("医院名称2", "设备类型2", "设备类型2_2")
            for k in _field_semantic_candidate_keys(report_field)
        ):
            continue

        slot_values: dict[str, str] = {}
        picked_parts: list[str] = []
        for i, src in enumerate(sources, start=1):
            if not isinstance(src, dict):
                continue
            site_tid = int(src.get("siteTaskId") or 0)
            site_code = str(src.get("siteTaskCode") or "").strip()
            site_pid = str(src.get("sitePdfFieldId") or "").strip()
            if (site_tid <= 0 and not site_code) or not site_pid:
                continue
            site_parsed = _parsed_for_site_task(site_tid, site_code)
            resolved_pid = site_pid
            if site_parsed:
                resolved_pid = _resolve_site_pdf_field_id_by_label(
                    site_parsed,
                    site_pid,
                    str(src.get("label") or "").strip(),
                )
            val = _pick_site_field_value(
                site_parsed or {},
                resolved_pid,
                site_tid=site_tid,
                site_code=site_code,
            )
            if val is None:
                continue
            src_label = str(src.get("label") or "")
            if _is_implausible_mapped_site_value(val, src_label):
                continue
            report_label = str(report_field.get("label") or report_field.get("title") or "").strip()
            if _should_skip_boolean_site_pick(
                val,
                report_field_label=report_label,
                source_label=src_label,
            ) and not _normalize_value_cases(cfg.get("valueCases") or cfg.get("value_cases")):
                continue
            slot = str(src.get("slot") or str(i)).strip() or str(i)
            val = _coerce_site_pick_value_for_date_slot(val, label=src_label, slot=slot)
            slot_values[slot] = val
            slot_values[str(i)] = val
            slot_values[site_pid] = val
            picked_parts.append(val)

        if not picked_parts:
            if sources and str(cfg.get("mapSource") or "").strip().lower() in ("explicit", ""):
                placeholder = "/"
                value_mapping[report_pid] = placeholder
                for key in _field_semantic_candidate_keys(report_field):
                    if key:
                        value_mapping[key] = placeholder
            continue
        template = _coerce_value_template(cfg.get("valueTemplate") or cfg.get("value_template"))
        report_label = str(report_field.get("label") or report_field.get("title") or "").strip()
        insp_type = _inspection_type_from_payload(source_data)
        final_value, peer_verdict = _compose_report_site_field_final_value(
            picked_parts,
            slot_values,
            template,
            sources=sources,
            report_field_label=report_label,
            value_cases=cfg.get("valueCases") or cfg.get("value_cases"),
            site_parsed_resolver=_parsed_for_site_task,
            inspection_type=insp_type,
        )

        if peer_verdict:
            _write_peer_verdict_for_report_result_field(
                value_mapping,
                report_field,
                report_pid,
                peer_verdict,
                report_parsed_template,
            )
        if only_peer_verdicts:
            continue

        for key in _field_semantic_candidate_keys(report_field):
            if key:
                value_mapping[key] = final_value
        value_mapping[report_pid] = final_value


def apply_explicit_site_field_peer_verdicts(
    value_mapping: dict,
    source_data: dict,
    bindings: dict | None,
    *,
    report_parsed_template: dict | None = None,
    ordered_submit_payloads: Sequence[dict] | None = None,
) -> None:
    """在 reconcile/restore 显式映射结果后，补写同行单项判定（合格/不合格）。"""
    apply_report_site_field_map_to_value_mapping(
        value_mapping,
        source_data,
        bindings,
        report_parsed_template=report_parsed_template,
        ordered_submit_payloads=ordered_submit_payloads,
        only_peer_verdicts=True,
    )


def snapshot_explicit_report_site_field_mapping(
    value_mapping: dict,
    bindings: dict | None,
) -> dict[str, str]:
    """在 reconcile 等后处理前保存显式 report→site 映射写入的值。"""
    if not isinstance(value_mapping, dict) or not isinstance(bindings, dict):
        return {}
    configs = parse_report_site_field_configs_from_bindings(bindings)
    if not configs:
        return {}
    snap: dict[str, str] = {}
    for cfg in configs:
        if str(cfg.get("mapSource") or "").strip().lower() not in ("explicit", ""):
            continue
        pid = str(cfg.get("reportPdfFieldId") or "").strip()
        if not pid:
            continue
        val = value_mapping.get(pid)
        if val in (None, "") or isinstance(val, (dict, list, bool)):
            continue
        sval = str(val).strip()
        if _is_boolean_site_pick_value(sval):
            continue
        snap[pid] = sval
    return snap


def restore_explicit_report_site_field_mapping(
    value_mapping: dict,
    snapshot: Mapping[str, str] | None,
) -> None:
    """reconcile 后恢复显式 site 映射，避免 f20–f24 等质控槽被基本情况语义覆盖。"""
    if not isinstance(value_mapping, dict) or not isinstance(snapshot, Mapping):
        return
    for pid, val in snapshot.items():
        if pid and val not in (None, ""):
            value_mapping[str(pid)] = str(val)
