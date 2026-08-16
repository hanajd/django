"""从评价信息表 PDF 整页导出附图（不拆组件）。

图题从像素中裁出，仅作为 caption 文本保存在图外，便于统一编号。
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple


_CAPTION_HINT = re.compile(
    r"(示意图|平面布局图|布局图|总平面|地理位置|通风管道布局|机房平面布局|图\s*\d+)"
)


def _strip_figure_number(caption: str) -> str:
    """去掉「图 13」「图13、」等编号前缀，方便工作台统一重编号。"""
    s = str(caption or "").strip()
    s2 = re.sub(r"^图\s*\d+\s*[.、:：\-—\s]*", "", s).strip()
    return s2 or s


def _normalize_caption_spaces(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "").strip())


def _block_text(block: Dict[str, Any]) -> str:
    parts: List[str] = []
    for line in block.get("lines") or []:
        for span in line.get("spans") or []:
            t = str(span.get("text") or "")
            if t:
                parts.append(t)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _pick_caption_from_blocks(page: Any, fallback: str) -> Tuple[str, Optional[Tuple[float, float, float, float]]]:
    """优先取含「示意图/布局图」的底部文字块作为图题，并返回其 bbox。"""
    try:
        data = page.get_text("dict") or {}
    except Exception:
        data = {}
    page_h = float(page.rect.height)
    candidates: List[Tuple[float, str, Tuple[float, float, float, float]]] = []
    for block in data.get("blocks") or []:
        if int(block.get("type") or 0) != 0:
            continue
        bbox = block.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        bb = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        bt = _block_text(block)
        if not bt or not _CAPTION_HINT.search(bt):
            continue
        # 图题几乎总在页下半；页内标注（通风口）也含「通风」但不含「示意图」全文
        if "示意图" in bt or "平面布局" in bt or "布局图" in bt or "地理位置" in bt or "总平面" in bt or re.search(r"图\s*\d+", bt):
            y_mid = (bb[1] + bb[3]) / 2.0
            candidates.append((y_mid, bt, bb))
    if candidates:
        # 取最靠下的图题块
        candidates.sort(key=lambda x: x[0], reverse=True)
        _y, text, bb = candidates[0]
        return _strip_figure_number(text), bb
    # 回退：整页文本中找含示意图的行
    text = page.get_text("text") or ""
    for ln in text.splitlines():
        s = re.sub(r"\s+", " ", ln).strip()
        if not s or not _CAPTION_HINT.search(s):
            continue
        if (
            "示意" in s
            or "布局图" in s
            or "平面布局" in s
            or "地理位置" in s
            or "总平面" in s
        ):
            return _strip_figure_number(s), None
    return _strip_figure_number(fallback), None


def _caption_clip_rect(page: Any, fallback_caption: str) -> Tuple[Any, str]:
    """
    定位图题并裁掉其所在条带，返回绘图区 clip 与图外 caption。
    """
    import fitz

    page_rect = page.rect
    page_h = float(page_rect.height)
    page_w = float(page_rect.width)
    caption, cap_bbox = _pick_caption_from_blocks(page, fallback_caption)

    top_cut = 0.0
    bottom_cut = page_h
    pad = 8.0

    if cap_bbox is not None:
        y0, y1 = float(cap_bbox[1]), float(cap_bbox[3])
        y_mid = (y0 + y1) / 2.0
        if y_mid < page_h * 0.35:
            top_cut = max(top_cut, y1 + pad)
        else:
            # 底部图题：裁到图题上方，并多留一点空白
            bottom_cut = min(bottom_cut, max(40.0, y0 - pad))
    else:
        # 无 bbox 时按经验裁掉底部约 12%（A3 横置图题常见落在此处）
        bottom_cut = page_h * 0.88

    # 再扫一遍：去掉其它顶/底题注条
    try:
        data = page.get_text("dict") or {}
    except Exception:
        data = {}
    for block in data.get("blocks") or []:
        if int(block.get("type") or 0) != 0:
            continue
        bbox = block.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        y0, y1 = float(bbox[1]), float(bbox[3])
        bt = _block_text(block)
        if not bt or not _CAPTION_HINT.search(bt):
            continue
        if "通风口" in bt and "示意" not in bt:
            continue
        y_mid = (y0 + y1) / 2.0
        if y_mid < page_h * 0.15 and ("图" in bt or "布局" in bt):
            top_cut = max(top_cut, y1 + pad)
        if y_mid > page_h * 0.78 and (
            "示意" in bt or "布局图" in bt or "平面布局" in bt or "地理位置" in bt or "总平面" in bt
        ):
            bottom_cut = min(bottom_cut, max(40.0, y0 - pad))

    top_cut = max(0.0, min(top_cut, page_h * 0.25))
    bottom_cut = max(top_cut + 80.0, min(bottom_cut, page_h))
    clip = fitz.Rect(0.0, top_cut, page_w, bottom_cut)
    return clip, caption or _strip_figure_number(fallback_caption)


def is_basement_layout_text(text: str) -> bool:
    """地下室/负一层平面布局（工作场所布局不收录）。"""
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return False
    if "地下室" in t or "地下层" in t or "地下一层" in t or "地下二层" in t:
        return True
    if "负一层" in t or "负1层" in t or "－1层" in t or "-1层" in t:
        return True
    if re.search(r"B\s*1\s*层", t, re.I):
        return True
    return False


def is_room_layout_page(text: str) -> bool:
    """机房平面布局图（排除通道走向、地理位置、院区总平面、地下室等）。"""
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return False
    if "通道走向" in t:
        return False
    if "地理位置" in t:
        return False
    if "院区总平面" in t or "总平面图" in t:
        return False
    if is_basement_layout_text(t):
        return False
    if "通风管道" in t:
        return False
    if "平面布局" in t:
        return True
    return False


def is_ventilation_duct_page(text: str) -> bool:
    """通风管道布局示意图。"""
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return False
    if "通风管道" in t and ("布局" in t or "示意" in t):
        return True
    if "通风管道布局" in t:
        return True
    return False


def extract_info_sheet_pages(
    pdf_path: str,
    assets_dir: str,
    *,
    page_pred: Callable[[str], bool],
    file_prefix: str,
    caption_fallback: str,
    dpi: float = 144.0,
    page_mode: str = "a3_landscape",
) -> List[Dict[str, Any]]:
    """
    按页序整页渲染匹配页面为 PNG，返回 A3 横置 figure 列表。

    图题裁出到 caption 字段，不烧进 PNG。
    """
    import fitz
    from f1_eval_report.base.page_modes import apply_page_mode_to_figure

    os.makedirs(assets_dir, exist_ok=True)
    for name in os.listdir(assets_dir):
        if name.startswith(file_prefix) and name.lower().endswith((".png", ".jpg", ".jpeg")):
            try:
                os.remove(os.path.join(assets_dir, name))
            except OSError:
                pass

    doc = fitz.open(pdf_path)
    figures: List[Dict[str, Any]] = []
    zoom = max(1.0, float(dpi) / 72.0)
    matrix = fitz.Matrix(zoom, zoom)
    try:
        for i in range(len(doc)):
            page = doc[i]
            text = page.get_text("text") or ""
            if not page_pred(text):
                continue
            clip, caption = _caption_clip_rect(page, caption_fallback)
            pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            fname = f"{file_prefix}{i + 1:02d}.png"
            out_path = os.path.abspath(os.path.join(assets_dir, fname))
            pix.save(out_path)
            fig = {
                "image": out_path,
                "caption": caption or caption_fallback,
                "standalone_page": True,
                "source_pdf_page": i + 1,
                "full_page_from_info_sheet": True,
                "caption_outside_image": True,
            }
            figures.append(apply_page_mode_to_figure(fig, page_mode))
    finally:
        doc.close()
    return figures


def number_figure_captions(
    figures: List[Dict[str, Any]],
    *,
    start: int = 1,
) -> List[Dict[str, Any]]:
    """按顺序为 caption 加上「图 N 」前缀（已有编号则替换）。"""
    out: List[Dict[str, Any]] = []
    n = int(start)
    for fig in figures:
        if not isinstance(fig, dict):
            continue
        item = dict(fig)
        body = _strip_figure_number(str(item.get("caption") or "").strip())
        if not body:
            body = "附图"
        item["caption"] = f"图{n} {body}"
        item["figure_number"] = n
        out.append(item)
        n += 1
    return out


_TABLE_KEYS_IN_ORDER: Tuple[str, ...] = (
    "radiation_source_tables",
    "evaluation_objective_tables",
    "workplace_layout_tables",
    "protection_zoning_tables",
    "shielding_tables",
    "safety_protection_tables",
    "personal_protective_equipment_tables",
    "normal_condition_tables",
    "management_system_tables",
    "staff_management_tables",
    "personal_monitoring_tables",
    "health_surveillance_tables",
    "radiation_training_tables",
    "archive_management_tables",
)

_FIGURE_KEYS_IN_ORDER: Tuple[str, ...] = (
    "extra_figures::workplace_layout",
    "extra_figures::safety_protection",
)


def _strip_table_number(title: str) -> str:
    s = str(title or "").strip()
    s2 = re.sub(r"^表\s*\d+\s*[.、:：\-—\s]*", "", s).strip()
    return s2 or s


def assign_sequential_table_numbers(data: Dict[str, Any]) -> Dict[str, int]:
    """按栏目顺序为嵌套表题统一「表N …」编号，返回旧号→新号。"""
    old_to_new: Dict[int, int] = {}
    n = 0
    for key in _TABLE_KEYS_IN_ORDER:
        tables = data.get(key)
        if not isinstance(tables, list):
            continue
        for t in tables:
            if not isinstance(t, dict):
                continue
            title = str(t.get("title") or "").strip()
            m = re.match(r"^表\s*(\d+)", title)
            body = _strip_table_number(title) if title else "附表"
            if not body:
                body = "附表"
            n += 1
            if m:
                old_to_new[int(m.group(1))] = n
            t["title"] = f"表{n} {body}"
            t["table_number"] = n
    return old_to_new


def _rewrite_figure_table_refs(
    text: str,
    *,
    fig_map: Dict[int, int],
    table_map: Dict[int, int],
) -> str:
    """替换正文中的见图N/如图N/表N/见表N（避开表B.1 等字母表号）。"""
    if not text:
        return text
    s = str(text)

    def _sub_fig(m: re.Match) -> str:
        prefix, num = m.group(1), int(m.group(2))
        return f"{prefix}{fig_map.get(num, num)}"

    def _sub_tbl(m: re.Match) -> str:
        prefix, num = m.group(1), int(m.group(2))
        return f"{prefix}{table_map.get(num, num)}"

    if fig_map:
        s = re.sub(r"(见图|如图|图\s*)(\d+)", _sub_fig, s)
    if table_map:
        # 见表6 / 如表8 / 表10（前不为字母）
        s = re.sub(r"(?<![A-Za-z])(见表|如表|表)(\d+)", _sub_tbl, s)
    return s


def _apply_ref_maps_to_data(
    data: Dict[str, Any],
    *,
    fig_map: Dict[int, int],
    table_map: Dict[int, int],
) -> None:
    if not fig_map and not table_map:
        return
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else None
    if isinstance(fields, dict):
        for k, v in list(fields.items()):
            if isinstance(v, str) and v.strip():
                fields[k] = _rewrite_figure_table_refs(
                    v, fig_map=fig_map, table_map=table_map
                )
    for k, v in list(data.items()):
        if isinstance(v, str) and k not in ("schema",) and v.strip():
            if k.startswith("extra_figures"):
                continue
            data[k] = _rewrite_figure_table_refs(
                v, fig_map=fig_map, table_map=table_map
            )
        # 嵌套表单元格中的见图号
        if isinstance(v, list) and k.endswith("_tables"):
            for t in v:
                if not isinstance(t, dict):
                    continue
                for row in t.get("rows") or []:
                    if not isinstance(row, dict):
                        continue
                    for cell in row.get("cells") or []:
                        if not isinstance(cell, dict):
                            continue
                        tx = cell.get("text")
                        if isinstance(tx, str) and tx.strip():
                            cell["text"] = _rewrite_figure_table_refs(
                                tx, fig_map=fig_map, table_map=table_map
                            )


def assign_sequential_figure_numbers(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    按报告顺序统一编号图、表（去掉写死的图13/表8 等）：
    危害因素分析流程图占 1..W → 工作场所平面布局 → 安全防护样式图+通风图；
    表按栏目出现顺序 1..N。
    """
    try:
        from f1_eval_report.templates.protection_zoning import _hazard_workflow_figure_count

        n = int(_hazard_workflow_figure_count(data) or 0)
    except Exception:
        n = 0

    fig_old_to_new: Dict[int, int] = {}
    # 危害分析正文中的图 1..W 保持原号（即新号=旧号）
    for i in range(1, n + 1):
        fig_old_to_new[i] = i

    for key in _FIGURE_KEYS_IN_ORDER:
        figs = data.get(key)
        if not isinstance(figs, list) or not figs:
            continue
        cleaned: List[Dict[str, Any]] = []
        for f in figs:
            if not isinstance(f, dict):
                continue
            if key == "extra_figures::workplace_layout" and is_basement_layout_text(
                str(f.get("caption") or "")
            ):
                continue
            cleaned.append(f)
        # 记录旧号
        old_nums: List[Optional[int]] = []
        for f in cleaned:
            m = re.match(r"^图\s*(\d+)", str(f.get("caption") or "").strip())
            old_nums.append(int(m.group(1)) if m else None)
        numbered = number_figure_captions(cleaned, start=n + 1)
        data[key] = numbered
        for old, item in zip(old_nums, numbered):
            new_n = int(item.get("figure_number") or 0)
            if old is not None and new_n:
                fig_old_to_new[old] = new_n
        n += len(numbered)

    table_map = assign_sequential_table_numbers(data)
    # 仅当编号发生变化时改写正文引用，避免无意义替换
    fig_changed = {a: b for a, b in fig_old_to_new.items() if a != b}
    tbl_changed = {a: b for a, b in table_map.items() if a != b}
    _apply_ref_maps_to_data(data, fig_map=fig_changed, table_map=tbl_changed)
    return data


