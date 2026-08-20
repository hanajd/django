"""Structured chapter editor for non-LaTeX users."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.library_access import library_user_may_access_evaluation_report, role_has

from .chapter_manifest import chapter_by_key, editable_chapters_for
from .models import EvaluationReport
from .structured_blocks import (
    ContentBlock,
    _as_block,
    formula_to_latex,
    parse_chapter_blocks,
    serialize_chapter,
    visible_blocks,
)
from .tabular_codec import TableModel, model_to_latex, model_to_preview_html


def _keywords_url_with_return(pk: int, return_url: str) -> str:
    """项目信息入口：带上返回编辑页地址。"""
    from urllib.parse import urlencode

    base = reverse("evaluation_report_keywords", kwargs={"pk": pk})
    return_url = (return_url or "").strip()
    if not return_url:
        return base
    return f"{base}?{urlencode({'next': return_url})}"


def _unfilled_edit_context(report: EvaluationReport, *, current_chapter: str = "") -> dict:
    """结构化编辑页：仅未填个性化项 + 可跳转链接。"""
    from urllib.parse import urlencode

    from .keywords_service import list_unfilled_personalized_keywords

    pk = report.pk
    edit_base = reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
    keywords_base = reverse("evaluation_report_keywords", kwargs={"pk": pk})
    items = list_unfilled_personalized_keywords(report)
    by_chapter: dict[str, int] = {}
    unfilled_commands: list[str] = []
    unfilled_sources: list[str] = []
    enriched: list[dict] = []
    for item in items:
        ck = (item.get("chapter_key") or "").strip()
        if ck:
            by_chapter[ck] = by_chapter.get(ck, 0) + 1
        return_chapter = ck or current_chapter or "cover"
        next_url = f"{edit_base}?chapter={return_chapter}"
        cmd = item["command"]
        unfilled_commands.append(cmd)
        src = (item.get("source_file") or "").strip()
        if src:
            unfilled_sources.append(src)
        kw_url = (
            f"{keywords_base}?{urlencode({'next': next_url, 'focus': cmd})}"
            f"#kw_{cmd}"
        )
        chapter_url = f"{edit_base}?chapter={ck}" if ck else kw_url
        enriched.append(
            {
                **item,
                "keywords_focus_url": kw_url,
                "chapter_url": chapter_url,
            }
        )
    return {
        "unfilled_keywords": enriched,
        "unfilled_keyword_count": len(enriched),
        "unfilled_by_chapter": by_chapter,
        "unfilled_by_chapter_json": json.dumps(by_chapter, ensure_ascii=False),
        "unfilled_commands_json": json.dumps(unfilled_commands, ensure_ascii=False),
        "unfilled_sources_json": json.dumps(unfilled_sources, ensure_ascii=False),
    }


from .template_preview import (
    TemplatePreviewError,
    ensure_editable_latex,
    ensure_work_latex_images,
    write_template_tex,
)


def _require_perm(request: HttpRequest):
    if not library_user_may_access_evaluation_report(request.user):
        messages.error(request, "无权访问评价报告功能")
        return redirect(reverse("evaluation_report_list"))
    return None


def _template_display_name(report: EvaluationReport) -> str:
    key = (report.latex_template_key or "").strip()
    try:
        from .latex_template_service import get_template

        return get_template(key).name or key
    except Exception:
        from .constants import REPORT_TEMPLATES

        tpl = REPORT_TEMPLATES.get(key)
        return (tpl.name if tpl else "") or key


def _merge_edits(original: list[ContentBlock], edited_visible: list) -> list[ContentBlock]:
    edited = [_as_block(b) for b in edited_visible]
    ei = 0
    out: list[ContentBlock] = []
    for b in original:
        if b.type == "raw":
            out.append(b)
            continue
        if ei < len(edited):
            out.append(edited[ei])
            ei += 1
    while ei < len(edited):
        out.append(edited[ei])
        ei += 1
    return out


def _parse_report_chapter(report: EvaluationReport, latex_root: Path, content: str):
    from .front_matter import resolve_macros

    macros = resolve_macros(report, latex_root)
    return parse_chapter_blocks(content, latex_root=latex_root, macros=macros), macros


@login_required
def evaluation_report_structured_edit(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    try:
        latex_root = ensure_editable_latex(report)
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_report_detail", pk=pk)

    chapter_key = (request.GET.get("chapter") or editable_chapters_for(report.latex_template_key)[0].key).strip()
    spec = chapter_by_key(chapter_key, report.latex_template_key) or editable_chapters_for(report.latex_template_key)[0]
    tex_path = latex_root / spec.relpath
    if not tex_path.is_file():
        messages.error(request, f"章节文件不存在：{spec.relpath}")
        return redirect("evaluation_report_detail", pk=pk)

    if spec.kind == "front":
        return _render_front_edit(request, report, latex_root, spec)

    content = tex_path.read_text(encoding="utf-8")
    from .front_matter import body_page_chrome, resolve_macros

    macros = resolve_macros(report, latex_root)
    preamble, blocks = parse_chapter_blocks(content, latex_root=latex_root, macros=macros)
    display = visible_blocks(blocks)

    groups: dict[str, list] = {}
    for ch in editable_chapters_for(report.latex_template_key):
        groups.setdefault(ch.group, []).append(ch)

    chapter_no = 0
    m = re.match(r"ch(\d+)$", spec.key)
    if m:
        chapter_no = int(m.group(1))

    chrome = body_page_chrome(macros)
    pdf_ready = (request.GET.get("pdf") or "").strip() == "1"
    unfilled_ctx = _unfilled_edit_context(report, current_chapter=spec.key)

    from .keywords_service import personalized_file_label_map

    return render(
        request,
        "evaluation_report/structured_edit.html",
        {
            "report": report,
            "chapters": editable_chapters_for(report.latex_template_key),
            "chapter_groups": groups,
            "current": spec,
            "is_front": False,
            "chapter_no": chapter_no,
            "preamble_json": json.dumps(preamble, ensure_ascii=False),
            "original_json": json.dumps(content, ensure_ascii=False),
            "blocks_json": json.dumps([b.to_dict() for b in display], ensure_ascii=False),
            "personalized_file_labels_json": json.dumps(
                personalized_file_label_map(), ensure_ascii=False
            ),
            "save_url": reverse("evaluation_report_structured_save", kwargs={"pk": pk}),
            "latex_template_key": report.latex_template_key,
            "latex_template_name": _template_display_name(report),
            "upload_url": reverse("evaluation_report_structured_upload_image", kwargs={"pk": pk}),
            "table_edit_url": reverse("evaluation_report_table_edit", kwargs={"pk": pk}),
            "formula_edit_url": reverse("evaluation_report_formula_edit", kwargs={"pk": pk}),
            "flowchart_edit_url": reverse("evaluation_report_flowchart_edit", kwargs={"pk": pk}),
            "media_base": reverse(
                "evaluation_report_work_media", kwargs={"pk": pk, "relpath": "images"}
            ).rsplit("/", 1)[0]
            + "/",
            "page_chrome_json": json.dumps(chrome.to_dict(), ensure_ascii=False),
            "keywords_url": _keywords_url_with_return(
                pk,
                reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
                + f"?chapter={spec.key}",
            ),
            "pdf_ready": pdf_ready,
            "pdf_view_url": reverse("evaluation_report_pdf_view", kwargs={"pk": pk})
            if report.output_pdf
            else "",
            "pdf_download_url": reverse("evaluation_report_download", kwargs={"pk": pk, "kind": "pdf"})
            if report.output_pdf
            else "",
            **unfilled_ctx,
        },
    )


def _render_front_edit(request: HttpRequest, report: EvaluationReport, latex_root, spec):
    from .front_matter import (
        cover_chrome,
        declaration_chrome,
        parse_cover,
        parse_declaration,
        parse_qualification,
        qualification_chrome,
        resolve_macros,
    )

    pk = report.pk
    tex_path = latex_root / spec.relpath
    content = tex_path.read_text(encoding="utf-8")
    macros = resolve_macros(report, latex_root)

    page_data: dict = {}
    chrome = cover_chrome()
    if spec.key == "cover":
        from .keywords_service import cover_keywords_for_edit

        page_data = parse_cover(content, macros).to_dict()
        # Personalized fields come from report keywords (not baked into cover.tex)
        page_data.update(cover_keywords_for_edit(report))
        chrome = cover_chrome()
    elif spec.key == "qualification":
        page_data = parse_qualification(content).to_dict()
        chrome = qualification_chrome(macros)
    elif spec.key == "declaration":
        from .front_matter import ensure_declaration_sign_macros

        content2, changed = ensure_declaration_sign_macros(content)
        if changed:
            tex_path.write_text(content2, encoding="utf-8")
            content = content2
        page_data = parse_declaration(content, macros).to_dict()
        chrome = declaration_chrome(macros)

    groups: dict[str, list] = {}
    for ch in editable_chapters_for(report.latex_template_key):
        groups.setdefault(ch.group, []).append(ch)

    pdf_ready = (request.GET.get("pdf") or "").strip() == "1"
    unfilled_ctx = _unfilled_edit_context(report, current_chapter=spec.key)

    return render(
        request,
        "evaluation_report/front_edit.html",
        {
            "report": report,
            "chapters": editable_chapters_for(report.latex_template_key),
            "chapter_groups": groups,
            "current": spec,
            "page_key": spec.key,
            "page_json": json.dumps(page_data, ensure_ascii=False),
            "page_chrome_json": json.dumps(chrome.to_dict(), ensure_ascii=False),
            "original_json": json.dumps(content, ensure_ascii=False),
            "save_url": reverse("evaluation_report_front_save", kwargs={"pk": pk}),
            "latex_template_key": report.latex_template_key,
            "latex_template_name": _template_display_name(report),
            "upload_url": reverse("evaluation_report_structured_upload_image", kwargs={"pk": pk}),
            "table_edit_url": reverse("evaluation_report_table_edit", kwargs={"pk": pk}),
            "media_base": reverse(
                "evaluation_report_work_media", kwargs={"pk": pk, "relpath": "images"}
            ).rsplit("/", 1)[0]
            + "/",
            "keywords_url": _keywords_url_with_return(
                pk,
                reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
                + f"?chapter={spec.key}",
            ),
            "edit_base": reverse("evaluation_report_structured_edit", kwargs={"pk": pk}),
            "pdf_ready": pdf_ready,
            "pdf_view_url": reverse("evaluation_report_pdf_view", kwargs={"pk": pk})
            if report.output_pdf
            else "",
            "pdf_download_url": reverse(
                "evaluation_report_download", kwargs={"pk": pk, "kind": "pdf"}
            )
            if report.output_pdf
            else "",
            **unfilled_ctx,
        },
    )


@login_required
@require_POST
def evaluation_report_front_save(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "无效 JSON"}, status=400)

    chapter_key = str(payload.get("chapter") or "").strip()
    page = payload.get("page")
    if not isinstance(page, dict):
        return JsonResponse({"ok": False, "error": "缺少页面数据"}, status=400)

    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None or spec.kind != "front":
        return JsonResponse({"ok": False, "error": "未知前置页"}, status=400)

    try:
        content = _save_front_page_to_work(report, spec, page)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "redirect": reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
            + f"?chapter={spec.key}",
        }
    )


@login_required
@require_POST
def evaluation_report_structured_save(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "无效 JSON"}, status=400)

    chapter_key = str(payload.get("chapter") or "").strip()
    preamble = str(payload.get("preamble") or "")
    original = str(payload.get("original") or "")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return JsonResponse({"ok": False, "error": "blocks 必须是数组"}, status=400)

    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None:
        return JsonResponse({"ok": False, "error": "未知章节"}, status=400)

    try:
        root = ensure_editable_latex(report)
        from .front_matter import resolve_macros

        macros = resolve_macros(report, root)
        if spec.kind == "front":
            tex = serialize_chapter(
                preamble,
                blocks,
                front_matter=True,
                original_content=original or None,
                latex_root=root,
                macros=macros,
            )
        else:
            # Always merge against on-disk chapter so repeated saves do not use a
            # stale browser ``original`` snapshot (which caused duplicated prose).
            src = (root / spec.relpath).read_text(encoding="utf-8")
            pre2, all_blocks = parse_chapter_blocks(
                src, latex_root=root, macros=macros
            )
            merged = _merge_edits(all_blocks, blocks)
            tex = serialize_chapter(
                pre2 or preamble, merged, latex_root=root, macros=macros
            )
        write_template_tex(report, spec.relpath, tex)
        # Keep DB file-backed keywords in sync so ensure_editable_latex does not
        # clobber A3 / figure markup written into files/*.tex.
        try:
            from .keywords_service import sync_file_backed_keyword_from_disk

            sync_file_backed_keyword_from_disk(report, root)
        except Exception:
            pass
    except TemplatePreviewError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "relpath": spec.relpath,
            "blocks": len(blocks),
            "content": tex,
        }
    )


def _save_front_page_to_work(report: EvaluationReport, spec, page: dict) -> str:
    """写入前置页到工作区，返回 tex 正文（关键词仍只进项目，不进模板）。"""
    from .front_matter import (
        patch_cover_logo,
        patch_declaration_statement,
        patch_qualification_image,
    )
    from .keywords_service import apply_project_keywords_to_main_tex, save_project_keywords

    root = ensure_editable_latex(report)
    tex_path = root / spec.relpath
    content = tex_path.read_text(encoding="utf-8")

    if spec.key == "cover":
        logo = str(page.get("logo_path") or "").strip()
        width = str(page.get("logo_width") or "4.6cm").strip()
        content = patch_cover_logo(content, logo, width)
        kw = {
            "reportno": str(page.get("reportno") or ""),
            "reportver": str(page.get("reportver") or ""),
            "buildunit": str(page.get("buildunit") or ""),
            "project": str(page.get("project") or ""),
            "radiationhazard": str(page.get("radiationhazard") or ""),
            "reportkind": str(page.get("reportkind") or ""),
            "mitnote": str(page.get("mitnote") or ""),
            "company": str(page.get("company") or ""),
            "printdate": str(page.get("printdate") or ""),
        }
        save_project_keywords(report, kw)
        apply_project_keywords_to_main_tex(report, root / "main.tex")
    elif spec.key == "qualification":
        path = str(page.get("image_path") or "").strip()
        try:
            angle = int(page.get("image_angle") if page.get("image_angle") is not None else 90)
        except (TypeError, ValueError):
            angle = 90
        try:
            scale = float(page.get("image_scale") if page.get("image_scale") is not None else 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        content = patch_qualification_image(content, path, angle=angle, scale=scale)
    elif spec.key == "declaration":
        from .front_matter import ensure_declaration_sign_macros

        statement = str(page.get("statement") or "")
        content = patch_declaration_statement(content, statement)
        content, _ = ensure_declaration_sign_macros(content)

    write_template_tex(report, spec.relpath, content)
    return content


def _table_model_for_edit(
    report: EvaluationReport,
    latex_root: Path,
    target: ContentBlock | None,
    *,
    is_front: bool,
) -> TableModel:
    """Build the same TableModel the chapter preview uses (macros + files/ body)."""
    from .front_matter import resolve_macros
    from .structured_blocks import _extract_table_block

    if target is not None and target.table_model:
        model = TableModel.from_dict(target.table_model)
        if is_front:
            model.caption = ""
        return model

    latex = (target.latex if target else "") or ""
    caption = "" if is_front else ((target.caption if target else "") or "")
    if latex.strip():
        macros = resolve_macros(report, latex_root)
        block = _extract_table_block(
            latex,
            (target.id if target else "tmp"),
            latex_root=latex_root,
            macros=macros,
        )
        model = TableModel.from_dict(block.table_model) if block.table_model else TableModel.from_dict(None)
        if is_front:
            model.caption = ""
        elif caption and not model.caption:
            model.caption = caption
        return model

    return TableModel.from_dict(None)


@login_required
def evaluation_report_table_edit(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    chapter_key = (request.GET.get("chapter") or "").strip()
    block_id = (request.GET.get("block") or "").strip()
    is_new = request.GET.get("new") == "1" or block_id.startswith("new_")
    after_id = (request.GET.get("after") or "").strip()
    insert_pos = (request.GET.get("pos") or "after").strip()  # after | start
    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None:
        messages.error(request, "未知章节")
        return redirect("evaluation_report_structured_edit", pk=pk)

    try:
        latex_root = ensure_editable_latex(report)
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_report_detail", pk=pk)

    tex_path = latex_root / spec.relpath
    content = tex_path.read_text(encoding="utf-8") if tex_path.is_file() else ""
    preamble, blocks = _parse_report_chapter(report, latex_root, content)[0] if content else ("", [])
    target = next((b for b in blocks if b.id == block_id and b.type == "table"), None)
    if target is None:
        model = TableModel.from_dict(None)
        if not block_id:
            block_id = f"b{uuid.uuid4().hex[:8]}"
        is_new = True
    else:
        # Prefer the parsed table_model (same as main-page preview): macros expanded,
        # \\erpTabInput / files/*.tex bodies already inlined.
        model = _table_model_for_edit(
            report, latex_root, target, is_front=(spec.kind == "front")
        )
        is_new = False

    return render(
        request,
        "evaluation_report/table_edit.html",
        {
            "report": report,
            "current": spec,
            "is_front": spec.kind == "front",
            "block_id": block_id,
            "is_new": is_new,
            "after_id": after_id,
            "insert_pos": insert_pos,
            "model_json": json.dumps(model.to_dict(), ensure_ascii=False),
            "back_url": reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
            + f"?chapter={spec.key}",
            "save_url": reverse("evaluation_report_table_save", kwargs={"pk": pk}),
            "keywords_url": _keywords_url_with_return(
                pk,
                reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
                + f"?chapter={spec.key}",
            ),
            "has_body_input": bool((model.body_input or "").strip()),
        },
    )


@login_required
@require_POST
def evaluation_report_table_save(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "无效 JSON"}, status=400)

    chapter_key = str(payload.get("chapter") or "").strip()
    block_id = str(payload.get("block_id") or "").strip()
    is_new = bool(payload.get("is_new"))
    after_id = str(payload.get("after_id") or "").strip()
    insert_pos = str(payload.get("insert_pos") or "after").strip()
    model_data = payload.get("model")
    if not isinstance(model_data, dict):
        return JsonResponse({"ok": False, "error": "缺少表格数据"}, status=400)

    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None:
        return JsonResponse({"ok": False, "error": "未知章节"}, status=400)

    try:
        root = ensure_editable_latex(report)
        tex_path = root / spec.relpath
        content = tex_path.read_text(encoding="utf-8")
        (preamble, blocks), macros = _parse_report_chapter(report, root, content)
        model = TableModel.from_dict(model_data)
        if spec.kind == "front":
            model.caption = ""

        old = next((b for b in blocks if b.id == block_id and b.type == "table"), None)
        # Front-matter signature tables use \\mylen / \\dimexpr — keep original
        # colspec/macros when possible. Text-only edits patch in place; merges
        # rebuild the bare tabular (never wrap in table env).
        if (
            spec.kind == "front"
            and old
            and old.latex
            and (r"\mylen" in old.latex or r"\dimexpr" in old.latex or r"\erpdecl" in old.latex)
        ):
            from .tabular_codec import (
                latex_table_to_model,
                patch_cell_texts_in_latex,
                rebuild_front_matter_tabular,
                table_structure_equal,
            )

            old_model = latex_table_to_model(old.latex, "")
            if table_structure_equal(old_model, model):
                latex = patch_cell_texts_in_latex(old.latex, old_model, model)
            else:
                latex = rebuild_front_matter_tabular(old.latex, old_model, model)
            preview = model_to_preview_html(latex_table_to_model(latex, ""))
        else:
            latex = model_to_latex(model)
            preview = model_to_preview_html(model)

        new_block = ContentBlock(
            id=block_id or f"b{uuid.uuid4().hex[:8]}",
            type="table",
            caption=model.caption,
            latex=latex,
            table_model=model.to_dict(),
            preview_html=preview,
            rows=[[c.text for c in row] for row in model.cells],
        )

        found = False
        for i, b in enumerate(blocks):
            if b.id == block_id and b.type == "table":
                blocks[i] = new_block
                found = True
                break
        if not found:
            insert_at = len(blocks)
            if insert_pos == "start":
                insert_at = 0
            elif after_id:
                for i, b in enumerate(blocks):
                    if b.id == after_id:
                        insert_at = i + 1
                        break
            blocks.insert(insert_at, new_block)

        if spec.kind == "front":
            # Replace original table latex chunk in file when possible
            out = content
            old = next((b for b in _parse_report_chapter(report, root, content)[0][1] if b.id == block_id), None)
            if old and old.latex and old.latex in out:
                out = out.replace(old.latex, latex, 1)
            else:
                out = serialize_chapter(preamble, blocks, front_matter=False, latex_root=root, macros=macros)
            write_template_tex(report, spec.relpath, out)
        else:
            write_template_tex(
                report,
                spec.relpath,
                serialize_chapter(preamble, blocks, latex_root=root, macros=macros),
            )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "redirect": reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
            + f"?chapter={spec.key}",
        }
    )


@login_required
def evaluation_report_formula_edit(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    chapter_key = (request.GET.get("chapter") or "").strip()
    block_id = (request.GET.get("block") or "").strip()
    is_new = request.GET.get("new") == "1"
    after_id = (request.GET.get("after") or "").strip()
    insert_pos = (request.GET.get("pos") or "after").strip()
    # inline edit inside text/list: parent block + occurrence index
    parent_id = (request.GET.get("parent") or "").strip()
    inline_idx = request.GET.get("idx")
    try:
        inline_idx_i = int(inline_idx) if inline_idx is not None and inline_idx != "" else -1
    except ValueError:
        inline_idx_i = -1

    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None:
        messages.error(request, "未知章节")
        return redirect("evaluation_report_structured_edit", pk=pk)

    try:
        latex_root = ensure_editable_latex(report)
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_report_detail", pk=pk)

    tex_path = latex_root / spec.relpath
    content = tex_path.read_text(encoding="utf-8") if tex_path.is_file() else ""
    _preamble, blocks = _parse_report_chapter(report, latex_root, content)[0] if content else ("", [])

    body = "E = mc^2"
    mode = "display"
    is_inline_edit = bool(parent_id) and inline_idx_i >= 0

    if is_inline_edit:
        parent = next((b for b in blocks if b.id == parent_id), None)
        if parent is None:
            messages.error(request, "找不到对应段落")
            return redirect("evaluation_report_structured_edit", pk=pk)
        source = parent.text if parent.type == "text" else "\n".join(parent.items or [])
        maths = re.findall(r"\$([^$]+)\$", source or "")
        if 0 <= inline_idx_i < len(maths):
            body = maths[inline_idx_i]
        mode = "inline"
        block_id = parent_id
    else:
        target = next((b for b in blocks if b.id == block_id and b.type == "formula"), None)
        if target is not None:
            body = target.text or ""
            mode = target.level or "display"
            is_new = False
        else:
            is_new = True
            if not block_id:
                block_id = f"b{uuid.uuid4().hex[:8]}"

    return render(
        request,
        "evaluation_report/formula_edit.html",
        {
            "report": report,
            "current": spec,
            "block_id": block_id,
            "is_new": is_new,
            "after_id": after_id,
            "insert_pos": insert_pos,
            "parent_id": parent_id,
            "inline_idx": inline_idx_i,
            "is_inline_edit": is_inline_edit,
            "body": body,
            "body_json": json.dumps(body, ensure_ascii=False),
            "mode": mode,
            "back_url": reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
            + f"?chapter={spec.key}",
            "save_url": reverse("evaluation_report_formula_save", kwargs={"pk": pk}),
        },
    )


@login_required
@require_POST
def evaluation_report_formula_save(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "无效 JSON"}, status=400)

    chapter_key = str(payload.get("chapter") or "").strip()
    block_id = str(payload.get("block_id") or "").strip()
    is_new = bool(payload.get("is_new"))
    after_id = str(payload.get("after_id") or "").strip()
    insert_pos = str(payload.get("insert_pos") or "after").strip()
    parent_id = str(payload.get("parent_id") or "").strip()
    inline_idx = payload.get("inline_idx")
    try:
        inline_idx_i = int(inline_idx) if inline_idx is not None else -1
    except (TypeError, ValueError):
        inline_idx_i = -1
    body = str(payload.get("body") or "").strip()
    mode = str(payload.get("mode") or "display").strip()
    # Accept full delimited source ($...$, \[...\], align, …) and normalize.
    from .structured_blocks import formula_body_from_chunk

    if body.startswith(("$", "\\[", "\\begin")) or body.startswith("$$"):
        detected, bare = formula_body_from_chunk(body)
        body = bare
        if detected:
            mode = detected
    if mode == "display2":
        mode = "display"
    if not body:
        return JsonResponse({"ok": False, "error": "公式内容不能为空"}, status=400)

    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None:
        return JsonResponse({"ok": False, "error": "未知章节"}, status=400)

    try:
        root = ensure_editable_latex(report)
        tex_path = root / spec.relpath
        content = tex_path.read_text(encoding="utf-8")
        (preamble, blocks), macros = _parse_report_chapter(report, root, content)

        if parent_id and inline_idx_i >= 0:
            # replace N-th $...$ in text/list parent
            found = False
            for i, b in enumerate(blocks):
                if b.id != parent_id:
                    continue
                found = True
                if b.type == "text":
                    maths = list(re.finditer(r"\$[^$]+\$", b.text or ""))
                    if inline_idx_i >= len(maths):
                        return JsonResponse({"ok": False, "error": "公式索引无效"}, status=400)
                    m = maths[inline_idx_i]
                    new_text = (b.text or "")[: m.start()] + f"${body}$" + (b.text or "")[m.end() :]
                    blocks[i] = ContentBlock(
                        id=b.id, type="text", text=new_text, latex=b.latex
                    )
                elif b.type == "list":
                    # Legacy: treat as numbered plain text after edit
                    joined = "\n".join(b.items or [])
                    maths = list(re.finditer(r"\$[^$]+\$", joined))
                    if inline_idx_i >= len(maths):
                        return JsonResponse({"ok": False, "error": "公式索引无效"}, status=400)
                    m = maths[inline_idx_i]
                    new_joined = joined[: m.start()] + f"${body}$" + joined[m.end() :]
                    ordered = b.level != "itemize"
                    paras = []
                    for i, it in enumerate(new_joined.split("\n")):
                        t = (it or "").strip()
                        if ordered and not re.match(r"^[（(]\d+[）)]", t):
                            t = f"（{i + 1}）{t}" if t else f"（{i + 1}）"
                        paras.append(t)
                    blocks[i] = ContentBlock(
                        id=b.id,
                        type="text",
                        text="\n\n".join(paras),
                        latex=b.latex,
                    )
                else:
                    return JsonResponse({"ok": False, "error": "父块类型不支持行内公式"}, status=400)
                break
            if not found:
                return JsonResponse({"ok": False, "error": "找不到父块"}, status=400)
        else:
            latex = formula_to_latex(body, mode)
            new_block = ContentBlock(
                id=block_id or f"b{uuid.uuid4().hex[:8]}",
                type="formula",
                level=mode,
                text=body,
                latex=latex,
            )
            found = False
            for i, b in enumerate(blocks):
                if b.id == block_id and b.type == "formula":
                    blocks[i] = new_block
                    found = True
                    break
            if not found:
                insert_at = len(blocks)
                if insert_pos == "start":
                    insert_at = 0
                elif after_id:
                    for i, b in enumerate(blocks):
                        if b.id == after_id:
                            insert_at = i + 1
                            break
                blocks.insert(insert_at, new_block)

        write_template_tex(
            report, spec.relpath, serialize_chapter(preamble, blocks, latex_root=root, macros=macros)
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "redirect": reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
            + f"?chapter={spec.key}",
        }
    )


@login_required
def evaluation_report_flowchart_edit(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    chapter_key = (request.GET.get("chapter") or "").strip()
    block_id = (request.GET.get("block") or "").strip()
    is_new = request.GET.get("new") == "1"
    after_id = (request.GET.get("after") or "").strip()
    insert_pos = (request.GET.get("pos") or "after").strip()
    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None:
        messages.error(request, "未知章节")
        return redirect("evaluation_report_structured_edit", pk=pk)

    try:
        latex_root = ensure_editable_latex(report)
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_report_detail", pk=pk)

    from .flowchart_codec import FlowModel, FlowStep, latex_flowchart_to_model, model_to_latex

    tex_path = latex_root / spec.relpath
    content = tex_path.read_text(encoding="utf-8") if tex_path.is_file() else ""
    _preamble, blocks = _parse_report_chapter(report, latex_root, content)[0] if content else ("", [])
    target = next((b for b in blocks if b.id == block_id and b.type == "flowchart"), None)
    if target is None:
        model = FlowModel(steps=[FlowStep(id="n1", text="新步骤")])
        if not block_id:
            block_id = f"b{uuid.uuid4().hex[:8]}"
        is_new = True
    else:
        if target.table_model and target.table_model.get("kind") == "flowchart":
            model = FlowModel.from_dict(target.table_model)
        elif target.latex:
            model = latex_flowchart_to_model(target.latex, target.caption)
        else:
            model = FlowModel(
                caption=target.caption,
                steps=[FlowStep(id=f"n{i+1}", text=t) for i, t in enumerate(target.items or [""])],
            )
        model.caption = target.caption or model.caption
        model.latex = target.latex or model.latex
        is_new = False

    if not (model.latex or "").strip():
        model.latex = model_to_latex(model)

    return render(
        request,
        "evaluation_report/flowchart_edit.html",
        {
            "report": report,
            "current": spec,
            "block_id": block_id,
            "is_new": is_new,
            "after_id": after_id,
            "insert_pos": insert_pos,
            "model_json": json.dumps(model.to_dict(), ensure_ascii=False),
            "back_url": reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
            + f"?chapter={spec.key}",
            "save_url": reverse("evaluation_report_flowchart_save", kwargs={"pk": pk}),
            "preview_url": reverse("evaluation_report_flowchart_preview", kwargs={"pk": pk}),
        },
    )


@login_required
@require_POST
def evaluation_report_flowchart_preview(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "无效 JSON"}, status=400)

    latex = str(payload.get("latex") or "")
    from .flowchart_codec import latex_flowchart_to_model, model_to_preview_html

    model = latex_flowchart_to_model(latex)
    model.latex = latex
    return JsonResponse(
        {
            "ok": True,
            "preview_html": model_to_preview_html(model),
            "caption": model.caption,
            "steps": [s.to_dict() for s in model.steps],
        }
    )


@login_required
@require_POST
def evaluation_report_flowchart_save(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "无效 JSON"}, status=400)

    chapter_key = str(payload.get("chapter") or "").strip()
    block_id = str(payload.get("block_id") or "").strip()
    is_new = bool(payload.get("is_new"))
    after_id = str(payload.get("after_id") or "").strip()
    insert_pos = str(payload.get("insert_pos") or "after").strip()
    model_data = payload.get("model")
    if not isinstance(model_data, dict):
        return JsonResponse({"ok": False, "error": "缺少流程图数据"}, status=400)

    spec = chapter_by_key(chapter_key, report.latex_template_key)
    if spec is None:
        return JsonResponse({"ok": False, "error": "未知章节"}, status=400)

    try:
        from .flowchart_codec import (
            FlowModel,
            latex_flowchart_to_model,
            model_to_preview_html as flow_preview,
        )

        root = ensure_editable_latex(report)
        tex_path = root / spec.relpath
        content = tex_path.read_text(encoding="utf-8")
        (preamble, blocks), macros = _parse_report_chapter(report, root, content)
        model = FlowModel.from_dict(model_data)
        latex = (model.latex or "").strip()
        if not latex:
            return JsonResponse({"ok": False, "error": "缺少流程图 LaTeX"}, status=400)
        parsed = latex_flowchart_to_model(latex, model.caption, label=model.label)
        parsed.latex = latex
        if model.caption and not parsed.caption:
            parsed.caption = model.caption
        if model.label and not parsed.label:
            parsed.label = model.label
        preview = flow_preview(parsed)

        new_block = ContentBlock(
            id=block_id or f"b{uuid.uuid4().hex[:8]}",
            type="flowchart",
            caption=parsed.caption,
            label=parsed.label,
            items=[s.text for s in parsed.steps if s.text],
            latex=latex,
            table_model=parsed.to_dict(),
            preview_html=preview,
            text="\n".join(s.text for s in parsed.steps if s.text),
        )

        found = False
        for i, b in enumerate(blocks):
            if b.id == block_id and b.type == "flowchart":
                blocks[i] = new_block
                found = True
                break
        if not found:
            insert_at = len(blocks)
            if insert_pos == "start":
                insert_at = 0
            elif after_id:
                for i, b in enumerate(blocks):
                    if b.id == after_id:
                        insert_at = i + 1
                        break
            blocks.insert(insert_at, new_block)

        write_template_tex(
            report, spec.relpath, serialize_chapter(preamble, blocks, latex_root=root, macros=macros)
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "redirect": reverse("evaluation_report_structured_edit", kwargs={"pk": pk})
            + f"?chapter={spec.key}",
        }
    )


@login_required
@require_http_methods(["POST"])
def evaluation_report_structured_upload_image(request: HttpRequest, pk: int) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    report = get_object_or_404(EvaluationReport, pk=pk)
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"ok": False, "error": "请选择图片"}, status=400)

    suffix = Path(upload.name or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return JsonResponse({"ok": False, "error": "仅支持常见图片格式"}, status=400)

    try:
        latex_root = ensure_editable_latex(report)
    except TemplatePreviewError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    images_dir = latex_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    name = f"edit_{uuid.uuid4().hex[:12]}{suffix}"
    dest = images_dir / name
    with dest.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh)

    return JsonResponse({"ok": True, "path": f"images/{name}"})
