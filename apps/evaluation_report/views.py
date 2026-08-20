from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.core.library_access import library_user_may_access_evaluation_report, role_has
from apps.core.models import CommissionOrganization

from .constants import (
    REPORT_TYPE_CHOICES,
    SUBTYPE_BRACHY_PRE,
    template_choices_for_ui,
    template_for_type,
)
from .conversion_preview import (
    convert_library_file_to_markdown,
    convert_markdown_to_tex_fragment,
    library_file_path,
    media_url_for_library_file,
    preview_kind_for_file,
    rewrite_markdown_image_urls,
)
from .keywords_service import (
    apply_project_keywords_to_main_tex,
    project_keywords_grouped,
    save_project_keywords,
    schema_rows,
    seed_schema_from_csv,
    sync_project_keywords_from_markdown,
)
from .library_integration import (
    add_slot_files,
    checklist_for_report,
    ensure_upload_slots,
    remove_upload_file,
    reorder_upload_files,
    update_upload_file_display,
)
from .upload_staging import (
    add_staging_files,
    clear_staging,
    commit_staging_files,
    list_staging_files,
    remove_staging_file,
    reorder_staging_files,
    update_staging_display,
)
from .models import EvaluationKeywordSchema, EvaluationReport, EvaluationReportUpload
from .services import build_report_pdf, delete_evaluation_report, generate_base_report, report_has_base_content
from .template_preview import (
    TemplatePreviewError,
    build_latex_file_tree,
    latex_project_kind,
    latex_project_kind_for_report,
    latex_project_kind_label,
    latex_root_for_report,
    latex_source_kind,
    list_template_tex_files,
    read_template_tex,
    resolve_template_preview_selection,
    write_template_tex,
)


def _require_evaluation_perm(request):
    if not library_user_may_access_evaluation_report(request.user):
        messages.error(request, "无权访问评价报告功能")
        return redirect(reverse("dashboard"))
    return None


def _hospitals_qs():
    return CommissionOrganization.objects.filter(
        level=CommissionOrganization.LEVEL_HOSPITAL,
        is_active=True,
    ).order_by("name")


@login_required
def evaluation_report_list(request):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    qs = EvaluationReport.objects.select_related("hospital", "created_by").all()
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "evaluation_report/list.html",
        {
            "reports": page,
        },
    )


@login_required
def evaluation_report_create(request):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    hospitals = _hospitals_qs()

    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        hospital_id = request.POST.get("hospital")
        report_subtype = (request.POST.get("report_subtype") or "").strip() or SUBTYPE_BRACHY_PRE
        latex_template_key = (request.POST.get("latex_template_key") or "").strip()

        if not title:
            messages.error(request, "请填写报告标题")
            return redirect("evaluation_report_create")
        hospital = hospitals.filter(pk=hospital_id).first()
        if not hospital:
            messages.error(request, "请选择医院")
            return redirect("evaluation_report_create")

        from .latex_template_service import get_template

        if latex_template_key:
            get_template(latex_template_key)
        else:
            tpl = template_for_type(report_subtype)
            if tpl:
                latex_template_key = tpl.key
            else:
                messages.error(request, "请选择 LaTeX 模板")
                return redirect("evaluation_report_create")

        report = EvaluationReport.objects.create(
            title=title,
            hospital=hospital,
            device_category="",
            report_subtype=report_subtype,
            latex_template_key=latex_template_key,
            status=EvaluationReport.Status.UPLOADING,
            allow_incomplete_build=getattr(settings, "EVALUATION_REPORT_ALLOW_INCOMPLETE", True),
            created_by=request.user,
        )
        report.ensure_work_dir()
        ensure_upload_slots(report)
        messages.success(
            request,
            f"已创建「{report.title}」。可先上传评价信息表，也可直接进入正文编辑。",
        )
        return redirect("evaluation_report_evaluation_form", pk=report.pk)

    return render(
        request,
        "evaluation_report/form.html",
        {
            "hospitals": hospitals,
            "subtype_choices": REPORT_TYPE_CHOICES,
            "template_registry": template_choices_for_ui(),
        },
    )


