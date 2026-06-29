import io
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.report_template_profiles import ReportTemplateProfile

# 与 htmlpdf 一致：嵌入宋体后目录/页眉替换的字宽才接近 Word/Acrobat 导出的模板（内置 china-s 度量不同）
_TOC_EMBED_FONT_NAME = "toc_simsun_embed"

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from pypdf import PdfWriter as _PdfWriter
    from pypdf import PdfReader as _PdfReader
except ImportError:
    _PdfWriter = None
    _PdfReader = None
from PyPDF2 import PdfMerger
from PIL import Image

logger = logging.getLogger(__name__)

# 中文 Word 字号（pt，叠印用）：四号≈14pt，小四≈12pt
_MERGE_PT_SIHAO = 14.0
_MERGE_PT_XIAOSI = 12.0


def _normalize_rp_phrase_spacing(text: str) -> str:
    """去掉「防护」等固定词组内的错误空格（PDF 换行/折行有时会拆成「防 护」）。"""
    s = str(text or "")
    if not s:
        return s
    s = re.sub(r"工作场所放射防\s+护", "工作场所放射防护", s)
    s = re.sub(r"工作场所\s+放射防护", "工作场所放射防护", s)
    s = re.sub(r"放射防\s+护", "放射防护", s)
    s = re.sub(r"防\s+护检测", "防护检测", s)
    s = re.sub(r"放射防护\s+检测", "放射防护检测", s)
    s = re.sub(r"工作场所放射防护\s+检测", "工作场所放射防护检测", s)
    s = re.sub(r"防\s+护", "防护", s)
    return s.replace("\u2060", "").replace("\u200b", "")

# 报告合并封面/项目名称：优先从标题中识别的设备大类（后续版本可在此元组末尾追加新类型）。
# 正则 alternation 按「长度降序、同长按字典序」排列，避免「DR」误匹配「动态DR」等子串。
MERGED_REPORT_DEVICE_TYPE_LABELS: Tuple[str, ...] = (
    "CT",
    "DR",
    "DSA",
    "C形臂",
    "胃肠机",
    "动态DR",
    "乳腺DR",
    "口腔CBCT",
    "加速器中的CBCT",
    "直线加速器中CBCT",
    "牙科全景机",
    "口内牙片机",
)
_MERGED_DEVICE_TYPE_RE = re.compile(
    "(" + "|".join(re.escape(s) for s in sorted(MERGED_REPORT_DEVICE_TYPE_LABELS, key=lambda s: (-len(s), s))) + ")"
)

# 合并报告叠印固定框（物理页码 1-based，x0、y0、width、height；PyMuPDF 左上原点、y 向下）。
# 与首份模板 PDF 版式对齐后固化；若模板改版需同步改此处或改为 overlay 传入。
_MERGE_OVERLAY_FIXED_RECT_1BASED_XYWH: Dict[str, Tuple[int, float, float, float, float]] = {
    # 首页：报告名称（第二行）
    "cover_report_title": (1, 181.84, 479.49, 315.95, 29.87),
    # 第 3 页（一、项目基本情况）：项目名称取值、受检设备台数取值、二、评价正文
    "summary_project_name": (3, 175.3, 94.42, 344.63, 34.27),
    # 第 3 页：受检单位名称取值格（叠印前读文本，并入基本情况「项目名称」）
    "summary_inspected_org_value": (3, 175.3, 163.74, 343.88, 34.0),
    "summary_device_count": (3, 388.0, 322.0, 130.0, 16.0),
    # 二、评价正文区（y0 略下移约 14pt，避免叠字整体偏上）
    "summary_evaluation": (3, 65.58, 475.83, 453.47, 75.62),
}

# 基本情况表取值列左界（评价擦除/叠印仅作用于该列以右，避免破坏「（结果）表」等标签列）
_MERGE_BASIC_INFO_VALUE_X0_PT = 176.0
# 评价区擦除/叠字左右内缩（pt），避免 redact 吃掉表格外竖线
_MERGE_EVAL_BOX_H_INSET_PT = 3.0
# 评价正文叠印写入区左右内缩（小于擦除内缩，尽量用满表格取值列宽）
_MERGE_EVAL_WRITE_H_INSET_PT = 0.6
# DSA 等模板基本情况表右边界（与 PDF 表格线 x≈519.34 对齐）
_MERGE_BASIC_INFO_TABLE_X1_PT = 519.34
# 基本情况页「项目名称」双格写入区（由空白模板实测：左格至 f36 左界，右格至表右缘）
_MERGE_SUMMARY_PN_ORG_XYXY = (175.14, 111.57, 329.65, 142.62)
_MERGE_SUMMARY_PN_TITLE_XYXY = (329.65, 112.33, 519.34, 141.87)
# 仅「应委托方要求」「所检设备的质量控制相关参数」两行首加缩进（全角空格×2）
_MERGE_EVAL_LINE_INDENT = "\u3000\u3000"


def _resolve_report_template_profile(profile: Optional["ReportTemplateProfile"] = None):
    from apps.core.report_template_profiles import DEFAULT_REPORT_TEMPLATE_PROFILE

    return profile if profile is not None else DEFAULT_REPORT_TEMPLATE_PROFILE


def _merge_adjust_basic_info_device_count_write_rect(
    rect: Any,
    profile: Optional["ReportTemplateProfile"] = None,
) -> Any:
    """基本情况「受检设备台数」写入区平移（避免取值压单元格左线）。"""
    if not fitz or rect is None or rect.is_empty:
        return rect
    prof = _resolve_report_template_profile(profile)
    x_off = float(prof.basic_info_device_count_write_x_offset or 0.0)
    y_off = float(prof.basic_info_device_count_write_y_offset or 0.0)
    if not x_off and not y_off:
        return rect
    return fitz.Rect(
        float(rect.x0) + x_off,
        float(rect.y0) + y_off,
        float(rect.x1) + x_off,
        float(rect.y1) + y_off,
    )


def _merge_clamp_basic_info_value_rect(
    rect: Any,
    profile: Optional["ReportTemplateProfile"] = None,
) -> Any:
    """基本情况表取值列：左界/右界与模板表格对齐。"""
    if not fitz or rect is None or rect.is_empty:
        return rect
    prof = _resolve_report_template_profile(profile)
    return fitz.Rect(
        max(float(rect.x0), float(prof.basic_info_value_x0)),
        float(rect.y0),
        min(float(rect.x1), float(prof.basic_info_table_x1) - 2.0),
        float(rect.y1),
    )


def _merge_evaluation_overlay_text(
    raw: str,
    profile: Optional["ReportTemplateProfile"] = None,
) -> str:
    """
    评价叠印用正文：去掉各行误加的首空白后，仅在
    以「应委托方要求」或「所检设备的质量控制相关参数」开头的行前加固定缩进，其它行不缩进。
    CBCT 等模板 write_x0 已与「评」字对齐时须关闭缩进（``evaluation_text_indent=False``）。
    """
    t = _normalize_rp_phrase_spacing((raw or "").replace("\r\n", "\n").strip())
    if not t:
        return ""
    prof = _resolve_report_template_profile(profile)
    indent = _MERGE_EVAL_LINE_INDENT if prof.evaluation_text_indent else ""
    out_lines: List[str] = []
    for ln in t.split("\n"):
        s = ln.lstrip(" \t\u3000")
        if not s:
            out_lines.append("")
            continue
        if s.startswith("应委托方要求"):
            out_lines.append(indent + s)
        elif s.startswith("所检设备的质量控制相关参数") or s.startswith("存在参数不符合相关标准"):
            out_lines.append(indent + s)
        elif s.startswith("结果表明："):
            out_lines.append(indent + s if indent else s)
        else:
            out_lines.append(s)
    return "\n".join(out_lines)


def _merge_fixed_fitz_rect_for_page_index(spec_key: str, page_index_0based: int) -> Optional[Any]:
    """若固定配置存在且物理页与当前 0-based 页一致，返回 fitz.Rect(x0,y0,x1,y1)，否则 None。"""
    if not fitz:
        return None
    tup = _MERGE_OVERLAY_FIXED_RECT_1BASED_XYWH.get(spec_key)
    if not tup:
        return None
    p1, x, y, w, h = tup
    if int(p1) - 1 != int(page_index_0based):
        return None
    return fitz.Rect(float(x), float(y), float(x) + float(w), float(y) + float(h))


def _merge_inset_rect(rr: Any, inset: float = 1.5) -> Any:
    """叠印擦除/写字矩形内缩，避免 redact 吃掉表格外框线。"""
    if not fitz or rr is None or rr.is_empty:
        return rr
    ins = max(0.0, float(inset))
    return fitz.Rect(
        float(rr.x0) + ins,
        float(rr.y0) + ins,
        float(rr.x1) - ins,
        float(rr.y1) - ins,
    )


def _fitz_rect_from_pdf_field_rect(rect_list: Any, page_index_0based: int) -> Optional[Any]:
    if not fitz or not rect_list or len(rect_list) < 5:
        return None
    p1, x, y, w, h = int(rect_list[0]), float(rect_list[1]), float(rect_list[2]), float(rect_list[3]), float(rect_list[4])
    if int(p1) - 1 != int(page_index_0based):
        return None
    return fitz.Rect(x, y, x + w, y + h)


def _fitz_rect_from_template_field(field: dict, page_index_0based: int) -> Optional[Any]:
    """从 pdf.fields 原始 rect 或 parse_template_json 物化后的 page/x/y/w/h 取框。"""
    if not fitz or not isinstance(field, dict):
        return None
    rect_list = field.get("rect")
    if isinstance(rect_list, (list, tuple)) and len(rect_list) >= 5:
        hit = _fitz_rect_from_pdf_field_rect(rect_list, page_index_0based)
        if hit is not None and not hit.is_empty:
            return hit
    try:
        page1 = int(field.get("page") or 0)
        if page1 - 1 != int(page_index_0based):
            return None
        x = float(field["x"])
        y = float(field["y"])
        w = float(field["w"])
        h = float(field["h"])
        if w <= 0 or h <= 0:
            return None
        return fitz.Rect(x, y, x + w, y + h)
    except (TypeError, ValueError, KeyError):
        return None


def _merge_cover_title_write_rect(org_rect: Any, title_rect: Any) -> Optional[Any]:
    """封面第二行报告名称：过窄时扩展为与第一行同宽。"""
    if not fitz or title_rect is None or title_rect.is_empty:
        return title_rect
    if org_rect is None or org_rect.is_empty:
        return title_rect
    if float(title_rect.width) >= 120.0:
        return title_rect
    return fitz.Rect(
        float(org_rect.x0),
        float(title_rect.y0),
        float(org_rect.x1),
        float(title_rect.y1),
    )


def _iter_template_step_fields(parsed: Optional[dict]):
    if not isinstance(parsed, dict):
        return
    steps = parsed.get("steps")
    if not isinstance(steps, list):
        form_schema = parsed.get("form_schema")
        if isinstance(form_schema, dict):
            steps = form_schema.get("steps")
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            for field in sec.get("fields") or []:
                if isinstance(field, dict):
                    yield field


def _fitz_rect_from_step_pdf_anchor(field: dict, page_index_0based: int) -> Optional[Any]:
    if not fitz or not isinstance(field, dict):
        return None
    anchor = field.get("pdfAnchor")
    if not isinstance(anchor, dict):
        return None
    try:
        page1 = int(anchor.get("page") or 0)
        if page1 - 1 != int(page_index_0based):
            return None
        rect = anchor.get("rect")
        if not isinstance(rect, (list, tuple)) or len(rect) < 4:
            return None
        x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        if x1 <= x0 or y1 <= y0:
            w = float(rect[2]) if len(rect) > 2 else 0.0
            h = float(rect[3]) if len(rect) > 3 else 0.0
            return fitz.Rect(x0, y0, x0 + w, y0 + h)
        return fitz.Rect(x0, y0, x1, y1)
    except (TypeError, ValueError):
        return None


def _cover_semantic_keys(fid: str, flabel: str, hierarchy_key: str = "") -> set[str]:
    blob = " ".join([fid, flabel, hierarchy_key])
    keys: set[str] = set()
    if fid == "项目名称" or flabel == "项目名称":
        keys.add("cover_project_name")
    if (
        "项目名称_医院" in blob
        or "项目名称_医院名称" in blob
        or fid in ("项目名称_医院", "项目名称_医院名称")
        or flabel in ("项目名称_医院", "项目名称_医院名称")
    ):
        keys.add("cover_project_name_org")
    if (
        "项目名称_设备" in blob
        or "项目名称_设备类型" in blob
        or "项目名称_报告名称" in blob
        or fid in ("项目名称_设备", "项目名称_设备类型", "项目名称_报告名称")
        or flabel in ("项目名称_设备", "项目名称_设备类型", "项目名称_报告名称")
    ):
        keys.add("cover_project_name_title")
    if fid == "受检单位" or flabel == "受检单位" or hierarchy_key == "受检单位":
        keys.add("cover_inspected_org")
    if fid in ("报告日期", "报告时间") or flabel in ("报告日期", "报告时间"):
        keys.add("cover_report_date")
    if "报告编号" in fid or flabel == "报告编号":
        keys.add("cover_report_no")
    if fid == "检测类型" or flabel == "检测类型":
        keys.add("cover_inspection_type")
    return keys


def _summary_device_count_label_needles() -> tuple[str, ...]:
    return ("受检设备台数", "受检工作场所")


def _line_has_summary_device_count_label(line) -> bool:
    t = re.sub(r"\s+", "", _line_text(line))
    return any(n in t for n in _summary_device_count_label_needles())


def _summary_semantic_keys(fid: str, flabel: str) -> set[str]:
    keys: set[str] = set()
    blob = f"{fid} {flabel}"
    if fid == "项目名称" or flabel == "项目名称":
        keys.add("summary_project_name")
    if fid in ("医院名称1",) or flabel == "医院名称1":
        keys.add("summary_project_name_org")
    if fid in ("设备类型1",) or flabel == "设备类型1":
        keys.add("summary_project_name_title")
    if "项目名称_医院" in blob:
        keys.add("summary_project_name_org")
    if "项目名称_设备" in blob:
        keys.add("summary_project_name_title")
    # 「受检设备台数」与部分模板「受检工作场所」同行取值
    if fid in ("受检设备台数", "受检工作场所") or flabel in ("受检设备台数", "受检工作场所"):
        keys.add("summary_device_count")
    elif ("设备台数" in fid or "设备台数" in flabel) and "场所" not in blob and "所在" not in blob:
        keys.add("summary_device_count")
    if fid == "受检单位名称" or flabel == "受检单位名称":
        keys.add("summary_inspected_org_name")
    if fid in ("f1", "委托编号") or flabel == "委托编号":
        keys.add("summary_commission_no")
    if fid == "受检单位地址" or flabel == "受检单位地址":
        keys.add("summary_inspected_org_address")
    if fid == "评价" or flabel == "评价":
        keys.add("summary_evaluation")
    if fid == "检测类别" or flabel == "检测类别":
        keys.add("summary_inspection_type")
    if fid == "检测项目" or flabel == "检测项目":
        keys.add("summary_inspection_item")
    return keys


def _union_assign_overlay_rect(out: Dict[str, Any], key: str, rect: Any) -> None:
    if not fitz or not key or rect is None or rect.is_empty:
        return
    prev = out.get(key)
    if prev is None or prev.is_empty:
        out[key] = fitz.Rect(rect)
        return
    try:
        out[key] = fitz.Rect(prev) | fitz.Rect(rect)
    except Exception:
        out[key] = fitz.Rect(rect)


def _finalize_summary_template_rects(out: Dict[str, Any], *, summary_page_1based: int = 3) -> None:
    """
    基本情况页叠印框收尾：合并项目名称分栏，并补齐与合成报告一致的台数/评价定版框。
    单份报告与合成报告共用同一套物理框，避免按窄模板框或搜字误写到「设备所在场所」等栏。
    """
    if not fitz:
        return
    pi0 = int(summary_page_1based) - 1
    for spec_key, out_key in (
        ("summary_project_name", "summary_project_name"),
        ("summary_device_count", "summary_device_count"),
        ("summary_evaluation", "summary_evaluation"),
    ):
        fr = _merge_fixed_fitz_rect_for_page_index(spec_key, pi0)
        if fr is None or fr.is_empty:
            continue
        if out_key == "summary_device_count":
            # 合成报告定版宽取值格（非 f47 窄框）
            out[out_key] = fitz.Rect(fr)
        elif out_key == "summary_evaluation":
            _union_assign_overlay_rect(out, out_key, fr)
        elif out_key == "summary_project_name":
            has_split = (
                out.get("summary_project_name_org") is not None
                and out.get("summary_project_name_title") is not None
                and not out["summary_project_name_org"].is_empty
                and not out["summary_project_name_title"].is_empty
            )
            if not has_split:
                _union_assign_overlay_rect(out, out_key, fr)
        else:
            _union_assign_overlay_rect(out, out_key, fr)


def _assign_overlay_rect(out: Dict[str, Any], key: str, rect: Any) -> None:
    if key and rect is not None and not rect.is_empty and key not in out:
        out[key] = rect


def _merge_overlay_write_cover_field(
    page,
    field_rect: Any,
    text: str,
    *,
    align: int,
    preferred_fontsize: float,
    wide_threshold: float = 120.0,
) -> None:
    """封面栏位：窄框用整行居中带叠印，宽框用模板内叠印。"""
    if not (text or "").strip() or field_rect is None or field_rect.is_empty:
        return
    if float(field_rect.width) < float(wide_threshold):
        _merge_overlay_write_cover_centered_band(
            page,
            field_rect,
            text,
            align=align,
            preferred_fontsize=preferred_fontsize,
        )
    else:
        _merge_overlay_write_in_field_rect(
            page,
            field_rect,
            text,
            align=align,
            preferred_fontsize=preferred_fontsize,
        )


def _merge_cover_project_name_label_rect(page) -> Optional[Any]:
    """封面「项目名称」标签矩形，用作取值列左界。"""
    if not fitz or page is None:
        return None
    flags = 0
    for name in ("TEXT_DEHYPHENATE", "TEXT_PRESERVE_LIGATURES"):
        v = getattr(fitz, name, None)
        if isinstance(v, int):
            flags |= v
    try:
        hits = page.search_for("项目名称", flags=flags) if flags else page.search_for("项目名称")
    except Exception:
        hits = []
    if hits:
        merged = _merge_union_search_hits_first_line(hits)
        if merged is not None and not merged.is_empty:
            return merged
    r = _merge_search_label_rect_from_dict(page, "项目名称")
    if r is not None and not r.is_empty:
        return r
    return _merge_search_last_by_bottom(page, ["项目名称"])


def _merge_cover_value_x_bounds(page) -> Tuple[float, float]:
    """封面取值列左右界：左=「项目名称」标签右缘，右=页宽−2.8cm（A4 页面基础设置）。"""
    pw = float(page.rect.width)
    x1 = pw - _merge_page_right_margin(page)
    r_pn = _merge_cover_project_name_label_rect(page)
    if r_pn is not None and not r_pn.is_empty:
        x0 = float(r_pn.x1) + 1.0
    else:
        x0 = 175.0
    if x1 <= x0 + 24.0:
        x1 = pw - 20.0
    return x0, x1


def _merge_cover_page_center_x_bounds(page) -> Tuple[float, float]:
    """封面整页居中区（报告编号等），左右留固定边距。"""
    pw = float(page.rect.width)
    pad = 40.0
    return pad, pw - pad


def _merge_overlay_write_cover_centered_band(
    page,
    band_rect: Any,
    text: str,
    *,
    align: int,
    preferred_fontsize: float,
    erase_before_write: bool = True,
    bold: bool = True,
    value_column_center: bool = True,
    fontsize_floor: Optional[float] = None,
) -> None:
    """封面居中叠印：默认在「项目名称」右缘～页右边距取值列；报告编号用整页居中。"""
    if not fitz or page is None or band_rect is None or band_rect.is_empty or not (text or "").strip():
        return
    text = _normalize_rp_phrase_spacing(text)
    if value_column_center:
        x0, x1 = _merge_cover_value_x_bounds(page)
    else:
        x0, x1 = _merge_cover_page_center_x_bounds(page)
    write_rect = fitz.Rect(
        x0,
        float(band_rect.y0) - 1.0,
        x1,
        float(band_rect.y1) + 1.0,
    )
    if erase_before_write:
        _merge_overlay_erase_transparent(page, write_rect)
        _merge_apply_redactions_overlay(page)
    use_bold = bool(bold) and _cover_overlay_use_bold()
    floor_fs = float(fontsize_floor) if fontsize_floor is not None else float(preferred_fontsize)
    _merge_overlay_write_text_html_style(
        page,
        write_rect,
        text,
        align=align,
        preferred_fontsize=preferred_fontsize,
        fontsize_floor=floor_fs,
        bold=use_bold,
        valign_top=True,
    )


def _iter_template_pdf_fields(parsed: Optional[dict]) -> List[dict]:
    if not isinstance(parsed, dict):
        return []
    fields = parsed.get("fields")
    if not isinstance(fields, list):
        pdf = parsed.get("pdf")
        if isinstance(pdf, dict):
            fields = pdf.get("fields")
    return [f for f in fields if isinstance(f, dict)] if isinstance(fields, list) else []


def build_report_template_overlay_rects(
    parsed: Optional[dict],
    *,
    cover_page_1based: int = 1,
    summary_page_1based: int = 3,
    apply_fixed_summary_rects: bool = True,
) -> Dict[str, Any]:
    """从报告模板 pdf.fields / steps.pdfAnchor 解析封面与基本情况页取值框。"""
    out: Dict[str, Any] = {}
    cover_pi = int(cover_page_1based) - 1
    summary_pi = int(summary_page_1based) - 1
    for f in _iter_template_pdf_fields(parsed):
        fid = str(f.get("id") or "").strip()
        flabel = str(f.get("label") or f.get("title") or "").strip()
        pid = str(f.get("pdfFieldId") or "").strip()
        page_no = int(f.get("page") or 0)
        fr_cover = _fitz_rect_from_template_field(f, cover_pi)
        fr_summary = _fitz_rect_from_template_field(f, summary_pi)
        if fr_cover is not None and not fr_cover.is_empty:
            for key in _cover_semantic_keys(fid, flabel):
                _assign_overlay_rect(out, key, fr_cover)
            if _template_field_page_1based(f) == int(cover_page_1based):
                if pid == "f1":
                    _assign_overlay_rect(out, "cover_report_no", fr_cover)
                elif pid == "f2" and fid not in ("受检单位名称", "受检单位地址") and flabel not in (
                    "受检单位名称",
                    "受检单位地址",
                ):
                    _assign_overlay_rect(out, "cover_project_name_org", fr_cover)
                elif pid == "f3" and fid not in ("受检单位地址",) and flabel != "受检单位地址":
                    _assign_overlay_rect(out, "cover_project_name_title", fr_cover)
                elif pid == "f4":
                    _assign_overlay_rect(out, "cover_report_date", fr_cover)
                elif pid == "f5" and "cover_report_date" not in out:
                    _assign_overlay_rect(out, "cover_report_date", fr_cover)
        if fr_summary is not None and not fr_summary.is_empty:
            for key in _summary_semantic_keys(fid, flabel):
                _union_assign_overlay_rect(out, key, fr_summary)

    for field in _iter_template_step_fields(parsed):
        fid = str(field.get("id") or "").strip()
        flabel = str(field.get("label") or "").strip()
        hk = str(field.get("hierarchyKey") or "").strip()
        pid = str(field.get("pdfFieldId") or "").strip()
        fr_cover = _fitz_rect_from_step_pdf_anchor(field, cover_pi)
        fr_summary = _fitz_rect_from_step_pdf_anchor(field, summary_pi)
        if fr_cover is not None and not fr_cover.is_empty:
            for key in _cover_semantic_keys(fid, flabel, hk):
                _assign_overlay_rect(out, key, fr_cover)
            if pid == "f1":
                _assign_overlay_rect(out, "cover_report_no", fr_cover)
            elif pid == "f2":
                _assign_overlay_rect(out, "cover_project_name_org", fr_cover)
            elif pid == "f3" and "cover_project_name_title" not in out:
                if hk == "受检单位" or flabel == "受检单位":
                    _assign_overlay_rect(out, "cover_inspected_org", fr_cover)
                else:
                    _assign_overlay_rect(out, "cover_project_name_title", fr_cover)
            elif pid == "f4":
                _assign_overlay_rect(out, "cover_report_date", fr_cover)
        if fr_summary is not None and not fr_summary.is_empty:
            for key in _summary_semantic_keys(fid, flabel):
                _union_assign_overlay_rect(out, key, fr_summary)
    if apply_fixed_summary_rects:
        _finalize_summary_template_rects(out, summary_page_1based=summary_page_1based)
    _reconcile_cover_f4_f5_inspected_org_vs_date(
        parsed, cover_page_1based=cover_page_1based, cover_pi=cover_pi, out=out
    )
    return out


def _template_field_page_1based(field: dict) -> int:
    rect_list = field.get("rect")
    if isinstance(rect_list, (list, tuple)) and len(rect_list) >= 1:
        try:
            return int(rect_list[0])
        except (TypeError, ValueError):
            pass
    try:
        return int(field.get("page") or 0)
    except (TypeError, ValueError):
        return 0


def _reconcile_cover_f4_f5_inspected_org_vs_date(
    parsed: Optional[dict],
    *,
    cover_page_1based: int,
    cover_pi: int,
    out: Dict[str, Any],
) -> None:
    """
    部分验收模板（如 dr-1）封面 f4=受检单位行、f5=报告日期；默认 pid 规则会把 f4 当作日期。
    """
    f4_rect = None
    f5_rect = None
    for f in _iter_template_pdf_fields(parsed):
        pid = str(f.get("pdfFieldId") or "").strip().lower()
        page_no = _template_field_page_1based(f)
        if page_no != int(cover_page_1based):
            continue
        fr = _fitz_rect_from_template_field(f, cover_pi)
        if fr is None or fr.is_empty:
            continue
        if pid == "f4":
            f4_rect = fr
        elif pid == "f5":
            f5_rect = fr
    if f4_rect is None or f5_rect is None:
        return
    if float(f5_rect.y0) > float(f4_rect.y0) + 18.0:
        out["cover_inspected_org"] = f4_rect
        out["cover_report_date"] = f5_rect


def build_summary_overlay_rects_from_template(
    parsed: Optional[dict],
    *,
    summary_page_1based: int = 3,
) -> Dict[str, Any]:
    """兼容别名：仅基本情况页字段。"""
    full = build_report_template_overlay_rects(parsed, summary_page_1based=summary_page_1based)
    return {k: v for k, v in full.items() if k.startswith("summary_")}


def _merge_overlay_write_in_field_rect(
    page,
    field_rect: Any,
    text: str,
    *,
    align: int,
    preferred_fontsize: float,
    paragraph_layout: bool = False,
) -> None:
    if not text or field_rect is None or field_rect.is_empty:
        return
    band = _merge_inset_rect(field_rect, 1.0)
    if band is None or band.is_empty:
        return
    _merge_overlay_erase_transparent(page, band, pad=0.12)
    _merge_apply_redactions_overlay(page)
    _merge_overlay_write_text_html_style(
        page,
        band,
        text,
        align=align,
        preferred_fontsize=preferred_fontsize,
        paragraph_layout=paragraph_layout,
        fontsize_floor=preferred_fontsize,
    )


def _merge_basic_info_value_cell_rect_single_row(
    page,
    label_rect: Any,
    *,
    template_field_rect: Optional[Any] = None,
) -> Any:
    """基本情况表取值格：优先模板 field rect，否则仅标签同行右侧单行高度。"""
    if not fitz or label_rect is None or label_rect.is_empty:
        return fitz.Rect(0, 0, 0, 0)
    if template_field_rect is not None and not template_field_rect.is_empty:
        return _merge_inset_rect(template_field_rect, 1.2)
    x0 = float(label_rect.x1) + 0.8
    pw = float(page.rect.width)
    mr = _merge_page_right_margin(page)
    return fitz.Rect(x0, float(label_rect.y0) + 1.0, pw - mr, float(label_rect.y1) - 1.0)


def _merge_read_summary_inspected_org_from_rect(doc: Any, nh: int) -> str:
    """
    从基本情况页固定矩形内读取「受检单位名称」印刷字（叠印前），供合并到项目名称。
    矩形见 ``summary_inspected_org_value``；页码为物理第 nh 页（0-based 索引 nh-1）。
    """
    if not fitz or doc is None or int(nh) < 3:
        return ""
    pi = int(nh) - 1
    if pi < 0 or pi >= doc.page_count:
        return ""
    tup = _MERGE_OVERLAY_FIXED_RECT_1BASED_XYWH.get("summary_inspected_org_value")
    if not tup:
        return ""
    p1, x, y, w, h = tup
    if int(p1) - 1 != pi:
        return ""
    page = doc[pi]
    r = fitz.Rect(float(x), float(y), float(x) + float(w), float(y) + float(h))
    if r.is_empty:
        return ""
    try:
        raw = page.get_text("text", clip=r)
    except Exception:
        try:
            raw = page.get_text(clip=r)
        except Exception:
            return ""
    t = (raw or "").strip()
    t = re.sub(r"[\r\n]+", " ", t)
    t = re.sub(r"[ \t\u3000]+", " ", t).strip()
    t = re.sub(r"^(受检单位|单位名称|医疗机构)\s*[：:]\s*", "", t).strip()
    return t


def image_to_pdf(image_path: str, output_pdf_path: str) -> bool:
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.save(output_pdf_path, "PDF", resolution=100.0, save_all=True)
        return os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0
    except Exception as e:
        logger.error("图片转PDF失败: %s", e)
        return False


def _merge_pdfs_ghostscript(pdf_paths: List[str], output_path: str) -> Tuple[bool, str]:
    gs_bin = shutil.which("gs")
    if not gs_bin:
        return False, ""
    out_abs = os.path.abspath(output_path)
    ins = [os.path.abspath(p) for p in pdf_paths if os.path.isfile(p)]
    if not ins:
        return False, ""
    try:
        cmd = [
            gs_bin,
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-sDEVICE=pdfwrite",
            "-dPDFSETTINGS=/default",
            f"-sOutputFile={out_abs}",
        ] + ins
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if proc.returncode == 0 and os.path.isfile(out_abs) and os.path.getsize(out_abs) > 0:
            return True, ""
        return False, f"Ghostscript错误: {proc.stderr[:800]}"
    except Exception as e:
        return False, str(e)


def merge_pdfs(pdf_paths: List[str], output_path: str) -> Tuple[bool, str]:
    valid = [p for p in pdf_paths if os.path.isfile(p) and os.path.getsize(p) > 0]
    if not valid:
        return False, "无有效PDF"
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)

    if _PdfWriter and _PdfReader:
        try:
            w = _PdfWriter()
            page_count = 0
            for p in valid:
                try:
                    r = _PdfReader(p, strict=False)
                    for page in r.pages:
                        w.add_page(page)
                        page_count += 1
                except Exception:
                    pass
            if page_count > 0:
                with open(output_path, "wb") as f:
                    w.write(f)
                return True, ""
        except Exception:
            pass

    try:
        merger = PdfMerger()
        for p in valid:
            merger.append(p)
        with open(output_path, "wb") as f:
            merger.write(f)
        merger.close()
        return True, ""
    except Exception:
        pass

    ok_gs, msg_gs = _merge_pdfs_ghostscript(valid, output_path)
    if ok_gs:
        return True, "使用Ghostscript合并"
    return False, "合并失败"


