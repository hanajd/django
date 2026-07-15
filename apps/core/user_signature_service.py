"""用户手写签名：每人一份，上传、查询与使用审计（项目签字位单独记录）。"""
from __future__ import annotations

import hashlib
import imghdr
from typing import Iterable

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import transaction

from apps.core.models import (
    InspectionCase,
    InspectionSubmission,
    LibraryFile,
    LibraryProject,
    LibraryTask,
    PROJECT_SIGNATURE_SLOT_CHOICES,
    UserSignature,
    UserSignatureEvent,
)

# 项目/现场模板 canonical 签字槽（与提交 JSON signatures.* 键一致）
PROJECT_SIGNATURE_SLOTS: tuple[str, ...] = tuple(k for k, _ in PROJECT_SIGNATURE_SLOT_CHOICES)

REPORT_API_ROLE_TO_SLOT = {
    "author": "inspector",
    "reviewer": "checker",
    "approver": "accompanyingPerson",
}

PROJECT_SLOT_LABELS = dict(PROJECT_SIGNATURE_SLOT_CHOICES)

_MAX_SIGNATURE_BYTES = 2 * 1024 * 1024


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_signature_image_bytes(raw: bytes) -> tuple[bytes, str]:
    if not raw:
        raise ValueError("签名图片不能为空")
    if len(raw) > _MAX_SIGNATURE_BYTES:
        raise ValueError("签名图片不能超过 2MB")
    kind = imghdr.what(None, h=raw[:32])
    ext_map = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp"}
    ext = ext_map.get(kind or "")
    if not ext:
        raise ValueError("仅支持 PNG、JPEG、GIF、WebP 格式的签名图片")
    return raw, ext


def read_uploaded_signature_file(uploaded) -> tuple[bytes, str]:
    raw = uploaded.read()
    data, ext = validate_signature_image_bytes(raw)
    name = getattr(uploaded, "name", "") or f"signature{ext}"
    return data, name


def get_active_user_signature(user: User) -> UserSignature | None:
    return (
        UserSignature.objects.filter(user=user, is_active=True)
        .order_by("-version", "-updated_at", "-id")
        .first()
    )


def list_signature_events(
    user: User,
    *,
    event_type: str = "",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[UserSignatureEvent], int]:
    qs = (
        UserSignatureEvent.objects.filter(user=user)
        .select_related("actor", "project", "case", "submission", "user_signature", "library_file")
        .order_by("-created_at", "-id")
    )
    if event_type:
        qs = qs.filter(event_type=event_type)
    total = qs.count()
    rows = list(qs[offset : offset + limit])
    return rows, total


def _log_signature_event(
    *,
    owner: User,
    event_type: str,
    actor: User | None,
    usage_slot: str = "",
    user_signature: UserSignature | None = None,
    project: LibraryProject | None = None,
    case: InspectionCase | None = None,
    submission: InspectionSubmission | None = None,
    task_no: str = "",
    task_code: str = "",
    output_target: str = "",
    library_file: LibraryFile | None = None,
    snapshot_path: str = "",
    content_sha256: str = "",
    detail: dict | None = None,
) -> UserSignatureEvent:
    slot = (usage_slot or "").strip()
    if slot and slot not in PROJECT_SIGNATURE_SLOTS:
        slot = REPORT_API_ROLE_TO_SLOT.get(slot, slot)
    return UserSignatureEvent.objects.create(
        user=owner,
        user_signature=user_signature,
        role=slot,
        event_type=event_type,
        actor=actor,
        project=project,
        case=case,
        submission=submission,
        task_no=(task_no or "").strip(),
        task_code=(task_code or "").strip(),
        output_target=(output_target or "").strip(),
        library_file=library_file,
        snapshot_path=(snapshot_path or "").strip()[:512],
        content_sha256=(content_sha256 or "").strip(),
        detail=dict(detail or {}),
    )


@transaction.atomic
def save_user_signature(
    owner: User,
    image_bytes: bytes,
    *,
    actor: User | None = None,
    original_filename: str = "",
) -> tuple[UserSignature, UserSignatureEvent]:
    data, ext = validate_signature_image_bytes(image_bytes)
    sha = _sha256_bytes(data)
    actor = actor or owner

    existing = (
        UserSignature.objects.select_for_update()
        .filter(user=owner, is_active=True)
        .order_by("-version")
        .first()
    )
    event_type = UserSignatureEvent.EVENT_REPLACED if existing else UserSignatureEvent.EVENT_UPLOADED
    old_sha = (existing.content_sha256 or "") if existing else ""

    if existing:
        existing.image.save(f"signature{ext}", ContentFile(data), save=False)
        existing.content_sha256 = sha
        existing.original_filename = (original_filename or existing.original_filename or "").strip()
        existing.version = (existing.version or 1) + 1
        existing.uploaded_by = actor
        existing.save()
        sig = existing
    else:
        sig = UserSignature(
            user=owner,
            content_sha256=sha,
            original_filename=(original_filename or "").strip(),
            version=1,
            uploaded_by=actor,
            is_active=True,
        )
        sig.save()
        sig.image.save(f"signature{ext}", ContentFile(data), save=False)
        sig.save(update_fields=["image", "updated_at"])

    event = _log_signature_event(
        owner=owner,
        event_type=event_type,
        actor=actor,
        user_signature=sig,
        content_sha256=sha,
        snapshot_path=sig.image.name if sig.image else "",
        detail={
            "version": sig.version,
            "previous_sha256": old_sha,
            "original_filename": sig.original_filename,
        },
    )
    return sig, event


