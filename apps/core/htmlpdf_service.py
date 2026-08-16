"""HTML-PDF 编辑器服务：模板导入/导出与 PDF 生成。"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import fitz
from django.conf import settings
from utils.unified_template_fields import materialize_unified_pdf_fields

DEFAULT_FONT_PT = 10.5
# 最小文本框：至少容纳一个五号汉字宽度，且高度可正常显示五号字单行
MIN_TEXT_FIELD_WIDTH = (DEFAULT_FONT_PT * 1.05) + 10
MIN_TEXT_FIELD_HEIGHT = (DEFAULT_FONT_PT * 1.2) + 6

_FONTS_DIR = Path(settings.BASE_DIR) / "fonts"
# 兼容历史软链 htmlpdf/fonts → ../fonts
if not _FONTS_DIR.is_dir():
    _FONTS_DIR = Path(settings.BASE_DIR) / "htmlpdf" / "fonts"


def get_simsun_font() -> Path:
    """宋体：思源宋体 Regular（缓存实例）；缺失时回退 SIMSUN.TTC。"""
    from utils.pymupdf_fonts import song_font_path

    p = song_font_path(bold=False)
    return p if p is not None else (_FONTS_DIR / "SIMSUN.TTC")


def get_simhei_font(*, bold: bool = False) -> Path:
    """黑体：思源黑体 Regular/Bold。"""
    from utils.pymupdf_fonts import hei_font_path

    p = hei_font_path(bold=bold)
    return p if p is not None else (_FONTS_DIR / "wqy-zenhei.ttc")


# 兼容旧代码：首次访问时解析（勿在 import 时实例化可变字体）
class _LazySimsunPath:
    def __str__(self) -> str:
        return str(get_simsun_font())

    def __fspath__(self) -> str:
        return str(get_simsun_font())

    def exists(self) -> bool:
        return get_simsun_font().is_file()

    def is_file(self) -> bool:
        return get_simsun_font().is_file()

    @property
    def name(self) -> str:
        return get_simsun_font().name


SIMSUN_FONT = _LazySimsunPath()
TIMES_FONT_CANDIDATES = [
    _FONTS_DIR / "times.ttf",
    _FONTS_DIR / "TIMES.TTF",
    _FONTS_DIR / "Times New Roman.ttf",
    Path("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/times.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
]
CHECK_FONT_CANDIDATES = [
    _FONTS_DIR / "SEGUISYM.TTF",
    _FONTS_DIR / "SEGUISYM.ttf",
    _FONTS_DIR / "seguisym.ttf",
    _FONTS_DIR / "SegoeUISymbol.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    # 思源宋体作最后回退（延迟解析）
    SIMSUN_FONT,
]


def ensure_htmlpdf_temp_dir(user_id: int) -> Path:
    root = Path(settings.FILE_LIBRARY_TEMP_ROOT) / "htmlpdf" / str(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def htmlpdf_source_pdf_path(user_id: int) -> Path:
    return ensure_htmlpdf_temp_dir(user_id) / "original.pdf"


def htmlpdf_source_meta_path(user_id: int) -> Path:
    return ensure_htmlpdf_temp_dir(user_id) / "source_meta.json"


def _coerce_rect(raw_rect) -> fitz.Rect | None:
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


def _extract_table_cell_rects(page: fitz.Page) -> List[fitz.Rect]:
    out: List[fitz.Rect] = []
    if hasattr(page, "find_tables"):
        try:
            table_finder = page.find_tables()
        except Exception:
            table_finder = None
        tables = getattr(table_finder, "tables", None) or []
        for table in tables:
            rows = getattr(table, "rows", None)
            if rows:
                for row in rows:
                    for cell in (getattr(row, "cells", None) or []):
                        rect = _coerce_rect(cell)
                        if rect is not None and not rect.is_empty:
                            out.append(rect)
                continue
            for cell in (getattr(table, "cells", None) or []):
                rect = _coerce_rect(cell)
                if rect is not None and not rect.is_empty:
                    out.append(rect)
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        if d.get("type") not in ("s", "fs", "sf"):
            continue
        rect = _coerce_rect(d.get("rect"))
        if rect is None or rect.is_empty:
            continue
        w, h = float(rect.width), float(rect.height)
        if w < 18 or h < 10:
            continue
        if w > page.rect.width * 0.95 and h > page.rect.height * 0.95:
            continue
        out.append(rect)
    return out


def detect_table_cell_bbox_at_point(source_pdf: Path, page_no: int, x: float, y: float) -> Dict[str, float] | None:
    if not source_pdf.exists():
        raise FileNotFoundError("source pdf not found")
    if page_no <= 0:
        return None
    doc = fitz.open(source_pdf)
    try:
        if page_no > len(doc):
            return None
        page = doc[page_no - 1]
        point = fitz.Point(float(x), float(y))
        hits = [rect for rect in _extract_table_cell_rects(page) if rect.contains(point)]
        if not hits:
            return None
        hits.sort(key=lambda r: max(0.0, float(r.width) * float(r.height)))
        rect = hits[0]
        return {
            "x": float(rect.x0),
            "y": float(rect.y0),
            "w": float(rect.width),
            "h": float(rect.height),
        }
    finally:
        doc.close()


def detect_table_cells_for_field_boxes(source_pdf: Path, fields: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    批量为字段定位所属表格单元格（按字段中心点命中最小 cell）。
    返回项:
    {
      "index": 字段下标,
      "page": 页码,
      "cell": {"x","y","w","h"} | None
    }
    """
    out: List[Dict[str, Any]] = []
    if not source_pdf.exists():
        raise FileNotFoundError("source pdf not found")
    doc = fitz.open(source_pdf)
    try:
        page_cells: Dict[int, List[fitz.Rect]] = {}
        for i in range(len(doc)):
            page_no = i + 1
            page_cells[page_no] = _extract_table_cell_rects(doc[i])
        for idx, item in enumerate(fields or []):
            if not isinstance(item, dict):
                out.append({"index": idx, "page": 0, "cell": None})
                continue
            try:
                page_no = int(item.get("page") or 0)
                x = float(item.get("x") or 0.0)
                y = float(item.get("y") or 0.0)
                w = float(item.get("w") or 0.0)
                h = float(item.get("h") or 0.0)
            except Exception:
                out.append({"index": idx, "page": 0, "cell": None})
                continue
            if page_no <= 0 or page_no not in page_cells:
                out.append({"index": idx, "page": page_no, "cell": None})
                continue
            pt = fitz.Point(x + w / 2.0, y + h / 2.0)
            hits = [rect for rect in page_cells[page_no] if rect.contains(pt)]
            if not hits:
                out.append({"index": idx, "page": page_no, "cell": None})
                continue
            hits.sort(key=lambda r: max(0.0, float(r.width) * float(r.height)))
            hit = hits[0]
            out.append(
                {
                    "index": idx,
                    "page": page_no,
                    "cell": {
                        "x": float(hit.x0),
                        "y": float(hit.y0),
                        "w": float(hit.width),
                        "h": float(hit.height),
                    },
                }
            )
        return out
    finally:
        doc.close()


