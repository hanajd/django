"""LaTeX 模板库的结构化编辑（与项目正文编辑同 UI，仅保存到模板）。"""

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

from apps.core.library_access import role_has

from .chapter_manifest import chapter_by_key, editable_chapters_for
from .latex_template_service import LatexTemplateError, get_template, list_templates
from .models import EvaluationLatexTemplate
from .structured_blocks import (
    parse_chapter_blocks,
    serialize_chapter,
    visible_blocks,
)
from .template_preview import TemplatePreviewError, template_root_for_library
from .template_push import write_library_tex


def _require_perm(request: HttpRequest):
    if not role_has(request.user, "perm_evaluation_report"):
        messages.error(request, "无权访问评价报告功能")
        return redirect(reverse("evaluation_report_list"))
    return None


def _resolve_template_macros(latex_root: Path) -> dict[str, str]:
    from .front_matter import load_main_macros

    return load_main_macros(latex_root / "main.tex")


def _chapter_groups(template_key: str) -> dict[str, list]:
    groups: dict[str, list] = {}
    for ch in editable_chapters_for(template_key):
        groups.setdefault(ch.group, []).append(ch)
    return groups


def _merge_edits(original, edited_visible):
    from .structured_blocks import ContentBlock, _as_block

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


def _patch_template_main_cover_macros(latex_root: Path, kw: dict[str, str]) -> None:
    """把封面短字段写回模板 main.tex 的 PROJECT_VARS（模板默认值，非项目数据）。"""
    from converter.project_vars import (
        VARS_END,
        VARS_START,
        extract_newcommand_definitions,
        patch_main_tex,
        render_newcommand_block,
    )
    from converter.project_vars import load_keywords
    from django.conf import settings

    main_tex = latex_root / "main.tex"
    if not main_tex.is_file():
        return
    text = main_tex.read_text(encoding="utf-8")
    start = text.find(VARS_START)
    end = text.find(VARS_END)
    region = text[start:end] if start != -1 and end != -1 else text
    current = extract_newcommand_definitions(region)
    for cmd, val in kw.items():
        if cmd:
            current[cmd] = val
    csv_path = Path(settings.BASE_DIR) / "converter" / "data" / "project_keywords.csv"
    keywords = load_keywords(csv_path) if csv_path.is_file() else []
    block = render_newcommand_block(current, keywords)
    if VARS_START not in text:
        block = f"{VARS_START}\n{block}\n{VARS_END}\n"
    patch_main_tex(main_tex, block)


@login_required
def evaluation_latex_template_edit_picker(request: HttpRequest) -> HttpResponse:
    """主页入口：选择要结构化编辑的模板。"""
    denied = _require_perm(request)
    if denied:
        return denied
    return render(
        request,
        "evaluation_report/template_edit_picker.html",
        {"templates": list_templates()},
    )


