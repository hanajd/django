import argparse
import base64
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import fitz
from PIL import Image


@dataclass
class Config:
    line_tolerance_px: float = 3.0
    gap_threshold_px: float = 6.0
    merge_paragraph: bool = False
    table_aware: bool = True
    detect_images: bool = True
    detect_watermarks: bool = True
    table_min_bbox_area: float = 5000.0
    table_min_avg_cell_area: float = 300.0
    table_min_bbox_width: float = 220.0
    single_cell_min_width: float = 18.0
    single_cell_min_height: float = 10.0
    single_cell_min_area: float = 220.0
    enable_synthetic_cells: bool = False
    export_rebuild_payload: bool = True
    draw_label: bool = True
    draw_fill_alpha: float = 0.08
    draw_color: Sequence[float] = (1.0, 0.0, 0.0)
    draw_width: float = 0.8
    label_font_size: float = 6.0


def _token_from_word_tuple(word: Sequence, page_num: int) -> Dict:
    x0, y0, x1, y1, text = word[:5]
    return {
        "text": text,
        "x0": float(x0),
        "y0": float(y0),
        "x1": float(x1),
        "y1": float(y1),
        "page": page_num,
    }


def extract_page_tokens(page: fitz.Page, page_num: int) -> List[Dict]:
    words = page.get_text("words")
    tokens = [_token_from_word_tuple(w, page_num) for w in words if len(w) >= 5 and str(w[4]).strip()]
    tokens.sort(key=lambda t: ((t["y0"] + t["y1"]) / 2.0, t["x0"]))
    return tokens


def _assign_lines(tokens: Sequence[Dict], line_tolerance_px: float) -> List[List[Dict]]:
    lines: List[List[Dict]] = []
    line_centers: List[float] = []

    for tk in sorted(tokens, key=lambda t: ((t["y0"] + t["y1"]) / 2.0, t["x0"])):
        cy = (tk["y0"] + tk["y1"]) / 2.0
        assigned_idx = None
        for idx, center in enumerate(line_centers):
            if abs(cy - center) <= line_tolerance_px:
                assigned_idx = idx
                break

        if assigned_idx is None:
            lines.append([tk])
            line_centers.append(cy)
        else:
            lines[assigned_idx].append(tk)
            prev_center = line_centers[assigned_idx]
            line_centers[assigned_idx] = (prev_center * (len(lines[assigned_idx]) - 1) + cy) / len(
                lines[assigned_idx]
            )

    for line in lines:
        line.sort(key=lambda t: t["x0"])

    lines.sort(key=lambda line: min(t["y0"] for t in line))
    return lines


def _join_tokens_text(tokens: Sequence[Dict]) -> str:
    out: List[str] = []
    for i, tk in enumerate(tokens):
        if i > 0:
            prev = tokens[i - 1]["text"]
            cur = tk["text"]
            if prev and cur:
                out.append(" ")
        out.append(tk["text"])
    return "".join(out).strip()


def _make_block(tokens: Sequence[Dict], page_no: int, order: int) -> Dict:
    x0 = min(t["x0"] for t in tokens)
    y0 = min(t["y0"] for t in tokens)
    x1 = max(t["x1"] for t in tokens)
    y1 = max(t["y1"] for t in tokens)
    return {
        "id": f"p{page_no}_b{order}",
        "order": order,
        "text": _join_tokens_text(tokens),
        "bbox": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
        "token_count": len(tokens),
        "tokens": [
            {"text": t["text"], "x0": t["x0"], "y0": t["y0"], "x1": t["x1"], "y1": t["y1"]}
            for t in tokens
        ],
    }


def _make_object_block(page_no: int, order: int, x0: float, y0: float, x1: float, y1: float, obj_type: str, text: str) -> Dict:
    return {
        "id": f"p{page_no}_b{order}",
        "order": order,
        "type": obj_type,
        "text": text,
        "bbox": {"x": float(x0), "y": float(y0), "w": float(x1 - x0), "h": float(y1 - y0)},
        "token_count": 0,
        "tokens": [],
    }