def extract_table_cells_with_text(source_pdf: Path, page_no: int) -> List[Dict[str, Any]]:
    """
    读取某页表格单元格及单元格文字（按词坐标拼接）。
    返回 [{"x","y","w","h","text"}...]
    """
    if not source_pdf.exists():
        raise FileNotFoundError("source pdf not found")
    if page_no <= 0:
        return []
    doc = fitz.open(source_pdf)
    try:
        if page_no > len(doc):
            return []
        page = doc[page_no - 1]
        rects = _extract_table_cell_rects(page)
        words = page.get_text("words") or []
        word_rows = []
        for w in words:
            if not isinstance(w, (list, tuple)) or len(w) < 5:
                continue
            x0, y0, x1, y1, txt = w[:5]
            t = str(txt or "").strip()
            if not t:
                continue
            word_rows.append(
                {
                    "x0": float(x0),
                    "y0": float(y0),
                    "x1": float(x1),
                    "y1": float(y1),
                    "cx": (float(x0) + float(x1)) / 2.0,
                    "cy": (float(y0) + float(y1)) / 2.0,
                    "text": t,
                }
            )

        out: List[Dict[str, Any]] = []
        for r in rects:
            inside = []
            for wd in word_rows:
                pt = fitz.Point(float(wd["cx"]), float(wd["cy"]))
                if r.contains(pt):
                    inside.append(wd)
            inside.sort(key=lambda z: (float(z["y0"]), float(z["x0"])))
            text = "".join(str(z.get("text") or "") for z in inside).strip()
            out.append(
                {
                    "x": float(r.x0),
                    "y": float(r.y0),
                    "w": float(r.width),
                    "h": float(r.height),
                    "text": text,
                }
            )
        return out
    finally:
        doc.close()


def _load_full_text_coordinate_boxing():
    path = Path(settings.BASE_DIR) / "htmlpdf" / "full_text_coordinate_boxing.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("full_text_coordinate_boxing", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _auto_box_rect_key(box: Dict[str, Any], *, tol: float = 0.5) -> tuple:
    page = int(box.get("page") or 1)

    def _r(v: float) -> float:
        return round(float(v) / tol) * tol

    return (
        page,
        _r(float(box.get("x") or 0)),
        _r(float(box.get("y") or 0)),
        _r(float(box.get("w") or 0)),
        _r(float(box.get("h") or 0)),
    )


def _auto_box_keep_score(box: Dict[str, Any]) -> int:
    score = len(str(box.get("name") or box.get("text") or ""))
    if str(box.get("judgmentCriterionText") or "").strip():
        score += 500
    if isinstance(box.get("checkboxPair"), dict) and box.get("checkboxPair"):
        score += 80
    if str(box.get("fieldType") or "").strip().lower() in ("check", "image"):
        score += 40
    if isinstance(box.get("autoSemantic"), dict) and box.get("autoSemantic"):
        score += 30
    return score


def _dedupe_auto_boxes_by_rect(
    boxes: List[Dict[str, Any]],
    *,
    overlap_fn=None,
    overlap_threshold: float = 0.72,
    tol: float = 0.5,
) -> List[Dict[str, Any]]:
    """合并同一位置（或高度重叠）的自动框，避免重复划框。"""
    kept: List[Dict[str, Any]] = []
    for raw in boxes or []:
        if not isinstance(raw, dict):
            continue
        replaced = False
        for idx, prev in enumerate(kept):
            if int(raw.get("page") or 1) != int(prev.get("page") or 1):
                continue
            same_place = _auto_box_rect_key(raw, tol=tol) == _auto_box_rect_key(prev, tol=tol)
            overlap = False
            if not same_place and callable(overlap_fn):
                try:
                    overlap = float(overlap_fn(raw, prev)) >= overlap_threshold
                except Exception:
                    overlap = False
            if not (same_place or overlap):
                continue
            if _auto_box_keep_score(raw) > _auto_box_keep_score(prev):
                kept[idx] = raw
            replaced = True
            break
        if not replaced:
            kept.append(raw)
    return kept