@login_required
def evaluation_report_template(request, pk: int):
    """预览报告绑定的 LaTeX 模板源码（创建后默认进入此页）。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(
        EvaluationReport.objects.select_related("hospital", "created_by"),
        pk=pk,
    )
    selected = (request.GET.get("file") or "main.tex").strip()
    view_param = (request.GET.get("view") or "").strip()
    edit_mode = request.GET.get("edit") == "1"

    try:
        latex_root = latex_root_for_report(report)
        project_kind = latex_project_kind_for_report(report, latex_root)
        tex_files = list_template_tex_files(report)
        file_tree = build_latex_file_tree(latex_root, report)
        preview = resolve_template_preview_selection(
            report, selected, view=view_param
        )
        if edit_mode and (
            preview["initial_view"] == "asset"
            or not preview["selected_file"].lower().endswith(".tex")
        ):
            edit_mode = False
        show_tex_edit_toggle = (
            preview["initial_view"] == "tex"
            and preview["selected_file"].lower().endswith(".tex")
        )
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_report_detail", pk=pk)

    preview_asset = preview.get("preview_asset")
    return render(
        request,
        "evaluation_report/template_preview.html",
        {
            "report": report,
            "template_info": report.template_def,
            "latex_root": str(latex_root),
            "latex_source": latex_source_kind(report),
            "latex_project_kind": project_kind,
            "latex_project_kind_label": latex_project_kind_label(project_kind),
            "tex_files": tex_files,
            "file_tree": file_tree,
            "selected_file": preview["selected_file"],
            "initial_view": preview["initial_view"],
            "preview_asset": preview_asset,
            "tex_content": preview["tex_content"],
            "tex_lines": preview["tex_content"].splitlines(),
            "edit_mode": edit_mode,
            "show_tex_edit_toggle": show_tex_edit_toggle,
        },
    )


@login_required
def evaluation_report_template_content(request, pk: int):
    """AJAX 获取 .tex 文件内容，用于无刷新切换左侧文件。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return JsonResponse({"error": "无权访问"}, status=403)

    report = get_object_or_404(EvaluationReport, pk=pk)
    selected = (request.GET.get("file") or "main.tex").strip()

    try:
        relpath, content = read_template_tex(report, selected)
    except TemplatePreviewError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    lines = content.splitlines()
    return JsonResponse(
        {
            "file": relpath,
            "content": content,
            "length": len(content),
            "line_count": len(lines) if lines else 1,
        }
    )