def extract_workplace_layout_figures(
    pdf_path: str,
    assets_dir: str,
    *,
    dpi: float = 144.0,
    page_mode: str = "a3_landscape",
) -> List[Dict[str, Any]]:
    """机房平面布局图 → extra_figures::workplace_layout（不含地下室；图题在 caption）。"""
    figs = extract_info_sheet_pages(
        pdf_path,
        assets_dir,
        page_pred=is_room_layout_page,
        file_prefix="workplace_layout_page_",
        caption_fallback="机房平面布局图",
        dpi=dpi,
        page_mode=page_mode,
    )
    # 题注二次过滤，防止页内文字漏检仍写入
    return [
        f
        for f in figs
        if isinstance(f, dict) and not is_basement_layout_text(str(f.get("caption") or ""))
    ]


def _clean_drawing_caption(caption: str) -> str:
    """去掉「图 N」「附件4-1」等编号前缀，保留图纸名称。"""
    s = _strip_figure_number(str(caption or "").strip())
    s = re.sub(r"^附件\s*\d+\s*[-–—]\s*\d+\s*", "", s).strip()
    return s or str(caption or "").strip()


def is_geography_map_page(text: str) -> bool:
    """新建院区/园区地理位置图（附件四图纸起点）。"""
    t = re.sub(r"\s+", "", str(text or ""))
    return bool(t and "地理位置" in t)