def htmlpdf_auto_red_text_fields_for_editor(user_id: int) -> List[Dict[str, Any]]:
    """
    自动划框：复用 full_text_coordinate_boxing 的合并规则，仅保留 PDF 中红色字体对应的文本块。
    返回编辑器 fields 列表片段（含 redText 标记，前端用红色显示填写内容）。
    """
    pdf = htmlpdf_source_pdf_path(user_id)
    if not pdf.is_file():
        return []
    mod = _load_full_text_coordinate_boxing()
    if mod is None:
        return []
    extract_fn = getattr(mod, "extract_red_text_field_boxes", None)
    if not callable(extract_fn):
        return []
    boxes = extract_fn(str(pdf))
    if not isinstance(boxes, list):
        boxes = []
    check_boxes: List[Dict[str, Any]] = []
    extract_check_fn = getattr(mod, "extract_checkbox_symbol_boxes", None)
    if callable(extract_check_fn):
        raw_checks = extract_check_fn(str(pdf))
        if isinstance(raw_checks, list):
            check_boxes = raw_checks
    image_boxes: List[Dict[str, Any]] = []
    extract_image_fn = getattr(mod, "extract_signature_image_boxes", None)
    if callable(extract_image_fn):
        raw_images = extract_image_fn(str(pdf))
        if isinstance(raw_images, list):
            image_boxes = raw_images
    is_sig_label = getattr(mod, "_span_text_is_signature_label", None)
    overlap_fn = getattr(mod, "_rect_overlap_ratio", None)
    filtered_boxes: List[Dict[str, Any]] = []
    for b in boxes:
        if not isinstance(b, dict):
            continue
        nm = str(b.get("name") or b.get("text") or "")
        if callable(is_sig_label) and is_sig_label(nm):
            continue
        skip = False
        if callable(overlap_fn):
            for img in image_boxes:
                if isinstance(img, dict) and overlap_fn(b, img) > 0.22:
                    skip = True
                    break
        if skip:
            continue
        filtered_boxes.append(b)
    merged = list(filtered_boxes) + list(check_boxes) + list(image_boxes)
    merged = _dedupe_auto_boxes_by_rect(merged, overlap_fn=overlap_fn)
    fields: List[Dict[str, Any]] = []
    for i, b in enumerate(merged):
        if not isinstance(b, dict):
            continue
        field_type = str(b.get("fieldType") or "text").strip().lower()
        is_check = field_type == "check"
        is_image = field_type == "image"
        auto_name = str(b.get("name") or "").strip()
        slot_ph = str(b.get("placeholder") or "").strip()
        snippet = str(b.get("text") or "").strip().replace("\n", " ")[:24]
        ph = (slot_ph if is_image and slot_ph else None) or auto_name or (
            (f"勾选{i + 1}")
            if is_check
            else ((f"图片{i + 1}") if is_image else (f"红字{i + 1}" + (f" · {snippet}" if snippet else "")))
        )
        row: Dict[str, Any] = {
            "page": int(b.get("page") or 1),
            "x": float(b.get("x") or 0),
            "y": float(b.get("y") or 0),
            "w": float(b.get("w") or 0),
            "h": float(b.get("h") or 0),
            "placeholder": ph,
            "originalPlaceholder": ph,
            "fieldType": "check" if is_check else ("image" if is_image else "text"),
            "redText": False if (is_check or is_image) else True,
            "value": "",
            "checked": False,
            "imageData": "",
        }
        pdf_fid = str(b.get("pdfFieldId") or "").strip()
        if pdf_fid:
            row["pdfFieldId"] = pdf_fid
        cp = b.get("checkboxPair")
        if isinstance(cp, dict) and cp and is_check:
            row["checkboxPair"] = cp
        jct = str(b.get("judgmentCriterionText") or "").strip()
        if jct:
            row["judgmentCriterionText"] = jct[:500]
        auto_semantic = b.get("autoSemantic")
        if isinstance(auto_semantic, dict) and auto_semantic:
            row["autoSemantic"] = {
                str(k): v
                for k, v in auto_semantic.items()
                if isinstance(k, str) and v is not None and v != ""
            }
        fields.append(row)
    assign_sections_fn = getattr(mod, "assign_template_sections_to_fields", None)
    if callable(assign_sections_fn) and fields:
        try:
            fields = assign_sections_fn(str(pdf), fields)
        except Exception:
            pass
    return fields


def pick_times_font() -> Path | None:
    for p in TIMES_FONT_CANDIDATES:
        if p.exists():
            return p
    return None


def pick_check_font() -> Path | None:
    # 优先使用 SEGUISYM.TTF，确保对勾符号在导出 PDF 中可稳定显示。
    preferred = _FONTS_DIR / "SEGUISYM.TTF"
    if preferred.exists():
        return preferred
    for p in CHECK_FONT_CANDIDATES:
        if p.exists():
            return p
    return None


def contains_cjk(text: str) -> bool:
    if re.search(r"[\u3400-\u9FFF]", text or ""):
        return True
    # 全角标点（如质控映射「104kV，91mA」中的「，」）须走宋体，否则 Times 下逗号可能缺失
    return re.search(r"[\u3000-\u303F\uFF00-\uFFEF]", text or "") is not None