@login_required
@require_POST
def evaluation_report_template_save(request, pk: int):
    """保存对报告 LaTeX 工作区 .tex 文件的编辑。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    relpath = (request.POST.get("file") or "main.tex").strip()
    content = request.POST.get("content", "")

    try:
        saved = write_template_tex(report, relpath, content)
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_report_template", pk=pk)

    messages.success(request, f"已保存 {saved}")
    query = urlencode({"file": saved})
    return redirect(f"{reverse('evaluation_report_template', kwargs={'pk': pk})}?{query}")


@login_required
def evaluation_report_detail(request, pk: int):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(
        EvaluationReport.objects.select_related("hospital", "created_by"),
        pk=pk,
    )
    checklist = checklist_for_report(report)
    progress = report.upload_progress()
    has_any_attachment = any(
        bool(row.get("is_attachment") and row.get("files")) for row in checklist
    )
    return render(
        request,
        "evaluation_report/detail.html",
        {
            "report": report,
            "checklist": checklist,
            "progress": progress,
            "template_info": report.template_def,
            "has_any_attachment": has_any_attachment,
            "evaluation_form_uploaded": any(
                row.get("is_eval_form") and row.get("uploaded") for row in checklist
            ),
            "base_report_ready": report_has_base_content(report),
        },
    )


@login_required
def evaluation_report_evaluation_form(request, pk: int):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(
        EvaluationReport.objects.select_related("hospital", "created_by"),
        pk=pk,
    )
    checklist = checklist_for_report(report)
    evaluation_row = next((row for row in checklist if row.get("is_eval_form")), None)
    if evaluation_row is None:
        raise Http404("evaluation form upload slot is missing")

    return render(
        request,
        "evaluation_report/evaluation_form_upload.html",
        {
            "report": report,
            "row": evaluation_row,
            "template_info": report.template_def,
            "base_report_ready": report_has_base_content(report),
        },
    )


@login_required
def evaluation_report_attachments(request, pk: int):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(
        EvaluationReport.objects.select_related("hospital", "created_by"),
        pk=pk,
    )
    checklist = checklist_for_report(report)
    attachment_rows = [row for row in checklist if row.get("is_attachment")]
    has_any_attachment = any(row.get("files") for row in attachment_rows)
    attachment_progress = {
        "done": sum(1 for row in attachment_rows if row.get("uploaded")),
        "total": len(attachment_rows),
    }
    return render(
        request,
        "evaluation_report/attachments.html",
        {
            "report": report,
            "attachment_rows": attachment_rows,
            "has_any_attachment": has_any_attachment,
            "progress": attachment_progress,
        },
    )


@login_required
@require_POST
def evaluation_report_upload(request, pk: int, slot_key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    slot = get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)

    if slot.slot_key.startswith("attachment_"):
        messages.info(request, "附件请使用「选择文件」进入预览页，确认顺序后点击「上传文件」")
        return redirect("evaluation_report_attachments", pk=pk)

    uploaded_list = request.FILES.getlist("file")
    if not uploaded_list:
        single = request.FILES.get("file")
        uploaded_list = [single] if single else []

    if not uploaded_list:
        messages.error(request, "请选择文件")
        return redirect("evaluation_report_evaluation_form", pk=pk)

    try:
        rows = add_slot_files(report, slot, uploaded_list, request.user, replace=True)
        if len(rows) == 1:
            messages.success(request, f"已上传：{slot.label}")
        else:
            messages.success(request, f"已添加 {len(rows)} 个文件到 {slot.label}")
    except Exception as exc:
        messages.error(request, f"上传失败：{exc}")
    if slot.slot_key == "evaluation_form":
        return redirect(
            f"{reverse('evaluation_report_upload_preview', kwargs={'pk': pk, 'slot_key': slot_key})}?view=markdown"
        )
    return redirect("evaluation_report_attachments", pk=pk)


@login_required
@require_POST
def evaluation_report_generate_base(request, pk: int):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    try:
        generate_base_report(report)
        messages.success(request, "基础报告内容已生成，请继续进行结构化正文编辑")
        return redirect("evaluation_report_structured_edit", pk=pk)
    except Exception as exc:
        messages.error(request, f"基础报告生成失败：{exc}")
        return redirect(
            f"{reverse('evaluation_report_upload_preview', kwargs={'pk': pk, 'slot_key': 'evaluation_form'})}?view=markdown"
        )


@login_required
@require_POST
def evaluation_report_upload_stage(request, pk: int, slot_key: str):
    """附件：选择文件后暂存，留在材料上传清单页调整顺序。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    slot = get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    if not slot.slot_key.startswith("attachment_"):
        messages.error(request, "该槽位不支持分步上传")
        return redirect("evaluation_report_attachments", pk=pk)

    uploaded_list = request.FILES.getlist("file")
    if not uploaded_list:
        single = request.FILES.get("file")
        uploaded_list = [single] if single else []
    if not uploaded_list:
        messages.error(request, "请选择文件")
        return redirect("evaluation_report_attachments", pk=pk)

    try:
        added = add_staging_files(report, slot.slot_key, uploaded_list)
        messages.success(request, f"已选择 {len(added)} 个文件，请调整顺序后点击「确认上传」")
    except Exception as exc:
        messages.error(request, f"选择文件失败：{exc}")
        return redirect("evaluation_report_evaluation_form" if slot_key == "evaluation_form" else "evaluation_report_attachments", pk=pk)

    return redirect(f"{reverse('evaluation_report_attachments', kwargs={'pk': pk})}#slot-{slot_key}")