@login_required
def evaluation_latex_template_structured_edit(request: HttpRequest, key: str) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    try:
        tpl = get_template(key)
    except LatexTemplateError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_latex_template_edit_picker")

    try:
        latex_root = template_root_for_library(tpl)
    except TemplatePreviewError as exc:
        messages.error(request, str(exc))
        return redirect("evaluation_latex_template_edit_picker")

    chapters = editable_chapters_for(tpl.key)
    if not chapters:
        messages.error(request, "该模板没有可编辑章节")
        return redirect("evaluation_latex_template_edit_picker")

    chapter_key = (request.GET.get("chapter") or chapters[0].key).strip()
    spec = chapter_by_key(chapter_key, tpl.key) or chapters[0]
    tex_path = latex_root / spec.relpath
    if not tex_path.is_file():
        messages.error(request, f"章节文件不存在：{spec.relpath}")
        return redirect("evaluation_latex_template_edit_picker")

    if spec.kind == "front":
        return _render_template_front_edit(request, tpl, latex_root, spec)

    content = tex_path.read_text(encoding="utf-8")
    from .front_matter import body_page_chrome

    macros = _resolve_template_macros(latex_root)
    preamble, blocks = parse_chapter_blocks(content, latex_root=latex_root, macros=macros)
    display = visible_blocks(blocks)

    chapter_no = 0
    m = re.match(r"ch(\d+)$", spec.key)
    if m:
        chapter_no = int(m.group(1))

    edit_base = reverse("evaluation_latex_template_structured_edit", kwargs={"key": tpl.key})
    return render(
        request,
        "evaluation_report/structured_edit.html",
        {
            "template_mode": True,
            "library_template": tpl,
            "report": None,
            "chapters": chapters,
            "chapter_groups": _chapter_groups(tpl.key),
            "current": spec,
            "is_front": False,
            "chapter_no": chapter_no,
            "preamble_json": json.dumps(preamble, ensure_ascii=False),
            "original_json": json.dumps(content, ensure_ascii=False),
            "blocks_json": json.dumps([b.to_dict() for b in display], ensure_ascii=False),
            "save_url": reverse(
                "evaluation_latex_template_structured_save", kwargs={"key": tpl.key}
            ),
            "latex_template_key": tpl.key,
            "latex_template_name": tpl.name,
            "upload_url": reverse(
                "evaluation_latex_template_structured_upload_image", kwargs={"key": tpl.key}
            ),
            "table_edit_url": "",
            "formula_edit_url": "",
            "flowchart_edit_url": "",
            "media_base": reverse(
                "evaluation_latex_template_media",
                kwargs={"key": tpl.key, "relpath": "images"},
            ).rsplit("/", 1)[0]
            + "/",
            "page_chrome_json": json.dumps(body_page_chrome(macros).to_dict(), ensure_ascii=False),
            "keywords_url": "",
            "edit_base": edit_base,
            "pdf_ready": False,
            "pdf_view_url": "",
            "pdf_download_url": "",
            "back_url": reverse("evaluation_latex_template_edit_picker"),
        },
    )


def _render_template_front_edit(
    request: HttpRequest,
    tpl: EvaluationLatexTemplate,
    latex_root: Path,
    spec,
) -> HttpResponse:
    from .front_matter import (
        cover_chrome,
        declaration_chrome,
        parse_cover,
        parse_declaration,
        parse_qualification,
        qualification_chrome,
    )

    tex_path = latex_root / spec.relpath
    content = tex_path.read_text(encoding="utf-8")
    macros = _resolve_template_macros(latex_root)

    page_data: dict = {}
    chrome = cover_chrome()
    if spec.key == "cover":
        page_data = parse_cover(content, macros).to_dict()
        # 模板默认封面文字来自 main.tex 宏，不是项目 keywords
        for cmd in (
            "reportno",
            "reportver",
            "buildunit",
            "project",
            "radiationhazard",
            "reportkind",
            "mitnote",
            "company",
            "printdate",
        ):
            if cmd in macros:
                page_data[cmd] = macros[cmd]
        chrome = cover_chrome()
    elif spec.key == "qualification":
        page_data = parse_qualification(content).to_dict()
        chrome = qualification_chrome(macros)
    elif spec.key == "declaration":
        page_data = parse_declaration(content, macros).to_dict()
        chrome = declaration_chrome(macros)

    edit_base = reverse("evaluation_latex_template_structured_edit", kwargs={"key": tpl.key})
    return render(
        request,
        "evaluation_report/front_edit.html",
        {
            "template_mode": True,
            "library_template": tpl,
            "report": None,
            "chapters": editable_chapters_for(tpl.key),
            "chapter_groups": _chapter_groups(tpl.key),
            "current": spec,
            "page_key": spec.key,
            "page_json": json.dumps(page_data, ensure_ascii=False),
            "page_chrome_json": json.dumps(chrome.to_dict(), ensure_ascii=False),
            "original_json": json.dumps(content, ensure_ascii=False),
            "save_url": reverse(
                "evaluation_latex_template_front_save", kwargs={"key": tpl.key}
            ),
            "latex_template_key": tpl.key,
            "latex_template_name": tpl.name,
            "upload_url": reverse(
                "evaluation_latex_template_structured_upload_image", kwargs={"key": tpl.key}
            ),
            "table_edit_url": "",
            "media_base": reverse(
                "evaluation_latex_template_media",
                kwargs={"key": tpl.key, "relpath": "images"},
            ).rsplit("/", 1)[0]
            + "/",
            "keywords_url": "",
            "edit_base": edit_base,
            "pdf_ready": False,
            "pdf_view_url": "",
            "pdf_download_url": "",
            "back_url": reverse("evaluation_latex_template_edit_picker"),
        },
    )