def find_project_drawings_start_page(pdf_path: str) -> int:
    """返回「地理位置图」所在页的 0-based 下标；未找到返回 -1。"""
    import fitz

    doc = fitz.open(pdf_path)
    try:
        for i in range(len(doc)):
            if is_geography_map_page(doc[i].get_text("text") or ""):
                return i
    finally:
        doc.close()
    return -1


def extract_project_drawing_figures(
    pdf_path: str,
    assets_dir: str,
    *,
    dpi: float = 144.0,
    page_mode: str = "a3_landscape",
) -> List[Dict[str, Any]]:
    """
    附件四「本项目相关图纸」：
    自信息表「地理位置图」页起，向后整页提取全部图纸；
    图题裁出到 caption，不烧进 PNG；页面模式默认 A3 横置。
    """
    import fitz
    from f1_eval_report.base.page_modes import apply_page_mode_to_figure

    start = find_project_drawings_start_page(pdf_path)
    if start < 0:
        return []

    file_prefix = "project_drawing_page_"
    os.makedirs(assets_dir, exist_ok=True)
    for name in os.listdir(assets_dir):
        if name.startswith(file_prefix) and name.lower().endswith((".png", ".jpg", ".jpeg")):
            try:
                os.remove(os.path.join(assets_dir, name))
            except OSError:
                pass

    doc = fitz.open(pdf_path)
    figures: List[Dict[str, Any]] = []
    zoom = max(1.0, float(dpi) / 72.0)
    matrix = fitz.Matrix(zoom, zoom)
    try:
        for i in range(start, len(doc)):
            page = doc[i]
            text = page.get_text("text") or ""
            fallback = "本项目相关图纸"
            if is_geography_map_page(text):
                fallback = "新建院区地理位置图"
            elif "总平面" in re.sub(r"\s+", "", text):
                fallback = "院区总平面图"
            elif "平面布局" in re.sub(r"\s+", "", text):
                fallback = "平面布局图"
            clip, caption = _caption_clip_rect(page, fallback)
            caption = _clean_drawing_caption(caption or fallback)
            pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            fname = f"{file_prefix}{i + 1:02d}.png"
            out_path = os.path.abspath(os.path.join(assets_dir, fname))
            pix.save(out_path)
            fig = {
                "image": out_path,
                "caption": caption or fallback,
                "standalone_page": True,
                "source_pdf_page": i + 1,
                "full_page_from_info_sheet": True,
                "caption_outside_image": True,
                "attachment_code": "附件四",
            }
            figures.append(apply_page_mode_to_figure(fig, page_mode))
    finally:
        doc.close()
    return figures


def extract_ventilation_duct_figures(
    pdf_path: str,
    assets_dir: str,
    *,
    dpi: float = 144.0,
    page_mode: str = "a3_landscape",
) -> List[Dict[str, Any]]:
    """通风管道布局示意图（图题在 caption；编号由 assign_sequential_figure_numbers 统一分配）。"""
    return extract_info_sheet_pages(
        pdf_path,
        assets_dir,
        page_pred=is_ventilation_duct_page,
        file_prefix="ventilation_duct_page_",
        caption_fallback="通风管道布局示意图",
        dpi=dpi,
        page_mode=page_mode,
    )