def _coerce_rect(raw_rect) -> Optional[fitz.Rect]:
    if raw_rect is None:
        return None
    if isinstance(raw_rect, fitz.Rect):
        return raw_rect
    if hasattr(raw_rect, "bbox"):
        box = getattr(raw_rect, "bbox")
        if box and len(box) >= 4:
            return fitz.Rect(float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    if isinstance(raw_rect, (list, tuple)) and len(raw_rect) >= 4:
        return fitz.Rect(float(raw_rect[0]), float(raw_rect[1]), float(raw_rect[2]), float(raw_rect[3]))
    return None


def extract_table_cells(page: fitz.Page) -> List[Dict]:
    if not hasattr(page, "find_tables"):
        return []
    try:
        table_finder = page.find_tables()
    except Exception:
        return []

    tables = getattr(table_finder, "tables", None) or []
    cells_out: List[Dict] = []
    for t_idx, table in enumerate(tables):
        # Preferred path: preserve row/col semantics when available.
        rows = getattr(table, "rows", None)
        if rows:
            for r_idx, row in enumerate(rows):
                row_cells = getattr(row, "cells", None) or []
                for c_idx, cell in enumerate(row_cells):
                    rect = _coerce_rect(cell)
                    if rect is None or rect.is_empty:
                        continue
                    cells_out.append(
                        {"table_id": t_idx, "row": r_idx, "col": c_idx, "rect": rect, "cell_id": f"t{t_idx}_r{r_idx}_c{c_idx}"}
                    )
            continue

        # Fallback path for versions exposing only flat cells.
        raw_cells = getattr(table, "cells", None) or []
        for c_idx, cell in enumerate(raw_cells):
            rect = _coerce_rect(cell)
            if rect is None or rect.is_empty:
                continue
            cells_out.append({"table_id": t_idx, "row": None, "col": c_idx, "rect": rect, "cell_id": f"t{t_idx}_c{c_idx}"})
    return cells_out


def _filter_noise_tables(cells: Sequence[Dict], cfg: Config) -> List[Dict]:
    if not cells:
        return []
    grouped: Dict[int, List[Dict]] = {}
    for cell in cells:
        grouped.setdefault(int(cell["table_id"]), []).append(cell)

    kept: List[Dict] = []
    for _, table_cells in grouped.items():
        cell_count = len(table_cells)
        xs0 = [c["rect"].x0 for c in table_cells]
        ys0 = [c["rect"].y0 for c in table_cells]
        xs1 = [c["rect"].x1 for c in table_cells]
        ys1 = [c["rect"].y1 for c in table_cells]
        table_bbox_width = max(xs1) - min(xs0)
        table_bbox_height = max(ys1) - min(ys0)
        table_bbox_area = max(0.0, (max(xs1) - min(xs0)) * (max(ys1) - min(ys0)))
        avg_cell_area = sum(max(0.0, c["rect"].width * c["rect"].height) for c in table_cells) / max(1, len(table_cells))
        row_count = len({c.get("row") for c in table_cells if c.get("row") is not None})

        # 过滤单行多列碎格伪表（页眉页脚/日期拆格等）
        # 但保留可能真实存在的 1x1 独立单元格
        if row_count <= 1 and cell_count > 1:
            continue
        # 单个单元格使用独立阈值，避免被大表阈值误杀
        if cell_count == 1:
            if (
                table_bbox_width < cfg.single_cell_min_width
                or table_bbox_height < cfg.single_cell_min_height
                or table_bbox_area < cfg.single_cell_min_area
            ):
                continue
            kept.extend(table_cells)
            continue
        # 过滤过窄伪表（例如文本断句被误识别为小网格）
        if table_bbox_width < cfg.table_min_bbox_width:
            continue
        # 去掉 t0/t2 这类“超小格子”的噪声表
        if avg_cell_area < cfg.table_min_avg_cell_area:
            continue
        if table_bbox_area < cfg.table_min_bbox_area:
            continue
        kept.extend(table_cells)
    return kept


def extract_vector_rect_cells(page: fitz.Page, page_no: int) -> List[Dict]:
    cells: List[Dict] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return cells

    idx = 1
    for d in drawings:
        rect = _coerce_rect(d.get("rect"))
        if rect is None or rect.is_empty:
            continue
        w, h = float(rect.width), float(rect.height)
        # 过滤过小噪声与整页背景框
        if w < 18 or h < 10:
            continue
        if w > page.rect.width * 0.95 and h > page.rect.height * 0.95:
            continue
        # 只保留描边路径（更像真实线框）
        if d.get("type") not in ("s", "fs", "sf"):
            continue
        cells.append(
            {
                "table_id": 8000 + page_no,
                "row": None,
                "col": idx,
                "rect": rect,
                "cell_id": f"vcell_p{page_no}_{idx}",
                "source": "vector_rect",
            }
        )
        idx += 1
    # Some PDFs draw a cell by 4 separate line segments.
    # Rebuild such rectangles so single large cells are not missed.
    line_rects = _rebuild_rectangles_from_lines(drawings)
    for rect in line_rects:
        w, h = float(rect.width), float(rect.height)
        if w < 18 or h < 10:
            continue
        cells.append(
            {
                "table_id": 8000 + page_no,
                "row": None,
                "col": idx,
                "rect": rect,
                "cell_id": f"vcell_p{page_no}_{idx}",
                "source": "vector_line_rect",
            }
        )
        idx += 1
    return cells


def _rebuild_rectangles_from_lines(drawings: Sequence[Dict], tol: float = 1.2) -> List[fitz.Rect]:
    horizontals: List[tuple] = []
    verticals: List[tuple] = []
    for d in drawings:
        for item in d.get("items", []):
            if not item or item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            x0, y0, x1, y1 = float(p0.x), float(p0.y), float(p1.x), float(p1.y)
            if abs(y1 - y0) <= tol and abs(x1 - x0) > 8:
                xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
                horizontals.append((xa, xb, (y0 + y1) / 2.0))
            elif abs(x1 - x0) <= tol and abs(y1 - y0) > 8:
                ya, yb = (y0, y1) if y0 <= y1 else (y1, y0)
                verticals.append(((x0 + x1) / 2.0, ya, yb))

    rects: List[fitz.Rect] = []
    seen = set()
    for i in range(len(horizontals)):
        x0a, x1a, y_top = horizontals[i]
        for j in range(i + 1, len(horizontals)):
            x0b, x1b, y_bot = horizontals[j]
            if abs(y_bot - y_top) < 10:
                continue
            if abs(x0a - x0b) > 2.0 or abs(x1a - x1b) > 2.0:
                continue
            left_x = None
            right_x = None
            ymin, ymax = (y_top, y_bot) if y_top <= y_bot else (y_bot, y_top)
            for vx, vy0, vy1 in verticals:
                if vy0 <= ymin + tol and vy1 >= ymax - tol:
                    if abs(vx - x0a) <= 2.0:
                        left_x = vx
                    if abs(vx - x1a) <= 2.0:
                        right_x = vx
            if left_x is None or right_x is None:
                continue
            key = (round(left_x, 1), round(ymin, 1), round(right_x, 1), round(ymax, 1))
            if key in seen:
                continue
            seen.add(key)
            rects.append(fitz.Rect(left_x, ymin, right_x, ymax))
    return rects


def _table_cells_to_json(cells: Sequence[Dict]) -> List[Dict]:
    out: List[Dict] = []
    for cell in cells:
        rect = cell["rect"]
        out.append(
            {
                "table_id": cell["table_id"],
                "row": cell["row"],
                "col": cell["col"],
                "cell_id": cell["cell_id"],
                "bbox": _rect_to_bbox(rect),
            }
        )
    return out


def _cell_contains(a: Dict, b: Dict, tolerance: float = 0.5) -> bool:
    ax0, ay0 = a["bbox"]["x"], a["bbox"]["y"]
    ax1, ay1 = ax0 + a["bbox"]["w"], ay0 + a["bbox"]["h"]
    bx0, by0 = b["bbox"]["x"], b["bbox"]["y"]
    bx1, by1 = bx0 + b["bbox"]["w"], by0 + b["bbox"]["h"]
    return ax0 <= bx0 + tolerance and ay0 <= by0 + tolerance and ax1 >= bx1 - tolerance and ay1 >= by1 - tolerance


def _cell_area(cell: Dict) -> float:
    return max(0.0, float(cell["bbox"]["w"])) * max(0.0, float(cell["bbox"]["h"]))


def _filter_contained_table_cells(cells: Sequence[Dict]) -> List[Dict]:
    if not cells:
        return []

    grouped: Dict[int, List[Dict]] = {}
    for cell in cells:
        grouped.setdefault(int(cell.get("table_id", -1)), []).append(cell)

    kept: List[Dict] = []
    for _, group_cells in grouped.items():
        # 先按面积从大到小排序：优先保留大单元格
        sorted_cells = sorted(group_cells, key=_cell_area, reverse=True)
        kept_in_table: List[Dict] = []
        for cell in sorted_cells:
            contained = any(_cell_contains(k, cell) for k in kept_in_table)
            if not contained:
                kept_in_table.append(cell)
        # 恢复阅读顺序，便于输出稳定
        kept_in_table.sort(key=lambda c: (c["bbox"]["y"], c["bbox"]["x"]))
        kept.extend(kept_in_table)

    kept.sort(key=lambda c: (c["table_id"], c["bbox"]["y"], c["bbox"]["x"]))
    return kept


def _rect_to_bbox(rect: fitz.Rect) -> Dict:
    return {"x": float(rect.x0), "y": float(rect.y0), "w": float(rect.width), "h": float(rect.height)}


def _color_int_to_rgb(color_int) -> List[float]:
    if color_int is None:
        return [0.0, 0.0, 0.0]
    try:
        val = int(color_int)
        r = ((val >> 16) & 255) / 255.0
        g = ((val >> 8) & 255) / 255.0
        b = (val & 255) / 255.0
        return [r, g, b]
    except Exception:
        return [0.0, 0.0, 0.0]


def extract_image_blocks(page: fitz.Page, page_no: int) -> List[Dict]:
    blocks: List[Dict] = []
    # image blocks from text dict usually carry bbox and block number
    try:
        text_dict = page.get_text("dict")
    except Exception:
        return blocks

    order = 0
    for blk in text_dict.get("blocks", []):
        if blk.get("type") != 1:
            continue
        bbox = blk.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
        if x1 <= x0 or y1 <= y0:
            continue
        order += 1
        b = _make_object_block(
            page_no=page_no,
            order=0,
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            obj_type="image",
            text="[IMAGE]",
        )
        b["image_meta"] = {
            "block_no": blk.get("number"),
            "width": blk.get("width"),
            "height": blk.get("height"),
            "ext": blk.get("ext"),
            "colorspace": blk.get("colorspace"),
            "xres": blk.get("xres"),
            "yres": blk.get("yres"),
        }
        blocks.append(b)
    return blocks


def extract_watermark_blocks(page: fitz.Page, page_no: int) -> List[Dict]:
    blocks: List[Dict] = []
    try:
        raw = page.get_text("rawdict")
    except Exception:
        return blocks

    page_cx = page.rect.width / 2.0
    page_cy = page.rect.height / 2.0
    wm_idx = 0

    for blk in raw.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            direction = line.get("dir", (1.0, 0.0))
            rotated = abs(direction[0]) < 0.97
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                bbox = span.get("bbox")
                size = float(span.get("size") or 0.0)
                if not text or not bbox or len(bbox) < 4:
                    continue
                x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
                if x1 <= x0 or y1 <= y0:
                    continue
                cx = (x0 + x1) / 2.0
                cy = (y0 + y1) / 2.0
                near_center = abs(cx - page_cx) <= page.rect.width * 0.35 and abs(cy - page_cy) <= page.rect.height * 0.35
                big_text = size >= 18.0 or (x1 - x0) >= page.rect.width * 0.25
                likely_wm = (rotated and big_text) or (near_center and big_text and len(text) >= 3)
                if not likely_wm:
                    continue
                wm_idx += 1
                b = _make_object_block(
                    page_no=page_no,
                    order=0,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    obj_type="watermark",
                    text=text,
                )
                b["watermark_meta"] = {"font": span.get("font"), "size": size, "dir": direction}
                blocks.append(b)
    return blocks


def extract_text_spans(page: fitz.Page) -> List[Dict]:
    spans_out: List[Dict] = []
    try:
        raw = page.get_text("rawdict")
    except Exception:
        return spans_out

    order = 1
    temp_spans: List[Dict] = []
    for blk in raw.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            direction = line.get("dir", (1.0, 0.0))
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    chars = span.get("chars") or []
                    text = "".join((ch.get("c") or "") for ch in chars).strip()
                bbox = span.get("bbox")
                if not text or not bbox or len(bbox) < 4:
                    continue
                x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
                if x1 <= x0 or y1 <= y0:
                    continue
                temp_spans.append(
                    {
                        "order": order,
                        "text": text,
                        "bbox": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                        "font": span.get("font"),
                        "size": float(span.get("size") or 0.0),
                        "flags": span.get("flags"),
                        "color": span.get("color"),
                        "color_rgb": _color_int_to_rgb(span.get("color")),
                        "is_bold": bool(int(span.get("flags") or 0) & 16)
                        or ("bold" in str(span.get("font") or "").lower()),
                        "is_italic": bool(int(span.get("flags") or 0) & 2),
                        "line_dir": [float(direction[0]), float(direction[1])],
                    }
                )
                order += 1

    # Detect synthetic bold rendered by repeated same text at near-identical location.
    for i, sp in enumerate(temp_spans):
        if sp["is_bold"]:
            continue
        b1 = sp["bbox"]
        x10, y10, x11, y11 = b1["x"], b1["y"], b1["x"] + b1["w"], b1["y"] + b1["h"]
        for j, other in enumerate(temp_spans):
            if i == j:
                continue
            if sp["text"] != other["text"]:
                continue
            if abs(float(sp["size"]) - float(other["size"])) > 0.3:
                continue
            b2 = other["bbox"]
            x20, y20, x21, y21 = b2["x"], b2["y"], b2["x"] + b2["w"], b2["y"] + b2["h"]
            inter_w = max(0.0, min(x11, x21) - max(x10, x20))
            inter_h = max(0.0, min(y11, y21) - max(y10, y20))
            inter = inter_w * inter_h
            if inter <= 0:
                continue
            area1 = max(1e-6, b1["w"] * b1["h"])
            area2 = max(1e-6, b2["w"] * b2["h"])
            iou = inter / (area1 + area2 - inter)
            if iou >= 0.88:
                sp["is_bold"] = True
                break

    spans_out.extend(temp_spans)
    return spans_out


def extract_vector_lines(page: fitz.Page) -> List[Dict]:
    out: List[Dict] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return out

    for d in drawings:
        stroke = d.get("color")
        width = float(d.get("width") or 0.6)
        for item in d.get("items", []):
            if not item or item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            out.append(
                {
                    "x0": float(p0.x),
                    "y0": float(p0.y),
                    "x1": float(p1.x),
                    "y1": float(p1.y),
                    "width": width,
                    "color": list(stroke) if isinstance(stroke, (list, tuple)) else [0.0, 0.0, 0.0],
                }
            )
    return out


def extract_image_payload(page: fitz.Page) -> List[Dict]:
    payload: List[Dict] = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        infos = []

    for info in infos:
        bbox = info.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
        if x1 <= x0 or y1 <= y0:
            continue
        xref = int(info.get("xref") or 0)
        img_b64 = None
        img_ext = "png"
        has_mask = bool(info.get("has-mask"))
        if xref > 0:
            try:
                doc = page.parent
                extracted = doc.extract_image(xref)
                raw_base = extracted.get("image")
                smask_xref = int(extracted.get("smask") or 0)

                if raw_base and smask_xref > 0:
                    # Compose RGBA manually to preserve transparency.
                    base_img = Image.open(io.BytesIO(raw_base)).convert("RGBA")
                    smask_raw = doc.extract_image(smask_xref).get("image")
                    if smask_raw:
                        alpha_img = Image.open(io.BytesIO(smask_raw)).convert("L")
                        if alpha_img.size != base_img.size:
                            alpha_img = alpha_img.resize(base_img.size)
                        base_img.putalpha(alpha_img)
                        buf = io.BytesIO()
                        base_img.save(buf, format="PNG")
                        img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                        img_ext = "png"
                if img_b64 is None:
                    # Fallback: use pixmap path (or raw image if pixmap fails)
                    try:
                        pix = fitz.Pixmap(doc, xref)
                        if pix.colorspace is not None and pix.colorspace.n not in (1, 3):
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        img_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
                        img_ext = "png"
                    except Exception:
                        if raw_base:
                            img_ext = extracted.get("ext") or "bin"
                            img_b64 = base64.b64encode(raw_base).decode("ascii")
            except Exception:
                img_b64 = None
                img_ext = "bin"
        payload.append(
            {
                "bbox": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                "width": info.get("width"),
                "height": info.get("height"),
                "ext": img_ext or "bin",
                "colorspace": info.get("colorspace"),
                "xref": xref,
                "has_mask": has_mask,
                "image_base64": img_b64,
            }
        )
    return payload


def _dynamic_gap_threshold(tokens: Sequence[Dict], fallback: float) -> float:
    if not tokens:
        return fallback
    heights = [max(0.1, t["y1"] - t["y0"]) for t in tokens]
    heights.sort()
    median_h = heights[len(heights) // 2]
    dyn = median_h * 0.35
    return max(3.0, min(12.0, max(fallback, dyn)))


def merge_tokens_to_blocks(tokens: Sequence[Dict], cfg: Config) -> List[Dict]:
    if not tokens:
        return []
    line_groups = _assign_lines(tokens, line_tolerance_px=cfg.line_tolerance_px)
    gap_threshold = _dynamic_gap_threshold(tokens, cfg.gap_threshold_px)

    blocks: List[Dict] = []
    order = 1
    for line in line_groups:
        current: List[Dict] = []
        for tk in line:
            if not current:
                current.append(tk)
                continue

            gap = tk["x0"] - current[-1]["x1"]
            if gap <= gap_threshold:
                current.append(tk)
            else:
                blocks.append(_make_block(current, page_no=tokens[0]["page"], order=order))
                order += 1
                current = [tk]

        if current:
            blocks.append(_make_block(current, page_no=tokens[0]["page"], order=order))
            order += 1

    # First release keeps paragraph merge closed by default.
    if cfg.merge_paragraph:
        pass
    return blocks


def _token_center(token: Dict) -> fitz.Point:
    return fitz.Point((token["x0"] + token["x1"]) / 2.0, (token["y0"] + token["y1"]) / 2.0)


def _token_in_rect(token: Dict, rect: fitz.Rect, tolerance: float = 1.0) -> bool:
    center = _token_center(token)
    expanded = fitz.Rect(rect.x0 - tolerance, rect.y0 - tolerance, rect.x1 + tolerance, rect.y1 + tolerance)
    return expanded.contains(center)


def merge_tokens_with_table_awareness(page: fitz.Page, tokens: Sequence[Dict], page_no: int, cfg: Config) -> List[Dict]:
    if not tokens:
        return []

    if not cfg.table_aware:
        return merge_tokens_to_blocks(tokens, cfg)

    table_cells = _filter_noise_tables(extract_table_cells(page), cfg) + extract_vector_rect_cells(page, page_no)
    if not table_cells:
        return merge_tokens_to_blocks(tokens, cfg)

    token_used = [False] * len(tokens)
    table_blocks: List[Dict] = []

    # 先把同单元格文本合并，避免跨列误并
    for cell in table_cells:
        grouped: List[Dict] = []
        for idx, tk in enumerate(tokens):
            if token_used[idx]:
                continue
            if _token_in_rect(tk, cell["rect"], tolerance=1.2):
                grouped.append(tk)
                token_used[idx] = True
        if grouped:
            grouped.sort(key=lambda t: ((t["y0"] + t["y1"]) / 2.0, t["x0"]))
            block = _make_block(grouped, page_no=page_no, order=0)
            block["table_cell"] = {
                "table_id": cell["table_id"],
                "row": cell["row"],
                "col": cell["col"],
                "cell_id": cell["cell_id"],
            }
            table_blocks.append(block)

    remaining = [tk for idx, tk in enumerate(tokens) if not token_used[idx]]
    normal_blocks = merge_tokens_to_blocks(remaining, cfg) if remaining else []

    # 合并后重排，保证阅读顺序和连续编号稳定
    all_blocks = table_blocks + normal_blocks
    all_blocks.sort(key=lambda b: (b["bbox"]["y"], b["bbox"]["x"]))
    for i, block in enumerate(all_blocks, start=1):
        block["order"] = i
        block["id"] = f"p{page_no}_b{i}"
    return all_blocks


def _overlap_ratio_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    den = max(1e-6, min(a1 - a0, b1 - b0))
    return inter / den


def infer_synthetic_cells_from_text_blocks(page_no: int, text_blocks: Sequence[Dict]) -> List[Dict]:
    candidates = [b for b in text_blocks if b.get("type") == "text" and "table_cell" not in b]
    candidates.sort(key=lambda b: (b["bbox"]["y"], b["bbox"]["x"]))
    if len(candidates) < 3:
        return []

    synthetic: List[Dict] = []
    group: List[Dict] = []
    sc_idx = 1

    def flush_group() -> None:
        nonlocal sc_idx
        if len(group) < 3:
            return
        x0 = min(b["bbox"]["x"] for b in group)
        y0 = min(b["bbox"]["y"] for b in group)
        x1 = max(b["bbox"]["x"] + b["bbox"]["w"] for b in group)
        y1 = max(b["bbox"]["y"] + b["bbox"]["h"] for b in group)
        if (x1 - x0) < 180 or (y1 - y0) < 35:
            return
        synthetic.append(
            {
                "table_id": 9000 + page_no,
                "row": 0,
                "col": sc_idx,
                "cell_id": f"scell_p{page_no}_{sc_idx}",
                "source": "synthetic",
                "bbox": {"x": x0 - 4, "y": y0 - 3, "w": (x1 - x0) + 8, "h": (y1 - y0) + 6},
            }
        )
        sc_idx += 1

    for blk in candidates:
        if not group:
            group = [blk]
            continue
        prev = group[-1]
        px0, pw = prev["bbox"]["x"], prev["bbox"]["w"]
        px1 = px0 + pw
        bx0, bw = blk["bbox"]["x"], blk["bbox"]["w"]
        bx1 = bx0 + bw
        ov = _overlap_ratio_1d(px0, px1, bx0, bx1)
        y_gap = blk["bbox"]["y"] - (prev["bbox"]["y"] + prev["bbox"]["h"])
        left_close = abs(bx0 - px0) <= 30
        # 左边可轻微缩进，右边有重叠，行距连续 => 视为同一框内多行
        if (left_close or ov >= 0.55) and -2 <= y_gap <= 24:
            group.append(blk)
        else:
            flush_group()
            group = [blk]
    flush_group()
    return synthetic


def _intersects_bbox(a: Dict, b: Dict, tolerance: float = 1.0) -> bool:
    ax0, ay0 = a["x"], a["y"]
    ax1, ay1 = ax0 + a["w"], ay0 + a["h"]
    bx0, by0 = b["x"], b["y"]
    bx1, by1 = bx0 + b["w"], by0 + b["h"]
    return not (ax1 < bx0 - tolerance or bx1 < ax0 - tolerance or ay1 < by0 - tolerance or by1 < ay0 - tolerance)


def _merge_page_blocks(page_no: int, text_blocks: List[Dict], image_blocks: List[Dict], watermark_blocks: List[Dict]) -> List[Dict]:
    filtered_watermarks: List[Dict] = []
    for wm in watermark_blocks:
        wm_bbox = wm["bbox"]
        # 避免把正常大标题误判成水印：和文本块重叠比例高时跳过
        overlap_with_text = any(_intersects_bbox(wm_bbox, tb["bbox"], tolerance=0.5) for tb in text_blocks)
        if not overlap_with_text:
            filtered_watermarks.append(wm)

    all_blocks = text_blocks + image_blocks + filtered_watermarks
    all_blocks.sort(key=lambda b: (b["bbox"]["y"], b["bbox"]["x"]))
    for i, block in enumerate(all_blocks, start=1):
        block["order"] = i
        block["id"] = f"p{page_no}_b{i}"
        block.setdefault("type", "text")
    return all_blocks


def is_span_red_rgb(color_rgb: Sequence[float]) -> bool:
    """
    判断 PDF 文本是否为红色（与常见表单中“待填写红字”一致）。
    RGB 为 0~1；兼容深红/浅红。
    """
    if not color_rgb or len(color_rgb) < 3:
        return False
    r, g, b = float(color_rgb[0]), float(color_rgb[1]), float(color_rgb[2])
    return r >= 0.45 and g <= 0.42 and b <= 0.42


def _normalize_cell_text(text: str) -> str:
    return "".join(str(text or "").split()).strip()


def _normalize_common_label_noise(text: str) -> str:
    """
    纠正常见 PDF 断字/串字导致的列名噪声。
    例如：SN/设备编号(序/列号产品编号） -> 设备编号(SN/序列号/产品编号)
    """
    t = str(text or "")
    if not t:
        return ""
    s = t.replace("（", "(").replace("）", ")")
    if ("设备编号" in s and "SN" in s and "序" in s and "列号" in s and "产品编号" in s):
        return "设备编号(SN/序列号/产品编号)"
    # 委托单位联系人/电话 常见断字与前导斜杠污染
    if ("委托单位" in s and "联系人" in s and "电话" in s):
        return "委托单位联系人/电话"
    # 环境温度/湿度 常见丢分隔符
    if ("环境" in s and "温度" in s and "湿度" in s):
        return "环境温度/湿度"
    # 兜底：避免中文标签出现异常前导 /
    if s.startswith("/") and re.search(r"[\u4e00-\u9fff]", s):
        return s.lstrip("/")
    return t


def _sanitize_name_piece(text: str) -> str:
    raw = _normalize_cell_text(text)
    if not raw:
        return ""
    keep = []
    for ch in raw:
        if ch.isalnum() or ch in ("_", "-", "（", "）", "(", ")", "/", "·", "."):
            keep.append(ch)
    out = "".join(keep).strip("_")
    out = _normalize_common_label_noise(out or raw)
    return out or raw


# 辐射/常用计量单位（长匹配优先），用于 □ 旁标签识别
_RADIO_UNIT_LABELS: tuple = (
    "μGy/min",
    "μGy/s",
    "mGy/min",
    "mGy/s",
    "μGy",
    "mGy",
    "μSv",
    "mSv",
    "μSv/h",
)


def _normalize_unit_mu(s: str) -> str:
    return (
        str(s or "")
        .replace("\u00b5", "μ")  # PDF 中常见 micro sign 与希腊字母 μ 混用
        .replace("uGy/", "μGy/")
        .replace("uGy", "μGy")
        .replace("uSv/", "μSv/")
        .replace("uSv", "μSv")
    )


def _extract_checkbox_unit_label(span_text: str, char_idx: int) -> str:
    """
    从包含 □ 的 span 中取出紧邻的计量单位（如 μGy/s、μGy/min、mGy/min）。
    优先 □ 右侧，其次 □ 左侧末尾。
    """
    t = str(span_text or "")
    if char_idx < 0 or char_idx >= len(t):
        return ""
    right = _normalize_unit_mu(t[char_idx + 1 :].lstrip(" :：，,\t"))
    for lab in _RADIO_UNIT_LABELS:
        if right.startswith(lab):
            return lab
    left = _normalize_unit_mu(t[:char_idx].rstrip(" :：，,\t"))
    for lab in _RADIO_UNIT_LABELS:
        if left.endswith(lab):
            return lab
    return ""


def _label_is_radiation_unit(label: str) -> bool:
    s = _normalize_unit_mu(_sanitize_name_piece(label) or label)
    if not s:
        return False
    for lab in _RADIO_UNIT_LABELS:
        if s == lab or s.startswith(lab) or lab in s:
            return True
    return False


def _detect_binary_pair_option(a: str, b: str) -> Optional[Dict[str, Any]]:
    """有/无、是/否、自动/手动 成对互斥。"""
    sa, sb = (a or "").strip(), (b or "").strip()
    if not sa or not sb or sa == sb:
        return None
    rules = (
        ("有/无", ("有", "无")),
        ("是/否", ("是", "否")),
        ("自动/手动", ("自动", "手动")),
    )
    for kind, (x, y) in rules:
        if {sa, sb} == {x, y}:
            return {"kind": kind, "options": (x, y)}
    return None


def _canonical_radiation_unit_label(u: str) -> str:
    s = _normalize_unit_mu(_sanitize_name_piece(u) or u)
    if not s:
        return ""
    for lab in _RADIO_UNIT_LABELS:
        if s == lab or s.startswith(lab):
            return lab
    return s


def _best_radiation_unit_token(u: str) -> str:
    """
    从一段合并文本中取出最可能的剂量/剂量率单位（兼容 □ 与单位不同 span、前后有杂字）。
    """
    if not u:
        return ""
    t = _normalize_unit_mu(_sanitize_name_piece(u) or u)
    if not t:
        return ""
    for lab in _RADIO_UNIT_LABELS:
        if t == lab or t.startswith(lab):
            return lab
    best = ""
    for lab in _RADIO_UNIT_LABELS:
        if lab in t and len(lab) > len(best):
            best = lab
    return best


def _checkbox_option_labels_from_cell_full(cell_full: str) -> List[str]:
    """
    从单元格合并文本中按 □/☐/▢ 切分，得到每个勾选旁选项名（左→右、上→下与 span 拼接顺序一致）。
    解决「□ 与 DSA设备 不在同一 text span」时 own_name 为空的问题。
    """
    s = _normalize_cell_text(cell_full)
    if not s:
        return []
    parts = re.split(r"[□☐▢]+", s)
    out: List[str] = []
    for p in parts:
        t = _sanitize_name_piece(p)
        if t:
            out.append(t)
    return out


def _rect_area(rect: fitz.Rect) -> float:
    return max(0.0, float(rect.width)) * max(0.0, float(rect.height))


def _pick_best_cell_for_span(span_bbox: Dict[str, float], cells: Sequence[Dict]) -> Optional[Dict]:
    cx = float(span_bbox["x"]) + float(span_bbox["w"]) / 2.0
    cy = float(span_bbox["y"]) + float(span_bbox["h"]) / 2.0
    pt = fitz.Point(cx, cy)
    hits = [c for c in cells if c["rect"].contains(pt)]
    if not hits:
        return None
    hits.sort(key=lambda c: _rect_area(c["rect"]))
    return hits[0]


def _build_structured_cell_maps(page: fitz.Page, cells: Sequence[Dict], spans: Sequence[Dict]) -> Dict[str, Any]:
    structured = [c for c in cells if c.get("row") is not None and c.get("col") is not None]
    cell_texts: Dict[tuple, str] = {}
    cell_spans: Dict[tuple, List[Dict[str, Any]]] = {}
    table_cells: Dict[int, Dict[tuple, Dict]] = {}
    for cell in structured:
        table_id = int(cell["table_id"])
        key = (int(cell["row"]), int(cell["col"]))
        table_cells.setdefault(table_id, {})[key] = cell
        cell_texts[(table_id, key[0], key[1])] = ""
        cell_spans[(table_id, key[0], key[1])] = []

    for sp in spans:
        bb = sp.get("bbox") or {}
        if not bb:
            continue
        cx = float(bb["x"]) + float(bb["w"]) / 2.0
        cy = float(bb["y"]) + float(bb["h"]) / 2.0
        pt = fitz.Point(cx, cy)
        for table_id, cells_by_rc in table_cells.items():
            for (row, col), cell in cells_by_rc.items():
                if cell["rect"].contains(pt):
                    piece = str(sp.get("text") or "")
                    cell_spans[(table_id, row, col)].append(
                        {
                            "text": piece,
                            "x": float(bb["x"]),
                            "y": float(bb["y"]),
                            "color_rgb": sp.get("color_rgb") or [0.0, 0.0, 0.0],
                        }
                    )
                    break

    for key, slist in cell_spans.items():
        slist.sort(key=lambda s: (float(s["y"]), float(s["x"])))
        cell_texts[key] = "".join(str(s.get("text") or "") for s in slist)

    cell_black_prefix_texts: Dict[tuple, str] = {}
    for key, spans_in_cell in cell_spans.items():
        spans_in_cell.sort(key=lambda s: (float(s["y"]), float(s["x"])))
        blacks: List[str] = []
        for sp in spans_in_cell:
            txt = str(sp.get("text") or "")
            if not txt:
                continue
            if is_span_red_rgb(sp.get("color_rgb") or []):
                break
            blacks.append(txt)
        cell_black_prefix_texts[key] = _sanitize_name_piece("".join(blacks))

    return {"table_cells": table_cells, "cell_texts": cell_texts, "cell_black_prefix_texts": cell_black_prefix_texts}


def _infer_table_header_map(table_id: int, cells_by_rc: Dict[tuple, Dict], cell_texts: Dict[tuple, str]) -> Dict[str, int]:
    header_keywords = ["序号", "检测项目", "检测条件", "检测结果", "计算结果", "报出值", "判定标准", "验收", "状态", "单项判定"]
    by_row: Dict[int, List[tuple]] = {}
    for (row, col), _ in cells_by_rc.items():
        by_row.setdefault(int(row), []).append((int(row), int(col)))
    if not by_row:
        return {}

    sorted_rows = sorted(by_row.keys())
    # 合并前 3 行同列文本，识别双行/拆行表头（如「计算」「结果」分两行、验收/状态单独一行）
    merge_rows = sorted_rows[: min(3, len(sorted_rows))]
    cols_seen: set = set()
    for r in merge_rows:
        for _, c in by_row.get(r, []):
            cols_seen.add(int(c))
    merged_by_col: Dict[int, str] = {}
    for c in sorted(cols_seen):
        parts: List[str] = []
        for r in merge_rows:
            piece = _normalize_cell_text(cell_texts.get((table_id, r, c), ""))
            if piece:
                parts.append(piece)
        merged_by_col[c] = "".join(parts)

    mapping: Dict[str, int] = {}
    for col in sorted(merged_by_col.keys()):
        txt = merged_by_col[col]
        if not txt:
            continue
        for k in header_keywords:
            if k in txt and k not in mapping:
                mapping[k] = int(col)
    return mapping


def _get_item_name_for_row(table_id: int, row: int, item_col: int, cell_texts: Dict[tuple, str]) -> str:
    txt = _sanitize_name_piece(cell_texts.get((table_id, row, item_col), ""))
    if txt:
        return txt
    r = row - 1
    while r >= 0:
        t = _sanitize_name_piece(cell_texts.get((table_id, r, item_col), ""))
        if t:
            return t
        r -= 1
    return ""


def _checkbox_semantic_base_prefix(
    table_id: int,
    row: int,
    col: int,
    header_map: Dict[str, int],
    cell_texts: Dict[tuple, str],
    cell_black_prefix_texts: Dict[tuple, str],
    cells_by_rc: Dict[tuple, Dict],
    left_txt: str,
) -> str:
    """
    成对勾选 / 辐射单位等同列多框的语义前缀：检测项目行名 + 中间列补充，否则左侧单元格。
    """
    item_col = header_map.get("检测项目")
    red_pos = {(table_id, row, col)}
    if item_col is not None:
        item_name = _get_item_name_for_row(table_id, row, int(item_col), cell_texts) or "检测项目"
        supplement = _get_row_prefix_text(
            table_id=table_id,
            row=row,
            item_col=int(item_col),
            target_col=int(col),
            header_map=header_map,
            cell_texts=cell_texts,
            cell_black_prefix_texts=cell_black_prefix_texts,
            cells_by_rc=cells_by_rc,
            red_cell_pos_set=red_pos,
        )
        return f"{item_name}_{supplement}" if supplement else item_name
    lp = str(left_txt or "").strip()
    if lp:
        return lp
    return "剂量"


def _get_row_prefix_text(
    table_id: int,
    row: int,
    item_col: int,
    target_col: int,
    header_map: Dict[str, int],
    cell_texts: Dict[tuple, str],
    cell_black_prefix_texts: Dict[tuple, str],
    cells_by_rc: Dict[tuple, Dict],
    red_cell_pos_set: set,
) -> str:
    """
    前缀补充文本来源：从“检测项目列”右侧开始，且不能超过当前目标列。
    如果该列是“判定标准”，则跳过，继续向右找最近非空文本。
    """
    cols = sorted([int(col) for (_, col) in cells_by_rc.keys()])
    if not cols:
        return ""
    std_col = header_map.get("判定标准")
    for c in cols:
        if c <= int(item_col):
            continue
        if c >= int(target_col):
            continue
        if std_col is not None and int(c) == int(std_col):
            continue
        if (int(table_id), int(row), int(c)) in red_cell_pos_set:
            continue
        txt = _sanitize_name_piece(cell_black_prefix_texts.get((table_id, row, c), ""))
        if not txt:
            txt = _sanitize_name_piece(cell_texts.get((table_id, row, c), ""))
        if txt:
            return txt
    return ""


def _resolve_semantic_type_by_col(col: int, header_map: Dict[str, int]) -> Optional[str]:
    """
    连续列规则：
    若表头列为 c2(检测条件), c5(检测结果)，则 c2,c3,c4 均视为检测条件。
    """
    checkpoints: List[tuple] = []
    # “计算结果/报出值”按“检测结果”语义处理
    result_anchor = header_map.get("检测结果")
    if result_anchor is None:
        result_anchor = header_map.get("计算结果")
    if result_anchor is None:
        result_anchor = header_map.get("报出值")
    verdict_anchor = header_map.get("判定标准")
    if verdict_anchor is None:
        verdict_anchor = header_map.get("验收")
    if verdict_anchor is None:
        verdict_anchor = header_map.get("状态")
    normalized_map = {
        "检测条件": header_map.get("检测条件"),
        "检测结果": result_anchor,
        "判定标准": verdict_anchor,
        "单项判定": header_map.get("单项判定"),
    }
    for key in ["检测条件", "检测结果", "判定标准", "单项判定"]:
        c = normalized_map.get(key)
        if c is not None:
            checkpoints.append((int(c), key))
    if not checkpoints:
        return None
    checkpoints.sort(key=lambda x: x[0])
    for idx, (start_col, key) in enumerate(checkpoints):
        end_col = checkpoints[idx + 1][0] - 1 if idx + 1 < len(checkpoints) else 10**9
        if int(col) < start_col or int(col) > end_col:
            continue
        if key == "检测条件":
            return "检测条件"
        if key == "检测结果":
            return "检测结果"
        if key == "判定标准":
            return "判定标准"
        if key == "单项判定":
            return "单项判定"
        return None
    return None


def _column_header_keyword(col: int, header_map: Dict[str, int]) -> Optional[str]:
    """
    当前列与表头映射中哪一列完全一致（优先于语义区间内的笼统类型名）。
    用于命名中区分「计算结果 / 报出值 / 验收 / 状态」等同级不同列表头。
    """
    c = int(col)
    for k in (
        "计算结果",
        "报出值",
        "检测结果",
        "判定标准",
        "验收",
        "状态",
        "检测条件",
        "单项判定",
    ):
        hc = header_map.get(k)
        if hc is not None and int(hc) == c:
            return k
    return None


def _extract_unit_suffix(text: str) -> str:
    if not text:
        return ""
    t = _normalize_unit_mu(str(text).strip())
    t = t.replace("uGy/min", "μGy/min")
    m = re.search(r"(μGy/min|μGy/s|mGy/min|mGy/s|μGy|mGy|kV|mA|%|cm|mm)\b", t)
    return m.group(1) if m else ""


def _build_underline_boxes_from_spans(spans: Sequence[Dict[str, Any]], page_no: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sp in spans:
        text = str(sp.get("text") or "")
        if not text or "_" not in text:
            continue
        bb = sp.get("bbox") or {}
        if not bb:
            continue
        x0 = float(bb["x"])
        y0 = float(bb["y"])
        w = float(bb["w"])
        h = float(bb["h"])
        if w <= 0 or h <= 0:
            continue
        chars = list(text)
        char_w = w / max(1, len(chars))
        for m in re.finditer(r"_{2,}", text):
            s0, s1 = m.start(), m.end()
            bx0 = x0 + s0 * char_w
            bx1 = x0 + s1 * char_w
            if bx1 <= bx0:
                continue
            before = _sanitize_name_piece(text[:s0])
            after = _sanitize_name_piece(text[s1:])
            out.append(
                {
                    "page": page_no,
                    "x": float(bx0),
                    "y": float(y0),
                    "w": float(bx1 - bx0),
                    "h": float(h),
                    "text": text[s0:s1],
                    "fieldType": "text",
                    "tail_name": before or after,
                    "unit": _extract_unit_suffix(text[s1:]),
                }
            )
    return out


def _pick_checkbox_item_text(
    spans: Sequence[Dict[str, Any]],
    cur_span: Dict[str, Any],
    char_idx: int,
    symbol_chars: set,
) -> str:
    text = str(cur_span.get("text") or "")
    if text:
        unit = _extract_checkbox_unit_label(text, char_idx)
        if unit:
            return _sanitize_name_piece(unit) or unit
        left = text[:char_idx].strip(" :：,，;；/\\|")
        right = text[char_idx + 1 :].strip(" :：,，;；/\\|")
        left = "".join(ch for ch in left if ch not in symbol_chars).strip()
        right = "".join(ch for ch in right if ch not in symbol_chars).strip()
        candidate = left or right
        if candidate:
            return _sanitize_name_piece(candidate)
    return ""


def _left_cell_plain_text(
    table_id: int,
    row: int,
    col: int,
    cell_texts: Dict[tuple, str],
    cell_black_prefix_texts: Dict[tuple, str],
) -> str:
    if col <= 0:
        return ""
    lc = col - 1
    t = _sanitize_name_piece(cell_black_prefix_texts.get((table_id, row, lc), ""))
    if not t:
        t = _sanitize_name_piece(cell_texts.get((table_id, row, lc), ""))
    return t


def _finalize_checkbox_names(
    raw_items: List[Dict[str, Any]],
    cell_texts: Dict[tuple, str],
    cell_black_prefix_texts: Dict[tuple, str],
    table_cells_by_rc: Dict[int, Dict[tuple, Dict]],
    header_map_by_table: Dict[int, Dict[str, int]],
) -> None:
    """
    委托单位右侧双框、检测仪器序号、检测类型/受检设备类型前缀、表头语义命名、左侧单元格兜底。
    """
    by_cell: Dict[tuple, List[Dict[str, Any]]] = {}
    for it in raw_items:
        tid = it.get("table_id")
        r, c = it.get("row"), it.get("col")
        if tid is None or r is None or c is None:
            by_cell.setdefault((-1, -1, -1), []).append(it)
        else:
            by_cell.setdefault((int(tid), int(r), int(c)), []).append(it)

    for key, items in by_cell.items():
        if key == (-1, -1, -1):
            for it in items:
                it["name"] = it.get("own_name") or "勾选"
            continue
        table_id, row, col = key
        items.sort(key=lambda x: (float(x["y"]), float(x["x"])))
        left_txt = _left_cell_plain_text(table_id, row, col, cell_texts, cell_black_prefix_texts)
        cell_full = str(items[0].get("cell_full") or "")
        header_map = header_map_by_table.get(table_id) or {}
        cells_by_rc = table_cells_by_rc.get(table_id) or {}

        # 委托单位：左侧为「委托单位」且本格恰好两个 □
        if "委托单位" in left_txt and len(items) == 2:
            items[0]["name"] = "委托单位_同受检单位"
            items[1]["name"] = "委托单位_委托单位"
            continue

        # 检测仪器：左侧为「检测仪器」，自上而下、从左到右 仪器1…
        if "检测仪器" in left_txt:
            for idx, it in enumerate(items, start=1):
                it["name"] = f"检测仪器_仪器{idx}"
            continue

        # 受检设备类型：左侧表头，子项为 □ 旁文本（整格按 □ 切分，避免 □ 与「DSA设备」分属不同 span 时丢名）
        if "受检设备类型" in left_txt:
            labels = _checkbox_option_labels_from_cell_full(cell_full)
            if labels and len(labels) >= len(items):
                for it, lab in zip(items, labels):
                    it["name"] = f"受检设备类型_{lab}"
            else:
                for idx, it in enumerate(items):
                    own = str(it.get("own_name") or "").strip()
                    if not own and idx < len(labels):
                        own = labels[idx]
                    it["name"] = f"受检设备类型_{own}" if own else "受检设备类型"
            continue

        # 成对互斥：有/无、是/否、自动/手动（命名统一前缀 + 后缀匹配校验）
        if len(items) == 2:
            oa = str(items[0].get("own_name") or "").strip()
            ob = str(items[1].get("own_name") or "").strip()
            pair = _detect_binary_pair_option(oa, ob)
            dup_mismatch = False
            for opts, kind in (
                ({"有", "无"}, "有/无"),
                ({"是", "否"}, "是/否"),
                ({"自动", "手动"}, "自动/手动"),
            ):
                if oa in opts and ob in opts and oa == ob:
                    dup_mismatch = True
                    for it in items:
                        it["checkboxPair"] = {
                            "mode": "mutually_exclusive",
                            "pairKind": kind,
                            "matchOk": False,
                            "reason": "duplicate_option",
                        }
                    break
            if pair and not dup_mismatch:
                base = _checkbox_semantic_base_prefix(
                    table_id, row, col, header_map, cell_texts, cell_black_prefix_texts, cells_by_rc, left_txt
                )
                pair_id = f"t{table_id}_r{row}_c{col}"
                exp = list(pair["options"])
                for it in items:
                    opt_label = str(it.get("own_name") or "").strip()
                    it["name"] = f"{base}_{opt_label}"
                    it["checkboxPair"] = {
                        "mode": "mutually_exclusive",
                        "pairId": pair_id,
                        "pairKind": pair["kind"],
                        "expected": exp,
                        "option": opt_label,
                        "matchOk": True,
                    }
                continue

        # 检测类型：□ 同格多选项时，不得用整格 cell_full 判词——否则「状态检测」会命中每一个 □。
        # 优先按 □ 切分后的片段逐框对应；否则仅用本框 own_name + span_text（不含 cell_full）。
        type_piece_labels = _checkbox_option_labels_from_cell_full(cell_full)
        if len(items) >= 2 and len(type_piece_labels) >= len(items):
            for it, lab in zip(items, type_piece_labels):
                lab_s = str(lab)
                if "验收检测" in lab_s:
                    it["name"] = "检测类型_验收检测"
                elif "状态检测" in lab_s:
                    it["name"] = "检测类型_状态检测"
        else:
            for it in items:
                own = str(it.get("own_name") or "")
                span_t = str(it.get("span_text") or "")
                frag = own + span_t
                if "验收检测" in frag:
                    it["name"] = "检测类型_验收检测"
                elif "状态检测" in frag:
                    it["name"] = "检测类型_状态检测"

        # 辐射剂量等单位：同一格多个 □ 仅单位不同（μGy/s、μGy/min、mGy/min 等）
        # 整格按 □ 切分补全单位（□ 与单位常分属不同 span）
        if len(items) >= 2:
            cell_unit_pieces = _checkbox_option_labels_from_cell_full(cell_full)
            effective_units: List[str] = []
            for i, it in enumerate(items):
                u = str(it.get("option_label") or "").strip()
                if not u:
                    u = str(it.get("own_name") or "").strip()
                if not u and i < len(cell_unit_pieces):
                    u = cell_unit_pieces[i]
                token = _best_radiation_unit_token(u)
                if not token and i < len(cell_unit_pieces):
                    token = _best_radiation_unit_token(cell_unit_pieces[i])
                effective_units.append(token)
            if effective_units and all(_label_is_radiation_unit(x) for x in effective_units):
                base = _checkbox_semantic_base_prefix(
                    table_id, row, col, header_map, cell_texts, cell_black_prefix_texts, cells_by_rc, left_txt
                )
                for it, canon in zip(items, effective_units):
                    it["name"] = f"{base}({canon})"
                continue

        # 表头语义（检测项目等）+ 前缀补充；未命中则用左侧单元格作前缀
        item_col = header_map.get("检测项目")
        red_pos = {(table_id, row, col)}
        for it in items:
            if str(it.get("name") or "").strip():
                continue
            own_name = str(it.get("own_name") or "")
            name = ""
            if item_col is not None:
                type_name = _resolve_semantic_type_by_col(int(col), header_map)
                if type_name:
                    role_label = _column_header_keyword(int(col), header_map) or type_name
                    item_name = _get_item_name_for_row(table_id, row, int(item_col), cell_texts) or "检测项目"
                    supplement = _get_row_prefix_text(
                        table_id=table_id,
                        row=row,
                        item_col=int(item_col),
                        target_col=int(col),
                        header_map=header_map,
                        cell_texts=cell_texts,
                        cell_black_prefix_texts=cell_black_prefix_texts,
                        cells_by_rc=table_cells_by_rc.get(table_id) or {},
                        red_cell_pos_set=red_pos,
                    )
                    prefix = f"{item_name}_{supplement}" if supplement else item_name
                    name = f"{prefix}_{own_name}" if own_name else f"{prefix}_{role_label}"
                else:
                    lp = _left_cell_plain_text(table_id, row, col, cell_texts, cell_black_prefix_texts)
                    if lp and own_name:
                        name = f"{lp}_{own_name}"
                    elif lp:
                        name = lp
                    else:
                        name = own_name or "勾选"
            else:
                lp = _left_cell_plain_text(table_id, row, col, cell_texts, cell_black_prefix_texts)
                if lp and own_name:
                    name = f"{lp}_{own_name}"
                elif lp:
                    name = lp
                else:
                    name = own_name or "勾选"
            it["name"] = name


def extract_checkbox_symbol_boxes(pdf_path: str) -> List[Dict[str, Any]]:
    """
    自动识别空方框符号（□/☐/▢），用于生成勾选框。
    返回 [{"page","x","y","w","h","text","fieldType"}, ...]
    """
    symbols = {"□", "☐", "▢"}
    doc = fitz.open(pdf_path)
    raw_items: List[Dict[str, Any]] = []
    cfg = Config()
    try:
        for i in range(len(doc)):
            page = doc[i]
            page_no = i + 1
            spans = extract_text_spans(page)
            table_cells = _filter_noise_tables(extract_table_cells(page), cfg) + extract_vector_rect_cells(page, page_no)
            maps = _build_structured_cell_maps(page, table_cells, spans)
            table_cells_by_rc: Dict[int, Dict[tuple, Dict]] = maps["table_cells"]
            cell_texts: Dict[tuple, str] = maps["cell_texts"]
            cell_black_prefix_texts: Dict[tuple, str] = maps.get("cell_black_prefix_texts") or {}
            header_map_by_table: Dict[int, Dict[str, int]] = {}
            for t_id, cells_by_rc in table_cells_by_rc.items():
                header_map_by_table[t_id] = _infer_table_header_map(t_id, cells_by_rc, cell_texts)

            page_raw: List[Dict[str, Any]] = []
            for sp in spans:
                text = str(sp.get("text") or "")
                if not text:
                    continue
                if not any(ch in symbols for ch in text):
                    continue
                bb = sp.get("bbox") or {}
                if not bb:
                    continue
                x0 = float(bb["x"])
                y0 = float(bb["y"])
                w = float(bb["w"])
                h = float(bb["h"])
                if w <= 0 or h <= 0:
                    continue
                chars = list(text)
                char_w = w / max(1, len(chars))
                for idx, ch in enumerate(chars):
                    if ch not in symbols:
                        continue
                    cx0 = x0 + idx * char_w
                    cx1 = cx0 + char_w
                    cy0 = y0
                    cy1 = y0 + h
                    side = max(8.0, min(cx1 - cx0, cy1 - cy0))
                    cxc = (cx0 + cx1) / 2.0
                    cyc = (cy0 + cy1) / 2.0
                    bx = cxc - side / 2.0
                    by = cyc - side / 2.0
                    own_name = _pick_checkbox_item_text(spans, sp, idx, symbols)
                    option_label = _extract_checkbox_unit_label(text, idx)
                    cell = _pick_best_cell_for_span({"x": bx, "y": by, "w": side, "h": side}, table_cells)
                    table_id = row = col = None
                    cell_full = ""
                    if cell is not None and cell.get("row") is not None and cell.get("col") is not None:
                        table_id = int(cell["table_id"])
                        row = int(cell["row"])
                        col = int(cell["col"])
                        cell_full = _normalize_cell_text(cell_texts.get((table_id, row, col), ""))
                    page_raw.append(
                        {
                            "page": page_no,
                            "x": float(bx),
                            "y": float(by),
                            "w": float(side),
                            "h": float(side),
                            "text": ch,
                            "own_name": own_name,
                            "option_label": option_label,
                            "span_text": text,
                            "table_id": table_id,
                            "row": row,
                            "col": col,
                            "cell_full": cell_full,
                            "fieldType": "check",
                        }
                    )
            _finalize_checkbox_names(
                page_raw,
                cell_texts,
                cell_black_prefix_texts,
                table_cells_by_rc,
                header_map_by_table,
            )
            for it in page_raw:
                rec: Dict[str, Any] = {
                    "page": it["page"],
                    "x": it["x"],
                    "y": it["y"],
                    "w": it["w"],
                    "h": it["h"],
                    "text": it["text"],
                    "name": it.get("name") or "勾选",
                    "fieldType": "check",
                }
                cp = it.get("checkboxPair")
                if isinstance(cp, dict) and cp:
                    rec["checkboxPair"] = cp
                raw_items.append(rec)
    finally:
        doc.close()
    return raw_items


def extract_signature_image_boxes(pdf_path: str) -> List[Dict[str, Any]]:
    """
    自动识别签名标签文本，并在其右侧生成图片框。
    规则：
    - 图片框不与标签重叠（x 从标签右侧开始）
    - 上端与标签文本框上端对齐
    - 高度统一 35
    - 宽度：检测员 225；受检单位陪同人 85；校核员及校核日期 125
    """
    targets = [
        {
            "name": "检测员",
            "aliases": ["检测员", "检测员：", "检测员:"],
            "w": 225.0,
            "h": 40.0,
        },
        {
            "name": "受检单位陪同人",
            "aliases": ["受检单位陪同人", "受检单位陪同人：", "受检单位陪同人:"],
            "w": 83.0,
            "h": 40.0,
        },
        {
            "name": "校核员及校核日期",
            "aliases": ["校核员及校核日期", "校核员及校核日期：", "校核员及校核日期:"],
            "w": 125.0,
            "h": 40.0,
        },
    ]
    doc = fitz.open(pdf_path)
    out: List[Dict[str, Any]] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            page_no = i + 1
            page_w = float(page.rect.width)
            page_h = float(page.rect.height)
            spans = extract_text_spans(page)
            for item in targets:
                hit = None
                for sp in spans:
                    txt = _normalize_cell_text(sp.get("text") or "")
                    if not txt:
                        continue
                    if any(alias in txt for alias in item["aliases"]):
                        hit = sp
                        break
                if hit is None:
                    continue
                bb = hit.get("bbox") or {}
                if not bb:
                    continue
                label_right = float(bb["x"]) + float(bb["w"])
                x = label_right
                w = float(item["w"])
                h = float(item["h"])
                if x + w > page_w - 2:
                    x = max(0.0, page_w - w - 2)
                y = max(0.0, min(float(bb["y"]), page_h - h))
                out.append(
                    {
                        "page": page_no,
                        "x": x,
                        "y": y,
                        "w": w,
                        "h": h,
                        "text": item["name"],
                        "name": item["name"],
                        "fieldType": "image",
                    }
                )
    finally:
        doc.close()
    return out


def _get_left_cell_text_fallback(
    table_id: int,
    row: int,
    col: int,
    cell_texts: Dict[tuple, str],
    cell_black_prefix_texts: Dict[tuple, str],
    red_cell_pos_set: set,
) -> str:
    for lc in range(int(col) - 1, -1, -1):
        if (int(table_id), int(row), int(lc)) in red_cell_pos_set:
            continue
        txt = _sanitize_name_piece(cell_black_prefix_texts.get((table_id, row, lc), ""))
        if not txt:
            txt = _sanitize_name_piece(cell_texts.get((table_id, row, lc), ""))
        if txt:
            return txt
    return ""


def extract_red_text_field_boxes(pdf_path: str, cfg: Optional[Config] = None) -> List[Dict[str, Any]]:
    """
    仅对红色文本划框：先按 extract_text_spans 取色，再复用 merge_tokens_to_blocks 做行内合并。
    返回 [{"page","x","y","w","h","text"}, ...]，坐标为 PDF 点单位。
    """
    cfg = cfg or Config()
    doc = fitz.open(pdf_path)
    out: List[Dict[str, Any]] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            page_no = i + 1
            page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
            spans = extract_text_spans(page)
            table_cells = _filter_noise_tables(extract_table_cells(page), cfg) + extract_vector_rect_cells(page, page_no)
            maps = _build_structured_cell_maps(page, table_cells, spans)
            table_cells_by_rc: Dict[int, Dict[tuple, Dict]] = maps["table_cells"]
            cell_texts: Dict[tuple, str] = maps["cell_texts"]
            cell_black_prefix_texts: Dict[tuple, str] = maps.get("cell_black_prefix_texts") or {}
            header_map_by_table: Dict[int, Dict[str, int]] = {}
            for t_id, cells_by_rc in table_cells_by_rc.items():
                header_map_by_table[t_id] = _infer_table_header_map(t_id, cells_by_rc, cell_texts)

            red_tokens: List[Dict] = []
            red_cell_targets: Dict[tuple, Dict[str, Any]] = {}
            underline_slots_by_cell: Dict[tuple, int] = {}
            for sp in spans:
                if not is_span_red_rgb(sp.get("color_rgb") or []):
                    continue
                text = (sp.get("text") or "").strip()
                if not text:
                    continue
                bb = sp["bbox"]
                cell = _pick_best_cell_for_span(bb, table_cells)
                if cell is not None:
                    key = (
                        int(cell.get("table_id", -1)),
                        int(cell.get("row")) if cell.get("row") is not None else -1,
                        int(cell.get("col")) if cell.get("col") is not None else -1,
                        str(cell.get("cell_id") or ""),
                    )
                    if key not in red_cell_targets:
                        red_cell_targets[key] = {
                            "page": page_no,
                            "x": float(cell["rect"].x0),
                            "y": float(cell["rect"].y0),
                            "w": float(cell["rect"].width),
                            "h": float(cell["rect"].height),
                            "text": "",
                            "table_id": key[0],
                            "row": None if key[1] < 0 else key[1],
                            "col": None if key[2] < 0 else key[2],
                            "cell_id": key[3],
                        }
                    cur = red_cell_targets[key]["text"]
                    red_cell_targets[key]["text"] = f"{cur}{text}".strip()
                    continue
                red_tokens.append(
                    {
                        "text": text,
                        "x0": bb["x"],
                        "y0": bb["y"],
                        "x1": bb["x"] + bb["w"],
                        "y1": bb["y"] + bb["h"],
                        "page": page_no,
                    }
                )
            # 空白单元格自动框：单元格内无文字时加入文本框
            for table_id, cells_by_rc in table_cells_by_rc.items():
                for (row, col), cell in cells_by_rc.items():
                    cell_txt = _normalize_cell_text(cell_texts.get((table_id, row, col), ""))
                    if cell_txt:
                        continue
                    key = (int(table_id), int(row), int(col), str(cell.get("cell_id") or ""))
                    if key in red_cell_targets:
                        continue
                    red_cell_targets[key] = {
                        "page": page_no,
                        "x": float(cell["rect"].x0),
                        "y": float(cell["rect"].y0),
                        "w": float(cell["rect"].width),
                        "h": float(cell["rect"].height),
                        "text": "",
                        "table_id": int(table_id),
                        "row": int(row),
                        "col": int(col),
                        "cell_id": str(cell.get("cell_id") or ""),
                        "fieldType": "text",
                    }
            # 连续下划线自动框：同单元格多处按序号记录
            underline_boxes = _build_underline_boxes_from_spans(spans, page_no)
            for ub in underline_boxes:
                cell = _pick_best_cell_for_span({"x": ub["x"], "y": ub["y"], "w": ub["w"], "h": ub["h"]}, table_cells)
                if cell is None or cell.get("row") is None or cell.get("col") is None:
                    key = (-1, -1, -1, f"ul_p{page_no}_{len(red_cell_targets)+1}")
                    red_cell_targets[key] = {
                        "page": page_no,
                        "x": float(ub["x"]),
                        "y": float(ub["y"]),
                        "w": float(ub["w"]),
                        "h": float(ub["h"]),
                        "text": str(ub.get("text") or ""),
                        "table_id": None,
                        "row": None,
                        "col": None,
                        "cell_id": "",
                        "fieldType": "text",
                        "tail_name": str(ub.get("tail_name") or ""),
                        "unit": str(ub.get("unit") or ""),
                        "slot_no": 1,
                    }
                    continue
                table_id = int(cell["table_id"])
                row = int(cell["row"])
                col = int(cell["col"])
                slot_key = (table_id, row, col)
                underline_slots_by_cell[slot_key] = underline_slots_by_cell.get(slot_key, 0) + 1
                slot_no = underline_slots_by_cell[slot_key]
                key = (table_id, row, col, f"{cell.get('cell_id') or ''}__ul{slot_no}")
                red_cell_targets[key] = {
                    "page": page_no,
                    "x": float(ub["x"]),
                    "y": float(ub["y"]),
                    "w": float(ub["w"]),
                    "h": float(ub["h"]),
                    "text": str(ub.get("text") or ""),
                    "table_id": table_id,
                    "row": row,
                    "col": col,
                    "cell_id": str(cell.get("cell_id") or ""),
                    "fieldType": "text",
                    "tail_name": str(ub.get("tail_name") or ""),
                    "unit": str(ub.get("unit") or ""),
                    "slot_no": slot_no,
                }
            red_cell_pos_set = {(int(t), int(r), int(c)) for (t, r, c, _) in red_cell_targets.keys() if int(r) >= 0 and int(c) >= 0}
            semantic_targets: List[Dict[str, Any]] = []
            for _, target in red_cell_targets.items():
                table_id = target.get("table_id")
                row = target.get("row")
                col = target.get("col")
                if row is None or col is None or table_id not in header_map_by_table:
                    continue
                header_map = header_map_by_table.get(table_id) or {}
                type_name = _resolve_semantic_type_by_col(int(col), header_map)
                if not type_name:
                    continue
                item_col = header_map.get("检测项目")
                if item_col is None:
                    continue
                item_name = _get_item_name_for_row(table_id, row, item_col, cell_texts) or "检测项目"
                target["item_name"] = item_name
                target["type_name"] = _column_header_keyword(int(col), header_map) or type_name
                target["item_col"] = int(item_col)
                semantic_targets.append(target)

            # 同行三类字段使用同一前缀（检测项目 + 同行补充文本）
            row_prefix_cache: Dict[tuple, str] = {}
            for target in semantic_targets:
                table_id = int(target["table_id"])
                row = int(target["row"])
                col = int(target["col"])
                item_name = str(target["item_name"])
                row_key = (table_id, row, item_name, col)
                if row_key in row_prefix_cache:
                    continue
                header_map = header_map_by_table.get(table_id) or {}
                cells_by_rc = table_cells_by_rc.get(table_id) or {}
                supplement = _get_row_prefix_text(
                    table_id=table_id,
                    row=row,
                    item_col=int(target["item_col"]),
                    target_col=col,
                    header_map=header_map,
                    cell_texts=cell_texts,
                    cell_black_prefix_texts=cell_black_prefix_texts,
                    cells_by_rc=cells_by_rc,
                    red_cell_pos_set=red_cell_pos_set,
                )
                row_prefix_cache[row_key] = f"{item_name}_{supplement}" if supplement else item_name

            # 同一检测项目 + 同类型：
            # 最小列作为主列（不补充文本），其余列按同行补充文本命名。
            groups_by_item_type: Dict[tuple, List[Dict[str, Any]]] = {}
            for target in semantic_targets:
                table_id = int(target["table_id"])
                type_name = str(target["type_name"])
                item_name = str(target["item_name"])
                gk = (table_id, item_name, type_name)
                groups_by_item_type.setdefault(gk, []).append(target)

            # 第一阶段：先产出“检测条件”，并记录每行最终前缀，供其他类型复用
            row_prefix_by_condition: Dict[tuple, str] = {}
            for (table_id, item_name, type_name), targets in groups_by_item_type.items():
                if type_name != "检测条件":
                    continue
                targets.sort(key=lambda t: (int(t["row"]), float(t["y"]), float(t["x"])))
                min_col = min(int(t["col"]) for t in targets)
                seq_fallback = 0
                for t in targets:
                    row = int(t["row"])
                    col = int(t["col"])
                    if col == min_col:
                        prefix_used = item_name
                        t["name"] = f"{prefix_used}_{type_name}"
                    else:
                        prefix = row_prefix_cache.get((table_id, row, item_name, col), item_name)
                        if prefix and prefix != item_name:
                            prefix_used = prefix
                            t["name"] = f"{prefix_used}_{type_name}"
                        else:
                            seq_fallback += 1
                            prefix_used = item_name
                            t["name"] = f"{item_name}_{type_name}{seq_fallback}"
                    row_prefix_by_condition[(table_id, row, item_name)] = prefix_used

            # 第二阶段：检测结果/单项判定优先复用同一行检测条件前缀，确保命名对齐
            for (table_id, item_name, type_name), targets in groups_by_item_type.items():
                if type_name == "检测条件":
                    continue
                targets.sort(key=lambda t: (int(t["row"]), float(t["y"]), float(t["x"])))
                for t in targets:
                    row = int(t["row"])
                    prefix = row_prefix_by_condition.get((table_id, row, item_name))
                    if not prefix:
                        col = int(t["col"])
                        prefix = row_prefix_cache.get((table_id, row, item_name, col), item_name)
                    t["name"] = f"{prefix}_{type_name}" if prefix else f"{item_name}_{type_name}"
                    tail_name = _sanitize_name_piece(str(t.get("tail_name") or ""))
                    if tail_name:
                        t["name"] = f"{t['name']}_{tail_name}"

            # 兜底：未命中表头语义规则的单元格，使用左侧单元格文本命名
            for target in red_cell_targets.values():
                if str(target.get("name") or "").strip():
                    continue
                table_id = target.get("table_id")
                row = target.get("row")
                col = target.get("col")
                if row is None or col is None:
                    continue
                fallback = _get_left_cell_text_fallback(
                    table_id=int(table_id),
                    row=int(row),
                    col=int(col),
                    cell_texts=cell_texts,
                    cell_black_prefix_texts=cell_black_prefix_texts,
                    red_cell_pos_set=red_cell_pos_set,
                )
                tail_name = _sanitize_name_piece(str(target.get("tail_name") or ""))
                base_name = fallback or ""
                if not base_name and tail_name:
                    base_name = tail_name
                elif base_name and tail_name:
                    base_name = f"{base_name}_{tail_name}"
                if base_name:
                    target["name"] = base_name
                slot_no = int(target.get("slot_no") or 0)
                if slot_no > 1 and str(target.get("name") or "").strip():
                    target["name"] = f"{target['name']}{slot_no}"
                unit = _extract_unit_suffix(str(target.get("unit") or ""))
                if unit and str(target.get("name") or "").strip():
                    target["name"] = f"{target['name']}({unit})"

            # 单位后缀统一补充（语义命名分支）
            for target in red_cell_targets.values():
                nm = str(target.get("name") or "").strip()
                if not nm:
                    continue
                slot_no = int(target.get("slot_no") or 0)
                if slot_no > 1 and not nm.endswith(str(slot_no)):
                    nm = f"{nm}{slot_no}"
                unit = _extract_unit_suffix(str(target.get("unit") or ""))
                if unit and not nm.endswith(f"({unit})"):
                    nm = f"{nm}({unit})"
                target["name"] = nm

            for _, t in sorted(red_cell_targets.items(), key=lambda kv: (kv[1]["y"], kv[1]["x"])):
                w, h = float(t["w"]), float(t["h"])
                if w <= 0 or h <= 0:
                    continue
                if w * h > page_area * 0.45:
                    continue
                if w < 8 or h < 6:
                    continue
                out.append(
                    {
                        "page": page_no,
                        "x": float(t["x"]),
                        "y": float(t["y"]),
                        "w": w,
                        "h": h,
                        "text": (t.get("text") or "")[:120],
                        "name": (t.get("name") or "")[:120],
                    }
                )
            blocks = merge_tokens_to_blocks(red_tokens, cfg)
            for b in blocks:
                bb = b["bbox"]
                w, h = float(bb["w"]), float(bb["h"])
                if w <= 0 or h <= 0:
                    continue
                if w * h > page_area * 0.45:
                    continue
                if w < 8 or h < 6:
                    continue
                out.append(
                    {
                        "page": page_no,
                        "x": float(bb["x"]),
                        "y": float(bb["y"]),
                        "w": w,
                        "h": h,
                        "text": (b.get("text") or "")[:120],
                        "name": "",
                    }
                )
    finally:
        doc.close()
    return out


def build_text_boxing_json(pdf_path: str, cfg: Config) -> Dict:
    doc = fitz.open(pdf_path)
    pages_out = []
    for i in range(len(doc)):
        page = doc[i]
        page_no = i + 1
        tokens = extract_page_tokens(page, page_no)
        table_cells: List[Dict] = []
        if cfg.table_aware:
            detected_table_cells = _filter_noise_tables(extract_table_cells(page), cfg)
            vector_cells = extract_vector_rect_cells(page, page_no)
            table_cells = detected_table_cells + vector_cells
        text_blocks = merge_tokens_with_table_awareness(page, tokens, page_no, cfg)
        image_blocks = extract_image_blocks(page, page_no) if cfg.detect_images else []
        watermark_blocks = extract_watermark_blocks(page, page_no) if cfg.detect_watermarks else []
        blocks = _merge_page_blocks(page_no, text_blocks, image_blocks, watermark_blocks)
        synthetic_cells = infer_synthetic_cells_from_text_blocks(page_no, text_blocks) if cfg.enable_synthetic_cells else []
        all_table_cells = _filter_contained_table_cells(_table_cells_to_json(table_cells) + synthetic_cells)
        rebuild_payload = {}
        if cfg.export_rebuild_payload:
            rebuild_payload = {
                "spans": extract_text_spans(page),
                "vector_lines": extract_vector_lines(page),
                "images": extract_image_payload(page),
            }
        pages_out.append(
            {
                "page": page_no,
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "table_cells": all_table_cells,
                "blocks": blocks,
                "rebuild_payload": rebuild_payload,
            }
        )

    result = {
        "schema": "text_boxing/v1",
        "source": {
            "file_name": os.path.basename(pdf_path),
            "file_type": "pdf",
            "page_count": len(doc),
        },
        "pages": pages_out,
    }
    doc.close()
    return result


def draw_boxes_on_pdf(input_pdf: str, output_pdf: str, data: Dict, cfg: Config) -> None:
    doc = fitz.open(input_pdf)
    for p in data.get("pages", []):
        page_idx = int(p["page"]) - 1
        if page_idx < 0 or page_idx >= len(doc):
            continue
        page = doc[page_idx]
        for cell in p.get("table_cells", []):
            cb = cell["bbox"]
            cx, cy, cw, ch = cb["x"], cb["y"], cb["w"], cb["h"]
            cell_rect = fitz.Rect(cx, cy, cx + cw, cy + ch)
            cell_color = (0.0, 0.7, 0.0)
            page.draw_rect(
                cell_rect,
                color=cell_color,
                fill=None,
                width=max(0.6, cfg.draw_width),
                stroke_opacity=1.0,
            )
            if cfg.draw_label:
                label_y = cy - 2 if cy >= 2 else cy + 6
                page.insert_text(
                    (cx, label_y),
                    f"{cell.get('cell_id', 'cell')}:cell",
                    fontsize=cfg.label_font_size,
                    color=cell_color,
                )
        for b in p.get("blocks", []):
            bbox = b["bbox"]
            x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
            rect = fitz.Rect(x, y, x + w, y + h)
            block_type = b.get("type", "text")
            if block_type == "image":
                color = (0.0, 0.4, 1.0)
            elif block_type == "watermark":
                color = (0.6, 0.0, 0.8)
            else:
                color = cfg.draw_color
            page.draw_rect(
                rect,
                color=color,
                fill=color if cfg.draw_fill_alpha > 0 else None,
                width=cfg.draw_width,
                stroke_opacity=1.0,
                fill_opacity=max(0.0, min(1.0, cfg.draw_fill_alpha)),
            )
            if cfg.draw_label:
                label_y = y - 2 if y >= 2 else y + 6
                page.insert_text(
                    (x, label_y),
                    f"{b['id']}:{block_type}",
                    fontsize=cfg.label_font_size,
                    color=color,
                )

    doc.save(output_pdf)
    doc.close()


def process_pdf(input_pdf: str, output_json: str, output_boxed_pdf: str, cfg: Config) -> None:
    data = build_text_boxing_json(input_pdf, cfg)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)
    draw_boxes_on_pdf(input_pdf, output_boxed_pdf, data, cfg)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Full-text coordinate boxing for PDF files.")
    parser.add_argument("--input", required=True, help="Input PDF path")
    parser.add_argument("--output-json", default=None, help="Output JSON path")
    parser.add_argument("--output-pdf", default=None, help="Output boxed PDF path")
    parser.add_argument("--line-tolerance-px", type=float, default=3.0, help="Line grouping tolerance")
    parser.add_argument("--gap-threshold-px", type=float, default=6.0, help="In-line merge gap threshold")
    parser.add_argument("--merge-paragraph", action="store_true", help="Enable paragraph merge (reserved)")
    parser.add_argument("--disable-table-aware", action="store_true", help="Disable table cell aware grouping")
    parser.add_argument("--enable-synthetic-cells", action="store_true", help="Enable inferred table-cell boxes")
    parser.add_argument("--disable-image-detect", action="store_true", help="Disable image bbox detection")
    parser.add_argument("--disable-watermark-detect", action="store_true", help="Disable watermark bbox detection")
    parser.add_argument("--no-label", action="store_true", help="Disable block id drawing")
    parser.add_argument("--draw-fill-alpha", type=float, default=0.08, help="Box fill alpha")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_pdf = args.input
    stem, _ = os.path.splitext(input_pdf)
    output_json = args.output_json or f"{stem}_boxed.json"
    output_pdf = args.output_pdf or f"{stem}_boxed.pdf"

    cfg = Config(
        line_tolerance_px=args.line_tolerance_px,
        gap_threshold_px=args.gap_threshold_px,
        merge_paragraph=args.merge_paragraph,
        table_aware=not args.disable_table_aware,
        enable_synthetic_cells=args.enable_synthetic_cells,
        detect_images=not args.disable_image_detect,
        detect_watermarks=not args.disable_watermark_detect,
        draw_label=not args.no_label,
        draw_fill_alpha=args.draw_fill_alpha,
    )
    process_pdf(input_pdf, output_json, output_pdf, cfg)
    print(f"JSON written: {output_json}")
    print(f"Boxed PDF written: {output_pdf}")


if __name__ == "__main__":
    main()