@login_required
@require_POST
def evaluation_report_upload_staging_commit(request, pk: int, slot_key: str):
    """附件：确认上传，文件入库并同步 appendix。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    slot = get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    try:
        count = commit_staging_files(report, slot, request.user)
        messages.success(request, f"已上传 {count} 个文件到 {slot.label}，已同步至附录模板")
    except Exception as exc:
        messages.error(request, f"上传失败：{exc}")
        return redirect(f"{reverse('evaluation_report_attachments', kwargs={'pk': pk})}#slot-{slot_key}")
    return redirect(f"{reverse('evaluation_report_attachments', kwargs={'pk': pk})}#slot-{slot_key}")


@login_required
@require_POST
def evaluation_report_upload_staging_reorder(request, pk: int, slot_key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    raw = (request.POST.get("order") or "").strip()
    ordered_ids = [x.strip() for x in raw.split(",") if x.strip()]
    try:
        reorder_staging_files(report, slot_key, ordered_ids)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True})
        messages.success(request, "已更新待上传文件顺序")
    except Exception as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"更新顺序失败：{exc}")
    return redirect(f"{reverse('evaluation_report_attachments', kwargs={'pk': pk})}#slot-{slot_key}")


@login_required
@require_POST
def evaluation_report_upload_staging_delete(request, pk: int, slot_key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    file_id = (request.POST.get("file_id") or "").strip()
    if not file_id:
        messages.error(request, "缺少文件 ID")
        return redirect("evaluation_report_attachments", pk=pk)
    try:
        remove_staging_file(report, slot_key, file_id)
        messages.success(request, "已移除待上传文件")
    except Exception as exc:
        messages.error(request, f"删除失败：{exc}")

    return redirect(f"{reverse('evaluation_report_attachments', kwargs={'pk': pk})}#slot-{slot_key}")


@login_required
@require_POST
def evaluation_report_upload_staging_display(request, pk: int, slot_key: str):
    """更新待上传附件的显示选项（A3 / 旋转 / 宽度）。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    file_id = (request.POST.get("file_id") or "").strip()
    if not file_id:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "缺少文件 ID"}, status=400)
        messages.error(request, "缺少文件 ID")
        return redirect("evaluation_report_attachments", pk=pk)
    try:
        display = update_staging_display(
            report,
            slot_key,
            file_id,
            page_mode=request.POST.get("page_mode"),
            rotate=request.POST.get("rotate"),
            width_percent=request.POST.get("width_percent"),
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True, **display})
        messages.success(request, "已更新待上传文件显示设置")
    except Exception as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"更新显示设置失败：{exc}")
    return redirect(f"{reverse('evaluation_report_attachments', kwargs={'pk': pk})}#slot-{slot_key}")