def _page_looks_like_next_major_section(text: str) -> bool:
    """识别「四、…」「附录」等作为「三、检测结果」结束后的下一主节（按行首匹配，减少误伤正文）。"""
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("四、") or s.startswith("四.") or s.startswith("附录"):
            return True
    return False


def detect_sanjian_jiance_jieguo_page_range(pdf_path: str) -> Optional[Tuple[int, int]]:
    """
    在 PDF 中定位「三、检测结果」所在页至下一主节之前的半开区间 [from_page, to_page)（0-based）。
    找不到则返回 None。
    """
    if not fitz or not os.path.isfile(pdf_path):
        return None
    markers_start = ("三、检测结果", "三.检测结果", "三．检测结果")
    doc = fitz.open(pdf_path)
    try:
        start: Optional[int] = None
        for i in range(doc.page_count):
            page_text = doc[i].get_text()
            if any(m in page_text for m in markers_start):
                start = i
                break
        if start is None:
            return None
        end = int(doc.page_count)
        for j in range(start + 1, doc.page_count):
            if _page_looks_like_next_major_section(doc[j].get_text()):
                end = j
                break
        if end <= start:
            return None
        return (start, end)
    finally:
        doc.close()


def _fitz_text_width(
    text: str,
    fontname: str,
    fontsize: float,
    page: Optional[Any] = None,
) -> float:
    if not fitz or not text:
        return 0.0
    song_path = _resolve_song_embed_font_path(overlay_textbox=True) or ""
    if song_path:
        try:
            fo = fitz.Font(fontfile=song_path)
            return float(fo.text_length(text, fontsize=fontsize))
        except Exception:
            pass
    if page is not None:
        gtl = getattr(page, "get_text_length", None)
        if callable(gtl):
            try:
                return float(gtl(text, fontname=fontname, fontsize=fontsize))
            except Exception:
                pass
    for fn in (fontname, "china-s", "helv"):
        try:
            return float(fitz.get_text_length(text, fontname=fn, fontsize=fontsize))
        except Exception:
            continue
    return len(text) * fontsize * 0.55


def _text_has_cjk(s: str) -> bool:
    for ch in s or "":
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def _fitz_insert_text_line(
    page,
    x: float,
    y: float,
    text: str,
    fontsize: float = 11.0,
    fontname: str = "china-s",
    *,
    render_mode: int = 0,
    border_width: float = 0.05,
) -> None:
    if not fitz:
        return
    try:
        page.insert_text(
            (x, y),
            text,
            fontsize=fontsize,
            fontname=fontname,
            render_mode=render_mode,
            border_width=border_width,
        )
    except Exception:
        try:
            if fontname != "china-s":
                page.insert_text((x, y), text, fontsize=fontsize, fontname="china-s")
            else:
                raise
        except Exception:
            if _text_has_cjk(text):
                return
            page.insert_text((x, y), text, fontsize=fontsize, fontname="helv")


def _sanitize_report_no_for_header(raw: str) -> str:
    """
    页眉「报告编号」后只保留编号本体，去掉误带入的项目名称、设备名等（常见于 PDF 一行多字段）。
    """
    t = re.sub(r"\s+", " ", (raw or "").strip())
    if not t:
        return ""
    for sep in (
        "项目名称",
        "设备名称",
        "设备类型",
        "受检单位",
        "受检编号",
        "检测日期",
        "检测类型",
        "委托单位",
        "型号",
    ):
        if sep in t:
            t = t.split(sep)[0].strip()
    if "  " in t:
        t = t.split("  ", 1)[0].strip()
    t = t.rstrip(" ：:，,")
    return t[:88] if len(t) > 88 else t


def _extract_report_number_from_pdf(pdf_path: str) -> str:
    """从报告前几页正文中提取「报告编号：…」，供目录页眉与参考版式一致。"""
    if not fitz or not os.path.isfile(pdf_path):
        return ""
    doc = fitz.open(pdf_path)
    try:
        return _extract_report_number_from_doc(doc)
    except Exception:
        return ""
    finally:
        doc.close()


def _extract_report_number_from_doc(doc) -> str:
    if not fitz or doc is None:
        return ""
    try:
        for i in range(min(4, doc.page_count)):
            t = doc[i].get_text()
            m = re.search(r"报告编号\s*[：:]\s*([^\r\n]+?)(?:\s+第\s*\d+\s*页|$)", t)
            if m:
                return _sanitize_report_no_for_header(m.group(1))
            m2 = re.search(r"报告编号\s*[：:]\s*([^\r\n]+)", t)
            if m2:
                return _sanitize_report_no_for_header(m2.group(1))
    except Exception:
        pass
    return ""


def _extract_report_number_from_pdf_bytes(pdf_bytes: bytes) -> str:
    if not fitz or not pdf_bytes:
        return ""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return _extract_report_number_from_doc(doc)
    finally:
        doc.close()


def _redact_yixia_kongbai_on_page(page) -> int:
    """擦除单页中的「以下空白」模板占位行。"""
    if not fitz or page is None:
        return 0
    changed = 0
    for _ in range(32):
        d = page.get_text("dict") or {}
        best: Optional[Tuple[float, fitz.Rect]] = None
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                txt = _line_text(line)
                if "以下空白" not in txt:
                    continue
                bbox = _line_bbox_union(line.get("spans") or [])
                if bbox is None or bbox.is_empty:
                    continue
                y0 = float(bbox.y0)
                if best is None or y0 < best[0]:
                    best = (y0, bbox)
        if best is None:
            break
        _, bbox = best
        pad = fitz.Rect(-2.0, -2.0, 2.0, 2.0)
        _merge_overlay_erase_transparent(page, bbox + pad, pad=0.35)
        _merge_apply_redactions_overlay(page)
        changed += 1
    return changed


def redact_yixia_kongbai_except_last_page(doc) -> int:
    """
    擦除除最后一页外各页中的「以下空白」标记行（模板占位，插入内容后应去掉）。
    最后一页由 ensure_last_page_yixia_kongbai_marker 在正文结束处重写。
    """
    if not fitz or doc is None or doc.page_count <= 0:
        return 0
    last_pi = doc.page_count - 1
    changed = 0
    for pi in range(doc.page_count):
        if pi >= last_pi:
            continue
        changed += _redact_yixia_kongbai_on_page(doc[pi])
    return changed


def _line_is_page_footer_noise(text: str, *, y1: float, page_height: float) -> bool:
    """页脚页码等短行，不计入正文末尾。"""
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True
    if y1 <= page_height - 52.0:
        return False
    if re.fullmatch(r"\d{1,4}", compact):
        return True
    if len(compact) <= 16 and re.search(r"第?\s*\d+\s*页", text or ""):
        return True
    return False


def _page_report_content_bottom_y(page) -> float:
    """末页正文（文字/插图）最低 y1，不含「以下空白」与页脚页码。"""
    if not fitz or page is None:
        return 0.0
    ph = float(page.rect.height)
    bottom = 0.0
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        btype = block.get("type")
        if btype == 0:
            for line in block.get("lines", []):
                txt = _line_text(line)
                if "以下空白" in txt:
                    continue
                bbox = _line_bbox_union(line.get("spans") or [])
                if bbox is None or bbox.is_empty:
                    continue
                if _line_is_page_footer_noise(txt, y1=float(bbox.y1), page_height=ph):
                    continue
                bottom = max(bottom, float(bbox.y1))
        elif btype == 1:
            bbox = block.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                bottom = max(bottom, float(bbox[3]))
    return bottom


_REPORT_LAST_PAGE_YIXIA_KONGBAI = "————以下空白————"
_YIXIA_KONGBAI_GAP_BELOW_CONTENT_PT = 8.0


def ensure_last_page_yixia_kongbai_marker(doc) -> None:
    """在报告末页正文结束处写入「以下空白」终止符（非固定页底）。"""
    if not fitz or doc is None or doc.page_count <= 0:
        return
    page = doc[doc.page_count - 1]
    _redact_yixia_kongbai_on_page(page)

    pw = float(page.rect.width)
    ph = float(page.rect.height)
    fs = _MERGE_PT_XIAOSI
    band_h = fs * 1.35
    content_bottom = _page_report_content_bottom_y(page)
    if content_bottom <= 0.0:
        content_bottom = ph * 0.45
    y0 = content_bottom + _YIXIA_KONGBAI_GAP_BELOW_CONTENT_PT
    y1 = y0 + band_h
    max_y1 = ph - 24.0
    if y1 > max_y1:
        y0 = max(content_bottom + 4.0, max_y1 - band_h)
        y1 = y0 + band_h
    band = fitz.Rect(48.0, y0, pw - 48.0, y1)
    ac = int(getattr(fitz, "TEXT_ALIGN_CENTER", 1))
    _merge_overlay_write_text_html_style(
        page,
        band,
        _REPORT_LAST_PAGE_YIXIA_KONGBAI,
        align=ac,
        preferred_fontsize=fs,
        fontsize_floor=fs,
        valign_top=False,
    )


def _digits_for_commission_suffix6(raw: str) -> str:
    """从项目编码/委托编号类字符串中抽取数字，取末 6 位（不足左补 0）。"""
    d = "".join(re.findall(r"\d", raw or ""))
    if not d:
        return ""
    if len(d) >= 6:
        return d[-6:]
    return d.zfill(6)


def format_gan_jcy_institute_report_no(
    commission_no: str,
    *,
    has_radiation_protection: bool = False,
) -> str:
    """
    院方报告编号：赣检测院FJ-ZK{6位}（仅质控）或 赣检测院FJ-FH{6位}（含工作场所放射防护）。
    """
    kind = "FJ-FH" if has_radiation_protection else "FJ-ZK"
    suffix = _digits_for_commission_suffix6(commission_no)
    return f"赣检测院{kind}{suffix}" if suffix else f"赣检测院{kind}"


def format_cover_report_number_line(
    commission_no: str,
    *,
    has_radiation_protection: bool = False,
) -> str:
    """封面整行报告编号：报告编号：赣检测院FJ-ZK/FH{委托编号末6位}。"""
    body = format_gan_jcy_institute_report_no(
        commission_no,
        has_radiation_protection=has_radiation_protection,
    )
    if not body:
        return ""
    if body.startswith("报告编号"):
        return body
    return f"报告编号：{body}"


def build_single_report_cover_title_line(
    device_abbr: str,
    *,
    has_radiation_protection: bool = False,
    qc_with_radiation_protection: bool = False,
) -> str:
    """单份报告封面项目名称第二行（非合并报告命名）。"""
    ab = (device_abbr or "").strip() or "设备"
    if qc_with_radiation_protection:
        kind = "质量控制检测及工作场所放射防护检测"
    elif has_radiation_protection:
        kind = "工作场所放射防护检测"
    else:
        kind = "质量控制检测"
    return _normalize_rp_phrase_spacing(f"放射诊疗设备（{ab}）{kind}")


def build_single_report_toc_device_title(
    device_name: str,
    device_type: str,
    device_model: str,
) -> str:
    """目录一级章节名：{设备名称}（{设备类型}，型号：{设备型号}）。"""
    name = _normalize_rp_phrase_spacing((device_name or "").strip()) or "检测设备"
    dtype_raw = (device_type or "").strip()
    model = (device_model or "").strip()
    abbr = extract_modality_abbr_from_title(dtype_raw or name or model) or dtype_raw
    type_part = abbr or dtype_raw
    if model and type_part:
        return f"{name}（{type_part}，型号：{model}）"
    if model:
        return f"{name}（型号：{model}）"
    if type_part:
        return f"{name}（{type_part}）"
    return name


def normalize_inspection_type_for_evaluation(inspection_type: str = "") -> str:
    """评价句括号内检测类型：验收检测 / 状态检测 / 定期检测。"""
    t = (inspection_type or "").strip()
    if not t:
        return "状态检测"
    tl = t.lower()
    if t == "验收检测" or tl in {"acceptance", "验收"} or ("验收" in t and "状态" not in t):
        return "验收检测"
    if t == "状态检测" or tl in {"status", "状态"} or "状态" in t:
        return "状态检测"
    if t == "定期检测" or tl in {"periodic", "定期"} or "定期" in t:
        return "定期检测"
    return t


def format_qc_detection_for_evaluation(inspection_type: str = "") -> str:
    """评价句中「质量控制检测（验收检测|状态检测|…）」短语。"""
    kind = normalize_inspection_type_for_evaluation(inspection_type)
    return f"质量控制检测（{kind}）"


def build_single_report_evaluation_body(
    inspected_org: str,
    device_abbr: str,
    *,
    has_radiation_protection: bool = False,
    qc_with_radiation_protection: bool = False,
    has_fail: bool = False,
    inspection_type: str = "",
) -> str:
    """单份报告「二、评价」正文（与合并报告句式一致，不合格时改末句）。"""
    org = (inspected_org or "").strip()
    ab = (device_abbr or "").strip() or "设备"
    qc_phrase = format_qc_detection_for_evaluation(inspection_type)
    if qc_with_radiation_protection:
        action = f"放射诊疗设备（{ab}）进行了{qc_phrase}及工作场所放射防护检测"
    elif has_radiation_protection:
        action = f"放射诊疗设备（{ab}）进行了工作场所放射防护检测"
    else:
        action = f"放射诊疗设备（{ab}）进行了{qc_phrase}"
    if has_radiation_protection:
        if qc_with_radiation_protection:
            ok_tail = "所检设备的质量控制相关参数及工作场所放射防护检测结果均符合相关标准要求。"
            fail_tail = "存在参数或工作场所放射防护检测结果不符合相关标准。"
        else:
            ok_tail = "工作场所放射防护检测结果符合相关标准要求。"
            fail_tail = "工作场所放射防护检测结果不符合相关标准。"
    else:
        ok_tail = "所检设备的质量控制相关参数均符合相关标准要求。"
        fail_tail = "存在参数不符合相关标准。"
    tail = fail_tail if has_fail else ok_tail
    return f"应委托方要求，依据相关检测标准，对{org}{action}，结果表明：\n{tail}"


def _apply_commission_suffix_to_report_no_display(report_no: str, commission_suffix6: str) -> str:
    """
    将「报告编号」展示串中**最右侧一段连续 6 位数字**（如 FJ-ZK123456 中的 123456）
    替换为项目委托编号对应的 6 位数字；若无任何 6 位数字段则在末尾拼接 6 位。
    """
    suf = _digits_for_commission_suffix6(commission_suffix6)
    if not suf:
        return (report_no or "").strip()
    s = (report_no or "").strip()
    if not s:
        return suf
    trim = s.rstrip()
    tail = s[len(trim) :]
    matches = list(re.finditer(r"\d{6}", trim))
    if matches:
        last = matches[-1]
        return trim[: last.start()] + suf + trim[last.end() :] + tail
    return trim + suf + tail


def _toc_level1_title_from_pdf_filename(title: Optional[str], pdf_path: str) -> str:
    """目录一级行：直接取该份报告 PDF 文件名（去掉 .pdf），与合并勾选顺序一致。"""
    raw = (title or "").strip() or os.path.basename(pdf_path or "")
    base, ext = os.path.splitext(raw)
    if ext.lower() == ".pdf":
        name = base.strip()
    else:
        name = raw.strip()
    if len(name) > 118:
        return name[:115] + "…"
    return name


def _cn_ordinal_major(i: int) -> str:
    """条目序号：一、二、三、…（与参考报告目录一致）。"""
    table = "一二三四五六七八九十"
    if 0 <= i < len(table):
        return table[i]
    if i < 20:
        return "十" + (table[i - 10] if i > 10 else "")
    return str(i + 1)


def _report_body_page_from_physical_1based(physical_1based: int) -> int:
    """
    与检测院报告模板一致的正文页码：前两页为封面、声明，不计入「第 x 页」。
    物理第 1–2 页 → 无正文页码；物理第 3 页 → 报告第 1 页（一、项目基本情况）；
    物理第 4 页 → 报告第 2 页（目录）；物理第 5 页起 → 报告第 3 页起（三、检测结果等）。
    """
    return max(0, int(physical_1based) - 2)


def _report_body_total_from_physical_count(total_physical_pages: int) -> int:
    """「共 y 页」：整份 PDF 总页数减去封面与声明 2 页。"""
    return max(0, int(total_physical_pages) - 2)


def _line_bbox_union(spans: List[dict]) -> fitz.Rect:
    if not spans or not fitz:
        return fitz.Rect(0, 0, 0, 0)
    boxes: List[Tuple[float, float, float, float]] = []
    for s in spans:
        b = s.get("bbox")
        if b is not None and len(b) >= 4:
            boxes.append((float(b[0]), float(b[1]), float(b[2]), float(b[3])))
    if not boxes:
        return fitz.Rect(0, 0, 0, 0)
    x0 = min(t[0] for t in boxes)
    y0 = min(t[1] for t in boxes)
    x1 = max(t[2] for t in boxes)
    y1 = max(t[3] for t in boxes)
    return fitz.Rect(x0, y0, x1, y1)


def _line_text(line: dict) -> str:
    return "".join(str(s.get("text", "") or "") for s in line.get("spans", []))


def _toc_stripped_major_match(txt: str) -> bool:
    """目录主行：「一、…」「1、…」等（兼容 . 全角．）。"""
    s = txt.strip()
    if not s:
        return False
    if re.search(r"^[一二三四五六七八九十百]+[、.．]", s):
        return True
    if re.match(r"^\d{1,2}[、.．]", s):
        return True
    return False


def _toc_stripped_sub_match(txt: str) -> bool:
    s = txt.strip()
    return bool(re.match(r"^\d+[.．]\d+", s))


def _toc_leader_only_line(txt: str) -> bool:
    """Word 导出的点线/省略线（整行多为点号）。"""
    s = txt.strip()
    if len(s) < 8:
        return False
    allowed = set(".·…．‧•\u3000 \t")
    return all(c in allowed or c.isdigit() for c in s)


def _scale_toc_geometry(geom: Dict[str, Any], sx: float, sy: float) -> Dict[str, Any]:
    """样例页 MediaBox 与首份报告不一致时，将测量几何缩放到合并页坐标。"""
    out = dict(geom)
    s_fs = min(float(sx), float(sy))
    for k in ("header_x0", "title_x0", "entry_left_x", "sub_left_x", "title_mu_x0", "title_lu_x0"):
        if k in out and out[k] is not None:
            out[k] = float(out[k]) * sx
    if out.get("num_right_x") is not None:
        out["num_right_x"] = float(out["num_right_x"]) * sx
    for k in ("header_baseline", "title_baseline", "first_entry_baseline"):
        if k in out and out[k] is not None:
            out[k] = float(out[k]) * sy
    if out.get("line_step") is not None:
        out["line_step"] = float(out["line_step"]) * sy
    for k in ("body_fs", "header_fs", "title_fs"):
        if k in out and out[k] is not None:
            out[k] = max(6.0, float(out[k]) * s_fs)
    return out


def _scale_toc_redact_rects(rects: List[fitz.Rect], sx: float, sy: float) -> List[fitz.Rect]:
    out: List[fitz.Rect] = []
    for r in rects:
        if r.is_empty:
            continue
        out.append(fitz.Rect(r.x0 * sx, r.y0 * sy, r.x1 * sx, r.y1 * sy))
    return out


def _toc_wipe_body_band_after_title(page, w: float, h: float, geom: Dict[str, Any]) -> None:
    """
    无样例/无文本层时：在「目录」标题下方白底覆盖一条带（整宽），便于叠印新目录行。
    """
    if not fitz:
        return
    title_bl = float(geom.get("title_baseline") or 92.0)
    fs_t = float(geom.get("title_fs") or 11.0)
    y_top = title_bl + max(18.0, fs_t * 1.15)
    y_bot = max(y_top + 24.0, h - 58.0)
    x0 = max(8.0, float(geom.get("entry_left_x") or 56.0) - 16.0)
    x1 = w - 36.0
    rr = fitz.Rect(x0, y_top, x1, y_bot)
    if rr.y1 <= rr.y0 or rr.is_empty:
        return
    page.add_redact_annot(rr + fitz.Rect(-1, -1, 2, 2), fill=(1, 1, 1))
    page.apply_redactions()


def _toc_wipe_layout_redraw_zones(page, w: float, h: float, geom: Dict[str, Any]) -> None:
    """
    目录样例整页叠字时：用**整块**白底覆盖「页眉报告编号行」与「目录标题以下列表区」各一次，
    避免按行红区产生碎白条；左右留边以尽量保留样例水印。
    """
    if not fitz:
        return
    pads = fitz.Rect(-2, -2, 4, 4)
    rects: List[fitz.Rect] = []
    margin_x = max(24.0, w * 0.045)
    margin_side = max(12.0, w * 0.018)
    fs_h = float(geom.get("header_fs") or 10.5)
    hbl = float(geom.get("header_baseline") or 60.0)
    # 整行页眉常跨页宽；样例左上角「报告编号」与右侧「第 x 页」须一并盖住，避免叠字
    y0h = max(2.0, hbl - fs_h * 2.1)
    y1h = min(h - 2.0, hbl + fs_h * 0.95)
    x0h = margin_side
    x1h = w - margin_side
    rects.append(fitz.Rect(x0h, y0h, x1h, y1h))

    lm = float(geom.get("entry_left_x") or 56.0)
    title_bl = float(geom.get("title_baseline") or 90.0)
    fs_t = float(geom.get("title_fs") or 11.0)
    # 先擦掉模板原「目」「录」标题区，避免与重绘标题叠成两层
    y0_title = max(2.0, title_bl - fs_t * 2.8)
    y1_title = title_bl + fs_t * 1.6
    rt = fitz.Rect(margin_side, y0_title, w - margin_side, y1_title)
    if rt.y1 > rt.y0 + 4.0:
        rects.append(rt)
    y_top = title_bl + max(10.0, fs_t * 1.08)
    y_bot = h - 46.0
    x0b = max(margin_x, lm - 14.0)
    x1b = w - margin_x
    rb = fitz.Rect(x0b, y_top, x1b, y_bot)
    if rb.y1 > rb.y0 + 8.0:
        rects.append(rb)

    for rr in rects:
        if rr.is_empty or rr.y1 <= rr.y0:
            continue
        page.add_redact_annot(rr + pads, fill=(1, 1, 1))
    if rects:
        page.apply_redactions()


def _refine_toc_geometry_for_draw(w: float, h: float, geom: Dict[str, Any]) -> Dict[str, Any]:
    """校正测量噪声：首行基线、行距、子行缩进、页码列右缘，减少叠字与错位。"""
    g = dict(geom)
    fs = max(8.0, float(g.get("body_fs") or 10.5))
    fs_t = max(9.0, float(g.get("title_fs") or fs + 0.5))
    fs_h = max(8.0, float(g.get("header_fs") or fs))
    g["body_fs"], g["title_fs"], g["header_fs"] = fs, fs_t, fs_h

    margin_x = max(24.0, w * 0.045)
    t_bl = float(g.get("title_baseline") or 90.0)
    fe_bl = float(g.get("first_entry_baseline") or (t_bl + fs_t * 1.5))
    if fe_bl <= t_bl + fs_t * 1.02:
        fe_bl = t_bl + fs_t * 1.38 + fs * 0.28
    g["first_entry_baseline"] = min(fe_bl, h - 72.0)

    step = float(g.get("line_step") or fs * 1.62)
    g["line_step"] = max(fs * 1.34, min(step, fs * 2.12))

    lm = float(g.get("entry_left_x") or 56.0)
    g["entry_left_x"] = max(margin_x + 2.0, min(lm, w * 0.24))
    lm2 = float(g["entry_left_x"])

    sl = g.get("sub_left_x")
    sl_f = float(sl) if sl is not None else lm2 + 22.0
    g["sub_left_x"] = max(sl_f, lm2 + 18.0, min(lm2 + 34.0, w * 0.30))

    nr_f = float(g.get("num_right_x") or (w - 50.0))
    g["num_right_x"] = min(max(nr_f, lm2 + 100.0), w - 40.0)

    hx = float(g.get("header_x0") or lm2)
    g["header_x0"] = max(margin_x, min(hx, w * 0.40))

    # 合并目录「目」「录」两字分写居中：勿将 title_x0 夹到 0.42w，否则会毁掉居中与字号观感
    if not g.get("toc_title_two_chars"):
        tx = float(g.get("title_x0") or lm2)
        g["title_x0"] = max(margin_x, min(tx, w * 0.42))

    return g


def _bundled_simsun_ttc_path() -> Optional[Path]:
    """
    与 apps.core.htmlpdf_service 一致：项目内 htmlpdf/fonts/SIMSUN.TTC。
    请将 Windows 自带宋体集合复制为该文件名（用户所指的 @SIMSUN.TTC）。
    """
    try:
        from django.conf import settings

        p = Path(settings.BASE_DIR) / "htmlpdf" / "fonts" / "SIMSUN.TTC"
        if p.is_file():
            return p
    except Exception:
        pass
    p2 = Path(__file__).resolve().parent.parent / "htmlpdf" / "fonts" / "SIMSUN.TTC"
    return p2 if p2.is_file() else None


