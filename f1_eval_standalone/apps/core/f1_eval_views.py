# -*- coding: utf-8 -*-
"""F.1 预评价报告表制作 — Web 视图。"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, List

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.core import f1_eval_service as svc
from apps.core.library_access import role_has


def _require_perm(request, perm: str):
    if not role_has(request.user, perm):
        return JsonResponse({"ok": False, "error": "无权访问该功能"}, status=403)
    return None


def _require_perm_page(request, perm: str):
    from django.contrib import messages
    from django.shortcuts import redirect

    if not role_has(request.user, perm):
        messages.error(request, "无权访问该功能")
        return redirect(reverse("dashboard"))
    return None


def _json_error(msg: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": msg}, status=status)


def _ws(request):
    return svc.ensure_workspace(request.user.id)


@login_required
def f1_eval_workbench(request):
    """评价报告表制作主页（栏目结构化编辑）。"""
    denied = _require_perm_page(request, "perm_file_library")
    if denied:
        return denied
    ws = _ws(request)
    from apps.core import f1_eval_editor as editor

    schema = svc.format_schema_for_ui(ws)
    album = svc.build_attachment_album(ws)
    attachments = album.get("attachments") or []
    info_sheet = ws / "uploads" / "info_sheet.pdf"
    body_pdf = ws / "output" / "f1_eval_body.pdf"
    att_pdf = ws / "output" / "f1_eval_attachments.pdf"
    data = svc.load_report_data(ws)
    fields = data.get("fields") or {}
    page_modes = album.get("page_modes") or svc.page_mode_specs(ws)
    default_template = "250075YP"
    nav = editor.editor_nav(default_template, ws)
    from apps.core import f1_eval_projects as projects

    current = projects.ensure_projects_initialized(ws)
    return render(
        request,
        "core/f1_eval_workbench.html",
        {
            "section_nav": nav,
            "format_schema": schema,
            "attachments": attachments,
            "attachment_slots": album.get("slots") or [],
            "page_modes": page_modes,
            "has_info_sheet": info_sheet.is_file(),
            "has_body_pdf": body_pdf.is_file(),
            "has_att_pdf": att_pdf.is_file(),
            "project_name": fields.get("project_name") or current.get("name") or "",
            "current_project": current,
            "table_templates": ["250075YP", "gbzt"],
            "default_template": default_template,
            "js_body_format": schema["body_format"],
            "js_attachment_format": schema["attachment_format"],
            "js_body_fields": schema["body_fields"],
            "js_attachment_fields": schema["attachment_fields"],
            "js_page_modes": page_modes,
            "js_attachment_album": album,
            "js_section_nav": nav,
            "js_main_table_preview": schema.get("main_table_preview") or {},
            "js_column_widths": schema.get("column_widths_mm") or {},
            "download_body_url": reverse("f1_eval_download", args=["body"]),
            "download_att_url": reverse("f1_eval_download", args=["attachments"]),
        },
    )


@login_required
@require_GET
def f1_eval_api_section(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    section_id = request.GET.get("section") or ""
    template = request.GET.get("template") or "250075YP"
    try:
        ws = _ws(request)
        payload = editor.build_section_editor(ws, section_id, template)
        return JsonResponse({"ok": True, **payload})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_save_section(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    try:
        payload = json.loads(request.body.decode("utf-8"))
        ws = _ws(request)
        result = editor.save_section_edits(
            ws,
            section_id=str(payload.get("section_id") or ""),
            edits=payload.get("edits") or [],
            table_template=str(payload.get("template") or "250075YP"),
        )
        nav = editor.editor_nav(str(payload.get("template") or "250075YP"), ws)
        return JsonResponse({"ok": True, **result, "section_nav": nav})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_GET
def f1_eval_api_table(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    try:
        ws = _ws(request)
        common_path = str(request.GET.get("path") or "").strip()
        if common_path:
            payload = editor.load_common_template_table_editor(ws, common_path)
        else:
            payload = editor.load_embedded_table_editor(
                ws,
                str(request.GET.get("tables_key") or ""),
                int(request.GET.get("table_index") or 0),
            )
        return JsonResponse({"ok": True, **payload})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_save_table(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    try:
        payload = json.loads(request.body.decode("utf-8"))
        ws = _ws(request)
        common_path = str(payload.get("path") or "").strip()
        if common_path or str(payload.get("mode") or "") == "common_template":
            result = editor.save_common_template_table_editor(
                ws,
                rel=common_path or str(payload.get("path") or ""),
                edit_grid=payload.get("edit_grid") or payload.get("grid"),
                title=payload.get("title"),
            )
        else:
            result = editor.save_embedded_table_editor(
                ws,
                tables_key=str(payload.get("tables_key") or ""),
                table_index=int(payload.get("table_index") or 0),
                grid=payload.get("grid"),
                edit_grid=payload.get("edit_grid"),
                title=payload.get("title"),
            )
        return JsonResponse({"ok": True, **result})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_table_op(request):
    """嵌套表结构操作（不落盘，由前端保存时写入）。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    try:
        payload = json.loads(request.body.decode("utf-8"))
        result = editor.operate_embedded_table_editor(
            edit_grid=payload.get("edit_grid") or {},
            op=str(payload.get("op") or ""),
            payload=payload,
        )
        return JsonResponse({"ok": True, **result})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_add_table(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    try:
        payload = json.loads(request.body.decode("utf-8"))
        ws = _ws(request)
        result = editor.add_embedded_table(ws, str(payload.get("tables_key") or ""))
        return JsonResponse({"ok": True, **result})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_delete_table(request):
    """删除嵌套表。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        tables_key = str(payload.get("tables_key") or "")
        if not tables_key:
            return _json_error("缺少 tables_key")
        try:
            table_index = int(payload.get("table_index"))
        except (TypeError, ValueError):
            return _json_error("缺少 table_index")
        ws = _ws(request)
        result = editor.delete_embedded_table(ws, tables_key, table_index)
        return JsonResponse({"ok": True, **result})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_delete_figure(request):
    """删除独立插页或附图。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        figures_key = str(payload.get("figures_key") or "")
        figure_key = str(payload.get("figure_key") or "")
        raw_idx = payload.get("figure_index")
        figure_index = None
        if raw_idx is not None and raw_idx != "":
            try:
                figure_index = int(raw_idx)
            except (TypeError, ValueError):
                return _json_error("figure_index 无效")
        ws = _ws(request)
        result = editor.delete_figure(
            ws,
            figures_key=figures_key,
            figure_index=figure_index,
            figure_key=figure_key,
        )
        return JsonResponse({"ok": True, **result})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_import_excel(request):
    """从 Excel 导入嵌套表（覆盖当前表或追加新表）。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    tables_key = request.POST.get("tables_key") or ""
    f = request.FILES.get("file")
    if not tables_key or not f:
        return _json_error("缺少 tables_key 或 file")
    name = (f.name or "").lower()
    if not (name.endswith(".xlsx") or name.endswith(".xlsm")):
        return _json_error("请上传 .xlsx / .xlsm 文件")
    try:
        ws = _ws(request)
        content = b"".join(chunk for chunk in f.chunks())
        raw_idx = request.POST.get("table_index")
        try:
            table_index = int(raw_idx) if raw_idx not in (None, "") else None
        except (TypeError, ValueError):
            table_index = None
        # table_index=-1 表示追加新表
        if table_index is not None and table_index < 0:
            table_index = None
        result = editor.import_excel_into_table(
            ws,
            tables_key=tables_key,
            content=content,
            filename=f.name or "",
            table_index=table_index,
            title=request.POST.get("title") or None,
        )
        return JsonResponse({"ok": True, **result})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_export_excel(request):
    """导出嵌套表为 Excel（可用当前编辑中的 edit_grid，含未保存修改）。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from urllib.parse import quote

    from django.http import HttpResponse

    from apps.core import f1_eval_editor as editor

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    tables_key = str(payload.get("tables_key") or "")
    edit_grid = payload.get("edit_grid") if isinstance(payload.get("edit_grid"), dict) else None
    # 允许仅凭当前编辑网格导出（常用模板打开时 tables_key 可能为空）
    if not tables_key and not edit_grid:
        return _json_error("缺少 tables_key")
    try:
        table_index = int(payload.get("table_index") or 0)
    except (TypeError, ValueError):
        table_index = 0
    title = str(payload.get("title") or "") or None
    try:
        ws = _ws(request)
        content, filename = editor.export_embedded_table_xlsx(
            ws,
            tables_key=tables_key or "_export_grid_",
            table_index=table_index,
            edit_grid=edit_grid,
            title=title,
        )
        resp = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        # 兼容中文文件名
        resp["Content-Disposition"] = (
            f"attachment; filename=\"export.xlsx\"; filename*=UTF-8''{quote(filename)}"
        )
        return resp
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_upload_figure(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    figure_key = request.POST.get("figure_key") or ""
    figures_key = request.POST.get("figures_key") or ""
    f = request.FILES.get("file")
    if (not figure_key and not figures_key) or not f:
        return _json_error("缺少 figure_key/figures_key 或 file")
    try:
        ws = _ws(request)
        content = b"".join(chunk for chunk in f.chunks())
        raw_idx = request.POST.get("figure_index")
        try:
            figure_index = int(raw_idx) if raw_idx not in (None, "") else None
        except (TypeError, ValueError):
            figure_index = None
        fig = editor.upload_figure(
            ws,
            figure_key or figures_key,
            f.name,
            content,
            caption=request.POST.get("caption"),
            figures_key=figures_key,
            figure_index=figure_index,
            page_mode=str(request.POST.get("page_mode") or "") or None,
            row_id=str(request.POST.get("row_id") or ""),
        )
        return JsonResponse({"ok": True, "figure": fig})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_save_figure_caption(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_editor as editor

    try:
        payload = json.loads(request.body.decode("utf-8"))
        ws = _ws(request)
        fig_idx = payload.get("figure_index")
        try:
            fig_idx = int(fig_idx) if fig_idx is not None and str(fig_idx) != "" else None
        except (TypeError, ValueError):
            fig_idx = None
        fig = editor.save_figure_caption(
            ws,
            str(payload.get("figure_key") or ""),
            str(payload.get("caption") or ""),
            figures_key=str(payload.get("figures_key") or ""),
            figure_index=fig_idx,
            page_mode=str(payload.get("page_mode") or "") or None,
        )
        return JsonResponse({"ok": True, "figure": fig})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_GET
def f1_eval_asset_media(request, filename: str):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        raise Http404()
    ws = _ws(request)
    path = (ws / "assets" / filename).resolve()
    if not str(path).startswith(str((ws / "assets").resolve())):
        raise Http404()
    if not path.is_file():
        raise Http404()
    ctype, _ = mimetypes.guess_type(str(path))
    return FileResponse(path.open("rb"), content_type=ctype or "application/octet-stream")


def _group_files(files):
    groups = {}
    order = ["版式", "模板索引", "封面目录", "正文栏目", "附件说明", "子模板库", "数据"]
    for f in files:
        groups.setdefault(f["group"], []).append(f)
    return [(g, groups[g]) for g in order if g in groups] + [
        (g, items) for g, items in groups.items() if g not in order
    ]


@login_required
@require_GET
def f1_eval_api_common_templates(request):
    """列出常用文本模板（分组），供工作台维护。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return denied
    try:
        ws = _ws(request)
        items = svc.list_common_templates_ui(ws)
        groups: Dict[str, Any] = {}
        order: List[str] = []
        for it in items:
            gid = str(it.get("group_id") or "other")
            if gid not in groups:
                groups[gid] = {
                    "id": gid,
                    "title": str(it.get("group_title") or gid),
                    "items": [],
                }
                order.append(gid)
            groups[gid]["items"].append(it)
        return JsonResponse(
            {
                "ok": True,
                "groups": [groups[g] for g in order],
                "count": len(items),
            }
        )
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_GET
def f1_eval_common_template_media(request):
    """预览常用模板中的图片（样式图等）。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        raise Http404()
    rel = request.GET.get("path") or ""
    try:
        ws = _ws(request)
        path = svc.resolve_workspace_file(ws, rel)
        if not path.is_file():
            raise Http404()
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            raise Http404()
        ctype, _ = mimetypes.guess_type(str(path))
        resp = FileResponse(path.open("rb"), content_type=ctype or "application/octet-stream")
        resp["Cache-Control"] = "no-cache"
        return resp
    except Http404:
        raise
    except Exception:
        raise Http404()


@login_required
@require_POST
def f1_eval_api_upload_common_template(request):
    """替换常用模板图片（样式图）。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return denied
    rel = str(request.POST.get("path") or "").strip()
    f = request.FILES.get("file")
    if not rel or not f:
        return _json_error("缺少 path 或 file")
    try:
        ws = _ws(request)
        path = svc.save_common_template_binary(ws, rel, f)
        return JsonResponse({"ok": True, "path": rel, "size": path.stat().st_size})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_reset_common_template(request):
    """将某一常用模板重置为包内默认。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return denied
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}") if request.body else {}
        rel = str(payload.get("path") or "")
        ws = _ws(request)
        content = svc.reset_common_template_from_package(ws, rel)
        is_binary = rel.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))
        if is_binary:
            # 再走一遍二进制保存，同步到当前项目 assets
            path = svc.resolve_workspace_file(ws, rel)
            if path.is_file():

                class _Uploaded:
                    def __init__(self, data: bytes):
                        self._data = data

                    def chunks(self):
                        yield self._data

                svc.save_common_template_binary(ws, rel, _Uploaded(path.read_bytes()))
        return JsonResponse({"ok": True, "path": rel, "content": content, "is_binary": is_binary})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_GET
