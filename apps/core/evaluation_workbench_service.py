"""评价部委托管理 / 项目工作台：展示评价报告表（预留评价报告书）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from django.urls import reverse

from apps.core import f1_eval_projects as projects
from apps.core import f1_eval_service as svc
from apps.core.library_access import (
    library_user_may_access_f1_eval,
    library_user_may_assign_f1_eval_projects,
    library_user_may_manage_f1_eval_projects,
)


DOC_TYPE_FORM = "eval_form"  # 评价报告表
DOC_TYPE_BOOK = "eval_book"  # 评价报告书


def evaluation_doc_type_labels() -> List[Dict[str, str]]:
    return [
        {
            "key": DOC_TYPE_FORM,
            "label": "评价报告表",
            "available": True,
            "hint": "当前可用：新建、指派与编辑评价报告表项目。",
        },
        {
            "key": DOC_TYPE_BOOK,
            "label": "评价报告书",
            "available": True,
            "hint": "上传评价信息表与附件，结构化编辑后编译 PDF（后装/加速器 · 预评/控评）。",
            "open_url": reverse("evaluation_report_list"),
            "create_url": reverse("evaluation_report_create"),
        },
    ]


def _book_open_url(report_id: int) -> str:
    return reverse("evaluation_report_detail", kwargs={"pk": int(report_id)})


def list_evaluation_book_rows(user=None) -> List[Dict[str, Any]]:
    """评价报告书列表（供委托管理 / 项目工作台展示）。"""
    from apps.core.library_access import library_user_may_access_evaluation_report
    from apps.evaluation_report.models import EvaluationReport

    if user is not None and not library_user_may_access_evaluation_report(user):
        return []
    rows: List[Dict[str, Any]] = []
    qs = (
        EvaluationReport.objects.select_related("hospital", "created_by")
        .order_by("-updated_at", "-id")[:200]
    )
    for r in qs:
        hospital_name = ""
        if r.hospital_id:
            hospital_name = (r.hospital.name or "").strip()
        has_pdf = bool(r.output_pdf)
        rows.append(
            {
                "id": f"book-{r.pk}",
                "report_id": r.pk,
                "name": (r.title or "").strip() or f"评价报告书 #{r.pk}",
                "doc_type": DOC_TYPE_BOOK,
                "doc_type_label": "评价报告书",
                "hospital_id": r.hospital_id,
                "hospital_name": hospital_name,
                "hospital_label": hospital_name or "未挂载医院",
                "report_subtype": r.report_subtype or "",
                "report_subtype_label": r.report_subtype_label,
                "status": r.status,
                "status_label": r.get_status_display() if hasattr(r, "get_status_display") else r.status,
                "is_completed": has_pdf or str(r.status or "") == "compiled",
                "is_dispatched": True,
                "dispatch_label": "—",
                "creator_label": getattr(r.created_by, "username", None) or "—",
                "updated_at": r.updated_at,
                "open_url": _book_open_url(r.pk),
                "edit_url": reverse("evaluation_report_structured_edit", kwargs={"pk": r.pk}),
            }
        )
    return rows



def _f1_open_url(project_id: str = "") -> str:
    base = reverse("f1_eval_workbench")
    pid = (project_id or "").strip()
    if not pid:
        return base
    return f"{base}?project={quote(pid)}"


def _workbench_url(project_id: str = "") -> str:
    base = reverse("library_projects")
    pid = (project_id or "").strip()
    if not pid:
        return base
    return f"{base}?project={quote(pid)}"


def _commission_url(project_id: str = "") -> str:
    base = reverse("commission_manage")
    pid = (project_id or "").strip()
    if not pid:
        return base
    return f"{base}?project={quote(pid)}"


def _filled_field_count(data: Dict[str, Any]) -> int:
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    n = 0
    for v in fields.values():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, (list, dict)) and not v:
            continue
        n += 1
    return n


def _cover_brief(cover: Dict[str, Any]) -> str:
    code = str(cover.get("report_code") or "").strip()
    full = str(cover.get("report_full_name") or "").strip()
    org = str(cover.get("cover_org_line") or "").strip()
    if code and full:
        return f"{code} · {full}"
    if code:
        return code
    if full:
        return full
    if org:
        return org
    return ""


def _enrich_eval_row(item: Dict[str, Any]) -> Dict[str, Any]:
    """补充派工、完成、信息表/封面与跳转链接。"""
    row = dict(item)
    pid = str(row.get("id") or "").strip()
    row["doc_type"] = DOC_TYPE_FORM
    row["doc_type_label"] = "评价报告表"
    row["open_url"] = _f1_open_url(pid)
    row["workbench_url"] = _workbench_url(pid)
    row["commission_url"] = _commission_url(pid)

    assignees = list(row.get("assignees") or [])
    names = [str(a.get("username") or "").strip() for a in assignees if a]
    names = [n for n in names if n]
    row["assignee_names"] = names
    row["assignee_summary"] = "、".join(names) if names else ""
    row["is_dispatched"] = bool(names)
    row["dispatch_label"] = row["assignee_summary"] if names else "未派工"
    row["dispatch_status"] = "assigned" if names else "unassigned"

    creator = str(row.get("created_by_username") or "").strip()
    row["creator_label"] = creator or "—"

    has_body = False
    has_att = False
    has_info_sheet = False
    filled = 0
    cover: Dict[str, Any] = {}
    decl: Dict[str, Any] = {}
    if pid:
        pdir = projects._project_dir(None, pid)
        has_body = (pdir / "output" / "f1_eval_body.pdf").is_file()
        has_att = (pdir / "output" / "f1_eval_attachments.pdf").is_file()
        has_info_sheet = (pdir / "uploads" / "info_sheet.pdf").is_file()
        data = projects._read_json(pdir / "data" / "report_data.json", {})
        if isinstance(data, dict):
            filled = _filled_field_count(data)
            raw_cover = data.get("cover_meta")
            if isinstance(raw_cover, dict):
                cover = dict(raw_cover)
                if isinstance(cover.get("declaration"), dict):
                    decl = dict(cover["declaration"])

    row["has_body_pdf"] = has_body
    row["has_att_pdf"] = has_att
    row["has_info_sheet"] = has_info_sheet
    row["info_sheet_label"] = "已上传" if has_info_sheet else "未上传"
    row["filled_field_count"] = filled
    row["cover_meta"] = cover
    row["cover_declaration"] = decl
    row["cover_brief"] = _cover_brief(cover)
    row["has_cover_info"] = bool(row["cover_brief"] or decl)
    row["cover_label"] = row["cover_brief"] if row["cover_brief"] else "未填写"
    row["hospital_label"] = str(row.get("hospital_name") or "").strip() or "未挂载医院"
    row["has_hospital"] = bool(
        row.get("hospital_id") not in (None, "") or str(row.get("hospital_name") or "").strip()
    )

    if has_body:
        row["progress_status"] = "completed"
        row["progress_label"] = "已生成"
        row["progress_detail"] = "正文 PDF 已生成" + (" · 附件已生成" if has_att else "")
        row["is_completed"] = True
    elif filled >= 8 or names:
        row["progress_status"] = "in_progress"
        row["progress_label"] = "编制中"
        row["progress_detail"] = f"已填写约 {filled} 项" if filled else "已派工，待编制"
        row["is_completed"] = False
    else:
        row["progress_status"] = "not_started"
        row["progress_label"] = "未开始"
        row["progress_detail"] = "尚未派工或填写"
        row["is_completed"] = False

    return row


def _resolve_editable_project(user, project_id: str) -> tuple[Dict[str, Any], Any]:
    """返回 (index_item, project_dir)；校验可打开/可编辑。"""
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("未指定项目")
    ws = svc.ensure_workspace(user.pk)
    items = projects.list_projects(ws, user=user)
    found = None
    for it in items:
        if str(it.get("id") or "") == pid:
            found = it
            break
    if found is None:
        raise PermissionError("项目不存在或无权访问")
    if not projects.user_may_edit_project(user, found):
        raise PermissionError("无权修改该项目")
    return found, projects._project_dir(None, pid)


def _hospital_options() -> List[Dict[str, Any]]:
    try:
        from apps.core.models import CommissionOrganization

        rows = (
            CommissionOrganization.objects.filter(
                level=CommissionOrganization.LEVEL_HOSPITAL,
                is_active=True,
            )
            .order_by("name", "id")
            .values("id", "name")[:500]
        )
        return [{"id": r["id"], "name": str(r["name"] or "").strip() or f"医院#{r['id']}"} for r in rows]
    except Exception:
        return []


def _assignable_user_options(user) -> List[Dict[str, Any]]:
    from apps.core.library_access import f1_eval_assignable_users_for

    out: List[Dict[str, Any]] = []
    for u in f1_eval_assignable_users_for(user):
        role = getattr(getattr(u, "profile", None), "role", None)
        out.append(
            {
                "id": u.pk,
                "username": u.username,
                "role_name": (role.name if role else "") or "",
            }
        )
    return out


def handle_evaluation_workbench_post(request):
    """
    评价部项目工作台表单动作：打开 / 新建 / 指派 / 挂载医院 / 信息表 / 封面。
    成功时返回 HttpResponseRedirect；未知 action 返回 None。
    """
    from django.contrib import messages
    from django.http import HttpResponseRedirect

    action = (request.POST.get("action") or "").strip()
    known = {
        "eval_open_project",
        "eval_create_project",
        "eval_assign_project",
        "eval_set_hospital",
        "eval_upload_info_sheet",
        "eval_save_cover",
    }
    if action not in known:
        return None
    if not library_user_may_access_f1_eval(request.user):
        messages.error(request, "无权操作评价报告表项目")
        return HttpResponseRedirect(reverse("library_projects"))

    ws = svc.ensure_workspace(request.user.pk)

    def _back(pid: str = "") -> HttpResponseRedirect:
        return HttpResponseRedirect(_workbench_url(pid))

    if action == "eval_open_project":
        pid = (request.POST.get("project_id") or "").strip()
        if not pid:
            messages.error(request, "未指定项目")
            return _back()
        try:
            projects.open_project(ws, pid, user=request.user)
        except PermissionError as exc:
            messages.error(request, str(exc) or "无权打开该项目")
            return _back()
        except Exception as exc:
            messages.error(request, f"打开失败：{exc}")
            return _back()
        return HttpResponseRedirect(_f1_open_url(pid))

    if action == "eval_create_project":
        if not library_user_may_manage_f1_eval_projects(request.user):
            messages.error(request, "无权新建评价报告表项目")
            return _back()
        name = (request.POST.get("name") or "").strip()
        hospital_id = (request.POST.get("hospital_id") or "").strip()
        hospital_name = (request.POST.get("hospital_name") or "").strip()
        try:
            meta = projects.create_project(
                ws,
                name,
                hospital_id=hospital_id or None,
                hospital_name=hospital_name,
                user=request.user,
            )
            pid = str(meta.get("id") or "")
            projects.open_project(ws, pid, user=request.user)
        except PermissionError as exc:
            messages.error(request, str(exc) or "无权新建")
            return _back()
        except Exception as exc:
            messages.error(request, f"新建失败：{exc}")
            return _back()
        messages.success(request, f"已新建并打开：{meta.get('name') or pid}")
        return HttpResponseRedirect(_f1_open_url(pid))

    if action == "eval_assign_project":
        if not library_user_may_assign_f1_eval_projects(request.user):
            messages.error(request, "无权指派评价报告表项目")
            return _back()
        pid = (request.POST.get("project_id") or "").strip()
        assignee_ids = request.POST.getlist("assignee_ids")
        if not pid:
            messages.error(request, "未指定项目")
            return _back()
        try:
            meta = projects.assign_project(
                ws,
                pid,
                assignee_ids=assignee_ids,
                user=request.user,
                replace=True,
            )
        except PermissionError as exc:
            messages.error(request, str(exc) or "指派失败")
            return _back(pid)
        except Exception as exc:
            messages.error(request, f"指派失败：{exc}")
            return _back(pid)
        names = "、".join(
            str(a.get("username") or "") for a in (meta.get("assignees") or []) if a.get("username")
        )
        if names:
            messages.success(request, f"已指派「{meta.get('name') or pid}」给：{names}")
        else:
            messages.success(request, f"已清空「{meta.get('name') or pid}」的指派")
        return _back(pid)

    if action == "eval_set_hospital":
        pid = (request.POST.get("project_id") or "").strip()
        clear = (request.POST.get("clear_hospital") or "").strip() in ("1", "true", "on")
        hospital_id = "" if clear else (request.POST.get("hospital_id") or "").strip()
        hospital_name = "" if clear else (request.POST.get("hospital_name") or "").strip()
        if not pid:
            messages.error(request, "未指定项目")
            return _back()
        try:
            meta = projects.update_project_hospital(
                ws,
                pid,
                hospital_id=None if clear else (hospital_id or None),
                hospital_name="" if clear else hospital_name,
                user=request.user,
            )
        except PermissionError as exc:
            messages.error(request, str(exc) or "无权修改医院挂载")
            return _back(pid)
        except Exception as exc:
            messages.error(request, f"挂载医院失败：{exc}")
            return _back(pid)
        if clear or not (meta.get("hospital_name") or meta.get("hospital_id")):
            messages.success(request, f"已取消「{meta.get('name') or pid}」的医院挂载")
        else:
            messages.success(
                request,
                f"已将「{meta.get('name') or pid}」挂载到：{meta.get('hospital_name') or meta.get('hospital_id')}",
            )
        return _back(pid)

    if action == "eval_upload_info_sheet":
        pid = (request.POST.get("project_id") or "").strip()
        uploaded = request.FILES.get("info_sheet")
        if not pid:
            messages.error(request, "未指定项目")
            return _back()
        if not uploaded:
            messages.error(request, "请选择信息表 PDF 文件")
            return _back(pid)
        try:
            _resolve_editable_project(request.user, pid)
            projects.open_project(ws, pid, user=request.user)
            path = svc.save_info_sheet(ws, uploaded)
            svc.extract_from_info_sheet(ws, path)
            projects.save_project(ws, project_id=pid, user=request.user)
        except PermissionError as exc:
            messages.error(request, str(exc) or "无权上传信息表")
            return _back(pid)
        except Exception as exc:
            messages.error(request, f"信息表处理失败：{exc}")
            return _back(pid)
        messages.success(request, "信息表已上传并提取写入项目")
        return _back(pid)

    if action == "eval_save_cover":
        pid = (request.POST.get("project_id") or "").strip()
        if not pid:
            messages.error(request, "未指定项目")
            return _back()
        patch = {
            "report_code": (request.POST.get("report_code") or "").strip(),
            "report_date": (request.POST.get("report_date") or "").strip(),
            "cover_org_line": (request.POST.get("cover_org_line") or "").strip(),
            "cover_project_type_line": (request.POST.get("cover_project_type_line") or "").strip(),
            "construction_unit": (request.POST.get("construction_unit") or "").strip(),
            "evaluation_unit": (request.POST.get("evaluation_unit") or "").strip(),
            "report_full_name": (request.POST.get("report_full_name") or "").strip(),
            "declaration": {
                "legal_person": (request.POST.get("decl_legal") or "").strip(),
                "project_leader": (request.POST.get("decl_leader") or "").strip(),
                "project_leader_cert": (request.POST.get("decl_leader_cert") or "").strip(),
                "author": (request.POST.get("decl_author") or "").strip(),
                "author_cert": (request.POST.get("decl_author_cert") or "").strip(),
                "reviewer": (request.POST.get("decl_reviewer") or "").strip(),
                "reviewer_cert": (request.POST.get("decl_reviewer_cert") or "").strip(),
                "issuer": (request.POST.get("decl_issuer") or "").strip(),
                "issuer_cert": (request.POST.get("decl_issuer_cert") or "").strip(),
            },
        }
        try:
            _resolve_editable_project(request.user, pid)
            projects.open_project(ws, pid, user=request.user)
            cert = request.FILES.get("certificate")
            if cert:
                svc.save_cover_certificate(ws, cert)
            svc.save_cover_meta(ws, patch)
            projects.save_project(ws, project_id=pid, user=request.user)
        except PermissionError as exc:
            messages.error(request, str(exc) or "无权保存封面信息")
            return _back(pid)
        except Exception as exc:
            messages.error(request, f"保存封面失败：{exc}")
            return _back(pid)
        messages.success(request, "封面与声明信息已保存")
        return _back(pid)

    return None


def build_evaluation_workbench_context(
    user,
    *,
    selected_project_id: str = "",
) -> Dict[str, Any]:
    """评价部项目工作台 / 委托管理共用数据。"""
    empty_caps = {"can_create": False, "can_delete": False, "can_assign": False}
    if not library_user_may_access_f1_eval(user):
        return {
            "doc_types": evaluation_doc_type_labels(),
            "hospitals": [],
            "ungrouped": [],
            "projects": [],
            "book_projects": [],
            "selected_project": None,
            "hospital_options": [],
            "assignable_users": [],
            "overview": {
                "total": 0,
                "form_count": 0,
                "book_count": 0,
                "hospital_count": 0,
                "dispatched": 0,
                "undispatched": 0,
                "completed": 0,
                "incomplete": 0,
            },
            "capabilities": empty_caps,
            "f1_workbench_url": reverse("f1_eval_workbench"),
            "evaluation_report_list_url": reverse("evaluation_report_list"),
            "evaluation_report_create_url": reverse("evaluation_report_create"),
        }

    ws = svc.ensure_workspace(user.pk)
    grouped = projects.list_projects_grouped(ws, user=user)
    items = [_enrich_eval_row(it) for it in (grouped.get("projects") or [])]

    hospitals = []
    for g in grouped.get("hospitals") or []:
        hospital_projects = [_enrich_eval_row(it) for it in (g.get("projects") or [])]
        hospitals.append(
            {
                **g,
                "projects": hospital_projects,
                "form_count": len(hospital_projects),
                "book_count": 0,
                "dispatched": sum(1 for p in hospital_projects if p.get("is_dispatched")),
                "completed": sum(1 for p in hospital_projects if p.get("is_completed")),
            }
        )

    ungrouped = [_enrich_eval_row(it) for it in (grouped.get("ungrouped") or [])]

    sel_id = (selected_project_id or "").strip()
    selected: Optional[Dict[str, Any]] = None
    if sel_id:
        for it in items:
            if str(it.get("id") or "") == sel_id:
                selected = dict(it)
                break
        if selected is not None:
            # 用 ensure_cover_meta 补全默认字段，便于表单展示
            try:
                pdir = projects._project_dir(None, sel_id)
                selected["cover_meta"] = svc.get_cover_meta(pdir)
                decl = selected["cover_meta"].get("declaration")
                selected["cover_declaration"] = decl if isinstance(decl, dict) else {}
            except Exception:
                pass

    caps = dict(grouped.get("capabilities") or {})
    caps["can_create"] = library_user_may_manage_f1_eval_projects(user)
    caps["can_delete"] = library_user_may_manage_f1_eval_projects(user)
    caps["can_assign"] = library_user_may_assign_f1_eval_projects(user)

    book_rows = list_evaluation_book_rows(user)
    dispatched = sum(1 for p in items if p.get("is_dispatched"))
    completed = sum(1 for p in items if p.get("is_completed"))
    book_completed = sum(1 for p in book_rows if p.get("is_completed"))

    return {
        "doc_types": evaluation_doc_type_labels(),
        "hospitals": hospitals,
        "ungrouped": ungrouped,
        "projects": items,
        "book_projects": book_rows,
        "selected_project": selected,
        "selected_project_id": sel_id,
        "hospital_options": _hospital_options(),
        "assignable_users": _assignable_user_options(user) if caps["can_assign"] else [],
        "overview": {
            "total": len(items) + len(book_rows),
            "form_count": len(items),
            "book_count": len(book_rows),
            "hospital_count": len(hospitals),
            "dispatched": dispatched,
            "undispatched": max(0, len(items) - dispatched),
            "completed": completed + book_completed,
            "incomplete": max(0, len(items) - completed) + max(0, len(book_rows) - book_completed),
        },
        "capabilities": caps,
        "f1_workbench_url": reverse("f1_eval_workbench"),
        "evaluation_report_list_url": reverse("evaluation_report_list"),
        "evaluation_report_create_url": reverse("evaluation_report_create"),
    }

