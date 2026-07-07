"""一次性导出 DSA 状态检测报告（JS-001 + JS-117），供 manage.py shell 加载。"""
from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model

from apps.api.inspection_pdf_service import (
    accumulate_inspection_payloads_ordered_merge,
    load_report_payload_for_manual_export,
    resolve_merged_report_device_count,
    resolve_submit_payload_for_site_record_pdf_lf,
    site_record_task_count_for_project,
)
from apps.api.inspection_report_make import (
    _build_filled_template_fields_for_task,
    _persist_filled_pdf_from_submit,
    _resolve_report_task_for_case,
)
from apps.core.models import InspectionCase, LibraryFile, LibraryProject

COMMISSION_NO = "260001"
SITE_RECORD_RELPATHS = (
    "site_records/260001/20260623161314/e8e40e68091745aa8d3a3049b8cb3bcf.pdf",
    "site_records/260001/20260623161316/e14270c4d1874251965a736ed8c23930.pdf",
)
SUBMIT_JSON_PATHS = (
    "inspection_submits/260001/20260623161314/JXFS-JS001_V3.0_X_射线透视设备质量控制（性能）检测及工作场所放射防护检测原始记录2026.4.1填写数据.json",
    "inspection_submits/260001/20260623161316/JXFS-JS117_V2.0_具有CBCT功能的C形臂血管造影机质量控制（性能）检测原始记录2026.4.1填写数据.json",
)


def _load_submit_from_disk(rel: str) -> dict:
    from django.conf import settings

    root = Path(getattr(settings, "FILE_LIBRARY_ROOT", ""))
    path = root / rel
    return json.loads(path.read_text(encoding="utf-8"))


def run() -> None:
    project = LibraryProject.objects.filter(code=COMMISSION_NO).first()
    if project is None:
        project = LibraryProject.objects.filter(name__icontains=COMMISSION_NO).first()
    if project is None:
        raise SystemExit(f"未找到项目 {COMMISSION_NO}")

    rows_ok: list[tuple[LibraryFile, InspectionCase, dict]] = []
    for rel in SITE_RECORD_RELPATHS:
        lf = LibraryFile.objects.filter(relative_path=rel).first()
        if lf is None:
            raise SystemExit(f"文件库无记录: {rel}")
        submit_payload, case, err = resolve_submit_payload_for_site_record_pdf_lf(lf)
        if err or case is None or not isinstance(submit_payload, dict):
            raise SystemExit(f"{rel}: {err or '无法解析提交数据'}")
        rows_ok.append((lf, case, submit_payload))
        print(f"OK site_record {rel} case={case.case_no} taskNo={submit_payload.get('taskNo')}")

    # 磁盘 JSON 与库内解析结果对齐校验（显式映射依赖 ordered_submit_payloads）
    disk_payloads = [_load_submit_from_disk(p) for p in SUBMIT_JSON_PATHS]
    ordered = [p for _lf, _c, p in rows_ok]
    if len(disk_payloads) == len(ordered):
        for i, disk in enumerate(disk_payloads):
            ordered[i] = disk
            print(f"使用磁盘 JSON: {SUBMIT_JSON_PATHS[i]}")

    cases_for_load: list[InspectionCase] = []
    seen: set[int] = set()
    for _lf, c, _p in rows_ok:
        if int(c.pk) not in seen:
            seen.add(int(c.pk))
            cases_for_load.append(c)

    persist_case = min(cases_for_load, key=lambda x: (x.case_no or ""))
    report_task = _resolve_report_task_for_case(persist_case.case_no, project)
    if report_task is None:
        raise SystemExit("未找到报告任务")

    merged_submit = accumulate_inspection_payloads_ordered_merge(ordered)
    source_payload, merge_note = load_report_payload_for_manual_export(
        cases_for_load,
        project,
        report_task,
        merged_submit,
        submit_merge_is_authoritative=True,
    )
    if not isinstance(source_payload, dict) or not source_payload:
        raise SystemExit(merge_note or "报告数据汇总失败")
    if merge_note:
        print("merge:", merge_note)

    task_no_for_fill = str(merged_submit.get("taskNo") or "").strip() or str(persist_case.case_no or "").strip()
    manual_device_count = resolve_merged_report_device_count(
        project,
        rows_ok,
        report_task=report_task,
    )

    filled_fields, template_pdf_id, fill_reason, template_json_name = _build_filled_template_fields_for_task(
        report_task,
        source_payload,
        project=project,
        task_no=task_no_for_fill,
        inspection_case=persist_case,
        manual_device_count=manual_device_count,
        ordered_submit_payloads=ordered,
    )
    if not filled_fields:
        raise SystemExit(fill_reason or "模板填充失败")

    # 抽样检查 CBCT 映射
    vm_probe = {str(f.get("pdfFieldId") or ""): str(f.get("value") or f.get("content") or "") for f in filled_fields if isinstance(f, dict)}
    for pid in ("f82", "f83", "f84", "f85"):
        if vm_probe.get(pid):
            print(f"  filled {pid} = {vm_probe[pid]}")

    User = get_user_model()
    user = User.objects.filter(is_superuser=True).order_by("id").first()
    if user is None:
        user = User.objects.order_by("id").first()
    if user is None:
        raise SystemExit("无可用用户")

    ok, pdf_reason, lf_out = _persist_filled_pdf_from_submit(
        user,
        task_no_for_fill,
        persist_case,
        project,
        filled_fields,
        template_pdf_id=template_pdf_id,
        template_json_name=template_json_name,
        task_obj=report_task,
        source_payload=source_payload,
        manual_device_count=manual_device_count,
        ordered_submit_payloads=ordered,
    )
    if not ok:
        raise SystemExit(pdf_reason or "PDF 导出失败")
    print("EXPORT_OK", getattr(lf_out, "relative_path", None), getattr(lf_out, "original_name", None))


run()
