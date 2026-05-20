import io
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

# 报告合并封面/项目名称：优先从标题中识别的设备大类（后续版本可在此元组末尾追加新类型）。
# 正则 alternation 按「长度降序、同长按字典序」排列，避免「DR」误匹配「动态DR」等子串。
MERGED_REPORT_DEVICE_TYPE_LABELS: Tuple[str, ...] = (
    "CT",
    "DR",
    "DSA",
    "C型臂",
    "胃肠机",
    "动态DR",
    "乳腺DR",
    "口腔CBCT",
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
    "summary_device_count": (3, 393.1, 305.19, 126.83, 38.2),
    # 二、评价正文区（y0 略下移约 14pt，避免叠字整体偏上）
    "summary_evaluation": (3, 65.58, 475.83, 453.47, 75.62),
}

# 评价区擦除/叠字左右内缩（pt），避免 redact 吃掉表格外竖线
_MERGE_EVAL_BOX_H_INSET_PT = 3.0
# 仅「应委托方要求」「所检设备的质量控制相关参数」两行首加缩进（全角空格×2）
_MERGE_EVAL_LINE_INDENT = "\u3000\u3000"


def _merge_evaluation_overlay_text(raw: str) -> str:
    """
    评价叠印用正文：去掉各行误加的首空白后，仅在
    以「应委托方要求」或「所检设备的质量控制相关参数」开头的行前加固定缩进，其它行不缩进。
    """
    t = (raw or "").replace("\r\n", "\n").strip()
    if not t:
        return ""
    out_lines: List[str] = []
    for ln in t.split("\n"):
        s = ln.lstrip(" \t\u3000")
        if not s:
            out_lines.append("")
            continue
        if s.startswith("应委托方要求"):
            out_lines.append(_MERGE_EVAL_LINE_INDENT + s)
        elif s.startswith("所检设备的质量控制相关参数"):
            out_lines.append(_MERGE_EVAL_LINE_INDENT + s)
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
    finally:
        doc.close()
    return ""


def _digits_for_commission_suffix6(raw: str) -> str:
    """从项目编码/委托编号类字符串中抽取数字，取末 6 位（不足左补 0）。"""
    d = "".join(re.findall(r"\d", raw or ""))
    if not d:
        return ""
    if len(d) >= 6:
        return d[-6:]
    return d.zfill(6)


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