def f1_eval_api_file(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    rel = request.GET.get("path") or ""
    try:
        ws = _ws(request)
        content = svc.read_file(ws, rel)
        return JsonResponse({"ok": True, "path": rel, "content": content})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_save_file(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        rel = payload.get("path") or ""
        content = payload.get("content")
        if content is None:
            return _json_error("缺少 content")
        ws = _ws(request)
        svc.write_file(ws, rel, content)
        return JsonResponse({"ok": True, "path": rel})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_save_format_fields(request):
    """快捷保存版式字段（正文 / 附件）；正文可附带列宽。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        kind = payload.get("kind") or "body"
        values = payload.get("values") or {}
        ws = _ws(request)
        if kind == "body":
            preview = svc.save_body_format_with_columns(
                ws,
                values=values,
                column_widths_mm=payload.get("column_widths_mm"),
                label_edits=payload.get("label_edits"),
                grid=payload.get("grid"),
                cell_texts=payload.get("cell_texts"),
                table_template=str(payload.get("template") or "250075YP"),
            )
            return JsonResponse({
                "ok": True,
                "preview": preview,
                "section_nav": preview.get("section_nav") or [],
                "saved_template": preview.get("saved_template") or payload.get("template"),
            })
        rel = "base/attachment_format.json"
        schema = svc.format_schema_for_ui(ws)
        field_defs = schema["attachment_fields"]
        data = svc.load_json(ws, rel)
        for fd in field_defs:
            key = fd["key"]
            if key in values:
                svc.set_nested(data, fd["path"], values[key])
        svc.save_json(ws, rel, data)
        return JsonResponse({"ok": True})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_preview_format(request):
    """按草稿版式实时预览空主表（不落盘）。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        ws = _ws(request)
        preview = svc.preview_main_table(
            ws,
            table_template=str(payload.get("template") or "250075YP"),
            values=payload.get("values") or {},
            column_widths_mm=payload.get("column_widths_mm"),
            reset_columns=bool(payload.get("reset_columns")),
            label_edits=payload.get("label_edits"),
            grid=payload.get("grid"),
            cell_texts=payload.get("cell_texts"),
            reset_grid=bool(payload.get("reset_grid")),
            rematerialize_columns=payload.get("rematerialize_columns"),
        )
        return JsonResponse({"ok": True, "preview": preview})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_table_grid(request):
    """主表网格结构操作：插删行列、合并/取消合并。"""
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        ws = _ws(request)
        preview = svc.apply_grid_op(
            ws,
            op=str(payload.get("op") or "get"),
            at=payload.get("at"),
            r0=payload.get("r0"),
            c0=payload.get("c0"),
            r1=payload.get("r1"),
            c1=payload.get("c1"),
            grid=payload.get("grid"),
            cell_texts=payload.get("cell_texts"),
            persist=payload.get("persist", True) is not False,
            table_template=str(payload.get("template") or "250075YP"),
            values=payload.get("values") or {},
            column_widths_mm=payload.get("column_widths_mm"),
            label_edits=payload.get("label_edits"),
        )
        return JsonResponse({"ok": True, "preview": preview, "grid": preview.get("grid")})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_upload_info_sheet(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    f = request.FILES.get("file")
    if not f:
        return _json_error("未选择文件")
    try:
        ws = _ws(request)
        path = svc.save_info_sheet(ws, f)
        data = svc.extract_from_info_sheet(ws, path)
        fields = data.get("fields") or {}
        return JsonResponse(
            {
                "ok": True,
                "project_name": fields.get("project_name") or "",
                "org_name": fields.get("org_name") or "",
                "summary_preview": (fields.get("project_summary") or "")[:240],
            }
        )
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_GET
def f1_eval_api_cover_meta(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        ws = _ws(request)
        meta = svc.get_cover_meta(ws)
        return JsonResponse({"ok": True, "cover_meta": meta})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_save_cover_meta(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        ws = _ws(request)
        meta = svc.save_cover_meta(ws, payload if isinstance(payload, dict) else {})
        return JsonResponse({"ok": True, "cover_meta": meta})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_upload_cover_certificate(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    f = request.FILES.get("file")
    if not f:
        return _json_error("未选择证书图片")
    try:
        ws = _ws(request)
        meta = svc.save_cover_certificate(ws, f)
        return JsonResponse({"ok": True, "cover_meta": meta})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_GET
def f1_eval_api_attachment_album(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        ws = _ws(request)
        album = svc.build_attachment_album(ws)
        return JsonResponse({"ok": True, **album})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_upload_attachments(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    files = request.FILES.getlist("files")
    if not files:
        return _json_error("未选择图片")
    primary_code = (
        request.POST.get("primary_code")
        or request.POST.get("attachment_code")
        or ""
    ).strip()
    try:
        ws = _ws(request)
        album = svc.add_attachment_images(ws, files, primary_code=primary_code)
        return JsonResponse({"ok": True, **album})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_update_attachment(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        item_id = payload.get("id") or ""
        if not item_id:
            return _json_error("缺少 id")
        patch = {
            k: payload[k]
            for k in (
                "primary_title",
                "secondary_title",
                "page_mode",
                "image_rotate_deg",
                "scale",
                "attachment_code",
            )
            if k in payload
        }
        ws = _ws(request)
        album = svc.update_attachment_meta(ws, item_id, patch)
        return JsonResponse({"ok": True, **album})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def f1_eval_api_delete_attachment(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        ws = _ws(request)
        album = svc.delete_attachment(ws, payload.get("id") or "")
        return JsonResponse({"ok": True, **album})
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_GET
def f1_eval_attachment_media(request, filename: str):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        raise Http404()
    ws = _ws(request)
    path = (ws / "uploads" / "attachments" / filename).resolve()
    if not str(path).startswith(str((ws / "uploads" / "attachments").resolve())):
        raise Http404()
    if not path.is_file():
        raise Http404()
    ctype, _ = mimetypes.guess_type(str(path))
    return FileResponse(path.open("rb"), content_type=ctype or "application/octet-stream")


@login_required
@require_GET
def f1_eval_api_project_list(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_projects as projects

    ws = _ws(request)
    items = projects.list_projects(ws)
    current = projects.get_current_project(ws)
    return JsonResponse({"ok": True, "projects": items, "current": current})


@login_required
@require_POST
def f1_eval_api_project_create(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_projects as projects

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}") if request.body else {}
    except Exception:
        payload = {}
    try:
        ws = _ws(request)
        meta = projects.create_project(ws, str(payload.get("name") or ""))
        return JsonResponse({"ok": True, "project": meta})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_project_save(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_projects as projects

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}") if request.body else {}
    except Exception:
        payload = {}
    try:
        ws = _ws(request)
        meta = projects.save_project(
            ws,
            project_id=str(payload.get("project_id") or "") or None,
            name=str(payload.get("name") or "") or None,
        )
        return JsonResponse({"ok": True, "project": meta})
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_project_open(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    from apps.core import f1_eval_projects as projects

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}") if request.body else {}
    except Exception:
        payload = {}
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id:
        return _json_error("缺少 project_id")
    try:
        ws = _ws(request)
        meta = projects.open_project(ws, project_id)
        data = svc.load_report_data(ws)
        fields = data.get("fields") or {}
        body_pdf = ws / "output" / "f1_eval_body.pdf"
        att_pdf = ws / "output" / "f1_eval_attachments.pdf"
        return JsonResponse(
            {
                "ok": True,
                "project": meta,
                "project_name": fields.get("project_name") or meta.get("name") or "",
                "has_body_pdf": body_pdf.is_file(),
                "has_att_pdf": att_pdf.is_file(),
            }
        )
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_POST
def f1_eval_api_generate(request):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        return _json_error("无权访问", 403)
    try:
        payload = {}
        if request.content_type and "application/json" in request.content_type:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        else:
            payload = {
                "template": request.POST.get("template") or "250075YP",
                "include_attachments": request.POST.get("include_attachments", "1") == "1",
            }
        ws = _ws(request)
        body_pdf, att_pdf = svc.generate_report_pdf(
            ws,
            table_template=str(payload.get("template") or "250075YP"),
            include_attachments=bool(payload.get("include_attachments", True)),
        )
        return JsonResponse(
            {
                "ok": True,
                "body_pdf": reverse("f1_eval_download", args=["body"]),
                "att_pdf": reverse("f1_eval_download", args=["attachments"])
                if att_pdf.is_file()
                else "",
            }
        )
    except Exception as e:
        return _json_error(str(e), 500)


@login_required
@require_GET
def f1_eval_download(request, kind: str):
    denied = _require_perm(request, "perm_file_library")
    if denied:
        raise Http404()
    ws = _ws(request)
    if kind == "body":
        path = ws / "output" / "f1_eval_body.pdf"
        try:
            from f1_eval_report.cover import export_body_pdf_filename

            data = svc.load_report_data(ws)
            name = export_body_pdf_filename(data)
        except Exception:
            name = "f1_eval_report.pdf"
    elif kind == "attachments":
        path = ws / "output" / "f1_eval_attachments.pdf"
        name = "f1_eval_attachments.pdf"
    else:
        raise Http404()
    if not path.is_file():
        raise Http404("尚未生成")
    return FileResponse(path.open("rb"), as_attachment=True, filename=name, content_type="application/pdf")
