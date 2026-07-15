"""报告第三页电子签字：从个人签名库叠印至 PDF，并与工作流推进联动。"""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import fitz
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core import pipeline_service
from apps.core.models import (
    CaseReportSignatureRecord,
    InspectionCase,
    InspectionCaseWorkflowState,
    InspectionSubmission,
    LibraryFile,
    LibraryProject,
    LibraryProjectWorkflowMember,
    LibraryTask,
    UserSignature,
    UserSignatureEvent,
)
from apps.core.user_signature_service import get_active_user_signature

# 推进目标环节 → 须叠印的报告签字位（授权签发另写签发日期）
ADVANCE_TARGET_SIGN_SLOT: dict[str, str] = {
    InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: CaseReportSignatureRecord.SLOT_REPORT_AUTHOR,
    InspectionCaseWorkflowState.STAGE_REPORT_SIGN: CaseReportSignatureRecord.SLOT_REPORT_AUDITOR,
    InspectionCaseWorkflowState.STAGE_ISSUED: CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
}

SIGN_SLOT_LABELS: dict[str, str] = {
    CaseReportSignatureRecord.SLOT_REPORT_AUTHOR: "编制人",
    CaseReportSignatureRecord.SLOT_REPORT_AUDITOR: "审核人",
    CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY: "授权人",
    CaseReportSignatureRecord.SLOT_ISSUE_DATE: "签发日期",
}

# 模板 field id / 中文标签 → 签字位
_FIELD_LABEL_TO_SLOT: dict[str, str] = {
    "编制人": CaseReportSignatureRecord.SLOT_REPORT_AUTHOR,
    "审核人": CaseReportSignatureRecord.SLOT_REPORT_AUDITOR,
    "授权人签字": CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
    "授权签字人": CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
    "签发日期": CaseReportSignatureRecord.SLOT_ISSUE_DATE,
    "年月日": CaseReportSignatureRecord.SLOT_ISSUE_DATE,
}

