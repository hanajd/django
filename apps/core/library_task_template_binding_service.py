"""任务模板 PDF/JSON 绑定：解析当前配对、轮换绑定、历史回溯。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apps.core.library_access import (
    library_template_file_accessible_for_task,
    library_user_may_edit_library_task,
)
from apps.core.library_file_service import (
    attach_files_to_projects,
    attach_files_to_tasks,
    detach_files_from_projects,
    detach_files_from_tasks,
    library_file_exists_on_disk,
)
from apps.core.models import (
    LibraryFile,
    LibraryTask,
    LibraryTaskTemplateBindingHistory,
)
from apps.core import pipeline_service


_AUXILIARY_JSON_NAME_SUFFIXES = ("_frontend.json", "_matrix.json")


def is_auxiliary_template_json_filename(name: str) -> bool:
    """前端规则 JSON / 矩阵 JSON 等辅助文件，不参与任务「主 JSON 模板」绑定。"""
    low = (name or "").lower().strip()
    return any(low.endswith(suf) for suf in _AUXILIARY_JSON_NAME_SUFFIXES)


def score_unified_template_json_blob(obj: dict) -> int:
    """分数越高越适合作为任务主 JSON（统一坐标模板）。"""
    if not isinstance(obj, dict):
        return 0
    score = 0
    schema = str(obj.get("schema") or "").strip().lower()
    if schema.startswith("unified_form_template"):
        score += 4
    pdf = obj.get("pdf") if isinstance(obj.get("pdf"), dict) else {}
    fields = pdf.get("fields") if isinstance(pdf.get("fields"), list) else []
    if fields:
        score += 8
    elif isinstance(obj.get("fields"), list) and obj.get("fields"):
        score += 2
    if isinstance(obj.get("steps"), list) and obj.get("steps"):
        score += 1
    return score


def is_auxiliary_template_json_file(lf: LibraryFile) -> bool:
    """按文件名或内容判断是否为辅助 JSON（非 pdf.fields 统一模板）。"""
    if is_auxiliary_template_json_filename(lf.original_name or ""):
        return True
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    if score_unified_template_json_blob(obj) >= 8:
        return False
    if isinstance(obj.get("steps"), list) and obj.get("steps"):
        return True
    return False


def score_library_template_json_file(lf: LibraryFile) -> int:
    if is_auxiliary_template_json_filename(lf.original_name or ""):
        return -1
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0
    if not isinstance(obj, dict):
        return 0
    return score_unified_template_json_blob(obj)


def pick_task_primary_json_template(task: LibraryTask) -> LibraryFile | None:
    """任务主 JSON：优先含 pdf.fields 的统一模板，排除 *_frontend.json / *_matrix.json。"""
    candidates: list[tuple[int, int, LibraryFile]] = []
    fallback: LibraryFile | None = None
    from apps.core.library_file_service import library_file_exists_on_disk

    for lf in task.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).order_by(
        "-created_at", "-id"
    ):
        if not library_file_exists_on_disk(lf):
            continue
        if not (lf.original_name or "").lower().endswith(".json"):
            continue
        if is_auxiliary_template_json_filename(lf.original_name or ""):
            continue
        sc = score_library_template_json_file(lf)
        if sc > 0:
            candidates.append((sc, int(lf.pk), lf))
        elif fallback is None and not is_auxiliary_template_json_file(lf):
            fallback = lf
    if candidates:
        candidates.sort(key=lambda t: (-t[0], -t[1]))
        return candidates[0][2]
    return fallback


def infer_template_file_role(lf: LibraryFile) -> str | None:
    name = (lf.original_name or "").lower()
    if name.endswith(".pdf"):
        return LibraryTaskTemplateBindingHistory.ROLE_PDF
    if name.endswith(".json"):
        return LibraryTaskTemplateBindingHistory.ROLE_JSON
    return None


def get_task_template_pair(task: LibraryTask) -> tuple[LibraryFile | None, LibraryFile | None]:
    """返回任务上当前 (pdf_template, json_template)；JSON 取主统一模板，非最新 *_frontend.json。"""
    pdf_lf = None
    qs = (
        LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE, library_tasks=task)
        .order_by("-created_at", "-id")
        .distinct()
    )
    from apps.core.library_file_service import library_file_exists_on_disk

    for lf in qs:
        if not library_file_exists_on_disk(lf):
            continue
        role = infer_template_file_role(lf)
        if role == LibraryTaskTemplateBindingHistory.ROLE_PDF and pdf_lf is None:
            pdf_lf = lf
        if pdf_lf is not None:
            break
    json_lf = pick_task_primary_json_template(task)
    return pdf_lf, json_lf


def resolve_task_export_pdf_template_id(
    task: LibraryTask | None,
    json_source_pdf_id: int | None = None,
) -> int | None:
    """
    导出/回填所用空白 PDF：以任务当前绑定的 PDF 为准。

    JSON 内 ``pdf.source_pdf.template_file_id`` 在轮换 PDF 或从其它任务复制坐标后，
    可能仍指向旧版式（例如状态检测 JSON 仍引用验收检测 PDF），与模板编辑器不一致。
    """
    if task is None:
        if json_source_pdf_id in (None, ""):
            return None
        try:
            return int(json_source_pdf_id)
        except (TypeError, ValueError):
            return None
    pdf_lf, _json_lf = get_task_template_pair(task)
    if pdf_lf is not None:
        bound_id = int(pdf_lf.pk)
        if json_source_pdf_id not in (None, ""):
            try:
                stale_id = int(json_source_pdf_id)
            except (TypeError, ValueError):
                stale_id = None
            if stale_id is not None and stale_id != bound_id:
                import logging

                logging.getLogger(__name__).warning(
                    "task %s export PDF: JSON source_pdf #%s overridden by bound PDF #%s",
                    task.pk,
                    stale_id,
                    bound_id,
                )
        return bound_id
    if json_source_pdf_id in (None, ""):
        return None
    try:
        return int(json_source_pdf_id)
    except (TypeError, ValueError):
        return None


def task_has_pdf_and_json_bound(task: LibraryTask) -> bool:
    pdf_lf, json_lf = get_task_template_pair(task)
    return pdf_lf is not None and json_lf is not None


def resolve_default_json_template_id(
    task: LibraryTask,
    *,
    pdf_template_id: int | None = None,
) -> int | None:
    """任务已同时绑定 PDF 与 JSON 时，返回应默认载入的 JSON 文件 id。"""
    pdf_lf, json_lf = get_task_template_pair(task)
    if pdf_lf is None or json_lf is None:
        return None
    if pdf_template_id is not None:
        on_task = LibraryFile.objects.filter(
            pk=int(pdf_template_id),
            category=LibraryFile.CATEGORY_TEMPLATE,
            library_tasks=task,
        ).exists()
        if not on_task:
            return None
    return json_lf.pk


def detach_auxiliary_template_json_from_task(
    task: LibraryTask, *, user=None
) -> list[int]:
    """
    从任务 M2M 移除前端规则 JSON / 矩阵 JSON（仅作文件库辅助，不参与任务主模板绑定）。
    保存前端 JSON 后调用，避免误关联或历史错误绑定残留。
    """
    aux_ids: list[int] = []
    for lf in task.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).order_by("-id"):
        if not is_auxiliary_template_json_file(lf):
            continue
        aux_ids.append(int(lf.pk))
    if not aux_ids:
        return []
    detach_files_from_tasks(aux_ids, [task.pk])
    project_ids = list(task.projects.values_list("id", flat=True))
    if project_ids:
        detach_files_from_projects(aux_ids, project_ids)
    return aux_ids


def _list_task_storage_slot_json_files(task: LibraryTask, slot: str) -> list[LibraryFile]:
    from apps.core.library_file_service import library_file_exists_on_disk
    from apps.core.template_storage_service import task_storage_base_segments

    prefix = "/".join(["templates", *task_storage_base_segments(task), slot]) + "/"
    out: list[LibraryFile] = []
    for lf in LibraryFile.objects.filter(
        category=LibraryFile.CATEGORY_TEMPLATE,
        relative_path__startswith=prefix,
        deleted_at__isnull=True,
    ).order_by("-created_at", "-id"):
        if not (lf.original_name or "").lower().endswith(".json"):
            continue
        if is_auxiliary_template_json_file(lf):
            continue
        if not library_file_exists_on_disk(lf):
            continue
        out.append(lf)
    return out


def list_task_storage_current_json_files(task: LibraryTask) -> list[LibraryFile]:
    """任务分层目录 current/ 下的主 JSON 模板（含仅落盘、尚未 M2M 绑定的文件）。"""
    return _list_task_storage_slot_json_files(task, "current")


def list_task_storage_history_json_files(task: LibraryTask) -> list[LibraryFile]:
    """任务分层目录 history/ 下已归档的 JSON 模板。"""
    return _list_task_storage_slot_json_files(task, "history")


def list_task_template_files_by_role(task: LibraryTask, role: str) -> list[LibraryFile]:
    out: list[LibraryFile] = []
    for lf in task.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE).order_by(
        "-created_at", "-id"
    ):
        if infer_template_file_role(lf) != role:
            continue
        if role == LibraryTaskTemplateBindingHistory.ROLE_JSON and is_auxiliary_template_json_file(lf):
            continue
        out.append(lf)
    return out


def replace_task_template_file_binding(
    *,
    task: LibraryTask,
    new_file: LibraryFile,
    user,
    source: str = "",
) -> dict[str, Any]:
    """将任务上同角色（pdf/json）的旧绑定替换为新文件，并写入历史记录。"""
    if new_file.category != LibraryFile.CATEGORY_TEMPLATE:
        return {"ok": False, "error": "仅支持模板分类文件"}
    role = infer_template_file_role(new_file)
    if role not in (
        LibraryTaskTemplateBindingHistory.ROLE_PDF,
        LibraryTaskTemplateBindingHistory.ROLE_JSON,
    ):
        return {"ok": False, "error": "仅支持 PDF 或 JSON 模板文件"}
    if role == LibraryTaskTemplateBindingHistory.ROLE_JSON and is_auxiliary_template_json_file(
        new_file
    ):
        return {
            "ok": False,
            "error": "前端规则 JSON / 矩阵 JSON 不参与任务模板绑定，请使用「保存坐标模板」",
        }
    if not library_user_may_edit_library_task(user, task):
        return {"ok": False, "error": "无权维护该任务模板绑定"}

    old_files = list_task_template_files_by_role(task, role)
    if role == LibraryTaskTemplateBindingHistory.ROLE_JSON:
        seen_ids = {int(f.pk) for f in old_files}
        for lf in list_task_storage_current_json_files(task):
            if int(lf.pk) == int(new_file.pk) or int(lf.pk) in seen_ids:
                continue
            old_files.append(lf)
            seen_ids.add(int(lf.pk))
    replaced_ids: list[int] = []
    for old in old_files:
        if old.pk == new_file.pk:
            continue
        LibraryTaskTemplateBindingHistory.objects.create(
            library_task=task,
            library_file=old,
            file_id_snapshot=old.pk,
            original_name_snapshot=old.original_name or "",
            file_role=role,
            replaced_by=user if getattr(user, "is_authenticated", False) else None,
            replaced_by_file=new_file,
            source=(source or "").strip()[:64],
        )
        detach_files_from_tasks([old.pk], [task.pk])
        project_ids = list(task.projects.values_list("id", flat=True))
        if project_ids:
            detach_files_from_projects([old.pk], project_ids)
        replaced_ids.append(old.pk)
        if not old.library_tasks.exists():
            from apps.core.template_storage_service import archive_template_file_for_task

            archive_template_file_for_task(old, task)

    from apps.core.template_storage_service import relocate_library_template_file, _write_manifest_for_task

    relocate_library_template_file(new_file, task=task, slot="current", write_manifest=False)
    attach_files_to_tasks([new_file.pk], [task.pk], user)
    _write_manifest_for_task(task)
    project_ids = list(task.projects.values_list("id", flat=True))
    if project_ids:
        attach_files_to_projects([new_file.pk], project_ids, user)

    return {
        "ok": True,
        "library_task_id": task.pk,
        "file_role": role,
        "replaced_file_ids": replaced_ids,
        "bound_file_id": new_file.pk,
    }


def collect_task_template_unbind_ids(
    task: LibraryTask,
    file_id: int,
    *,
    include_paired: bool = False,
) -> tuple[list[int], str]:
    """
    解析要从任务解除绑定的文件 id。
    默认仅解除 file_id 对应文件；include_paired=True 时连带解除同名 stem 的 PDF/主 JSON 配对。
    """
    lf = (
        LibraryFile.objects.filter(
            pk=file_id,
            category=LibraryFile.CATEGORY_TEMPLATE,
            library_tasks=task,
        )
        .first()
    )
    if lf is None:
        return [], ""

    ids = [int(lf.pk)]
    stem = Path(lf.original_name or "").stem or (lf.original_name or str(lf.pk))

    if not include_paired or is_auxiliary_template_json_file(lf):
        return ids, stem

    name_low = (lf.original_name or "").lower()
    for other in task.library_files.filter(category=LibraryFile.CATEGORY_TEMPLATE):
        if other.pk == lf.pk:
            continue
        if Path(other.original_name or "").stem != Path(lf.original_name or "").stem:
            continue
        other_low = (other.original_name or "").lower()
        if name_low.endswith(".pdf") and other_low.endswith(".json"):
            if not is_auxiliary_template_json_file(other):
                ids.append(int(other.pk))
        elif name_low.endswith(".json") and other_low.endswith(".pdf"):
            ids.append(int(other.pk))

    return list(dict.fromkeys(ids)), stem


def unbind_template_files_from_task(
    task: LibraryTask,
    file_id: int,
    *,
    user,
    include_paired: bool = False,
) -> dict[str, Any]:
    """从任务模板解除文件绑定（不删除文件库中的文件）。默认仅解除指定文件；include_paired=True 时连带同名 stem 的配对文件。"""
    if not library_user_may_edit_library_task(user, task):
        return {"ok": False, "error": "无权维护该任务模板"}
    ids, stem = collect_task_template_unbind_ids(
        task, file_id, include_paired=include_paired
    )
    if not ids:
        return {"ok": False, "error": "该文件未绑定到此任务模板"}

    target_lf = LibraryFile.objects.filter(pk=file_id).first()
    names = list(
        LibraryFile.objects.filter(pk__in=ids).values_list("original_name", flat=True)
    )
    detach_files_from_tasks(ids, [task.pk])
    project_ids = list(task.projects.values_list("id", flat=True))
    if project_ids:
        detach_files_from_projects(ids, project_ids)

    if len(ids) > 1:
        msg = f"已解除绑定「{stem}」下的 {len(ids)} 个文件（{'、'.join(names)}）"
    elif target_lf is not None:
        role = infer_template_file_role(target_lf)
        label = names[0] if names else stem
        if role == LibraryTaskTemplateBindingHistory.ROLE_PDF:
            msg = f"已解除 PDF 绑定：{label}"
        elif role == LibraryTaskTemplateBindingHistory.ROLE_JSON:
            if is_auxiliary_template_json_file(target_lf):
                msg = f"已解除辅助 JSON 绑定：{label}"
            else:
                msg = f"已解除 JSON 绑定：{label}"
        else:
            msg = f"已解除绑定：{label}"
    else:
        msg = f"已解除绑定：{names[0] if names else stem}"
    return {"ok": True, "detached_ids": ids, "message": msg}


def restore_task_template_from_history(*, history_id: int, user) -> dict[str, Any]:
    hist = (
        LibraryTaskTemplateBindingHistory.objects.select_related(
            "library_task", "library_file"
        )
        .filter(pk=history_id)
        .first()
    )
    if hist is None:
        return {"ok": False, "error": "历史记录不存在"}
    if not library_user_may_edit_library_task(user, hist.library_task):
        return {"ok": False, "error": "无权维护该任务模板"}
    lf = hist.library_file
    if lf is None or lf.deleted_at:
        return {"ok": False, "error": "历史模板文件已删除，无法恢复"}
    try:
        path = pipeline_service.library_absolute_path(lf.relative_path)
        if not path.is_file():
            return {"ok": False, "error": "历史模板文件已不存在，无法恢复"}
    except Exception:
        return {"ok": False, "error": "历史模板文件无法访问"}

    return replace_task_template_file_binding(
        task=hist.library_task,
        new_file=lf,
        user=user,
        source="restore_history",
    )


def _editor_json_file_accessible(user, task: LibraryTask, lf: LibraryFile) -> bool:
    """编辑器下拉是否可列出该 JSON（含已轮换进历史、已从任务解绑的文件）。"""
    return library_template_file_accessible_for_task(user, task, lf)


def list_task_editor_template_json_options(
    task: LibraryTask,
    *,
    user,
    pdf_template_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    模板编辑器下拉：仅当前任务的主 JSON、其它已绑定 JSON、绑定历史中的 JSON（不含全库 500 条）。
    """
    primary = pick_task_primary_json_template(task)
    if pdf_template_id is not None:
        on_task = LibraryFile.objects.filter(
            pk=int(pdf_template_id),
            category=LibraryFile.CATEGORY_TEMPLATE,
            library_tasks=task,
        ).exists()
        if not on_task:
            primary = None

    seen: set[int] = set()
    rows: list[dict[str, Any]] = []

    def _append(lf: LibraryFile | None, *, kind: str, label: str) -> None:
        if lf is None or lf.pk in seen:
            return
        if not (lf.original_name or "").lower().endswith(".json"):
            return
        if is_auxiliary_template_json_file(lf):
            return
        if not _editor_json_file_accessible(user, task, lf):
            return
        seen.add(int(lf.pk))
        rows.append(
            {
                "id": int(lf.pk),
                "name": lf.original_name or f"#{lf.pk}",
                "kind": kind,
                "label": label,
                "isPrimary": kind == "primary",
            }
        )

    history_qs = (
        LibraryTaskTemplateBindingHistory.objects.filter(
            library_task=task,
            file_role=LibraryTaskTemplateBindingHistory.ROLE_JSON,
        )
        .select_related("library_file", "replaced_by_file")
        .order_by("-replaced_at", "-id")
    )
    history_label_by_id: dict[int, str] = {}
    history_ids: set[int] = set()
    for hist in history_qs:
        lf = hist.library_file
        if lf is None and hist.file_id_snapshot:
            lf = LibraryFile.objects.filter(
                pk=int(hist.file_id_snapshot), deleted_at__isnull=True
            ).first()
        if lf is None:
            continue
        if not hist.file_still_available:
            continue
        ts = hist.replaced_at.strftime("%Y-%m-%d %H:%M") if hist.replaced_at else ""
        history_label_by_id[int(lf.pk)] = f"历史 {ts}".strip() if ts else "历史"
        history_ids.add(int(lf.pk))

    if primary is not None:
        _append(primary, kind="primary", label="主模板")

    def _is_archived_path(lf: LibraryFile) -> bool:
        return "/history/" in str(lf.relative_path or "").replace("\\", "/")

    for lf in list_task_template_files_by_role(task, LibraryTaskTemplateBindingHistory.ROLE_JSON):
        if primary is not None and lf.pk == primary.pk:
            continue
        if int(lf.pk) in history_ids or _is_archived_path(lf):
            continue
        _append(lf, kind="bound", label="当前绑定")

    for hist in history_qs:
        lf = hist.library_file
        if lf is None and hist.file_id_snapshot:
            lf = LibraryFile.objects.filter(
                pk=int(hist.file_id_snapshot), deleted_at__isnull=True
            ).first()
        if lf is None or not hist.file_still_available:
            continue
        label = history_label_by_id.get(int(lf.pk), "历史")
        _append(lf, kind="history", label=label)

    for lf in list_task_storage_history_json_files(task):
        if int(lf.pk) in seen:
            continue
        _append(lf, kind="history", label="历史·归档")

    return rows


def serialize_binding_history_row(row: LibraryTaskTemplateBindingHistory) -> dict[str, Any]:
    lf = row.library_file
    new_lf = row.replaced_by_file
    return {
        "id": row.pk,
        "fileRole": row.file_role,
        "fileId": row.file_id_snapshot,
        "fileName": row.original_name_snapshot or (lf.original_name if lf else ""),
        "replacedAt": row.replaced_at.isoformat() if row.replaced_at else "",
        "replacedByUsername": (row.replaced_by.username if row.replaced_by else ""),
        "newFileId": new_lf.pk if new_lf else None,
        "newFileName": new_lf.original_name if new_lf else "",
        "source": row.source or "",
        "canRestore": row.file_still_available,
    }
