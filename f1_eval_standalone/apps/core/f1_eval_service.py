# -*- coding: utf-8 -*-
"""F.1 预评价报告表：工作区、模板/版式读写、提取与生成。"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

SAFE_REL_RE = re.compile(r"^[A-Za-z0-9_\-./]+$")


def package_dir() -> Path:
    return Path(getattr(settings, "F1_EVAL_PACKAGE_DIR", settings.BASE_DIR.parent / "f1_eval_report"))


def workspace_root() -> Path:
    root = Path(getattr(settings, "F1_EVAL_WORKSPACE_ROOT", settings.MEDIA_ROOT / "f1_eval" / "workspaces"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_workspace(user_id: int) -> Path:
    path = workspace_root() / f"user_{user_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_tree_if_missing(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if dst.exists():
        return
    shutil.copytree(src, dst)


def _ensure_tree_files(src: Path, dst: Path) -> None:
    """将 src 下缺失文件补齐到 dst（已存在的不覆盖）。"""
    if not src.exists():
        return
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dst / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def ensure_workspace(user_id: int) -> Path:
    """初始化用户工作区：复制默认模板与版式 JSON。"""
    ws = user_workspace(user_id)
    pkg = package_dir()
    _copy_tree_if_missing(pkg / "templates", ws / "templates")
    # 后续新增的常用文本模板：补齐到已有工作区
    _ensure_tree_files(pkg / "templates" / "common", ws / "templates" / "common")
    _ensure_tree_files(pkg / "templates" / "defaults", ws / "templates" / "defaults")
    base_dst = ws / "base"
    base_dst.mkdir(parents=True, exist_ok=True)
    for name in ("format.json", "attachment_format.json"):
        src = pkg / "base" / name
        dst = base_dst / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    (ws / "uploads").mkdir(exist_ok=True)
    (ws / "uploads" / "attachments").mkdir(exist_ok=True)
    (ws / "assets").mkdir(exist_ok=True)
    (ws / "output").mkdir(exist_ok=True)
    (ws / "data").mkdir(exist_ok=True)
    data_path = ws / "data" / "report_data.json"
    if not data_path.exists():
        data: Dict[str, Any] = {"fields": {}, "attachment_pages": []}
        ensure_fixed_field_defaults(data, ws=ws)
        data_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    meta_path = ws / "uploads" / "attachments_meta.json"
    if not meta_path.exists():
        meta_path.write_text("[]", encoding="utf-8")
    return ws


def _safe_rel(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/") or not SAFE_REL_RE.match(rel):
        raise ValueError(f"非法路径: {rel!r}")
    return rel


def resolve_workspace_file(ws: Path, rel: str) -> Path:
    rel = _safe_rel(rel)
    full = (ws / rel).resolve()
    if not str(full).startswith(str(ws.resolve())):
        raise ValueError("路径越界")
    return full


def list_editable_files(ws: Path) -> List[Dict[str, Any]]:
    """Overleaf 式文件树条目。"""
    items: List[Dict[str, Any]] = []

    def add(rel: str, label: str, group: str) -> None:
        p = ws / rel
        if p.is_file():
            items.append(
                {
                    "path": rel.replace("\\", "/"),
                    "label": label,
                    "group": group,
                    "size": p.stat().st_size,
                }
            )

    add("base/format.json", "正文版式 format.json", "版式")
    add("base/attachment_format.json", "附件版式 attachment_format.json", "版式")

    cat_path = ws / "templates" / "catalog.json"
    if cat_path.is_file():
        add("templates/catalog.json", "栏目索引 catalog.json", "模板索引")
        try:
            catalog = json.loads(cat_path.read_text(encoding="utf-8"))
        except Exception:
            catalog = {}
        front = catalog.get("front") or {}
        for key, rel in front.items():
            add(f"templates/{rel}", f"封面/目录 · {key}", "封面目录")
        for sec in catalog.get("body") or []:
            rel = str(sec.get("path") or "")
            title = sec.get("title") or sec.get("id") or rel
            order = sec.get("order", "")
            add(f"templates/{rel}", f"{order}. {title}", "正文栏目")
        back = catalog.get("back") or {}
        for key, rel in back.items():
            add(f"templates/{rel}", f"附件说明 · {key}", "附件说明")

    lib = ws / "templates" / "library"
    if lib.is_dir():
        for sub in ("device_principles", "workflows"):
            d = lib / sub
            if not d.is_dir():
                continue
            for name in sorted(d.glob("*.md")):
                add(
                    f"templates/library/{sub}/{name.name}",
                    f"{sub}/{name.stem}",
                    "子模板库",
                )

    # 常用文本模板（优先展示 common/；包内索引驱动）
    try:
        from f1_eval_report.templates.loader import list_common_files

        for it in list_common_files():
            rel = str(it.get("path") or "")
            # list_common_files 返回的是包相对 path，映射到工作区
            if rel.startswith("templates/"):
                add(rel, str(it.get("label") or rel), str(it.get("group") or "常用文本模板"))
    except Exception:
        common = ws / "templates" / "common"
        if common.is_dir():
            for path in sorted(common.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".md", ".json"}:
                    rel = path.relative_to(ws).as_posix()
                    add(rel, path.name, "常用文本模板")

    add("data/report_data.json", "报告数据 report_data.json", "数据")
    return items


def read_file(ws: Path, rel: str) -> str:
    path = resolve_workspace_file(ws, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    return path.read_text(encoding="utf-8")


def write_file(ws: Path, rel: str, content: str) -> None:
    path = resolve_workspace_file(ws, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_json(ws: Path, rel: str) -> Any:
    return json.loads(read_file(ws, rel))


def save_json(ws: Path, rel: str, data: Any) -> None:
    write_file(ws, rel, json.dumps(data, ensure_ascii=False, indent=2))


def load_report_data(ws: Path) -> Dict[str, Any]:
    path = ws / "data" / "report_data.json"
    if not path.exists():
        data: Dict[str, Any] = {"fields": {}, "attachment_pages": []}
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {"fields": {}, "attachment_pages": []}
    if ensure_fixed_field_defaults(data, ws=ws):
        try:
            save_report_data(ws, data)
        except Exception:
            pass
    return data


def save_report_data(ws: Path, data: Dict[str, Any]) -> None:
    (ws / "data" / "report_data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ensure_fixed_field_defaults(data: Dict[str, Any], ws: Optional[Path] = None) -> bool:
    """
    为空字段补齐固定栏目模板。
    当前：主要评价依据、评价目标（intro+表2/默认表3）。
    返回是否发生了写入。
    """
    if not isinstance(data, dict):
        return False

    def _apply() -> bool:
        changed = False
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        styles = data.get("field_styles") if isinstance(data.get("field_styles"), dict) else {}
        before_text = str(fields.get("evaluation_basis") or "")
        before_style = styles.get("evaluation_basis")
        try:
            from f1_eval_report.templates.loader import apply_evaluation_basis_default

            apply_evaluation_basis_default(data, force=False)
        except Exception:
            pass
        fields2 = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        styles2 = data.get("field_styles") if isinstance(data.get("field_styles"), dict) else {}
        if (
            str(fields2.get("evaluation_basis") or "") != before_text
            or styles2.get("evaluation_basis") != before_style
        ):
            changed = True

        before_intro = str(data.get("evaluation_objective_intro") or "").strip()
        before_tables = data.get("evaluation_objective_tables")
        if not before_intro:
            try:
                from f1_eval_report.templates.loader import assemble_evaluation_objective
                from f1_eval_report.templates.shielding_summary import devices_from_report_data

                devices = devices_from_report_data(data)
                obj = assemble_evaluation_objective(devices=devices or None)
                data["evaluation_objective_intro"] = obj.get("evaluation_objective_intro") or ""
                if not before_tables:
                    data["evaluation_objective_tables"] = obj.get("evaluation_objective_tables") or []
                changed = True
            except Exception:
                pass
        else:
            try:
                from f1_eval_report.templates.loader import (
                    ensure_evaluation_objective_table_marks,
                )

                fixed = ensure_evaluation_objective_table_marks(before_intro)
                if fixed != before_intro:
                    data["evaluation_objective_intro"] = fixed
                    changed = True
            except Exception:
                pass

        # 职业病危害因素分析：空则按装置清单组稿
        fields_ha = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        if not str(fields_ha.get("hazard_analysis") or "").strip():
            try:
                from f1_eval_report.templates.hazard_analysis import assemble_hazard_analysis
                from f1_eval_report.templates.shielding_summary import devices_from_report_data

                devices = devices_from_report_data(data)
                text = assemble_hazard_analysis(devices or None)
                if text:
                    fields_ha["hazard_analysis"] = text
                    data["fields"] = fields_ha
                    changed = True
            except Exception:
                pass

        # 工作场所布局：空则按机房区位组稿 + 表4/表5骨架
        if not str(data.get("workplace_layout_intro") or "").strip():
            try:
                from f1_eval_report.templates.hazard_analysis import classify_devices
                from f1_eval_report.templates.shielding_summary import devices_from_report_data
                from f1_eval_report.templates.workplace_layout import assemble_workplace_layout

                devices = devices_from_report_data(data)
                fig_start = max(1, len(classify_devices(devices or [])) + 1)
                obj = assemble_workplace_layout(
                    devices or None, fig_start=fig_start, include_tables=True
                )
                data["workplace_layout_intro"] = obj.get("workplace_layout_intro") or ""
                if not data.get("workplace_layout_tables"):
                    data["workplace_layout_tables"] = obj.get("workplace_layout_tables") or []
                changed = True
            except Exception:
                pass

        # 放射防护分区：空则按信息表机房数组稿导语 + 表6汇总行
        fields_pz = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        zoning_text = str(
            fields_pz.get("protection_zoning") or data.get("protection_zoning") or ""
        ).strip()
        need_zoning = (not zoning_text) or ("{room_count}" in zoning_text) or (
            not data.get("protection_zoning_tables")
        )
        if need_zoning:
            try:
                from f1_eval_report.templates.protection_zoning import (
                    assemble_protection_zoning,
                )
                from f1_eval_report.templates.shielding_summary import (
                    devices_from_report_data,
                )

                devices = devices_from_report_data(data)
                obj = assemble_protection_zoning(
                    devices or None,
                    report_data=data,
                    table_mode="summary",
                )
                if (not zoning_text) or ("{room_count}" in zoning_text):
                    fields_pz["protection_zoning"] = obj["protection_zoning"]
                    data["fields"] = fields_pz
                    data["protection_zoning"] = obj["protection_zoning"]
                    changed = True
                if not data.get("protection_zoning_tables"):
                    data["protection_zoning_tables"] = (
                        obj.get("protection_zoning_tables") or []
                    )
                    changed = True
            except Exception:
                pass

        # 屏蔽设施：空则按信息表机房间数组稿导语 + 表7 + 表后总结
        fields_sh = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        shield_text = str(
            fields_sh.get("shielding") or data.get("shielding") or ""
        ).strip()
        need_shield = (
            (not shield_text)
            or ("{room_count}" in shield_text)
            or ("<<<F1_TABLE>>>" not in shield_text)
            or (not data.get("shielding_tables"))
        )
        if need_shield:
            try:
                from f1_eval_report.templates.shielding_facilities import (
                    assemble_shielding_facilities,
                )
                from f1_eval_report.templates.shielding_summary import (
                    devices_from_report_data,
                )

                devices = devices_from_report_data(data)
                pdf = ws / "uploads" / "info_sheet.pdf" if ws is not None else None
                obj = assemble_shielding_facilities(
                    devices or None,
                    pdf_path=str(pdf) if pdf and pdf.is_file() else None,
                )
                # 缺表标记或占位符时重写全文；仅缺表时保留已有正文并补表
                rewrite_text = (
                    (not shield_text)
                    or ("{room_count}" in shield_text)
                    or ("<<<F1_TABLE>>>" not in shield_text)
                )
                if rewrite_text:
                    fields_sh["shielding"] = obj["shielding"]
                    data["fields"] = fields_sh
                    data["shielding"] = obj["shielding"]
                    changed = True
                if not data.get("shielding_tables"):
                    data["shielding_tables"] = obj.get("shielding_tables") or []
                    changed = True
            except Exception:
                pass
        return changed

    if ws is not None:
        from f1_eval_report.templates.loader import templates_root_context

        with templates_root_context(str(ws / "templates")):
            return _apply()
    return _apply()


def list_common_templates_ui(ws: Path) -> List[Dict[str, Any]]:
    """工作台「常用模板」面板：按分组列出可编辑模板文件。"""
    pkg_common = package_dir() / "templates" / "common"
    ws_common = ws / "templates" / "common"
    _ensure_tree_files(pkg_common, ws_common)
    # catalog 有新增条目时同步（包内较新则覆盖工作区索引）
    pkg_cat = pkg_common / "catalog.json"
    ws_cat = ws_common / "catalog.json"
    if pkg_cat.is_file() and (
        not ws_cat.is_file() or pkg_cat.stat().st_mtime > ws_cat.stat().st_mtime
    ):
        ws_cat.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pkg_cat, ws_cat)
    from f1_eval_report.templates.loader import list_common_files, templates_root_context

    with templates_root_context(str(ws / "templates")):
        items = list_common_files()
    out: List[Dict[str, Any]] = []
    for it in items:
        rel = str(it.get("path") or "")
        if not rel:
            continue
        if (ws / rel).is_file():
            out.append(dict(it))
    return out


def reset_common_template_from_package(ws: Path, rel: str) -> str:
    """用包内默认覆盖工作区某一常用模板文件，返回新内容（图片返回空字符串）。"""
    rel = _safe_rel(rel)
    if not rel.startswith("templates/common/"):
        raise ValueError("仅允许重置常用文本模板")
    pkg_file = package_dir() / rel
    if not pkg_file.is_file():
        raise FileNotFoundError(f"包内无此模板: {rel}")
    dst = ws / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pkg_file, dst)
    if rel.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        return ""
    return dst.read_text(encoding="utf-8")


def save_common_template_binary(ws: Path, rel: str, uploaded) -> Path:
    """保存常用模板二进制文件（样式图等），并同步到当前打开项目 assets（若存在同名）。"""
    rel = _safe_rel(rel)
    if not rel.startswith("templates/common/"):
        raise ValueError("仅允许写入常用模板")
    if not rel.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        raise ValueError("仅支持图片文件")
    dst = resolve_workspace_file(ws, rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        for chunk in uploaded.chunks():
            f.write(chunk)
    # 同步到工作区级 style_figures 已写入；再尽量同步当前项目 assets
    name = dst.name
    try:
        from apps.core import f1_eval_projects as projects

        cur = projects.get_current_project(ws) or {}
        pid = str(cur.get("id") or "").strip()
        if pid:
            pdir = projects._project_dir(ws, pid)
            assets = pdir / "assets"
            if assets.is_dir():
                shutil.copy2(dst, assets / name)
            proj_tpl = pdir / "templates" / "common" / "protection_measures" / "style_figures"
            proj_tpl.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, proj_tpl / name)
    except Exception:
        pass
    return dst


def save_info_sheet(ws: Path, uploaded) -> Path:
    dest = ws / "uploads" / "info_sheet.pdf"
    with dest.open("wb") as f:
        for chunk in uploaded.chunks():
            f.write(chunk)
    return dest


def extract_from_info_sheet(ws: Path, pdf_path: Optional[Path] = None) -> Dict[str, Any]:
    pdf = pdf_path or (ws / "uploads" / "info_sheet.pdf")
    if not pdf.is_file():
        raise FileNotFoundError("请先上传评价信息表 PDF")
    from f1_eval_report.extract_ncu2_info_sheet import build_data
    from f1_eval_report.templates.loader import templates_root_context
    from f1_eval_report.templates.workplace_layout import extract_workplace_layout_figures

    with templates_root_context(str(ws / "templates")):
        data = build_data(str(pdf))
    # 合并已有附件页配置
    existing = load_report_data(ws)
    if existing.get("attachment_pages"):
        data["attachment_pages"] = existing["attachment_pages"]
    # 表6：每次提取按信息表机房数重算（导语/汇总行）；手工改请在提取后再编辑
    # 保留用户已编辑的表4/表5（设计列常为手工填写）
    existing_wl_tables = existing.get("workplace_layout_tables")
    if isinstance(existing_wl_tables, list) and existing_wl_tables:
        data["workplace_layout_tables"] = existing_wl_tables
    # 把自动生成的「综上 / WS76」写回可编辑常用模板，便于用户再改
    gen = data.pop("_generated_shielding", None) or {}
    try:
        if gen.get("project_summary"):
            write_file(
                ws,
                "templates/common/evaluation_objective/02_project_summary.md",
                str(gen["project_summary"]).rstrip() + "\n",
            )
        if gen.get("ws76_rooms"):
            ws76 = (
                "根据WS 76-2020《医用X射线诊断设备质量控制检测规范》附录B表B.1的要求，本项目"
                f"{gen['ws76_rooms']}透视防护区检测平面上周围剂量当量率应不大于400μSv/h。\n"
            )
            write_file(ws, "templates/common/evaluation_objective/02_ws76.md", ws76)
    except Exception:
        pass

    # 机房平面布局图：信息表整页渲染 → 工作场所布局栏目末尾（A3 横置）
    try:
        figs = extract_workplace_layout_figures(str(pdf), str(ws / "assets"))
        data["extra_figures::workplace_layout"] = figs
    except Exception:
        data.setdefault("extra_figures::workplace_layout", [])

    # 附件四「本项目相关图纸」：自地理位置图起整页提取 → 附件图册（A3 横置，子标题附件 4-n）
    try:
        from f1_eval_report.templates.info_sheet_figures import (
            extract_project_drawing_figures,
        )

        drawings = extract_project_drawing_figures(str(pdf), str(ws / "assets"))
        data["project_drawing_figures"] = drawings
        if drawings:
            album = replace_attachment4_from_drawings(ws, drawings)
            data["attachment_pages"] = [
                {
                    "primary_title": it.get("primary_title") or "",
                    "secondary_title": it.get("secondary_title") or "",
                    "attachment_code": it.get("attachment_code") or "",
                    "image": str(ws / it["rel_path"]),
                    "page_mode": it.get("page_mode") or "a3_landscape",
                    "image_rotate_deg": int(it.get("image_rotate_deg") or 0),
                    "scale": float(it.get("scale") or 1.0),
                    "original_caption": it.get("original_caption") or "",
                }
                for it in (album.get("attachments") or [])
                if isinstance(it, dict) and it.get("rel_path")
            ]
    except Exception:
        data.setdefault("project_drawing_figures", [])

    # 通风管道布局示意图 + 样式图 → 安全防护措施正文/表8之后（样式 A4，通风 A3 横置）
    try:
        from f1_eval_report.templates.info_sheet_figures import (
            extract_ventilation_duct_figures,
        )
        from f1_eval_report.templates.safety_protection import (
            assemble_safety_protection,
            merge_safety_extra_figures,
        )
        from f1_eval_report.templates.shielding_summary import devices_from_report_data

        vfigs = extract_ventilation_duct_figures(str(pdf), str(ws / "assets"))
        with templates_root_context(str(ws / "templates")):
            devices = devices_from_report_data(data)
            safety = assemble_safety_protection(
                devices or None,
                include_table=True,
                assets_dir=str(ws / "assets"),
                ventilation_figs=vfigs,
            )
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        fields["safety_protection"] = safety["safety_protection"]
        data["fields"] = fields
        data["safety_protection"] = safety["safety_protection"]
        data["safety_protection_tables"] = safety.get("safety_protection_tables") or []
        data["extra_figures::safety_protection"] = safety.get("extra_figures") or merge_safety_extra_figures(
            assets_dir=str(ws / "assets"),
            ventilation_figs=vfigs,
        )
        try:
            import json as _json

            tables = data.get("safety_protection_tables") or []
            if tables and isinstance(tables[0], dict):
                write_file(
                    ws,
                    "templates/common/protection_measures/table8.json",
                    _json.dumps(tables[0], ensure_ascii=False, indent=2) + "\n",
                )
            write_file(
                ws,
                "templates/common/protection_measures/safety_intro.md",
                str(safety.get("safety_protection") or "")
                .split("<<<F1_TABLE>>>")[0]
                .strip()
                + "\n",
            )
        except Exception:
            pass
    except Exception:
        data.setdefault("extra_figures::safety_protection", [])

    # 个人防护用品：导语 + 表9 + 表10 + 表后总结
    try:
        from f1_eval_report.templates.personal_protective_equipment import (
            assemble_personal_protective_equipment,
        )
        from f1_eval_report.templates.shielding_summary import devices_from_report_data

        with templates_root_context(str(ws / "templates")):
            devices = devices_from_report_data(data)
            ppe = assemble_personal_protective_equipment(
                devices or None,
                include_table=True,
                pdf_path=str(pdf) if pdf.is_file() else None,
            )
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        fields["personal_protective_equipment"] = ppe["personal_protective_equipment"]
        data["fields"] = fields
        data["personal_protective_equipment"] = ppe["personal_protective_equipment"]
        data["personal_protective_equipment_tables"] = (
            ppe.get("personal_protective_equipment_tables") or []
        )
        try:
            import json as _json

            tables = data.get("personal_protective_equipment_tables") or []
            if len(tables) >= 1 and isinstance(tables[0], dict):
                write_file(
                    ws,
                    "templates/common/protection_measures/table9.json",
                    _json.dumps(tables[0], ensure_ascii=False, indent=2) + "\n",
                )
            if len(tables) >= 2 and isinstance(tables[1], dict):
                write_file(
                    ws,
                    "templates/common/protection_measures/table10.json",
                    _json.dumps(tables[1], ensure_ascii=False, indent=2) + "\n",
                )
            text = str(ppe.get("personal_protective_equipment") or "")
            parts = text.split("<<<F1_TABLE>>>")
            intro = (parts[0] if parts else "").strip()
            after = (parts[-1] if len(parts) > 1 else "").strip()
            if intro:
                write_file(
                    ws,
                    "templates/common/protection_measures/ppe_intro.md",
                    intro + "\n",
                )
            if after:
                write_file(
                    ws,
                    "templates/common/protection_measures/ppe_after.md",
                    after + "\n",
                )
        except Exception:
            pass
    except Exception:
        data.setdefault("personal_protective_equipment_tables", [])

    # 放射性废物处置：固定常用模板
    try:
        from f1_eval_report.templates.loader import load_common_text

        with templates_root_context(str(ws / "templates")):
            waste = load_common_text("protection_measures/waste_disposal.md").strip()
        if waste:
            fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
            fields["waste_disposal"] = waste
            data["fields"] = fields
            data["waste_disposal"] = waste
            try:
                write_file(
                    ws,
                    "templates/common/protection_measures/waste_disposal.md",
                    waste + "\n",
                )
            except Exception:
                pass
    except Exception:
        pass

    # 健康影响评价 · 正常情况下：表11～15
    try:
        from f1_eval_report.templates.health_impact import assemble_normal_condition
        from f1_eval_report.templates.shielding_summary import devices_from_report_data

        with templates_root_context(str(ws / "templates")):
            devices = devices_from_report_data(data)
            hi = assemble_normal_condition(devices or None, include_table=True)
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        fields["normal_condition"] = hi["normal_condition"]
        data["fields"] = fields
        data["normal_condition"] = hi["normal_condition"]
        data["normal_condition_tables"] = hi.get("normal_condition_tables") or []
        try:
            import json as _json

            tables = data.get("normal_condition_tables") or []
            for i, name in enumerate(
                ["table11", "table12", "table13", "table14", "table15"]
            ):
                if i < len(tables) and isinstance(tables[i], dict):
                    write_file(
                        ws,
                        f"templates/common/health_impact/{name}.json",
                        _json.dumps(tables[i], ensure_ascii=False, indent=2) + "\n",
                    )
            text = str(hi.get("normal_condition") or "")
            parts = [p.strip() for p in text.split("<<<F1_TABLE>>>")]
            # parts: imaging_intro, (t11), (between t11-t12 empty?), ...
            # Better write fixed md from assemble pieces
            from f1_eval_report.templates.loader import load_common_text

            for rel in [
                "health_impact/imaging_intro.md",
                "health_impact/imaging_after.md",
                "health_impact/interventional_intro.md",
            ]:
                try:
                    write_file(
                        ws,
                        f"templates/common/{rel}",
                        load_common_text(rel).rstrip() + "\n",
                    )
                except Exception:
                    pass
            # 介入结论由表14/15生成后写回模板
            after = str(hi.get("interventional_after") or "").strip()
            if after:
                write_file(
                    ws,
                    "templates/common/health_impact/interventional_after.md",
                    after + "\n",
                )
        except Exception:
            pass
    except Exception:
        data.setdefault("normal_condition_tables", [])

    # 健康影响评价 · 异常情况下（固定模板）
    try:
        from f1_eval_report.templates.radiation_management import (
            assemble_abnormal_condition,
        )

        with templates_root_context(str(ws / "templates")):
            ab = assemble_abnormal_condition()
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        fields["abnormal_condition"] = ab
        data["fields"] = fields
        data["abnormal_condition"] = ab
        try:
            write_file(
                ws,
                "templates/common/health_impact/abnormal.md",
                ab.rstrip() + "\n",
            )
        except Exception:
            pass
    except Exception:
        pass

    # 放射防护管理：组织机构 + 表16～21 + 结论与建议
    try:
        from f1_eval_report.templates.radiation_management import (
            assemble_radiation_management,
            build_conclusion,
        )
        from f1_eval_report.templates.shielding_summary import devices_from_report_data

        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        workers_raw = str(
            fields.get("planned_radiation_workers")
            or data.get("planned_radiation_workers")
            or ""
        )
        staff_total = None
        try:
            import re as _re

            m = _re.search(r"(\d+)", workers_raw)
            if m:
                staff_total = int(m.group(1))
        except Exception:
            staff_total = None

        with templates_root_context(str(ws / "templates")):
            devices = devices_from_report_data(data)
            rm = assemble_radiation_management(
                staff_total=staff_total,
                include_tables=True,
            )
            conclusion = build_conclusion(
                devices or None,
                staff_count=int(rm.get("staff_count") or staff_total or 59),
            )

        for k in (
            "organization",
            "management_system",
            "staff_management",
            "personal_monitoring",
            "health_surveillance",
            "radiation_training",
            "archive_management",
        ):
            fields[k] = rm[k]
            data[k] = rm[k]
        fields["conclusion"] = conclusion
        data["fields"] = fields
        data["conclusion"] = conclusion
        for tk in (
            "management_system_tables",
            "staff_management_tables",
            "personal_monitoring_tables",
            "health_surveillance_tables",
            "radiation_training_tables",
            "archive_management_tables",
        ):
            data[tk] = rm.get(tk) or []

        try:
            import json as _json

            table_map = [
                ("management_system_tables", "table16"),
                ("staff_management_tables", "table17"),
                ("personal_monitoring_tables", "table18"),
                ("health_surveillance_tables", "table19"),
                ("radiation_training_tables", "table20"),
                ("archive_management_tables", "table21"),
            ]
            for tk, name in table_map:
                tables = data.get(tk) or []
                if tables and isinstance(tables[0], dict):
                    write_file(
                        ws,
                        f"templates/common/radiation_management/{name}.json",
                        _json.dumps(tables[0], ensure_ascii=False, indent=2) + "\n",
                    )
            from f1_eval_report.templates.loader import load_common_text

            for rel in [
                "radiation_management/organization.md",
                "radiation_management/management_intro.md",
                "radiation_management/management_after.md",
                "radiation_management/staff_after.md",
            ]:
                try:
                    write_file(
                        ws,
                        f"templates/common/{rel}",
                        load_common_text(rel).rstrip() + "\n",
                    )
                except Exception:
                    pass
            # staff_intro 含人数
            intro = str(rm.get("staff_management") or "").split("<<<F1_TABLE>>>")[0].strip()
            if intro:
                write_file(
                    ws,
                    "templates/common/radiation_management/staff_intro.md",
                    intro + "\n",
                )
            write_file(
                ws,
                "templates/common/radiation_management/conclusion.md",
                conclusion.rstrip() + "\n",
            )
        except Exception:
            pass
    except Exception:
        for tk in (
            "management_system_tables",
            "staff_management_tables",
            "personal_monitoring_tables",
            "health_surveillance_tables",
            "radiation_training_tables",
            "archive_management_tables",
        ):
            data.setdefault(tk, [])

    # 按报告顺序统一编号：危害因素分析流程图之后顺延
    try:
        from f1_eval_report.templates.info_sheet_figures import (
            assign_sequential_figure_numbers,
        )

        assign_sequential_figure_numbers(data)
    except Exception:
        pass

    # 表6：附图提取后再组一次，写入机房间数与见图号
    try:
        from f1_eval_report.templates.protection_zoning import assemble_protection_zoning
        from f1_eval_report.templates.shielding_summary import devices_from_report_data

        with templates_root_context(str(ws / "templates")):
            devices = devices_from_report_data(data)
            zoning = assemble_protection_zoning(
                devices or None,
                include_table=True,
                report_data=data,
                table_mode="summary",
            )
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        fields["protection_zoning"] = zoning["protection_zoning"]
        data["fields"] = fields
        data["protection_zoning"] = zoning["protection_zoning"]
        data["protection_zoning_tables"] = zoning.get("protection_zoning_tables") or []
        # 同步常用模板 table6.json，便于在「常用模板」中打开同一份
        try:
            import json as _json

            tables = data.get("protection_zoning_tables") or []
            if tables and isinstance(tables[0], dict):
                write_file(
                    ws,
                    "templates/common/protection_measures/table6.json",
                    _json.dumps(tables[0], ensure_ascii=False, indent=2) + "\n",
                )
        except Exception:
            pass
    except Exception:
        pass

    # 表7：导语 + 表7 + 表后总结（机房间数）；同步常用模板
    try:
        from f1_eval_report.templates.shielding_facilities import (
            assemble_shielding_facilities,
        )
        from f1_eval_report.templates.shielding_summary import devices_from_report_data

        with templates_root_context(str(ws / "templates")):
            devices = devices_from_report_data(data)
            pdf = ws / "uploads" / "info_sheet.pdf"
            shield = assemble_shielding_facilities(
                devices or None,
                pdf_path=str(pdf) if pdf.is_file() else None,
            )
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        fields["shielding"] = shield["shielding"]
        data["fields"] = fields
        data["shielding"] = shield["shielding"]
        data["shielding_tables"] = shield.get("shielding_tables") or []
        try:
            import json as _json

            tables = data.get("shielding_tables") or []
            if tables and isinstance(tables[0], dict):
                write_file(
                    ws,
                    "templates/common/protection_measures/table7.json",
                    _json.dumps(tables[0], ensure_ascii=False, indent=2) + "\n",
                )
            raw_sh = str(shield.get("shielding") or "")
            if "<<<F1_TABLE>>>" in raw_sh:
                intro_part, after_part = raw_sh.split("<<<F1_TABLE>>>", 1)
                write_file(
                    ws,
                    "templates/common/protection_measures/shielding_intro.md",
                    intro_part.strip() + "\n",
                )
                write_file(
                    ws,
                    "templates/common/protection_measures/shielding_after.md",
                    after_part.strip() + "\n",
                )
        except Exception:
            pass
    except Exception:
        pass

    save_report_data(ws, data)
    return data


def load_attachments_meta(ws: Path) -> List[Dict[str, Any]]:
    path = ws / "uploads" / "attachments_meta.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_attachments_meta(ws: Path, items: List[Dict[str, Any]]) -> None:
    (ws / "uploads" / "attachments_meta.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_CN_ORDINAL = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def attachment_code_index(code: str) -> int:
    """附件二 / 附件2 → 2。"""
    s = str(code or "").strip()
    m = re.search(r"附件\s*([一二三四五六七八九十百零\d]+)", s)
    if not m:
        return 0
    token = m.group(1)
    if token.isdigit():
        return int(token)
    if token in _CN_ORDINAL:
        return int(_CN_ORDINAL[token])
    if token.startswith("十") and len(token) == 2 and token[1] in _CN_ORDINAL:
        return 10 + int(_CN_ORDINAL[token[1]])
    return 0


def parse_attachments_list_slots(ws: Path) -> List[Dict[str, str]]:
    """
    从正文「附件」清单解析槽位；若为空则回退到 attachment_format.fixed_attachment_titles。
    返回 [{code, title}, ...]
    """
    data = load_report_data(ws)
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    text = str(fields.get("attachments_list") or "").strip()
    slots: List[Dict[str, str]] = []
    if text:
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            m = re.match(r"^(附件[一二三四五六七八九十百零\d]+)\s*(.*)$", ln)
            if m:
                slots.append({"code": m.group(1), "title": (m.group(2) or "").strip()})
            else:
                slots.append({"code": ln, "title": ""})
    if slots:
        return slots
    try:
        from f1_eval_report.attachments import default_attachment_titles

        project = str(fields.get("project_name") or "")
        fmt = None
        try:
            fmt = load_json(ws, "base/attachment_format.json")
        except Exception:
            pass
        items = default_attachment_titles(project_full_name=project, fmt=fmt)
        return [
            {"code": str(it.get("code") or ""), "title": str(it.get("title") or "")}
            for it in items
            if str(it.get("code") or "").strip()
        ]
    except Exception:
        return []


def page_mode_specs(ws: Path) -> List[Dict[str, Any]]:
    """页面模式（含尺寸，供前端纸张预览）。"""
    try:
        fmt = load_json(ws, "base/attachment_format.json")
        modes = fmt.get("page_modes") or {}
        out: List[Dict[str, Any]] = []
        for k, v in modes.items():
            vv = v or {}
            out.append(
                {
                    "key": k,
                    "label": vv.get("label") or k,
                    "width_pt": float(vv.get("width_pt") or 595.3),
                    "height_pt": float(vv.get("height_pt") or 841.9),
                }
            )
        if out:
            return out
    except Exception:
        pass
    return [
        {"key": "a4_portrait", "label": "A4竖版", "width_pt": 595.3, "height_pt": 841.9},
        {"key": "a4_landscape", "label": "A4横版", "width_pt": 841.9, "height_pt": 595.3},
        {"key": "a3_portrait", "label": "A3竖版", "width_pt": 841.9, "height_pt": 1190.55},
        {"key": "a3_landscape", "label": "A3横版", "width_pt": 1190.55, "height_pt": 841.9},
    ]


def _item_attachment_code(item: Dict[str, Any]) -> str:
    return str(
        item.get("attachment_code") or item.get("primary_title") or ""
    ).strip()


def _next_secondary_title(meta: List[Dict[str, Any]], primary_code: str) -> str:
    idx = attachment_code_index(primary_code) or max(1, len(meta) + 1)
    n = sum(1 for it in meta if _item_attachment_code(it) == primary_code)
    return f"附件{idx}-{n + 1}"


def build_attachment_album(ws: Path) -> Dict[str, Any]:
    """按正文附件标题分组的图册数据（供工作台）。"""
    slots_def = parse_attachments_list_slots(ws)
    meta = load_attachments_meta(ws)
    by_code: Dict[str, List[Dict[str, Any]]] = {}
    for it in meta:
        code = _item_attachment_code(it)
        by_code.setdefault(code, []).append(it)
    used = set()
    slots: List[Dict[str, Any]] = []
    for s in slots_def:
        code = str(s.get("code") or "").strip()
        pages = list(by_code.get(code) or [])
        used.add(code)
        slots.append(
            {
                "code": code,
                "title": str(s.get("title") or ""),
                "pages": pages,
                "page_count": len(pages),
            }
        )
    orphans: List[Dict[str, Any]] = []
    for code, pages in by_code.items():
        if code in used:
            continue
        orphans.extend(pages)
    return {
        "slots": slots,
        "attachments": meta,
        "orphans": orphans,
        "page_modes": page_mode_specs(ws),
    }


def add_attachment_images(
    ws: Path,
    files,
    *,
    primary_code: str = "",
) -> Dict[str, Any]:
    """上传图片并归属到指定附件标题（如「附件二」）；自动生成「附件 2-1」子标题。"""
    meta = load_attachments_meta(ws)
    att_dir = ws / "uploads" / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    fmt: Dict[str, Any] = {}
    try:
        fmt = load_json(ws, "base/attachment_format.json")
    except Exception:
        pass
    default_mode = str(fmt.get("default_page_mode") or "a4_portrait")
    default_rot = int(fmt.get("default_image_rotate_deg") or 0)

    code = str(primary_code or "").strip()
    if not code:
        # 兼容旧调用：落到第一个空槽或按序号
        slots = parse_attachments_list_slots(ws)
        for s in slots:
            c = str(s.get("code") or "")
            if not any(_item_attachment_code(it) == c for it in meta):
                code = c
                break
        if not code:
            idx = len(meta) + 1
            cn = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]
            code = f"附件{cn[min(idx - 1, 8)]}"

    for f in files:
        name = os.path.basename(f.name)
        stem, ext = os.path.splitext(name)
        safe = re.sub(r"[^\w.\-一-龥]+", "_", name) or "image.jpg"
        dest = att_dir / safe
        n = 1
        while dest.exists():
            dest = att_dir / f"{stem}_{n}{ext or '.jpg'}"
            n += 1
        with dest.open("wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        secondary = _next_secondary_title(meta, code)
        meta.append(
            {
                "id": f"att_{len(meta) + 1}_{dest.stem}",
                "filename": dest.name,
                "rel_path": f"uploads/attachments/{dest.name}",
                "attachment_code": code,
                "primary_title": code,
                "secondary_title": secondary,
                "page_mode": default_mode,
                "image_rotate_deg": default_rot,
                "scale": 1.0,
            }
        )
    save_attachments_meta(ws, meta)
    _sync_attachment_pages_into_data(ws, meta)
    return build_attachment_album(ws)


def replace_attachment4_from_drawings(
    ws: Path,
    figures: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    用信息表「地理位置图」起的图纸重建附件四图册页：
    - page_mode = a3_landscape
    - secondary_title = 附件4-n + 原图题（写在框内附件名上）
    - 图题不烧进图片
    保留其它附件（非附件四）的已上传图片。
    """
    import shutil

    meta = load_attachments_meta(ws)
    att_dir = ws / "uploads" / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)

    keep: List[Dict[str, Any]] = []
    for item in meta:
        if _item_attachment_code(item) == "附件四":
            p = ws / str(item.get("rel_path") or "")
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
            continue
        keep.append(item)

    for i, fig in enumerate(figures or [], start=1):
        if not isinstance(fig, dict):
            continue
        src = Path(str(fig.get("image") or ""))
        if not src.is_file():
            continue
        page_no = int(fig.get("source_pdf_page") or i)
        dest_name = f"att4_{i:02d}_p{page_no:02d}{src.suffix or '.png'}"
        dest = att_dir / dest_name
        shutil.copy2(src, dest)
        orig_cap = _strip_att_caption(str(fig.get("caption") or ""))
        code_part = f"附件4-{i}"
        # 图题写到框内附件名上：附件4-1 新建院区地理位置图
        if orig_cap and not orig_cap.startswith(code_part):
            secondary = f"{code_part} {orig_cap}"
        elif orig_cap:
            secondary = orig_cap
        else:
            secondary = code_part
        keep.append(
            {
                "id": f"att4_{i}_{dest.stem}",
                "filename": dest.name,
                "rel_path": f"uploads/attachments/{dest.name}",
                "attachment_code": "附件四",
                "primary_title": "附件四",
                "secondary_title": secondary,
                "original_caption": orig_cap,
                "page_mode": "a3_landscape",
                "image_rotate_deg": 0,
                "scale": 1.0,
                "from_info_sheet": True,
                "source_pdf_page": page_no,
            }
        )

    save_attachments_meta(ws, keep)
    _sync_attachment_pages_into_data(ws, keep)
    return build_attachment_album(ws)


