"""
报告「三、检测结果」页「主要检测仪器」表：按原始记录仪器重绘。

列宽沿用模板网格；行高按单元格折行自适应。
检定/校准三行均空时，该格居中填「/」。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

import fitz

from radiation_detection_report.generator import (
    CELL_PAD_X,
    FONT_SIZE_XIAO_SI,
    _autofit_row_height,
    _cell_lines,
    _line_leading,
    _load_font_resources,
    _text_ascent,
    _text_block_height,
)

logger = logging.getLogger(__name__)

_BORDER_W = 0.5
_BORDER_COLOR = (0.0, 0.0, 0.0)
_MIN_ROW_H = 34.0
_HEADER_TITLE_RE = re.compile(r"仪器型号名称|检定/校准信息|仪器唯一")
_LEFT_LABEL = "主要检测仪器"


@dataclass
class InstrumentTableGeom:
    page_index: int
    xs: list[float]
    header_top: float
    data_top: float
    data_bottom: float

    @property
    def x_left(self) -> float:
        return float(self.xs[0])

    @property
    def x_right(self) -> float:
        return float(self.xs[-1])


_SCOPE_QC = "qualityControl"
_SCOPE_RP = "radiationProtection"


def resolve_results_instrument_scope(
    *,
    has_radiation_protection: bool = False,
    include_qc_content: bool = True,
) -> str:
    """
    无防护结果册（front/ZK）→ slot1 质量控制；
    仅防护结果册（rp/FH，不含质控正文）→ slot2 放射防护。
    全本仍以检测结果首页仪器表对应质控仪器（防护仪器用于现场记录 slot2）。
    """
    if has_radiation_protection and not include_qc_content:
        return _SCOPE_RP
    return _SCOPE_QC


def apply_results_instrument_tables(
    doc: fitz.Document,
    *,
    source_payload: Optional[Mapping[str, Any]] = None,
    ordered_submit_payloads: Optional[Sequence[Mapping[str, Any]]] = None,
    instrument_scope: str = _SCOPE_QC,
) -> None:
    """在终稿叠印之后，用提交仪器重绘检测结果页的主要检测仪器表。"""
    if doc is None or doc.page_count <= 0:
        return
    payloads = _payloads_for_tables(source_payload, ordered_submit_payloads)
    tables = _detect_instrument_tables(doc)
    if not tables:
        return
    scope = str(instrument_scope or _SCOPE_QC).strip() or _SCOPE_QC
    fonts_base = _load_font_resources()
    for idx, geom in enumerate(reversed(tables)):
        table_i = len(tables) - 1 - idx
        if table_i >= len(payloads):
            continue
        payload = payloads[table_i]
        rows = collect_results_instrument_rows(payload, scope=scope)
        try:
            _rewrite_instrument_table(doc, geom, rows, fonts_base)
        except Exception:
            logger.exception("rewrite results instrument table failed page=%s", geom.page_index)


def _payloads_for_tables(
    source_payload: Optional[Mapping[str, Any]],
    ordered_submit_payloads: Optional[Sequence[Mapping[str, Any]]],
) -> list[dict]:
    ordered: list[dict] = []
    if isinstance(ordered_submit_payloads, Sequence):
        for item in ordered_submit_payloads:
            if isinstance(item, dict) and item:
                ordered.append(item)
    if ordered:
        return ordered
    if isinstance(source_payload, dict) and source_payload:
        return [dict(source_payload)]
    return []


def collect_results_instrument_rows(
    payload: Mapping[str, Any] | dict,
    *,
    scope: str = _SCOPE_QC,
) -> list[dict[str, Any]]:
    """从原始记录提交取出检测结果表用的仪器行（按 scope：质控/防护）。"""
    items = _instrument_items_for_scope(payload, scope)
    out: list[dict[str, Any]] = []
    for it in items:
        row = _instrument_display_row(it)
        if row is None:
            continue
        out.append(row)
    return out


def _instrument_items_for_scope(payload: Mapping[str, Any] | dict, scope: str) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    want = str(scope or _SCOPE_QC).strip() or _SCOPE_QC
    bags: list[Any] = [payload.get("instruments")]
    raw = payload.get("rawPayload")
    if isinstance(raw, dict):
        bags.append(raw.get("instruments"))

    scoped: list[dict] = []
    flat_scoped: list[dict] = []
    for bag in bags:
        if isinstance(bag, dict):
            for it in bag.get(want) or []:
                if isinstance(it, dict):
                    scoped.append(it)
        elif isinstance(bag, list):
            for it in bag:
                if not isinstance(it, dict):
                    continue
                item_scope = str(it.get("instrumentScope") or "").strip()
                if item_scope == want or (want == _SCOPE_QC and not item_scope):
                    flat_scoped.append(it)
    # 优先结构化 bags（rawPayload.instruments.{scope}），否则用扁平列表按 scope 过滤
    picked = scoped or flat_scoped
    return [it for it in picked if it.get("enabled") is not False]


def _qc_instrument_items(payload: Mapping[str, Any] | dict) -> list[dict]:
    """兼容旧调用：等同质量控制仪器。"""
    return _instrument_items_for_scope(payload, _SCOPE_QC)


def _instrument_display_row(it: dict) -> dict[str, Any] | None:
    enriched = _enrich_instrument_from_catalog(it)
    code = str(enriched.get("code") or "").strip()
    model = str(enriched.get("model") or "").strip()
    name = str(enriched.get("name") or "").strip()
    model_name = " ".join(x for x in (model, name) if x)
    if not code and not model_name:
        return None
    org = str(enriched.get("calibration_org") or "").strip()
    cert = str(enriched.get("certificate_no") or "").strip()
    until = str(enriched.get("valid_until") or "").strip()
    calib_lines: list[str] = []
    if org:
        calib_lines.append(f"检定/校准单位：{org}")
    if cert:
        calib_lines.append(f"证书编号：{cert}")
    if until:
        calib_lines.append(f"证书有效期至：{until}")
    return {
        "code": code,
        "model_name": model_name,
        "calib_lines": calib_lines,
        "calib_slash": not calib_lines,
    }


def _enrich_instrument_from_catalog(it: dict) -> dict[str, str]:
    code = str(it.get("identifier") or it.get("code") or it.get("serialNumber") or "").strip()
    name = str(
        it.get("name") or it.get("instrumentName") or it.get("instrumentType") or ""
    ).strip()
    model = str(it.get("model") or "").strip()
    org = str(it.get("calibrationOrg") or it.get("calibration_org") or "").strip()
    cert = str(it.get("certificateNo") or it.get("certificate_no") or "").strip()
    until = _format_certificate_until(
        it.get("validUntil")
        or it.get("certificateValidUntil")
        or it.get("certificate_valid_until")
    )
    inst = _lookup_instrument_catalog(it, code)
    if inst is not None:
        code = code or str(inst.code or "").strip()
        name = name or str(inst.name or "").strip()
        model = model or str(inst.model or "").strip()
        org = org or str(inst.calibration_org or "").strip()
        cert = cert or str(inst.certificate_no or "").strip()
        if not until:
            until = _format_certificate_until(inst.certificate_valid_until)
    return {
        "code": code,
        "name": name,
        "model": model,
        "calibration_org": org,
        "certificate_no": cert,
        "valid_until": until,
    }


def _lookup_instrument_catalog(it: dict, code: str):
    try:
        from apps.core.models import InstrumentCatalog
    except Exception:
        return None
    pk_raw = it.get("id") or it.get("instrumentId")
    try:
        pk = int(str(pk_raw).strip())
    except (TypeError, ValueError):
        pk = 0
    if pk > 0:
        found = InstrumentCatalog.objects.filter(pk=pk).first()
        if found is not None:
            return found
    if code:
        return InstrumentCatalog.objects.filter(code=code).first()
    return None


def _format_certificate_until(raw: object) -> str:
    if raw in (None, ""):
        return ""
    if isinstance(raw, datetime):
        raw = raw.date()
    if isinstance(raw, date):
        return f"{raw.year}年{raw.month}月{raw.day}日"
    s = str(raw).strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y}年{mo}月{d}日"
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y}年{mo}月{d}日"
    return ""


def _detect_instrument_tables(doc: fitz.Document) -> list[InstrumentTableGeom]:
    out: list[InstrumentTableGeom] = []
    in_results = False
    for pi in range(doc.page_count):
        page = doc[pi]
        compact = re.sub(r"\s+", "", page.get_text("text") or "")
        if "三、检测结果" in compact or "三.检测结果" in compact or "三．检测结果" in compact:
            in_results = True
        if not in_results:
            continue
        if "四、" in compact[:80] and "检测结果" not in compact[:40]:
            break
        geom = _detect_instrument_table_on_page(page, pi)
        if geom is not None:
            out.append(geom)
    return out


def _detect_instrument_table_on_page(page: fitz.Page, page_index: int) -> InstrumentTableGeom | None:
    title = _find_header_title_rect(page)
    if title is None:
        return None
    horiz, verts = _page_table_segments(page)
    wide = [h for h in horiz if (h[1] - h[0]) >= 300]
    if not wide:
        return None
    y_mid = (float(title.y0) + float(title.y1)) * 0.5
    header_lines = [h for h in wide if abs(h[2] - y_mid) <= 42.0]
    if len(header_lines) < 1:
        return None
    header_ys = _cluster([h[2] for h in header_lines], 1.2)
    header_ys.sort()
    # 表头上下沿：标题落入的那一行
    header_top = header_ys[0]
    header_bot = header_ys[-1]
    if header_bot - header_top < 8:
        above = [h[2] for h in wide if header_top - 50 <= h[2] < header_top - 0.8]
        below = [h[2] for h in wide if header_bot + 0.8 < h[2] <= header_bot + 50]
        if above:
            header_top = max(above)
        if below:
            header_bot = min(below)
    x0 = min(h[0] for h in header_lines)
    x1 = max(h[1] for h in header_lines)
    # 数据行横线常从「唯一性标识」列起，不穿过左侧「主要检测仪器」列
    same = [
        h[2]
        for h in wide
        if abs(h[1] - x1) <= 4.0
        and h[0] <= x0 + 40.0
        and h[2] >= header_top - 1.0
    ]
    ys = _cluster(same, 1.2)
    ys.sort()
    data_ys = [y for y in ys if y >= header_bot - 0.6]
    if len(data_ys) < 2:
        return None
    data_top = data_ys[0]
    data_bottom = data_ys[-1]
    xs = _cluster(
        [v[0] for v in verts if v[2] >= data_top + 8.0 and v[1] <= data_bottom - 8.0],
        1.5,
    )
    xs = [x for x in xs if x0 - 3.0 <= x <= x1 + 3.0]
    if len(xs) < 4:
        xs = _cluster([x0, x1] + xs, 1.5)
    if len(xs) < 4:
        return None
    if abs(xs[0] - x0) > 3:
        xs.insert(0, x0)
    if abs(xs[-1] - x1) > 3:
        xs.append(x1)
    xs = _cluster(xs, 1.5)
    if len(xs) < 4:
        return None
    return InstrumentTableGeom(
        page_index=page_index,
        xs=xs,
        header_top=header_top,
        data_top=data_top,
        data_bottom=data_bottom,
    )


def _find_header_title_rect(page: fitz.Page) -> fitz.Rect | None:
    for needle in ("仪器型号名称", "检定/校准信息", "仪器唯一"):
        hits = page.search_for(needle) or []
        if hits:
            return fitz.Rect(hits[0])
    words = page.get_text("words") or []
    blob = ""
    rects: list[fitz.Rect] = []
    for w in words:
        t = str(w[4] or "")
        blob += t
        rects.append(fitz.Rect(float(w[0]), float(w[1]), float(w[2]), float(w[3])))
        compact = re.sub(r"\s+", "", blob[-40:])
        if _HEADER_TITLE_RE.search(compact) and rects:
            u = rects[-1]
            for r in rects[-6:]:
                u |= r
            return u
    return None


def _page_table_segments(page: fitz.Page) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """提取表格横/竖线：兼容细矩形描边（re）与直线路径（l）。"""
    horiz: list[tuple[float, float, float]] = []
    verts: list[tuple[float, float, float]] = []
    for d in page.get_drawings() or []:
        for item in d.get("items") or []:
            kind = item[0] if item else ""
            if kind == "re" and len(item) >= 2:
                r = item[1]
                try:
                    x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
                except Exception:
                    continue
                w, h = abs(x1 - x0), abs(y1 - y0)
                if w >= 20 and h <= 1.6:
                    horiz.append((min(x0, x1), max(x0, x1), (y0 + y1) * 0.5))
                elif h >= 12 and w <= 1.6:
                    verts.append(((x0 + x1) * 0.5, min(y0, y1), max(y0, y1)))
            elif kind == "l" and len(item) >= 3:
                try:
                    p1, p2 = item[1], item[2]
                    x0, y0 = float(p1.x), float(p1.y)
                    x1, y1 = float(p2.x), float(p2.y)
                except Exception:
                    continue
                if abs(y1 - y0) <= 1.6 and abs(x1 - x0) >= 20:
                    horiz.append((min(x0, x1), max(x0, x1), (y0 + y1) * 0.5))
                elif abs(x1 - x0) <= 1.6 and abs(y1 - y0) >= 12:
                    verts.append(((x0 + x1) * 0.5, min(y0, y1), max(y0, y1)))
    return _merge_horiz_by_y(horiz), verts


def _merge_horiz_by_y(
    horiz: Sequence[tuple[float, float, float]], *, y_tol: float = 1.2
) -> list[tuple[float, float, float]]:
    """模板横线常被竖线切成多段，按 y 合并成整行。"""
    grouped: list[list[float]] = []
    for x0, x1, y in sorted(horiz, key=lambda t: (t[2], t[0])):
        if grouped and abs(y - grouped[-1][2]) <= y_tol:
            grouped[-1][0] = min(grouped[-1][0], x0)
            grouped[-1][1] = max(grouped[-1][1], x1)
        else:
            grouped.append([x0, x1, y])
    return [(a, b, c) for a, b, c in grouped]


def _cluster(vals: Sequence[float], tol: float) -> list[float]:
    out: list[float] = []
    for v in sorted(float(x) for x in vals):
        if not out or abs(v - out[-1]) > tol:
            out.append(v)
        else:
            out[-1] = (out[-1] * 0.4) + (v * 0.6)
    return out


def _rewrite_instrument_table(
    doc: fitz.Document,
    geom: InstrumentTableGeom,
    rows: list[dict[str, Any]],
    fonts_base,
) -> None:
    from utils.pdf_merge import (
        _merge_apply_redactions_overlay,
        _merge_overlay_erase_transparent,
        _merge_shift_page_content_down,
    )

    page = doc[geom.page_index]
    xs = list(geom.xs)
    if len(xs) == 4:
        xs = [xs[0], (xs[0] + xs[1]) * 0.35, xs[1], xs[2], xs[3]]
    if len(xs) < 5:
        return
    x_left, x_id, x_model, x_calib, x_right = xs[0], xs[1], xs[2], xs[3], xs[-1]
    display_rows = rows or [
        {"code": "", "model_name": "", "calib_lines": [], "calib_slash": True}
    ]
    heights = [
        _row_height(r, x_id, x_model, x_calib, x_right, fonts_base) for r in display_rows
    ]
    new_h = float(sum(heights))
    old_h = float(geom.data_bottom - geom.data_top)
    delta = new_h - old_h
    new_bottom = float(geom.data_top) + new_h
    if delta > 0.8:
        page = _merge_shift_page_content_down(doc, geom.page_index, geom.data_bottom, delta) or page

    # 擦到 max(旧底, 新底)：加高时清掉下移残留的竖线残段；变矮时清掉弃用行区。
    # 擦除须盖住原数据区第一行竖线（常从 data_top+0.2 起），否则 redact 因相交而整段保留。
    erase_bottom = max(float(geom.data_bottom), new_bottom) + 1.2
    erase = fitz.Rect(
        x_left - 1.0,
        float(geom.data_top) + 0.08,
        x_right + 1.0,
        erase_bottom,
    )
    if erase.height > 1.0 and erase.width > 1.0:
        _merge_overlay_erase_transparent(page, erase, pad=0.12)
        _merge_apply_redactions_overlay(page)

    song_font, times_font = _load_instrument_draw_fonts()
    y = float(geom.data_top)
    cells: list[tuple[fitz.Rect, str, str, str]] = []
    n_rows = len(display_rows)
    for i, (row, rh) in enumerate(zip(display_rows, heights)):
        y1 = y + rh
        id_rect = fitz.Rect(x_id + 1.5, y + 1.0, x_model - 1.5, y1 - 1.0)
        model_rect = fitz.Rect(x_model + 1.5, y + 1.0, x_calib - 1.5, y1 - 1.0)
        calib_rect = fitz.Rect(x_calib + 1.5, y + 1.0, x_right - 1.5, y1 - 1.0)
        cells.append(
            (id_rect, _wrap_instrument_code(row.get("code") or ""), "left", "center")
        )
        cells.append((model_rect, str(row.get("model_name") or ""), "left", "center"))
        if row.get("calib_slash"):
            cells.append((calib_rect, "/", "center", "center"))
        else:
            cells.append(
                (calib_rect, "\n".join(row.get("calib_lines") or []), "left", "top")
            )
        if i < n_rows - 1:
            _stroke_hline(page, y1, x_id, x_right)
        else:
            _stroke_hline(page, y1, x_left, x_right)
        y = y1
    new_bottom = y
    label_rect = fitz.Rect(x_left + 1.0, geom.data_top + 2.0, x_id - 1.0, new_bottom - 2.0)
    cells.append((label_rect, "\n".join(_LEFT_LABEL), "center", "center"))
    for xv in (x_left, x_id, x_model, x_calib, x_right):
        _stroke_vline(page, xv, geom.data_top, new_bottom)
    _write_cells_with_textwriter(page, cells, song_font, times_font, fonts_base)
    # 长竖线 redact 常整段保留，表底以下仍会冒出；白带盖住残段后再描底边。
    _cover_dangling_instrument_verticals(
        page,
        xs=[x_left, x_id, x_model, x_calib, x_right],
        new_bottom=new_bottom,
        fallback_bottom=erase_bottom,
    )


def _max_instrument_vert_stub_y(
    page: fitz.Page,
    xs: Sequence[float],
    *,
    below_y: float,
) -> float:
    """表列附近、延伸到 below_y 以下的竖线最远端。"""
    targets = [float(x) for x in xs]
    farthest = float(below_y)
    for d in page.get_drawings() or []:
        for item in d.get("items") or []:
            kind = item[0] if item else ""
            x = y0 = y1 = None
            if kind == "l" and len(item) >= 3:
                try:
                    p1, p2 = item[1], item[2]
                    xa, ya = float(p1.x), float(p1.y)
                    xb, yb = float(p2.x), float(p2.y)
                except Exception:
                    continue
                if abs(xa - xb) > 1.6:
                    continue
                x, y0, y1 = (xa + xb) * 0.5, min(ya, yb), max(ya, yb)
            elif kind == "re" and len(item) >= 2:
                r = item[1]
                try:
                    xa, ya, xb, yb = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
                except Exception:
                    continue
                if abs(xb - xa) > 1.6 or abs(yb - ya) < 8.0:
                    continue
                x, y0, y1 = (xa + xb) * 0.5, min(ya, yb), max(ya, yb)
            if x is None or y1 is None or y0 is None:
                continue
            if y1 <= below_y + 0.8:
                continue
            if not any(abs(x - tx) <= 1.8 for tx in targets):
                continue
            farthest = max(farthest, float(y1))
    return farthest


def _first_text_y_below(
    page: fitz.Page, *, y0: float, x0: float, x1: float
) -> float | None:
    ys: list[float] = []
    for w in page.get_text("words") or []:
        try:
            wx0, wy0, wx1 = float(w[0]), float(w[1]), float(w[2])
        except Exception:
            continue
        if wy0 < y0 + 1.0:
            continue
        if wx1 < x0 - 2.0 or wx0 > x1 + 2.0:
            continue
        ys.append(wy0)
    return min(ys) if ys else None


def _cover_dangling_instrument_verticals(
    page: fitz.Page,
    *,
    xs: Sequence[float],
    new_bottom: float,
    fallback_bottom: float,
) -> None:
    """盖住主要检测仪器表底以下多余竖线（不影响表内与下方正文）。"""
    if len(xs) < 2:
        return
    x_left = float(xs[0])
    x_right = float(xs[-1])
    stub_to = _max_instrument_vert_stub_y(page, xs, below_y=new_bottom)
    stub_to = max(stub_to, float(fallback_bottom))
    text_y = _first_text_y_below(page, y0=new_bottom, x0=x_left, x1=x_right)
    if text_y is not None:
        stub_to = min(stub_to, float(text_y) - 1.0)
    if stub_to <= new_bottom + 1.0:
        return
    cover = fitz.Rect(x_left - 1.2, float(new_bottom) + 0.35, x_right + 1.2, stub_to + 0.6)
    if cover.height < 0.8 or cover.width < 2.0:
        return
    try:
        page.draw_rect(cover, color=(1, 1, 1), fill=(1, 1, 1), width=0)
    except Exception:
        logger.exception("cover dangling instrument verticals failed")
        return
    _stroke_hline(page, float(new_bottom), x_left, x_right)


def _prefer_simsun_ttc_path() -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for rel in ("fonts/SIMSUN.TTC", "htmlpdf/fonts/SIMSUN.TTC"):
        p = root / rel
        if p.is_file():
            return str(p)
    return ""


def _load_font_file(path: str):
    if not path:
        return None
    if path.lower().endswith(".ttc"):
        for idx in (0, 1):
            try:
                return fitz.Font(fontfile=path, fontindex=idx)
            except Exception:
                continue
    try:
        return fitz.Font(fontfile=path)
    except Exception:
        return None


def _load_instrument_draw_fonts():
    """中文宋体 + 数字/英文 Times New Roman，与报告模板一致。"""
    song = None
    path = _prefer_simsun_ttc_path()
    if path:
        song = _load_font_file(path)
    if song is None:
        try:
            from utils.pdf_merge import _resolve_song_embed_font_path

            fallback = _resolve_song_embed_font_path(overlay_textbox=True)
            if fallback:
                song = _load_font_file(fallback)
        except Exception:
            song = None
    if song is None:
        song = fitz.Font("cjk")
    times = None
    try:
        from utils.pdf_merge import _resolve_times_embed_font_path

        tpath = _resolve_times_embed_font_path()
        if tpath:
            times = _load_font_file(tpath)
    except Exception:
        times = None
    if times is None:
        try:
            times = fitz.Font("times")
        except Exception:
            times = song
    return song, times


def _char_uses_times(ch: str) -> bool:
    """字母、数字及仪器编号/证书号里常见 ASCII 符号走 Times。"""
    if ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9"):
        return True
    return ch in "/-.:%+()[]"


def _font_runs(text: str) -> list[tuple[str, bool]]:
    runs: list[tuple[str, bool]] = []
    for ch in text:
        use_times = _char_uses_times(ch)
        if runs and runs[-1][1] == use_times:
            runs[-1] = (runs[-1][0] + ch, use_times)
        else:
            runs.append((ch, use_times))
    return runs


def _run_font(use_times: bool, song_font, times_font):
    if use_times and times_font is not None:
        return times_font
    return song_font


def _line_advance(line: str, song_font, times_font, fontsize: float) -> float:
    total = 0.0
    for piece, use_times in _font_runs(line):
        fo = _run_font(use_times, song_font, times_font)
        draw = piece.replace("-", "\u2010") if use_times else piece
        try:
            total += float(fo.text_length(draw, fontsize=fontsize))
        except Exception:
            total += fontsize * max(1, len(piece)) * (0.5 if use_times else 1.0)
    return total


def _write_cells_with_textwriter(
    page: fitz.Page,
    cells: list[tuple[fitz.Rect, str, str, str]],
    song_font,
    times_font,
    fonts_base,
) -> None:
    tw = fitz.TextWriter(page.rect, color=(0, 0, 0))
    fs = FONT_SIZE_XIAO_SI
    leading = _line_leading(fs)
    for rect, raw, align, valign in cells:
        s = str(raw or "")
        if not s.strip() and s != "/":
            continue
        max_w = max(8.0, float(rect.width) - 2.0)
        lines = _cell_lines(s, max_w, fonts_base, fs)
        n = max(1, len(lines))
        block_h = _text_block_height(n, fs)
        inner_h = float(rect.height)
        if valign == "top" or block_h >= inner_h - 0.5:
            y0 = float(rect.y0) + _text_ascent(fs)
        else:
            y0 = float(rect.y0) + max(0.0, (inner_h - block_h) / 2.0) + _text_ascent(fs)
        for i, line in enumerate(lines):
            if not line:
                continue
            lw = _line_advance(line, song_font, times_font, fs)
            if align == "center":
                x = float(rect.x0) + max(0.0, (float(rect.width) - lw) / 2.0)
            else:
                x = float(rect.x0) + 1.0
            y = y0 + i * leading
            for piece, use_times in _font_runs(line):
                fo = _run_font(use_times, song_font, times_font)
                draw = piece.replace("-", "\u2010") if use_times else piece
                tw.append(fitz.Point(x, y), draw, font=fo, fontsize=fs)
                try:
                    x += float(fo.text_length(draw, fontsize=fs))
                except Exception:
                    x += fs * max(1, len(piece)) * (0.5 if use_times else 1.0)
    try:
        tw.write_text(page, overlay=True)
    except Exception:
        logger.exception("write instrument table text failed")


def _wrap_instrument_code(code: str) -> str:
    """窄列「仪器编号」：在编号中的 - 处换行，避免拆开数字。"""
    s = str(code or "").strip()
    if not s or "-" not in s:
        return s
    head, tail = s.split("-", 1)
    if not head or not tail:
        return s
    return f"{head}\n-{tail}"


def _row_height(row: dict[str, Any], x_id: float, x_model: float, x_calib: float, x_right: float, fonts) -> float:
    id_w = max(8.0, x_model - x_id - 2 * CELL_PAD_X)
    model_w = max(8.0, x_calib - x_model - 2 * CELL_PAD_X)
    calib_w = max(8.0, x_right - x_calib - 2 * CELL_PAD_X)
    h_id = _autofit_row_height(
        _wrap_instrument_code(str(row.get("code") or "")),
        id_w,
        fonts,
        FONT_SIZE_XIAO_SI,
        min_height=_MIN_ROW_H,
    )
    h_model = _autofit_row_height(
        str(row.get("model_name") or ""), model_w, fonts, FONT_SIZE_XIAO_SI, min_height=_MIN_ROW_H
    )
    if row.get("calib_slash"):
        h_calib = _MIN_ROW_H
    else:
        h_calib = _autofit_row_height(
            "\n".join(row.get("calib_lines") or []),
            calib_w,
            fonts,
            FONT_SIZE_XIAO_SI,
            min_height=_MIN_ROW_H,
        )
    return max(h_id, h_model, h_calib, _MIN_ROW_H)


def _stroke_hline(page: fitz.Page, y: float, x0: float, x1: float) -> None:
    page.draw_line(
        fitz.Point(x0, y),
        fitz.Point(x1, y),
        color=_BORDER_COLOR,
        width=_BORDER_W,
    )


def _stroke_vline(page: fitz.Page, x: float, y0: float, y1: float) -> None:
    page.draw_line(
        fitz.Point(x, y0),
        fitz.Point(x, y1),
        color=_BORDER_COLOR,
        width=_BORDER_W,
    )