_DEFAULT_SLOT_PDF_FIELD_IDS: dict[str, str] = {
    CaseReportSignatureRecord.SLOT_REPORT_AUTHOR: "f10",
    CaseReportSignatureRecord.SLOT_REPORT_AUDITOR: "f11",
    CaseReportSignatureRecord.SLOT_ISSUE_DATE: "f12",
    CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY: "f13",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _format_issue_date(d: date | None = None) -> str:
    d = d or timezone.localdate()
    return f"{d.year}年{d.month}月{d.day}日"


def resolve_report_signature_slots(template_parsed: dict | None) -> dict[str, dict[str, Any]]:
    """
    解析报告模板签字位坐标。
    返回 slot -> {page, rect: [x0,y0,x1,y1], pdfFieldId, label}
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(template_parsed, dict):
        return out

    configured = template_parsed.get("reportSignatureSlots")
    if isinstance(configured, dict):
        pdf_fields = _index_pdf_fields(template_parsed)
        for slot, spec in configured.items():
            if not isinstance(spec, dict):
                continue
            slot_key = str(slot or "").strip()
            if not slot_key:
                continue
            fid = str(spec.get("pdfFieldId") or "").strip()
            field = pdf_fields.get(fid) if fid else None
            if field is None and spec.get("rect"):
                rect_raw = spec.get("rect")
                if isinstance(rect_raw, (list, tuple)) and len(rect_raw) >= 5:
                    page = int(rect_raw[0])
                    x0, y0, w, h = [float(rect_raw[i]) for i in range(1, 5)]
                    out[slot_key] = {
                        "page": page,
                        "rect": [x0, y0, x0 + w, y0 + h],
                        "pdfFieldId": fid,
                        "label": SIGN_SLOT_LABELS.get(slot_key, slot_key),
                    }
                    continue
            if field:
                out[slot_key] = field

    if out:
        return out

    pdf_fields = _index_pdf_fields(template_parsed)
    by_fid = {v.get("pdfFieldId"): k for k, v in pdf_fields.items() if v.get("pdfFieldId")}
    for slot, default_fid in _DEFAULT_SLOT_PDF_FIELD_IDS.items():
        if slot in out:
            continue
        label_key = by_fid.get(default_fid)
        if label_key and label_key in pdf_fields:
            out[slot] = pdf_fields[label_key]
            continue
        for label, mapped_slot in _FIELD_LABEL_TO_SLOT.items():
            if mapped_slot != slot:
                continue
            if label in pdf_fields:
                out[slot] = pdf_fields[label]
                break
    return out


def _index_pdf_fields(template_parsed: dict) -> dict[str, dict[str, Any]]:
    fields = template_parsed.get("pdf", {}).get("fields") if isinstance(template_parsed.get("pdf"), dict) else None
    if not isinstance(fields, list):
        fields = template_parsed.get("fields")
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(fields, list):
        return out
    for f in fields:
        if not isinstance(f, dict):
            continue
        label = str(f.get("id") or f.get("placeholder") or "").strip()
        rect_raw = f.get("rect")
        if not label or not isinstance(rect_raw, (list, tuple)) or len(rect_raw) < 5:
            continue
        page = int(rect_raw[0])
        x0, y0, w, h = [float(rect_raw[i]) for i in range(1, 5)]
        slot = _FIELD_LABEL_TO_SLOT.get(label)
        fid = str(f.get("pdfFieldId") or "").strip()
        entry = {
            "page": page,
            "rect": [x0, y0, x0 + w, y0 + h],
            "pdfFieldId": fid,
            "label": label,
        }
        out[label] = entry
        if slot:
            out[slot] = entry
        if fid:
            out[fid] = entry
    return out


def find_latest_report_library_file(
    *,
    case: InspectionCase,
    project: LibraryProject,
    task_no: str = "",
) -> LibraryFile | None:
    qs = (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_REPORT,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            projects=project,
            deleted_at__isnull=True,
        )
        .order_by("-created_at", "-id")
    )
    tn = (task_no or "").strip()
    if tn:
        safe = tn.replace("/", "_")
        qs = qs.filter(
            Q(original_name__icontains=safe)
            | Q(original_name__endswith="-报告.pdf")
            | Q(original_name__icontains="__task__")
        )
    return qs.first()


def resolve_report_library_task(
    *,
    project: LibraryProject,
    submission: InspectionSubmission,
    report_file: LibraryFile | None = None,
) -> LibraryTask | None:
    if report_file is not None:
        rt = (
            report_file.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
            .order_by("code", "id")
            .first()
        )
        if rt:
            return rt
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    site_task = _resolve_library_task_for_task_no(submission.task_no, project)
    if site_task is not None and site_task.output_target == LibraryTask.OUTPUT_REPORT:
        return site_task
    if site_task is not None:
        rt = (
            project.library_tasks.filter(
                output_target=LibraryTask.OUTPUT_REPORT,
                report_source_tasks=site_task,
            )
            .order_by("code", "id")
            .first()
        )
        if rt:
            return rt
    return (
        project.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
        .order_by("code", "id")
        .first()
    )


def load_report_template_parsed(report_task: LibraryTask | None) -> dict | None:
    from apps.api.inspection_report_make import _load_first_parsed_template_json_for_task

    return _load_first_parsed_template_json_for_task(report_task)


def list_case_report_signature_records(
    *,
    case_id: int,
    task_no: str = "",
) -> list[CaseReportSignatureRecord]:
    qs = CaseReportSignatureRecord.objects.filter(case_id=case_id).select_related(
        "signer", "user_signature", "report_file"
    )
    tn = (task_no or "").strip()
    if tn:
        qs = qs.filter(task_no=tn)
    return list(qs.order_by("sign_version", "signed_at", "id"))


def slot_already_signed(
    *,
    case_id: int,
    task_no: str,
    slot: str,
) -> bool:
    return CaseReportSignatureRecord.objects.filter(
        case_id=case_id,
        task_no=(task_no or "").strip(),
        slot=slot,
    ).exists()


def _read_user_signature_bytes(user_sig: UserSignature) -> bytes:
    if not user_sig.image:
        return b""
    path = Path(settings.MEDIA_ROOT) / str(user_sig.image)
    if path.is_file():
        return path.read_bytes()
    return b""


def _overlay_signature_on_pdf(
    pdf_bytes: bytes,
    *,
    page_1based: int,
    rect_xyxy: list[float],
    image_bytes: bytes,
    date_text: str = "",
) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_idx = max(0, int(page_1based) - 1)
        if page_idx >= doc.page_count:
            raise ValueError(f"签字页码 {page_1based} 超出报告页数")
        page = doc[page_idx]
        r = fitz.Rect(*rect_xyxy)
        if image_bytes:
            page.insert_image(r, stream=image_bytes, keep_proportion=True, overlay=True)
        if date_text:
            page.insert_textbox(
                r,
                date_text,
                fontsize=10.5,
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_CENTER,
                overlay=True,
            )
        return doc.tobytes()
    finally:
        doc.close()


def _save_signed_report_file(
    *,
    user: User,
    project: LibraryProject,
    report_task: LibraryTask | None,
    case: InspectionCase,
    task_no: str,
    pdf_bytes: bytes,
    sign_version: int,
) -> LibraryFile:
    from apps.core.library_file_service import report_relative_path, safe_library_basename

    tn = (task_no or "").replace("/", "_")
    display_name = f"{tn}__signed_v{sign_version}-报告.pdf"
    disk_name = safe_library_basename(display_name)
    rel = report_relative_path(disk_name, project=project, library_task=report_task)
    abs_p = pipeline_service.library_absolute_path(rel)
    abs_p.parent.mkdir(parents=True, exist_ok=True)
    abs_p.write_bytes(pdf_bytes)
    sha = _sha256_bytes(pdf_bytes)
    lf = LibraryFile.objects.create(
        original_name=display_name,
        relative_path=rel,
        category=LibraryFile.CATEGORY_REPORT,
        content_sha256=sha,
        size=len(pdf_bytes),
        created_by=user,
        link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
        link_object_id=case.pk,
    )
    from apps.core.models import LibraryFileProject

    LibraryFileProject.objects.get_or_create(
        library_file=lf,
        project_id=project.pk,
        defaults={"created_by": user},
    )
    if report_task is not None:
        from apps.core.models import LibraryFileTask

        LibraryFileTask.objects.get_or_create(
            library_file=lf,
            library_task=report_task,
            defaults={"created_by": user},
        )
    return lf


def _log_signature_usage(
    *,
    owner: User,
    user_sig: UserSignature,
    slot: str,
    actor: User,
    project: LibraryProject,
    case: InspectionCase,
    submission: InspectionSubmission,
    report_file: LibraryFile,
    report_sha256: str,
    workflow_stage: str,
) -> None:
    UserSignatureEvent.objects.create(
        user=owner,
        user_signature=user_sig,
        role=slot,
        event_type=UserSignatureEvent.EVENT_USED_REPORT_SIGN,
        actor=actor,
        project=project,
        case=case,
        submission=submission,
        task_no=(submission.task_no or "").strip(),
        library_file=report_file,
        snapshot_path=report_file.relative_path if report_file else "",
        content_sha256=user_sig.content_sha256 or report_sha256,
        detail={
            "context": "report_workflow_sign",
            "workflow_stage": workflow_stage,
            "report_sha256": report_sha256,
        },
    )


def build_report_signature_status(
    *,
    case: InspectionCase | None,
    project: LibraryProject,
    submission: InspectionSubmission | None,
    viewer: User,
) -> dict[str, Any]:
    """委托管理 UI：报告签字状态面板数据。"""
    empty = {
        "enabled": False,
        "report_file": None,
        "report_download_url": "",
        "slots": [],
        "timeline": [],
        "pending_slot": "",
        "pending_label": "",
        "user_has_signature": False,
        "can_sign": False,
    }
    if case is None or submission is None or not (submission.task_no or "").strip():
        return empty

    report_file = find_latest_report_library_file(
        case=case, project=project, task_no=submission.task_no
    )
    records = list_case_report_signature_records(case_id=case.pk, task_no=submission.task_no)
    signed_slots = {r.slot for r in records if r.slot != CaseReportSignatureRecord.SLOT_ISSUE_DATE}

    from apps.core.commission_workflow_progress import effective_workflow_stage

    stage = effective_workflow_stage(case, submission)
    pending_slot = ""
    pending_label = ""
    target = _pending_sign_target_stage(stage)
    if target:
        pending_slot = ADVANCE_TARGET_SIGN_SLOT.get(target, "")
        if pending_slot and pending_slot not in signed_slots:
            pending_label = f"待您签字（{SIGN_SLOT_LABELS.get(pending_slot, pending_slot)}）"

    user_sig = get_active_user_signature(viewer)
    slots_order = (
        CaseReportSignatureRecord.SLOT_REPORT_AUTHOR,
        CaseReportSignatureRecord.SLOT_REPORT_AUDITOR,
        CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
    )
    rec_by_slot = {r.slot: r for r in records}
    slots_ui = []
    for slot in slots_order:
        rec = rec_by_slot.get(slot)
        slots_ui.append(
            {
                "slot": slot,
                "label": SIGN_SLOT_LABELS.get(slot, slot),
                "signed": slot in signed_slots,
                "signer": (
                    (rec.signer.get_full_name() or rec.signer.username) if rec and rec.signer_id else ""
                ),
                "signed_at": rec.signed_at if rec else None,
                "version": rec.sign_version if rec else 0,
            }
        )
    issue_rec = rec_by_slot.get(CaseReportSignatureRecord.SLOT_ISSUE_DATE)
    if issue_rec:
        slots_ui.append(
            {
                "slot": CaseReportSignatureRecord.SLOT_ISSUE_DATE,
                "label": "签发日期",
                "signed": True,
                "signer": (
                    (issue_rec.signer.get_full_name() or issue_rec.signer.username)
                    if issue_rec.signer_id
                    else ""
                ),
                "signed_at": issue_rec.signed_at,
                "version": issue_rec.sign_version,
            }
        )

    timeline = [
        {
            "version": r.sign_version,
            "slot_label": r.slot_label,
            "signer": (r.signer.get_full_name() or r.signer.username) if r.signer_id else "—",
            "signed_at": r.signed_at,
            "report_file_id": r.report_file_id,
            "sha256_short": (r.report_sha256_after or "")[:8],
        }
        for r in records
        if r.slot != CaseReportSignatureRecord.SLOT_ISSUE_DATE
    ]

    can_sign = bool(
        pending_slot
        and report_file
        and user_sig
        and _viewer_may_sign_slot(viewer, project, pending_slot, stage)
    )

    download_url = ""
    if report_file:
        download_url = reverse("file_library_download", args=[report_file.pk])

    return {
        "enabled": bool(report_file),
        "report_file": report_file,
        "report_file_name": report_file.original_name if report_file else "",
        "report_download_url": download_url,
        "slots": slots_ui,
        "timeline": timeline,
        "pending_slot": pending_slot,
        "pending_label": pending_label,
        "user_has_signature": user_sig is not None,
        "can_sign": can_sign,
        "no_report_hint": "" if report_file else "报告尚未生成，请先在项目工作台导出报告",
        "no_signature_hint": "" if user_sig else "请先在「签名管理」上传个人签名",
    }


def _pending_sign_target_stage(current_stage: str) -> str:
    mapping = {
        InspectionCaseWorkflowState.STAGE_REPORT_DRAFT: InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN: InspectionCaseWorkflowState.STAGE_ISSUED,
    }
    return mapping.get(current_stage or "", "")


def _viewer_may_sign_slot(
    viewer: User,
    project: LibraryProject,
    slot: str,
    current_stage: str,
) -> bool:
    from apps.core.commission_workflow_progress import user_may_manual_advance_to_stage

    target = _pending_sign_target_stage(current_stage)
    if not target or ADVANCE_TARGET_SIGN_SLOT.get(target) != slot:
        return False
    required_role = {
        CaseReportSignatureRecord.SLOT_REPORT_AUTHOR: LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR,
        CaseReportSignatureRecord.SLOT_REPORT_AUDITOR: LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR,
        CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY: LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY,
    }.get(slot)
    if not required_role:
        return False
    return user_may_manual_advance_to_stage(
        viewer, project, required_workflow_role=required_role
    )


@transaction.atomic
def apply_report_signature_and_advance(
    *,
    user: User,
    project: LibraryProject,
    submission: InspectionSubmission,
    target_stage: str,
    issue_date: date | None = None,
) -> tuple[bool, str]:
    """
    使用当前用户个人签名叠印报告对应栏位，并推进工作流。
    仅用于 REPORT_AUDIT / REPORT_SIGN / ISSUED 三个推进目标。
    """
    if submission.case_id is None:
        return False, "该任务尚无关联案件"
    case = submission.case
    if case.library_project_id and int(case.library_project_id) != int(project.pk):
        return False, "案件与委托不匹配"

    sign_slot = ADVANCE_TARGET_SIGN_SLOT.get(target_stage)
    if not sign_slot:
        return False, "该推进步骤不需要报告签字"

    from apps.core.commission_workflow_progress import (
        apply_manual_workflow_advance,
        effective_workflow_stage,
        user_may_manual_advance_to_stage,
    )

    required_role = {
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN: LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR,
        InspectionCaseWorkflowState.STAGE_ISSUED: LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY,
    }.get(target_stage)
    if not required_role or not user_may_manual_advance_to_stage(
        user, project, required_workflow_role=required_role
    ):
        return False, "无权执行该环节签字"

    effective = effective_workflow_stage(case, submission)
    expected_current = {
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: InspectionCaseWorkflowState.STAGE_REPORT_DRAFT,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN: InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        InspectionCaseWorkflowState.STAGE_ISSUED: InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
    }.get(target_stage)
    if effective != expected_current:
        return False, f"当前环节为「{dict(InspectionCaseWorkflowState.STAGE_CHOICES).get(effective, effective)}」，无法执行此签字"

    if slot_already_signed(case_id=case.pk, task_no=submission.task_no, slot=sign_slot):
        return False, f"「{SIGN_SLOT_LABELS.get(sign_slot, sign_slot)}」已签字，请勿重复操作"

    user_sig = get_active_user_signature(user)
    if user_sig is None:
        return False, "请先在「签名管理」上传您的个人签名"

    report_file = find_latest_report_library_file(
        case=case, project=project, task_no=submission.task_no
    )
    if report_file is None:
        return False, "报告尚未生成，请先在项目工作台导出报告后再签字"

    report_task = resolve_report_library_task(
        project=project, submission=submission, report_file=report_file
    )
    template_parsed = load_report_template_parsed(report_task)
    slots = resolve_report_signature_slots(template_parsed)
    slot_spec = slots.get(sign_slot)
    if not slot_spec:
        return False, "报告模板未配置该签字位坐标，请联系管理员"

    sig_bytes = _read_user_signature_bytes(user_sig)
    if not sig_bytes:
        return False, "无法读取您的签名图片"

    src_path = pipeline_service.library_absolute_path(report_file.relative_path)
    if not src_path.is_file():
        return False, "报告文件不存在或已被删除"
    pdf_before = src_path.read_bytes()
    sha_before = _sha256_bytes(pdf_before)

    page = int(slot_spec.get("page") or 3)
    rect = list(slot_spec.get("rect") or [])
    if len(rect) < 4:
        return False, "签字位坐标无效"

    pdf_after = _overlay_signature_on_pdf(
        pdf_before,
        page_1based=page,
        rect_xyxy=rect,
        image_bytes=sig_bytes,
    )

    sign_version = (
        CaseReportSignatureRecord.objects.filter(case_id=case.pk, task_no=submission.task_no).count() + 1
    )
    new_lf = _save_signed_report_file(
        user=user,
        project=project,
        report_task=report_task,
        case=case,
        task_no=submission.task_no,
        pdf_bytes=pdf_after,
        sign_version=sign_version,
    )
    sha_after = _sha256_bytes(pdf_after)

    CaseReportSignatureRecord.objects.create(
        case=case,
        project=project,
        submission=submission,
        task_no=(submission.task_no or "").strip(),
        workflow_stage=effective,
        slot=sign_slot,
        signer=user,
        user_signature=user_sig,
        signature_sha256=user_sig.content_sha256 or _sha256_bytes(sig_bytes),
        report_file=new_lf,
        report_sha256_before=sha_before,
        report_sha256_after=sha_after,
        overlay_page=page,
        overlay_rect=rect,
        sign_version=sign_version,
    )
    _log_signature_usage(
        owner=user,
        user_sig=user_sig,
        slot=sign_slot,
        actor=user,
        project=project,
        case=case,
        submission=submission,
        report_file=new_lf,
        report_sha256=sha_after,
        workflow_stage=effective,
    )

    if target_stage == InspectionCaseWorkflowState.STAGE_ISSUED:
        issue_spec = slots.get(CaseReportSignatureRecord.SLOT_ISSUE_DATE)
        idate = issue_date or timezone.localdate()
        if issue_spec:
            issue_rect = list(issue_spec.get("rect") or [])
            issue_page = int(issue_spec.get("page") or 3)
            if len(issue_rect) >= 4:
                pdf_with_date = _overlay_signature_on_pdf(
                    pdf_after,
                    page_1based=issue_page,
                    rect_xyxy=issue_rect,
                    image_bytes=b"",
                    date_text=_format_issue_date(idate),
                )
                sha_after_date = _sha256_bytes(pdf_with_date)
                new_lf = _save_signed_report_file(
                    user=user,
                    project=project,
                    report_task=report_task,
                    case=case,
                    task_no=submission.task_no,
                    pdf_bytes=pdf_with_date,
                    sign_version=sign_version + 1,
                )
                CaseReportSignatureRecord.objects.create(
                    case=case,
                    project=project,
                    submission=submission,
                    task_no=(submission.task_no or "").strip(),
                    workflow_stage=effective,
                    slot=CaseReportSignatureRecord.SLOT_ISSUE_DATE,
                    signer=user,
                    user_signature=None,
                    signature_sha256="",
                    report_file=new_lf,
                    report_sha256_before=sha_after,
                    report_sha256_after=sha_after_date,
                    overlay_page=issue_page,
                    overlay_rect=issue_rect,
                    sign_version=sign_version + 1,
                )
                sha_after = sha_after_date

    ok, msg = apply_manual_workflow_advance(
        user=user,
        project=project,
        submission=submission,
        target_stage=target_stage,
    )
    if not ok:
        return False, msg
    slot_label = SIGN_SLOT_LABELS.get(sign_slot, sign_slot)
    return True, f"已叠印{slot_label}签名并推进流程"