def _collect_song_font_file_candidates() -> List[Path]:
    """宋体/宋体族字体文件候选（用于目录与页码重绘）；合并报告固定优先 SIMSUN.TTC。"""
    names = (
        "SIMSUN.TTC",
        "simsun.ttc",
        "SimSun.ttc",
        "SIMSUN.ttf",
        "simsun.ttf",
        "STSONG.TTF",
        "stsong.ttf",
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


def _resolve_song_embed_font_path() -> Optional[str]:
    cands = _collect_song_font_file_candidates()
    return str(cands[0]) if cands else None


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
    return rects


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


def _redact_original_toc_text_spans(page: fitz.Page) -> int:
    """
    仅擦除模板目录页中需替换的文字块（页眉编号行、目录标题、各目录行），尽量保留背景与水印底图。
    返回添加的红区数量。
    """
    rects = _collect_toc_redact_rects_from_page(page)
    return _apply_toc_redact_rects(page, rects)


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
    return str(p2) if p2.is_file() else ""


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


def _draw_header_report_no_and_page_footer(
    page,
    report_no: str,
    report_x: int,
    report_y: int,
    split_geom: Dict[str, Any],
    fontname: str,
) -> None:
    """左上角「报告编号：…」+ 右上角「第 x 页/共 y 页」，坐标取自 split_geom。"""
    if not fitz:
        return
    fs = float(split_geom.get("fs") or 10.5)
    left_txt = f"报告编号：{report_no}"
    # 页码展示：「第 x 页/共 y 页」（「第」「共」后各一空格）
    right_txt = f"第 {int(report_x)} 页/共 {int(report_y)} 页"
    lx = float(split_geom["left_x0"])
    ly = float(split_geom["left_baseline"])
    rx1 = float(split_geom["right_x1"])
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
        split = _header_split_geom_scaled_to_page(page)
        w, h = float(page.rect.width), float(page.rect.height)
        band_y1 = min(float(split.get("band_y1") or 80.0), h * 0.2)
        band = fitz.Rect(margin, 2.0, w - margin, band_y1)
        if not band.is_empty and band.y1 > band.y0:
            page.add_redact_annot(band + fitz.Rect(-1, -1, 3, 3), fill=(1, 1, 1))
            page.apply_redactions()
        font_embed = _register_simsun_on_page(page)
        _draw_header_report_no_and_page_footer(
            page, report_no, report_x, report_y, split, font_embed
        )


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
        if hits:
            try:
                return fitz.Rect(hits[0])
            except Exception:
                pass
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
    """(「一、项目基本情况」标题下缘, 「受检设备台数」行上缘)，用于锁定表中「项目名称」。"""
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
            if "受检设备台数" in t:
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
    return None


def _merge_basic_info_device_count_value_rect(page) -> Optional[fitz.Rect]:
    """基本情况表内「受检设备台数」同行右侧取值单元格（避免误用页脚/其它处重复词）。"""
    if not fitz or page is None:
        return None
    y_lo, _y_hi = _merge_basic_info_section_y_bounds(page)
    nd = "受检设备台数"
    d = page.get_text("dict") or {}
    best_line: Optional[dict] = None
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
            if nd not in _merge_line_compact_text(line):
                continue
            if best_y0 is None or cy0 < best_y0:
                best_y0 = cy0
                best_line = line
    if not best_line:
        return None
    spans = best_line.get("spans") or []
    lab = _merge_span_prefix_union_until_needle(best_line, nd)
    if lab is None or lab.is_empty:
        return None
    lx1 = float(lab.x1) + 0.5
    parts: List[fitz.Rect] = []
    for sp in spans:
        bb = sp.get("bbox")
        if not bb or len(bb) < 4:
            continue
        sr = fitz.Rect(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
        if float(sr.x0) >= lx1 - 0.5:
            parts.append(sr)
    if not parts:
        return None
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
            if not item or item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            out.append((float(p0.x), float(p0.y), float(p1.x), float(p1.y)))
    return out


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


def _merge_summary_project_name_value_erase_union(page, label_rect: fitz.Rect) -> fitz.Rect:
    """
    基本情况页「项目名称」取值列：并集标签行右侧及下方直至「受检设备台数」前的所有相关 span，
    覆盖多行旧项目名称，避免擦除不全导致仍显示第一份报告文案。
    """
    if not fitz or label_rect is None or label_rect.is_empty:
        return fitz.Rect(0, 0, 0, 0)
    y_lo, y_hi = _merge_basic_info_section_y_bounds(page)
    y_top = float(label_rect.y0) - 6.0
    if y_hi is not None:
        y_bot = float(y_hi) - 4.0
    else:
        # 未定位到「受检设备台数」行时勿向页下方无限扩张，避免擦到「二、评价」正文
        y_bot = min(float(page.rect.height) * 0.5, float(label_rect.y1) + 88.0)
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
            if "受检设备台数" in tcompact:
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
                if (
                    cy > float(label_rect.y1) + 2.0
                    and cy < y_bot - 4.0
                    and "项目名称" not in tcompact
                    and "受检设备" not in tcompact
                    and float(lb.x0) >= float(label_rect.x0) + 10.0
                ):
                    u = fitz.Rect(lb)
                    merged = u if merged is None else (merged | u)
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
) -> None:
    """
    与 apps.core.htmlpdf_service.build_filled_pdf 一致：ensure_min_text_rect → safe_inset → fit_text_for_box → insert_textbox(overlay=True)。
    合并说明性文字用黑色；不再铺白底。
    paragraph_layout：多段/多行时自框顶排版（保留首行缩进），避免整段垂直居中导致版式像「缩进丢失」。
    preferred_fontsize：优先字号（pt），如四号 14、小四 12；放不进时再略缩小。
    fontsize_floor：与 preferred 同时使用时，缩小字号不低于该值（用于小四等下限）。
    """
    if not fitz or rect is None or rect.is_empty or not (text or "").strip():
        return
    try:
        from apps.core import htmlpdf_service as _hs
    except Exception as exc:
        logger.warning("merge overlay: htmlpdf_service import failed: %s", exc)
        fsu = float(preferred_fontsize or 10.5)
        _fitz_insert_text_line(
            page,
            float(rect.x0) + 0.5,
            _baseline_y_from_line(rect, fsu),
            (text or "")[:400],
            fsu,
            "china-s",
        )
        return
    if not _hs.SIMSUN_FONT.is_file():
        fsu = float(preferred_fontsize or 10.5)
        _fitz_insert_text_line(
            page,
            float(rect.x0) + 0.5,
            _baseline_y_from_line(rect, fsu),
            (text or "")[:400],
            fsu,
            "china-s",
        )
        return
    raw = fitz.Rect(rect)
    rr = _hs.ensure_min_text_rect(raw)
    r = _hs.safe_inset_rect(rr, 0.6)
    try:
        page.insert_font(fontname="F_MERGE_SIM", fontfile=str(_hs.SIMSUN_FONT))
        fo = fitz.Font(fontfile=str(_hs.SIMSUN_FONT))
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
        elif text_h < r.height:
            offset_y = (r.height - text_h) / 2.0
            text_rect = fitz.Rect(r.x0, r.y0 + offset_y, r.x1, r.y1)
        else:
            text_rect = r
        remain = page.insert_textbox(
            text_rect,
            wrapped,
            fontname="F_MERGE_SIM",
            fontsize=fs,
            color=(0, 0, 0),
            align=align,
            overlay=True,
        )
        if remain < 0:
            logger.debug("merge overlay insert_textbox overflow remain=%s", remain)
    except Exception as exc:
        logger.debug("merge overlay insert_textbox fallback: %s", exc)
        fsu = float(preferred_fontsize or 10.5)
        _fitz_insert_text_line(
            page,
            float(r.x0) + 0.5,
            _baseline_y_from_line(r, fsu),
            (text or "")[:400],
            fsu,
            "china-s",
        )


def _merge_page_right_margin(page) -> float:
    pw = float(page.rect.width)
    return max(26.0, min(52.0, pw * 0.055))


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
            if "受检设备台数" in t:
                bbox = _line_bbox_union(line.get("spans") or [])
                if float(bbox.y0) > float(label_rect.y1) - 1.0:
                    y_cap = min(y_cap, float(bbox.y0) - 3.0)
                    break
    y1 = min(y1, y_cap)
    return fitz.Rect(x0, y0, pw - mr, max(y1, y0 + line_fs * 1.35))


def _merge_summary_project_name_cell_rect_robust(
    page, label_rect: fitz.Rect, *, line_fs: float = 10.5
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
    x1 = pw - mr
    y0 = float(label_rect.y0) - 4.0
    if y_lo is not None:
        y0 = max(y0, float(y_lo) + 1.0)
    y1 = float(label_rect.y1) + max(56.0, float(line_fs) * 4.5)
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
    eval_body = (
        f"应委托方要求，依据相关检测标准，对{org}{dev_eval}进行了质量控制检测，结果表明：\n"
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


def apply_merged_report_merge_overlay(doc, header_page_count: int, overlay: Optional[dict]) -> None:
    """
    在已插入的首份前 N 页上叠印（默认 N=3：封面、声明、一、项目基本情况；**版式页均来自合成顺序中
    第一份小报告 PDF 的前 N 页**，再叠合并信息）。

    封面：报告日期、项目名称（受检单位 / 报告名称两行）、受检编号序列表等。
    第 N 页（基本情况）：**项目名称** 改为与封面一致的 **「受检单位名称 + 报告名称」** 单行
    （``project_name_combined``），字号 **小四**；受检设备台数、「二、评价」等。

    与 ``htmlpdf_service.build_filled_pdf`` 对齐：坐标优先取标签右侧 span / 下划线界；透明擦除；overlay 写字。
    若 ``_MERGE_OVERLAY_FIXED_RECT_1BASED_XYWH`` 中配置了与当前物理页一致的框，则 **项目名称 / 台数 / 评价 / 封面报告名称**
    直接使用该矩形叠印（定版坐标）。
    """
    if not fitz or not overlay or not isinstance(overlay, dict):
        return
    nh = int(header_page_count)
    org_pdf_cell = _merge_read_summary_inspected_org_from_rect(doc, nh)

    md = (overlay.get("merge_date_str") or "").strip()
    org = (overlay.get("cover_org_line") or overlay.get("inspected_org") or "").strip()
    crt = (overlay.get("cover_report_title") or "").strip()
    nos = (overlay.get("inspection_index_line") or "").strip()
    pfn_ov = (overlay.get("project_name_combined") or "").strip()
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
    eva = _merge_evaluation_overlay_text(eva)
    if not any([md, org, crt, nos, pfn, dcl, eva]):
        return

    al = int(getattr(fitz, "TEXT_ALIGN_LEFT", 0))
    ac = int(getattr(fitz, "TEXT_ALIGN_CENTER", 1))

    def _apply_cover(pi: int) -> None:
        if pi < 0 or pi >= doc.page_count:
            return
        page = doc[pi]

        r_date = _merge_search_first(page, ["报告日期"])
        if r_date and md:
            fb = _merge_value_rect_same_line(page, r_date)
            vr = _merge_value_span_union_right_of_label(page, r_date)
            dr = _merge_dict_line_value_rect_right_of_label(page, r_date, ("报告日期",))
            band = (
                dr
                if (dr is not None and not dr.is_empty)
                else (vr if (vr is not None and not vr.is_empty) else fb)
            )
            _merge_overlay_erase_transparent(page, band)
            _merge_apply_redactions_overlay(page)
            wrect = _merge_cover_write_rect_above_underline(page, band, r_date)
            _merge_overlay_write_text_html_style(
                page, wrect, md, align=ac, preferred_fontsize=_MERGE_PT_SIHAO
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
        if r_lab and (org or crt):
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
            w_top, w_bot = _merge_cover_project_name_two_line_write_rects(page, band, r_lab)
            if org:
                _merge_overlay_write_text_html_style(
                    page, w_top, org, align=ac, preferred_fontsize=_MERGE_PT_XIAOSI
                )
            if crt:
                frt = _merge_fixed_fitz_rect_for_page_index("cover_report_title", pi)
                w_crt = frt if (frt is not None and not frt.is_empty) else w_bot
                _merge_overlay_write_text_html_style(
                    page, w_crt, crt, align=ac, preferred_fontsize=_MERGE_PT_XIAOSI
                )

    def _apply_summary(pi: int) -> None:
        if pi < 0 or pi >= doc.page_count:
            return
        page = doc[pi]

        rr_pn = _merge_fixed_fitz_rect_for_page_index("summary_project_name", pi)
        rr_dc = _merge_fixed_fitz_rect_for_page_index("summary_device_count", pi)
        rr_ev = _merge_fixed_fitz_rect_for_page_index("summary_evaluation", pi)
        if (
            rr_pn is not None
            and rr_dc is not None
            and rr_ev is not None
            and (pfn or dcl or eva)
        ):
            if pfn:
                _merge_overlay_erase_transparent(page, rr_pn)
                _merge_apply_redactions_overlay(page)
                _merge_overlay_write_text_html_style(
                    page,
                    rr_pn,
                    pfn,
                    align=al,
                    preferred_fontsize=_MERGE_PT_XIAOSI,
                    paragraph_layout=True,
                    fontsize_floor=_MERGE_PT_XIAOSI,
                )
            if dcl:
                _merge_overlay_erase_transparent(page, rr_dc)
                _merge_apply_redactions_overlay(page)
                _merge_overlay_write_text_html_style(
                    page,
                    rr_dc,
                    dcl,
                    align=al,
                    preferred_fontsize=_MERGE_PT_XIAOSI,
                    fontsize_floor=_MERGE_PT_XIAOSI,
                )
            if eva:
                ins = float(_MERGE_EVAL_BOX_H_INSET_PT)
                rr_erase = fitz.Rect(
                    float(rr_ev.x0) + ins,
                    float(rr_ev.y0),
                    float(rr_ev.x1) - ins,
                    float(rr_ev.y1),
                )
                rr_write = fitz.Rect(
                    float(rr_ev.x0) + ins + 0.35,
                    float(rr_ev.y0),
                    float(rr_ev.x1) - ins - 0.35,
                    float(rr_ev.y1),
                )
                _merge_overlay_erase_transparent(page, rr_erase)
                _merge_apply_redactions_overlay(page)
                _merge_overlay_write_text_html_style(
                    page,
                    rr_write,
                    eva,
                    align=al,
                    paragraph_layout=True,
                    preferred_fontsize=_MERGE_PT_XIAOSI,
                    fontsize_floor=_MERGE_PT_XIAOSI,
                )
            return

        _, y_hi_b = _merge_basic_info_section_y_bounds(page)

        r_ev = _merge_search_first(page, ["二、评价", "二.评价", "二．评价"])
        ev_rect: Optional[fitz.Rect] = None
        if r_ev:
            ev_rect = _merge_evaluation_body_rect(page, r_ev)

        r_pn = _merge_basic_info_project_name_label_rect(page)
        if r_pn is None:
            r_pn = _merge_search_last_by_bottom(page, ["项目名称"])

        val_dc = _merge_basic_info_device_count_value_rect(page)
        if val_dc is not None and not val_dc.is_empty and dcl:
            band_dc = fitz.Rect(val_dc)
            band_dc.y1 = max(float(band_dc.y1), float(band_dc.y0) + 11.0)
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
        elif dcl:
            r_dc = _merge_search_last_by_bottom(page, ["受检设备台数"])
            if r_dc is None:
                r_dc = _merge_search_first(page, ["受检设备台数"])
            if r_dc:
                fb = _merge_value_rect_same_line(page, r_dc)
                vr = _merge_value_span_union_right_of_label(page, r_dc)
                dr = _merge_dict_line_value_rect_right_of_label(page, r_dc, ("受检设备台数",))
                band_dc = (
                    dr
                    if (dr is not None and not dr.is_empty)
                    else (vr if (vr is not None and not vr.is_empty) else fb)
                )
                if band_dc is not None and not band_dc.is_empty:
                    band_dc.y1 = max(float(band_dc.y1), float(r_dc.y1) + 8.0)
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

        if r_pn and pfn:
            erase_u = _merge_summary_project_name_value_erase_union(page, r_pn)
            band = fitz.Rect(erase_u)
            lx1 = float(r_pn.x1) + 0.8
            band.x0 = max(float(band.x0), lx1)
            vr = _merge_value_span_union_right_of_label(page, r_pn)
            dr = _merge_dict_line_value_rect_right_of_label(page, r_pn, ("项目名称",))
            pick = dr if (dr is not None and not dr.is_empty) else vr
            if pick is not None and not pick.is_empty and float(pick.x0) >= lx1 - 0.5:
                band.x1 = max(float(band.x1), float(pick.x1))
            band.x0 = max(float(band.x0), lx1)
            geo = _merge_summary_project_name_cell_rect_robust(page, r_pn, line_fs=_MERGE_PT_XIAOSI)
            if geo is not None and not geo.is_empty:
                try:
                    band |= geo
                except Exception:
                    if band.is_empty:
                        band = fitz.Rect(geo)
            if y_hi_b is not None:
                band.y1 = min(float(band.y1), float(y_hi_b) - 3.0)
            if ev_rect is not None and not ev_rect.is_empty:
                band.y1 = min(float(band.y1), float(ev_rect.y0) - 5.0)
            if float(band.y1) > float(band.y0) + 9.0:
                _merge_overlay_erase_transparent(page, band)
                _merge_apply_redactions_overlay(page)
                _merge_overlay_write_text_html_style(
                    page,
                    band,
                    pfn,
                    align=al,
                    preferred_fontsize=_MERGE_PT_XIAOSI,
                    paragraph_layout=True,
                    fontsize_floor=_MERGE_PT_XIAOSI,
                )

        if r_ev and eva:
            if ev_rect is None or ev_rect.is_empty:
                ev_rect = _merge_evaluation_body_rect(page, r_ev)
            if ev_rect is not None and not ev_rect.is_empty:
                ins = float(_MERGE_EVAL_BOX_H_INSET_PT)
                rr_erase = fitz.Rect(
                    float(ev_rect.x0) + ins,
                    float(ev_rect.y0),
                    float(ev_rect.x1) - ins,
                    float(ev_rect.y1),
                )
                rr_write = fitz.Rect(
                    float(ev_rect.x0) + ins + 0.35,
                    float(ev_rect.y0),
                    float(ev_rect.x1) - ins - 0.35,
                    float(ev_rect.y1),
                )
                _merge_overlay_erase_transparent(page, rr_erase)
                _merge_apply_redactions_overlay(page)
                _merge_overlay_write_text_html_style(
                    page,
                    rr_write,
                    eva,
                    align=al,
                    paragraph_layout=True,
                    preferred_fontsize=_MERGE_PT_XIAOSI,
                    fontsize_floor=_MERGE_PT_XIAOSI,
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

            return out.tobytes(deflate=True, garbage=4, clean=True), ""
        finally:
            out.close()
    except Exception as exc:
        logger.exception("merge_report_pdfs_header_toc_sections failed")
        return None, str(exc)
    finally:
        first_doc.close()