def _sha256_from_signature_value(value: str) -> str:
    from apps.api.inspection_submit_payload_service import (
        _sha256_of_stored_media,
        decode_signature_role_png_bytes,
    )

    if not isinstance(value, str) or not value.strip():
        return ""
    sha = _sha256_of_stored_media(value.strip())
    if sha:
        return sha
    raw = decode_signature_role_png_bytes(value)
    if raw:
        return _sha256_bytes(raw)
    return ""


def _match_user_signature_by_value(owner: User, value: str) -> UserSignature | None:
    val = str(value or "").strip()
    if not val:
        return None
    sig = get_active_user_signature(owner)
    if sig is None:
        return None
    needle = f"user_signatures/{owner.pk}/"
    if needle in val.replace("\\", "/"):
        return sig
    sha = _sha256_from_signature_value(val)
    if sha and sig.content_sha256 == sha:
        return sig
    return None


def record_signature_usage_from_submission(
    *,
    actor: User | None,
    submission: InspectionSubmission,
    payload: dict,
    generation_results: Iterable[dict] | None = None,
) -> None:
    """检测提交落库后：匹配用户个人签名，并记录被填入的项目签字位。"""
    if actor is None or submission is None:
        return
    sigs = payload.get("signatures") if isinstance(payload.get("signatures"), dict) else {}
    project = submission.project
    case = submission.case
    task_no = (submission.task_no or "").strip()

    output_by_code: dict[str, str] = {}
    for gr in generation_results or []:
        if not isinstance(gr, dict):
            continue
        code = str(gr.get("taskCode") or "").strip()
        ot = str(gr.get("outputTarget") or "").strip()
        if code and ot:
            output_by_code[code] = ot

    logged_submit: set[str] = set()
    for slot in PROJECT_SIGNATURE_SLOTS:
        val = sigs.get(slot)
        if not val:
            continue
        user_sig = _match_user_signature_by_value(actor, str(val))
        if user_sig is None:
            continue
        sha = user_sig.content_sha256 or _sha256_from_signature_value(str(val))
        if slot not in logged_submit:
            logged_submit.add(slot)
            _log_signature_event(
                owner=user_sig.user,
                usage_slot=slot,
                event_type=UserSignatureEvent.EVENT_USED_SUBMIT,
                actor=actor,
                user_signature=user_sig,
                project=project,
                case=case,
                submission=submission,
                task_no=task_no,
                snapshot_path=str(val).strip()[:512],
                content_sha256=sha,
                detail={"context": "inspection_submit"},
            )
        for task_code, output_target in output_by_code.items():
            evt = (
                UserSignatureEvent.EVENT_USED_REPORT
                if output_target == LibraryTask.OUTPUT_REPORT
                else UserSignatureEvent.EVENT_USED_SITE_RECORD
            )
            _log_signature_event(
                owner=user_sig.user,
                usage_slot=slot,
                event_type=evt,
                actor=actor,
                user_signature=user_sig,
                project=project,
                case=case,
                submission=submission,
                task_no=task_no,
                task_code=task_code,
                output_target=output_target,
                snapshot_path=str(val).strip()[:512],
                content_sha256=sha,
                detail={"context": "pdf_generation"},
            )


def record_signature_usage_from_report_upload(
    *,
    actor: User | None,
    submission: InspectionSubmission,
    report_api_role: str,
    raw_png: bytes,
    task_no: str = "",
    library_files: list[LibraryFile] | None = None,
) -> None:
    if actor is None:
        return
    slot = REPORT_API_ROLE_TO_SLOT.get((report_api_role or "").strip(), "")
    sha = _sha256_bytes(raw_png) if raw_png else ""
    user_sig = get_active_user_signature(actor)
    if user_sig and sha and user_sig.content_sha256 != sha:
        user_sig = None
    elif user_sig and not sha:
        user_sig = user_sig
    elif sha and not user_sig:
        user_sig = UserSignature.objects.filter(
            user=actor, content_sha256=sha, is_active=True
        ).first()
    lf = (library_files or [None])[0] if library_files else None
    _log_signature_event(
        owner=actor,
        usage_slot=slot,
        event_type=UserSignatureEvent.EVENT_USED_REPORT,
        actor=actor,
        user_signature=user_sig,
        project=submission.project if submission else None,
        case=submission.case if submission else None,
        submission=submission,
        task_no=(task_no or (submission.task_no if submission else "") or "").strip(),
        library_file=lf,
        content_sha256=sha,
        detail={
            "context": "report_signature_api",
            "report_api_role": report_api_role,
            "matched_library": user_sig is not None,
        },
    )


def format_event_summary(event: UserSignatureEvent) -> str:
    parts: list[str] = [str(event.event_type_label)]
    role_label = str(event.role_label)
    if role_label and role_label != "—":
        parts.append(role_label)
    if event.event_type == UserSignatureEvent.EVENT_USED_REPORT_SIGN:
        ctx = event.detail.get("workflow_stage") if isinstance(event.detail, dict) else ""
        if ctx:
            parts.append(f"环节 {ctx}")
    if event.project_id and event.project:
        parts.append(f"项目 {event.project.code or event.project.name}")
    if event.task_no:
        parts.append(f"任务 {event.task_no}")
    if event.task_code:
        parts.append(event.task_code)
    if event.output_target:
        ot = event.output_target
        if ot == LibraryTask.OUTPUT_REPORT:
            parts.append("报告")
        elif ot == LibraryTask.OUTPUT_SITE_RECORD:
            parts.append("现场记录")
        else:
            parts.append(ot)
    if event.case_id and event.case:
        parts.append(f"案件 {event.case.case_no or event.case_id}")
    return " · ".join(parts)