@login_required
@require_POST
def evaluation_report_upload_file_display(request, pk: int, slot_key: str, file_id: int):
    """更新已上传附件的显示选项，并同步附录 LaTeX。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    slot = get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    try:
        display = update_upload_file_display(
            report,
            slot,
            file_id,
            page_mode=request.POST.get("page_mode"),
            rotate=request.POST.get("rotate"),
            width_percent=request.POST.get("width_percent"),
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True, **display})
        messages.success(request, "已更新附件显示设置并同步附录")
    except Exception as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"更新显示设置失败：{exc}")
    return redirect(f"{reverse('evaluation_report_attachments', kwargs={'pk': pk})}#slot-{slot_key}")


@login_required
def evaluation_report_staging_media(request, pk: int, slot_key: str, file_id: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    row = next((r for r in list_staging_files(report, slot_key) if r["id"] == file_id), None)
    if not row:
        raise Http404

    import mimetypes

    path = row["path"]
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        return FileResponse(path.open("rb"), content_type=ctype)
    except FileNotFoundError as exc:
        raise Http404 from exc


@login_required
@require_POST
def evaluation_report_upload_file_delete(request, pk: int, slot_key: str, file_id: int):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    slot = get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    try:
        remove_upload_file(report, slot, file_id)
        messages.success(request, "已删除文件")
    except Exception as exc:
        messages.error(request, f"删除失败：{exc}")
    return redirect("evaluation_report_attachments", pk=pk)


@login_required
@require_POST
def evaluation_report_upload_file_reorder(request, pk: int, slot_key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    slot = get_object_or_404(EvaluationReportUpload, report=report, slot_key=slot_key)
    raw = (request.POST.get("order") or "").strip()
    ordered_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    try:
        reorder_upload_files(slot, ordered_ids)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True})
        messages.success(request, "已更新文件顺序")
    except Exception as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"更新顺序失败：{exc}")
    return redirect("evaluation_report_attachments", pk=pk)


@login_required
def evaluation_report_upload_preview(request, pk: int, slot_key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    slot = get_object_or_404(
        EvaluationReportUpload.objects.prefetch_related("files__library_file"),
        report=report,
        slot_key=slot_key,
    )

    mode = (request.GET.get("mode") or "").strip()
    is_attachment = slot.slot_key.startswith("attachment_")
    is_staging = mode == "staging" and is_attachment

    if is_staging:
        return redirect(f"{reverse('evaluation_report_attachments', kwargs={'pk': pk})}#slot-{slot_key}")

    file_id = request.GET.get("file_id")
    upload_file = None
    if file_id and str(file_id).isdigit():
        upload_file = slot.files.filter(pk=int(file_id)).select_related("library_file").first()
    if not upload_file:
        upload_file = slot.ordered_files().first()

    if not upload_file:
        target = "evaluation_report_evaluation_form" if slot_key == "evaluation_form" else "evaluation_report_attachments"
        messages.warning(request, "尚未上传文件")
        return redirect(target, pk=pk)

    lf = upload_file.library_file
    view = (request.GET.get("view") or "raw").strip()
    is_eval_form = slot.slot_key == "evaluation_form"
    work = report.ensure_work_dir()

    preview_kind = preview_kind_for_file(lf)
    media_url = media_url_for_library_file(lf)
    markdown_content = ""
    tex_content = ""
    error = ""

    if view == "markdown" and is_eval_form:
        try:
            if slot.preview_md and request.GET.get("refresh") != "1":
                markdown_content = slot.preview_md
            else:
                markdown_content = convert_library_file_to_markdown(lf, work)
                slot.preview_md = markdown_content
                slot.save(update_fields=["preview_md", "updated_at"])
                sync_project_keywords_from_markdown(
                    report, markdown_content, only_empty=True
                )
                messages.info(request, "已从 Markdown 同步项目关键词取值")
        except Exception as exc:
            error = str(exc)
    elif view == "tex" and is_eval_form:
        try:
            if not slot.preview_md or request.GET.get("refresh") == "1":
                slot.preview_md = convert_library_file_to_markdown(lf, work)
            markdown_content = slot.preview_md
            if slot.preview_tex and request.GET.get("refresh") != "1":
                tex_content = slot.preview_tex
            else:
                tex_content = convert_markdown_to_tex_fragment(markdown_content, work)
                slot.preview_tex = tex_content
                slot.save(update_fields=["preview_md", "preview_tex", "updated_at"])
        except Exception as exc:
            error = str(exc)
    elif view == "markdown" and preview_kind == "markdown":
        markdown_content = library_file_path(lf).read_text(encoding="utf-8")

    markdown_format = (request.GET.get("format") or "rendered").strip()
    if markdown_format not in ("rendered", "source"):
        markdown_format = "rendered"
    md_payload = None
    if markdown_content and markdown_format == "rendered":
        md_payload = {"text": rewrite_markdown_image_urls(markdown_content, report.pk)}

    return render(
        request,
        "evaluation_report/upload_preview.html",
        {
            "report": report,
            "slot": slot,
            "upload_file": upload_file,
            "library_file": lf,
            "view_mode": view,
            "markdown_format": markdown_format,
            "is_eval_form": is_eval_form,
            "preview_kind": preview_kind,
            "media_url": media_url,
            "markdown_content": markdown_content,
            "md_payload": md_payload,
            "tex_content": tex_content,
            "error": error,
        },
    )


@login_required
@xframe_options_sameorigin
def evaluation_report_work_media(request, pk: int, relpath: str):
    """提供评价报告工作目录 / LaTeX 工程内图片等资源。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    from .template_preview import ensure_work_latex_images, resolve_work_media_file

    try:
        ensure_work_latex_images(report)
    except Exception:  # noqa: BLE001
        pass

    target = resolve_work_media_file(report, relpath)
    if target is None:
        raise Http404

    import mimetypes

    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    try:
        return FileResponse(target.open("rb"), content_type=ctype)
    except FileNotFoundError as exc:
        raise Http404 from exc


@login_required
def evaluation_keyword_schema_list(request):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "import_csv":
            n = seed_schema_from_csv()
            messages.success(request, f"已从 CSV 同步通用关键词（新增 {n} 条）")
            return redirect("evaluation_keyword_schema_list")
        command = (request.POST.get("command") or "").strip()
        label_cn = (request.POST.get("label_cn") or "").strip()
        if command and label_cn:
            EvaluationKeywordSchema.objects.update_or_create(
                command=command,
                defaults={"label_cn": label_cn},
            )
            messages.success(request, f"已保存 \\{command}")
        return redirect("evaluation_keyword_schema_list")

    if not schema_rows():
        seed_schema_from_csv()

    return render(
        request,
        "evaluation_report/keyword_schema.html",
        {
            "schema_rows": schema_rows(),
        },
    )


