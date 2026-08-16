# -*- coding: utf-8 -*-
"""PyMuPDF 导出体积优化：字体子集化、流压缩、插入图压缩。"""
from __future__ import annotations

import io
from typing import Any

import fitz


def subset_doc_fonts(doc: fitz.Document) -> None:
    """仅保留文档实际用到的字形，避免整包 SIMSUN.TTC（~18MB）写入 PDF。"""
    try:
        doc.subset_fonts()
    except Exception:
        pass


def pdf_document_to_compressed_bytes(doc: fitz.Document, *, subset_fonts: bool = True) -> bytes:
    """
    将打开的 Document 写成压缩 PDF 字节。

    主路径原先 ``save(clean=True, garbage=3)`` 且不 subset，现场记录/报告常达 20–30MB。
    """
    if subset_fonts:
        subset_doc_fonts(doc)
    kw: dict[str, Any] = dict(garbage=4, deflate=True, clean=True)
    try:
        return doc.tobytes(**kw, deflate_images=True, deflate_fonts=True)
    except TypeError:
        return doc.tobytes(**kw)


def compress_pdf_bytes(pdf_bytes: bytes, *, subset_fonts: bool = True) -> bytes:
    """对已有 PDF 字节做 subset + deflate（签字/后处理二次写出时复用）。"""
    if not pdf_bytes:
        return pdf_bytes
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return pdf_document_to_compressed_bytes(doc, subset_fonts=subset_fonts)
    finally:
        doc.close()


def compress_image_bytes_for_pdf(
    image_bytes: bytes,
    *,
    box_width_pt: float = 0.0,
    box_height_pt: float = 0.0,
    target_dpi: float = 150.0,
    max_edge_px: int = 1800,
    jpeg_quality: int = 78,
) -> bytes:
    """
    叠印前压缩位图：按目标框估算像素上限，转 JPEG（保留透明则用 PNG 优化）。

    失败时返回原字节，避免阻断导出。
    """
    if not image_bytes:
        return image_bytes
    try:
        from PIL import Image
    except Exception:
        return image_bytes

    try:
        im = Image.open(io.BytesIO(image_bytes))
        im.load()
    except Exception:
        return image_bytes

    try:
        has_alpha = im.mode in ("RGBA", "LA") or (
            im.mode == "P" and "transparency" in im.info
        )
        if im.mode not in ("RGB", "L", "RGBA", "LA"):
            im = im.convert("RGBA" if has_alpha else "RGB")

        w, h = im.size
        if w <= 0 or h <= 0:
            return image_bytes

        max_w = max_edge_px
        max_h = max_edge_px
        if box_width_pt > 1 and box_height_pt > 1 and target_dpi > 0:
            max_w = max(64, min(max_edge_px, int(box_width_pt * target_dpi / 72.0)))
            max_h = max(64, min(max_edge_px, int(box_height_pt * target_dpi / 72.0)))

        scale = min(1.0, max_w / float(w), max_h / float(h))
        if scale < 0.999:
            nw = max(1, int(w * scale))
            nh = max(1, int(h * scale))
            im = im.resize((nw, nh), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        if has_alpha:
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            im.save(out, format="PNG", optimize=True)
        else:
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.save(out, format="JPEG", quality=int(jpeg_quality), optimize=True)
        compressed = out.getvalue()
        # 仅在确实变小时采用
        if compressed and len(compressed) < len(image_bytes):
            return compressed
        return image_bytes
    except Exception:
        return image_bytes