def _strip_att_caption(caption: str) -> str:
    from f1_eval_report.templates.info_sheet_figures import _clean_drawing_caption

    return _clean_drawing_caption(caption)


def update_attachment_meta(ws: Path, item_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    meta = load_attachments_meta(ws)
    for item in meta:
        if item.get("id") != item_id:
            continue
        old_code = _item_attachment_code(item)
        for key in (
            "primary_title",
            "secondary_title",
            "page_mode",
            "image_rotate_deg",
            "scale",
            "attachment_code",
        ):
            if key in patch:
                item[key] = patch[key]
        if "primary_title" in patch or "attachment_code" in patch:
            new_code = str(
                patch.get("attachment_code")
                or patch.get("primary_title")
                or item.get("attachment_code")
                or ""
            ).strip()
            item["attachment_code"] = new_code
            item["primary_title"] = new_code or item.get("primary_title") or ""
            # 仅当归属附件变更且未手改子标题时，重编「附件 n-k」
            if (
                new_code
                and new_code != old_code
                and "secondary_title" not in patch
            ):
                others = [it for it in meta if it.get("id") != item_id]
                item["secondary_title"] = _next_secondary_title(others, new_code)
        break
    save_attachments_meta(ws, meta)
    _sync_attachment_pages_into_data(ws, meta)
    return build_attachment_album(ws)


def delete_attachment(ws: Path, item_id: str) -> Dict[str, Any]:
    meta = load_attachments_meta(ws)
    keep: List[Dict[str, Any]] = []
    for item in meta:
        if item.get("id") == item_id:
            p = ws / item.get("rel_path", "")
            if p.is_file():
                p.unlink()
        else:
            keep.append(item)
    save_attachments_meta(ws, keep)
    _sync_attachment_pages_into_data(ws, keep)
    return build_attachment_album(ws)


def _sync_attachment_pages_into_data(ws: Path, meta: List[Dict[str, Any]]) -> None:
    """仅同步图册页到 report_data；不覆盖正文 attachments_list 正式标题。"""
    data = load_report_data(ws)
    pages = []
    for item in meta:
        code = _item_attachment_code(item)
        pages.append(
            {
                "primary_title": item.get("primary_title") or code,
                "secondary_title": item.get("secondary_title") or "",
                "attachment_code": code,
                "image": str(ws / item["rel_path"]),
                "page_mode": item.get("page_mode") or "a4_portrait",
                "image_rotate_deg": int(item.get("image_rotate_deg") or 0),
                "scale": float(item.get("scale") or 1.0),
                "original_caption": item.get("original_caption") or "",
            }
        )
    data["attachment_pages"] = pages
    save_report_data(ws, data)


def page_mode_options(ws: Path) -> List[Dict[str, str]]:
    return [
        {"key": m["key"], "label": m["label"]}
        for m in page_mode_specs(ws)
    ]


def generate_report_pdf(
    ws: Path,
    *,
    table_template: str = "250075YP",
    include_attachments: bool = True,
) -> Tuple[Path, Path]:
    """生成封面+正文 PDF；可选再生成附件图册并返回路径。

    返回 (body_or_merged_pdf, att_pdf)。
    合并稿写入 output/f1_eval_body.pdf（含封面）；附件仍单独一份，页码在合并后处理。
    """
    from f1_eval_report.generate import generate_f1_eval_pdf
    from f1_eval_report.attachments import generate_attachments_pdf
    from f1_eval_report.cover import (
        ensure_cover_meta_in_data,
        generate_cover_pdf,
        merge_pdfs,
    )
    from f1_eval_report.chrome import apply_chrome_merged
    from f1_eval_report.sections.catalog import all_row_definitions
    from apps.core.f1_eval_editor import (
        apply_label_overrides_to_rows,
        load_label_overrides,
    )

    data = load_report_data(ws)
    cover_meta = ensure_cover_meta_in_data(data)
    # 附图路径：相对 assets/xxx 或仅文件名时，解析为工作区绝对路径
    assets = ws / "assets"
    for key, figs in list(data.items()):
        if not str(key).startswith("extra_figures::") or not isinstance(figs, list):
            continue
        for fig in figs:
            if not isinstance(fig, dict):
                continue
            img = fig.get("image")
            if not img:
                continue
            p = Path(str(img))
            if p.is_file():
                fig["image"] = str(p.resolve())
                continue
            cand = assets / p.name
            if cand.is_file():
                fig["image"] = str(cand.resolve())
    data_path = ws / "data" / "report_data.json"
    save_report_data(ws, data)

    overrides = load_label_overrides(ws, table_template)
    rows = apply_label_overrides_to_rows(all_row_definitions(table_template), overrides)
    base_format = ws / "base" / "format.json"
    # 合并当前模板的列宽覆盖后再生成
    try:
        from apps.core.f1_eval_grid import load_template_table_format
        import copy

        fmt = load_json(ws, "base/format.json")
        fmt = copy.deepcopy(fmt or {})
        table = fmt.setdefault("table", {})
        if not isinstance(table, dict):
            table = {}
            fmt["table"] = table
        pag = fmt.setdefault("pagination", {})
        if isinstance(pag, dict):
            pag["report_chrome"] = True
        # 表题：完整报告表名称（按表宽换行）+ 报告编号
        title = fmt.setdefault("title", {})
        if not isinstance(title, dict):
            title = {}
            fmt["title"] = title
        full_name = str(cover_meta.get("report_full_name") or "").strip()
        if not full_name:
            org = str(cover_meta.get("cover_org_line") or "").strip()
            typ = str(cover_meta.get("cover_project_type_line") or "").strip()
            if typ and not typ.endswith("放射性职业病危害预评价报告表"):
                if typ.endswith("建设项目"):
                    typ = f"{typ}放射性职业病危害预评价报告表"
                else:
                    typ = f"{typ}建设项目放射性职业病危害预评价报告表"
            full_name = f"{org}{typ}".strip()
        title["table_label"] = ""
        title["table_name"] = full_name
        title["title_line1"] = ""
        title["title_line2"] = ""
        title["report_no"] = str(cover_meta.get("report_no") or "")
        title["gap_below_pt"] = 2.0
        tf = load_template_table_format(ws, table_template)
        if isinstance(tf.get("column_widths_mm"), dict):
            widths = dict(table.get("column_widths_mm") or {})
            widths.update(tf["column_widths_mm"])
            table["column_widths_mm"] = widths
        tmp_fmt = ws / "data" / "templates" / table_template / "_format_runtime.json"
        tmp_fmt.parent.mkdir(parents=True, exist_ok=True)
        tmp_fmt.write_text(json.dumps(fmt, ensure_ascii=False, indent=2), encoding="utf-8")
        base_format = tmp_fmt
    except Exception:
        pass

    out_dir = ws / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    cover_pdf = out_dir / "f1_eval_cover.pdf"
    body_only_pdf = out_dir / "f1_eval_body_only.pdf"
    body_pdf = out_dir / "f1_eval_body.pdf"
    att_pdf = out_dir / "f1_eval_attachments.pdf"

    # 生成前按栏目顺序重编号图/表（去掉写死的图13、表8 等）
    try:
        from f1_eval_report.templates.info_sheet_figures import (
            assign_sequential_figure_numbers,
        )

        assign_sequential_figure_numbers(data)
        save_report_data(ws, data)
    except Exception:
        pass

    from f1_eval_report.templates.loader import templates_root_context
    from f1_eval_report.cover import build_toc_entries

    # 先生成正文与附件，再按页码回填封面目录
    with templates_root_context(str(ws / "templates")):
        generate_f1_eval_pdf(
            str(body_only_pdf),
            str(data_path),
            base_format_path=str(base_format),
            table_template=table_template,
            rows=rows,
        )

    if include_attachments and data.get("attachment_pages"):
        att_fmt = ws / "base" / "attachment_format.json"
        try:
            import copy as _copy

            af = load_json(ws, "base/attachment_format.json")
            af = _copy.deepcopy(af or {})
            page_cfg = af.setdefault("page", {})
            if isinstance(page_cfg, dict):
                page_cfg["draw_page_number"] = False
            tmp_af = out_dir / "_att_format_runtime.json"
            tmp_af.write_text(json.dumps(af, ensure_ascii=False, indent=2), encoding="utf-8")
            att_fmt = tmp_af
        except Exception:
            pass
        generate_attachments_pdf(
            data,
            str(att_pdf),
            format_path=str(att_fmt),
            start_page_no=1,
        )
    elif att_pdf.exists():
        att_pdf.unlink()

    # 目录条目：正文第1页 + 各附件起始页
    body_n = 1
    try:
        import fitz as _fitz

        bd = _fitz.open(str(body_only_pdf))
        body_n = len(bd)
        bd.close()
    except Exception:
        body_n = 1

    att_titles: List[str] = []
    att_starts: List[int] = []
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    raw_list = str(fields.get("attachments_list") or "").strip()
    if raw_list:
        for line in raw_list.splitlines():
            t = line.strip()
            if t:
                att_titles.append(t)
    pages = data.get("attachment_pages") if isinstance(data.get("attachment_pages"), list) else []
    # 按主标题分组估算起始页
    if pages:
        groups: List[str] = []
        group_pages: Dict[str, int] = {}
        cur = None
        count = 0
        for fig in pages:
            if not isinstance(fig, dict):
                continue
            prim = str(fig.get("primary_title") or "").strip() or "附件"
            if prim != cur:
                cur = prim
                groups.append(prim)
                group_pages[prim] = count
            count += 1
        if not att_titles:
            # 用图册主标题拼目录（仅名称）
            for g in groups:
                att_titles.append(g)
        # 起始页 = 正文页数 + 组内首图偏移 + 1
        for t in att_titles:
            key = next((g for g in groups if t.startswith(g) or g in t), None)
            off = group_pages.get(key or "", 0)
            att_starts.append(body_n + off + 1)
    else:
        for i, _t in enumerate(att_titles):
            att_starts.append(body_n + i + 1)

    toc_entries = build_toc_entries(
        cover_meta,
        body_page_count=body_n,
        attachment_titles=att_titles,
        attachment_page_starts=att_starts,
    )

    # 封面（含目录）
    try:
        pkg_tpl = Path(__file__).resolve().parents[2] / "f1_eval_report" / "base" / "cover_front_template.pdf"
        tpl = ws / "base" / "cover_front_template.pdf"
        if not tpl.is_file() and pkg_tpl.is_file():
            tpl.parent.mkdir(parents=True, exist_ok=True)
            import shutil as _sh

            _sh.copy2(pkg_tpl, tpl)
        search = [str(ws), str(ws / "uploads"), str(ws / "uploads" / "cover"), str(ws / "assets")]
        generate_cover_pdf(
            data,
            str(cover_pdf),
            template_path=str(tpl) if tpl.is_file() else None,
            search_dirs=search,
            toc_entries=toc_entries,
        )
    except Exception as e:
        cover_pdf = Path("")
        try:
            (out_dir / "cover_error.txt").write_text(str(e), encoding="utf-8")
        except Exception:
            pass

    # 合并：封面 + 正文 + 附件（附件附在报告表后）
    parts = []
    if cover_pdf and Path(cover_pdf).is_file():
        parts.append(str(cover_pdf))
    parts.append(str(body_only_pdf))
    if att_pdf.is_file():
        parts.append(str(att_pdf))
    merge_pdfs(parts, str(body_pdf))

    cover_count = 0
    if cover_pdf and Path(cover_pdf).is_file():
        try:
            import fitz as _fitz

            d = _fitz.open(str(cover_pdf))
            cover_count = len(d)
            d.close()
        except Exception:
            cover_count = 8

    # 全文页眉页脚：正文从封面后第1页计；附件已并入则页码含附件
    try:
        import fitz as _fitz
        from f1_eval_report.cover import _song_fontfile
        from f1_eval_report.chrome import draw_report_footer, draw_report_header
        from f1_eval_report.fonts_util import (
            save_pdf_compressed as _save_pdf,
            subset_doc_fonts as _subset,
        )

        att_n = 0
        if att_pdf.is_file():
            ad = _fitz.open(str(att_pdf))
            att_n = len(ad)
            ad.close()
        # 合并稿已含附件时，总页数以合并稿正文段为准，避免重复加附件页
        merged_body_pages = max(0, 0)
        try:
            md = _fitz.open(str(body_pdf))
            merged_body_pages = max(0, len(md) - cover_count)
            md.close()
        except Exception:
            merged_body_pages = body_n + att_n
        total_flow = merged_body_pages if merged_body_pages > 0 else (body_n + att_n)
        ff = _song_fontfile()
        full_name = str(cover_meta.get("report_full_name") or "")
        report_no = str(cover_meta.get("report_no") or "")

        bdoc = _fitz.open(str(body_pdf))
        try:
            for i in range(len(bdoc)):
                if i < cover_count:
                    continue
                draw_report_header(
                    bdoc[i],
                    full_name=full_name,
                    report_no=report_no,
                    fontfile=ff,
                )
                draw_report_footer(
                    bdoc[i],
                    page_no=i - cover_count + 1,
                    total_pages=total_flow,
                    fontfile=ff,
                )
            _subset(bdoc)
            _save_pdf(bdoc, str(body_pdf))
        finally:
            bdoc.close()

        # 附件单独文件也写连续页码（与合并稿一致的起始偏移）
        if att_pdf.is_file() and att_n:
            doc = _fitz.open(str(att_pdf))
            try:
                for i in range(len(doc)):
                    draw_report_header(
                        doc[i],
                        full_name=full_name,
                        report_no=report_no,
                        fontfile=ff,
                    )
                    draw_report_footer(
                        doc[i],
                        page_no=body_n + i + 1,
                        total_pages=body_n + att_n,
                        fontfile=ff,
                    )
                _subset(doc)
                _save_pdf(doc, str(att_pdf))
            finally:
                doc.close()
    except Exception as e:
        try:
            (out_dir / "chrome_error.txt").write_text(repr(e), encoding="utf-8")
        except Exception:
            pass

    save_report_data(ws, data)
    return body_pdf, att_pdf


def get_cover_meta(ws: Path) -> Dict[str, Any]:
    from f1_eval_report.cover import ensure_cover_meta_in_data

    data = load_report_data(ws)
    meta = ensure_cover_meta_in_data(data)
    save_report_data(ws, data)
    return meta


def save_cover_meta(ws: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    """保存封面/声明页可编辑字段。"""
    from f1_eval_report.cover import ensure_cover_meta_in_data, DEFAULT_DECLARATION

    data = load_report_data(ws)
    cover = data.get("cover_meta") if isinstance(data.get("cover_meta"), dict) else {}
    for key in (
        "report_code",
        "cover_org_line",
        "cover_project_type_line",
        "construction_unit",
        "evaluation_unit",
        "report_date",
        "report_full_name",
        "version_label",
        "draft_label",
        "certificate_image",
    ):
        if key in patch and patch[key] is not None:
            cover[key] = patch[key]
    if isinstance(patch.get("declaration"), dict):
        decl = dict(DEFAULT_DECLARATION)
        old = cover.get("declaration") if isinstance(cover.get("declaration"), dict) else {}
        decl.update(old)
        decl.update(patch["declaration"])
        cover["declaration"] = decl
    data["cover_meta"] = cover
    meta = ensure_cover_meta_in_data(data)
    save_report_data(ws, data)
    return meta


def save_cover_certificate(ws: Path, uploaded) -> Dict[str, Any]:
    """上传资质证书图片（封面第3页）。"""
    dest_dir = ws / "uploads" / "cover"
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(getattr(uploaded, "name", None) or "certificate.jpg").name
    safe = re.sub(r"[^\w.\-一-龥]+", "_", name) or "certificate.jpg"
    dest = dest_dir / safe
    with dest.open("wb") as out:
        for chunk in uploaded.chunks():
            out.write(chunk)
    rel = f"uploads/cover/{dest.name}"
    return save_cover_meta(ws, {"certificate_image": rel})

def format_schema_for_ui(ws: Path) -> Dict[str, Any]:
    """把版式 JSON 展成可编辑字段表（便于后台调节）。"""
    from f1_eval_report.base.columns import default_column_widths_mm
    from apps.core.f1_eval_table_preview import build_main_table_preview
    from apps.core.f1_eval_editor import load_label_overrides

    fmt = load_json(ws, "base/format.json")
    att = load_json(ws, "base/attachment_format.json")
    table = fmt.get("table") if isinstance(fmt.get("table"), dict) else {}
    widths = dict(default_column_widths_mm())
    widths.update(table.get("column_widths_mm") or {})
    preview = build_main_table_preview(
        base_format=fmt,
        table_template="250075YP",
        label_overrides=load_label_overrides(ws),
        workspace=ws,
        persist_seeded_grid=True,
    )
    return {
        "body_format": fmt,
        "attachment_format": att,
        "column_widths_mm": widths,
        "main_table_preview": preview,
        "body_fields": [
            {"key": "margins_mm.top", "label": "上边距 (mm)", "path": ["margins_mm", "top"]},
            {"key": "margins_mm.bottom", "label": "下边距 (mm)", "path": ["margins_mm", "bottom"]},
            {"key": "margins_mm.left", "label": "左边距 (mm)", "path": ["margins_mm", "left"]},
            {"key": "margins_mm.right", "label": "右边距 (mm)", "path": ["margins_mm", "right"]},
            {"key": "text.font_size_pt", "label": "字号 (pt)", "path": ["text", "font_size_pt"]},
            {"key": "text.line_spacing_pt", "label": "行距 (pt)", "path": ["text", "line_spacing_pt"]},
            {"key": "title.table_label", "label": "表标签", "path": ["title", "table_label"]},
            {"key": "title.table_name", "label": "表名称", "path": ["title", "table_name"]},
            {"key": "title.gap_below_pt", "label": "表题下间距 (pt)", "path": ["title", "gap_below_pt"]},
            {"key": "table.row_min_height_pt", "label": "行最小高度 (pt)", "path": ["table", "row_min_height_pt"]},
            {"key": "table.border_width_pt", "label": "边框线宽 (pt)", "path": ["table", "border_width_pt"]},
        ],
        "attachment_fields": [
            {"key": "page.margins_mm.top", "label": "附件上边距 (mm)", "path": ["page", "margins_mm", "top"]},
            {"key": "page.margins_mm.bottom", "label": "附件下边距 (mm)", "path": ["page", "margins_mm", "bottom"]},
            {"key": "page.margins_mm.left", "label": "附件左边距 (mm)", "path": ["page", "margins_mm", "left"]},
            {"key": "page.margins_mm.right", "label": "附件右边距 (mm)", "path": ["page", "margins_mm", "right"]},
            {"key": "page.font_size_pt", "label": "附件字号 (pt)", "path": ["page", "font_size_pt"]},
            {"key": "page.inner_pad_pt", "label": "表框内边距 (pt)", "path": ["page", "inner_pad_pt"]},
            {"key": "default_page_mode", "label": "默认页面模式", "path": ["default_page_mode"]},
            {"key": "default_image_rotate_deg", "label": "默认图片旋转", "path": ["default_image_rotate_deg"]},
        ],
    }


def preview_main_table(
    ws: Path,
    *,
    table_template: str = "250075YP",
    values: Optional[Dict[str, Any]] = None,
    column_widths_mm: Optional[Dict[str, Any]] = None,
    reset_columns: bool = False,
    label_edits: Optional[Dict[str, str]] = None,
    grid: Optional[Dict[str, Any]] = None,
    cell_texts: Optional[List[Dict[str, Any]]] = None,
    reset_grid: bool = False,
    rematerialize_columns: Optional[bool] = None,
) -> Dict[str, Any]:
    """按当前（或传入）版式字段生成空主表预览。"""
    from apps.core.f1_eval_table_preview import build_main_table_preview
    from apps.core.f1_eval_editor import load_label_overrides
    from apps.core.f1_eval_grid import load_grid, normalize_grid, update_cell_text
    from f1_eval_report.base.columns import default_column_widths_mm

    fmt = load_json(ws, "base/format.json")
    body_paths = [
        (["margins_mm", "top"], "margins_mm.top"),
        (["margins_mm", "bottom"], "margins_mm.bottom"),
        (["margins_mm", "left"], "margins_mm.left"),
        (["margins_mm", "right"], "margins_mm.right"),
        (["text", "font_size_pt"], "text.font_size_pt"),
        (["text", "line_spacing_pt"], "text.line_spacing_pt"),
        (["title", "table_label"], "title.table_label"),
        (["title", "table_name"], "title.table_name"),
        (["title", "gap_below_pt"], "title.gap_below_pt"),
        (["table", "row_min_height_pt"], "table.row_min_height_pt"),
        (["table", "border_width_pt"], "table.border_width_pt"),
    ]
    if values:
        key_to_path = {k: p for p, k in body_paths}
        for key, path in key_to_path.items():
            if key in values:
                set_nested(fmt, path, values[key])
    widths_in = column_widths_mm
    if values and isinstance(values.get("column_widths_mm"), dict):
        widths_in = values["column_widths_mm"]
    table = fmt.setdefault("table", {})
    if not isinstance(table, dict):
        table = {}
        fmt["table"] = table
    if reset_columns:
        table["column_widths_mm"] = dict(default_column_widths_mm())
    elif widths_in is not None:
        widths = dict(default_column_widths_mm())
        if not reset_columns:
            widths.update(table.get("column_widths_mm") or {})
        for k, v in widths_in.items():
            try:
                widths[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        table["column_widths_mm"] = widths

    overrides = dict(load_label_overrides(ws, table_template))
    if label_edits:
        for k, v in label_edits.items():
            if k:
                overrides[str(k)] = str(v or "")

    # 模板专属列宽覆盖
    from apps.core.f1_eval_grid import load_template_table_format

    tf = load_template_table_format(ws, table_template)
    if isinstance(tf.get("column_widths_mm"), dict) and not reset_columns and widths_in is None:
        widths = dict(default_column_widths_mm())
        widths.update(table.get("column_widths_mm") or {})
        widths.update(tf["column_widths_mm"])
        table["column_widths_mm"] = widths

    if reset_grid:
        from apps.core.f1_eval_grid import template_data_dir

        path = template_data_dir(ws, table_template) / "main_table_grid.json"
        if path.exists():
            path.unlink()
        legacy = ws / "data" / "main_table_grid.json"
        # 不删共用旧文件，避免误伤另一模板
        grid = None
    elif grid is None:
        grid = load_grid(ws, table_template)

    if isinstance(grid, dict) and cell_texts:
        g = normalize_grid(grid)
        for item in cell_texts:
            if not isinstance(item, dict):
                continue
            g = update_cell_text(
                g,
                int(item.get("r0", 0)),
                int(item.get("c0", 0)),
                str(item.get("text") or ""),
            )
        grid = g

    # 仅在显式要求或恢复默认列宽时按骨架重算列界，避免拖线调节被冲掉
    do_remat = bool(reset_columns) if rematerialize_columns is None else bool(rematerialize_columns)
    if rematerialize_columns is None and reset_columns:
        do_remat = True

    return build_main_table_preview(
        base_format=fmt,
        table_template=table_template,
        label_overrides=overrides,
        grid=grid,
        workspace=ws,
        persist_seeded_grid=True,
        rematerialize_columns=do_remat,
    )


def apply_grid_op(
    ws: Path,
    *,
    op: str,
    at: Optional[int] = None,
    r0: Optional[int] = None,
    c0: Optional[int] = None,
    r1: Optional[int] = None,
    c1: Optional[int] = None,
    grid: Optional[Dict[str, Any]] = None,
    cell_texts: Optional[List[Dict[str, Any]]] = None,
    persist: bool = True,
    table_template: str = "250075YP",
    values: Optional[Dict[str, Any]] = None,
    column_widths_mm: Optional[Dict[str, Any]] = None,
    label_edits: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """对主表网格执行结构操作并返回最新预览。"""
    from apps.core.f1_eval_grid import (
        delete_col,
        delete_row,
        grid_label_overrides,
        insert_col,
        insert_row,
        load_grid,
        merge_cells,
        normalize_grid,
        save_grid,
        unmerge_cell,
        update_cell_text,
    )
    from apps.core.f1_eval_editor import load_label_overrides, save_label_overrides

    g = normalize_grid(grid) if isinstance(grid, dict) else load_grid(ws, table_template)
    if g is None:
        # 先用预览种子一次
        preview_main_table(ws, table_template=table_template)
        g = load_grid(ws, table_template)
    if g is None:
        raise ValueError("无法初始化主表网格")

    if cell_texts:
        for item in cell_texts:
            if not isinstance(item, dict):
                continue
            g = update_cell_text(
                g,
                int(item.get("r0", 0)),
                int(item.get("c0", 0)),
                str(item.get("text") or ""),
            )

    op = (op or "get").strip().lower()
    if op in ("get", "save", ""):
        pass
    elif op == "insert_row":
        g = insert_row(g, int(at if at is not None else g["row_count"]))
    elif op == "delete_row":
        g = delete_row(g, int(at if at is not None else g["row_count"] - 1))
    elif op == "insert_col":
        g = insert_col(g, int(at if at is not None else g["col_count"]))
    elif op == "delete_col":
        g = delete_col(g, int(at if at is not None else g["col_count"] - 1))
    elif op == "merge":
        if None in (r0, c0, r1, c1):
            raise ValueError("合并需要 r0,c0,r1,c1")
        g = merge_cells(g, int(r0), int(c0), int(r1), int(c1))
    elif op == "unmerge":
        if r0 is None or c0 is None:
            raise ValueError("取消合并需要 r0,c0")
        g = unmerge_cell(g, int(r0), int(c0))
    elif op == "reset":
        from apps.core.f1_eval_grid import template_data_dir

        path = template_data_dir(ws, table_template) / "main_table_grid.json"
        if path.exists():
            path.unlink()
        g = None
    else:
        raise ValueError(f"未知操作: {op}")

    if persist and g is not None:
        save_grid(ws, g, table_template)
        # 同步 label_overrides（按模板）
        overrides = load_label_overrides(ws, table_template)
        overrides.update(grid_label_overrides(g))
        if label_edits:
            for k, v in label_edits.items():
                if k:
                    overrides[str(k)] = str(v or "")
        save_label_overrides(ws, overrides, table_template)

    return preview_main_table(
        ws,
        table_template=table_template,
        values=values,
        column_widths_mm=column_widths_mm,
        label_edits=label_edits,
        grid=g,
        reset_grid=(op == "reset"),
    )


def save_body_format_with_columns(
    ws: Path,
    *,
    values: Optional[Dict[str, Any]] = None,
    column_widths_mm: Optional[Dict[str, Any]] = None,
    label_edits: Optional[Dict[str, str]] = None,
    grid: Optional[Dict[str, Any]] = None,
    cell_texts: Optional[List[Dict[str, Any]]] = None,
    table_template: str = "250075YP",
) -> Dict[str, Any]:
    from f1_eval_report.base.columns import default_column_widths_mm
    from apps.core.f1_eval_editor import load_label_overrides, save_label_overrides
    from apps.core.f1_eval_grid import (
        grid_label_overrides,
        load_grid,
        normalize_grid,
        save_grid,
        save_template_table_format,
        update_cell_text,
    )

    rel = "base/format.json"
    data = load_json(ws, rel)
    schema = format_schema_for_ui(ws)
    for fd in schema["body_fields"]:
        key = fd["key"]
        if values and key in values:
            set_nested(data, fd["path"], values[key])

    widths_out: Dict[str, float] = {}
    if column_widths_mm is not None:
        table = data.setdefault("table", {})
        if not isinstance(table, dict):
            table = {}
            data["table"] = table
        widths = dict(default_column_widths_mm())
        widths.update(table.get("column_widths_mm") or {})
        for k, v in column_widths_mm.items():
            try:
                widths[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        # 只保留关键列宽键，避免脏数据
        keep = (
            "label_main",
            "label_main_half",
            "sub_label_row_half",
            "mid_offset",
            "pair_offset",
            "label_mid",
            "label_pair",
            "value_contact",
            "label_phone",
            "label_invest",
            "value_invest",
            "label_area",
        )
        widths_out = {k: widths[k] for k in keep if k in widths}
        # 半宽子表头默认与半宽主表头对齐（两字宽）
        if "label_main_half" in widths_out:
            if "sub_label_row_half" not in column_widths_mm:
                widths_out["sub_label_row_half"] = widths_out["label_main_half"]
        table["column_widths_mm"] = dict(widths_out)
        # ★ 按当前模板写入独立槽位
        save_template_table_format(
            ws,
            {"column_widths_mm": dict(widths_out), "table_template": table_template},
            table_template,
        )
    save_json(ws, rel, data)

    g = normalize_grid(grid) if isinstance(grid, dict) else load_grid(ws, table_template)
    if g is not None:
        if cell_texts:
            for item in cell_texts:
                if not isinstance(item, dict):
                    continue
                g = update_cell_text(
                    g,
                    int(item.get("r0", 0)),
                    int(item.get("c0", 0)),
                    str(item.get("text") or ""),
                )
        save_grid(ws, g, table_template)
        overrides = load_label_overrides(ws, table_template)
        overrides.update(grid_label_overrides(g))
        if label_edits:
            for k, v in label_edits.items():
                if not k:
                    continue
                overrides[str(k)] = str(v or "")
        save_label_overrides(ws, overrides, table_template)
    elif label_edits:
        overrides = load_label_overrides(ws, table_template)
        for k, v in label_edits.items():
            if not k:
                continue
            overrides[str(k)] = str(v or "")
        save_label_overrides(ws, overrides, table_template)

    # 列宽变更后按骨架重算网格列界（写入当前模板）
    preview = preview_main_table(
        ws,
        table_template=table_template,
        grid=None if column_widths_mm is not None else g,
        column_widths_mm=column_widths_mm,
        rematerialize_columns=column_widths_mm is not None,
    )
    preview["saved_template"] = table_template
    return preview


def set_nested(data: Dict[str, Any], path: List[str], value: Any) -> None:
    cur: Any = data
    for key in path[:-1]:
        if key not in cur or not isinstance(cur[key], dict):
            cur[key] = {}
        cur = cur[key]
    leaf = path[-1]
    # 尝试数值
    if isinstance(value, str):
        v = value.strip()
        if re.fullmatch(r"-?\d+", v):
            value = int(v)
        elif re.fullmatch(r"-?\d+\.\d+", v):
            value = float(v)
    cur[leaf] = value