@login_required
@require_POST
def evaluation_latex_template_structured_save(request: HttpRequest, key: str) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    try:
        tpl = get_template(key)
    except LatexTemplateError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=404)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "无效 JSON"}, status=400)

    chapter_key = str(payload.get("chapter") or "").strip()
    preamble = str(payload.get("preamble") or "")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return JsonResponse({"ok": False, "error": "blocks 必须是数组"}, status=400)

    spec = chapter_by_key(chapter_key, tpl.key)
    if spec is None:
        return JsonResponse({"ok": False, "error": "未知章节"}, status=400)

    try:
        root = template_root_for_library(tpl)
        macros = _resolve_template_macros(root)
        src = (root / spec.relpath).read_text(encoding="utf-8")
        pre2, all_blocks = parse_chapter_blocks(src, latex_root=root, macros=macros)
        merged = _merge_edits(all_blocks, blocks)
        tex = serialize_chapter(pre2 or preamble, merged, latex_root=root, macros=macros)
        written = write_library_tex(tpl, spec.relpath, tex, allow_system=True)
    except (TemplatePreviewError, LatexTemplateError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "message": f"已保存到模板「{tpl.name}」",
            "relpath": spec.relpath,
            "written": written,
            "content": tex,
            "blocks": len(blocks),
        }
    )


@login_required
@require_POST
def evaluation_latex_template_front_save(request: HttpRequest, key: str) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    try:
        tpl = get_template(key)
    except LatexTemplateError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=404)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "无效 JSON"}, status=400)

    chapter_key = str(payload.get("chapter") or "").strip()
    page = payload.get("page")
    if not isinstance(page, dict):
        return JsonResponse({"ok": False, "error": "缺少页面数据"}, status=400)

    spec = chapter_by_key(chapter_key, tpl.key)
    if spec is None or spec.kind != "front":
        return JsonResponse({"ok": False, "error": "未知前置页"}, status=400)

    from .front_matter import (
        patch_cover_logo,
        patch_declaration_statement,
        patch_qualification_image,
    )

    try:
        root = template_root_for_library(tpl)
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
            _patch_template_main_cover_macros(root, kw)
            from .template_push import library_write_roots

            for alt in library_write_roots(tpl):
                try:
                    if alt.resolve() != root.resolve():
                        _patch_template_main_cover_macros(alt, kw)
                except OSError:
                    if alt != root:
                        _patch_template_main_cover_macros(alt, kw)
        elif spec.key == "qualification":
            path = str(page.get("image_path") or "").strip()
            try:
                angle = int(page.get("image_angle") if page.get("image_angle") is not None else 90)
            except (TypeError, ValueError):
                angle = 90
            try:
                scale = float(
                    page.get("image_scale") if page.get("image_scale") is not None else 1.0
                )
            except (TypeError, ValueError):
                scale = 1.0
            content = patch_qualification_image(content, path, angle=angle, scale=scale)
        elif spec.key == "declaration":
            statement = str(page.get("statement") or "")
            content = patch_declaration_statement(content, statement)

        written = write_library_tex(tpl, spec.relpath, content, allow_system=True)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "message": f"已保存到模板「{tpl.name}」",
            "written": written,
            "redirect": reverse(
                "evaluation_latex_template_structured_edit", kwargs={"key": tpl.key}
            )
            + f"?chapter={spec.key}",
        }
    )


@login_required
@require_http_methods(["POST"])
def evaluation_latex_template_structured_upload_image(
    request: HttpRequest, key: str
) -> HttpResponse:
    denied = _require_perm(request)
    if denied:
        return denied

    try:
        tpl = get_template(key)
    except LatexTemplateError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=404)

    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"ok": False, "error": "请选择图片"}, status=400)

    suffix = Path(upload.name or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return JsonResponse({"ok": False, "error": "仅支持常见图片格式"}, status=400)

    try:
        from .template_push import library_write_roots

        roots = library_write_roots(tpl)
        name = f"edit_{uuid.uuid4().hex[:12]}{suffix}"
        for root in roots:
            images_dir = root / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            dest = images_dir / name
            upload.file.seek(0)
            with dest.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    return JsonResponse({"ok": True, "path": f"images/{name}"})
