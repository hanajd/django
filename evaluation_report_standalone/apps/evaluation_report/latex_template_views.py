"""评价报告 LaTeX 模板库页面视图。"""

from __future__ import annotations

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from apps.core.library_access import role_has

from .constants import REPORT_TYPE_CHOICES
from .latex_template_service import (
    LatexTemplateError,
    create_template,
    delete_template,
    ingest_multipart_files,
    ingest_zip_upload,
    list_template_files,
    list_templates,
    update_template_metadata,
)
from .models import EvaluationLatexTemplate
from .template_preview import (
    TemplatePreviewError,
    build_latex_file_tree_for_template,
    latex_project_kind,
    latex_project_kind_label,
    read_library_template_tex,
    resolve_library_asset_preview,
    resolve_library_template_preview_selection,
    write_library_template_tex,
)


def _require_evaluation_perm(request):
    if not role_has(request.user, "perm_evaluation_report"):
        messages.error(request, "无权访问评价报告功能")
        return redirect(reverse("dashboard"))
    return None


@login_required
def evaluation_latex_template_list(request):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied
    templates = list_templates()
    return render(
        request,
        "evaluation_report/latex_template_list.html",
        {"templates": templates},
    )


@login_required
def evaluation_latex_template_create(request):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    if request.method == "POST":
        try:
            tpl = create_template(
                name=request.POST.get("name", ""),
                key=request.POST.get("key", ""),
                description=request.POST.get("description", ""),
                report_subtype=request.POST.get("report_subtype", ""),
                user=request.user,
            )
            replace = request.POST.get("replace") == "1"
            stats = None
            zip_file = request.FILES.get("zip_file")
            files = request.FILES.getlist("files")
            rel_paths = request.POST.getlist("relative_paths")
            if zip_file:
                stats = ingest_zip_upload(tpl, zip_file, replace=replace)
            elif files:
                stats = ingest_multipart_files(
                    tpl,
                    files,
                    relative_paths=rel_paths,
                    replace=replace,
                )
            if stats:
                if stats.errors:
                    for err in stats.errors[:5]:
                        messages.warning(request, err)
                if stats.files_written:
                    messages.success(
                        request,
                        f"已创建模板「{tpl.name}」，写入 {stats.files_written} 个文件",
                    )
                elif not zip_file and not files:
                    messages.success(request, f"已创建模板「{tpl.name}」，请继续上传文件")
            else:
                messages.success(request, f"已创建模板「{tpl.name}」")
            return redirect("evaluation_latex_template_detail", key=tpl.key)
        except LatexTemplateError as exc:
            messages.error(request, str(exc))

    return render(
        request,
        "evaluation_report/latex_template_form.html",
        {
            "template_obj": None,
            "subtype_choices": REPORT_TYPE_CHOICES,
        },
    )


