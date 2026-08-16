# -*- coding: utf-8 -*-
"""中文字体路径与字号（号→pt）。嵌入后请对 Document 调用 subset_fonts() 控制体积。"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import fitz

# Word 常用换算
PT_XIAO_WU = 9.0       # 小五
PT_WU_HAO = 10.5       # 五号
PT_XIAO_SI = 12.0      # 小四
PT_SI_HAO = 14.0       # 四号
PT_SAN_HAO = 16.0      # 三号
PT_XIAO_ER = 18.0      # 小二（封面大标题可略大）
PT_COVER_TITLE = 22.0  # 封面两行项目名（样例约 26，略收以防溢出）
PT_COVER_META = 16.0   # 封面编号/单位/日期（样例约 16）


def _first_existing(*paths: str) -> Optional[str]:
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def simhei_path() -> Optional[str]:
    """黑体：优先 Windows 宋黑 / 项目内字库；Linux 回退文泉驿正黑等（勿用 china-s，中英混排会错）。"""
    fonts_dir = os.path.join(os.path.dirname(__file__), "..", "radiation_detection_report", "fonts")
    return _first_existing(
        os.path.join(fonts_dir, "SIMHEI.TTF"),
        os.path.join(fonts_dir, "simhei.ttf"),
        os.path.join(fonts_dir, "SimHei.ttf"),
        os.path.join(fonts_dir, "wqy-zenhei.ttc"),
        os.path.join(fonts_dir, "WQY-ZENHEI.TTC"),
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\SIMHEI.TTF",
        r"C:\Windows\Fonts\msyh.ttc",  # 微软雅黑（无黑体时的接近黑体备选）
        r"C:\Windows\Fonts\msyhbd.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    )


def simsun_path() -> Optional[str]:
    return _first_existing(
        os.path.join(os.path.dirname(__file__), "..", "radiation_detection_report", "fonts", "SIMSUN.TTC"),
        os.path.join(os.path.dirname(__file__), "..", "radiation_detection_report", "fonts", "simsun.ttc"),
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\SimSun.ttf",
    )


def times_path() -> Optional[str]:
    return _first_existing(
        os.path.join(os.path.dirname(__file__), "..", "radiation_detection_report", "fonts", "TIMES.TTF"),
        os.path.join(os.path.dirname(__file__), "..", "radiation_detection_report", "fonts", "times.ttf"),
        r"C:\Windows\Fonts\times.ttf",
        r"C:\Windows\Fonts\timesi.ttf",
    )


def insert_named_font(page: fitz.Page, fontname: str, fontfile: Optional[str]) -> str:
    """向页面注册字体；失败则回退内置 CJK。"""
    if fontfile and os.path.isfile(fontfile):
        try:
            page.insert_font(fontname=fontname, fontfile=fontfile)
            return fontname
        except Exception:
            pass
    return "china-s" if "hei" in fontname.lower() or "song" in fontname.lower() else "Times-Roman"


def ensure_hei(page: fitz.Page) -> str:
    return insert_named_font(page, "f1hei", simhei_path())


def ensure_song(page: fitz.Page) -> str:
    return insert_named_font(page, "f1song", simsun_path())


def ensure_times(page: fitz.Page) -> str:
    return insert_named_font(page, "f1times", times_path())


def subset_doc_fonts(doc: fitz.Document) -> None:
    try:
        doc.subset_fonts()
    except Exception:
        pass


def save_pdf_compressed(doc: fitz.Document, output_path: str) -> None:
    """
    完整重写保存并启用压缩。

    PyMuPDF 默认 insert_image / 嵌字体常写入未压缩流；若不设 deflate，
    几十张 A3 图即可把 PDF 撑到数百 MB。禁止用 saveIncr 代替本函数。

    若目标路径即当前打开的文件，doc.save 会报 “must be incremental”；
    此时改用 tobytes 再写回（Windows 上亦可在文档仍打开时覆盖）。
    """
    output_path = os.path.abspath(str(output_path))
    kw = dict(garbage=4, deflate=True, clean=True)
    opened = ""
    try:
        opened = os.path.abspath(str(doc.name or ""))
    except Exception:
        opened = ""
    same_file = bool(opened and opened == output_path)

    def _tobytes() -> bytes:
        try:
            return doc.tobytes(**kw, deflate_images=True, deflate_fonts=True)
        except TypeError:
            return doc.tobytes(**kw)

    if same_file:
        data = _tobytes()
        with open(output_path, "wb") as f:
            f.write(data)
        return
    try:
        try:
            doc.save(output_path, **kw, deflate_images=True, deflate_fonts=True)
        except TypeError:
            doc.save(output_path, **kw)
    except ValueError:
        data = _tobytes()
        with open(output_path, "wb") as f:
            f.write(data)


def text_width(text: str, fontfile: Optional[str], fontsize: float) -> float:
    if not text:
        return 0.0
    try:
        if fontfile and os.path.isfile(fontfile):
            return float(fitz.Font(fontfile=fontfile).text_length(text, fontsize=fontsize))
    except Exception:
        pass
    return len(text) * fontsize * 0.55


def title_two_lines(org_line: str, type_line: str) -> Tuple[str, str]:
    """表题两行：与封面一致的单位行 + 项目类型行，并带上报告表后缀。"""
    a = str(org_line or "").strip()
    b = str(type_line or "").strip()
    if not b.endswith("放射性职业病危害预评价报告表"):
        if b.endswith("建设项目"):
            b = f"{b}放射性职业病危害预评价报告表"
        else:
            b = f"{b}建设项目放射性职业病危害预评价报告表"
    return a, b