@login_required
def evaluation_report_keywords(request, pk: int):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    edit_base = reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
    detail_url = reverse("evaluation_report_detail", kwargs={"pk": pk})
    back_url, back_label, next_value = _resolve_keywords_back(request, pk, edit_base, detail_url)

    if request.method == "POST":
        data = {
            key.replace("kw_", ""): request.POST.get(key, "")
            for key in request.POST
            if key.startswith("kw_")
        }
        save_project_keywords(report, data)
        try:
            from .template_preview import ensure_editable_latex

            root = ensure_editable_latex(report)
            apply_project_keywords_to_main_tex(report, root / "main.tex")
        except Exception:
            pass
        messages.success(request, "项目信息已保存，并已同步到工作区 main.tex")
        # 保存后仍留在本页，保留返回编辑页链接
        from urllib.parse import urlencode

        redirect_to = reverse("evaluation_report_keywords", kwargs={"pk": pk})
        if next_value:
            redirect_to = f"{redirect_to}?{urlencode({'next': next_value})}"
        return redirect(redirect_to)

    groups = project_keywords_grouped(report)
    return render(
        request,
        "evaluation_report/project_keywords.html",
        {
            "report": report,
            "keyword_groups": groups,
            "edit_base": edit_base,
            "back_url": back_url,
            "back_label": back_label,
            "next_value": next_value,
        },
    )


def _resolve_keywords_back(request, pk: int, edit_base: str, detail_url: str):
    """从编辑页带来的 next 优先；否则默认回结构化编辑页。"""
    raw = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if raw.startswith("/") and not raw.startswith("//") and "://" not in raw:
        if f"/{pk}/" in raw or raw.rstrip("/").endswith(f"/{pk}"):
            if "/edit" in raw:
                return raw, "返回编辑页", raw
            if raw.rstrip("/") == detail_url.rstrip("/") or raw.startswith(detail_url):
                return raw, "返回详情", raw
            return raw, "返回上一页", raw
    return edit_base, "返回编辑页", edit_base


@login_required
@xframe_options_sameorigin
def evaluation_report_pdf_view(request, pk: int):
    """内联输出报告 PDF（供 iframe / 新窗口预览）。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    if not report.output_pdf:
        raise Http404
    try:
        filename = report.output_pdf.name.split("/")[-1]
        response = FileResponse(
            report.output_pdf.open("rb"),
            content_type="application/pdf",
            filename=filename,
        )
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response
    except FileNotFoundError as exc:
        raise Http404 from exc


@login_required
def evaluation_report_pdf_preview(request, pk: int):
    """兼容旧链接：直接跳转到 PDF 内联地址。"""
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    if not report.output_pdf:
        messages.warning(request, "尚未生成报告 PDF，请先点击「生成报告 PDF」")
        return redirect("evaluation_report_detail", pk=pk)

    cache_bust = int(report.updated_at.timestamp()) if report.updated_at else report.pk
    return redirect(f"{reverse('evaluation_report_pdf_view', kwargs={'pk': pk})}?v={cache_bust}")


@login_required
@require_POST
def evaluation_report_build(request, pk: int):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    try:
        build_report_pdf(report)
        messages.success(request, "报告 PDF 已生成")
        # 最终生成后回到详情页，下一步是预览或导出，不再回到正文编辑页
        return redirect(
            f"{reverse('evaluation_report_detail', kwargs={'pk': pk})}?pdf=1#export"
        )
    except Exception as exc:
        messages.error(request, f"生成失败：{exc}")
        return redirect("evaluation_report_detail", pk=pk)


@login_required
def evaluation_report_download(request, pk: int, kind: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    if kind != "pdf" or not report.output_pdf:
        raise Http404
    try:
        return FileResponse(
            report.output_pdf.open("rb"),
            as_attachment=True,
            filename=report.output_pdf.name.split("/")[-1],
        )
    except FileNotFoundError as exc:
        raise Http404 from exc


@login_required
@require_POST
def evaluation_report_delete(request, pk: int):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    title = report.title
    try:
        delete_evaluation_report(report)
    except Exception:
        messages.error(request, f"删除「{title}」失败")
        return redirect("evaluation_report_detail", pk=pk)
    messages.success(request, f"已删除评价报告「{title}」")
    return redirect("evaluation_report_list")