def _collect_song_font_file_candidates(*, overlay_textbox: bool = False) -> List[Path]:
    """宋体/宋体族字体候选（目录与页码重绘）；叠印 textbox 时排除 Noto CJK（无法渲染中文）。"""
    names = (
        "SIMSUN.TTC",
        "simsun.ttc",
        "SimSun.ttc",
        "SIMSUN.ttf",
        "simsun.ttf",
        "STSONG.TTF",
        "stsong.ttf",
    )
    if not overlay_textbox:
        names = names + (
            "NotoSerifCJK-Regular.ttc",
            "NotoSerifCJKsc-Regular.otf",
        )
    seen: set[str] = set()
    out: List[Path] = []
    raw = (os.environ.get("MERGED_REPORT_TOC_FONT") or "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            k = str(p.resolve())
            if k not in seen:
                seen.add(k)
                out.append(p)
        elif p.is_dir():
            for n in names:
                q = p / n
                if q.is_file():
                    k = str(q.resolve())
                    if k not in seen:
                        seen.add(k)
                        out.append(q)
    bundled = _bundled_simsun_ttc_path()
    if bundled is not None:
        k = str(bundled.resolve())
        if k not in seen:
            seen.add(k)
            out.append(bundled)
    try:
        from django.conf import settings

        d = Path(settings.BASE_DIR) / "htmlpdf" / "fonts"
        for n in names:
            q = d / n
            if q.is_file():
                k = str(q.resolve())
                if k not in seen:
                    seen.add(k)
                    out.append(q)
    except Exception:
        pass
    d2 = Path(__file__).resolve().parent.parent / "htmlpdf" / "fonts"
    for n in names:
        q = d2 / n
        if q.is_file():
            k = str(q.resolve())
            if k not in seen:
                seen.add(k)
                out.append(q)
    # 合并报告目录/页码要求宋体：勿用 AR PL UMing 等顶替（易被误认为「没用宋体」）
    if not overlay_textbox:
        for sys_p in (
            Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf"),
        ):
            if sys_p.is_file():
                k = str(sys_p.resolve())
                if k not in seen:
                    seen.add(k)
                    out.append(sys_p)
    return out


def _resolve_song_embed_font_path(*, overlay_textbox: bool = False) -> Optional[str]:
    cands = _collect_song_font_file_candidates(overlay_textbox=overlay_textbox)
    return str(cands[0]) if cands else None


def _cover_overlay_use_bold() -> bool:
    """封面取值列：仅当项目内存在可安全 textbox 的黑体/粗体时才加粗。"""
    return _resolve_bold_embed_font_path(overlay_textbox=True) is not None


def _merge_overlay_fallback_write(
    page,
    rect: fitz.Rect,
    text: str,
    *,
    fontsize: float,
    fontname: str,
    align: int,
) -> None:
    """insert_textbox 失败时回退：用已嵌入字体按 baseline 写入（居中/左对齐）。"""
    if not fitz or rect is None or rect.is_empty or not (text or "").strip():
        return
    body = (text or "").replace("\n", " ").strip()[:400]
    fs = float(fontsize)
    ac = int(getattr(fitz, "TEXT_ALIGN_CENTER", 1))
    al = int(getattr(fitz, "TEXT_ALIGN_LEFT", 0))
    if int(align) == ac:
        tw = _fitz_text_width(body, fontname, fs, page=page)
        x = float(rect.x0) + max(0.0, (float(rect.width) - tw) / 2.0)
    else:
        x = float(rect.x0) + 1.0
    y = _baseline_y_from_line(rect, fs)
    _fitz_insert_text_line(page, x, y, body, fs, fontname)


def _embed_font_file_on_page(page, fontname: str, font_path: str) -> bool:
    """
    将字体文件嵌入页面（须在 apply_redactions 之后调用）。
    TTC 优先经 fitz.Font → fontbuffer 嵌入，避免 insert_font(fontfile=*.ttc) 在部分环境失败。
    """
    if not fitz or page is None:
        return False
    path = (font_path or "").strip()
    if not path or not os.path.isfile(path):
        return False
    fname = (fontname or "").strip()
    if not fname:
        return False

    def _try(**kwargs: Any) -> bool:
        try:
            page.insert_font(fontname=fname, **kwargs)
            return True
        except Exception:
            return False

    ffont = getattr(fitz, "Font", None)
    if callable(ffont):
        attempts: List[Dict[str, Any]] = []
        if path.lower().endswith(".ttc"):
            for idx in (0, 1):
                attempts.append({"fontfile": path, "fontindex": idx})
            attempts.append({"fontfile": path})
        else:
            attempts.append({"fontfile": path})
        for kwargs in attempts:
            try:
                fn = ffont(**kwargs)
                buf = getattr(fn, "buffer", None)
                if buf and _try(fontbuffer=buf):
                    return True
            except TypeError:
                continue
            except Exception:
                continue
    if _try(fontfile=path):
        return True
    try:
        with open(path, "rb") as f:
            buf = f.read()
        if _try(fontbuffer=buf):
            return True
    except Exception:
        pass
    return False


def _register_simsun_on_page(page: fitz.Page) -> str:
    """
    嵌入宋体：优先 htmlpdf/fonts/SIMSUN.TTC（与 HTML 转 PDF 一致）。
    使用 fitz.Font 从 TTC 取子字体再 fontbuffer 嵌入；须在 apply_redactions 之后再调用。
    """
    if not fitz:
        return "china-s"
    path = _resolve_song_embed_font_path()
    if not path:
        logger.warning(
            "未找到 SIMSUN.TTC：请将宋体复制为 %s（与 HTML 转 PDF 相同路径），"
            "否则目录/页码只能使用内置 china-s",
            "htmlpdf/fonts/SIMSUN.TTC",
        )
        return "china-s"
    is_ttc = path.lower().endswith(".ttc")

    def _try_insert(**kwargs: Any) -> bool:
        try:
            page.insert_font(_TOC_EMBED_FONT_NAME, **kwargs)
            return True
        except Exception:
            return False

    ffont = getattr(fitz, "Font", None)
    if callable(ffont):
        attempts: List[Dict[str, Any]] = []
        if is_ttc:
            for idx in (0, 1):
                attempts.append({"fontfile": path, "fontindex": idx})
            attempts.append({"fontfile": path})
        else:
            attempts.append({"fontfile": path})
        for kwargs in attempts:
            try:
                fn = ffont(**kwargs)
                buf = getattr(fn, "buffer", None)
                if buf and _try_insert(fontbuffer=buf):
                    return _TOC_EMBED_FONT_NAME
            except TypeError:
                continue
            except Exception:
                continue

    if is_ttc:
        for idx in (0, 1):
            if _try_insert(fontfile=path, fontindex=idx):
                return _TOC_EMBED_FONT_NAME
        try:
            with open(path, "rb") as f:
                buf = f.read()
            for idx in (0, 1):
                if _try_insert(fontbuffer=buf, fontindex=idx):
                    return _TOC_EMBED_FONT_NAME
        except Exception:
            pass
    if _try_insert(fontfile=path):
        return _TOC_EMBED_FONT_NAME
    try:
        with open(path, "rb") as f:
            buf = f.read()
        if _try_insert(fontbuffer=buf):
            return _TOC_EMBED_FONT_NAME
    except Exception:
        pass
    logger.warning("嵌入 SIMSUN 失败 (%s)，回退 china-s", path)
    return "china-s"


_MERGE_OVERLAY_FONT_REGULAR = "F_MERGE_SIM"
_MERGE_OVERLAY_FONT_BOLD = "F_MERGE_SIM_BOLD"


def _collect_bold_font_file_candidates() -> List[Path]:
    """
    封面/叠印用真粗体字体候选。宋体 TTC 无 Bold 面，优先项目内黑体/粗体，再系统 Noto CJK Bold。
    可通过环境变量 MERGE_OVERLAY_BOLD_FONT 指定字体文件路径。
    """
    names = (
        "SIMSUNB.TTF",
        "simsunb.ttf",
        "SimSunB.ttf",
        "MSYHBD.TTC",
        "msyhbd.ttc",
        "NotoSerifCJK-Bold.ttc",
        "SIMHEI.TTF",
        "simhei.ttf",
        "SimHei.ttf",
        "NotoSansCJK-Bold.ttc",
        "wqy-microhei.ttc",
    )
    seen: set[str] = set()
    out: List[Path] = []
    raw = (os.environ.get("MERGE_OVERLAY_BOLD_FONT") or "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            k = str(p.resolve())
            if k not in seen:
                seen.add(k)
                out.append(p)
    for base in (
        _bundled_simsun_ttc_path(),
        Path(__file__).resolve().parent.parent / "htmlpdf" / "fonts",
    ):
        if base is None:
            continue
        root = base.parent if base.suffix.lower() == ".ttc" else base
        if not root.is_dir():
            continue
        for n in names:
            q = root / n
            if q.is_file():
                k = str(q.resolve())
                if k not in seen:
                    seen.add(k)
                    out.append(q)
    for sys_p in (
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    ):
        if sys_p.is_file():
            k = str(sys_p.resolve())
            if k not in seen:
                seen.add(k)
                out.append(sys_p)
    return out


def _resolve_bold_embed_font_path(*, overlay_textbox: bool = False) -> Optional[str]:
    """
    粗体字体路径。overlay_textbox=True 时排除 Noto CJK Bold 等，
    其在 insert_textbox 中常无法渲染中文（表现为封面无字）。
    """
    cands = _collect_bold_font_file_candidates()
    if overlay_textbox:
        safe: List[Path] = []
        for p in cands:
            low = p.name.lower()
            if "noto" in low or "wqy" in low:
                continue
            safe.append(p)
        cands = safe
    return str(cands[0]) if cands else None


def _register_merge_overlay_fonts(
    page,
    *,
    regular_path: str,
    want_bold: bool = False,
) -> Tuple[str, Any]:
    """嵌入叠印字体；want_bold 时优先真粗体 TTF/TTC（非描边模拟）。"""
    if not fitz or page is None:
        return "china-s", None
    reg = (regular_path or "").strip() or (_resolve_song_embed_font_path(overlay_textbox=True) or "")
    if not reg:
        return "china-s", None
    if not _embed_font_file_on_page(page, _MERGE_OVERLAY_FONT_REGULAR, reg):
        logger.warning("merge overlay: embed regular font failed (%s)", reg)
        return "china-s", None
    try:
        fo_reg = fitz.Font(fontfile=reg)
    except Exception:
        try:
            fo_reg = fitz.Font(fontbuffer=open(reg, "rb").read())
        except Exception:
            fo_reg = None
    if fo_reg is None:
        return _MERGE_OVERLAY_FONT_REGULAR, None
    if not want_bold:
        return _MERGE_OVERLAY_FONT_REGULAR, fo_reg
    bold_path = _resolve_bold_embed_font_path(overlay_textbox=True)
    if not bold_path:
        logger.debug("merge overlay: no bold font file; using regular SimSun")
        return _MERGE_OVERLAY_FONT_REGULAR, fo_reg
    if _embed_font_file_on_page(page, _MERGE_OVERLAY_FONT_BOLD, bold_path):
        try:
            fo_bold = fitz.Font(fontfile=bold_path)
            return _MERGE_OVERLAY_FONT_BOLD, fo_bold
        except Exception:
            pass
    logger.debug("merge overlay bold font embed failed (%s); using regular", bold_path)
    return _MERGE_OVERLAY_FONT_REGULAR, fo_reg


def _baseline_y_from_line(bbox: fitz.Rect, fontsize: float) -> float:
    """中文横排：baseline 约在行框底边略上方。"""
    return float(bbox.y1) - max(0.8, float(fontsize) * 0.12)


def _rightmost_digit_run_x1(spans: List[dict]) -> Optional[float]:
    """目录行末页码（纯数字 span，含全角数字）的右缘 x1。"""
    for sp in reversed(spans or []):
        t = (str(sp.get("text", "") or "")).strip()
        if t and re.match(r"^[\d０-９]+$", t):
            b = sp.get("bbox")
            if b and len(b) >= 4:
                return float(b[2])
    return None


def _measure_toc_template_geometry(page: fitz.Page) -> Dict[str, Any]:
    """
    在 redact 之前从模板「目录」页抽取几何：页眉/标题/条目的 x0、baseline、字号，
    以及末列页码右缘、行距（与资溪等 Word 转 PDF 模板对齐，避免手写死 56/11）。
    """
    pw = float(page.rect.width)
    out: Dict[str, Any] = {
        "header_x0": 56.0,
        "header_baseline": 62.0,
        "header_fs": 10.5,
        "title_x0": 56.0,
        "title_baseline": 92.0,
        "title_fs": 11.0,
        "entry_left_x": 56.0,
        "sub_left_x": 72.0,
        "first_entry_baseline": 120.0,
        "line_step": 17.0,
        "body_fs": 10.5,
        "num_right_x": pw - 52.0,
    }
    d = page.get_text("dict") or {}
    pat_footer = re.compile(r"第\s*\d+\s*页\s*[/／]\s*共\s*\d+\s*页")
    majors: List[Tuple[float, float, float, Optional[float]]] = []
    subs: List[Tuple[float, float, float, Optional[float]]] = []
    ordered_rows: List[float] = []

    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            txt = _line_text(line)
            tcompact = re.sub(r"\s+", "", txt)
            if not txt.strip():
                continue
            bbox = _line_bbox_union(spans)
            fs = float(spans[0].get("size") or 10.5)
            bl = _baseline_y_from_line(bbox, fs)
            num_x1 = _rightmost_digit_run_x1(spans)

            if pat_footer.search(txt) or (
                ("编" in txt and "号" in txt and "页" in txt) and ("报" in txt or "告" in txt)
            ):
                out["header_x0"] = float(bbox.x0)
                out["header_baseline"] = bl
                out["header_fs"] = fs
            elif "目录" in tcompact and len(tcompact) <= 8:
                out["title_x0"] = float(bbox.x0)
                out["title_baseline"] = bl
                out["title_fs"] = max(fs, 10.0)
            elif _toc_stripped_major_match(txt):
                majors.append((bl, float(bbox.x0), fs, num_x1))
                ordered_rows.append(bl)
            elif _toc_stripped_sub_match(txt):
                subs.append((bl, float(bbox.x0), fs, num_x1))
                ordered_rows.append(bl)

    majors.sort(key=lambda x: x[0])
    subs.sort(key=lambda x: x[0])
    ordered_rows.sort()

    if majors:
        out["body_fs"] = majors[0][2]
        out["entry_left_x"] = majors[0][1]
        out["first_entry_baseline"] = majors[0][0]
        if majors[0][3] is not None:
            out["num_right_x"] = majors[0][3]
    if subs:
        out["sub_left_x"] = subs[0][1]
        if subs[0][3] is not None:
            out["num_right_x"] = subs[0][3]

    if len(ordered_rows) >= 2:
        gaps = sorted(
            ordered_rows[i + 1] - ordered_rows[i] for i in range(len(ordered_rows) - 1)
        )
        ng = len(gaps)
        mid = ng // 2
        out["line_step"] = (
            gaps[mid] if ng % 2 == 1 else (gaps[mid - 1] + gaps[mid]) / 2.0
        )
    elif len(majors) >= 2:
        out["line_step"] = majors[1][0] - majors[0][0]

    if not majors and out.get("title_baseline"):
        out["first_entry_baseline"] = float(out["title_baseline"]) + float(out["body_fs"]) * 1.65

    fs_fix = float(out.get("body_fs") or 10.5)
    if out.get("line_step") is not None:
        out["line_step"] = max(
            fs_fix * 1.32,
            min(float(out["line_step"]), fs_fix * 2.2),
        )

    return out


def _collect_toc_redact_rects_from_page(page: fitz.Page) -> List[fitz.Rect]:
    """
    从「可提取文本」的模板页收集需白底覆盖的矩形（与 _measure 规则一致）。
    用于：先在首份 PDF 原页上算坐标，再应用到 show_pdf_page 后的目录页
    （复制后部分稿件无法 get_text，导致无法擦除旧字）。
    """
    rects: List[fitz.Rect] = []
    d = page.get_text("dict") or {}
    pat_footer = re.compile(r"第\s*\d+\s*页\s*[/／]\s*共\s*\d+\s*页")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            txt = _line_text(line)
            tcompact = re.sub(r"\s+", "", txt)
            if not txt.strip():
                continue
            if pat_footer.search(txt):
                rects.append(_line_bbox_union(spans))
                continue
            if "报告编号" in tcompact or ("报" in txt and "编" in txt and "号" in txt and "页" in txt):
                rects.append(_line_bbox_union(spans))
                continue
            if "目录" in tcompact and len(tcompact) <= 8:
                rects.append(_line_bbox_union(spans))
                continue
            if _toc_stripped_major_match(txt) or _toc_stripped_sub_match(txt):
                rects.append(_line_bbox_union(spans))
                continue
            if _toc_leader_only_line(txt):
                rects.append(_line_bbox_union(spans))
                continue
            if txt.count(".") >= 12 or txt.count("．") >= 6:
                rects.append(_line_bbox_union(spans))
                continue
            if "未定义书签" in txt or ("错误" in txt and "书签" in txt):
                rects.append(_line_bbox_union(spans))
                continue
            if re.match(r"^\d{1,4}$", tcompact):
                rects.append(_line_bbox_union(spans))
                continue
            if txt.strip().startswith("）") and ("." in txt or "…" in txt or "·" in txt):
                rects.append(_line_bbox_union(spans))
                continue
    return rects


def _redact_toc_body_residual_spans(page: fitz.Page, geom: Dict[str, Any]) -> int:
    """
    二次清扫：擦除目录标题区以下、页脚以上的全部残留文本行（含 Word「未定义书签」等），
    再重绘「目/录」与目录行。仅作用于文字层，尽量保留底层水印图。
    """
    if not fitz:
        return 0
    h = float(page.rect.height)
    y_header_bottom = float(geom.get("header_baseline") or 55.0) + float(
        geom.get("header_fs") or 10.5
    ) * 1.85
    y_body_top = max(y_header_bottom, float(geom.get("title_baseline") or 94.0) - 14.0)
    y_body_bottom = h - 44.0
    pat_footer = re.compile(r"第\s*\d+\s*页\s*[/／]\s*共\s*\d+\s*页")
    rects: List[fitz.Rect] = []
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            txt = _line_text(line)
            if not txt.strip():
                continue
            bbox = _line_bbox_union(spans)
            cy = (float(bbox.y0) + float(bbox.y1)) * 0.5
            if cy < y_body_top or cy > y_body_bottom:
                continue
            tcompact = re.sub(r"\s+", "", txt)
            if pat_footer.search(txt) or "报告编号" in tcompact:
                continue
            rects.append(bbox)
    return _apply_toc_redact_rects(page, rects)


def _apply_toc_redact_rects(page: fitz.Page, rects: List[fitz.Rect]) -> int:
    n = 0
    for r in rects:
        if r.is_empty:
            continue
        rr = r + fitz.Rect(-1, -1, 2, 3)
        page.add_redact_annot(rr, fill=(1, 1, 1))
        n += 1
    if n:
        page.apply_redactions()
    return n


def _redact_original_toc_text_spans(page: fitz.Page, geom: Optional[Dict[str, Any]] = None) -> int:
    """
    仅擦除模板目录页中需替换的文字块（页眉编号行、目录标题、各目录行），尽量保留背景与水印底图。
    返回添加的红区数量。
    """
    rects = _collect_toc_redact_rects_from_page(page)
    n = _apply_toc_redact_rects(page, rects)
    if geom is not None:
        n += _redact_toc_body_residual_spans(page, geom)
    return n


def _measure_header_split_geometry(page: fitz.Page) -> Optional[Dict[str, Any]]:
    """
    从模板页解析页眉：左侧「报告编号：…」左缘与基线，右侧「第 x 页/共 y 页」右缘与基线（与样例/正文页一致）。
    """
    if not fitz:
        return None
    d = page.get_text("dict") or {}
    pat_footer = re.compile(r"第\s*\d+\s*页\s*[/／]\s*共\s*\d+\s*页")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            txt = _line_text(line)
            tcompact = re.sub(r"\s+", "", txt)
            if "报告编号" not in tcompact:
                continue
            m = pat_footer.search(txt)
            if not m:
                continue
            split_i = m.start()
            acc = 0
            left_rects: List[fitz.Rect] = []
            right_rects: List[fitz.Rect] = []
            fs_vals: List[float] = []
            for sp in spans:
                st = str(sp.get("text", "") or "")
                sa, sb = acc, acc + len(st)
                acc = sb
                bb = sp.get("bbox")
                if not bb or len(bb) < 4:
                    continue
                r = fitz.Rect(bb)
                fs = float(sp.get("size") or 10.5)
                if sa < split_i:
                    left_rects.append(r)
                    fs_vals.append(fs)
                if sb > split_i:
                    right_rects.append(r)
                    if sa >= split_i:
                        fs_vals.append(fs)
            if not left_rects or not right_rects:
                continue
            lx0 = min(r.x0 for r in left_rects)
            ly1 = max(r.y1 for r in left_rects)
            rx1 = max(r.x1 for r in right_rects)
            ry1 = max(r.y1 for r in right_rects)
            fs_med = sorted(fs_vals)[len(fs_vals) // 2] if fs_vals else 10.5
            lbl = float(ly1) - max(0.8, fs_med * 0.12)
            rbl = float(ry1) - max(0.8, fs_med * 0.12)
            band_b = max(ly1, ry1) + fs_med * 0.42
            return {
                "left_x0": lx0,
                "left_baseline": lbl,
                "right_x1": rx1,
                "right_baseline": rbl,
                "fs": fs_med,
                "band_y1": min(float(band_b), float(page.rect.height) * 0.18),
            }
    # 「报告编号」与「第 x 页/共 y 页」分两行（常见 Word 导出）
    rows: List[Tuple[float, str, fitz.Rect, float]] = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            txt = _line_text(line)
            if not txt.strip():
                continue
            bbox = _line_bbox_union(spans)
            fs = float(spans[0].get("size") or 10.5)
            bl = _baseline_y_from_line(bbox, fs)
            rows.append((bl, txt, bbox, fs))
    left_t: Optional[Tuple[fitz.Rect, float]] = None
    right_t: Optional[Tuple[fitz.Rect, float]] = None
    for _bl, txt, bbox, fs in sorted(rows, key=lambda r: r[0]):
        tcompact = re.sub(r"\s+", "", txt)
        if "报告编号" in tcompact and not pat_footer.search(txt):
            left_t = (bbox, fs)
        if pat_footer.search(txt):
            right_t = (bbox, fs)
    if left_t and right_t:
        lb, ls = left_t
        rb, rs = right_t
        if abs(float(lb.y0) - float(rb.y0)) <= 22.0:
            fs_med = (ls + rs) / 2.0
            lbl = float(lb.y1) - max(0.8, ls * 0.12)
            rbl = float(rb.y1) - max(0.8, rs * 0.12)
            band_b = max(float(lb.y1), float(rb.y1)) + fs_med * 0.42
            return {
                "left_x0": float(lb.x0),
                "left_baseline": lbl,
                "right_x1": float(rb.x1),
                "right_baseline": rbl,
                "fs": fs_med,
                "band_y1": min(float(band_b), float(page.rect.height) * 0.18),
            }
    return None


def _scale_header_split_geom(g: Dict[str, Any], sx: float, sy: float) -> Dict[str, Any]:
    s = dict(g)
    kfs = min(float(sx), float(sy))
    s["left_x0"] = float(s["left_x0"]) * sx
    s["left_baseline"] = float(s["left_baseline"]) * sy
    s["right_x1"] = float(s["right_x1"]) * sx
    s["right_baseline"] = float(s["right_baseline"]) * sy
    s["band_y1"] = float(s["band_y1"]) * sy
    s["fs"] = max(7.0, float(s["fs"]) * kfs)
    return s


def _default_header_split_geom(page: fitz.Page) -> Dict[str, Any]:
    w, h = float(page.rect.width), float(page.rect.height)
    fs = 10.5
    return {
        "left_x0": max(24.0, w * 0.07),
        "left_baseline": 62.0,
        "right_x1": w - 48.0,
        "right_baseline": 62.0,
        "fs": fs,
        "band_y1": min(82.0, h * 0.11),
    }


# 合并报告固定版式基准（标准 A4 MediaBox，与院方 Word 导出 PDF 一致）
_MERGED_REPORT_BASE_WIDTH = 595.2999877929688
_MERGED_REPORT_BASE_HEIGHT = 841.9000244140625

# 页眉：左缘量自院方 DSA 样例第 3 行「报告编号」行；右缘为「第 n 页/共 m 页」整段右对齐锚点。
# 先前误用某副本 PDF 的略宽书眉；与目录样例/资溪稿对齐后右缘约 513.5pt，现改为距右页边约 40pt，避免字宽度量误差导致偏左。
_FIXED_MERGED_HEADER_SPLIT_GEOM: Dict[str, Any] = {
    "left_x0": 73.91999816894531,
    "left_baseline": 55.03370287656423,
    "right_x1": _MERGED_REPORT_BASE_WIDTH - 40.0,
    "right_baseline": 55.03370287656423,
    "fs": 10.449999809265137,
    "band_y1": 63.5,
}

# 合并目录页排版：量自原「目录样例」A4 第 1 页（固化后不再读文件）。「目」「录」为两行大字居中。
_FIXED_MERGED_TOC_GEOM_BASE: Dict[str, Any] = {
    "toc_title_two_chars": True,
    "header_x0": 238.1199951171875,
    "header_baseline": 55.033700675964354,
    "header_fs": 10.449999809265137,
    "title_x0": 56.0,
    "title_mu_x0": 258.96,
    "title_lu_x0": 314.16,
    "title_baseline": 94.8160025024414,
    "title_fs": 21.950000762939453,
    "entry_left_x": 70.91999816894531,
    "sub_left_x": 104.91999816894531,
    "first_entry_baseline": 135.96199279785156,
    "line_step": 23.399993896484375,
    "body_fs": 12.0,
    "num_right_x": 543.2999877929688,
}


def _header_split_geom_scaled_to_page(page: fitz.Page) -> Dict[str, Any]:
    """按固定基准坐标缩放到当前页 MediaBox（与首份报告纸张一致时比例约为 1）。"""
    pw, ph = float(page.rect.width), float(page.rect.height)
    sx = pw / _MERGED_REPORT_BASE_WIDTH
    sy = ph / _MERGED_REPORT_BASE_HEIGHT
    return _scale_header_split_geom(dict(_FIXED_MERGED_HEADER_SPLIT_GEOM), sx, sy)


def _merged_toc_geometry_for_size(pw: float, ph: float) -> Dict[str, Any]:
    sx = pw / _MERGED_REPORT_BASE_WIDTH
    sy = ph / _MERGED_REPORT_BASE_HEIGHT
    scaled = _scale_toc_geometry(dict(_FIXED_MERGED_TOC_GEOM_BASE), sx, sy)
    return _refine_toc_geometry_for_draw(pw, ph, scaled)


def _watermark_png_stream_black_to_transparent(path: str) -> Optional[bytes]:
    """
    将近黑底（RGB 均低于阈值）转为透明，供底层水印叠放；若处理失败则返回 None 并回退原图路径插入。
    """
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:
        return None
    th = 48
    datas = list(im.getdata())
    new_data: List[Tuple[int, int, int, int]] = []
    for pix in datas:
        r, g, b, a = pix[0], pix[1], pix[2], pix[3] if len(pix) > 3 else 255
        if r <= th and g <= th and b <= th:
            new_data.append((0, 0, 0, 0))
        else:
            new_data.append((r, g, b, a))
    im.putdata(new_data)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _resolve_merged_report_watermark_png_path() -> str:
    """
    合并目录页底层水印 PNG。MERGED_REPORT_WATERMARK_PNG 优先；
    否则使用 media/file_library/templates/merged_report_watermark.png。
    """
    raw = (os.environ.get("MERGED_REPORT_WATERMARK_PNG") or "").strip()
    if raw and os.path.isfile(raw):
        return raw
    try:
        from django.conf import settings

        p = Path(settings.BASE_DIR) / "media" / "file_library" / "templates" / "merged_report_watermark.png"
        if p.is_file():
            return str(p)
    except Exception:
        pass
    p2 = Path(__file__).resolve().parent.parent / "media" / "file_library" / "templates" / "merged_report_watermark.png"
    if p2.is_file():
        return str(p2)
    logger.warning(
        "报告页背景水印 PNG 未找到：请放置 merged_report_watermark.png 于 "
        "media/file_library/templates/ 或设置环境变量 MERGED_REPORT_WATERMARK_PNG"
    )
    return ""


def insert_report_page_watermark_bottom_layer(page: fitz.Page, pw: float, ph: float) -> None:
    """在空白或重绘页底层插入居中透明底水印（合并目录、放射防护表、平面布局等新建页共用）。"""
    _merged_report_insert_watermark_bottom_layer(page, pw, ph)


def _merged_report_insert_watermark_bottom_layer(page: fitz.Page, pw: float, ph: float) -> None:
    """
    在页面内容流最前插入水印图（overlay=False 时位于底层），居中缩放。
    RGB 黑底会在插入前转为透明像素；若已为 RGBA 则仅抠除近黑像素。
    """
    if not fitz:
        return
    path = _resolve_merged_report_watermark_png_path()
    if not path:
        return
    try:
        with Image.open(path) as im:
            iw, ih = im.size
    except Exception as exc:
        logger.warning("合并报告水印图无法读取: %s (%s)", path, exc)
        return
    if iw <= 0 or ih <= 0:
        return
    aspect = float(iw) / float(ih)
    box_h = min(pw, ph) * 0.42
    box_w = box_h * aspect
    if box_w > pw * 0.78:
        box_w = pw * 0.78
        box_h = box_w / max(aspect, 1e-6)
    cx, cy = pw * 0.5, ph * 0.5
    rect = fitz.Rect(cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2)
    stream = _watermark_png_stream_black_to_transparent(path)
    try:
        if stream:
            page.insert_image(rect, stream=stream, keep_proportion=True, overlay=False)
        else:
            page.insert_image(rect, filename=path, keep_proportion=True, overlay=False)
    except TypeError:
        if stream:
            page.insert_image(rect, stream=stream, keep_proportion=True)
        else:
            page.insert_image(rect, filename=path, keep_proportion=True)


def _header_split_geom_for_page(
    page: fitz.Page,
    profile: Optional["ReportTemplateProfile"] = None,
) -> Dict[str, Any]:
    split = _header_split_geom_scaled_to_page(page)
    prof = _resolve_report_template_profile(profile)
    if prof.page_header_left_x0 is not None:
        split = dict(split)
        split["left_x0"] = float(prof.page_header_left_x0)
    rx = prof.page_header_right_x1
    if rx is None:
        rx = prof.summary_page_header_right_x1
    if rx is not None:
        split = dict(split)
        split["right_x1"] = float(rx)
    return split


def _draw_header_report_no_and_page_footer(
    page,
    report_no: str,
    report_x: int,
    report_y: int,
    split_geom: Dict[str, Any],
    fontname: str,
    *,
    footer_left_x0: float | None = None,
    footer_right_x1: float | None = None,
) -> None:
    """左上角「报告编号：…」+ 右上角「第 x 页/共 y 页」，坐标取自 split_geom。"""
    if not fitz:
        return
    fs = float(split_geom.get("fs") or 10.5)
    left_txt = f"报告编号：{report_no}"
    # 页码展示：「第 x 页/共 y 页」（「第」「共」后各一空格）
    right_txt = f"第 {int(report_x)} 页/共 {int(report_y)} 页"
    lx = float(footer_left_x0 if footer_left_x0 is not None else split_geom["left_x0"])
    ly = float(split_geom["left_baseline"])
    rx1 = float(footer_right_x1 if footer_right_x1 is not None else split_geom["right_x1"])
    ry = float(split_geom["right_baseline"])
    tw_r = _fitz_text_width(right_txt, fontname, fs, page=page)
    x_right = rx1 - tw_r
    pw = float(page.rect.width)
    if x_right < lx + 80.0:
        x_right = max(lx + 100.0, pw - 48.0 - tw_r)
    _fitz_insert_text_line(page, lx, ly, left_txt, fs, fontname)
    _fitz_insert_text_line(page, x_right, ry, right_txt, fs, fontname)


def rewrite_merged_report_page_headers(
    doc: fitz.Document,
    *,
    report_no: str,
    toc_page_0based: Optional[int] = None,
    profile: Optional["ReportTemplateProfile"] = None,
    summary_page_0based: int = 2,
) -> None:
    """
    除封面、声明（物理第 1、2 页）外，统一按**固定基准**缩放的坐标重绘页眉：
    左上「报告编号：{report_no}」、右上「第 x 页/共 y 页」；目录页与正文页同一套坐标，仅 x/y 页码随页变化。
    """
    if not fitz:
        return
    total = doc.page_count
    report_y = _report_body_total_from_physical_count(total)
    margin = 10.0
    prof = _resolve_report_template_profile(profile)
    for pi in range(total):
        if pi < 2:
            continue
        page = doc[pi]
        phys_1 = pi + 1
        if toc_page_0based is not None and pi == toc_page_0based:
            report_x = 2
        else:
            report_x = _report_body_page_from_physical_1based(phys_1)
        if report_x <= 0:
            continue
        split = _header_split_geom_for_page(page, profile=prof)
        w, h = float(page.rect.width), float(page.rect.height)
        band_y1 = min(float(split.get("band_y1") or 80.0), h * 0.2)
        band = fitz.Rect(margin, 2.0, w - margin, band_y1)
        if not band.is_empty and band.y1 > band.y0:
            page.add_redact_annot(band + fitz.Rect(-1, -1, 3, 3), fill=(1, 1, 1))
            page.apply_redactions()
        font_embed = _register_simsun_on_page(page)
        footer_lx0 = float(prof.page_header_left_x0) if prof.page_header_left_x0 is not None else None
        footer_rx1 = prof.page_header_right_x1
        if footer_rx1 is None and prof.summary_page_header_right_x1 is not None:
            footer_rx1 = float(prof.summary_page_header_right_x1)
        elif footer_rx1 is not None:
            footer_rx1 = float(footer_rx1)
        _draw_header_report_no_and_page_footer(
            page,
            report_no,
            report_x,
            report_y,
            split,
            font_embed,
            footer_left_x0=footer_lx0,
            footer_right_x1=footer_rx1,
        )


_TOC_MAJOR_LINE_RE = re.compile(r"^([一二三四五六七八九十百千]+)[、.．]\s*(.+)$")
_TOC_MINOR_LINE_RE = re.compile(r"^(\d+)\s*[.．]\s*(\d+)\s+(.+)$")


def _norm_toc_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


_MERGE_COVER_LEFT_LABEL_MAX_X0_PT = 200.0


def _merge_cover_field_label_rect(page, label: str) -> Optional[Any]:
    """封面左侧字段标签（排除取值列正文中误含的「受检单位」等子串）。"""
    if not fitz or page is None:
        return None
    q = re.sub(r"\s+", "", (label or "").strip())
    if not q:
        return None
    best_y0: Optional[float] = None
    best: Optional[Any] = None
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            tcompact = _merge_line_compact_text(line)
            if tcompact != q:
                continue
            bb = _line_bbox_union(line.get("spans") or [])
            if bb.is_empty or float(bb.x0) > _MERGE_COVER_LEFT_LABEL_MAX_X0_PT:
                continue
            y0 = float(bb.y0)
            if best_y0 is None or y0 < best_y0:
                best_y0 = y0
                best = bb
    return best


def _merge_cover_next_field_top(page, label_rect: fitz.Rect, labels_below: Sequence[str]) -> Optional[float]:
    """取当前标签下方、最近一个封面字段标签的 y0（用于限制擦除/书写下边界）。"""
    if not fitz or label_rect is None or label_rect.is_empty:
        return None
    y_ref = float(label_rect.y0)
    best: Optional[float] = None
    for nd in labels_below:
        q = (nd or "").strip()
        if not q:
            continue
        r = _merge_cover_field_label_rect(page, q)
        if r is None or r.is_empty:
            continue
        y0 = float(r.y0)
        if y0 <= y_ref + 6.0:
            continue
        best = y0 if best is None else min(best, y0)
    return best


def _merge_cover_fullwidth_band_at_label(
    page,
    label_rect: fitz.Rect,
    *,
    line_count: float = 1.0,
    line_fs: float = 14.0,
    extend_below: float = 18.0,
    y1_cap: Optional[float] = None,
    tight_line: bool = False,
) -> fitz.Rect:
    """封面标签行取值列书写带（左界为「项目名称」标签右缘）。"""
    lh = max(12.0, float(line_fs) * 1.28)
    y0 = float(label_rect.y0) - 2.0
    if tight_line:
        y1 = float(label_rect.y1) + 2.0
    else:
        y1 = max(float(label_rect.y1) + float(extend_below), y0 + lh * float(line_count))
    if y1_cap is not None:
        y1 = min(y1, float(y1_cap) - 2.0)
    y1 = min(y1, float(page.rect.height) - 20.0)
    if y1 <= y0 + 6.0:
        y1 = min(float(page.rect.height) - 20.0, y0 + lh * float(line_count))
    x0, x1 = _merge_cover_value_x_bounds(page)
    return fitz.Rect(x0, y0, x1, y1)


def _merge_cover_value_write_rect_for_label(
    page,
    label_rect: fitz.Rect,
    labels: Sequence[str],
    *,
    line_fs: float = 14.0,
) -> fitz.Rect:
    """封面单行取值区：横向用「项目名称右缘～页右−2.8cm」整列居中，纵向对齐标签行。"""
    x0, x1 = _merge_cover_value_x_bounds(page)
    y0 = float(label_rect.y0) - 1.0
    y1 = float(label_rect.y1) + 2.0
    min_h = max(20.0, float(line_fs) * 1.42)
    if y1 - y0 < min_h:
        y1 = min(float(page.rect.height) - 20.0, y0 + min_h)
    return fitz.Rect(x0, y0, x1, y1)


def _merge_cover_value_only_erase_band(
    page,
    label_rect: fitz.Rect,
    labels: Sequence[str],
    *,
    y1_cap: Optional[float] = None,
    include_line_below: bool = True,
) -> fitz.Rect:
    """仅擦除标签右侧及（可选）下方取值区，不碰左侧标签列。"""
    if not fitz or label_rect is None or label_rect.is_empty:
        return fitz.Rect(0, 0, 0, 0)
    x_val0, x1 = _merge_cover_value_x_bounds(page)
    x_val0 = max(float(x_val0), float(label_rect.x1) + 1.0)
    y0 = float(label_rect.y0) - 1.5
    y1 = float(label_rect.y1) + (20.0 if include_line_below else 2.0)
    if y1_cap is not None:
        y1 = min(y1, float(y1_cap) - 2.0)
    band = fitz.Rect(x_val0, y0, x1, y1)
    fb = _merge_value_rect_same_line(page, label_rect)
    vr = _merge_value_span_union_right_of_label(page, label_rect)
    dr = _merge_dict_line_value_rect_right_of_label(page, label_rect, tuple(labels))
    for ext in (dr, vr, fb):
        if ext is not None and not ext.is_empty:
            try:
                band |= ext
            except Exception:
                pass
    if include_line_below:
        y_mid = (float(label_rect.y0) + float(label_rect.y1)) * 0.5
        d = page.get_text("dict") or {}
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans") or []
                if not spans:
                    continue
                lb = _line_bbox_union(spans)
                if lb.is_empty:
                    continue
                cy = (float(lb.y0) + float(lb.y1)) * 0.5
                if cy <= y_mid - 1.0 or cy > y1:
                    continue
                tcompact = re.sub(r"\s+", "", _line_text(line))
                if any(re.sub(r"\s+", "", (n or "")) in tcompact for n in labels if (n or "").strip()):
                    continue
                if float(lb.x0) >= x_val0 - 2.0:
                    try:
                        band |= lb
                    except Exception:
                        pass
    band.x0 = max(float(band.x0), x_val0)
    return band


def _merge_erase_cover_project_name_value_residuals(page, band: fitz.Rect) -> None:
    """擦除封面「项目名称」取值列内 HTMLPDF/模板预印残留（避免与叠印两行重复）。"""
    if not fitz or page is None or band is None or band.is_empty:
        return
    x_val0, _ = _merge_cover_value_x_bounds(page)
    y0, y1 = float(band.y0), float(band.y1)
    skip_labels = ("项目名称", "受检单位", "检测类型", "报告日期", "受检编号", "报告编号")
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = re.sub(r"\s+", "", _line_text(line))
            if any(re.sub(r"\s+", "", lb) in t for lb in skip_labels if lb):
                continue
            bbox = _line_bbox_union(line.get("spans") or [])
            if bbox.is_empty:
                continue
            cy = (float(bbox.y0) + float(bbox.y1)) * 0.5
            if cy < y0 - 1.0 or cy > y1 + 1.0 or float(bbox.x0) < x_val0 - 2.0:
                continue
            _merge_overlay_erase_transparent(page, bbox, pad=0.3)
    _merge_apply_redactions_overlay(page)


def _merge_erase_cover_standalone_modality_line(
    page,
    band: fitz.Rect,
    profile: Optional["ReportTemplateProfile"] = None,
) -> None:
    """擦除封面「项目名称」取值区内单独一行的模板预印「放射诊疗设备」等。"""
    if not fitz or page is None or band is None or band.is_empty:
        return
    y0 = float(band.y0)
    y1 = float(band.y1)
    x_val0, _ = _merge_cover_value_x_bounds(page)
    prof = _resolve_report_template_profile(profile)
    residues = {re.sub(r"\s+", "", t) for t in prof.cover_modality_residue_texts if t}
    # 模板预印「质量控制检测（状态检测）」等短行，叠印前擦除以免与第二行标题重复
    residues.add("质量控制检测")
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = re.sub(r"\s+", "", _line_text(line))
            if t not in residues and not (
                t.startswith("质量控制检测（") and len(t) <= 24
            ):
                continue
            bbox = _line_bbox_union(line.get("spans") or [])
            if bbox.is_empty:
                continue
            cy = (float(bbox.y0) + float(bbox.y1)) * 0.5
            if cy < y0 - 2.0 or cy > y1 + 2.0 or float(bbox.x0) < x_val0 - 4.0:
                continue
            _merge_overlay_erase_transparent(page, bbox, pad=0.8)
    _merge_apply_redactions_overlay(page)


def _merge_cover_erase_band_for_label(
    page,
    label_rect: fitz.Rect,
    labels: Sequence[str],
    *,
    full_band: Optional[fitz.Rect] = None,
) -> fitz.Rect:
    """封面标签取值擦除区（不擦标签列）。"""
    y1_cap = None
    if full_band is not None and not full_band.is_empty:
        y1_cap = float(full_band.y1)
    next_top = _merge_cover_next_field_top(
        page, label_rect, ("项目名称", "受检单位", "检测类型", "报告日期", "受检编号", "编制人")
    )
    if next_top is not None:
        y1_cap = min(y1_cap or next_top, float(next_top))
    include_below = any(re.sub(r"\s+", "", (n or "")) == "项目名称" for n in labels)
    return _merge_cover_value_only_erase_band(
        page,
        label_rect,
        labels,
        y1_cap=y1_cap,
        include_line_below=include_below,
    )


def _merge_overlay_write_cover_label_centered(
    page,
    labels: Sequence[str],
    text: str,
    *,
    preferred_fontsize: float,
    label_rect_finder=None,
    line_count: float = 1.0,
) -> bool:
    """封面标签：取值列内居中叠印（纵向往标签行收紧，横向对齐下划线）。"""
    if not fitz or not page or not (text or "").strip():
        return False
    ac = int(getattr(fitz, "TEXT_ALIGN_CENTER", 1))
    finder = label_rect_finder or _merge_search_first
    r_lab = finder(page, list(labels))
    if r_lab is None:
        return False
    write_rect = _merge_cover_value_write_rect_for_label(
        page, r_lab, labels, line_fs=preferred_fontsize
    )
    erase_band = _merge_cover_value_only_erase_band(
        page,
        r_lab,
        labels,
        y1_cap=float(write_rect.y1) + 1.0,
        include_line_below=False,
    )
    if erase_band is None or erase_band.is_empty:
        erase_band = write_rect
    _merge_overlay_erase_transparent(page, erase_band)
    _merge_apply_redactions_overlay(page)
    _merge_overlay_write_text_html_style(
        page,
        write_rect,
        text,
        align=ac,
        preferred_fontsize=preferred_fontsize,
        fontsize_floor=preferred_fontsize,
        bold=_cover_overlay_use_bold(),
        valign_top=True,
    )
    return True


def _merge_overlay_write_cover_label_value(
    page,
    labels: Sequence[str],
    text: str,
    *,
    align: int,
    preferred_fontsize: float,
    label_rect_finder=None,
) -> bool:
    """按封面标签检索取值区叠印；封面栏位默认整页宽居中。"""
    if int(align) == int(getattr(fitz, "TEXT_ALIGN_CENTER", 1)):
        return _merge_overlay_write_cover_label_centered(
            page,
            labels,
            text,
            preferred_fontsize=preferred_fontsize,
            label_rect_finder=label_rect_finder,
        )
    if not fitz or not page or not (text or "").strip():
        return False
    finder = label_rect_finder or _merge_search_first
    r_lab = finder(page, list(labels))
    if r_lab is None:
        return False
    fb = _merge_value_rect_same_line(page, r_lab)
    vr = _merge_value_span_union_right_of_label(page, r_lab)
    dr = _merge_dict_line_value_rect_right_of_label(page, r_lab, tuple(labels))
    band = dr if (dr is not None and not dr.is_empty) else (vr if (vr is not None and not vr.is_empty) else fb)
    if band is None or band.is_empty:
        return False
    _merge_overlay_erase_transparent(page, band)
    _merge_apply_redactions_overlay(page)
    wrect = _merge_cover_value_write_rect_for_label(
        page, r_lab, labels, line_fs=preferred_fontsize
    )
    _merge_overlay_write_text_html_style(
        page,
        wrect,
        text,
        align=align,
        preferred_fontsize=preferred_fontsize,
        fontsize_floor=preferred_fontsize,
        bold=_cover_overlay_use_bold(),
        valign_top=True,
    )
    return True


def _apply_report_cover_overlay_by_label_search(
    page,
    *,
    org: str = "",
    cover_title: str = "",
    merge_date: str = "",
    inspection_type: str = "",
    report_no_line: str = "",
    inspection_index: str = "",
    page_index_0based: int = 0,
    use_fixed_title_rect: bool = False,
    profile: Optional["ReportTemplateProfile"] = None,
) -> None:
    """
    封面叠印：仅通过 PDF 标签关键词定位（与合成报告 apply_merged_report_merge_overlay 封面逻辑一致）。
    不使用模板 pdfFieldId / f1-f4 映射。
    """
    if not fitz or page is None:
        return
    al = int(getattr(fitz, "TEXT_ALIGN_LEFT", 0))
    ac = int(getattr(fitz, "TEXT_ALIGN_CENTER", 1))

    rn = (report_no_line or "").strip()
    if rn and not rn.startswith("报告编号"):
        rn = f"报告编号：{rn}"
    if rn:
        r_no_lab = _merge_search_first(page, ["报告编号"])
        if r_no_lab is not None:
            x0, x1 = _merge_cover_page_center_x_bounds(page)
            y0 = float(r_no_lab.y0) - 2.0
            y1 = float(r_no_lab.y1) + 4.0
            write_band = fitz.Rect(x0, y0, x1, y1)
            # 整行擦除（含「报告编号：」与编号本体），再整页宽居中重写
            fb = _merge_value_rect_same_line(page, r_no_lab)
            vr = _merge_value_span_union_right_of_label(page, r_no_lab)
            dr = _merge_dict_line_value_rect_right_of_label(page, r_no_lab, ("报告编号",))
            erase_band = fitz.Rect(write_band)
            for ext in (dr, vr, fb):
                if ext is not None and not ext.is_empty:
                    try:
                        erase_band |= ext
                    except Exception:
                        pass
            _merge_overlay_erase_transparent(page, erase_band)
            _merge_apply_redactions_overlay(page)
            _merge_overlay_write_cover_centered_band(
                page,
                write_band,
                rn,
                align=ac,
                preferred_fontsize=_MERGE_PT_SIHAO,
                erase_before_write=False,
                value_column_center=False,
            )

    org_s = _normalize_rp_phrase_spacing((org or "").strip())
    crt = _normalize_rp_phrase_spacing((cover_title or "").strip())
    md = (merge_date or "").strip()
    insp = (inspection_type or "").strip()
    nos = (inspection_index or "").strip()

    if org_s:
        _merge_overlay_write_cover_label_value(
            page, ["受检单位"], org_s, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
        )

    if insp:
        _merge_overlay_write_cover_label_value(
            page, ["检测类型"], insp, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
        )

    if md:
        _merge_overlay_write_cover_label_value(
            page, ["报告日期", "报告时间"], md, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
        )

    if nos:
        r_idx = _merge_search_first(page, ["受检编号"])
        if r_idx is not None:
            fb = _merge_value_rect_same_line(page, r_idx)
            vr = _merge_value_span_union_right_of_label(page, r_idx)
            dr = _merge_dict_line_value_rect_right_of_label(page, r_idx, ("受检编号",))
            band = dr if (dr is not None and not dr.is_empty) else (vr if (vr is not None and not vr.is_empty) else fb)
            if band is not None and not band.is_empty:
                _merge_overlay_erase_transparent(page, band)
                _merge_apply_redactions_overlay(page)
                _merge_overlay_write_text_html_style(page, band, nos, align=al)

    if org_s or crt:
        r_lab = _merge_search_last_by_bottom(page, ["项目名称"])
        if r_lab is None:
            r_lab = _merge_search_first(page, ["项目名称"])
        if r_lab is not None:
            x0, x1 = _merge_cover_value_x_bounds(page)
            prof = _resolve_report_template_profile(profile)
            lh = max(14.0, _MERGE_PT_SIHAO * 1.3)
            align_label = bool(getattr(prof, "cover_project_name_first_row_align_label", False))
            if align_label:
                y0 = float(r_lab.y0) - 1.5
            else:
                y0 = float(r_lab.y0) - 2.0 - float(prof.cover_project_name_erase_above_pt or 0.0)
            y1_cap = _merge_cover_next_field_top(page, r_lab, ("受检单位", "检测类型", "报告日期", "受检编号"))
            # 擦除/残留清理区须覆盖「项目名称」双下划线两行取值格，直至下一标签行（受检单位）前。
            if y1_cap is not None:
                y1 = min(float(page.rect.height) - 24.0, float(y1_cap) - 3.0)
            else:
                y1 = min(float(page.rect.height) - 24.0, y0 + lh * 2.55)
                if crt and len(crt) > 26:
                    y1 = min(float(page.rect.height) - 24.0, y0 + lh * 2.95)
            full_band = fitz.Rect(x0, y0, x1, y1)
            erase_band = _merge_cover_erase_band_for_label(page, r_lab, ("项目名称",), full_band=full_band)
            underline_preserve_rect = fitz.Rect(
                x0,
                float(r_lab.y1) - 2.0,
                x1,
                min(float(y1_cap or float(r_lab.y1) + 58.0), float(page.rect.height) - 8.0),
            )
            saved_underlines = _merge_collect_horizontal_line_segments_in_rect(
                page, underline_preserve_rect
            )
            _merge_overlay_erase_transparent(page, erase_band)
            _merge_apply_redactions_overlay(page)
            _merge_erase_cover_project_name_value_residuals(page, full_band)
            _merge_erase_cover_standalone_modality_line(page, full_band, profile=profile)
            umy: Optional[float] = None
            cover_y_off = 0.0 if align_label else float(prof.cover_project_name_write_y_offset or 0.0)
            dual_ul_ys = _merge_sorted_underline_ys_from_segments(saved_underlines)
            use_dual_cells = bool(prof.cover_project_name_dual_underline_cells and len(dual_ul_ys) >= 2)
            precomputed_rows: Optional[Tuple[fitz.Rect, fitz.Rect]] = None
            line2_gap = float(prof.cover_project_name_line2_above_underline_pt or 5.5)
            if align_label:
                probe_y1 = float(y1_cap or float(r_lab.y1) + 58.0)
                _, _, umy = _merge_underline_x_bounds_in_band(
                    page,
                    fitz.Rect(x0, float(r_lab.y1), x1, probe_y1),
                    r_lab,
                    y_sub_top=float(r_lab.y1),
                    y_sub_bot=probe_y1,
                )
                if umy is not None:
                    precomputed_rows = _merge_cover_project_name_label_row_write_rects(
                        r_lab,
                        float(umy),
                        x0,
                        x1,
                        line2_above_underline_pt=line2_gap,
                    )
                    write_band = fitz.Rect(
                        x0,
                        float(precomputed_rows[0].y0),
                        x1,
                        float(precomputed_rows[1].y1),
                    )
                else:
                    write_band = full_band
            elif not use_dual_cells and prof.cover_project_name_write_below_label:
                probe_y1 = float(r_lab.y1) + 40.0
                if y1_cap is not None:
                    probe_y1 = min(probe_y1, float(y1_cap) - 4.0)
                _, _, umy = _merge_underline_x_bounds_in_band(
                    page,
                    fitz.Rect(x0, float(r_lab.y1), x1, probe_y1),
                    r_lab,
                    y_sub_top=float(r_lab.y1),
                    y_sub_bot=probe_y1,
                )
                if umy is not None:
                    write_band = _merge_cover_project_name_write_band_above_underline(
                        page,
                        r_lab,
                        float(umy),
                        x0=x0,
                        x1=x1,
                        y1_cap=y1_cap,
                        profile=profile,
                    )
                else:
                    write_y0 = float(r_lab.y1) + 1.0
                    write_band = fitz.Rect(x0, write_y0, x1, write_y0 + 34.0)
            else:
                write_band = full_band
            if cover_y_off:
                write_band = fitz.Rect(
                    float(write_band.x0),
                    float(write_band.y0) + cover_y_off,
                    float(write_band.x1),
                    float(write_band.y1) + cover_y_off,
                )
            if use_dual_cells:
                _merge_write_cover_project_name_dual_underline_cells(
                    page,
                    r_lab,
                    x0,
                    x1,
                    org_s,
                    crt,
                    dual_ul_ys,
                    align_center=ac,
                    cover_y_off=cover_y_off,
                    cell_pad=5.0,
                )
            else:
                _merge_write_cover_project_name_two_lines(
                    page,
                    r_lab,
                    write_band,
                    org_s,
                    crt,
                    align_center=ac,
                    page_index_0based=page_index_0based,
                    use_fixed_title_rect=use_fixed_title_rect,
                    underline_y=(
                        float(umy)
                        if prof.cover_project_name_write_below_label and umy is not None and not align_label
                        else None
                    ),
                    line2_above_underline_pt=line2_gap,
                    precomputed_rows=precomputed_rows,
                )
            if saved_underlines:
                _merge_redraw_horizontal_line_segments(page, saved_underlines)


def _merge_apply_cover_report_number(
    page,
    report_no: str,
    template_rects: Dict[str, Any],
    *,
    page_index_0based: int = 0,
    include_label_prefix: bool = False,
) -> None:
    """封面「报告编号」：优先宽行检索叠印，窄模板框时回退标签右侧取值区。"""
    if not fitz or not report_no or page is None:
        return
    ac = int(getattr(fitz, "TEXT_ALIGN_CENTER", 1))
    text = (report_no or "").strip()
    if include_label_prefix and text and not text.startswith("报告编号"):
        text = f"报告编号：{text}"

    cov_no_rect = template_rects.get("cover_report_no")
    use_search = include_label_prefix or len(text) > 18
    if cov_no_rect is not None and not cov_no_rect.is_empty:
        if float(cov_no_rect.width) < max(80.0, len(text) * 5.5):
            use_search = True

    if cov_no_rect is not None and not cov_no_rect.is_empty and (include_label_prefix or use_search):
        _merge_overlay_write_cover_centered_band(
            page,
            cov_no_rect,
            text,
            align=ac,
            preferred_fontsize=_MERGE_PT_XIAOSI,
            value_column_center=False,
        )
        return

    r_lab = _merge_search_first(page, ["报告编号"])
    if use_search and r_lab is not None:
        x0, x1 = _merge_cover_page_center_x_bounds(page)
        fb = _merge_value_rect_same_line(page, r_lab)
        vr = _merge_value_span_union_right_of_label(page, r_lab)
        dr = _merge_dict_line_value_rect_right_of_label(page, r_lab, ("报告编号",))
        y0 = float(r_lab.y0) - 2.0
        y1 = float(r_lab.y1) + 4.0
        write_band = fitz.Rect(x0, y0, x1, y1)
        erase_band = fitz.Rect(write_band)
        for ext in (dr, vr, fb):
            if ext is not None and not ext.is_empty:
                try:
                    erase_band |= ext
                except Exception:
                    pass
        _merge_overlay_erase_transparent(page, erase_band)
        _merge_apply_redactions_overlay(page)
        _merge_overlay_write_cover_centered_band(
            page,
            write_band,
            text,
            align=ac,
            preferred_fontsize=_MERGE_PT_XIAOSI,
            erase_before_write=False,
            value_column_center=False,
        )
        return

    if cov_no_rect is not None and not cov_no_rect.is_empty:
        _merge_overlay_write_in_field_rect(
            page,
            cov_no_rect,
            text,
            align=ac,
            preferred_fontsize=_MERGE_PT_XIAOSI,
        )
        return
    if r_lab is None:
        return
    x0, x1 = _merge_cover_page_center_x_bounds(page)
    fb = _merge_value_rect_same_line(page, r_lab)
    vr = _merge_value_span_union_right_of_label(page, r_lab)
    dr = _merge_dict_line_value_rect_right_of_label(page, r_lab, ("报告编号",))
    y0 = float(r_lab.y0) - 2.0
    y1 = float(r_lab.y1) + 4.0
    write_band = fitz.Rect(x0, y0, x1, y1)
    erase_band = fitz.Rect(write_band)
    for ext in (dr, vr, fb):
        if ext is not None and not ext.is_empty:
            try:
                erase_band |= ext
            except Exception:
                pass
    _merge_overlay_erase_transparent(page, erase_band)
    _merge_apply_redactions_overlay(page)
    _merge_overlay_write_cover_centered_band(
        page,
        write_band,
        text,
        align=ac,
        preferred_fontsize=_MERGE_PT_XIAOSI,
        erase_before_write=False,
        value_column_center=False,
    )


def fill_results_section_inspection_number(doc, inspected_no: str) -> None:
    """在「三、检测结果」首节，按「受检编号」标签定位写入编号（单份报告无 HTMLPDF 栏位时使用）。"""
    if not fitz or doc is None or not (inspected_no or "").strip():
        return
    nos = str(inspected_no).strip()
    in_results = False
    filled = False
    al = int(getattr(fitz, "TEXT_ALIGN_LEFT", 0))
    for pi in range(doc.page_count):
        text = doc[pi].get_text("text")
        compact = re.sub(r"\s+", "", text)
        if "三、检测结果" in compact or "三.检测结果" in compact or "三．检测结果" in compact:
            in_results = True
        if not in_results or filled:
            continue
        if "受检编号" not in compact:
            continue
        page = doc[pi]
        r_idx = _merge_search_first(page, ["受检编号"])
        if r_idx is None:
            continue
        fb = _merge_value_rect_same_line(page, r_idx)
        vr = _merge_value_span_union_right_of_label(page, r_idx)
        dr = _merge_dict_line_value_rect_right_of_label(page, r_idx, ("受检编号",))
        band = (
            dr
            if (dr is not None and not dr.is_empty)
            else (vr if (vr is not None and not vr.is_empty) else fb)
        )
        if band is None or band.is_empty:
            continue
        _merge_overlay_erase_transparent(page, band)
        _merge_apply_redactions_overlay(page)
        _merge_overlay_write_text_html_style(page, band, nos, align=al)
        filled = True


def apply_single_report_cover_overlay_extras(
    doc,
    overlay: Optional[dict],
    *,
    template_parsed: Optional[dict] = None,
) -> None:
    """单份报告封面：仅用标签关键词定位叠印（与合成报告一致，不用模板框映射）。"""
    if not fitz or not overlay or not isinstance(overlay, dict) or doc.page_count < 1:
        return
    page = doc[0]
    org = (overlay.get("cover_inspected_org") or overlay.get("cover_org_line") or "").strip()
    crt = (overlay.get("cover_report_title") or "").strip()
    md = (overlay.get("merge_date_str") or "").strip()
    insp = (overlay.get("cover_inspection_type") or "").strip()
    nos = (overlay.get("inspection_index_line") or "").strip()
    report_no_line = (overlay.get("cover_report_no_line") or "").strip()
    if not report_no_line:
        rn = _sanitize_report_no_for_header((overlay.get("report_no_display") or "").strip())
        if rn:
            report_no_line = f"报告编号：{rn}"
    _apply_report_cover_overlay_by_label_search(
        page,
        org=org,
        cover_title=crt,
        merge_date=md,
        inspection_type=insp,
        report_no_line=report_no_line,
        inspection_index=nos,
        page_index_0based=0,
        use_fixed_title_rect=False,
    )


def clear_report_signature_fields(doc) -> None:
    """擦除编制人/审核人/授权签字人/签发日期等栏位取值，单份自动报告不填写。"""
    if not fitz or doc is None:
        return
    labels = (
        "编制人",
        "审核人",
        "授权签字人",
        "授权人签字",
        "签发日期",
        "报告签发日期",
    )
    for pi in range(doc.page_count):
        page = doc[pi]
        for label in labels:
            for finder in (_merge_search_first, _merge_search_last_by_bottom):
                try:
                    r_lab = finder(page, [label])
                except Exception:
                    r_lab = None
                if r_lab is None:
                    continue
                fb = _merge_value_rect_same_line(page, r_lab)
                vr = _merge_value_span_union_right_of_label(page, r_lab)
                dr = _merge_dict_line_value_rect_right_of_label(page, r_lab, (label,))
                band = (
                    dr
                    if (dr is not None and not dr.is_empty)
                    else (vr if (vr is not None and not vr.is_empty) else fb)
                )
                if band is None or band.is_empty:
                    continue
                _merge_overlay_erase_transparent(page, band + fitz.Rect(-1, -1, 2, 2))
                _merge_apply_redactions_overlay(page)


def _toc_snapshot_page_background_png(page: fitz.Page) -> Optional[bytes]:
    """目录重绘前保留模板页底图（含水印/底纹），避免白底 redact 擦掉背景。"""
    if not fitz or page is None:
        return None
    try:
        pix = page.get_pixmap(dpi=144, alpha=False)
        return pix.tobytes("png")
    except Exception:
        return None


def _toc_restore_page_background(page: fitz.Page, png_bytes: bytes) -> None:
    if not fitz or page is None or not png_bytes:
        return
    try:
        page.insert_image(page.rect, stream=png_bytes, overlay=False)
    except Exception:
        pass


def _find_single_report_results_physical_page(doc) -> Optional[int]:
    if not fitz:
        return None
    for pi in range(2, doc.page_count):
        compact = re.sub(r"\s+", "", doc[pi].get_text("text") or "")
        if "三、检测结果" in compact or "三.检测结果" in compact:
            return pi + 1
    return None


def _find_single_report_toc_subsection_physical_pages(
    doc,
    *,
    results_phys: int,
    toc_page_0based: Optional[int] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """在「三、检测结果」页及之后定位 1.1 / 1.2 首次出现的物理页（1-based）。"""
    if not fitz:
        return None, None
    qc_phys: Optional[int] = None
    rp_phys: Optional[int] = None
    start_pi = max(2, int(results_phys) - 1)
    for pi in range(start_pi, doc.page_count):
        if toc_page_0based is not None and pi == toc_page_0based:
            continue
        phys = pi + 1
        for raw_line in (doc[pi].get_text("text") or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            m = _TOC_MINOR_LINE_RE.match(line)
            if not m:
                continue
            maj, mino = int(m.group(1)), int(m.group(2))
            rest = re.sub(r"\s+", "", m.group(3))
            if maj == 1 and mino == 1 and "质量控制" in rest and qc_phys is None:
                qc_phys = phys
            if maj == 1 and mino == 2 and "工作场所放射防护" in rest and rp_phys is None:
                rp_phys = phys
        if qc_phys is not None and rp_phys is not None:
            break
    return qc_phys, rp_phys


def _build_single_report_toc_entries_merged_style(
    doc,
    *,
    device_title: str = "",
    has_radiation_protection: bool = True,
    toc_page_0based: Optional[int] = None,
) -> List[Tuple[str, str, int]]:
    """
    单份报告目录：与合并报告一致——一级为受检设备标题，二级为 1.1 质控 / 1.2 防护。
    """
    if not fitz:
        return []
    title = _normalize_rp_phrase_spacing((device_title or "").strip())
    results_phys = _find_single_report_results_physical_page(doc)
    if results_phys is None:
        return []
    qc_phys, rp_phys = _find_single_report_toc_subsection_physical_pages(
        doc,
        results_phys=results_phys,
        toc_page_0based=toc_page_0based,
    )
    if not title:
        title = "检测结果"
    main_rp = _report_body_page_from_physical_1based(results_phys)
    entries: List[Tuple[str, str, int]] = [("major", f"一、{title}", main_rp)]
    qc_phys_eff = qc_phys if qc_phys is not None else results_phys
    qc_page = _report_body_page_from_physical_1based(qc_phys_eff)
    if qc_page > 0:
        entries.append(("minor", "1.1  质量控制检测项目及结果", qc_page))
    if has_radiation_protection:
        if rp_phys is not None:
            rp_page = _report_body_page_from_physical_1based(rp_phys)
        elif qc_phys_eff is not None:
            rp_page = _report_body_page_from_physical_1based(qc_phys_eff + 1)
        else:
            rp_page = 0
        if rp_page > 0:
            entries.append(("minor", "1.2  工作场所放射防护检测结果", rp_page))
    return entries


def _collect_single_report_toc_entries(
    doc,
    *,
    has_radiation_protection: bool = True,
) -> List[Tuple[str, str, int]]:
    """
    扫描全文标题，生成目录行：(level, left_text, report_page_num)。
    level 为 major / minor；保留全部次级标题（1.1、1.2…）。
    """
    if not fitz:
        return []
    seen: Dict[str, int] = {}
    ordered: List[Tuple[str, str, int]] = []

    def _skip_without_radiation(text: str) -> bool:
        if has_radiation_protection:
            return False
        compact = re.sub(r"\s+", "", text or "")
        return (
            "工作场所放射防护" in compact
            or "平面布局" in compact
            or "检测点方位图" in compact
        )

    def _push(level: str, text: str, phys_1: int) -> None:
        t = re.sub(r"\s+", " ", text or "").strip()
        if len(t) < 3:
            return
        if _skip_without_radiation(t):
            return
        if "错误!未定义书签" in t:
            return
        rp = _report_body_page_from_physical_1based(phys_1)
        if rp <= 0:
            return
        key = f"{level}:{t}"
        if key in seen:
            return
        seen[key] = rp
        ordered.append((level, t, rp))

    for pi in range(2, doc.page_count):
        phys = pi + 1
        for raw_line in (doc[pi].get_text("text") or "").splitlines():
            line = raw_line.strip()
            if not line or len(line) > 160:
                continue
            m_maj = _TOC_MAJOR_LINE_RE.match(line)
            if m_maj:
                title = f"{m_maj.group(1)}、{_norm_toc_text(m_maj.group(2))}"
                if len(title) >= 4:
                    _push("major", title, phys)
                continue
            m_min = _TOC_MINOR_LINE_RE.match(line)
            if m_min:
                title = f"{m_min.group(1)}.{m_min.group(2)}  {_norm_toc_text(m_min.group(3))}"
                if len(title) >= 6:
                    _push("minor", title, phys)

    ordered.sort(key=lambda x: (x[2], 0 if x[0] == "major" else 1, x[1]))
    return ordered


def regenerate_single_report_table_of_contents(
    doc,
    *,
    report_no: str = "",
    has_radiation_protection: bool = True,
    device_title: str = "",
    profile: Optional["ReportTemplateProfile"] = None,
) -> None:
    """擦除模板目录残留，铺水印底图并重绘全部一/二级目录行。"""
    if not fitz or doc is None or doc.page_count < 4:
        return
    toc_idx: Optional[int] = None
    for pi in range(2, min(doc.page_count, 8)):
        compact = re.sub(r"\s+", "", doc[pi].get_text("text"))
        if "目录" in compact:
            toc_idx = pi
            break
    if toc_idx is None:
        return

    page = doc[toc_idx]
    w, h = float(page.rect.width), float(page.rect.height)
    geom = _merged_toc_geometry_for_size(w, h)
    prof = _resolve_report_template_profile(profile)
    toc_rx = prof.page_header_right_x1 or prof.basic_info_table_x1
    if toc_rx is not None:
        geom = dict(geom)
        geom["num_right_x"] = float(toc_rx)
    # 按文字块红区，保留模板页矢量底纹/水印；整块白擦会抹掉背景图
    _redact_original_toc_text_spans(page, geom)
    if not page.get_images():
        _merged_report_insert_watermark_bottom_layer(page, w, h)
    font_main = _register_simsun_on_page(page)
    _draw_merged_toc_title_mu_lu(page, geom, font_main)

    entries = _build_single_report_toc_entries_merged_style(
        doc,
        device_title=device_title,
        has_radiation_protection=has_radiation_protection,
        toc_page_0based=toc_idx,
    )
    scanned = _collect_single_report_toc_entries(
        doc,
        has_radiation_protection=has_radiation_protection,
    )
    scanned_minors = sum(1 for e in scanned if e[0] == "minor")
    merged_minors = sum(1 for e in entries if e[0] == "minor")
    if scanned_minors >= max(merged_minors + 1, 3):
        entries = scanned
    elif not entries:
        entries = scanned
    if not entries:
        return

    y = float(geom.get("first_entry_baseline") or 136.0)
    rm = 52.0
    fs = float(geom.get("body_fs") or 12.0)
    line_step = max(12.0, float(geom.get("line_step") or fs * 1.65))
    num_rx_f = float(geom.get("num_right_x") or w - 52.0)
    link_plan: List[Tuple[fitz.Rect, int]] = []

    for level, left, page_display in entries:
        if y > h - 72:
            break
        left_x = float(geom.get("sub_left_x") or 105.0) if level == "minor" else float(geom.get("entry_left_x") or 71.0)
        rect = _draw_toc_row_leader_rightnum(
            page,
            y,
            w,
            left_x,
            rm,
            left,
            page_display,
            font_main,
            fs,
            num_right_x=num_rx_f,
        )
        dest_0 = None
        for pi in range(2, doc.page_count):
            if _report_body_page_from_physical_1based(pi + 1) == page_display:
                dest_0 = pi
                break
        if dest_0 is not None:
            link_plan.append((rect, dest_0))
        y += line_step

    for rect, dest_0 in link_plan:
        if dest_0 < 0 or dest_0 >= doc.page_count:
            continue
        try:
            page.insert_link(
                {
                    "kind": fitz.LINK_GOTO,
                    "from": rect,
                    "page": dest_0,
                    "to": fitz.Point(72, 96),
                }
            )
        except Exception as exc:
            logger.debug("toc link: %s", exc)


def _draw_merged_toc_title_mu_lu(page, geom: Dict[str, Any], fontname: str) -> None:
    """按院方目录样例：居中「目」「录」两大字（常规字重，不加粗）。"""
    if not fitz or not geom.get("toc_title_two_chars"):
        return
    fs = float(geom.get("title_fs") or 22.0)
    y = float(geom.get("title_baseline") or 94.8)
    x_mu = float(geom.get("title_mu_x0") or 259.0)
    x_lu = float(geom.get("title_lu_x0") or 314.0)
    for ch, x0 in (("目", x_mu), ("录", x_lu)):
        _fitz_insert_text_line(page, x0, y, ch, fs, fontname)


def _draw_toc_row_leader_rightnum(
    page,
    y: float,
    page_width: float,
    left_margin: float,
    right_margin: float,
    left_text: str,
    page_num_1based: int,
    fontname: str,
    fontsize: float,
    *,
    num_right_x: Optional[float] = None,
) -> fitz.Rect:
    """
    绘制「左侧标题 + 点线填满至页码前 + 右对齐页码」；标题与点线、点线与页码之间各留少量空。
    返回整行可点击区域（用于 PDF 内跳转）。
    """
    page_num_str = str(int(page_num_1based))
    tw_l = _fitz_text_width(left_text, fontname, fontsize, page=page)
    tw_n = _fitz_text_width(page_num_str, fontname, fontsize, page=page)
    pad_after_title = 2.0
    pad_before_page = 2.5
    if num_right_x is not None:
        num_x = float(num_right_x) - tw_n
    else:
        num_x = page_width - right_margin - tw_n
    leader_start = left_margin + tw_l + pad_after_title
    leader_end = num_x - pad_before_page
    available = max(0.0, float(leader_end - leader_start))
    leader = ""
    if available >= 1.2:
        # 中文目录常用间隔号「·」作点线，比半角「.」更易铺满且视觉均匀
        dot_ch = "·"
        wd_dot = _fitz_text_width(dot_ch, fontname, fontsize, page=page)
        if wd_dot < 0.08 * fontsize:
            wd_dot = max(0.08 * fontsize, 0.35)
        # 用单字宽估算个数再微调，避免逐串累加导致字宽度量漂移、点过少留白
        est = int(max(0, (available - 0.4) / wd_dot))
        leader = dot_ch * min(est, 4000)
        while leader and _fitz_text_width(leader, fontname, fontsize, page=page) > available + 0.08:
            leader = leader[:-1]
        while len(leader) < 4000:
            trial = leader + dot_ch
            if _fitz_text_width(trial, fontname, fontsize, page=page) <= available + 0.08:
                leader = trial
            else:
                break
    _fitz_insert_text_line(page, left_margin, y, left_text, fontsize, fontname)
    if leader:
        _fitz_insert_text_line(page, leader_start, y, leader, fontsize, fontname)
    _fitz_insert_text_line(page, num_x, y, page_num_str, fontsize, fontname)
    line_h = fontsize * 1.45
    return fitz.Rect(left_margin, y - fontsize * 0.85, page_width - right_margin, y + line_h * 0.35)


def _merge_search_last_by_bottom(page, needles: Sequence[str]) -> Optional[fitz.Rect]:
    """同一页多处命中时取最靠下的一处（基本情况表中「项目名称」常低于页眉重复词）。"""
    if not fitz or page is None:
        return None
    flags = 0
    for name in ("TEXT_DEHYPHENATE", "TEXT_PRESERVE_LIGATURES"):
        v = getattr(fitz, name, None)
        if isinstance(v, int):
            flags |= v
    best: Optional[fitz.Rect] = None
    best_y = -1e9
    for nd in needles:
        q = (nd or "").strip()
        if not q:
            continue
        try:
            hits = page.search_for(q, flags=flags) if flags else page.search_for(q)
        except TypeError:
            try:
                hits = page.search_for(q)
            except Exception:
                hits = []
        except Exception:
            hits = []
        for h in hits or []:
            try:
                rr = fitz.Rect(h)
            except Exception:
                continue
            if float(rr.y0) >= best_y:
                best_y = float(rr.y0)
                best = rr
    return best


def _merge_union_search_hits_first_line(hits: Sequence[Any]) -> Optional[Any]:
    """
    PyMuPDF search_for 在标签逐字分 span 时可能返回多个 Rect（如「报告日期」四字四框）。
    取纵坐标最靠上的一行，将该行所有命中并集为完整标签框。
    """
    if not fitz or not hits:
        return None
    rects: List[Any] = []
    for h in hits:
        try:
            r = fitz.Rect(h)
        except Exception:
            continue
        if r.is_empty:
            continue
        rects.append(r)
    if not rects:
        return None
    if len(rects) == 1:
        return rects[0]

    def _ym(r: Any) -> float:
        return (float(r.y0) + float(r.y1)) * 0.5

    rects.sort(key=lambda r: (_ym(r), float(r.x0)))
    anchor_y = _ym(rects[0])
    y_tol = max(4.0, float(rects[0].height) * 0.85)
    line_rects = [rects[0]]
    for r in rects[1:]:
        if abs(_ym(r) - anchor_y) <= y_tol:
            line_rects.append(r)
        else:
            break
    u = line_rects[0]
    for r in line_rects[1:]:
        u |= r
    return u


def _merge_search_label_rect_from_dict(page, needle: str) -> Optional[Any]:
    """从文本层 dict 按行匹配标签，返回整段标签 span 并集（避免 search_for 只命中首字）。"""
    if not fitz or page is None:
        return None
    q = re.sub(r"\s+", "", (needle or "").strip())
    if not q:
        return None
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            lt = _merge_line_compact_text(line)
            if q not in lt:
                continue
            u = _merge_span_prefix_union_until_needle(line, q)
            if u is not None and not u.is_empty:
                return u
    return None


def _merge_label_rect_looks_partial(rect: Any, needle: str) -> bool:
    """单命中但宽度过窄时，可能是 PDF 逐字分 span 只返回了首字框。"""
    if not fitz or rect is None or rect.is_empty:
        return True
    q_compact = re.sub(r"\s+", "", (needle or "").strip())
    if not q_compact:
        return False
    # 汉字标签约 10～14pt/字；低于约 0.65 倍预期宽度视为残缺
    min_w = max(18.0, len(q_compact) * 10.5)
    return float(rect.width) < min_w * 0.65


def _merge_search_first(page, needles: Sequence[str]) -> Optional[fitz.Rect]:
    if not fitz or page is None:
        return None
    flags = 0
    for name in ("TEXT_DEHYPHENATE", "TEXT_PRESERVE_LIGATURES"):
        v = getattr(fitz, name, None)
        if isinstance(v, int):
            flags |= v
    for nd in needles:
        q = (nd or "").strip()
        if not q:
            continue
        try:
            hits = page.search_for(q, flags=flags) if flags else page.search_for(q)
        except TypeError:
            try:
                hits = page.search_for(q)
            except Exception:
                hits = []
        except Exception:
            hits = []
        if not hits:
            continue
        if len(hits) > 1:
            merged = _merge_union_search_hits_first_line(hits)
            if merged is not None and not merged.is_empty:
                return merged
        try:
            single = fitz.Rect(hits[0])
        except Exception:
            continue
        if not _merge_label_rect_looks_partial(single, q):
            return single
        # 极少数 PDF 仅返回一字宽命中：再扫文本层取整段标签（慢路径）
        u = _merge_search_label_rect_from_dict(page, q)
        if u is not None and not u.is_empty:
            return u
        return single
    return None


def _merge_redact_rect(page, rr: fitz.Rect, pad: float = 1.0) -> None:
    if not fitz or rr is None or rr.is_empty:
        return
    page.add_redact_annot(rr + fitz.Rect(-pad, -pad, pad, pad), fill=(1, 1, 1))


def _merge_overlay_erase_transparent(page, rr: fitz.Rect, pad: float = 0.6) -> None:
    """
    合并叠印用擦除：去掉底层文字/线画，不铺白底（避免「白底块」）。
    与 build_filled_pdf 仅用 overlay 叠字不同处：需先清掉原印刷字再写新字。

    注意：PyMuPDF 文档写明默认 fill 为白；``fill=None`` 仍按白底处理，
    必须显式 ``fill=False`` 才能在 apply_redactions 后保持透明。
    """
    if not fitz or rr is None or rr.is_empty:
        return
    r = rr + fitz.Rect(-pad, -pad, pad, pad)
    try:
        page.add_redact_annot(r, fill=False)
    except TypeError:
        try:
            page.add_redact_annot(r, fill=())
        except TypeError:
            page.add_redact_annot(r)


def _merge_apply_redactions_overlay(page) -> None:
    """叠印擦除后应用 redact；尽量不破坏底层图元。"""
    if not fitz or page is None:
        return
    img_none = getattr(fitz, "PDF_REDACT_IMAGE_NONE", None)
    kwargs: Dict[str, Any] = {}
    if img_none is not None:
        kwargs["images"] = img_none
    try:
        page.apply_redactions(**kwargs)
    except TypeError:
        page.apply_redactions()


def _merge_dict_line_value_rect_right_of_label(
    page, label_rect: fitz.Rect, line_needles: Sequence[str]
) -> Optional[fitz.Rect]:
    """
    在标签所在文本行内，按 search_for 得到的 label_rect 取「标签右缘以右」的 span 并集。
    与同一行字典 bbox 对齐，通常比纯 search_for 取值区更贴下划线。
    """
    if not fitz or label_rect is None or label_rect.is_empty:
        return None
    y_mid = (float(label_rect.y0) + float(label_rect.y1)) * 0.5
    y_tol = max(7.0, float(label_rect.height) * 1.15)
    pw = float(page.rect.width)
    toks = [re.sub(r"\s+", "", (n or "")) for n in line_needles if (n or "").strip()]
    if not toks:
        return None
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            lb = _line_bbox_union(spans)
            if lb.is_empty:
                continue
            lmid = (float(lb.y0) + float(lb.y1)) * 0.5
            if abs(lmid - y_mid) > y_tol:
                continue
            lt_c = re.sub(r"\s+", "", _line_text(line))
            if not any(t in lt_c for t in toks):
                continue
            parts: List[fitz.Rect] = []
            for sp in spans:
                b = sp.get("bbox")
                if not b or len(b) < 4:
                    continue
                sr = fitz.Rect(float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                if float(sr.x0) >= float(label_rect.x1) + 0.8 and float(sr.x0) <= pw - 6.0:
                    parts.append(sr)
            if not parts:
                continue
            u = parts[0]
            for p in parts[1:]:
                u |= p
            ext = max(2.5, float(u.height) * 0.2)
            u.y1 = min(float(page.rect.height) - 4.0, float(u.y1) + ext)
            return u
    return None


def _merge_basic_info_section_y_bounds(page) -> Tuple[Optional[float], Optional[float]]:
    """(「一、项目基本情况」标题下缘, 「受检设备台数/受检工作场所」行上缘)，用于锁定表中「项目名称」。"""
    y_after_heading: Optional[float] = None
    y_before_devices: Optional[float] = None
    if not fitz or page is None:
        return None, None
    d = page.get_text("dict") or {}
    heads = ("一、项目基本情况", "一.项目基本情况", "一．项目基本情况", "一、项目基本情")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            t = re.sub(r"\s+", "", _line_text(line))
            bbox = _line_bbox_union(spans)
            if bbox.is_empty:
                continue
            if any(h in t for h in heads):
                y_after_heading = max(y_after_heading or 0.0, float(bbox.y1))
            if _line_has_summary_device_count_label(line):
                cand = float(bbox.y0)
                # 取「基本情况」标题之下的首行台数，避免页眉/页脚重复词把 y_hi 顶到错误纵坐标
                if y_after_heading is not None and cand <= float(y_after_heading) + 2.0:
                    continue
                y_before_devices = cand if y_before_devices is None else min(y_before_devices, cand)
    return y_after_heading, y_before_devices


def _merge_line_compact_text(line: dict) -> str:
    return re.sub(r"\s+", "", _line_text(line))


def _merge_span_prefix_union_until_needle(line: dict, needle: str) -> Optional[fitz.Rect]:
    """同一行从左累加 span 文本直至覆盖 needle，返回对应 span 并集。"""
    nd = re.sub(r"\s+", "", needle)
    spans = line.get("spans") or []
    if not spans or not nd:
        return None
    parts: List[fitz.Rect] = []
    acc = ""
    for sp in sorted(
        spans,
        key=lambda s: float((s.get("bbox") or [0.0, 0.0, 0.0, 0.0])[0]),
    ):
        acc += re.sub(r"\s+", "", str(sp.get("text", "") or ""))
        bb = sp.get("bbox")
        if bb and len(bb) >= 4:
            parts.append(fitz.Rect(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])))
        if nd in acc:
            break
    if not parts:
        return None
    u = parts[0]
    for p in parts[1:]:
        u |= p
    return u


def _merge_basic_info_table_label_rect_from_line(
    page, needle: str, *, y_lo: Optional[float], y_hi: Optional[float]
) -> Optional[fitz.Rect]:
    """
    按表格行定位标签小框：整行去空白后包含 needle（可跨多个 span、中间任意空格），
    从左向右累加 span 文本直至覆盖 needle，取这些 span 的 bbox 并集。
    """
    if not fitz or page is None or not (needle or "").strip():
        return None
    nd = re.sub(r"\s+", "", needle)
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            lb = _line_bbox_union(spans)
            if lb.is_empty:
                continue
            cy = (float(lb.y0) + float(lb.y1)) * 0.5
            if y_lo is not None and cy < float(y_lo) + 0.8:
                continue
            if y_hi is not None and cy >= float(y_hi) - 1.0:
                continue
            if nd not in _merge_line_compact_text(line):
                continue
            u = _merge_span_prefix_union_until_needle(line, needle)
            if u is not None and not u.is_empty:
                return u
    return _merge_basic_info_vertical_label_rect(page, needle, y_lo=y_lo, y_hi=y_hi)


def _merge_basic_info_vertical_label_rect(
    page,
    needle: str,
    *,
    y_lo: Optional[float],
    y_hi: Optional[float],
) -> Optional[fitz.Rect]:
    """基本情况表左栏竖排标签（如「项/目/名/称」拼成「项目名称」）。"""
    if not fitz or page is None or not (needle or "").strip():
        return None
    nd = re.sub(r"\s+", "", needle)
    if len(nd) < 2:
        return None
    rows: Dict[float, List[Tuple[str, fitz.Rect]]] = {}
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            lb = _line_bbox_union(spans)
            if lb.is_empty:
                continue
            cy = (float(lb.y0) + float(lb.y1)) * 0.5
            if y_lo is not None and cy < float(y_lo) + 0.8:
                continue
            if y_hi is not None and cy >= float(y_hi) - 1.0:
                continue
            y_key = round(float(lb.y0), 1)
            for sp in spans:
                bb = sp.get("bbox")
                if not bb or len(bb) < 4:
                    continue
                sr = fitz.Rect(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                if float(sr.x1) > 185.0:
                    continue
                raw = re.sub(r"\s+", "", str(sp.get("text", "") or ""))
                if not raw:
                    continue
                rows.setdefault(y_key, []).append((raw, sr))
    best: Optional[fitz.Rect] = None
    for _y_key, items in rows.items():
        items.sort(key=lambda it: float(it[1].x0))
        compact = "".join(ch for ch, _ in items)
        if compact != nd and nd not in compact:
            continue
        parts = [r for _, r in items]
        u = parts[0]
        for p in parts[1:]:
            u |= p
        if best is None or float(u.y0) < float(best.y0):
            best = u
    return best


def _merge_basic_info_label_row_y0(
    page,
    needle: str,
    *,
    y_lo: Optional[float] = None,
    y_hi: Optional[float] = None,
) -> Optional[float]:
    """基本情况表内某标签所在行的上缘 y0。"""
    cell = _merge_basic_info_table_label_rect_from_line(page, needle, y_lo=y_lo, y_hi=y_hi)
    if cell is None or cell.is_empty:
        return None
    return float(cell.y0)


def _merge_span_is_device_count_unit_label(text: str) -> bool:
    t = re.sub(r"\s+", "", str(text or ""))
    return t in ("台",)


def _merge_basic_info_device_count_value_rect(
    page,
    *,
    exclude_unit_span: bool = False,
) -> Optional[fitz.Rect]:
    """基本情况表内「受检设备台数 / 受检工作场所」同行右侧取值单元格。"""
    if not fitz or page is None:
        return None
    y_lo, _y_hi = _merge_basic_info_section_y_bounds(page)
    d = page.get_text("dict") or {}
    best_line: Optional[dict] = None
    best_nd: str = ""
    best_y0: Optional[float] = None
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            lb = _line_bbox_union(spans)
            if lb.is_empty:
                continue
            cy0 = float(lb.y0)
            if y_lo is not None and cy0 <= float(y_lo) + 2.0:
                continue
            tcompact = _merge_line_compact_text(line)
            matched_nd = next((n for n in _summary_device_count_label_needles() if n in tcompact), "")
            if not matched_nd:
                continue
            if best_y0 is None or cy0 < best_y0:
                best_y0 = cy0
                best_line = line
                best_nd = matched_nd
    if not best_line or not best_nd:
        return None
    spans = best_line.get("spans") or []
    lab = _merge_span_prefix_union_until_needle(best_line, best_nd)
    if lab is None or lab.is_empty:
        return None
    lx1 = float(lab.x1) + 0.5
    parts: List[fitz.Rect] = []
    for sp in spans:
        bb = sp.get("bbox")
        if not bb or len(bb) < 4:
            continue
        if exclude_unit_span and _merge_span_is_device_count_unit_label(sp.get("text", "")):
            continue
        sr = fitz.Rect(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
        if float(sr.x0) >= lx1 - 0.5:
            parts.append(sr)
    if not parts:
        y_mid = (float(lab.y0) + float(lab.y1)) * 0.5
        y_tol = max(4.0, float(lab.height) * 0.75)
        d2 = page.get_text("dict") or {}
        for block in d2.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for sp in line.get("spans") or []:
                    bb = sp.get("bbox")
                    if not bb or len(bb) < 4:
                        continue
                    if exclude_unit_span and _merge_span_is_device_count_unit_label(sp.get("text", "")):
                        continue
                    sr = fitz.Rect(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                    lmid = (float(sr.y0) + float(sr.y1)) * 0.5
                    if abs(lmid - y_mid) > y_tol:
                        continue
                    if float(sr.x0) >= lx1 - 0.5:
                        parts.append(sr)
    if not parts:
        mr = _merge_page_right_margin(page)
        pw = float(page.rect.width)
        return fitz.Rect(
            lx1 + 1.0,
            float(lab.y0) - 1.5,
            pw - mr,
            float(lab.y1) + 1.5,
        )
    u = parts[0]
    for p in parts[1:]:
        u |= p
    return u


def _merge_basic_info_project_name_label_rect(page) -> Optional[fitz.Rect]:
    """基本情况页表格内「项目名称」标签小框（按行/单元格解析，兼容 span 拆分与多空格）。"""
    if not fitz or page is None:
        return None
    y_lo, y_hi = _merge_basic_info_section_y_bounds(page)
    cell = _merge_basic_info_table_label_rect_from_line(
        page, "项目名称", y_lo=y_lo, y_hi=y_hi
    )
    if cell is not None and not cell.is_empty:
        return cell
    d = page.get_text("dict") or {}
    candidates: List[fitz.Rect] = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            for sp in spans:
                raw = str(sp.get("text", "") or "")
                if "项目名称" not in raw:
                    continue
                b = sp.get("bbox")
                if not b or len(b) < 4:
                    continue
                sr = fitz.Rect(float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                cy = (float(sr.y0) + float(sr.y1)) * 0.5
                if y_lo is not None and cy < float(y_lo) + 1.0:
                    continue
                if y_hi is not None and cy >= float(y_hi) - 2.0:
                    continue
                if float(sr.y1) < 72.0:
                    continue
                candidates.append(sr)
    if not candidates:
        return None
    return min(candidates, key=lambda r: float(r.y0))


def _merge_value_span_union_right_of_label(page, label_rect: fitz.Rect) -> Optional[fitz.Rect]:
    """与标签同一行、且位于标签右侧的 span 并集（坐标来自 PDF 文本层，与 htmlpdf 模板框一致）。"""
    if not fitz or label_rect is None or label_rect.is_empty:
        return None
    y_mid = (float(label_rect.y0) + float(label_rect.y1)) * 0.5
    y_tol = max(5.5, float(label_rect.height) * 0.95)
    pw = float(page.rect.width)
    d = page.get_text("dict") or {}
    candidates: List[fitz.Rect] = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            lb = _line_bbox_union(spans)
            if lb.is_empty:
                continue
            if lb.x1 < float(label_rect.x0) - 40.0 or lb.x0 > float(label_rect.x1) + 220.0:
                continue
            lmid = (float(lb.y0) + float(lb.y1)) * 0.5
            if abs(lmid - y_mid) > y_tol:
                continue
            parts: List[fitz.Rect] = []
            for sp in spans:
                b = sp.get("bbox")
                if not b or len(b) < 4:
                    continue
                sr = fitz.Rect(float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                if float(sr.x0) >= float(label_rect.x1) + 0.6 and float(sr.x0) <= pw - 4.0:
                    parts.append(sr)
            if not parts:
                continue
            u = parts[0]
            for p in parts[1:]:
                u |= p
            candidates.append(u)
    if not candidates:
        return None
    best: Optional[fitz.Rect] = None
    best_score = -1.0
    for u in candidates:
        iy = min(float(label_rect.y1), float(u.y1)) - max(float(label_rect.y0), float(u.y0))
        score = float(u.width) + max(0.0, iy) * 3.0
        if score > best_score:
            best_score = score
            best = u
    return best


def _merge_page_line_segments(page) -> List[Tuple[float, float, float, float]]:
    """页面矢量线段 (x0,y0,x1,y1)，用于识别下划线。"""
    out: List[Tuple[float, float, float, float]] = []
    if not fitz or page is None:
        return out
    try:
        drawings = page.get_drawings() or []
    except Exception:
        return out
    for d in drawings:
        for item in d.get("items") or []:
            if not item:
                continue
            if item[0] == "l" and len(item) >= 3:
                p0, p1 = item[1], item[2]
                out.append((float(p0.x), float(p0.y), float(p1.x), float(p1.y)))
                continue
            if item[0] == "re" and len(item) >= 2:
                rr = item[1]
                try:
                    x0, y0, x1, y1 = float(rr.x0), float(rr.y0), float(rr.x1), float(rr.y1)
                except Exception:
                    continue
                if abs(y1 - y0) <= 3.5 and abs(x1 - x0) >= 36.0:
                    out.append((x0, y0, x1, y1))
    return out


def _merge_collect_horizontal_line_segments_in_rect(
    page,
    rect: fitz.Rect,
    *,
    min_length: float = 36.0,
) -> List[Tuple[float, float, float, float, float]]:
    """收集与 rect 相交的水平线段 (x0, y, x1, y, width)，供擦除后重绘下划线。"""
    if not fitz or page is None or rect is None or rect.is_empty:
        return []
    yt, yb = float(rect.y0), float(rect.y1)
    xl, xr = float(rect.x0), float(rect.x1)
    out: List[Tuple[float, float, float, float, float]] = []
    try:
        drawings = page.get_drawings() or []
    except Exception:
        return out
    for d in drawings:
        width = float(d.get("width") or 1.0)
        for item in d.get("items") or []:
            if not item:
                continue
            if item[0] == "l" and len(item) >= 3:
                p0, p1 = item[1], item[2]
                x0, y0, x1, y1 = float(p0.x), float(p0.y), float(p1.x), float(p1.y)
                if abs(y1 - y0) > 3.5:
                    continue
                ym = (y0 + y1) * 0.5
                if ym < yt - 2.0 or ym > yb + 2.0:
                    continue
                xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
                if xb < xl - 12.0 or xa > xr + 12.0:
                    continue
                if abs(xb - xa) < min_length:
                    continue
                out.append((xa, ym, xb, ym, width))
                continue
            if item[0] == "re" and len(item) >= 2:
                rr = item[1]
                try:
                    x0, y0, x1, y1 = float(rr.x0), float(rr.y0), float(rr.x1), float(rr.y1)
                except Exception:
                    continue
                if abs(y1 - y0) > 3.5 or abs(x1 - x0) < min_length:
                    continue
                ym = (y0 + y1) * 0.5
                if ym < yt - 2.0 or ym > yb + 2.0:
                    continue
                xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
                if xb < xl - 12.0 or xa > xr + 12.0:
                    continue
                out.append((xa, ym, xb, ym, width))
    return out


def _merge_redraw_horizontal_line_segments(
    page,
    segments: Sequence[Tuple[float, float, float, float, float]],
) -> None:
    """擦除叠印后按原坐标重绘水平下划线。"""
    if not fitz or page is None or not segments:
        return
    for xa, y0, xb, y1, width in segments:
        try:
            page.draw_line(
                fitz.Point(xa, y0),
                fitz.Point(xb, y1),
                color=(0.0, 0.0, 0.0),
                width=max(0.5, float(width or 1.0)),
            )
        except Exception:
            continue


def _merge_underline_x_bounds_in_band(
    page,
    band: fitz.Rect,
    label_rect: fitz.Rect,
    *,
    y_sub_top: Optional[float] = None,
    y_sub_bot: Optional[float] = None,
) -> Tuple[float, float, Optional[float]]:
    """
    在 band 与标签行附近找近似水平长划线，返回 (x0, x1, 中线 y)；若无则 (band.x0, band.x1, None)。
    y_sub_top/y_sub_bot：可选，将搜索纵带限制在子矩形（如封面项目名称上/下格）。
    """
    if not fitz or band is None or band.is_empty:
        return 0.0, 0.0, None
    yt = float(y_sub_top) if y_sub_top is not None else float(min(band.y0, label_rect.y0)) - 4.0
    yb = float(y_sub_bot) if y_sub_bot is not None else float(max(band.y1, label_rect.y1)) + 26.0
    segs = _merge_page_line_segments(page)
    candidates: List[Tuple[float, float, float]] = []
    lx1 = float(label_rect.x1)
    bx0, bx1 = float(band.x0), float(band.x1)
    for x0, y0, x1, y1 in segs:
        xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
        ya, yb2 = (y0, y1) if y0 <= y1 else (y1, y0)
        if abs(yb2 - ya) > 3.5:
            continue
        ym = (ya + yb2) * 0.5
        if ym < yt or ym > yb:
            continue
        length = abs(xb - xa)
        if length < 36.0:
            continue
        if xb < lx1 - 12.0:
            continue
        overlap = min(xb, bx1 + 40.0) - max(xa, bx0 - 40.0)
        if overlap < min(36.0, 0.22 * max(length, float(band.width))):
            continue
        candidates.append((length, xa, xb, ym))
    if not candidates:
        return bx0, bx1, None
    length, xa, xb, ym = max(candidates, key=lambda t: t[0])
    return xa, xb, ym


def _merge_cover_write_rect_above_underline(
    page, band: fitz.Rect, label_rect: fitz.Rect, *, y_sub_top: Optional[float] = None, y_sub_bot: Optional[float] = None
) -> fitz.Rect:
    """根据下划线定左右边界；竖向取标签行至上方的书写区（字在下划线上方）。"""
    x0, x1, umy = _merge_underline_x_bounds_in_band(page, band, label_rect, y_sub_top=y_sub_top, y_sub_bot=y_sub_bot)
    pad_x = 2.0
    x0 = max(4.0, x0 + pad_x)
    x1 = min(float(page.rect.width) - 4.0, x1 - pad_x)
    if x1 <= x0 + 8.0:
        x0, x1 = float(band.x0) + 1.0, float(band.x1) - 1.0
    y0 = float(min(band.y0, label_rect.y0)) - 1.0
    if umy is not None and umy > y0 + 8.0:
        y1 = min(float(band.y1) + 6.0, umy - 1.2)
    else:
        y1 = float(max(band.y1, label_rect.y1)) + 3.0
    if y_sub_top is not None:
        y0 = max(y0, float(y_sub_top) - 0.5)
    if y_sub_bot is not None:
        y1 = min(y1, float(y_sub_bot) - 1.0)
    if y1 <= y0 + 6.0:
        y1 = float(band.y1) + 4.0
    return fitz.Rect(x0, y0, x1, y1)


def _merge_cover_field_write_rect_safe(
    page,
    band: fitz.Rect,
    label_rect: fitz.Rect,
    *,
    y_sub_top: Optional[float] = None,
    y_sub_bot: Optional[float] = None,
    min_height: float = 14.0,
) -> fitz.Rect:
    """封面项目名称格：下划线定宽；若竖向过扁则回退为子带内安全矩形，避免叠字空白。"""
    wr = _merge_cover_write_rect_above_underline(
        page, band, label_rect, y_sub_top=y_sub_top, y_sub_bot=y_sub_bot
    )
    sub_top = float(y_sub_top) if y_sub_top is not None else float(band.y0)
    sub_bot = float(y_sub_bot) if y_sub_bot is not None else float(band.y1)
    if wr.height >= min_height and wr.width >= 36.0 and not wr.is_empty:
        return wr
    x0, x1, _um = _merge_underline_x_bounds_in_band(
        page, band, label_rect, y_sub_top=y_sub_top, y_sub_bot=y_sub_bot
    )
    pw = float(page.rect.width)
    pad = 2.0
    xa = max(4.0, float(band.x0) + 1.0, x0 + pad)
    xb = min(pw - 4.0, float(band.x1) - 1.0, x1 - pad)
    if xb <= xa + 10.0:
        xa, xb = float(band.x0) + 1.0, float(band.x1) - 1.0
    y0 = sub_top + 0.8
    y1 = max(sub_bot - 0.8, y0 + min_height + 2.0)
    return fitz.Rect(xa, y0, xb, y1)


def _merge_cover_project_name_write_band_above_underline(
    page,
    label_rect: fitz.Rect,
    umy: float,
    *,
    x0: float,
    x1: float,
    y1_cap: Optional[float] = None,
    profile: Optional["ReportTemplateProfile"] = None,
) -> fitz.Rect:
    """封面「项目名称」两行整体落在上划线上方：自下而上定高，两行同步上移。"""
    prof = _resolve_report_template_profile(profile)
    gap = float(prof.cover_project_name_line2_above_underline_pt or 9.0)
    lh = max(13.5, _MERGE_PT_SIHAO * 1.12)
    line_gap = 1.2
    block_h = lh * 2.0 + line_gap
    cap_y1 = float(umy) - gap
    if y1_cap is not None:
        cap_y1 = min(cap_y1, float(y1_cap) - 4.0)
    write_y1 = cap_y1
    write_y0 = write_y1 - block_h
    erase_top = float(label_rect.y0) - float(prof.cover_project_name_erase_above_pt or 0.0) + 2.0
    write_y0 = max(erase_top, write_y0)
    if write_y1 <= write_y0 + lh:
        write_y1 = write_y0 + block_h
    return fitz.Rect(x0, write_y0, x1, write_y1)


def _merge_cover_project_name_two_line_write_rects(
    page, band: fitz.Rect, label_rect: fitz.Rect
) -> Tuple[fitz.Rect, fitz.Rect]:
    """
    封面「项目名称」右侧两格书写区：几何上下分带 + 下划线定左右，
    避免仅用 midy+下划线时下半格拾到上一根线导致报告名称不显示。
    """
    h = max(1.0, float(band.y1 - band.y0))
    split = float(band.y0) + max(11.0, h * 0.46)
    if split >= float(band.y1) - 14.0:
        split = float(band.y0) + h * 0.5
    xa, xb, _ = _merge_underline_x_bounds_in_band(page, band, label_rect)
    pw = float(page.rect.width)
    pad_x = 2.0
    x0 = max(4.0, float(band.x0) + 1.0, xa + pad_x)
    x1 = min(pw - 4.0, float(band.x1) - 1.0, xb - pad_x)
    if x1 <= x0 + 8.0:
        x0, x1 = float(band.x0) + 1.0, float(band.x1) - 1.0
    gap = 1.2
    w_top = fitz.Rect(x0, float(band.y0) + 0.6, x1, split - gap)
    w_bot = fitz.Rect(x0, split + gap, x1, float(band.y1) - 0.6)
    if w_top.height < 10.0:
        w_top.y1 = min(split - gap, w_top.y1 + (11.0 - w_top.height))
    if w_bot.height < 10.0:
        w_bot.y0 = max(split + gap, w_bot.y0 - (11.0 - w_bot.height))
    if w_bot.height < 8.0:
        w_bot.y0 = max(split + gap, float(band.y1) - 16.0)
        w_bot.y1 = float(band.y1) - 0.6
    return w_top, w_bot


def _merge_sorted_underline_ys_from_segments(
    segments: Sequence[Tuple[float, float, float, float, float]],
) -> List[float]:
    """从线段列表提取去重后的水平线 y（自上而下）。"""
    ys = sorted({round(float(s[1]), 2) for s in segments})
    return ys


def _merge_cover_project_name_label_row_write_rects(
    r_lab: fitz.Rect,
    umy: float,
    x0: float,
    x1: float,
    *,
    line2_above_underline_pt: float = 5.5,
) -> Tuple[fitz.Rect, fitz.Rect]:
    """
    单下划线封面：首行与「项目名称」标签同一行，次行紧贴模板第二格（约标签下 17pt）。
    不改动标签列与原 PDF 下划线坐标。
    """
    row2_top = float(r_lab.y1) + 17.0
    w_top = fitz.Rect(x0, float(r_lab.y0) - 1.0, x1, row2_top - 1.0)
    bot_bot = float(umy) - float(line2_above_underline_pt or 5.5)
    if bot_bot <= row2_top + 8.0:
        bot_bot = row2_top + 14.0
    w_bot = fitz.Rect(x0, row2_top, x1, bot_bot)
    return w_top, w_bot


def _merge_write_cover_project_name_dual_underline_cells(
    page,
    r_lab: fitz.Rect,
    x0: float,
    x1: float,
    org_s: str,
    crt_s: str,
    underline_ys: Sequence[float],
    *,
    align_center: int,
    cover_y_off: float = 0.0,
    cell_pad: float = 2.0,
) -> bool:
    """
    双下划线封面：第一行写在标签与第一根线之间，第二行写在两根线之间。
    避免单行模式下整块压到第一根下划线上。
    """
    if not fitz or page is None or r_lab is None or len(underline_ys) < 2:
        return False
    org_s = _normalize_rp_phrase_spacing((org_s or "").strip())
    crt_s = _normalize_rp_phrase_spacing((crt_s or "").strip())
    if not org_s and not crt_s:
        return False
    ul1, ul2 = float(underline_ys[0]), float(underline_ys[1])
    if ul2 <= ul1 + 6.0:
        return False
    pad = max(1.2, float(cell_pad))
    y_off = float(cover_y_off or 0.0)
    w_top = fitz.Rect(x0, float(r_lab.y1) + 0.6 + y_off, x1, ul1 - pad)
    w_bot = fitz.Rect(x0, ul1 + pad, x1, ul2 - pad)
    if w_top.height < 5.0 or w_bot.height < 8.0:
        return False
    for cell in (w_top, w_bot):
        _merge_overlay_erase_transparent(page, cell, pad=0.5)
    _merge_apply_redactions_overlay(page)
    if org_s:
        org_fs = min(10.5, max(9.5, w_top.height - 2.0))
        use_bold = _cover_overlay_use_bold()
        _merge_overlay_write_text_html_style(
            page,
            w_top,
            org_s,
            align=align_center,
            preferred_fontsize=org_fs,
            fontsize_floor=org_fs,
            bold=use_bold,
            valign_top=True,
        )
    if crt_s:
        crt_fs = _MERGE_PT_SIHAO
        crt_floor = _MERGE_PT_SIHAO
        if crt_s and len(crt_s) > 32:
            crt_fs, crt_floor = 11.0, 10.0
        if crt_s and len(crt_s) > 34:
            crt_fs, crt_floor = 10.5, 10.0
        use_bold = _cover_overlay_use_bold()
        _merge_overlay_write_text_html_style(
            page,
            w_bot,
            crt_s,
            align=align_center,
            preferred_fontsize=crt_fs,
            fontsize_floor=crt_floor,
            bold=use_bold,
            valign_top=True,
        )
    return True


def _merge_write_cover_project_name_two_lines(
    page,
    r_lab: fitz.Rect,
    band: fitz.Rect,
    org_s: str,
    crt_s: str,
    *,
    align_center: int,
    page_index_0based: int = 0,
    use_fixed_title_rect: bool = False,
    underline_y: Optional[float] = None,
    line2_above_underline_pt: float = 5.5,
    precomputed_rows: Optional[Tuple[fitz.Rect, fitz.Rect]] = None,
) -> None:
    """封面「项目名称」两行：上单位、下设备+检测类型；文字落在上划线上方，长标题略缩小字号。"""
    if not fitz or page is None or r_lab is None or band is None:
        return
    org_s = _normalize_rp_phrase_spacing((org_s or "").strip())
    crt_s = _normalize_rp_phrase_spacing((crt_s or "").strip())
    if not org_s and not crt_s:
        return
    if precomputed_rows is not None:
        w_top_wr, w_bot_wr = precomputed_rows
        use_bold = _cover_overlay_use_bold()
        crt_fs = _MERGE_PT_SIHAO
        crt_floor = _MERGE_PT_SIHAO
        if crt_s and len(crt_s) > 32:
            crt_fs, crt_floor = 11.0, 10.0
        if crt_s and len(crt_s) > 34:
            crt_fs, crt_floor = 10.5, 10.0
        if org_s:
            _merge_overlay_write_text_html_style(
                page,
                w_top_wr,
                org_s,
                align=align_center,
                preferred_fontsize=_MERGE_PT_SIHAO,
                fontsize_floor=_MERGE_PT_SIHAO,
                bold=use_bold,
                valign_top=True,
            )
        if crt_s:
            _merge_overlay_write_text_html_style(
                page,
                w_bot_wr,
                crt_s,
                align=align_center,
                preferred_fontsize=crt_fs,
                fontsize_floor=crt_floor,
                bold=use_bold,
                valign_top=True,
            )
        return
    elif underline_y is not None:
        pw = float(page.rect.width)
        xa, xb, _ = _merge_underline_x_bounds_in_band(page, band, r_lab)
        pad_x = 2.0
        cx0 = max(4.0, float(band.x0) + 1.0, xa + pad_x)
        cx1 = min(pw - 4.0, float(band.x1) - 1.0, xb - pad_x)
        if cx1 <= cx0 + 8.0:
            cx0, cx1 = float(band.x0) + 1.0, float(band.x1) - 1.0
        h = max(1.0, float(band.y1 - band.y0))
        mid_gap = 1.0
        split = float(band.y0) + (h - mid_gap) * 0.5
        w_top = fitz.Rect(cx0, float(band.y0) + 0.4, cx1, split)
        w_bot = fitz.Rect(cx0, split + mid_gap, cx1, float(band.y1) - 0.4)
        w_top_wr = w_top
        w_bot_wr = w_bot
    else:
        w_top, w_bot = _merge_cover_project_name_two_line_write_rects(page, band, r_lab)
        if crt_s and len(crt_s) > 26:
            h = max(1.0, float(band.y1 - band.y0))
            split = float(band.y0) + h * 0.36
            xa, xb, _ = _merge_underline_x_bounds_in_band(page, band, r_lab)
            pw = float(page.rect.width)
            pad_x = 2.0
            x0 = max(4.0, float(band.x0) + 1.0, xa + pad_x)
            x1 = min(pw - 4.0, float(band.x1) - 1.0, xb - pad_x)
            if x1 <= x0 + 8.0:
                x0, x1 = float(band.x0) + 1.0, float(band.x1) - 1.0
            gap = 1.0
            w_top = fitz.Rect(x0, float(band.y0) + 0.6, x1, split - gap)
            w_bot = fitz.Rect(x0, split + gap, x1, float(band.y1) - 0.6)
        w_top_wr = _merge_cover_field_write_rect_safe(
            page, band, r_lab, y_sub_top=w_top.y0, y_sub_bot=w_top.y1, min_height=11.0
        )
        w_bot_wr = _merge_cover_field_write_rect_safe(
            page, band, r_lab, y_sub_top=w_bot.y0, y_sub_bot=w_bot.y1, min_height=12.0
        )
    crt_fs = _MERGE_PT_SIHAO
    crt_floor = _MERGE_PT_SIHAO
    if crt_s and len(crt_s) > 32:
        crt_fs, crt_floor = 11.0, 10.0
    if crt_s and len(crt_s) > 34:
        crt_fs, crt_floor = 10.5, 10.0
    if org_s:
        _merge_overlay_write_cover_centered_band(
            page,
            w_top_wr,
            org_s,
            align=align_center,
            preferred_fontsize=_MERGE_PT_SIHAO,
            erase_before_write=False,
            fontsize_floor=_MERGE_PT_SIHAO,
        )
    if crt_s:
        frt = (
            _merge_fixed_fitz_rect_for_page_index("cover_report_title", page_index_0based)
            if use_fixed_title_rect
            else None
        )
        w_crt = frt if (frt is not None and not frt.is_empty) else w_bot_wr
        _merge_overlay_write_cover_centered_band(
            page,
            w_crt,
            crt_s,
            align=align_center,
            preferred_fontsize=crt_fs,
            erase_before_write=False,
            fontsize_floor=crt_floor,
        )


def _merge_summary_project_name_value_erase_union(page, label_rect: fitz.Rect) -> fitz.Rect:
    """
    基本情况页「项目名称」取值列：并集标签行右侧及下方直至「受检设备台数」前的所有相关 span，
    覆盖多行旧项目名称，避免擦除不全导致仍显示第一份报告文案。
    """
    if not fitz or label_rect is None or label_rect.is_empty:
        return fitz.Rect(0, 0, 0, 0)
    y_lo, y_hi = _merge_basic_info_section_y_bounds(page)
    y_top = float(label_rect.y0) - 6.0
    y_next_row = _merge_basic_info_label_row_y0(page, "委托编号", y_lo=y_lo, y_hi=y_hi)
    if y_next_row is not None:
        y_bot = float(y_next_row) - 4.0
    elif y_hi is not None:
        y_bot = float(y_hi) - 4.0
    else:
        # 未定位到「受检设备台数」行时勿向页下方无限扩张，避免擦到「二、评价」正文
        y_bot = min(float(page.rect.height) * 0.5, float(label_rect.y1) + 40.0)
    if y_lo is not None:
        y_top = max(y_top, float(y_lo) - 4.0)
    mr = _merge_page_right_margin(page)
    pw = float(page.rect.width)
    d = page.get_text("dict") or {}
    merged: Optional[fitz.Rect] = None
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            tcompact = re.sub(r"\s+", "", _line_text(line))
            if any(n in tcompact for n in _summary_device_count_label_needles()):
                continue
            lb = _line_bbox_union(spans)
            if lb.is_empty:
                continue
            cy = (float(lb.y0) + float(lb.y1)) * 0.5
            if cy < y_top or cy > y_bot + 2.0:
                continue
            parts: List[fitz.Rect] = []
            for sp in spans:
                bb = sp.get("bbox")
                if not bb or len(bb) < 4:
                    continue
                sr = fitz.Rect(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                if float(sr.x0) >= float(label_rect.x1) + 0.6:
                    parts.append(sr)
            if not parts:
                continue
            u = parts[0]
            for p in parts[1:]:
                u |= p
            merged = u if merged is None else (merged | u)
    if merged is None or merged.is_empty:
        return _merge_summary_project_name_value_band(page, label_rect, 10.5)
    merged.x0 = max(float(merged.x0), float(label_rect.x1) + 0.6)
    merged.x1 = max(float(merged.x1), pw - mr)
    merged.y0 = min(float(merged.y0), y_top + 1.0)
    merged.y1 = max(float(merged.y1), min(y_bot, float(merged.y1) + 6.0))
    return merged


def _merge_erase_sanjian_heading_line_if_present(page) -> None:
    """去掉拼接小节时重复的「三、检测结果」标题行（透明擦除）。"""
    if not fitz or page is None:
        return
    markers = ("三、检测结果", "三.检测结果", "三．检测结果")
    d = page.get_text("dict") or {}
    bbox: Optional[fitz.Rect] = None
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = re.sub(r"\s+", "", _line_text(line))
            if not any(m in t for m in markers):
                continue
            spans = line.get("spans") or []
            if not spans:
                continue
            bbox = _line_bbox_union(spans)
            break
        if bbox is not None:
            break
    if bbox is None or bbox.is_empty:
        return
    pad = fitz.Rect(-1.2, -2.0, 1.2, 3.0)
    _merge_overlay_erase_transparent(page, bbox + pad, pad=0.4)
    _merge_apply_redactions_overlay(page)


def _merge_fit_text_preferred_max(
    text: str,
    rect: fitz.Rect,
    font: Any,
    preferred_fs: float,
    hs_mod: Any,
    *,
    fontsize_floor: Optional[float] = None,
) -> Tuple[str, float]:
    """优先使用 preferred_fs，放不进框时再略缩小；fontsize_floor 指定时字号不低于该值（如小四锁定）。"""
    fs = float(preferred_fs)
    if fontsize_floor is not None:
        min_fs = float(fontsize_floor)
    else:
        min_fs = max(8.0, float(preferred_fs) * 0.62)
    w = max(1.0, float(rect.width) - 2.0)
    h = max(1.0, float(rect.height) - 1.0)
    while fs >= min_fs:
        wrapped = hs_mod.wrap_text_to_width(text, w, font, fs)
        line_count = max(1, wrapped.count("\n") + 1)
        if line_count * fs * 1.22 <= h:
            return wrapped, fs
        fs -= 0.5
    wrapped = hs_mod.wrap_text_to_width(text, w, font, min_fs)
    return wrapped, min_fs


def _merge_overlay_write_text_html_style(
    page,
    rect: fitz.Rect,
    text: str,
    *,
    align: int,
    paragraph_layout: bool = False,
    preferred_fontsize: Optional[float] = None,
    fontsize_floor: Optional[float] = None,
    bold: bool = False,
    valign_top: bool = False,
) -> None:
    """
    与 apps.core.htmlpdf_service.build_filled_pdf 一致：ensure_min_text_rect → safe_inset → fit_text_for_box → insert_textbox(overlay=True)。
    合并说明性文字用黑色；不再铺白底。
    paragraph_layout：多段/多行时自框顶排版（保留首行缩进），避免整段垂直居中导致版式像「缩进丢失」。
    preferred_fontsize：优先字号（pt），如四号 14、小四 12；放不进时再略缩小。
    fontsize_floor：与 preferred 同时使用时，缩小字号不低于该值（用于小四等下限）。
    bold：封面等需加粗时嵌入真粗体字体文件（黑体/Noto CJK Bold 等），非描边模拟。
    valign_top：封面单行栏位自框顶书写，避免在高书写带内垂直居中导致偏下。
    """
    if not fitz or rect is None or rect.is_empty or not (text or "").strip():
        return
    text = _normalize_rp_phrase_spacing(text)

    def _fallback_insert() -> None:
        fsu = float(preferred_fontsize or 10.5)
        reg_path = _resolve_song_embed_font_path(overlay_textbox=True) or ""
        fontname = "china-s"
        if reg_path and _embed_font_file_on_page(page, _MERGE_OVERLAY_FONT_REGULAR, reg_path):
            fontname = _MERGE_OVERLAY_FONT_REGULAR
        use_bold = bool(bold) and _cover_overlay_use_bold()
        if use_bold:
            bold_path = _resolve_bold_embed_font_path(overlay_textbox=True)
            if bold_path and _embed_font_file_on_page(page, _MERGE_OVERLAY_FONT_BOLD, bold_path):
                fontname = _MERGE_OVERLAY_FONT_BOLD
        if fontname != "china-s":
            try:
                remain = page.insert_textbox(
                    rect,
                    (text or "")[:400],
                    fontname=fontname,
                    fontsize=fsu,
                    color=(0, 0, 0),
                    align=align,
                    overlay=True,
                )
                if remain >= 0:
                    return
            except Exception:
                pass
            _merge_overlay_fallback_write(
                page, rect, (text or "")[:400], fontsize=fsu, fontname=fontname, align=align
            )
            return
        _merge_overlay_fallback_write(
            page, rect, (text or "")[:400], fontsize=fsu, fontname=fontname, align=align
        )

    regular_path = _resolve_song_embed_font_path(overlay_textbox=True) or ""
    try:
        from apps.core import htmlpdf_service as _hs

        if not regular_path and _hs.SIMSUN_FONT.is_file():
            regular_path = str(_hs.SIMSUN_FONT)
    except Exception as exc:
        logger.warning("merge overlay: htmlpdf_service import failed: %s", exc)
        _fallback_insert()
        return
    if not regular_path:
        _fallback_insert()
        return
    raw = fitz.Rect(rect)
    try:
        from apps.core import htmlpdf_service as _hs
    except Exception:
        _fallback_insert()
        return
    rr = _hs.ensure_min_text_rect(raw)
    r = _hs.safe_inset_rect(rr, 0.6)
    try:
        fontname, fo = _register_merge_overlay_fonts(
            page,
            regular_path=regular_path,
            want_bold=bold,
        )
        if fo is None:
            raise RuntimeError("overlay font unavailable")
        if preferred_fontsize is not None:
            wrapped, fs = _merge_fit_text_preferred_max(
                text, r, fo, float(preferred_fontsize), _hs, fontsize_floor=fontsize_floor
            )
        else:
            wrapped, fs = _hs.fit_text_for_box(text, r, fo)
        line_count = max(1, wrapped.count("\n") + 1)
        line_h = fs * 1.2
        text_h = line_count * line_h
        multi = ("\n" in (text or "")) or ("\n" in wrapped) or line_count > 1
        if paragraph_layout or multi:
            pad_top = max(0.5, fs * 0.15)
            text_rect = fitz.Rect(r.x0, r.y0 + pad_top, r.x1, r.y1)
        elif valign_top:
            pad_top = max(0.3, fs * 0.08)
            text_rect = fitz.Rect(r.x0, r.y0 + pad_top, r.x1, r.y1)
        elif text_h < r.height:
            offset_y = (r.height - text_h) / 2.0
            text_rect = fitz.Rect(r.x0, r.y0 + offset_y, r.x1, r.y1)
        else:
            text_rect = r
        remain = page.insert_textbox(
            text_rect,
            wrapped,
            fontname=fontname,
            fontsize=fs,
            color=(0, 0, 0),
            align=align,
            overlay=True,
        )
        if remain < 0:
            logger.debug("merge overlay insert_textbox overflow remain=%s", remain)
            _merge_overlay_fallback_write(
                page, text_rect, wrapped, fontsize=fs, fontname=fontname, align=align
            )
    except Exception as exc:
        logger.debug("merge overlay insert_textbox fallback: %s", exc)
        _fallback_insert()


# 页面基础设置（A4）：正文区右边界距页面最右侧 2.8cm
_PAGE_BASE_RIGHT_MARGIN_CM = 2.8


def _cm_to_pt(cm: float) -> float:
    """厘米 → PDF 点（pt）；1 inch = 2.54 cm = 72 pt。"""
    return float(cm) * 72.0 / 2.54


def _merge_page_right_margin(page) -> float:
    """页面右边距（pt），与 Word/A4 页面设置「距右侧 2.8cm」一致。"""
    del page  # A4 基础固定物理边距，不随页宽比例缩放
    return _cm_to_pt(_PAGE_BASE_RIGHT_MARGIN_CM)


def _merge_value_rect_same_line(page, label_rect: fitz.Rect) -> fitz.Rect:
    """标签右侧同一行取值区（勿用页宽比例误把 x0 拉到标签左侧）。"""
    mr = _merge_page_right_margin(page)
    pw = float(page.rect.width)
    x0 = min(float(label_rect.x1) + 1.5, pw - mr - 8.0)
    return fitz.Rect(x0, float(label_rect.y0) - 1.2, pw - mr, float(label_rect.y1) + 1.5)


def _merge_sample_fs_in_rect(page, rect: fitz.Rect, default_fs: float = 10.5) -> float:
    """在矩形附近采样正文字号，叠印时保持一致。"""
    if not fitz or rect is None or rect.is_empty:
        return default_fs
    best = 0.0
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            if not spans:
                continue
            bbox = _line_bbox_union(spans)
            if bbox.is_empty:
                continue
            if bbox.x1 < rect.x0 or rect.x1 < bbox.x0 or bbox.y1 < rect.y0 or rect.y1 < bbox.y0:
                continue
            for sp in spans:
                try:
                    fs = float(sp.get("size") or 0)
                except (TypeError, ValueError):
                    fs = 0.0
                if fs > best:
                    best = fs
    return best if best >= 7.0 else default_fs


def _merge_cover_project_name_value_band(page, label_rect: fitz.Rect, line_fs: float) -> fitz.Rect:
    """封面「项目名称」右侧两格：在标签右侧向下延伸约两行。"""
    mr = _merge_page_right_margin(page)
    pw = float(page.rect.width)
    x0 = min(float(label_rect.x1) + 1.5, pw - mr - 8.0)
    lh = max(11.0, line_fs * 1.22)
    y0 = float(label_rect.y0) - 2.0
    y1 = y0 + lh * 2.35 + 4.0
    y1 = min(y1, float(page.rect.height) - 24.0)
    return fitz.Rect(x0, y0, pw - mr, y1)


def _merge_summary_project_name_value_band(page, label_rect: fitz.Rect, line_fs: float) -> fitz.Rect:
    """基本情况页「项目名称」右侧：单行或略增高以容纳「单位+合并名称」。"""
    mr = _merge_page_right_margin(page)
    pw = float(page.rect.width)
    x0 = min(float(label_rect.x1) + 1.5, pw - mr - 8.0)
    y0 = float(label_rect.y0) - 2.0
    y1 = y0 + max(line_fs * 3.4, 38.0)
    # 向下扩展直到遇到「受检设备台数」行（避免盖住下一行标签）
    d = page.get_text("dict") or {}
    y_cap = float(page.rect.height) - 32.0
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = re.sub(r"\s+", "", _line_text(line))
            if _line_has_summary_device_count_label(line):
                bbox = _line_bbox_union(line.get("spans") or [])
                if float(bbox.y0) > float(label_rect.y1) - 1.0:
                    y_cap = min(y_cap, float(bbox.y0) - 3.0)
                    break
    y1 = min(y1, y_cap)
    return fitz.Rect(x0, y0, pw - mr, max(y1, y0 + line_fs * 1.35))


def _merge_summary_project_name_cell_rect_robust(
    page, label_rect: fitz.Rect, *, line_fs: float = _MERGE_PT_XIAOSI
) -> Optional[fitz.Rect]:
    """
    基本情况表「项目名称」取值格：不依赖旧文案全文匹配。
    用「一、项目基本情况」标题下缘～「受检设备台数」行上缘之间的纵带，
    在标签右缘至页右留白之间取一块足够容纳多行合并名称的矩形，供擦除与叠印。
    """
    if not fitz or page is None or label_rect is None or label_rect.is_empty:
        return None
    y_lo, y_hi = _merge_basic_info_section_y_bounds(page)
    mr = _merge_page_right_margin(page)
    pw = float(page.rect.width)
    x0 = float(label_rect.x1) + 0.6
    x1 = _merge_basic_info_table_x1(page, use_dsa_cap=False) - 2.0
    y0 = float(label_rect.y0) - 4.0
    if y_lo is not None:
        y0 = max(y0, float(y_lo) + 1.0)
    y_next_row = None
    for needle in ("委托编号", "受检单位名称", "受检单位", "受检设备台数"):
        y_cand = _merge_basic_info_label_row_y0(page, needle, y_lo=y_lo, y_hi=y_hi)
        if y_cand is not None and float(y_cand) > float(label_rect.y0) + 4.0:
            y_next_row = float(y_cand)
            break
    if y_next_row is not None:
        y1 = float(y_next_row) - 5.0
    else:
        y1 = float(label_rect.y1) + max(36.0, float(line_fs) * 3.2)
        if y_hi is not None:
            y1 = min(y1, float(y_hi) - 5.0)
    y1 = min(float(page.rect.height) - 18.0, max(y1, float(label_rect.y1) + float(line_fs) * 1.35))
    if y1 <= y0 + 8.0 or x1 <= x0 + 10.0:
        return None
    return fitz.Rect(x0, y0, x1, y1)


def _merge_evaluation_body_rect(page, heading_rect: fitz.Rect) -> Optional[fitz.Rect]:
    """
    「二、评价」标题以下至「三、…」之前：用文本层行框并集，贴近模板原有正文框范围。
    """
    if not fitz or heading_rect is None or heading_rect.is_empty:
        return None
    y_low = float(heading_rect.y1) + 2.0
    y_hi = float(page.rect.height) * 0.80
    d = page.get_text("dict") or {}
    quit_scan = False
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = _line_text(line).strip()
            if t.startswith("三、") or t.startswith("三.") or t.startswith("三．"):
                bbox = _line_bbox_union(line.get("spans") or [])
                if float(bbox.y0) > y_low + 4.0:
                    y_hi = min(y_hi, float(bbox.y0) - 3.0)
                quit_scan = True
                break
        if quit_scan:
            break
    boxes: List[fitz.Rect] = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            bbox = _line_bbox_union(spans)
            if bbox.is_empty:
                continue
            cy = (float(bbox.y0) + float(bbox.y1)) * 0.5
            if cy < y_low or cy > y_hi:
                continue
            t = _line_text(line).strip()
            if not t:
                continue
            if len(t) < 6:
                continue
            if t.startswith("三、") or t.startswith("四、"):
                continue
            if "二、评价" in re.sub(r"\s+", "", t) or "二.评价" in t:
                continue
            boxes.append(bbox)
    if not boxes:
        return None
    x0 = min(float(b.x0) for b in boxes) - 2.0
    x1 = max(float(b.x1) for b in boxes) + 2.0
    y0 = min(float(b.y0) for b in boxes) - 2.0
    y1 = max(float(b.y1) for b in boxes) + 2.0
    y1 = min(y1, y_hi + 4.0)
    max_h = min(150.0, float(page.rect.height) * 0.28)
    if (y1 - y0) > max_h:
        y1 = y0 + max_h
    return fitz.Rect(x0, y0, x1, y1)


def _patch_inspection_number_on_section_first_page(page, section_ordinal_1based: int) -> int:
    """将本页「受检编号：…」整行改为「（n）受检编号：nn」（n 为合并小节序号）。"""
    if not fitz or page is None:
        return 0
    ord_i = max(1, int(section_ordinal_1based))
    head_pat = re.compile(r"受检编号\s*[：:]")
    target = f"（{ord_i}）受检编号：{ord_i:02d}"
    changed = 0
    for _ in range(24):
        d = page.get_text("dict") or {}
        bbox: Optional[fitz.Rect] = None
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans") or []
                if not spans:
                    continue
                txt = _line_text(line)
                if not head_pat.search(txt):
                    continue
                compact = re.sub(r"\s+", "", txt)
                if compact == re.sub(r"\s+", "", target):
                    continue
                bbox = _line_bbox_union(spans)
                break
            if bbox is not None:
                break
        if bbox is None or bbox.is_empty:
            break
        pad_x = 1.2
        pad_y = max(1.5, float(bbox.height) * 0.35)
        wipe = bbox + fitz.Rect(-pad_x, -pad_y, pad_x, pad_y)
        _merge_overlay_erase_transparent(page, wipe, pad=0.35)
        _merge_apply_redactions_overlay(page)
        fs = float(_MERGE_PT_XIAOSI)
        prefix = f"（{ord_i}）受检编号："
        suffix = f"{ord_i:02d}"
        x0 = float(bbox.x0)
        bl_y = float(bbox.y1) - fs * 0.22
        drawn = False
        try:
            from apps.core import htmlpdf_service as _hs

            if _hs.SIMSUN_FONT.is_file():
                page.insert_font(fontname="F_MERGE_SIM", fontfile=str(_hs.SIMSUN_FONT))
                fo = fitz.Font(fontfile=str(_hs.SIMSUN_FONT))
                tw = float(fo.text_length(prefix, fontsize=fs))
                for dx, dy in ((0.0, 0.0), (0.22, 0.0), (-0.12, 0.0)):
                    page.insert_text(
                        (x0 + dx, bl_y + dy),
                        prefix,
                        fontname="F_MERGE_SIM",
                        fontsize=fs,
                        color=(0, 0, 0),
                        render_mode=0,
                        overlay=True,
                    )
                page.insert_text(
                    (x0 + tw, bl_y),
                    suffix,
                    fontname="F_MERGE_SIM",
                    fontsize=fs,
                    color=(0, 0, 0),
                    render_mode=0,
                    overlay=True,
                )
                drawn = True
        except Exception as exc:
            logger.debug("patch inspection bold draw: %s", exc)
        if not drawn:
            write_rect = fitz.Rect(
                float(bbox.x0),
                float(bbox.y0) - max(0.5, float(bbox.height) * 0.08),
                min(float(page.rect.width) - 8.0, float(bbox.x1) + 220.0),
                float(bbox.y1) + max(1.0, float(bbox.height) * 0.55),
            )
            _merge_overlay_write_text_html_style(
                page,
                write_rect,
                target,
                align=int(getattr(fitz, "TEXT_ALIGN_LEFT", 0)),
                preferred_fontsize=_MERGE_PT_XIAOSI,
                fontsize_floor=_MERGE_PT_XIAOSI,
            )
        changed += 1
        # get_text 仍可能读到底层未删净的旧串，导致反复命中同一 bbox 叠画多遍 → 乱码
        break
    return changed


def apply_merged_inspection_numbers_on_section_heads(
    doc,
    *,
    toc_page_0based: int,
    section_page_counts: Sequence[int],
) -> None:
    """各份「三、检测结果」插入块内：将该节各页「受检编号」行改为「（n）受检编号：nn」格式。"""
    if not fitz or not section_page_counts:
        return
    start = int(toc_page_0based) + 1
    acc = start
    for i, cnt in enumerate(section_page_counts):
        cnt = int(cnt)
        if cnt <= 0:
            acc += cnt
            continue
        for j in range(cnt):
            pi2 = acc + j
            if 0 <= pi2 < doc.page_count:
                try:
                    _patch_inspection_number_on_section_first_page(doc[pi2], i + 1)
                except Exception as exc:
                    logger.warning("patch inspection no page %s: %s", pi2, exc)
        acc += cnt


def _should_renumber_leading_1_k_subheading_line(txt: str) -> bool:
    """
    判断是否为小节标题行：行首「1.k」在合并第 2 份及以后应改为「n.k」。

    - 保留旧规则：含「1.1」且行内有「质量控制检测项目及结果」。
    - 扩展：行首「1.k」且整行较短、后续为标题性文字（含中文或拉丁字母），
      排除「三、」「四、」等大节行，避免误改表内数值。
    """
    if not txt or not str(txt).strip():
        return False
    raw = str(txt)
    compact = re.sub(r"\s+", "", raw)
    if re.search(r"1\s*[.．]\s*1", raw) and "质量控制检测项目及结果" in compact:
        return True
    st = raw.strip()
    if len(st) > 200:
        return False
    if re.match(r"^[三四五六七八九十百千]+[、.．]", st):
        return False
    m = re.match(r"^1\s*[.．]\s*(\d+)(?!\d)", st)
    if not m:
        return False
    tail = st[m.end() :].strip()
    if len(tail) < 2:
        return False
    head = tail[:80]
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", head):
        return False
    return True


def _sub_leading_1_k_with_n_k(txt: str, n: int) -> Optional[str]:
    """将行首「1.k」替换为「n.k」，仅替换首处编号；不匹配则返回 None。"""
    m = re.match(r"^(\s*)1\s*([.．])\s*(\d+)(?!\d)(.*)$", txt, re.DOTALL)
    if not m:
        return None
    prefix, dot, k, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    return f"{prefix}{n}{dot}{k}{rest}"


def _patch_quality_subheadings_1_k_to_n_k_on_page(page, section_ordinal_1based: int) -> int:
    """第 2 份及以后：将本页行首「1.1」「1.2」…小节标题改为「n.1」「n.2…」，与目录 n.1 一致。"""
    if not fitz or page is None:
        return 0
    n = max(1, int(section_ordinal_1based))
    if n <= 1:
        return 0
    changed = 0
    for _ in range(64):
        d = page.get_text("dict") or {}
        best: Optional[Tuple[float, fitz.Rect, str]] = None
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                txt = _line_text(line)
                if not _should_renumber_leading_1_k_subheading_line(txt):
                    continue
                new_txt = _sub_leading_1_k_with_n_k(txt, n)
                if not new_txt or new_txt == txt:
                    continue
                spans = line.get("spans") or []
                bbox = _line_bbox_union(spans)
                if bbox is None or bbox.is_empty:
                    continue
                y0 = float(bbox.y0)
                if best is None or y0 < best[0]:
                    best = (y0, bbox, new_txt.strip())
        if best is None:
            break
        _, bbox, new_txt = best
        pad = fitz.Rect(-1.5, -2.0, 1.5, 3.0)
        _merge_overlay_erase_transparent(page, bbox + pad, pad=0.35)
        _merge_apply_redactions_overlay(page)
        fs = float(_MERGE_PT_XIAOSI)
        bl_y = float(bbox.y1) - fs * 0.22
        drawn = False
        try:
            from apps.core import htmlpdf_service as _hs

            if _hs.SIMSUN_FONT.is_file():
                page.insert_font(fontname="F_MERGE_SIM", fontfile=str(_hs.SIMSUN_FONT))
                page.insert_text(
                    (float(bbox.x0), bl_y),
                    new_txt,
                    fontname="F_MERGE_SIM",
                    fontsize=fs,
                    color=(0, 0, 0),
                    overlay=True,
                )
                drawn = True
        except Exception as exc:
            logger.debug("patch 1.k line: %s", exc)
        if not drawn:
            write_rect = fitz.Rect(
                float(bbox.x0),
                float(bbox.y0) - max(0.5, float(bbox.height) * 0.08),
                min(float(page.rect.width) - 8.0, float(bbox.x1) + 260.0),
                float(bbox.y1) + max(1.0, float(bbox.height) * 0.55),
            )
            _merge_overlay_write_text_html_style(
                page,
                write_rect,
                new_txt,
                align=int(getattr(fitz, "TEXT_ALIGN_LEFT", 0)),
                preferred_fontsize=_MERGE_PT_XIAOSI,
                fontsize_floor=_MERGE_PT_XIAOSI,
            )
        changed += 1
    return changed


def apply_merged_quality_subheading_renumbers(
    doc,
    *,
    toc_page_0based: int,
    section_page_counts: Sequence[int],
) -> None:
    """各「三、检测结果」小节内：第 2 份起将行首「1.1」「1.2」…改为「n.1」「n.2…」与目录一致。"""
    if not fitz or not section_page_counts:
        return
    start = int(toc_page_0based) + 1
    acc = start
    for i, cnt in enumerate(section_page_counts):
        cnt = int(cnt)
        if cnt <= 0:
            acc += cnt
            continue
        nsec = i + 1
        if nsec <= 1:
            acc += cnt
            continue
        for j in range(cnt):
            pi2 = acc + j
            if 0 <= pi2 < doc.page_count:
                try:
                    _patch_quality_subheadings_1_k_to_n_k_on_page(doc[pi2], nsec)
                except Exception as exc:
                    logger.warning("quality subheading patch page %s: %s", pi2, exc)
        acc += cnt


def extract_cover_inspected_unit_fallback(pdf_path: str) -> str:
    """无法溯源提交时：从封面页文本猜测受检单位。"""
    if not fitz or not pdf_path or not os.path.isfile(pdf_path):
        return ""
    doc = fitz.open(pdf_path)
    try:
        t = doc[0].get_text()
    except Exception:
        return ""
    finally:
        doc.close()
    for pat in (
        r"受检单位\s*[：:]\s*([^\n\r]{1,120})",
        r"委托单位\s*[：:]\s*([^\n\r]{1,120})",
        r"申请单位\s*[：:]\s*([^\n\r]{1,120})",
    ):
        m = re.search(pat, t)
        if m:
            s = (m.group(1) or "").strip()
            if s:
                return s.split()[0][:120]
    return ""


def extract_modality_abbr_from_title(title: str) -> str:
    """从单份报告标题/设备名中抽取合并用语设备类型（优先命中 MERGED_REPORT_DEVICE_TYPE_LABELS）。"""
    t = (title or "").strip()
    if not t:
        return ""

    def _hit_known(s: str) -> str:
        m = _MERGED_DEVICE_TYPE_RE.search(s)
        return m.group(1) if m else ""

    hit = _hit_known(t)
    if hit:
        return hit

    # 括号内常见写法：（动态DR）（DSA）等；允许中英文混合
    m = re.search(r"[（(]\s*([^）)]{1,32}?)\s*[）)]", t)
    if m:
        inner = (m.group(1) or "").strip()
        hit = _hit_known(inner)
        if hit:
            return hit
        if re.fullmatch(r"[A-Za-z0-9\-]{1,12}", inner):
            up = inner.upper()
            if up == "CBCT":
                return "口腔CBCT"
            return up

    m2 = re.search(r"\b([A-Z]{2,10})\b", t)
    if m2:
        up = m2.group(1).upper()
        if up == "CBCT":
            return "口腔CBCT"
        return up

    m3 = re.search(r"(DSA|DR|CR|CT|MR|MRI|LA|LINAC|CBCT|PET|ECT|RF|OCT)", t, re.I)
    if m3:
        up = m3.group(1).upper()
        if up == "CBCT":
            return "口腔CBCT"
        return up
    return ""


def merged_cover_report_title_from_abbrs(abbrs: Sequence[str], n: int) -> str:
    """如：动态DR、胃肠机、DSA3台设备质量控制检测；超过三台为「前三种 + 等n台设备质量控制检测」。台数一律用阿拉伯数字。"""
    seq: List[str] = []
    for i in range(max(0, int(n))):
        raw = (abbrs[i] if i < len(abbrs) else "") or ""
        seq.append(raw.strip() or f"设备{i + 1}")
    if n <= 3:
        head = "、".join(seq[:n])
        return f"{head}{int(n)}台设备质量控制检测"
    head = "、".join(seq[:3])
    return f"{head}等{int(n)}台设备质量控制检测"


def merged_device_phrase_for_evaluation(abbrs: Sequence[str], n: int) -> str:
    """评价句中的设备短语（不含「质量控制检测」后缀）；台数一律用阿拉伯数字。"""
    seq: List[str] = []
    for i in range(max(0, int(n))):
        raw = (abbrs[i] if i < len(abbrs) else "") or ""
        seq.append(raw.strip() or f"设备{i + 1}")
    if n <= 3:
        head = "、".join(seq[:n])
        return f"{head}{int(n)}台设备"
    head = "、".join(seq[:3])
    return f"{head}等{int(n)}台设备"


def build_merged_report_overlay_fields(
    rows: List[dict], *, merge_date_str: str, cover_title_override: str | None = None
) -> dict:
    """
    由 inspection_pdf_service 收集的 rows（含 title_line / inspected_org_hint / path）生成叠印字典。

    ``project_name_combined``：基本情况页「项目名称」单行叠字用，语义为 **受检单位名称 + 合并报告名称**，
    与封面「项目名称」两行（受检单位一行 + 报告名称一行）总文案一致。

    ``cover_title_override``：若非空，封面第二行「合并报告名称」采用该串（如 {委托编号}{项目名称}），
    否则仍按各份报告标题抽取设备类型拼接（原逻辑）。
    """
    n = max(0, len(rows))
    if n == 0:
        return {}
    titles = [str(r.get("title_line") or "").strip() or os.path.basename(r.get("path") or "") for r in rows]
    abbrs = [extract_modality_abbr_from_title(t) for t in titles]
    ab2: List[str] = []
    for i, a in enumerate(abbrs):
        ab2.append((a or "").strip() or f"设备{i + 1}")
    org = ""
    for r in rows:
        oh = (r.get("inspected_org_hint") or "").strip()
        if oh:
            org = oh
            break
    if not org:
        for r in rows:
            org = extract_cover_inspected_unit_fallback(str(r.get("path") or ""))
            if org:
                break
    ov = (cover_title_override or "").strip()
    if ov:
        cover_title = ov
    else:
        cover_title = merged_cover_report_title_from_abbrs(ab2, n)
    dev_eval = merged_device_phrase_for_evaluation(ab2, n)
    project_name = f"{org}{cover_title}"
    nos = "、".join(f"{i:02d}" for i in range(1, n + 1))
    qc_phrase = format_qc_detection_for_evaluation("")
    eval_body = (
        f"应委托方要求，依据相关检测标准，对{org}{dev_eval}进行了{qc_phrase}，结果表明：\n"
        f"所检设备的质量控制相关参数均符合相关标准要求。"
    )
    return {
        "merge_date_str": (merge_date_str or "").strip(),
        "inspected_org": org.strip(),
        "cover_report_title": cover_title,
        "cover_org_line": org.strip(),
        "inspection_index_line": nos,
        "project_name_combined": project_name.strip(),
        "device_count_label": f"{n} 台",
        "evaluation_body": eval_body,
    }


def _merge_apply_textbox(page, rect: fitz.Rect, text: str, fontname: str, fontsize: float) -> None:
    """兼容旧参数；实际走与 htmlpdf_service.build_filled_pdf 相同的 fit_text + insert_textbox(overlay=True)。"""
    _merge_overlay_write_text_html_style(page, rect, text, align=int(getattr(fitz, "TEXT_ALIGN_LEFT", 0)))


def _merge_summary_evaluation_overlay_rect(page, heading_rect: Any) -> Optional[Any]:
    """评价正文擦写区：优先按已有文本行并集，且不得侵入编制人/审核人签字行。"""
    if not fitz or heading_rect is None or heading_rect.is_empty:
        return None
    y_sig = _merge_basic_info_label_row_y0(page, "编制人")
    ev = _merge_evaluation_body_rect(page, heading_rect)
    if ev is not None and not ev.is_empty:
        if y_sig is not None:
            ev.y1 = min(float(ev.y1), float(y_sig) - 8.0)
        if float(ev.y1) > float(ev.y0) + 14.0:
            return ev
    band = _merge_summary_evaluation_band_rect(page, heading_rect)
    if band is None or band.is_empty:
        return None
    if y_sig is not None:
        band.y1 = min(float(band.y1), float(y_sig) - 8.0)
    return band if float(band.y1) > float(band.y0) + 14.0 else None


def _merge_summary_evaluation_band_rect(page, heading_rect: Any) -> Optional[Any]:
    """
    「二、评价」标题下缘至「编制人」行上缘之间的正文带（仅取值列），
    用于完整擦除模板预印合格结论后再叠印评价全文。
    """
    if not fitz or heading_rect is None or heading_rect.is_empty:
        return None
    y0 = float(heading_rect.y1) + 3.0
    y_comp = _merge_basic_info_label_row_y0(page, "编制人")
    if y_comp is not None:
        y1 = float(y_comp) - 5.0
    else:
        y1 = float(heading_rect.y1) + 120.0
    if y1 <= y0 + 12.0:
        y1 = y0 + 80.0
    x0 = max(_MERGE_BASIC_INFO_VALUE_X0_PT, float(heading_rect.x1) + 2.0)
    mr = _merge_page_right_margin(page)
    x1 = float(page.rect.width) - mr
    return fitz.Rect(x0, y0, x1, y1)


def _merge_evaluation_value_column_rect(band: Any) -> Any:
    """评价正文擦除/写入区：限制在基本情况表取值列内。"""
    if not fitz or band is None or band.is_empty:
        return band
    return fitz.Rect(
        max(float(band.x0), _MERGE_BASIC_INFO_VALUE_X0_PT),
        float(band.y0),
        float(band.x1),
        float(band.y1),
    )


def _merge_build_evaluation_section_rects(
    page,
    heading_rect: fitz.Rect,
    profile: Optional["ReportTemplateProfile"] = None,
) -> tuple[Any, Any]:
    """「二、评价」下缘至「编制人/审核人」上缘：整段清空区 + 取值列写入区。"""
    prof = _resolve_report_template_profile(profile)
    if not fitz or heading_rect is None or heading_rect.is_empty:
        return None, None
    y0 = float(heading_rect.y1) + 2.0 + float(prof.evaluation_write_y_offset or 0.0)
    y_comp = _merge_basic_info_label_row_y0(page, "编制人")
    if y_comp is None:
        y_comp = _merge_basic_info_label_row_y0(page, "审核人")
    if y_comp is not None:
        y1 = float(y_comp) - 6.0
    else:
        y1 = y0 + 95.0
    if y1 <= y0 + 10.0:
        y1 = y0 + 80.0
    clear = fitz.Rect(
        float(prof.evaluation_clear_x0),
        y0,
        float(prof.basic_info_table_x1),
        y1,
    )
    write = fitz.Rect(
        float(prof.evaluation_write_x0),
        y0,
        float(prof.basic_info_table_x1) - 2.0,
        y1,
    )
    return clear, write


def _merge_clear_evaluation_section_full(
    page,
    heading_rect: fitz.Rect,
    profile: Optional["ReportTemplateProfile"] = None,
) -> tuple[Any, Any]:
    """完全清除评价标题与编制人/审核人之间的所有文本，返回 (clear_rect, write_rect)。"""
    clear, write = _merge_build_evaluation_section_rects(page, heading_rect, profile=profile)
    if clear is None or write is None or clear.is_empty:
        return clear, write
    prof = _resolve_report_template_profile(profile)
    y0, y1 = float(clear.y0), float(clear.y1)
    x_erase_min = float(heading_rect.x0) if heading_rect is not None else float(prof.evaluation_write_x0)
    if not prof.evaluation_text_only_clear:
        _merge_overlay_erase_transparent(page, clear, pad=0.12)
        _merge_apply_redactions_overlay(page)
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = _line_bbox_union(line.get("spans") or [])
            if bbox.is_empty:
                continue
            cy = (float(bbox.y0) + float(bbox.y1)) * 0.5
            if cy < y0 or cy > y1 or float(bbox.x0) < x_erase_min - 1.0:
                continue
            t = re.sub(r"\s+", "", _line_text(line))
            if t in ("二、评价", "二.评价", "编制人", "审核人", "授权签字人", "签发日期"):
                continue
            _merge_overlay_erase_transparent(page, bbox, pad=0.25)
    _merge_apply_redactions_overlay(page)
    return clear, write


def _merge_erase_evaluation_area_all_text(page, band: Any) -> None:
    """擦除「二、评价」正文带内全部旧文本（模板预印结论 + HTMLPDF 回填），再叠印评价模板句。"""
    if not fitz or page is None or band is None or band.is_empty:
        return
    val_band = _merge_evaluation_value_column_rect(band)
    _merge_overlay_erase_transparent(page, val_band, pad=0.15)
    _merge_apply_redactions_overlay(page)
    y0, y1 = float(val_band.y0), float(val_band.y1)
    x0 = float(val_band.x0)
    skip = ("二、评价", "二.评价", "编制人", "审核人", "授权签字")
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = re.sub(r"\s+", "", _line_text(line))
            if not t or any(s in t for s in skip):
                continue
            bbox = _line_bbox_union(line.get("spans") or [])
            if bbox.is_empty:
                continue
            cy = (float(bbox.y0) + float(bbox.y1)) * 0.5
            if cy < y0 or cy > y1 or float(bbox.x0) < x0 - 4.0:
                continue
            _merge_overlay_erase_transparent(page, bbox, pad=0.2)
    _merge_apply_redactions_overlay(page)


def _merge_erase_evaluation_verdict_residuals(page, band: Any) -> None:
    """擦除评价区内模板残留的合格/不合格结论文本行。"""
    if not fitz or page is None or band is None or band.is_empty:
        return
    needles = (
        "所检设备的质量控制相关参数均符合相关标准要求",
        "存在参数不符合相关标准",
        "所检设备的质量控制相关参数及工作场所放射防护检测结果均符合相关标准要求",
        "存在参数或工作场所放射防护检测结果不符合相关标准",
        "工作场所放射防护检测结果符合相关标准要求",
        "工作场所放射防护检测结果不符合相关标准",
    )
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = re.sub(r"\s+", "", _line_text(line))
            if not any(n in t for n in needles):
                continue
            bbox = _line_bbox_union(line.get("spans") or [])
            if bbox.is_empty:
                continue
            cy = (float(bbox.y0) + float(bbox.y1)) * 0.5
            if cy < float(band.y0) - 2.0 or cy > float(band.y1) + 2.0:
                continue
            _merge_overlay_erase_transparent(page, bbox, pad=0.2)
    _merge_apply_redactions_overlay(page)


def _merge_expand_device_count_erase_rect(rect: Any) -> Any:
    """台数叠印擦除区略向下扩展，覆盖模板栏位58/59（f49/f50）等红字残留。"""
    if not fitz or rect is None or rect.is_empty:
        return rect
    return fitz.Rect(
        float(rect.x0),
        float(rect.y0) - 1.0,
        float(rect.x1),
        max(float(rect.y1), float(rect.y0) + 28.0),
    )


def _merge_write_summary_overlay_cell(
    page,
    rect: Any,
    text: str,
    *,
    al: int,
    paragraph_layout: bool = False,
    inset: float = 1.0,
) -> None:
    if not (text or "").strip() or rect is None or rect.is_empty:
        return
    _merge_overlay_erase_transparent(page, _merge_inset_rect(rect, inset + 0.8), pad=0.12)
    _merge_apply_redactions_overlay(page)
    _merge_overlay_write_text_html_style(
        page,
        _merge_inset_rect(rect, inset),
        text,
        align=al,
        preferred_fontsize=_MERGE_PT_XIAOSI,
        paragraph_layout=paragraph_layout,
        fontsize_floor=_MERGE_PT_XIAOSI,
    )


def _merge_basic_info_table_x1(page, *, use_dsa_cap: bool = True) -> float:
    """基本情况表取值列右边界（pt）；单份报告用页面右留白，不套用 DSA 定版宽度。"""
    if not fitz or page is None:
        return _MERGE_BASIC_INFO_TABLE_X1_PT
    natural = float(page.rect.width) - _merge_page_right_margin(page)
    if not use_dsa_cap:
        return natural
    return min(natural, _MERGE_BASIC_INFO_TABLE_X1_PT)


def _expand_summary_project_name_write_rects(
    page,
    rr_org: Any,
    rr_title: Any,
    *,
    allow_fixed_fallback: bool = True,
    use_dsa_cap: bool = True,
) -> tuple[Any, Any]:
    """
    将模板 f35/f36 窄框扩展为表格实际两列取值格：
    左列至设备类型格左界，右列至表右缘（覆盖模板预印「放射诊疗设备/质量控制检测」）。
    """
    if not fitz:
        return rr_org, rr_title
    table_x1 = _merge_basic_info_table_x1(page, use_dsa_cap=use_dsa_cap)
    if rr_org is None or rr_org.is_empty or rr_title is None or rr_title.is_empty:
        if not allow_fixed_fallback:
            return rr_org, rr_title
        org = fitz.Rect(*_MERGE_SUMMARY_PN_ORG_XYXY)
        title = fitz.Rect(*_MERGE_SUMMARY_PN_TITLE_XYXY)
        title.x1 = table_x1
        return org, title
    split_x = float(rr_title.x0)
    y0 = min(float(rr_org.y0), float(rr_title.y0))
    y1 = max(float(rr_org.y1), float(rr_title.y1))
    org_w = fitz.Rect(float(rr_org.x0), y0, split_x - 0.4, y1)
    title_w = fitz.Rect(split_x, y0, table_x1, y1)
    return org_w, title_w


def _resolve_summary_project_name_cell_rects(
    page,
    template_rects: Dict[str, Any],
    *,
    allow_fixed_fallback: bool = True,
    use_dsa_cap: bool | None = None,
) -> tuple[Any, Any]:
    """解析项目名称左/右取值格；合成报告在模板框缺失时用定版坐标，单份报告不得套用。"""
    if not fitz:
        return None, None
    rr_org = template_rects.get("summary_project_name_org")
    rr_title = template_rects.get("summary_project_name_title")
    cap = allow_fixed_fallback if use_dsa_cap is None else bool(use_dsa_cap)
    if rr_org is None or rr_org.is_empty or rr_title is None or rr_title.is_empty:
        if not allow_fixed_fallback:
            return None, None
        rr_org = fitz.Rect(*_MERGE_SUMMARY_PN_ORG_XYXY)
        rr_title = fitz.Rect(*_MERGE_SUMMARY_PN_TITLE_XYXY)
        rr_title.x1 = _merge_basic_info_table_x1(page, use_dsa_cap=cap)
    return _expand_summary_project_name_write_rects(
        page,
        rr_org,
        rr_title,
        allow_fixed_fallback=allow_fixed_fallback,
        use_dsa_cap=cap,
    )


def _merge_summary_evaluation_write_band(
    page,
    body_rect: Any,
    *,
    use_dsa_cap: bool = True,
) -> tuple[Any, Any]:
    """返回 (擦除带, 写入带)；写入带尽量用满表格取值列，避免评价过早换行。"""
    if not fitz or body_rect is None or body_rect.is_empty:
        return body_rect, body_rect
    table_x0 = float(body_rect.x0)
    table_x1 = _merge_basic_info_table_x1(page, use_dsa_cap=use_dsa_cap)
    ins_e = float(_MERGE_EVAL_BOX_H_INSET_PT)
    ins_w = float(_MERGE_EVAL_WRITE_H_INSET_PT)
    erase = fitz.Rect(
        float(body_rect.x0) + ins_e,
        float(body_rect.y0),
        max(float(body_rect.x0) + ins_e + 20.0, float(body_rect.x1) - ins_e),
        float(body_rect.y1),
    )
    write = fitz.Rect(
        min(float(body_rect.x0), table_x0) + ins_w,
        float(body_rect.y0),
        max(float(body_rect.x1), table_x1) - ins_w,
        float(body_rect.y1),
    )
    return erase, write


def _merge_erase_summary_project_name_template_residuals(
    page,
    rr_org: Any,
    rr_title: Any,
) -> None:
    """擦除项目名称行模板预印字（如「放射诊疗设备」「质量控制检测」）及两格旧值。"""
    if not fitz or page is None:
        return
    row_rects: List[Any] = []
    org_w, title_w = _expand_summary_project_name_write_rects(page, rr_org, rr_title)
    for rect in (org_w, title_w):
        if rect is not None and not rect.is_empty:
            row_rects.append(rect)
    if not row_rects:
        return
    y0 = min(float(r.y0) for r in row_rects) - 2.0
    y1 = max(float(r.y1) for r in row_rects) + 2.0
    x0 = min(float(r.x0) for r in row_rects) - 1.0
    x1 = max(float(r.x1) for r in row_rects)
    row_band = fitz.Rect(x0, y0, x1, y1)
    _merge_overlay_erase_transparent(page, row_band, pad=0.08)
    _merge_apply_redactions_overlay(page)
    needles = (
        "放射诊疗设备",
        "质量控制检测",
        "工作场所放射防护检测",
        "性能检测",
    )
    d = page.get_text("dict") or {}
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            t = re.sub(r"\s+", "", _line_text(line))
            if not any(n in t for n in needles):
                continue
            for sp in line.get("spans") or []:
                bb = sp.get("bbox")
                if not bb or len(bb) < 4:
                    continue
                sr = fitz.Rect(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                cy = (float(sr.y0) + float(sr.y1)) * 0.5
                if cy < y0 or cy > y1 or float(sr.x0) < x0 - 4.0:
                    continue
                _merge_overlay_erase_transparent(page, sr, pad=0.25)
    _merge_apply_redactions_overlay(page)


def _write_summary_project_name_combined(
    page,
    rr_org: Any,
    rr_title: Any,
    combined: str,
    *,
    al: int,
) -> bool:
    """基本情况页项目名称：先合并为一行，再按合并单元格宽高自适应换行。"""
    text = (combined or "").strip()
    if not text or not fitz or page is None:
        return False
    org_w, title_w = _expand_summary_project_name_write_rects(page, rr_org, rr_title)
    merged = fitz.Rect(
        min(float(org_w.x0), float(title_w.x0)),
        min(float(org_w.y0), float(title_w.y0)),
        max(float(org_w.x1), float(title_w.x1)),
        max(float(org_w.y1), float(title_w.y1)),
    )
    _merge_erase_summary_project_name_template_residuals(page, rr_org, rr_title)
    _merge_overlay_erase_transparent(page, merged, pad=0.08)
    _merge_apply_redactions_overlay(page)
    _merge_overlay_write_text_html_style(
        page,
        merged,
        text,
        align=al,
        paragraph_layout=True,
        preferred_fontsize=_MERGE_PT_XIAOSI,
        fontsize_floor=_MERGE_PT_XIAOSI,
        valign_top=True,
    )
    return True


def _write_summary_project_name_split(
    page,
    rr_org: Any,
    rr_title: Any,
    org: str,
    title: str,
    *,
    al: int,
) -> bool:
    """基本情况页项目名称：医院名称 / 设备类型 双格分写（居中，先清模板预印字）。"""
    org_s = (org or "").strip()
    title_s = (title or "").strip()
    if not org_s and not title_s:
        return False
    if (
        rr_org is None
        or rr_title is None
        or rr_org.is_empty
        or rr_title.is_empty
    ):
        return False
    ac = int(getattr(fitz, "TEXT_ALIGN_CENTER", 1))
    org_w, title_w = _expand_summary_project_name_write_rects(page, rr_org, rr_title)
    _merge_erase_summary_project_name_template_residuals(page, rr_org, rr_title)
    if org_s:
        _merge_write_summary_overlay_cell(page, org_w, org_s, al=ac, paragraph_layout=False, inset=0.5)
    if title_s:
        _merge_write_summary_overlay_cell(page, title_w, title_s, al=ac, paragraph_layout=False, inset=0.5)
    return bool(org_s or title_s)


def _apply_summary_template_or_label_overlay(
    page,
    template_rects: Dict[str, Any],
    rect_key: str,
    label_needles: tuple[str, ...],
    text: str,
    *,
    al: int,
) -> None:
    """优先用模板 pdf.fields / steps.pdfAnchor 框叠印；无框时再按 PDF 文本层搜标签。"""
    if not fitz or page is None or not text:
        return
    rr = template_rects.get(rect_key) if isinstance(template_rects, dict) else None
    if rr is not None and not rr.is_empty:
        _merge_overlay_write_in_field_rect(
            page,
            rr,
            text,
            align=al,
            preferred_fontsize=_MERGE_PT_XIAOSI,
            paragraph_layout=False,
        )
        return
    _apply_summary_basic_info_label_row_overlay(page, label_needles, text, al=al)


def _apply_summary_basic_info_label_row_overlay(
    page,
    label_needles: tuple[str, ...],
    text: str,
    *,
    al: int,
    profile: Optional["ReportTemplateProfile"] = None,
) -> None:
    """基本情况表：按行标签右侧取值格叠印（检测类别、检测项目等）。"""
    if not fitz or page is None or not text:
        return
    y_lo, y_hi = _merge_basic_info_section_y_bounds(page)
    r_lab = None
    for needle in label_needles:
        r_lab = _merge_basic_info_table_label_rect_from_line(
            page, needle, y_lo=y_lo, y_hi=y_hi
        )
        if r_lab is not None and not r_lab.is_empty:
            break
    if r_lab is None:
        r_lab = _merge_search_first(page, list(label_needles))
    if r_lab is None or r_lab.is_empty:
        return
    band = _merge_basic_info_value_cell_rect_single_row(page, r_lab)
    band = _merge_clamp_basic_info_value_rect(band, profile=profile)
    if band is None or band.is_empty:
        return
    _merge_overlay_erase_transparent(page, band, pad=0.12)
    _merge_apply_redactions_overlay(page)
    _merge_overlay_write_text_html_style(
        page,
        band,
        text,
        align=al,
        preferred_fontsize=_MERGE_PT_XIAOSI,
        fontsize_floor=_MERGE_PT_XIAOSI,
    )


def _apply_summary_project_name_overlay(
    page,
    template_rects: Dict[str, Any],
    org: str,
    title: str,
    *,
    al: int,
    combined: str = "",
    single_report_mode: bool = False,
    profile: Optional["ReportTemplateProfile"] = None,
) -> None:
    """基本情况页：项目名称先合并为一行，按取值列宽高自适应换行（坐标逻辑不变）。"""
    if not fitz or page is None:
        return
    pfn = (combined or f"{(org or '').strip()}{(title or '').strip()}").strip()
    pfn = _normalize_rp_phrase_spacing(pfn)
    prof_early = _resolve_report_template_profile(profile)
    if pfn and len(pfn) > 36 and "及" in pfn and prof_early.basic_info_project_name_split_at_and:
        head, tail = pfn.split("及", 1)
        pfn = f"{head.rstrip()}\n及{tail.lstrip()}"
    if not pfn:
        return
    allow_fixed = not single_report_mode
    use_dsa_cap = allow_fixed
    r_lab = _merge_basic_info_project_name_label_rect(page)
    if r_lab is None:
        r_lab = _merge_search_first(page, ["项目名称"])
    rr_pn_full = template_rects.get("summary_project_name")
    rr_org, rr_title = _resolve_summary_project_name_cell_rects(
        page, template_rects, allow_fixed_fallback=allow_fixed, use_dsa_cap=use_dsa_cap
    )
    has_split_cells = (
        rr_org is not None
        and rr_title is not None
        and not rr_org.is_empty
        and not rr_title.is_empty
    )
    if has_split_cells:
        _merge_erase_summary_project_name_template_residuals(page, rr_org, rr_title)
    write_band = None
    if single_report_mode and rr_pn_full is not None and not rr_pn_full.is_empty:
        write_band = _merge_inset_rect(rr_pn_full, 1.0)
    elif r_lab is not None and not r_lab.is_empty:
        if single_report_mode:
            write_band = _merge_summary_project_name_cell_rect_robust(page, r_lab)
            if write_band is None or write_band.is_empty:
                write_band = _merge_summary_project_name_value_band(page, r_lab, _MERGE_PT_XIAOSI)
        else:
            write_band = _merge_summary_project_name_value_erase_union(page, r_lab)
    elif has_split_cells:
        org_w, title_w = _expand_summary_project_name_write_rects(
            page,
            rr_org,
            rr_title,
            allow_fixed_fallback=allow_fixed,
            use_dsa_cap=use_dsa_cap,
        )
        write_band = fitz.Rect(
            min(float(org_w.x0), float(title_w.x0)),
            min(float(org_w.y0), float(title_w.y0)),
            max(float(org_w.x1), float(title_w.x1)),
            max(float(org_w.y1), float(title_w.y1)),
        )
    if write_band is None or write_band.is_empty:
        return
    if single_report_mode:
        prof = _resolve_report_template_profile(profile)
        y_off = float(prof.basic_info_project_name_write_y_offset or 0.0)
        write_band = fitz.Rect(
            max(float(write_band.x0), float(prof.basic_info_project_name_x0)),
            float(write_band.y0) + y_off,
            float(prof.basic_info_table_x1) - 2.0,
            float(write_band.y1) + y_off,
        )
        min_h = max(40.0, _MERGE_PT_XIAOSI * 3.0)
        if float(write_band.y1 - write_band.y0) < min_h:
            write_band = fitz.Rect(
                float(write_band.x0),
                float(write_band.y0),
                float(write_band.x1),
                float(write_band.y0) + min_h,
            )
    if not single_report_mode:
        # 合成报告定版叠印微调：右移 12pt、上移 12pt（PyMuPDF y 向下为正）
        write_band = fitz.Rect(
            float(write_band.x0) + 12.0,
            float(write_band.y0) - 12.0,
            float(write_band.x1) + 12.0,
            float(write_band.y1) - 12.0,
        )
    _merge_overlay_erase_transparent(page, write_band, pad=0.08)
    _merge_apply_redactions_overlay(page)
    _merge_overlay_write_text_html_style(
        page,
        write_band,
        pfn,
        align=al,
        paragraph_layout=True,
        preferred_fontsize=_MERGE_PT_XIAOSI,
        fontsize_floor=_MERGE_PT_XIAOSI,
        valign_top=True,
    )


def _apply_summary_page_overlay_boxes(
    page,
    *,
    rr_pn: Any,
    rr_dc: Any,
    rr_ev: Any,
    pfn: str,
    dcl: str,
    eva: str,
    al: int,
    rr_pn_org: Any = None,
    rr_pn_title: Any = None,
    pfn_org: str = "",
    pfn_title: str = "",
    use_dsa_cap: bool = True,
    single_report_mode: bool = False,
    profile: Optional["ReportTemplateProfile"] = None,
) -> bool:
    """
    基本情况页：定版框透明擦除后叠印受检工作场所/台数与评价正文。
    项目名称由 ``_apply_summary_project_name_overlay`` 单独处理。
    """
    if not fitz or page is None:
        return False
    dcl_s = (dcl or "").strip()
    eva_s = _merge_evaluation_overlay_text((eva or "").strip(), profile=profile)
    if rr_dc is None or rr_dc.is_empty:
        if not dcl_s and not eva_s:
            return False
    elif not any([dcl_s, eva_s]):
        return False

    wrote = False
    if dcl_s and rr_dc is not None and not rr_dc.is_empty:
        rr_dc = _merge_adjust_basic_info_device_count_write_rect(rr_dc, profile=profile)
        rr_dc = _merge_expand_device_count_erase_rect(rr_dc)
        _merge_write_summary_overlay_cell(page, rr_dc, dcl_s, al=al, paragraph_layout=False)
        wrote = True

    if eva_s:
        r_ev = _merge_search_first(page, ["二、评价", "二.评价", "二．评价"])
        prof = _resolve_report_template_profile(profile)
        use_full_clear = single_report_mode or prof.evaluation_text_only_clear or float(
            prof.evaluation_clear_x0
        ) < float(prof.evaluation_write_x0) - 0.5
        rr_write = None
        if r_ev is not None and not r_ev.is_empty and use_full_clear:
            _, rr_write = _merge_clear_evaluation_section_full(page, r_ev, profile=profile)
        else:
            rr_ev_eff = None
            if rr_ev is not None and not rr_ev.is_empty:
                rr_ev_eff = fitz.Rect(rr_ev)
            elif r_ev is not None:
                rr_ev_eff = _merge_summary_evaluation_overlay_rect(page, r_ev)
            if rr_ev_eff is None or rr_ev_eff.is_empty:
                return wrote
            rr_erase, rr_write = _merge_summary_evaluation_write_band(
                page, rr_ev_eff, use_dsa_cap=use_dsa_cap
            )
            rr_erase = _merge_evaluation_value_column_rect(rr_erase)
            rr_write = _merge_evaluation_value_column_rect(rr_write)
            _merge_erase_evaluation_area_all_text(page, rr_erase)
            _merge_erase_evaluation_verdict_residuals(page, rr_erase)
            _merge_overlay_erase_transparent(page, rr_erase)
            _merge_apply_redactions_overlay(page)
        if rr_write is None or rr_write.is_empty:
            return wrote
        if not prof.evaluation_text_only_clear:
            rr_write = _merge_clamp_basic_info_value_rect(rr_write, profile=profile)
        _merge_overlay_write_text_html_style(
            page,
            rr_write,
            eva_s,
            align=al,
            paragraph_layout=True,
            preferred_fontsize=_MERGE_PT_XIAOSI,
            fontsize_floor=_MERGE_PT_XIAOSI,
            valign_top=True,
        )
        wrote = True
    return wrote


def apply_merged_report_merge_overlay(
    doc,
    header_page_count: int,
    overlay: Optional[dict],
    *,
    template_parsed: Optional[dict] = None,
    use_global_fixed_rects: bool = True,
    profile: Optional["ReportTemplateProfile"] = None,
) -> None:
    """
    在已插入的首份前 N 页上叠印（默认 N=3：封面、声明、一、项目基本情况；**版式页均来自合成顺序中
    第一份小报告 PDF 的前 N 页**，再叠合并信息）。

    封面：报告日期、项目名称（受检单位 / 报告名称两行）、受检编号序列表等。
    第 N 页（基本情况）：**项目名称** 改为与封面一致的 **「受检单位名称 + 报告名称」** 单行
    （``project_name_combined``），字号 **小四**；受检设备台数、「二、评价」等。

    与 ``htmlpdf_service.build_filled_pdf`` 对齐：坐标优先取标签右侧 span / 下划线界；透明擦除；overlay 写字。
    若 ``_MERGE_OVERLAY_FIXED_RECT_1BASED_XYWH`` 中配置了与当前物理页一致的框，则 **项目名称 / 台数 / 评价 / 封面报告名称**
    直接使用该矩形叠印（定版坐标）。

    单份自动报告应设 ``use_global_fixed_rects=False``，并传入 ``template_parsed``：
    叠印框 **仅** 从模板 ``pdf.fields`` / ``steps.pdfAnchor`` 解析；模板未配置时再按 PDF 文本层搜标签定位。
    **不得** 套用 ``_MERGE_OVERLAY_FIXED_RECT_1BASED_XYWH`` / DSA 定版坐标，以免擦除越界、表格线消失。
    """
    if not fitz or not overlay or not isinstance(overlay, dict):
        return
    nh = int(header_page_count)
    template_rects = build_report_template_overlay_rects(
        template_parsed,
        cover_page_1based=1,
        summary_page_1based=nh,
        apply_fixed_summary_rects=use_global_fixed_rects,
    )
    org_pdf_cell = _merge_read_summary_inspected_org_from_rect(doc, nh) if use_global_fixed_rects else ""

    md = (overlay.get("merge_date_str") or "").strip()
    org = (
        overlay.get("cover_org_line")
        or overlay.get("cover_inspected_org")
        or overlay.get("inspected_org")
        or ""
    ).strip()
    crt = (overlay.get("cover_report_title") or "").strip()
    nos = (overlay.get("inspection_index_line") or "").strip()
    pfn_ov = (overlay.get("project_name_combined") or "").strip()
    cover_report_no_line = (overlay.get("cover_report_no_line") or "").strip()
    cover_insp_type = (overlay.get("cover_inspection_type") or "").strip()
    report_no_disp = _sanitize_report_no_for_header((overlay.get("report_no_display") or "").strip())
    org_for_pfn = (org_pdf_cell or org).strip()
    # 基本情况「项目名称」= 受检单位名称 + 合并报告名称；表中受检单位框读字优先于 overlay
    if org_for_pfn and crt:
        pfn = f"{org_for_pfn}{crt}".strip()
    elif not pfn_ov:
        pfn = f"{org_for_pfn}{crt}".strip()
    else:
        pfn = pfn_ov
        if org_for_pfn and not pfn.startswith(org_for_pfn):
            pfn = f"{org_for_pfn}{pfn}".strip()
    dcl = (overlay.get("device_count_label") or "").strip()
    eva = (overlay.get("evaluation_body") or "").strip()
    eva = _merge_evaluation_overlay_text(eva, profile=profile)
    commission_no = (overlay.get("commission_no") or "").strip()
    inspected_addr = (overlay.get("inspected_org_address") or "").strip()
    if not any(
        [
            md,
            org,
            crt,
            nos,
            pfn,
            dcl,
            eva,
            report_no_disp,
            cover_report_no_line,
            cover_insp_type,
            commission_no,
            inspected_addr,
        ]
    ):
        return

    al = int(getattr(fitz, "TEXT_ALIGN_LEFT", 0))
    ac = int(getattr(fitz, "TEXT_ALIGN_CENTER", 1))

    def _apply_cover(pi: int) -> None:
        if pi < 0 or pi >= doc.page_count:
            return
        page = doc[pi]

        title_for_cover = (crt or "").strip()
        if not title_for_cover and pfn:
            title_for_cover = pfn
        insp_type = (overlay.get("cover_inspection_type") or "").strip()
        report_no_line = (overlay.get("cover_report_no_line") or "").strip()
        if not report_no_line and report_no_disp:
            report_no_line = f"报告编号：{report_no_disp}"

        if not use_global_fixed_rects:
            _apply_report_cover_overlay_by_label_search(
                page,
                org=org,
                cover_title=title_for_cover,
                merge_date=md,
                inspection_type=insp_type,
                report_no_line=report_no_line,
                inspection_index=nos,
                page_index_0based=pi,
                use_fixed_title_rect=False,
                profile=profile,
            )
            return

        cov_org_rect = template_rects.get("cover_inspected_org")
        cov_pn_rect = template_rects.get("cover_project_name")
        cov_pn_org = template_rects.get("cover_project_name_org")
        cov_pn_title = template_rects.get("cover_project_name_title")
        cov_date_rect = template_rects.get("cover_report_date")

        if report_no_disp:
            _merge_apply_cover_report_number(page, report_no_disp, template_rects, page_index_0based=pi)

        if org and cov_org_rect is not None:
            _merge_overlay_write_cover_field(
                page, cov_org_rect, org, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
            )
        title_for_cover = (crt or "").strip()
        if not title_for_cover and pfn:
            title_for_cover = pfn
        if org and cov_pn_org is not None:
            _merge_overlay_write_cover_field(
                page, cov_pn_org, org, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
            )
        if title_for_cover and cov_pn_title is not None:
            title_rect = _merge_cover_title_write_rect(cov_pn_org, cov_pn_title)
            _merge_overlay_write_cover_field(
                page, title_rect, title_for_cover, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
            )
        elif title_for_cover and cov_pn_rect is not None:
            _merge_overlay_write_cover_field(
                page, cov_pn_rect, title_for_cover, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
            )
        if md and cov_date_rect is not None:
            _merge_overlay_write_cover_field(
                page, cov_date_rect, md, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
            )

        insp_type = (overlay.get("cover_inspection_type") or "").strip()
        cov_type_rect = template_rects.get("cover_inspection_type")
        if insp_type and cov_type_rect is not None:
            _merge_overlay_write_in_field_rect(
                page,
                cov_type_rect,
                insp_type,
                align=ac,
                preferred_fontsize=_MERGE_PT_SIHAO,
            )

        need_date_search = md and cov_date_rect is None
        need_type_search = insp_type and cov_type_rect is None
        need_pn_search = (org or crt) and not (cov_pn_org is not None and cov_pn_title is not None)

        r_date = _merge_search_first(page, ["报告日期", "报告时间"])
        if r_date and need_date_search:
            _merge_overlay_write_cover_label_value(
                page,
                ["报告日期", "报告时间"],
                md,
                align=ac,
                preferred_fontsize=_MERGE_PT_SIHAO,
            )

        if need_type_search:
            _merge_overlay_write_cover_label_value(
                page,
                ["检测类型"],
                insp_type,
                align=ac,
                preferred_fontsize=_MERGE_PT_SIHAO,
            )

        r_no = _merge_search_first(page, ["受检编号"])
        if r_no and nos:
            fb = _merge_value_rect_same_line(page, r_no)
            vr = _merge_value_span_union_right_of_label(page, r_no)
            dr = _merge_dict_line_value_rect_right_of_label(page, r_no, ("受检编号",))
            band = (
                dr
                if (dr is not None and not dr.is_empty)
                else (vr if (vr is not None and not vr.is_empty) else fb)
            )
            _merge_overlay_erase_transparent(page, band)
            _merge_apply_redactions_overlay(page)
            _merge_overlay_write_text_html_style(page, band, nos, align=al)

        r_lab = _merge_search_last_by_bottom(page, ["项目名称"])
        if r_lab is None:
            r_lab = _merge_search_first(page, ["项目名称"])
        if need_pn_search and r_lab and (org or crt):
            band0 = _merge_cover_project_name_value_band(page, r_lab, 10.5)
            band = fitz.Rect(band0)
            lx1 = float(r_lab.x1) + 0.8
            vr = _merge_value_span_union_right_of_label(page, r_lab)
            dr = _merge_dict_line_value_rect_right_of_label(page, r_lab, ("项目名称",))
            # 仅用字典行扩展右边界，避免 include_rect 把整页纵跨拉进 band 导致 midy 分割错位、第二行不显示
            for ext in (vr, dr):
                if ext is not None and not ext.is_empty and float(ext.x0) >= lx1 - 0.5:
                    band.x1 = max(float(band.x1), float(ext.x1))
            band.x0 = max(float(band.x0), lx1)
            band.y0, band.y1 = float(band0.y0), float(band0.y1)
            _merge_overlay_erase_transparent(page, band)
            _merge_apply_redactions_overlay(page)
            _merge_write_cover_project_name_two_lines(
                page,
                r_lab,
                band,
                org or "",
                crt or "",
                align_center=ac,
                page_index_0based=pi,
                use_fixed_title_rect=True,
            )

    def _apply_summary(pi: int) -> None:
        if pi < 0 or pi >= doc.page_count:
            return
        page = doc[pi]

        rr_pn = template_rects.get("summary_project_name")
        rr_pn_org = template_rects.get("summary_project_name_org")
        rr_pn_title = template_rects.get("summary_project_name_title")
        rr_dc = template_rects.get("summary_device_count")
        rr_ev = template_rects.get("summary_evaluation")
        if use_global_fixed_rects:
            rr_pn = rr_pn or _merge_fixed_fitz_rect_for_page_index("summary_project_name", pi)
            rr_dc = rr_dc or _merge_fixed_fitz_rect_for_page_index("summary_device_count", pi)
            rr_ev = rr_ev or _merge_fixed_fitz_rect_for_page_index("summary_evaluation", pi)
            _apply_summary_page_overlay_boxes(
                page,
                rr_pn=rr_pn,
                rr_dc=rr_dc,
                rr_ev=rr_ev,
                pfn=pfn,
                dcl=dcl,
                eva=eva,
                al=al,
                rr_pn_org=rr_pn_org,
                rr_pn_title=rr_pn_title,
                pfn_org=org_for_pfn,
                pfn_title=crt,
                profile=profile,
            )
            _apply_summary_project_name_overlay(
                page,
                template_rects,
                org_for_pfn or org,
                crt,
                al=al,
                combined=pfn,
                profile=profile,
            )
            return

        # 单份报告：坐标仅来自 template_parsed（pdf.fields / steps.pdfAnchor）；缺框时再搜 PDF 文本层。
        _apply_summary_page_overlay_boxes(
            page,
            rr_pn=rr_pn,
            rr_dc=rr_dc,
            rr_ev=rr_ev,
            pfn=pfn,
            dcl=dcl,
            eva=eva,
            al=al,
            rr_pn_org=rr_pn_org,
            rr_pn_title=rr_pn_title,
            pfn_org=org_for_pfn,
            pfn_title=crt,
            use_dsa_cap=False,
            single_report_mode=True,
            profile=profile,
        )
        if dcl and (rr_dc is None or rr_dc.is_empty):
            prof_dc = _resolve_report_template_profile(profile)
            val_dc = _merge_basic_info_device_count_value_rect(page)
            if val_dc is not None and not val_dc.is_empty:
                exclude_unit = not bool(prof_dc.device_count_include_unit_suffix)
                erase_dc = _merge_basic_info_device_count_value_rect(
                    page,
                    exclude_unit_span=exclude_unit,
                )
                if erase_dc is None or erase_dc.is_empty:
                    erase_dc = val_dc
                erase_dc = _merge_expand_device_count_erase_rect(erase_dc)
                _merge_overlay_erase_transparent(page, erase_dc, pad=0.35)
                _merge_apply_redactions_overlay(page)
                write_dc = erase_dc if exclude_unit else val_dc
                band_dc = _merge_adjust_basic_info_device_count_write_rect(
                    _merge_inset_rect(write_dc, 0.6),
                    profile=profile,
                )
                _merge_overlay_write_text_html_style(
                    page,
                    band_dc,
                    dcl,
                    align=al,
                    preferred_fontsize=_MERGE_PT_XIAOSI,
                    fontsize_floor=_MERGE_PT_XIAOSI,
                )
            else:
                r_dc = _merge_search_last_by_bottom(page, list(_summary_device_count_label_needles()))
                if r_dc is None:
                    r_dc = _merge_search_first(page, list(_summary_device_count_label_needles()))
                if r_dc:
                    fb = _merge_value_rect_same_line(page, r_dc)
                    vr = _merge_value_span_union_right_of_label(page, r_dc)
                    dr = _merge_dict_line_value_rect_right_of_label(
                        page, r_dc, _summary_device_count_label_needles()
                    )
                    band_dc = (
                        dr
                        if (dr is not None and not dr.is_empty)
                        else (vr if (vr is not None and not vr.is_empty) else fb)
                    )
                    if band_dc is not None and not band_dc.is_empty:
                        band_dc.y1 = max(float(band_dc.y1), float(r_dc.y1) + 8.0)
                        band_dc = _merge_adjust_basic_info_device_count_write_rect(band_dc, profile=profile)
                        _merge_overlay_erase_transparent(page, band_dc)
                        _merge_apply_redactions_overlay(page)
                        _merge_overlay_write_text_html_style(
                            page,
                            band_dc,
                            dcl,
                            align=al,
                            preferred_fontsize=_MERGE_PT_XIAOSI,
                            fontsize_floor=_MERGE_PT_XIAOSI,
                        )

        _apply_summary_project_name_overlay(
            page,
            template_rects,
            org_for_pfn or org,
            crt,
            al=al,
            combined=pfn,
            single_report_mode=True,
            profile=profile,
        )

        prof = _resolve_report_template_profile(profile)
        skip_htmlpdf_slots = frozenset(prof.htmlpdf_basic_info_pdf_field_ids or ())
        if commission_no and "f1" not in skip_htmlpdf_slots:
            rr_comm = template_rects.get("summary_commission_no")
            if rr_comm is not None and not rr_comm.is_empty:
                band = _merge_inset_rect(rr_comm, 1.0)
                band = _merge_clamp_basic_info_value_rect(band, profile=profile)
                _merge_overlay_erase_transparent(page, band, pad=0.12)
                _merge_apply_redactions_overlay(page)
                _merge_overlay_write_text_html_style(
                    page,
                    band,
                    commission_no,
                    align=al,
                    preferred_fontsize=_MERGE_PT_XIAOSI,
                    fontsize_floor=_MERGE_PT_XIAOSI,
                )
            else:
                _apply_summary_basic_info_label_row_overlay(
                    page,
                    ("委托编号",),
                    commission_no,
                    al=al,
                    profile=profile,
                )
        if inspected_addr and "f3" not in skip_htmlpdf_slots:
            rr_addr = template_rects.get("summary_inspected_org_address")
            if rr_addr is not None and not rr_addr.is_empty:
                band = _merge_inset_rect(rr_addr, 1.0)
                band = _merge_clamp_basic_info_value_rect(band, profile=profile)
                _merge_overlay_erase_transparent(page, band, pad=0.12)
                _merge_apply_redactions_overlay(page)
                _merge_overlay_write_text_html_style(
                    page,
                    band,
                    inspected_addr,
                    align=al,
                    preferred_fontsize=_MERGE_PT_XIAOSI,
                    fontsize_floor=_MERGE_PT_XIAOSI,
                )
            else:
                _apply_summary_basic_info_label_row_overlay(
                    page,
                    ("受检单位地址",),
                    inspected_addr,
                    al=al,
                    profile=profile,
                )

    try:
        _apply_cover(0)
        if nh >= 3:
            _apply_summary(nh - 1)
    except Exception as exc:
        logger.warning("apply_merged_report_merge_overlay: %s", exc)


def merge_report_pdfs_header_toc_sections(
    pdf_paths: Sequence[str],
    *,
    section_titles: Optional[Sequence[str]] = None,
    header_page_count: int = 3,
    merged_catalog_report_no: str = "",
    commission_no_suffix6: str = "",
    merge_overlay: Optional[dict] = None,
) -> Tuple[Optional[bytes], str]:
    """
    合并多份「报告」PDF：**前 N 页（默认 N=3）整页取自合成顺序中第一份小报告 PDF 的前 N 页**，
    即封面、声明、一、项目基本情况（仅作版式与底图；合并字段由 ``merge_overlay`` 叠印改写）。

    下一页为插入的目录，再按顺序拼接各份「三、检测结果」一节（至「四、」或「附录」前）。

    插入的目录单独一页，页眉固定为「第 2 页/共 y 页」；y = PDF 总页数 − 2（封面、声明不计）。
    其余页「第 x 页」仍为物理页 − 2；目录页在全文替换页码时也强制为第 2 页。

    目录页：白底 + 底层居中水印（见 merged_report_watermark.png 或环境变量 MERGED_REPORT_WATERMARK_PNG），
    再按固定几何绘制「目  录」与两级目录行（「一、{PDF 文件名} …」「{n}.1  质量控制检测项目及结果 …」）。
    不再读取「目录样例」PDF；目录与页眉坐标均以代码内常量为准，按当前页宽高相对 A4 基准缩放。

    合并结束后：除封面、声明外，页眉统一为左上「报告编号：{report_no}」、右上「第 x 页/共 y 页」，
    左右坐标固定为院方 DSA 样例第 3 页测量值，仅页码随页变化。

    merge_overlay：若非空，在插入首份前 N 页后对封面与第 N 页叠印合并日期、合并报告名称、
    受检设备台数与评价等（见 apply_merged_report_merge_overlay）。

    返回 (pdf_bytes, error_message)；成功时 error 为空字符串。
    """
    if not fitz:
        return None, "未安装 PyMuPDF（pymupdf），无法合并报告"
    paths = [p for p in pdf_paths if p and os.path.isfile(p) and os.path.getsize(p) > 0]
    if len(paths) < 2:
        return None, "至少需要两份有效 PDF"
    titles = list(section_titles) if section_titles else [os.path.basename(p) for p in paths]
    while len(titles) < len(paths):
        titles.append(os.path.basename(paths[len(titles)]))

    section_specs: List[Tuple[str, int, int]] = []
    for p in paths:
        rng = detect_sanjian_jiance_jieguo_page_range(p)
        if rng is None:
            return None, f"未找到「三、检测结果」: {os.path.basename(p)}"
        a, b = rng
        if b <= a:
            return None, f"「三、检测结果」无有效页: {os.path.basename(p)}"
        section_specs.append((p, a, b))

    first_doc = fitz.open(paths[0])
    try:
        w, h = float(first_doc[0].rect.width), float(first_doc[0].rect.height)
        nh = min(max(0, int(header_page_count)), first_doc.page_count)
        if nh <= 0:
            return None, "首份 PDF 无可用页面作为统一前页"
        section_page_counts = [b - a for _, a, b in section_specs]
        total_physical_pages = nh + 1 + sum(section_page_counts)
        section_start_physical_1based: List[int] = []
        cur = nh + 2
        for cnt in section_page_counts:
            section_start_physical_1based.append(cur)
            cur += cnt

        # 合并生成的「目录」单独占一页；正文页码规则在 rewrite_merged_report_page_headers 中统一套用

        base_report_no = (merged_catalog_report_no or "").strip()
        if not base_report_no:
            base_report_no = _extract_report_number_from_pdf(paths[0])
        base_report_no = _sanitize_report_no_for_header(base_report_no) or "合并报告"
        report_no = _apply_commission_suffix_to_report_no_display(
            base_report_no, (commission_no_suffix6 or "").strip()
        )

        out = fitz.open()
        try:
            # 前 nh 页：合成顺序中第一份小报告 PDF 的前 nh 页（默认可为封面+声明+基本情况）
            out.insert_pdf(first_doc, from_page=0, to_page=nh - 1)
            apply_merged_report_merge_overlay(out, nh, merge_overlay)

            link_plan: List[Tuple[fitz.Rect, int]] = []
            geom = _merged_toc_geometry_for_size(w, h)
            draw_rm = 52.0

            # 在前 nh 页之后显式追加一页作为目录（物理第 nh+1 页），避免与前几页混同
            page = out.new_page(width=w, height=h)
            _merged_report_insert_watermark_bottom_layer(page, w, h)
            font_main = _register_simsun_on_page(page)

            fs = float(geom.get("body_fs") or 10.5)
            lm = float(geom.get("entry_left_x") or 56.0)
            rm = draw_rm
            num_rx = geom.get("num_right_x")
            num_rx_f = float(num_rx) if num_rx is not None else None
            line_step = max(12.0, float(geom.get("line_step") or fs * 1.65))

            # 页眉由 merge 结束后 rewrite_merged_report_page_headers 统一按固定坐标绘制
            _draw_merged_toc_title_mu_lu(page, geom, font_main)

            y = float(geom["first_entry_baseline"])
            for i, (pth, a, b) in enumerate(section_specs):
                raw_title = titles[i] if i < len(titles) else os.path.basename(pth)
                level1 = _toc_level1_title_from_pdf_filename(raw_title, pth)
                major = _cn_ordinal_major(i)
                left_main = f"{major}、{level1}"
                dest_0 = nh + 1 + sum(section_page_counts[:i])
                p_main_display = _report_body_page_from_physical_1based(
                    section_start_physical_1based[i]
                )
                rect_m = _draw_toc_row_leader_rightnum(
                    page,
                    y,
                    w,
                    lm,
                    rm,
                    left_main,
                    p_main_display,
                    font_main,
                    fs,
                    num_right_x=num_rx_f,
                )
                link_plan.append((rect_m, dest_0))
                y += line_step

                span = b - a
                if span >= 2:
                    sub_left = f"{i + 1}.1  质量控制检测项目及结果"
                    p_sub_display = _report_body_page_from_physical_1based(
                        section_start_physical_1based[i] + 1
                    )
                    dest_sub_0 = dest_0 + 1
                    rect_s = _draw_toc_row_leader_rightnum(
                        page,
                        y,
                        w,
                        float(geom.get("sub_left_x") or (lm + 22.0)),
                        rm,
                        sub_left,
                        p_sub_display,
                        font_main,
                        fs,
                        num_right_x=num_rx_f,
                    )
                    link_plan.append((rect_s, dest_sub_0))
                    y += line_step

                if y > h - 72:
                    break

            for spec_i, (pth, a, b) in enumerate(section_specs):
                doc = fitz.open(pth)
                try:
                    start_pi = out.page_count
                    out.insert_pdf(doc, from_page=a, to_page=b - 1)
                    if spec_i > 0 and start_pi < out.page_count:
                        _merge_erase_sanjian_heading_line_if_present(out[start_pi])
                finally:
                    doc.close()

            toc_idx_0 = nh
            if toc_idx_0 < out.page_count:
                toc_page = out[toc_idx_0]
                for rect, dest_0 in link_plan:
                    if dest_0 < 0 or dest_0 >= out.page_count:
                        continue
                    try:
                        toc_page.insert_link(
                            {
                                "kind": fitz.LINK_GOTO,
                                "from": rect,
                                "page": dest_0,
                                "to": fitz.Point(72, 96),
                            }
                        )
                    except Exception as exc:
                        logger.warning("insert_link failed: %s", exc)

            rewrite_merged_report_page_headers(
                out,
                report_no=report_no,
                toc_page_0based=nh,
            )

            # 页眉整带擦写后再叠印一次前 N 页合并字段，避免与页眉处理顺序相关的漏改/错位
            apply_merged_report_merge_overlay(out, nh, merge_overlay)

            apply_merged_inspection_numbers_on_section_heads(
                out,
                toc_page_0based=nh,
                section_page_counts=section_page_counts,
            )
            apply_merged_quality_subheading_renumbers(
                out,
                toc_page_0based=nh,
                section_page_counts=section_page_counts,
            )

            redact_yixia_kongbai_except_last_page(out)
            ensure_last_page_yixia_kongbai_marker(out)

            return out.tobytes(deflate=True, garbage=4, clean=True), ""
        finally:
            out.close()
    except Exception as exc:
        logger.exception("merge_report_pdfs_header_toc_sections failed")
        return None, str(exc)
    finally:
        first_doc.close()
