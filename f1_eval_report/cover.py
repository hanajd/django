# -*- coding: utf-8 -*-
"""预评价报告封面/前部（基于样例 PDF 模板叠印）。"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COVER_TEMPLATE = os.path.join(PACKAGE_DIR, "base", "cover_front_template.pdf")

# 页码（0-based）：样例 8 页前部
PAGE_COVER = 0
PAGE_CERT = 2
PAGE_DECLARATION = 4
PAGE_TOC = 6
# 第 2/4/6 页（1-based）为空白背页，不加页眉页脚
BLANK_PAGE_INDICES = frozenset({1, 3, 5})

DEFAULT_EVAL_UNIT = "江西辐射剂量检测院有限公司"
DEFAULT_DECLARATION = {
    "legal_person": "张国军",
    "project_leader": "杨磊",
    "project_leader_cert": "FWJP-2022207",
    "author": "杨磊",
    "author_cert": "FWJP-2022207",
    "reviewer": "方伟东",
    "reviewer_cert": "FWJP-2022204",
    "issuer": "张国军",
    "issuer_cert": "FWJP20200077",
    "issue_year": "",
    "issue_month": "",
    "issue_day": "",
}


def _song_fontfile() -> Optional[str]:
    from f1_eval_report.fonts_util import simsun_path

    return simsun_path()


def _hei_fontfile() -> Optional[str]:
    from f1_eval_report.fonts_util import simhei_path

    return simhei_path()


def _times_fontfile() -> Optional[str]:
    from f1_eval_report.fonts_util import times_path

    return times_path()


def _insert_font(page: fitz.Page, fontfile: Optional[str]) -> str:
    from f1_eval_report.fonts_util import insert_named_font

    return insert_named_font(page, "f1song", fontfile)


def format_report_no(report_code: str) -> str:
    code = str(report_code or "").strip()
    if not code:
        return "赣检测院YP000000"
    if code.startswith("赣检测院"):
        return code
    return f"赣检测院{code}"


def export_body_pdf_filename(data: Optional[Dict[str, Any]] = None) -> str:
    """
    导出名：{编号}YP{项目名称}.pdf
    例：250075YP南昌大学第二附属医院（新建院区）医用X射线影像诊断及介入放射学建设项目.pdf
    """
    data = data or {}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    cover = data.get("cover_meta") if isinstance(data.get("cover_meta"), dict) else {}
    raw_code = str(
        cover.get("report_code") or fields.get("report_code") or default_report_code(fields)
    ).strip()
    raw_code = raw_code.replace("赣检测院", "").replace(" ", "")
    # 统一成「数字编号 + YP」：YP250075 / 250075YP / 250075 → 250075YP
    m = re.search(r"(\d{4,})", raw_code)
    num = m.group(1) if m else re.sub(r"[^0-9]", "", raw_code) or "000000"
    project = str(fields.get("project_name") or cover.get("project_name") or "").strip()
    project = re.sub(r"[\r\n\t]+", "", project)
    # Windows 文件名非法字符
    for ch in '\\/:*?"<>|':
        project = project.replace(ch, "")
    if not project:
        project = "预评价报告表"
    name = f"{num}YP{project}.pdf"
    return name


def default_report_code(fields: Optional[Dict[str, Any]] = None) -> str:
    fields = fields or {}
    existing = str(fields.get("report_code") or "").strip()
    if existing:
        return existing.replace("赣检测院", "")
    # YP + 两位年 + 四位流水（默认 0075，可编辑）
    yy = datetime.now().strftime("%y")
    return f"YP{yy}0075"


def format_report_month(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now()
    return f"{dt.year}年{dt.month}月"


def build_report_full_name(
    *,
    org_line: str,
    type_line: str,
) -> str:
    a = str(org_line or "").strip()
    b = str(type_line or "").strip()
    core = f"{a}{b}".strip()
    if not core:
        return "放射性职业病危害预评价报告表"
    if core.endswith("放射性职业病危害预评价报告表"):
        return core
    if "建设项目" in core:
        return f"{core}放射性职业病危害预评价报告表"
    return f"{core}建设项目放射性职业病危害预评价报告表"


def split_cover_project_lines(
    fields: Dict[str, Any],
) -> Tuple[str, str]:
    """第一行单位/院区名称，第二行项目类型。"""
    org_line = str(fields.get("cover_org_line") or "").strip()
    type_line = str(fields.get("cover_project_type_line") or "").strip()
    if org_line and type_line:
        return org_line, type_line

    project = str(fields.get("project_name") or "").strip()
    org = str(fields.get("org_name") or "").strip()

    # 去掉末尾「放射性…报告表」
    project = re.sub(
        r"(医用)?X\s*射线.*?预评价报告表$|放射性职业病危害预评价报告表$",
        "",
        project,
    ).strip()
    project = re.sub(r"建设项目$", "建设项目", project)

    if not org_line:
        # 优先：单位（院区）+ 其余
        m = re.match(
            r"^(.+?（[^）]*院区）)(.+)$",
            project,
        )
        if m:
            org_line = m.group(1).strip()
            type_line = type_line or m.group(2).strip()
        elif org and project.startswith(org):
            org_line = org
            rest = project[len(org) :].strip(" ，,")
            # 若项目名含（新建院区）
            m2 = re.match(r"^(（[^）]+）)(.*)$", rest)
            if m2:
                org_line = f"{org}{m2.group(1)}"
                type_line = type_line or (m2.group(2).strip() or "介入放射学和X射线影像诊断建设项目")
            else:
                type_line = type_line or (rest or "介入放射学和X射线影像诊断建设项目")
        else:
            org_line = org or project or "建设单位"
            type_line = type_line or "介入放射学和X射线影像诊断建设项目"

    if not type_line:
        type_line = "介入放射学和X射线影像诊断建设项目"
    # 归一为样例封面用语
    type_line = re.sub(r"^医用", "", type_line).strip()
    if ("介入" in type_line) and ("影像诊断" in type_line or "X射线" in type_line):
        type_line = "介入放射学和X射线影像诊断建设项目"
    elif not type_line.endswith("建设项目"):
        type_line = re.sub(r"建设项目?$", "", type_line).strip() + "建设项目"
    return org_line, type_line


def resolve_cover_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    """从 report_data 解析/补全封面元数据。"""
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    cover = data.get("cover_meta") if isinstance(data.get("cover_meta"), dict) else {}
    merged = dict(cover)

    report_code = str(
        merged.get("report_code") or fields.get("report_code") or default_report_code(fields)
    ).strip()
    report_code = report_code.replace("赣检测院", "")
    report_no = format_report_no(report_code)

    org_line, type_line = split_cover_project_lines({**fields, **merged})
    construction = str(
        merged.get("construction_unit") or fields.get("org_name") or ""
    ).strip()
    evaluation = str(
        merged.get("evaluation_unit") or DEFAULT_EVAL_UNIT
    ).strip()
    report_date = str(
        merged.get("report_date") or fields.get("report_date") or format_report_month()
    ).strip()
    full_name = str(merged.get("report_full_name") or "").strip()
    if not full_name:
        full_name = build_report_full_name(org_line=org_line, type_line=type_line)

    decl = dict(DEFAULT_DECLARATION)
    raw_decl = merged.get("declaration") if isinstance(merged.get("declaration"), dict) else {}
    decl.update({k: v for k, v in raw_decl.items() if v is not None and str(v) != ""})
    # 签发年月日默认留空供手填；若给了 issue_date 则拆分
    issue_date = str(merged.get("issue_date") or raw_decl.get("issue_date") or "").strip()
    if issue_date:
        m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", issue_date)
        if m:
            decl["issue_year"] = m.group(1)
            decl["issue_month"] = m.group(2)
            decl["issue_day"] = m.group(3)

    cert = str(
        merged.get("certificate_image")
        or data.get("cover_certificate_image")
        or ""
    ).strip()

    return {
        "report_code": report_code,
        "report_no": report_no,
        "cover_org_line": org_line,
        "cover_project_type_line": type_line,
        "construction_unit": construction or org_line,
        "evaluation_unit": evaluation,
        "report_date": report_date,
        "report_full_name": full_name,
        "certificate_image": cert,
        "declaration": decl,
        "version_label": str(merged.get("version_label") or "（C 版）"),
        "draft_label": str(merged.get("draft_label") or "（报批稿）"),
    }


def ensure_cover_meta_in_data(data: Dict[str, Any]) -> Dict[str, Any]:
    meta = resolve_cover_meta(data)
    data["cover_meta"] = meta
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    fields["report_code"] = meta["report_code"]
    fields["report_no"] = meta["report_no"]
    fields["report_full_name"] = meta["report_full_name"]
    fields["report_date"] = meta["report_date"]
    data["fields"] = fields
    return meta


def _replace_text_near(
    page: fitz.Page,
    *,
    needle: str,
    new_text: str,
    fontname: str,
    fontsize: float,
    color: Tuple[float, float, float] = (0, 0, 0),
    align: int = 0,
) -> bool:
    """在含 needle 的文本块上盖白后写新字。"""
    hits = page.search_for(needle)
    if not hits:
        # 宽松：去空格匹配
        compact = re.sub(r"\s+", "", needle)
        for b in page.get_text("dict").get("blocks") or []:
            if int(b.get("type") or 0) != 0:
                continue
            raw = ""
            for line in b.get("lines") or []:
                for sp in line.get("spans") or []:
                    raw += str(sp.get("text") or "")
            if compact and compact in re.sub(r"\s+", "", raw):
                hits = [fitz.Rect(b["bbox"])]
                break
    if not hits:
        return False
    for rect in hits:
        pad = fitz.Rect(rect.x0 - 1, rect.y0 - 1, rect.x1 + 2, rect.y1 + 2)
        page.draw_rect(pad, color=(1, 1, 1), fill=(1, 1, 1), width=0)
        page.insert_textbox(
            pad,
            new_text,
            fontname=fontname,
            fontsize=fontsize,
            color=color,
            align=align,
        )
    return True


def _draw_header(
    page: fitz.Page,
    *,
    full_name: str,
    report_no: str,
    song_file: Optional[str] = None,
) -> None:
    """页眉：与正文 chrome 一致（上边距 1.50cm，左右 28mm）。"""
    from f1_eval_report.chrome import draw_report_header
    from f1_eval_report.fonts_util import ensure_song

    page_w = float(page.rect.width)
    _cover_band(page, fitz.Rect(40, 28, page_w - 40, 78))
    _apply_redactions(page)
    if song_file:
        _insert_font(page, song_file)
    else:
        ensure_song(page)
    draw_report_header(
        page,
        full_name=full_name,
        report_no=report_no,
        fontfile=song_file,
    )


def _cover_band(page: fitz.Page, rect: fitz.Rect) -> None:
    """用红action彻底去掉模板旧字，避免叠字。"""
    page.add_redact_annot(rect, fill=(1, 1, 1))


def _apply_redactions(page: fitz.Page) -> None:
    try:
        page.apply_redactions(images=0)
    except TypeError:
        page.apply_redactions()


def _paint_cover_page1(page: fitz.Page, meta: Dict[str, Any], hei_file: Optional[str] = None) -> None:
    """封面首页：全部黑体。"""
    from f1_eval_report.fonts_util import (
        PT_COVER_META,
        PT_COVER_TITLE,
        insert_named_font,
        simhei_path,
    )

    _cover_band(page, fitz.Rect(360, 88, 560, 140))
    _cover_band(page, fitz.Rect(60, 200, 540, 300))
    _cover_band(page, fitz.Rect(120, 655, 530, 760))
    _apply_redactions(page)
    fontname = insert_named_font(page, "f1hei", hei_file or simhei_path())

    page.insert_text(
        (375, 110),
        meta["report_no"],
        fontname=fontname,
        fontsize=PT_COVER_META,
        color=(0, 0, 0),
    )
    ver = str(meta.get("version_label") or "（C 版）").strip()
    page.insert_text(
        (456, 130),
        ver,
        fontname=fontname,
        fontsize=PT_COVER_META,
        color=(0, 0, 0),
    )
    page.insert_textbox(
        fitz.Rect(70, 208, 530, 252),
        meta["cover_org_line"],
        fontname=fontname,
        fontsize=PT_COVER_TITLE,
        color=(0, 0, 0),
        align=1,
    )
    page.insert_textbox(
        fitz.Rect(70, 252, 530, 296),
        meta["cover_project_type_line"],
        fontname=fontname,
        fontsize=PT_COVER_TITLE,
        color=(0, 0, 0),
        align=1,
    )
    page.insert_text(
        (143, 684),
        f"建设单位：{meta['construction_unit']}",
        fontname=fontname,
        fontsize=PT_COVER_META,
        color=(0, 0, 0),
    )
    page.insert_text(
        (143, 715),
        f"评价单位：{meta['evaluation_unit']}",
        fontname=fontname,
        fontsize=PT_COVER_META,
        color=(0, 0, 0),
    )
    page.insert_text(
        (255, 746),
        meta["report_date"],
        fontname=fontname,
        fontsize=PT_COVER_META,
        color=(0, 0, 0),
    )


def _paint_certificate_page(
    page: fitz.Page,
    meta: Dict[str, Any],
    *,
    song_file: Optional[str],
    cert_path: str,
) -> None:
    """第三页：证书横置（逆时针 90°）+ 红色水印（沿页面右下→左上对角线居中）。"""
    import math

    from f1_eval_report.fonts_util import ensure_song, text_width

    page_w = float(page.rect.width)
    page_h = float(page.rect.height)
    margin = 48.0
    frame = fitz.Rect(margin, 78, page_w - margin, page_h - 48)
    img_rect = fitz.Rect(frame)

    uploaded = bool(cert_path and os.path.isfile(cert_path))
    if uploaded:
        _cover_band(page, fitz.Rect(0, 70, page_w, page_h))
        _apply_redactions(page)
        try:
            for img in page.get_images(full=True):
                try:
                    page.delete_image(img[0])
                except Exception:
                    pass
        except Exception:
            pass
        # 先按旋转后宽高比算出精确占位，再插入该矩形，保证水印对角线与图一致
        try:
            pix = fitz.Pixmap(cert_path)
            iw, ih = float(pix.width), float(pix.height)
            pix = None
        except Exception:
            iw, ih = frame.height, frame.width
        disp_w, disp_h = ih, iw  # 逆时针 90°：显示宽=原高，显示高=原宽
        if disp_w > 1 and disp_h > 1:
            scale = min(frame.width / disp_w, frame.height / disp_h)
            nw, nh = disp_w * scale, disp_h * scale
            img_rect = fitz.Rect(
                frame.x0 + (frame.width - nw) / 2.0,
                frame.y0 + (frame.height - nh) / 2.0,
                frame.x0 + (frame.width - nw) / 2.0 + nw,
                frame.y0 + (frame.height - nh) / 2.0 + nh,
            )
        page.insert_image(
            img_rect,
            filename=cert_path,
            keep_proportion=True,
            rotate=90,
        )
        # 以实际落入页面的图片 bbox 为准（比理论计算更稳）
        try:
            infos = page.get_image_info(xrefs=True) or []
            if infos:
                img_rect = fitz.Rect(infos[-1]["bbox"])
        except Exception:
            pass

    _draw_header(
        page,
        full_name=meta["report_full_name"],
        report_no=meta["report_no"],
        song_file=song_file,
    )
    fontname = ensure_song(page)
    song_path = song_file if song_file and os.path.isfile(song_file) else None

    code = meta.get("report_code") or ""
    watermark = f"仅限于赣检测院{code} 放射性职业病危害评价报告使用 复印无效"
    # 目标视觉：页面右下 → 左上（居中）。
    # PyMuPDF morph 会把传入方向映到「另一条」对角，故传入页面右上→左下单位向量，
    # 才能落到页面右下↔左上对角，且阅读方向为从右下到左上。
    br = fitz.Point(img_rect.x1, img_rect.y1)  # 页面右下（目标起点侧）
    tl = fitz.Point(img_rect.x0, img_rect.y0)  # 页面左上（目标终点侧）
    tr = fitz.Point(img_rect.x1, img_rect.y0)
    bl = fitz.Point(img_rect.x0, img_rect.y1)
    cx = (br.x + tl.x) / 2.0
    cy = (br.y + tl.y) / 2.0
    dx = bl.x - tr.x
    dy = bl.y - tr.y
    diag_len = math.hypot(dx, dy)
    if diag_len < 1e-6:
        diag_len = 1.0
        dx, dy = -1.0, 1.0
    ux, uy = dx / diag_len, dy / diag_len
    # 字号按目标对角长度缩放
    target_len = math.hypot(tl.x - br.x, tl.y - br.y)
    fs = 20.0
    tw = text_width(watermark, song_path, fs)
    if tw > 1 and target_len > 40 and tw > target_len * 0.90:
        fs = max(12.0, fs * (target_len * 0.90 / tw))
        tw = text_width(watermark, song_path, fs)
    mat = fitz.Matrix(ux, uy, -uy, ux, 0, 0)
    p0 = fitz.Point(cx - tw / 2.0, cy + fs * 0.35)
    try:
        page.insert_text(
            p0,
            watermark,
            fontname=fontname,
            fontsize=fs,
            color=(1, 0, 0),
            morph=(fitz.Point(cx, cy), mat),
            overlay=True,
        )
    except Exception:
        page.insert_textbox(
            fitz.Rect(img_rect.x0 + 40, cy - 30, img_rect.x1 - 40, cy + 40),
            watermark,
            fontname=fontname,
            fontsize=16,
            color=(1, 0, 0),
            align=1,
        )


def _paint_declaration_page(
    page: fitz.Page,
    meta: Dict[str, Any],
    song_file: Optional[str],
    times_file: Optional[str],
) -> None:
    """
    声明页：宋体四号；证书编号 Times New Roman 四号。

    合并：前三行 1/4；法定代表人 1/1/3；项目负责人～签发人 1/1/1/1/1；签发时间整行 1。
    """
    from f1_eval_report.fonts_util import PT_SI_HAO, ensure_song, ensure_times

    decl = meta.get("declaration") or {}
    fs = PT_SI_HAO
    h = [292.5, 343.2, 389.3, 435.4, 481.5, 527.6, 573.7, 619.8, 665.9, 712.0]
    x_left, x_lab, x_name, x_cert_lab, x_cert, x_right = (
        73.9,
        170.4,
        241.6,
        321.4,
        425.4,
        521.3,
    )
    inset = 1.5

    def _cell_band(x0: float, y0: float, x1: float, y1: float) -> None:
        _cover_band(
            page,
            fitz.Rect(x0 + inset, y0 + inset, x1 - inset, y1 - inset),
        )

    def _vcenter_baseline(y0: float, y1: float) -> float:
        return (y0 + y1) / 2.0 + fs * 0.35

    _cell_band(x_lab, h[0], x_right, h[1])
    _cell_band(x_lab, h[1], x_right, h[2])
    _cell_band(x_lab, h[2], x_right, h[3])
    _cell_band(x_lab, h[3], x_name, h[4])
    _cell_band(x_name, h[3], x_right, h[4])
    for i in range(4, 8):
        _cell_band(x_lab, h[i], x_name, h[i + 1])
        _cell_band(x_cert_lab, h[i], x_cert, h[i + 1])
    _apply_redactions(page)

    _draw_header(
        page,
        full_name=meta["report_full_name"],
        report_no=meta["report_no"],
        song_file=song_file,
    )
    song = ensure_song(page)
    times = ensure_times(page)

    page.insert_textbox(
        fitz.Rect(x_lab + 4, h[0] + 4, x_right - 4, h[1] - 4),
        meta["report_full_name"],
        fontname=song,
        fontsize=fs,
        color=(0, 0, 0),
        align=0,
    )
    page.insert_text(
        (x_lab + 4, _vcenter_baseline(h[1], h[2])),
        f"{meta['evaluation_unit']}（盖章）",
        fontname=song,
        fontsize=fs,
        color=(0, 0, 0),
    )
    no = str(meta.get("report_no") or "")
    by = _vcenter_baseline(h[2], h[3])
    if no.startswith("赣检测院"):
        page.insert_text((x_lab + 18, by), "赣检测院", fontname=song, fontsize=fs, color=(0, 0, 0))
        page.insert_text(
            (x_lab + 18 + fs * 4.2, by), no[4:], fontname=times, fontsize=fs, color=(0, 0, 0)
        )
    else:
        page.insert_text((x_lab + 18, by), no, fontname=times, fontsize=fs, color=(0, 0, 0))

    def _name_cert(row_i: int, name: str, cert: str) -> None:
        y0, y1 = h[row_i], h[row_i + 1]
        by0 = _vcenter_baseline(y0, y1)
        page.insert_text((x_lab + 14, by0), name, fontname=song, fontsize=fs, color=(0, 0, 0))
        if cert:
            page.insert_text((x_cert_lab + 8, by0), cert, fontname=times, fontsize=fs, color=(0, 0, 0))

    _name_cert(3, str(decl.get("legal_person") or "张国军"), "")
    _name_cert(4, str(decl.get("project_leader") or "杨磊"), str(decl.get("project_leader_cert") or "FWJP-2022207"))
    _name_cert(5, str(decl.get("author") or "杨磊"), str(decl.get("author_cert") or "FWJP-2022207"))
    _name_cert(6, str(decl.get("reviewer") or "方伟东"), str(decl.get("reviewer_cert") or "FWJP-2022204"))
    _name_cert(7, str(decl.get("issuer") or "张国军"), str(decl.get("issuer_cert") or "FWJP20200077"))

    # 按模板区段重绘，禁止把内部竖线拉满全表
    def _vline(x: float, y0: float, y1: float) -> None:
        page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=0.6)

    def _hline(y: float) -> None:
        page.draw_line((x_left, y), (x_right, y), color=(0, 0, 0), width=0.6)

    for y in h:
        _hline(y)
    _vline(x_left, h[0], h[-1])
    _vline(x_right, h[0], h[-1])
    _vline(x_lab, h[0], h[8])  # 前八行表头列；末行签发时间无此线
    _vline(x_name, h[3], h[8])  # 法人起姓名右缘
    _vline(x_cert_lab, h[4], h[8])  # 项目负责人起证书列
    _vline(x_cert, h[4], h[8])


def build_toc_entries(
    meta: Dict[str, Any],
    *,
    body_page_count: int,
    attachment_titles: Optional[List[str]] = None,
    attachment_page_starts: Optional[List[int]] = None,
) -> List[Tuple[str, int]]:
    """目录条目：（标题, 页码）。正文从第 1 页起；附件接在正文后。"""
    entries: List[Tuple[str, int]] = []
    full = str(meta.get("report_full_name") or "").strip()
    if full:
        entries.append((full, 1))
    titles = list(attachment_titles or [])
    starts = list(attachment_page_starts or [])
    base = max(1, int(body_page_count)) + 1
    for i, title in enumerate(titles):
        t = str(title or "").strip()
        if not t:
            continue
        page_no = int(starts[i]) if i < len(starts) and starts[i] else base + i
        entries.append((t, page_no))
    return entries


def _paint_toc_page(
    page: fitz.Page,
    meta: Dict[str, Any],
    *,
    song_file: Optional[str],
    entries: Optional[List[Tuple[str, int]]] = None,
) -> None:
    """第7页目录：「目录」黑体小二；标题与页码宋体小四（12pt）；点线连接末行标题与页码。"""
    from f1_eval_report.chrome import DEFAULT_MARGIN_LR_PT
    from f1_eval_report.fonts_util import (
        PT_XIAO_ER,
        PT_XIAO_SI,
        PT_XIAO_WU,
        ensure_hei,
        ensure_song,
        simhei_bold_path,
        simhei_path,
        simsun_path,
    )

    page_w = float(page.rect.width)
    ml = float(DEFAULT_MARGIN_LR_PT)
    mr = float(DEFAULT_MARGIN_LR_PT)
    _cover_band(page, fitz.Rect(40, 72, page_w - 40, 720))
    _apply_redactions(page)
    _draw_header(
        page,
        full_name=meta["report_full_name"],
        report_no=meta["report_no"],
        song_file=song_file,
    )
    song_path = song_file if song_file and os.path.isfile(song_file) else simsun_path()
    hei_path = simhei_path()
    hei_bold = simhei_bold_path() or hei_path
    song = ensure_song(page)
    hei = ensure_hei(page)
    hei_b = ensure_hei(page, bold=True)
    if song_path:
        try:
            page.insert_font(fontname="f1song", fontfile=song_path)
            song = "f1song"
        except Exception:
            pass
    if hei_path:
        try:
            page.insert_font(fontname="f1hei", fontfile=hei_path)
            hei = "f1hei"
        except Exception:
            pass
    if hei_bold:
        try:
            page.insert_font(fontname="f1heib", fontfile=hei_bold)
            hei_b = "f1heib"
        except Exception:
            hei_b = hei

    def _tw(text: str, fontsize: float, *, bold: bool = False) -> float:
        path = hei_bold if bold else song_path
        try:
            if path and os.path.isfile(path):
                return float(fitz.Font(fontfile=path).text_length(text, fontsize=fontsize))
        except Exception:
            pass
        return len(text) * fontsize * (0.9 if bold else 0.55)

    # 「目」「录」黑体小二（加粗）
    title_fs = PT_XIAO_ER
    gap = 28.0
    w_mu = _tw("目", title_fs, bold=True)
    w_lu = _tw("录", title_fs, bold=True)
    total_w = w_mu + gap + w_lu
    x_mu = (page_w - total_w) / 2.0
    y_title = 102.0
    page.insert_text((x_mu, y_title), "目", fontname=hei_b, fontsize=title_fs, color=(0, 0, 0))
    page.insert_text(
        (x_mu + w_mu + gap, y_title), "录", fontname=hei_b, fontsize=title_fs, color=(0, 0, 0)
    )

    items = entries or [(meta["report_full_name"], 1)]
    y = 145.0
    left = ml
    right = page_w - mr
    fs = PT_XIAO_SI  # 小四 = 12pt（勿用四号 14pt）
    leading = fs * 1.5
    # 页码区预留：最长约 3～4 位 + 余量
    page_reserve = max(_tw("999", fs), _tw("88", fs)) + 4.0
    title_right = right - page_reserve  # 标题与点线不得超过此线；页码右对齐到 right

    for title, page_no in items:
        title = str(title or "").strip()
        if not title:
            continue
        page_str = str(int(page_no))
        page_tw = _tw(page_str, fs)
        page_x = right - page_tw
        # 标题折行宽度：到页码左缘再留点线空隙
        max_w = max(36.0, page_x - left - 18.0)
        lines: List[str] = []
        rest = title
        while rest:
            formed = ""
            for ch in rest:
                trial = formed + ch
                if formed and _tw(trial, fs) > max_w:
                    break
                formed = trial
            if not formed:
                formed = rest[0]
            lines.append(formed)
            rest = rest[len(formed) :].lstrip()
        if not lines:
            lines = [title]

        for i, line in enumerate(lines):
            baseline = y + fs * 0.85
            page.insert_text(
                (left, baseline), line, fontname=song, fontsize=fs, color=(0, 0, 0)
            )
            if i == len(lines) - 1:
                line_w = _tw(line, fs)
                dots_x0 = left + line_w + 3.0
                dots_x1 = page_x - 3.0
                if dots_x1 > dots_x0 + 4:
                    # 逐点绘制，保证接到页码且不压字
                    step = max(3.2, _tw(".", PT_XIAO_WU) * 1.05)
                    x = dots_x0
                    while x + 1.0 < dots_x1:
                        page.insert_text(
                            (x, baseline),
                            ".",
                            fontname=song,
                            fontsize=PT_XIAO_WU,
                            color=(0, 0, 0),
                        )
                        x += step
                page.insert_text(
                    (page_x, baseline),
                    page_str,
                    fontname=song,
                    fontsize=fs,
                    color=(0, 0, 0),
                )
            y += leading
        y += 6
        if y > 700:
            break


def paint_toc_into_cover_pdf(
    cover_pdf: str,
    meta: Dict[str, Any],
    entries: List[Tuple[str, int]],
) -> None:
    """生成正文/附件后回填封面目录页。"""
    if not cover_pdf or not os.path.isfile(cover_pdf):
        return
    doc = fitz.open(cover_pdf)
    try:
        if len(doc) <= PAGE_TOC:
            return
        song = _song_fontfile()
        _paint_toc_page(doc[PAGE_TOC], meta, song_file=song, entries=entries)
        from f1_eval_report.fonts_util import save_pdf_compressed, subset_doc_fonts

        subset_doc_fonts(doc)
        save_pdf_compressed(doc, cover_pdf)
    finally:
        doc.close()


def generate_cover_pdf(
    data: Dict[str, Any],
    output_path: str,
    *,
    template_path: Optional[str] = None,
    search_dirs: Optional[List[str]] = None,
    toc_entries: Optional[List[Tuple[str, int]]] = None,
) -> str:
    """生成封面前部 PDF，返回输出路径。"""
    from f1_eval_report.fonts_util import subset_doc_fonts

    meta = ensure_cover_meta_in_data(data)
    tpl = template_path or DEFAULT_COVER_TEMPLATE
    if not os.path.isfile(tpl):
        raise FileNotFoundError(f"封面模板不存在: {tpl}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(tpl)
    song_file = _song_fontfile()
    hei_file = _hei_fontfile()
    times_file = _times_fontfile()
    try:
        _paint_cover_page1(doc[PAGE_COVER], meta, hei_file)

        cert = meta.get("certificate_image") or ""
        if cert and not os.path.isabs(cert):
            for d in search_dirs or []:
                cand = os.path.join(d, cert)
                if os.path.isfile(cand):
                    cert = cand
                    break
                cand2 = os.path.join(d, os.path.basename(cert))
                if os.path.isfile(cand2):
                    cert = cand2
                    break
        if not (cert and os.path.isfile(cert)):
            cert = ""
        _paint_certificate_page(
            doc[PAGE_CERT], meta, song_file=song_file, cert_path=cert
        )
        _paint_declaration_page(
            doc[PAGE_DECLARATION], meta, song_file, times_file
        )
        _paint_toc_page(
            doc[PAGE_TOC],
            meta,
            song_file=song_file,
            entries=toc_entries,
        )
        # 空白页（2/4/6）不加页眉；其它内容页已处理

        subset_doc_fonts(doc)
        save_kw = dict(garbage=4, deflate=True, clean=True)
        try:
            doc.save(str(out), **save_kw, deflate_images=True, deflate_fonts=True)
        except TypeError:
            doc.save(str(out), **save_kw)
    finally:
        doc.close()
    return str(out)


def merge_pdfs(paths: List[str], output_path: str) -> str:
    out = fitz.open()
    try:
        for p in paths:
            if not p or not os.path.isfile(p):
                continue
            src = fitz.open(p)
            try:
                out.insert_pdf(src)
            finally:
                src.close()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        from f1_eval_report.fonts_util import save_pdf_compressed

        save_pdf_compressed(out, output_path)
    finally:
        out.close()
    return output_path

