"""评价信息表附图：按「图所在页/组合」整幅提取，图题不入图。

DOCX：把同一图题上方（或同段/同表）的全部嵌入图按版式合成一张 PNG。
PDF：匹配图题所在页整页渲染，并裁掉图题条带。
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from lxml import etree
from PIL import Image

FIGURE_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("plan", ("后装治疗机机房平面布局及尺寸图", "平面布局及尺寸图")),
    ("section", ("后装治疗机机房剖面图", "机房剖面图")),
    ("zoning", ("本项目放射分区示意图", "放射分区示意图")),
    ("safety", ("放射治疗机房防护安全措施拟安装位置图", "防护安全措施拟安装位置图")),
    ("vent", ("后装治疗机机房通风布局图", "机房通风布局图", "通风布局图")),
    ("cable", ("放射治疗机房电缆沟、风管穿墙示意图", "电缆沟、风管穿墙示意图", "电缆沟")),
)

_CAPTION_HINT = re.compile(
    r"(示意图|平面布局|剖面图|通风布局|分区|防护安全措施|电缆沟|穿墙|布局图|尺寸图)"
)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _norm(text: str) -> str:
    return re.sub(r"[\s*`#]", "", text or "")


def _match_fig_key(text: str) -> str | None:
    n = _norm(text)
    if not n:
        return None
    for key, titles in FIGURE_SPECS:
        for title in titles:
            tn = _norm(title)
            if tn and (tn in n or n in tn):
                return key
    return None


def _emu_to_px(emu: int | str, dpi: float = 96.0) -> float:
    return float(emu) * dpi / 914400.0


def _qn(tag: str) -> str:
    from docx.oxml.ns import qn

    return qn(tag)


def _drawing_items(element: Any, rid_ok: set[str]) -> list[dict[str, Any]]:
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    items: list[dict[str, Any]] = []
    for d in list(element.findall(f".//{{{wp}}}inline")) + list(
        element.findall(f".//{{{wp}}}anchor")
    ):
        tag = etree.QName(d).localname
        extent = d.find(f"{{{wp}}}extent")
        blip = d.find(f".//{{{a}}}blip")
        if extent is None or blip is None:
            continue
        rid = blip.get(_qn("r:embed"))
        if not rid or rid not in rid_ok:
            continue
        w = _emu_to_px(extent.get("cx"))
        h = _emu_to_px(extent.get("cy"))
        x = y = 0.0
        if tag == "anchor":
            pos_h = d.find(f"{{{wp}}}positionH")
            pos_v = d.find(f"{{{wp}}}positionV")
            if pos_h is not None:
                off = pos_h.find(f"{{{wp}}}posOffset")
                if off is not None and off.text:
                    x = _emu_to_px(off.text)
            if pos_v is not None:
                off = pos_v.find(f"{{{wp}}}posOffset")
                if off is not None and off.text:
                    y = _emu_to_px(off.text)
        items.append(
            {
                "rid": rid,
                "x": x,
                "y": y,
                "w": max(1.0, w),
                "h": max(1.0, h),
                "inline": tag == "inline",
            }
        )
    return items


def _composite_items(
    items: list[dict[str, Any]],
    rid_to_blob: dict[str, bytes],
    *,
    side_by_side: bool = False,
) -> Image.Image | None:
    if not items:
        return None
    placed: list[dict[str, Any]] = []
    if side_by_side and len(items) >= 2:
        x = 0.0
        max_h = max(i["h"] for i in items)
        for im in items:
            placed.append({**im, "x": x, "y": (max_h - im["h"]) / 2.0})
            x += im["w"] + 16
    else:
        cursor_y = 0.0
        has_anchor = any(not i["inline"] for i in items)
        for im in items:
            if im["inline"] and not has_anchor:
                placed.append({**im, "x": 0.0, "y": cursor_y})
                cursor_y += im["h"] + 8
            elif im["inline"] and has_anchor:
                placed.append({**im, "x": 0.0, "y": 0.0})
            else:
                placed.append(im)

    min_x = min(p["x"] for p in placed)
    min_y = min(p["y"] for p in placed)
    max_x = max(p["x"] + p["w"] for p in placed)
    max_y = max(p["y"] + p["h"] for p in placed)
    width = max(1, int(max_x - min_x + 24))
    height = max(1, int(max_y - min_y + 24))
    canvas = Image.new("RGB", (width, height), "white")
    for p in placed:
        blob = rid_to_blob.get(p["rid"])
        if not blob:
            continue
        try:
            piece = Image.open(io.BytesIO(blob)).convert("RGB")
        except Exception:
            continue
        tw, th = max(1, int(p["w"])), max(1, int(p["h"]))
        piece = piece.resize((tw, th), Image.Resampling.LANCZOS)
        canvas.paste(piece, (int(p["x"] - min_x + 12), int(p["y"] - min_y + 12)))
    return canvas


def extract_docx_composite_figures(
    docx_path: Path,
    images_dir: Path,
) -> dict[str, str]:
    """fig_key → ``images/figpage_<key>.png``（不含图题文字）。"""
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    docx_path = Path(docx_path)
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    document = Document(str(docx_path))
    rid_to_blob: dict[str, bytes] = {}
    for rid, rel in document.part.rels.items():
        if "image" not in rel.reltype:
            continue
        rid_to_blob[rid] = rel.target_part.blob
    rid_ok = set(rid_to_blob)

    def iter_blocks():
        for child in document.element.body.iterchildren():
            if isinstance(child, CT_P):
                yield "p", Paragraph(child, document)
            elif isinstance(child, CT_Tbl):
                yield "t", Table(child, document)

    pending: list[dict[str, Any]] = []
    groups: list[tuple[str, list[dict[str, Any]], bool]] = []

    for kind, block in iter_blocks():
        if kind == "p":
            text = block.text or ""
            imgs = _drawing_items(block._p, rid_ok)
            key = _match_fig_key(text)
            if key:
                groups.append((key, pending + imgs, False))
                pending = []
            elif imgs:
                pending.extend(imgs)
        else:
            texts = [
                cell.text.strip()
                for row in block.rows
                for cell in row.cells
                if cell.text and cell.text.strip()
            ]
            text = " ".join(texts)
            imgs = _drawing_items(block._tbl, rid_ok)
            key = _match_fig_key(text)
            if key and imgs:
                groups.append((key, pending + imgs, True))
                pending = []

    mapping: dict[str, str] = {}
    for key, items, side_by_side in groups:
        if key in mapping or not items:
            continue
        img = _composite_items(items, rid_to_blob, side_by_side=side_by_side)
        if img is None:
            continue
        name = f"figpage_{key}.png"
        img.save(images_dir / name, format="PNG")
        mapping[key] = f"images/{name}"
    return mapping


def _pick_caption_bbox(
    page: Any, fig_key: str
) -> tuple[str, tuple[float, float, float, float] | None]:
    titles = dict(FIGURE_SPECS).get(fig_key, ())
    try:
        data = page.get_text("dict") or {}
    except Exception:
        data = {}
    page_h = float(page.rect.height)
    candidates: list[tuple[float, str, tuple[float, float, float, float]]] = []
    for block in data.get("blocks") or []:
        if int(block.get("type") or 0) != 0:
            continue
        bbox = block.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        parts: list[str] = []
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                t = str(span.get("text") or "")
                if t:
                    parts.append(t)
        bt = _norm("".join(parts))
        if not bt:
            continue
        matched = any(_norm(t) in bt or bt in _norm(t) for t in titles)
        if not matched:
            if not _CAPTION_HINT.search(bt):
                continue
            if not any(
                k in bt
                for k in ("示意", "布局", "剖面", "分区", "防护", "电缆", "通风", "尺寸")
            ):
                continue
        bb = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        candidates.append(((bb[1] + bb[3]) / 2.0, bt, bb))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        _y, text, bb = candidates[0]
        return text, bb
    return titles[0] if titles else "", None


def extract_pdf_page_figures(
    pdf_path: Path,
    images_dir: Path,
    *,
    dpi: float = 160.0,
) -> dict[str, str]:
    """整页渲染（仅一页一图题时），裁掉图题条带。"""
    import fitz

    pdf_path = Path(pdf_path)
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    mapping: dict[str, str] = {}
    zoom = max(1.0, float(dpi) / 72.0)
    matrix = fitz.Matrix(zoom, zoom)
    try:
        for i in range(len(doc)):
            page = doc[i]
            text = page.get_text("text") or ""
            keys_on_page = [
                k
                for k, titles in FIGURE_SPECS
                if any(_norm(t) in _norm(text) for t in titles)
            ]
            # 一页多图题时跳过，避免把两张图糊成一页
            if len(keys_on_page) != 1:
                continue
            key = keys_on_page[0]
            if key in mapping:
                continue

            page_h = float(page.rect.height)
            page_w = float(page.rect.width)
            _cap, cap_bbox = _pick_caption_bbox(page, key)
            top_cut = 0.0
            bottom_cut = page_h
            pad = 8.0
            if cap_bbox is not None:
                y0, y1 = float(cap_bbox[1]), float(cap_bbox[3])
                y_mid = (y0 + y1) / 2.0
                if y_mid < page_h * 0.35:
                    top_cut = max(top_cut, y1 + pad)
                else:
                    bottom_cut = min(bottom_cut, max(40.0, y0 - pad))
            else:
                bottom_cut = page_h * 0.92

            top_cut = max(0.0, min(top_cut, page_h * 0.2))
            bottom_cut = max(top_cut + 80.0, min(bottom_cut, page_h))
            clip = fitz.Rect(0.0, top_cut, page_w, bottom_cut)
            pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            name = f"figpage_{key}.png"
            pix.save(str(images_dir / name))
            mapping[key] = f"images/{name}"
    finally:
        doc.close()
    return mapping


def extract_eval_form_figures(
    source_path: Path,
    images_dir: Path,
    *,
    companion_pdf: Path | None = None,
) -> dict[str, str]:
    source_path = Path(source_path)
    images_dir = Path(images_dir)
    suffix = source_path.suffix.lower()
    pdf: Path | None = None
    if suffix == ".pdf":
        pdf = source_path
    elif companion_pdf and Path(companion_pdf).is_file():
        pdf = Path(companion_pdf)
    else:
        cand = source_path.with_suffix(".pdf")
        if cand.is_file():
            pdf = cand

    mapping: dict[str, str] = {}
    if pdf and pdf.is_file():
        try:
            mapping = extract_pdf_page_figures(pdf, images_dir)
        except Exception:
            mapping = {}

    if suffix in {".docx", ".doc"}:
        # DOCX 按图题分组合成，通常比重排版 PDF 更贴近「一图一页」
        docx_map = extract_docx_composite_figures(source_path, images_dir)
        mapping = {**mapping, **docx_map}

    return mapping


def apply_figure_mapping_to_markdown(markdown: str, mapping: dict[str, str]) -> str:
    """把散图/表格包图替换为合成整图 + 图题（图题仅作 Markdown 文本）。"""
    if not mapping:
        return markdown

    key_to_title = {k: titles[0] for k, titles in FIGURE_SPECS}
    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    used: set[str] = set()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Drop HTML tables that only wrap cable/vent composites
        if stripped.lower().startswith("<table"):
            block_lines = [line]
            i += 1
            while i < len(lines) and "</table>" not in block_lines[-1].lower():
                block_lines.append(lines[i])
                i += 1
            block = "\n".join(block_lines)
            key = _match_fig_key(re.sub(r"<[^>]+>", " ", block))
            if key and key in mapping:
                out.append(f"![]({mapping[key]})")
                out.append("")
                out.append(f"**{key_to_title[key]}**")
                used.add(key)
                continue
            out.extend(block_lines)
            continue

        key = _match_fig_key(stripped)
        if key and key in mapping and key not in used:
            # Remove preceding image / blank lines (caption-below layout)
            while out and (
                _MD_IMAGE_RE.fullmatch(out[-1].strip()) or not out[-1].strip()
            ):
                out.pop()
            # Skip following raw image lines
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if not s:
                    j += 1
                    continue
                if _MD_IMAGE_RE.fullmatch(s):
                    j += 1
                    continue
                break
            out.append(f"![]({mapping[key]})")
            out.append("")
            out.append(f"**{key_to_title[key]}**")
            used.add(key)
            i = j
            continue

        # Drop orphan raw images that sit right before a known caption we'll rewrite
        if _MD_IMAGE_RE.fullmatch(stripped):
            # Look ahead for caption
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines) and _match_fig_key(lines[k]) in mapping:
                i += 1
                continue

        out.append(line)
        i += 1

    for key, path in mapping.items():
        if key in used:
            continue
        out.append("")
        out.append(f"![]({path})")
        out.append("")
        out.append(f"**{key_to_title[key]}**")

    return "\n".join(out).rstrip() + "\n"
