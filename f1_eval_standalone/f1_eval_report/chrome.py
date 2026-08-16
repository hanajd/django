# -*- coding: utf-8 -*-
"""报告表页眉 / 页脚（正文与附件共用，宋体小五）。"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import fitz

from f1_eval_report.fonts_util import (
    PT_XIAO_WU,
    ensure_song,
    save_pdf_compressed,
    simsun_path,
    subset_doc_fonts,
)

FOOTER_FONT_SIZE = PT_XIAO_WU
HEADER_FONT_SIZE = PT_XIAO_WU
FOOTER_LEFT = "江西辐射剂量检测院有限公司  编制"

# 与 format.json 默认页边距一致（28mm）；页眉上边距 1.50cm；页脚下边距 1.75cm
MM_TO_PT = 72.0 / 25.4
CM_TO_PT = 72.0 / 2.54
DEFAULT_MARGIN_LR_PT = 28.0 * MM_TO_PT
HEADER_TOP_PT = 1.50 * CM_TO_PT
FOOTER_BOTTOM_PT = 1.75 * CM_TO_PT


def _measure_song(text: str, fontsize: float, fontfile: Optional[str]) -> float:
    """用真实字体文件测宽；get_text_length(fontname=自定义) 会严重不准。"""
    if not text:
        return 0.0
    try:
        if fontfile and os.path.isfile(fontfile):
            return float(fitz.Font(fontfile=fontfile).text_length(text, fontsize=fontsize))
    except Exception:
        pass
    try:
        return float(fitz.get_text_length(text, fontname="china-s", fontsize=fontsize))
    except Exception:
        return len(text) * fontsize * 0.55


def draw_report_header(
    page: fitz.Page,
    *,
    full_name: str,
    report_no: str,
    fontfile: Optional[str] = None,
    top: float = HEADER_TOP_PT,
    margin_left: float = DEFAULT_MARGIN_LR_PT,
    margin_right: float = DEFAULT_MARGIN_LR_PT,
) -> None:
    """
    页眉：左侧项目全称单行不换行；与右侧编号会重叠时，右侧下移一行右对齐。
    左右边距与正文设定一致；上边距默认 1.50cm。
    """
    ff = fontfile if fontfile and os.path.isfile(fontfile) else simsun_path()
    fn = ensure_song(page)
    if ff:
        try:
            page.insert_font(fontname="f1song", fontfile=ff)
            fn = "f1song"
        except Exception:
            pass
    page_w = float(page.rect.width)
    name = str(full_name or "").strip()
    no = str(report_no or "").strip()
    fs = HEADER_FONT_SIZE
    baseline1 = top + fs * 0.85
    gap = 12.0
    name_w = _measure_song(name, fs, ff)
    no_w = _measure_song(no, fs, ff)
    avail = page_w - margin_left - margin_right
    # 左侧名称右缘会侵入右侧编号区域 → 编号下移
    overlap = bool(name and no and (name_w + gap + no_w > avail))

    if name:
        page.insert_text(
            (margin_left, baseline1),
            name,
            fontname=fn,
            fontsize=fs,
            color=(0, 0, 0),
        )
    if no:
        by = baseline1 + (fs * 1.35 if overlap else 0.0)
        page.insert_text(
            (page_w - margin_right - no_w, by),
            no,
            fontname=fn,
            fontsize=fs,
            color=(0, 0, 0),
        )


def draw_report_footer(
    page: fitz.Page,
    *,
    page_no: int,
    total_pages: int,
    fontfile: Optional[str] = None,
    left_text: str = FOOTER_LEFT,
    margin_left: float = DEFAULT_MARGIN_LR_PT,
    margin_right: float = DEFAULT_MARGIN_LR_PT,
    margin_bottom: float = FOOTER_BOTTOM_PT,
) -> None:
    """左侧编制单位，右侧「第N页  共M页」；下边距默认 1.75cm，左右同正文。"""
    ff = fontfile if fontfile and os.path.isfile(fontfile) else simsun_path()
    fn = ensure_song(page)
    if ff:
        try:
            page.insert_font(fontname="f1song", fontfile=ff)
            fn = "f1song"
        except Exception:
            pass
    page_w = float(page.rect.width)
    page_h = float(page.rect.height)
    baseline = page_h - margin_bottom + FOOTER_FONT_SIZE * 0.2
    page.draw_rect(
        fitz.Rect(
            margin_left - 2,
            page_h - margin_bottom - FOOTER_FONT_SIZE,
            page_w - margin_right + 2,
            page_h - 4,
        ),
        color=(1, 1, 1),
        fill=(1, 1, 1),
        width=0,
    )
    page.insert_text(
        (margin_left, baseline),
        left_text,
        fontname=fn,
        fontsize=FOOTER_FONT_SIZE,
        color=(0, 0, 0),
    )
    right = f"第{int(page_no)}页  共{int(total_pages)}页"
    tw = _measure_song(right, FOOTER_FONT_SIZE, ff)
    page.insert_text(
        (page_w - margin_right - tw, baseline),
        right,
        fontname=fn,
        fontsize=FOOTER_FONT_SIZE,
        color=(0, 0, 0),
    )


def apply_chrome_to_pdf(
    pdf_path: str,
    *,
    full_name: str,
    report_no: str,
    start_page_no: int = 1,
    draw_header: bool = True,
    draw_footer: bool = True,
    fontfile: Optional[str] = None,
    header_top: float = HEADER_TOP_PT,
) -> Tuple[int, int]:
    doc = fitz.open(pdf_path)
    try:
        total = len(doc)
        ff = fontfile or simsun_path()
        for i in range(total):
            page = doc[i]
            if draw_header:
                draw_report_header(
                    page,
                    full_name=full_name,
                    report_no=report_no,
                    fontfile=ff,
                    top=header_top,
                )
            if draw_footer:
                draw_report_footer(
                    page,
                    page_no=start_page_no + i,
                    total_pages=total,
                    fontfile=ff,
                )
        subset_doc_fonts(doc)
        save_pdf_compressed(doc, pdf_path)
        return start_page_no, total
    finally:
        doc.close()


def apply_chrome_merged(
    pdf_path: str,
    *,
    full_name: str,
    report_no: str,
    body_start_index: int,
    fontfile: Optional[str] = None,
) -> None:
    """
    合并稿：封面空白页不加页眉页脚；正文从 body_start_index 起页脚从第1页计。
    封面前部内容页的页眉已在 cover 中写入。
    """
    doc = fitz.open(pdf_path)
    try:
        n = len(doc)
        body_pages = max(0, n - int(body_start_index))
        ff = fontfile or simsun_path()
        for i in range(n):
            if i < body_start_index:
                continue
            page = doc[i]
            draw_report_header(
                page,
                full_name=full_name,
                report_no=report_no,
                fontfile=ff,
                top=HEADER_TOP_PT,
            )
            draw_report_footer(
                page,
                page_no=i - body_start_index + 1,
                total_pages=body_pages,
                fontfile=ff,
            )
        subset_doc_fonts(doc)
        save_pdf_compressed(doc, pdf_path)
    finally:
        doc.close()