def wrap_text_to_width(text: str, max_width: float, font: fitz.Font, font_size: float) -> str:
    lines: List[str] = []
    for para in (text or "").splitlines() or [""]:
        current = ""
        for ch in para:
            trial = current + ch
            if font.text_length(trial, fontsize=font_size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                    current = ch
                else:
                    lines.append(ch)
                    current = ""
        lines.append(current)
    return "\n".join(lines)


# 现场记录回填：五号 10.5pt（见 DEFAULT_FONT_PT）；报告回填：小四 12pt（REPORT_FILL_FONT_PT）
_REPORT_FILL_FONT_PT = 12.0
REPORT_FILL_FONT_PT = _REPORT_FILL_FONT_PT
_INSTRUMENT_PACK_SEP = "；"


def _pdf_field_width_pt(field: Dict[str, Any] | None) -> float | None:
    """从模板栏位 dict 解析 PDF 文本域宽度（pt）。"""
    if not isinstance(field, dict):
        return None
    anchor = field.get("pdfAnchor") if isinstance(field.get("pdfAnchor"), dict) else {}
    ar = anchor.get("rect")
    if isinstance(ar, (list, tuple)) and len(ar) >= 4:
        try:
            a0, a1, a2, a3 = float(ar[0]), float(ar[1]), float(ar[2]), float(ar[3])
            w, h = abs(a2 - a0), abs(a3 - a1)
            if w > 0 and h > 0 and w >= h:
                return w
        except (TypeError, ValueError):
            pass
    raw_rect = field.get("rect")
    if isinstance(raw_rect, (list, tuple)) and len(raw_rect) >= 5:
        try:
            w = float(raw_rect[3])
            if w > 0:
                return w
        except (TypeError, ValueError):
            pass
    if isinstance(raw_rect, (list, tuple)) and len(raw_rect) >= 4:
        try:
            w = abs(float(raw_rect[2]) - float(raw_rect[0]))
            h = abs(float(raw_rect[3]) - float(raw_rect[1]))
            if w > 0 and h > 0 and w >= h:
                return w
        except (TypeError, ValueError):
            pass
    if all(k in field for k in ("x0", "x1")):
        try:
            w = abs(float(field["x1"]) - float(field["x0"]))
            if w > 0:
                return w
        except (TypeError, ValueError):
            pass
    w_val = field.get("w")
    if w_val is not None:
        try:
            w = float(w_val)
            if w > 0:
                return w
        except (TypeError, ValueError):
            pass
    return None


def estimate_pdf_field_chars_per_line(
    field: Dict[str, Any] | None,
    font_size: float = DEFAULT_FONT_PT,
) -> int:
    """按栏位宽度与回填字号估算每行可容纳汉字数（用于仪器打包）。"""
    width = _pdf_field_width_pt(field)
    if width is None or width <= 0:
        return 96
    return max(12, int(width / max(1.0, font_size * 1.02)))


def wrap_instrument_text_to_width(
    text: str,
    max_width: float,
    font: fitz.Font,
    font_size: float,
    separator: str = _INSTRUMENT_PACK_SEP,
) -> str:
    """检测仪器串：仅在 separator（；）处换行，单台仪器信息不拆开；极个别超宽条目才按字拆。"""
    if max_width <= 0:
        return text or ""
    lines: List[str] = []
    for para in (text or "").splitlines() or [""]:
        para = para.strip()
        if not para:
            lines.append("")
            continue
        parts = para.split(separator)
        segments: List[str] = []
        for i, part in enumerate(parts):
            seg = part.strip()
            if not seg:
                continue
            if i < len(parts) - 1:
                seg = f"{seg}{separator}"
            segments.append(seg)
        if not segments:
            lines.append("")
            continue
        current = ""
        for seg in segments:
            candidate = seg if not current else f"{current}{seg}"
            if font.text_length(candidate, fontsize=font_size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if font.text_length(seg, fontsize=font_size) <= max_width:
                current = seg
                continue
            wrapped_seg = wrap_text_to_width(seg, max_width, font, font_size)
            seg_lines = wrapped_seg.splitlines()
            if seg_lines:
                lines.extend(seg_lines[:-1])
                current = seg_lines[-1]
        if current:
            lines.append(current)
    return "\n".join(lines)


def fit_instrument_text_for_box(
    text: str,
    rect: fitz.Rect,
    font: fitz.Font,
    *,
    max_font_pt: float = DEFAULT_FONT_PT,
) -> Tuple[str, float]:
    """检测仪器栏：按仪器条目边界换行；默认自适应缩小字号，调试可改为固定字号。"""
    from apps.core.pdf_fill_runtime_config import get_pdf_fill_runtime_config

    cfg = get_pdf_fill_runtime_config()
    max_fs = min(max_font_pt, rect.height * 0.85) if cfg.font_auto_shrink else float(max_font_pt)
    min_fs = float(cfg.font_min_pt) if cfg.font_auto_shrink else float(max_font_pt)
    min_fs = max(4.0, min(min_fs, max_fs if cfg.font_auto_shrink else max_font_pt))
    fs = max(max_fs, min_fs) if cfg.font_auto_shrink else float(max_font_pt)
    max_w = max(1.0, rect.width - 2)
    if not cfg.font_auto_shrink:
        return wrap_instrument_text_to_width(text, max_w, font, fs), fs
    while fs >= min_fs:
        wrapped = wrap_instrument_text_to_width(text, max_w, font, fs)
        line_count = max(1, wrapped.count("\n") + 1)
        line_height = fs * 1.2
        if line_count * line_height <= max(1.0, rect.height - 2):
            return wrapped, fs
        fs -= 0.5
    return wrap_instrument_text_to_width(text, max_w, font, min_fs), min_fs


def fit_text_for_box(
    text: str,
    rect: fitz.Rect,
    font: fitz.Font,
    *,
    max_font_pt: float = DEFAULT_FONT_PT,
) -> Tuple[str, float]:
    """普通文本栏：默认按框高自适应缩小；调试设置为 fixed 时强制用基准字号。"""
    from apps.core.pdf_fill_runtime_config import get_pdf_fill_runtime_config

    cfg = get_pdf_fill_runtime_config()
    if not cfg.font_auto_shrink:
        fs = float(max_font_pt)
        wrapped = wrap_text_to_width(text, max(1.0, rect.width - 2), font, fs)
        return wrapped, fs
    max_fs = min(max_font_pt, rect.height * 0.85)
    min_fs = max(4.0, min(float(cfg.font_min_pt), max_fs))
    fs = max(max_fs, min_fs)
    while fs >= min_fs:
        wrapped = wrap_text_to_width(text, max(1.0, rect.width - 2), font, fs)
        line_count = max(1, wrapped.count("\n") + 1)
        line_height = fs * 1.2
        if line_count * line_height <= max(1.0, rect.height - 1):
            return wrapped, fs
        fs -= 0.5
    return wrap_text_to_width(text, max(1.0, rect.width - 2), font, min_fs), min_fs


def _insert_multiline_text_left(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    font_name: str,
    font_obj: fitz.Font,
    fs: float,
    color: tuple[float, float, float],
) -> None:
    """多行左对齐顶对齐直写（insert_textbox 失败时的仪器栏回退）。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return
    line_h = fs * 1.2
    for i, line in enumerate(lines):
        baseline_y = rect.y0 + i * line_h + fs * 0.85
        if baseline_y > rect.y1:
            break
        page.insert_text(
            fitz.Point(rect.x0, baseline_y),
            line,
            fontname=font_name,
            fontsize=fs,
            color=color,
            overlay=True,
        )


def ensure_min_text_rect(rect: fitz.Rect) -> fitz.Rect:
    width = rect.width
    height = rect.height
    if width >= MIN_TEXT_FIELD_WIDTH and height >= MIN_TEXT_FIELD_HEIGHT:
        return rect
    cx = (rect.x0 + rect.x1) / 2.0
    cy = (rect.y0 + rect.y1) / 2.0
    half_w = max(width, MIN_TEXT_FIELD_WIDTH) / 2.0
    half_h = max(height, MIN_TEXT_FIELD_HEIGHT) / 2.0
    return fitz.Rect(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def safe_inset_rect(rect: fitz.Rect, pad: float) -> fitz.Rect:
    if rect.width <= pad * 2 + 0.5 or rect.height <= pad * 2 + 0.5:
        return rect
    return fitz.Rect(rect.x0 + pad, rect.y0 + pad, rect.x1 - pad, rect.y1 - pad)


def site_record_section_title_by_key(section_key: str) -> str:
    key = str(section_key or "").strip()
    if not key:
        return ""
    try:
        mod = _load_full_text_coordinate_boxing()
        specs = getattr(mod, "SITE_RECORD_TEMPLATE_SECTIONS", None) if mod else None
    except Exception:
        specs = None
    for spec in specs or []:
        if str(spec.get("key") or "").strip() == key:
            return str(spec.get("title") or key).strip()
    return key


def normalize_field_template_sections(fields: List[Dict[str, Any]]) -> None:
    """将 sectionKey / templateSectionKey 统一为编辑器使用的 templateSection* 字段。"""
    for row in fields or []:
        if not isinstance(row, dict):
            continue
        sk = str(row.get("templateSectionKey") or row.get("sectionKey") or "").strip()
        if not sk:
            continue
        row["templateSectionKey"] = sk
        row["templateSectionTitle"] = str(
            row.get("templateSectionTitle") or site_record_section_title_by_key(sk) or sk
        ).strip()
        row.pop("sectionKey", None)


def site_record_template_section_keys() -> frozenset[str]:
    mod = _load_full_text_coordinate_boxing()
    specs = getattr(mod, "SITE_RECORD_TEMPLATE_SECTIONS", None) if mod else None
    if isinstance(specs, list) and specs:
        return frozenset(
            str(s.get("key") or "").strip()
            for s in specs
            if isinstance(s, dict) and str(s.get("key") or "").strip()
        )
    return frozenset(
        {
            "site_unit_basic",
            "site_device_basic",
            "site_instruments_staff",
            "site_qc_performance",
            "site_radiation_protection",
            "site_layout_diagram",
        }
    )


def strip_site_record_section_fields(
    fields: List[Dict[str, Any]], *, remove_all_section_keys: bool = False
) -> None:
    """
    报告模板不写入/展示现场记录六大章节。
    remove_all_section_keys=True 时去掉任意 templateSectionKey（报告保存用）。
    """
    site_keys = site_record_template_section_keys()
    for row in fields or []:
        if not isinstance(row, dict):
            continue
        sk = str(row.get("templateSectionKey") or row.get("sectionKey") or "").strip()
        if remove_all_section_keys or sk in site_keys:
            row.pop("templateSectionKey", None)
            row.pop("templateSectionTitle", None)
            row.pop("sectionKey", None)


def library_template_file_linked_to_report_task(lf) -> bool:
    """模板文件是否关联到「报告」类任务模板。"""
    if lf is None:
        return False
    from apps.core.models import LibraryTask

    return LibraryTask.objects.filter(
        library_files=lf,
        output_target=LibraryTask.OUTPUT_REPORT,
    ).exists()


def fields_have_persisted_template_sections(
    fields: List[Dict[str, Any]], *, min_ratio: float = 0.5
) -> bool:
    """磁盘 JSON 已带章节键时无需再扫一遍 PDF（载入变慢的主因之一）。"""
    rows = [f for f in fields if isinstance(f, dict)]
    if not rows:
        return False
    keyed = sum(
        1
        for f in rows
        if str(f.get("templateSectionKey") or f.get("sectionKey") or "").strip()
    )
    return keyed >= max(1, int(len(rows) * min_ratio))


def slim_parsed_template_for_editor_layout(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """编辑器「载入版式」仅需 fields + 元数据，去掉巨型 formSchema.steps 以缩小响应。"""
    if not isinstance(parsed, dict):
        return {}
    meta = parsed.get("template_meta") if isinstance(parsed.get("template_meta"), dict) else {}
    fs = parsed.get("form_schema") if isinstance(parsed.get("form_schema"), dict) else {}
    out: Dict[str, Any] = {
        "fields": parsed.get("fields") or [],
        "content": parsed.get("content") if isinstance(parsed.get("content"), dict) else {},
        "bindings": parsed.get("bindings") if isinstance(parsed.get("bindings"), dict) else {},
        "source_pdf": parsed.get("source_pdf") if isinstance(parsed.get("source_pdf"), dict) else {},
        "template_meta": meta,
        "templateId": parsed.get("templateId") or meta.get("templateId") or "",
        "templateName": parsed.get("templateName") or meta.get("templateName") or "",
        "version": parsed.get("version") or meta.get("version") or "1.0.0",
        "reportType": parsed.get("reportType") or meta.get("reportType") or "",
        "standard": parsed.get("standard") or meta.get("standard") or "",
        "pdfUrl": parsed.get("pdfUrl") or meta.get("pdfUrl") or "",
        "locale": parsed.get("locale") or meta.get("locale") or "zh-CN",
        "constants": (
            parsed.get("constants")
            if isinstance(parsed.get("constants"), dict)
            else (fs.get("constants") if isinstance(fs.get("constants"), dict) else {})
        ),
        "enums": (
            parsed.get("enums")
            if isinstance(parsed.get("enums"), dict)
            else (fs.get("enums") if isinstance(fs.get("enums"), dict) else {})
        ),
        "schema": parsed.get("schema") or "",
    }
    for key in ("fieldFormulas", "fieldVerdictPlan", "lookupTables", "pdfFieldFormulas", "radiationProtectionChapter"):
        if key in parsed and parsed[key] not in (None, "", [], {}):
            out[key] = parsed[key]
        elif isinstance(fs.get(key), (dict, list)) and fs.get(key) not in (None, "", [], {}):
            out[key] = fs[key]
    if isinstance(fs, dict) and fs.get("radiationProtectionChapter") and "radiationProtectionChapter" not in out:
        out["radiationProtectionChapter"] = fs["radiationProtectionChapter"]
        out["form_schema"] = {
            "constants": fs.get("constants") if isinstance(fs.get("constants"), dict) else {},
            "enums": fs.get("enums") if isinstance(fs.get("enums"), dict) else {},
            "radiationProtectionChapter": fs.get("radiationProtectionChapter"),
        }
    return out


def assign_template_sections_for_editor(
    user_id: int, fields: List[Dict[str, Any]], *, skip_for_report: bool = False
) -> List[Dict[str, Any]]:
    """按当前编辑器 PDF 纵坐标为栏位写入章节（仅现场记录类模板；报告模板跳过）。"""
    rows = [dict(f) for f in fields if isinstance(f, dict)]
    if not rows:
        return rows
    if skip_for_report:
        strip_site_record_section_fields(rows, remove_all_section_keys=True)
        return rows
    normalize_field_template_sections(rows)
    if fields_have_persisted_template_sections(rows):
        return rows
    pdf = htmlpdf_source_pdf_path(user_id)
    if not pdf.is_file():
        return rows
    mod = _load_full_text_coordinate_boxing()
    assign_fn = getattr(mod, "assign_template_sections_to_fields", None) if mod else None
    if callable(assign_fn):
        try:
            rows = assign_fn(str(pdf), rows)
            normalize_field_template_sections(rows)
        except Exception:
            pass
    return rows


def parse_template_json(raw_text: str) -> Dict[str, Any]:
    data = json.loads(raw_text)
    if isinstance(data, list):
        return {"fields": data, "content": {}, "bindings": {}, "source_pdf": {}}
    if isinstance(data, dict):
        # Unified template format (v1/v2): keep HTMLPDF fields under pdf.fields.
        pdf_block = data.get("pdf")
        if isinstance(pdf_block, dict) and isinstance(pdf_block.get("fields"), list):
            template_meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
            if not template_meta:
                template_meta = {
                    "templateId": data.get("templateId", ""),
                    "templateName": data.get("templateName", ""),
                    "version": data.get("version", ""),
                    "reportType": data.get("reportType", ""),
                    "standard": data.get("standard", ""),
                    "pdfUrl": data.get("pdfUrl", ""),
                    "locale": data.get("locale", ""),
                }
            form_schema = data.get("formSchema") if isinstance(data.get("formSchema"), dict) else {}
            if not form_schema:
                form_schema = {
                    "constants": data.get("constants", {}) if isinstance(data.get("constants"), dict) else {},
                    "enums": data.get("enums", {}) if isinstance(data.get("enums"), dict) else {},
                    "steps": data.get("steps", []) if isinstance(data.get("steps"), list) else [],
                }
            else:
                _fs_steps = form_schema.get("steps")
                if not (isinstance(_fs_steps, list) and _fs_steps):
                    _top_steps = data.get("steps") if isinstance(data.get("steps"), list) else []
                    if _top_steps:
                        form_schema = {**form_schema, "steps": _top_steps}
            fields_out = materialize_unified_pdf_fields(pdf_block.get("fields") or [])
            normalize_field_template_sections(fields_out)
            parsed = {
                "fields": fields_out,
                "content": data.get("content", {}) if isinstance(data.get("content"), dict) else {},
                "bindings": data.get("bindings", {}) if isinstance(data.get("bindings"), dict) else {},
                "source_pdf": pdf_block.get("source_pdf", {}) if isinstance(pdf_block.get("source_pdf"), dict) else {},
                "template_meta": template_meta,
                "form_schema": form_schema,
                "templateId": template_meta.get("templateId", ""),
                "templateName": template_meta.get("templateName", ""),
                "version": template_meta.get("version", ""),
                "reportType": template_meta.get("reportType", ""),
                "standard": template_meta.get("standard", ""),
                "pdfUrl": template_meta.get("pdfUrl", ""),
                "locale": template_meta.get("locale", ""),
                "constants": form_schema.get("constants", {}) if isinstance(form_schema.get("constants"), dict) else {},
                "enums": form_schema.get("enums", {}) if isinstance(form_schema.get("enums"), dict) else {},
                "steps": form_schema.get("steps", []) if isinstance(form_schema.get("steps"), list) else [],
                "schema": data.get("schema") or "",
            }
            for key in ("lookupTables", "fieldVerdictPlan", "fieldFormulas", "pdfFieldFormulas", "radiationProtectionChapter"):
                val = form_schema.get(key) if isinstance(form_schema, dict) else None
                if val in (None, "", [], {}) and isinstance(data, dict):
                    val = data.get(key)
                if val not in (None, "", [], {}) and isinstance(val, (dict, list)):
                    parsed[key] = val
            return parsed
        if "fields" not in data and "content" not in data:
            return {
                "fields": [],
                "content": data,
                "bindings": data.get("bindings", {}) if isinstance(data.get("bindings"), dict) else {},
                "source_pdf": data.get("source_pdf", {}) if isinstance(data.get("source_pdf"), dict) else {},
                "template_meta": data.get("meta", {}) if isinstance(data.get("meta"), dict) else {},
                "schema": data.get("schema") or "",
            }
        fields = materialize_unified_pdf_fields(data.get("fields", []) if isinstance(data.get("fields"), list) else [])
        normalize_field_template_sections(fields)
        content_map = data.get("content", {})
        if not content_map and isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict) and "id" in item and "content" in item:
                    content_map[item["id"]] = item.get("content", "")
        return {
            "fields": fields,
            "content": content_map,
            "bindings": data.get("bindings", {}) if isinstance(data.get("bindings"), dict) else {},
            "source_pdf": data.get("source_pdf", {}) if isinstance(data.get("source_pdf"), dict) else {},
            "template_meta": data.get("meta", {}) if isinstance(data.get("meta"), dict) else {},
            "schema": data.get("schema") or "",
        }
    raise ValueError("JSON structure unsupported")


def _pdf_field_is_qc_verdict_slot(field: Dict[str, Any]) -> bool:
    """质控表「单项判定」格（报告用小四）；现场记录不走此分支。"""
    if field.get("_syntheticQcVerdict"):
        return True
    parts: list[str] = []
    for k in ("id", "placeholder", "originalPlaceholder", "title", "label", "fieldId", "pdfFieldId"):
        v = field.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    blob = " ".join(parts)
    if "单项判定" in blob:
        return True
    t = str(field.get("value") or field.get("content") or "").strip()
    return t in ("合格", "不合格", "符合", "不符合")


def _pdf_text_color_for_field(field: Dict[str, Any], text: str) -> tuple[float, float, float]:
    """
    回填 PDF 文字颜色。
    - 正式模式：纯黑
    - 测试模式：默认红；单项判定合格为绿、不合格为蓝
    """
    try:
        from apps.core.pdf_fill_runtime_config import pdf_fill_is_test_mode

        if not pdf_fill_is_test_mode():
            return (0, 0, 0)
    except Exception:
        pass
    parts: list[str] = []
    for k in ("id", "placeholder", "originalPlaceholder", "title", "label", "fieldId", "pdfFieldId"):
        v = field.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    blob = " ".join(parts)
    t = str(text or "").strip()
    verdict_slot = "单项判定" in blob or blob.strip() in ("判定", "合格", "不合格")
    if not verdict_slot and t:
        if t in ("不合格", "不符合", "未通过") or "不合格" in t:
            verdict_slot = True
        elif t in ("合格", "符合", "通过") or ("合格" in t and "不合格" not in t):
            verdict_slot = True
    if not verdict_slot:
        return (1, 0, 0)
    if t in ("不合格", "不符合", "未通过") or "不合格" in t:
        return (0, 0, 1)
    if t in ("合格", "符合", "通过") or "合格" in t:
        return (0, 0.55, 0)
    return (1, 0, 0)


def _pdf_check_mark_color() -> tuple[float, float, float]:
    """勾选 ✓ 颜色：测试模式红，正式模式黑。"""
    try:
        from apps.core.pdf_fill_runtime_config import pdf_fill_is_test_mode

        if not pdf_fill_is_test_mode():
            return (0, 0, 0)
    except Exception:
        pass
    return (1, 0, 0)


def _pdf_text_field_wants_justify_for_instrument_line(field: Dict[str, Any]) -> bool:
    """检测仪器类占位格：回填 PDF 时用两端对齐（insert_textbox）。"""
    parts: list[str] = []
    for k in ("id", "placeholder", "originalPlaceholder", "title", "label", "fieldId", "pdfFieldId"):
        v = field.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    blob = " ".join(parts)
    if "检测仪器" in blob:
        return True
    if re.search(r"仪器\s*[1-9]\d?", blob):
        return True
    val = str(field.get("value") or "")
    if "有效期至" in val and "年" in val and "月" in val and "日" in val:
        return True
    return False


def build_filled_pdf(
    fields: List[Dict[str, Any]],
    source_pdf: Path,
    *,
    fill_font_pt: float = DEFAULT_FONT_PT,
) -> bytes:
    if not source_pdf.exists():
        raise FileNotFoundError("source pdf not found")
    doc = fitz.open(source_pdf)
    times_font_path = pick_times_font()
    check_font_path = pick_check_font()
    if not SIMSUN_FONT.exists():
        doc.close()
        raise RuntimeError(f"missing font: {SIMSUN_FONT}")
    if not check_font_path:
        doc.close()
        raise RuntimeError("missing checkmark font")

    from utils.pdf_compress import (
        compress_image_bytes_for_pdf,
        pdf_document_to_compressed_bytes,
    )

    # 每页每种字体只 register 一次，减轻 subset 前的重复嵌入
    fonts_on_page: Dict[int, set] = {}

    def _ensure_font(page: fitz.Page, page_index: int, font_name: str, font_file: Path) -> None:
        used = fonts_on_page.setdefault(page_index, set())
        if font_name in used:
            return
        page.insert_font(fontname=font_name, fontfile=str(font_file))
        used.add(font_name)

    try:
        for f in fields:
            page_index = int(f["page"]) - 1
            page = doc[page_index]
            field_type = (f.get("fieldType") or "text").lower()
            raw_rect = fitz.Rect(f["x0"], f["y0"], f["x1"], f["y1"])
            if field_type == "text" and not f.get("_syntheticQcVerdict"):
                raw_rect = ensure_min_text_rect(raw_rect)
            r = safe_inset_rect(raw_rect, 0.6)

            if field_type == "check":
                checked = bool(f.get("checked")) or str(f.get("value", "")).strip() in (
                    "1",
                    "true",
                    "True",
                    "yes",
                    "on",
                    "✓",
                )
                if checked:
                    _ensure_font(page, page_index, "F_CHECK", Path(check_font_path))
                    check_color = _pdf_check_mark_color()
                    remain = page.insert_textbox(
                        r,
                        "✓",
                        fontname="F_CHECK",
                        fontsize=10.5,
                        color=check_color,
                        align=fitz.TEXT_ALIGN_CENTER,
                        overlay=True,
                    )
                    # 某些 PDF 在 textbox 布局下不落字，回退为中心点直写。
                    if remain < 0:
                        cx = (r.x0 + r.x1) / 2.0
                        cy = (r.y0 + r.y1) / 2.0
                        pt = fitz.Point(cx - 3.2, cy + 3.2)
                        page.insert_text(
                            pt,
                            "✓",
                            fontname="F_CHECK",
                            fontsize=10.5,
                            color=check_color,
                            overlay=True,
                        )
                continue

            if field_type == "image":
                image_data = (f.get("imageData") or "").strip()
                if not image_data:
                    continue
                if image_data.startswith("data:"):
                    image_data = image_data.split(",", 1)[1]
                image_bytes = base64.b64decode(image_data)
                image_bytes = compress_image_bytes_for_pdf(
                    image_bytes,
                    box_width_pt=float(r.width),
                    box_height_pt=float(r.height),
                )
                page.insert_image(r, stream=image_bytes, keep_proportion=True, overlay=True)
                continue

            text = str(f.get("value", "")).strip()
            if not text:
                continue

            # 拟合主格等：MathJax / $$...$$ → 渲染为公式图再叠印
            try:
                from utils.mathjax_pdf_render import (
                    looks_like_mathjax_equation,
                    stamp_equation_on_page,
                )

                if looks_like_mathjax_equation(text, f):
                    text_color = _pdf_text_color_for_field(f, text)
                    if stamp_equation_on_page(page, r, text, field=f, color=text_color):
                        continue
            except Exception:
                pass

            if f.get("_syntheticQcVerdict"):
                try:
                    erase = fitz.Rect(
                        r.x0 - 1.5,
                        r.y0 - 1.0,
                        r.x1 + 1.5,
                        r.y1 + 1.0,
                    )
                    page.add_redact_annot(erase, fill=(1, 1, 1))
                    page.apply_redactions()
                except Exception:
                    pass
            use_simsun = contains_cjk(text) or not times_font_path
            font_file = SIMSUN_FONT if use_simsun else times_font_path
            font_name = "F_SIMSUN" if use_simsun else "F_TIMES"
            _ensure_font(page, page_index, font_name, Path(font_file))
            font_obj = fitz.Font(fontfile=str(font_file))
            is_instrument = _pdf_text_field_wants_justify_for_instrument_line(f)
            if _pdf_field_is_qc_verdict_slot(f):
                wrapped_text = text
                fs = fill_font_pt
            elif is_instrument:
                wrapped_text, fs = fit_instrument_text_for_box(
                    text, r, font_obj, max_font_pt=fill_font_pt
                )
            else:
                wrapped_text, fs = fit_text_for_box(
                    text, r, font_obj, max_font_pt=fill_font_pt
                )
            if is_instrument:
                # 左对齐：两端对齐会在行未撑满时把字距拉得过大
                align = fitz.TEXT_ALIGN_LEFT
            elif "\n" in wrapped_text:
                align = fitz.TEXT_ALIGN_LEFT
            else:
                align = fitz.TEXT_ALIGN_CENTER
            line_count = max(1, wrapped_text.count("\n") + 1)
            text_h = line_count * fs * 1.2
            if is_instrument:
                # 仪器栏顶对齐，避免少行时垂直居中留下大块空白
                text_rect = r
            elif text_h < r.height:
                offset_y = (r.height - text_h) / 2.0
                text_rect = fitz.Rect(r.x0, r.y0 + offset_y, r.x1, r.y1)
            else:
                text_rect = r
            text_color = _pdf_text_color_for_field(f, text)
            remain = page.insert_textbox(
                text_rect,
                wrapped_text,
                fontname=font_name,
                fontsize=fs,
                color=text_color,
                align=align,
                overlay=True,
            )
            if remain < 0:
                if is_instrument:
                    _insert_multiline_text_left(
                        page, text_rect, wrapped_text, font_name, font_obj, fs, text_color
                    )
                else:
                    try:
                        from utils.pdf_merge import _flatten_wrapped_text_for_fallback

                        flat_text = _flatten_wrapped_text_for_fallback(wrapped_text)
                    except Exception:
                        flat_text = re.sub(r"(?<=[\u4e00-\u9fff])\n(?=[\u4e00-\u9fff])", "", wrapped_text)
                        flat_text = flat_text.replace("\n", " ").strip()
                    if flat_text:
                        text_w = font_obj.text_length(flat_text, fontsize=fs)
                        cx = (text_rect.x0 + text_rect.x1) / 2.0
                        cy = (text_rect.y0 + text_rect.y1) / 2.0
                        pt = fitz.Point(cx - (text_w / 2.0), cy + (fs * 0.35))
                        page.insert_text(
                            pt,
                            flat_text,
                            fontname=font_name,
                            fontsize=fs,
                            color=text_color,
                            overlay=True,
                        )
    finally:
        try:
            out_bytes = pdf_document_to_compressed_bytes(doc, subset_fonts=True)
        except Exception:
            out = io.BytesIO()
            doc.save(out, clean=True, garbage=4, deflate=True)
            out_bytes = out.getvalue()
        doc.close()
    return out_bytes