@login_required
def evaluation_latex_template_detail(request, key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    tpl = get_object_or_404(EvaluationLatexTemplate, key=key)
    if request.method == "POST" and request.POST.get("action") == "update_meta":
        try:
            update_template_metadata(
                tpl,
                name=request.POST.get("name", ""),
                description=request.POST.get("description", ""),
                report_subtype=request.POST.get("report_subtype", ""),
            )
            messages.success(request, "模板信息已保存")
        except LatexTemplateError as exc:
            messages.error(request, str(exc))
        return redirect("evaluation_latex_template_detail", key=tpl.key)

    return render(
        request,
        "evaluation_report/latex_template_detail.html",
        {
            "template_obj": tpl,
            "files": list_template_files(tpl),
            "subtype_choices": REPORT_TYPE_CHOICES,
        },
    )


@login_required
@require_POST
def evaluation_latex_template_upload(request, key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    tpl = get_object_or_404(EvaluationLatexTemplate, key=key)
    replace = request.POST.get("replace") == "1"
    try:
        zip_file = request.FILES.get("zip_file")
        files = request.FILES.getlist("files")
        rel_paths = request.POST.getlist("relative_paths")
        if zip_file:
            stats = ingest_zip_upload(tpl, zip_file, replace=replace)
        elif files:
            stats = ingest_multipart_files(
                tpl,
                files,
                relative_paths=rel_paths,
                replace=replace,
            )
        else:
            messages.error(request, "请选择 ZIP 或文件")
            return redirect("evaluation_latex_template_detail", key=tpl.key)
        if stats.errors:
            for err in stats.errors[:5]:
                messages.warning(request, err)
        if stats.files_written:
            messages.success(request, f"已写入 {stats.files_written} 个文件")
        else:
            messages.error(request, "未写入任何文件")
    except LatexTemplateError as exc:
        messages.error(request, str(exc))
    return redirect("evaluation_latex_template_detail", key=tpl.key)


@login_required
@require_POST
def evaluation_latex_template_delete(request, key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    tpl = get_object_or_404(EvaluationLatexTemplate, key=key)
    try:
        name = tpl.name
        delete_template(tpl)
        messages.success(request, f"已删除模板「{name}」")
    except LatexTemplateError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_latex_template_detail", key=key)
    return redirect("evaluation_latex_template_list")


@login_required
def evaluation_latex_template_preview(request, key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    tpl = get_object_or_404(EvaluationLatexTemplate, key=key)
    selected = (request.GET.get("file") or "main.tex").strip()
    view_param = (request.GET.get("view") or "").strip()
    edit_mode = request.GET.get("edit") == "1" and tpl.is_editable

    try:
        latex_root = tpl.storage_root.resolve()
        project_kind = latex_project_kind(latex_root)
        file_tree = build_latex_file_tree_for_template(tpl)
        preview = resolve_library_template_preview_selection(
            tpl, selected, view=view_param
        )
        if edit_mode and (
            preview["initial_view"] == "asset"
            or not preview["selected_file"].lower().endswith(".tex")
        ):
            edit_mode = False
        show_tex_edit_toggle = tpl.is_editable and (
            preview["initial_view"] == "tex"
            and preview["selected_file"].lower().endswith(".tex")
        )
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_latex_template_detail", key=tpl.key)

    preview_asset = preview.get("preview_asset")
    return render(
        request,
        "evaluation_report/template_preview.html",
        {
            "library_template": tpl,
            "report": None,
            "template_info": tpl,
            "latex_root": str(latex_root),
            "latex_source": "library",
            "latex_project_kind": project_kind,
            "latex_project_kind_label": latex_project_kind_label(project_kind),
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
def evaluation_latex_template_preview_content(request, key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return JsonResponse({"error": "无权访问"}, status=403)

    tpl = get_object_or_404(EvaluationLatexTemplate, key=key)
    selected = (request.GET.get("file") or "main.tex").strip()
    try:
        relpath, content = read_library_template_tex(tpl, selected)
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
def evaluation_latex_template_preview_save(request, key: str):
    denied = _require_evaluation_perm(request)
    if denied:
        return denied

    tpl = get_object_or_404(EvaluationLatexTemplate, key=key)
    relpath = (request.POST.get("file") or "main.tex").strip()
    content = request.POST.get("content", "")
    try:
        saved = write_library_template_tex(tpl, relpath, content)
        messages.success(request, f"已保存 {saved}")
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_latex_template_preview", key=tpl.key)

    query = urlencode({"file": saved})
    return redirect(f"{reverse('evaluation_latex_template_preview', kwargs={'key': tpl.key})}?{query}")


@login_required
def evaluation_latex_template_media(request, key: str, relpath: str):
    denied = _require_evaluation_perm(request)
    if denied:
        raise Http404

    tpl = get_object_or_404(EvaluationLatexTemplate, key=key)
    try:
        preview = resolve_library_asset_preview(tpl, relpath)
        root = tpl.storage_root.resolve()
        target = (root / preview["relpath"]).resolve()
        if not str(target).startswith(str(root)):
            raise Http404
        if not target.is_file():
            raise Http404
    except TemplatePreviewError as exc:
        raise Http404(str(exc)) from exc

    content_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(open(target, "rb"), content_type=content_type or "application/octet-stream")
