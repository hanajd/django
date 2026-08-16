"""报告第三页电子签字：从个人签名库叠印至 PDF，并与工作流推进联动。"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

import fitz
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core import pipeline_service
from apps.core.models import (
    CaseReportSignatureRecord,
    InspectionCase,
    InspectionCaseWorkflowState,
    InspectionSubmission,
    LibraryFile,
    LibraryProject,
    LibraryProjectWorkflowMember,
    LibraryTask,
    UserSignature,
    UserSignatureEvent,
)
from apps.core.user_signature_service import get_active_user_signature

# 推进目标环节 → 须叠印的报告签字位（授权签发另写签发日期）
ADVANCE_TARGET_SIGN_SLOT: dict[str, str] = {
    InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: CaseReportSignatureRecord.SLOT_REPORT_AUTHOR,
    InspectionCaseWorkflowState.STAGE_REPORT_SIGN: CaseReportSignatureRecord.SLOT_REPORT_AUDITOR,
    InspectionCaseWorkflowState.STAGE_ISSUED: CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
}

SIGN_SLOT_LABELS: dict[str, str] = {
    CaseReportSignatureRecord.SLOT_REPORT_AUTHOR: "编制人",
    CaseReportSignatureRecord.SLOT_REPORT_AUDITOR: "审核人",
    CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY: "授权签字人",
    CaseReportSignatureRecord.SLOT_ISSUE_DATE: "签发日期",
}

# 模板 field id / 中文标签 → 签字位
_FIELD_LABEL_TO_SLOT: dict[str, str] = {
    "编制人": CaseReportSignatureRecord.SLOT_REPORT_AUTHOR,
    "审核人": CaseReportSignatureRecord.SLOT_REPORT_AUDITOR,
    "授权人签字": CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
    "授权签字人": CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
    "签发日期": CaseReportSignatureRecord.SLOT_ISSUE_DATE,
    "年月日": CaseReportSignatureRecord.SLOT_ISSUE_DATE,
}

_DEFAULT_SLOT_PDF_FIELD_IDS: dict[str, str] = {
    CaseReportSignatureRecord.SLOT_REPORT_AUTHOR: "f10",
    CaseReportSignatureRecord.SLOT_REPORT_AUDITOR: "f11",
    CaseReportSignatureRecord.SLOT_ISSUE_DATE: "f12",
    CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY: "f13",
}

# 历史签发快照曾使用的展示名后缀；新快照与当前报告同名，靠路径含 /issued/ 区分
ISSUED_SNAPSHOT_NAME_SUFFIX = "-签发定稿.pdf"
ISSUED_SNAPSHOT_PATH_MARKER = "/issued/"


def is_issued_report_snapshot_name(name: str) -> bool:
    n = (name or "").strip()
    return n.endswith(ISSUED_SNAPSHOT_NAME_SUFFIX)


def is_issued_report_snapshot_file(lf: LibraryFile | None) -> bool:
    """是否为签发定稿快照（路径含 issued 段，或历史「-签发定稿」后缀）。"""
    if lf is None:
        return False
    rel = (getattr(lf, "relative_path", None) or "").replace("\\", "/")
    if "issued" in [p for p in rel.split("/") if p]:
        return True
    return is_issued_report_snapshot_name(getattr(lf, "original_name", None) or "")


def issued_snapshot_display_name(current_name: str, task_no: str = "") -> str:
    """签发定稿展示名与当前报告一致（不再加「-签发定稿」后缀）。"""
    return stable_current_report_display_name(current_name, task_no=task_no)


def stable_current_report_display_name(current_name: str, task_no: str = "") -> str:
    """去掉历史 signed_v / 签发定稿后缀，得到稳定的当前报告展示名。"""
    name = (current_name or "").strip()
    if not name:
        tn = (task_no or "").replace("/", "_").strip() or "report"
        return f"{tn}-报告.pdf"
    name = re.sub(r"__signed_v\d+", "", name, flags=re.IGNORECASE)
    if name.endswith(ISSUED_SNAPSHOT_NAME_SUFFIX):
        name = name[: -len(ISSUED_SNAPSHOT_NAME_SUFFIX)] + ".pdf"
    name = re.sub(r"-签发定稿(?=\.pdf$)", "", name)
    if not name.lower().endswith(".pdf"):
        tn = (task_no or "").replace("/", "_").strip() or "report"
        return f"{tn}-报告.pdf"
    return name


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _format_issue_date(d: date | None = None) -> str:
    d = d or timezone.localdate()
    return f"{d.year:04d}年{d.month:02d}月{d.day:02d}日"


def _issue_date_digit_parts(issue_day: date) -> tuple[str, str, str]:
    """年/月/日三格仅写数字（模板上已印「年」「月」「日」字）。"""
    return (
        f"{issue_day.year:04d}",
        f"{issue_day.month:02d}",
        f"{issue_day.day:02d}",
    )


def _find_printed_ymd_label_rects(
    page: fitz.Page,
    hint_rect: list[float] | tuple[float, ...] | None = None,
) -> list[fitz.Rect] | None:
    """
    在签发日期附近定位模板已印的「年」「月」「日」三字。
    返回按 x 排序的三个字框；找不到则 None。
    """
    clip = None
    if hint_rect is not None and len(hint_rect) >= 4:
        x0, y0, x1, y1 = [float(hint_rect[i]) for i in range(4)]
        # 向左多扩一点以覆盖「签发日期」与数字空白，上下略扩
        clip = fitz.Rect(min(x0, x1) - 100.0, min(y0, y1) - 12.0, max(x0, x1) + 30.0, max(y0, y1) + 12.0)

    found: dict[str, list[fitz.Rect]] = {"年": [], "月": [], "日": []}
    for ch in ("年", "月", "日"):
        try:
            hits = page.search_for(ch, clip=clip) if clip is not None else page.search_for(ch)
        except Exception:
            hits = []
        for h in hits or []:
            found[ch].append(fitz.Rect(h))

    # 「日」可能命中「签发日期」里的「日」；优先取最靠右且靠近 hint 的三个字
    def _pick(ch: str) -> fitz.Rect | None:
        cands = list(found.get(ch) or [])
        if not cands:
            return None
        if hint_rect is not None and len(hint_rect) >= 4:
            hx0, hy0, hx1, hy1 = [float(hint_rect[i]) for i in range(4)]
            hcy = (hy0 + hy1) / 2.0
            # 同行、落在 hint 右半区附近
            same_line = [r for r in cands if abs(((r.y0 + r.y1) / 2.0) - hcy) < 14.0]
            pool = same_line or cands
            # 「日」排除「签发日期」里的「日」：取同行最右侧
            if ch == "日":
                rightish = [r for r in pool if r.x0 >= (hx0 + 40.0)]
                pool = rightish or pool
                pool.sort(key=lambda r: (-r.x0, abs(((r.y0 + r.y1) / 2.0) - hcy)))
                return pool[0]
            pool.sort(key=lambda r: (abs(((r.y0 + r.y1) / 2.0) - hcy), r.x0))
            return pool[0]
        cands.sort(key=lambda r: (r.y0, r.x0))
        return cands[0]

    year_r = _pick("年")
    month_r = _pick("月")
    day_r = _pick("日")
    if not (year_r and month_r and day_r):
        return None
    ordered = sorted([year_r, month_r, day_r], key=lambda r: r.x0)
    # 必须是 年 < 月 < 日
    if not (ordered[0] == year_r and ordered[1] == month_r and ordered[2] == day_r):
        # 容错：若搜索错位，仍按几何 左→右 当作年/月/日
        pass
    return [year_r, month_r, day_r]


def _digit_blank_rects_before_ymd_labels(
    label_rects: list[fitz.Rect],
    *,
    hint_rect: list[float] | tuple[float, ...] | None = None,
) -> list[list[float]]:
    """
    根据已印「年/月/日」字框，推算其前方数字空白框 [x0,y0,x1,y1]。
    布局通常为： [yyyy]年[mm]月[dd]日

    竖直方向必须与「年/月/日」同一水平带；若用模板 hint 的高框，
    insert_textbox 垂直居中会把数字顶到汉字上方。
    """
    year_r, month_r, day_r = label_rects
    hy0 = min(float(year_r.y0), float(month_r.y0), float(day_r.y0))
    hy1 = max(float(year_r.y1), float(month_r.y1), float(day_r.y1))
    # 年前空白：从「年」左回溯，避免把「签发日期」标签算进数字区
    y_left = float(year_r.x0) - 36.0
    if hint_rect is not None and len(hint_rect) >= 4:
        # 若 hint 左缘更靠右（已是数字区），用之；否则仍按年字回溯
        hx0 = float(hint_rect[0])
        if hx0 > y_left:
            y_left = hx0

    gap = 1.5
    y_box = [y_left, hy0, float(year_r.x0) - gap, hy1]
    m_box = [float(year_r.x1) + gap, hy0, float(month_r.x0) - gap, hy1]
    d_box = [float(month_r.x1) + gap, hy0, float(day_r.x0) - gap, hy1]

    out: list[list[float]] = []
    for box, min_w in ((y_box, 22.0), (m_box, 12.0), (d_box, 12.0)):
        if box[2] - box[0] < min_w:
            box[0] = box[2] - min_w
        out.append(box)
    return out


def _snap_digit_rects_y_to_ymd_labels(
    digit_rects: list[list[float]],
    label_rects: list[fitz.Rect] | None,
) -> list[list[float]]:
    """把拆分格/模板数字框的 y 对齐到预印「年/月/日」，避免 hint 高框把数字顶偏。"""
    if not label_rects or len(label_rects) < 3:
        return digit_rects
    hy0 = min(float(r.y0) for r in label_rects[:3])
    hy1 = max(float(r.y1) for r in label_rects[:3])
    out: list[list[float]] = []
    for dr in digit_rects:
        if len(dr) < 4:
            out.append(dr)
            continue
        box = list(dr)
        box[1], box[3] = hy0, hy1
        out.append(box)
    return out


def _resolve_times_font_path() -> Path | None:
    """叠印数字/字母用 Times New Roman（与 htmlpdf 填字一致）。"""
    try:
        from apps.core.htmlpdf_service import pick_times_font

        p = pick_times_font()
        if p is not None and p.is_file():
            return p
    except Exception:
        pass
    root = Path(__file__).resolve().parent.parent
    for rel in (
        "fonts/TIMES.TTF",
        "fonts/times.ttf",
        "fonts/Times New Roman.ttf",
        "htmlpdf/fonts/TIMES.TTF",
        "htmlpdf/fonts/times.ttf",
    ):
        p = root / rel
        if p.is_file():
            return p
    for p in (
        Path("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf"),
        Path("/usr/share/fonts/truetype/msttcorefonts/times.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
    ):
        if p.is_file():
            return p
    return None


def _ensure_page_times_font(page: fitz.Page, fontname: str = "F_OVERLAY_TIMES") -> tuple[str, Any]:
    """在页面嵌入 Times；返回 (fontname, fitz.Font|None)。失败时 fontname 为空串。"""
    path = _resolve_times_font_path()
    if path is None:
        return "", None
    try:
        page.insert_font(fontname=fontname, fontfile=str(path))
    except Exception:
        try:
            fo = fitz.Font(fontfile=str(path))
            page.insert_font(fontname=fontname, fontbuffer=fo.buffer)
        except Exception:
            return "", None
    try:
        fo = fitz.Font(fontfile=str(path))
    except Exception:
        fo = None
    return fontname, fo


def _insert_issue_date_digits(
    page: fitz.Page,
    digit_rects: list[list[float]],
    texts: tuple[str, str, str],
    *,
    label_fontsize: float | None = None,
) -> None:
    """
    在空白框写入年月日数字。

    数字/字母用 Times New Roman；竖直方向按预印汉字行框基线对齐，
    避免 insert_textbox 在 hint 高框内垂直居中把数字顶到汉字上方。
    """
    fontname, fo = _ensure_page_times_font(page)
    for rect, text in zip(digit_rects, texts):
        if len(rect) < 4 or not text:
            continue
        r = fitz.Rect(*rect)
        if r.width < 4 or r.height < 4:
            continue
        target = float(label_fontsize) if label_fontsize and label_fontsize > 0 else float(r.height)
        fs = min(10.5, max(8.0, target * 0.78))

        def _text_w(fontsize: float) -> float:
            if fo is not None:
                try:
                    return float(fo.text_length(text, fontsize=fontsize))
                except Exception:
                    pass
            # Times 数字约 0.5em
            return fontsize * len(text) * 0.5

        tw = _text_w(fs)
        while tw > float(r.width) - 1.5 and fs > 8.0:
            fs -= 0.5
            tw = _text_w(fs)
        # SimSun「年/月/日」字框底边≈基线；Times 数字 descent 较小，基线略上提
        baseline = float(r.y1) - max(1.0, fs * 0.18)
        x = float(r.x1) - tw - 1.2
        if x < float(r.x0):
            x = float(r.x0)
        kwargs: dict[str, Any] = {
            "fontsize": fs,
            "color": (0, 0, 0),
            "overlay": True,
        }
        if fontname:
            kwargs["fontname"] = fontname
        page.insert_text(fitz.Point(x, baseline), text, **kwargs)


def _clip_rects_above_underline(
    page: fitz.Page,
    digit_rects: list[list[float]],
    hint_rect: list[float] | None = None,
    *,
    gap: float = 1.0,
) -> list[list[float]]:
    """将数字空白框底边收到下划线上方，避免白底擦断模板线。不抬高顶边。"""
    guide = hint_rect
    if guide is None or len(guide) < 4:
        xs0, ys0, xs1, ys1 = [], [], [], []
        for dr in digit_rects:
            if len(dr) >= 4:
                xs0.append(float(dr[0]))
                ys0.append(float(dr[1]))
                xs1.append(float(dr[2]))
                ys1.append(float(dr[3]))
        if not xs0:
            return digit_rects
        guide = [min(xs0), min(ys0), max(xs1), max(ys1)]
    ul = _find_field_underline(page, guide)
    if ul is None:
        return digit_rects
    _x0, _x1, uy = ul[0], ul[1], ul[2]
    out: list[list[float]] = []
    for dr in digit_rects:
        if len(dr) < 4:
            out.append(dr)
            continue
        box = list(dr)
        if box[3] > uy - gap:
            box[3] = uy - gap
        # 过扁时宁可略降底边也不抬顶，避免数字相对「年/月/日」上浮
        if box[3] - box[1] < 4:
            box[3] = min(float(uy) - gap, box[1] + 8.0)
        out.append(box)
    return out


def _overlay_issue_date_on_pdf(
    pdf_bytes: bytes,
    *,
    slots: dict[str, dict[str, Any]],
    issue_day: date,
) -> tuple[bytes, int, list[float]]:
    """
    叠印签发日期：在模板已有「年 / 月 / 日」基础上只填数字。

    优先写拆分格（签发日期1/2/3、日期年/月/日）；
    否则对单一「年月日/签发日期」框，按 PDF 文本层定位「年」「月」「日」并在其前写入数字。
    """
    from utils.pdf_compress import pdf_document_to_compressed_bytes

    texts = _issue_date_digit_parts(issue_day)
    parts = slots.get("_issueDateParts")
    if isinstance(parts, list) and len(parts) >= 3:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            digit_rects: list[list[float]] = []
            page_1based = 3
            for part in parts[:3]:
                page_1based = int(part.get("page") or page_1based or 3)
                digit_rects.append(list(part.get("rect") or []))
            page_idx = max(0, page_1based - 1)
            issue_spec = slots.get(CaseReportSignatureRecord.SLOT_ISSUE_DATE) or parts[0]
            hint = list(issue_spec.get("rect") or parts[0].get("rect") or [])
            if page_idx < doc.page_count:
                page = doc[page_idx]
                underline = _find_field_underline(page, hint) if hint else None
                label_rects = _find_printed_ymd_label_rects(page, hint) if hint else None
                label_fs = None
                if label_rects:
                    label_fs = max(float(r.y1 - r.y0) for r in label_rects[:3])
                    digit_rects = _snap_digit_rects_y_to_ymd_labels(digit_rects, label_rects)
                digit_rects = _clip_rects_above_underline(page, digit_rects, hint)
                for dr in digit_rects:
                    if len(dr) >= 4:
                        page.draw_rect(
                            fitz.Rect(*dr), color=(1, 1, 1), fill=(1, 1, 1), overlay=True
                        )
                _insert_issue_date_digits(
                    page, digit_rects, texts, label_fontsize=label_fs
                )
                _redraw_field_underline(page, underline)
            return (
                pdf_document_to_compressed_bytes(doc, subset_fonts=False),
                int(issue_spec.get("page") or parts[0].get("page") or 3),
                list(issue_spec.get("rect") or parts[0].get("rect") or []),
            )
        finally:
            doc.close()

    issue_spec = slots.get(CaseReportSignatureRecord.SLOT_ISSUE_DATE)
    if not issue_spec:
        raise ValueError("报告模板未配置签发日期栏位")
    rect = list(issue_spec.get("rect") or [])
    page_1based = int(issue_spec.get("page") or 3)
    if len(rect) < 4:
        raise ValueError("签发日期坐标无效")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_idx = max(0, page_1based - 1)
        if page_idx >= doc.page_count:
            raise ValueError(f"签发日期页码 {page_1based} 超出报告页数")
        page = doc[page_idx]
        underline = _find_field_underline(page, rect)
        label_rects = _find_printed_ymd_label_rects(page, rect)
        label_fs = None
        if label_rects:
            label_fs = max(float(r.y1 - r.y0) for r in label_rects[:3])
            digit_rects = _digit_blank_rects_before_ymd_labels(label_rects, hint_rect=rect)
            digit_rects = _clip_rects_above_underline(page, digit_rects, rect)
            # 重签时先擦除数字空白，避免叠字（不擦下划线）
            for dr in digit_rects:
                if len(dr) >= 4:
                    page.draw_rect(fitz.Rect(*dr), color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
            _insert_issue_date_digits(
                page, digit_rects, texts, label_fontsize=label_fs
            )
        else:
            # 找不到预印字时：将栏位三等分，仍只写数字（不写「年月日」文字）
            content = _content_rect_above_underline(page, rect)
            x0, y0, x1, y1 = content.x0, content.y0, content.x1, content.y1
            w = (x1 - x0) / 3.0
            digit_rects = [
                [x0, y0, x0 + w, y1],
                [x0 + w, y0, x0 + 2 * w, y1],
                [x0 + 2 * w, y0, x1, y1],
            ]
            for dr in digit_rects:
                page.draw_rect(fitz.Rect(*dr), color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
            _insert_issue_date_digits(page, digit_rects, texts)
        _redraw_field_underline(page, underline)
        return (
            pdf_document_to_compressed_bytes(doc, subset_fonts=False),
            page_1based,
            rect,
        )
    finally:
        doc.close()


def resolve_report_signature_slots(template_parsed: dict | None) -> dict[str, dict[str, Any]]:
    """
    解析报告模板签字位坐标。
    返回 slot -> {page, rect: [x0,y0,x1,y1], pdfFieldId, label}

    优先级：模板显式 reportSignatureSlots → 中文标签（编制人/审核人/…）
    → 第三页「下划线/年月日」布局推断 → 仅当默认 f 号标签确实对应签字位时才用。
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(template_parsed, dict):
        return out

    configured = template_parsed.get("reportSignatureSlots")
    if isinstance(configured, dict):
        pdf_fields = _index_pdf_fields(template_parsed)
        for slot, spec in configured.items():
            if not isinstance(spec, dict):
                continue
            slot_key = str(slot or "").strip()
            if not slot_key:
                continue
            fid = str(spec.get("pdfFieldId") or "").strip()
            field = pdf_fields.get(fid) if fid else None
            if field is None and spec.get("rect"):
                rect_raw = spec.get("rect")
                if isinstance(rect_raw, (list, tuple)) and len(rect_raw) >= 5:
                    page = int(rect_raw[0])
                    x0, y0, w, h = [float(rect_raw[i]) for i in range(1, 5)]
                    out[slot_key] = {
                        "page": page,
                        "rect": [x0, y0, x0 + w, y0 + h],
                        "pdfFieldId": fid,
                        "label": SIGN_SLOT_LABELS.get(slot_key, slot_key),
                    }
                    continue
            if field:
                out[slot_key] = field

    pdf_fields = _index_pdf_fields(template_parsed)
    slot_order = (
        CaseReportSignatureRecord.SLOT_REPORT_AUTHOR,
        CaseReportSignatureRecord.SLOT_REPORT_AUDITOR,
        CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
        CaseReportSignatureRecord.SLOT_ISSUE_DATE,
    )

    # 1) 已按标签索引的 slot（编制人/审核人/授权签字人/签发日期/年月日）
    for slot in slot_order:
        if slot in out:
            continue
        if slot in pdf_fields:
            out[slot] = pdf_fields[slot]
            continue
        for label, mapped_slot in _FIELD_LABEL_TO_SLOT.items():
            if mapped_slot != slot:
                continue
            if label in pdf_fields:
                out[slot] = pdf_fields[label]
                break

    # 2) 第三页常见「下划线 / 下划线2 / 下划线3 / 年月日」四框布局
    inferred = _infer_slots_from_underline_layout(pdf_fields)
    for slot, spec in inferred.items():
        out.setdefault(slot, spec)

    # 3) 拆分签发日期（年/月/日三格）→ 供叠印时分别写入
    prefer_page = None
    for slot in (
        CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
        CaseReportSignatureRecord.SLOT_REPORT_AUTHOR,
        CaseReportSignatureRecord.SLOT_REPORT_AUDITOR,
    ):
        if slot in out:
            prefer_page = int(out[slot].get("page") or 3)
            break
    date_parts = _resolve_split_issue_date_parts(
        pdf_fields,
        template_parsed=template_parsed,
        prefer_page=prefer_page,
    )
    if date_parts:
        out["_issueDateParts"] = date_parts  # type: ignore[assignment]
        if CaseReportSignatureRecord.SLOT_ISSUE_DATE not in out:
            # 用三格并集作为逻辑签发位，便于「已配置」判断
            xs0 = min(p["rect"][0] for p in date_parts)
            ys0 = min(p["rect"][1] for p in date_parts)
            xs1 = max(p["rect"][2] for p in date_parts)
            ys1 = max(p["rect"][3] for p in date_parts)
            out[CaseReportSignatureRecord.SLOT_ISSUE_DATE] = {
                "page": int(date_parts[0]["page"]),
                "rect": [xs0, ys0, xs1, ys1],
                "pdfFieldId": "",
                "label": "签发日期",
            }

    # 4) 默认 f 号：仅当该 f 的标签确实属于目标签字位时才采纳（避免 f10=设备型号 等误伤）
    for slot, default_fid in _DEFAULT_SLOT_PDF_FIELD_IDS.items():
        if slot in out:
            continue
        entry = pdf_fields.get(default_fid)
        if not entry:
            continue
        label = str(entry.get("label") or "").strip()
        if _FIELD_LABEL_TO_SLOT.get(label) == slot:
            out[slot] = entry

    # 不把内部辅助键当成正式 slot 返回给调用方以外的逻辑；保留在 dict 内供叠印使用
    return out


def _infer_slots_from_underline_layout(
    pdf_fields: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    部分报告第三页底部四框仍标为「下划线/下划线2/下划线3/年月日」：
    左上编制人、右上审核人、左下授权签字人、右下签发日期。
    """
    keys = ("下划线", "下划线2", "下划线3", "年月日")
    boxes = []
    for key in keys:
        entry = pdf_fields.get(key)
        if not entry:
            return {}
        boxes.append(entry)
    pages = {int(b.get("page") or 0) for b in boxes}
    if len(pages) != 1:
        return {}
    # 按 y 分行，再按 x 分左右
    sorted_by_y = sorted(boxes, key=lambda b: (b["rect"][1], b["rect"][0]))
    top = sorted_by_y[:2]
    bottom = sorted_by_y[2:]
    if len(top) != 2 or len(bottom) != 2:
        return {}
    top_l, top_r = sorted(top, key=lambda b: b["rect"][0])
    bot_l, bot_r = sorted(bottom, key=lambda b: b["rect"][0])
    return {
        CaseReportSignatureRecord.SLOT_REPORT_AUTHOR: {
            **top_l,
            "label": "编制人",
        },
        CaseReportSignatureRecord.SLOT_REPORT_AUDITOR: {
            **top_r,
            "label": "审核人",
        },
        CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY: {
            **bot_l,
            "label": "授权签字人",
        },
        CaseReportSignatureRecord.SLOT_ISSUE_DATE: {
            **bot_r,
            "label": "签发日期",
        },
    }


def _resolve_split_issue_date_parts(
    pdf_fields: dict[str, dict[str, Any]],
    *,
    template_parsed: dict | None = None,
    prefer_page: int | None = None,
) -> list[dict[str, Any]]:
    """签发日期1/2/3、日期1/2/3 或 日期年/月/日（年/月/日）拆分格。"""
    label_groups = (
        ("签发日期1", "签发日期2", "签发日期3"),
        ("日期1", "日期2", "日期3"),
        ("日期年", "日期月", "日期日"),
        ("年", "月", "日"),
    )

    def _from_index(labels: tuple[str, ...]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for label in labels:
            entry = pdf_fields.get(label)
            if not entry:
                return []
            parts.append(entry)
        return parts

    # 优先：同页且靠近签字区（prefer_page 或第 3 页）的一组
    if isinstance(template_parsed, dict):
        want_page = int(prefer_page or 3)
        for labels in label_groups:
            by_label: dict[str, list[dict[str, Any]]] = {lb: [] for lb in labels}
            for f in _iter_template_field_dicts(template_parsed):
                label = _signature_label_from_field(f)
                title = str(f.get("title") or f.get("label") or "").strip()
                name = title if title in by_label else (label if label in by_label else "")
                if not name:
                    continue
                geom = _geometry_from_template_field(f)
                if geom is None:
                    continue
                page, rect = geom
                by_label[name].append(
                    {
                        "page": page,
                        "rect": list(rect),
                        "pdfFieldId": str(f.get("pdfFieldId") or "").strip(),
                        "label": name,
                    }
                )
            if not all(by_label[lb] for lb in labels):
                continue
            # 在 prefer_page 上各取 y 最大的一格（靠近页底签字区）
            page_parts: list[dict[str, Any]] = []
            for lb in labels:
                cands = [e for e in by_label[lb] if int(e["page"]) == want_page]
                if not cands:
                    cands = by_label[lb]
                cands.sort(key=lambda e: (e["rect"][1], e["rect"][0]), reverse=True)
                page_parts.append(cands[0])
            pages = {int(p["page"]) for p in page_parts}
            if len(pages) == 1:
                return page_parts

    for labels in label_groups:
        parts = _from_index(labels)
        if len(parts) == 3:
            return parts
    return []


def _geometry_from_template_field(f: dict) -> tuple[int, list[float]] | None:
    """
    从模板栏位取出 (page, [x0,y0,x1,y1])。

    兼容：
    - ``rect: [page, x, y, w, h]``（pdf.fields / HTMLPDF）
    - ``page + x/y/w/h``（统一模板根级 fields，报告第三页签字框多为该格式）
    - ``pdfAnchor: {page, rect:[x0,y0,x1,y1]}``（steps / form_schema）
    """
    if not isinstance(f, dict):
        return None
    rect_raw = f.get("rect")
    if isinstance(rect_raw, (list, tuple)) and len(rect_raw) >= 5:
        try:
            page = int(rect_raw[0])
            x0, y0, w, h = [float(rect_raw[i]) for i in range(1, 5)]
            return page, [x0, y0, x0 + w, y0 + h]
        except (TypeError, ValueError):
            pass
    if f.get("page") is not None and f.get("x") is not None and f.get("y") is not None:
        try:
            page = int(f.get("page") or 0)
            x0 = float(f.get("x") or 0)
            y0 = float(f.get("y") or 0)
            w = float(f.get("w") or f.get("width") or 0)
            h = float(f.get("h") or f.get("height") or 0)
            if page > 0 and w > 0 and h > 0:
                return page, [x0, y0, x0 + w, y0 + h]
        except (TypeError, ValueError):
            pass
    anchor = f.get("pdfAnchor")
    if isinstance(anchor, dict):
        a_rect = anchor.get("rect")
        try:
            page = int(anchor.get("page") or f.get("page") or 0)
            if isinstance(a_rect, (list, tuple)) and len(a_rect) >= 4:
                x0, y0, x1, y1 = [float(a_rect[i]) for i in range(4)]
                # 兼容误写成 [page,x,y,w,h] 的锚点
                if len(a_rect) >= 5 and x1 < 50 and float(a_rect[3]) > 0:
                    page = int(a_rect[0])
                    x0, y0, w, h = [float(a_rect[i]) for i in range(1, 5)]
                    return page, [x0, y0, x0 + w, y0 + h]
                if page > 0:
                    return page, [x0, y0, x1, y1]
        except (TypeError, ValueError):
            pass
    return None


def _signature_label_from_field(f: dict) -> str:
    for key in ("id", "placeholder", "title", "label"):
        raw = str(f.get(key) or "").strip()
        if raw:
            return raw
    return ""


def _put_signature_field_entry(
    out: dict[str, dict[str, Any]],
    *,
    label: str,
    page: int,
    rect_xyxy: list[float],
    fid: str,
) -> None:
    if not label:
        return
    entry = {
        "page": page,
        "rect": list(rect_xyxy),
        "pdfFieldId": fid,
        "label": label,
    }
    out[label] = entry
    slot = _FIELD_LABEL_TO_SLOT.get(label)
    if slot:
        # 勿让「年月日」等弱标签覆盖已解析到的「签发日期」
        if slot not in out or label in {"编制人", "审核人", "授权签字人", "授权人签字", "签发日期"}:
            out[slot] = entry
    if fid:
        out[fid] = entry


def _iter_template_field_dicts(template_parsed: dict) -> list[dict]:
    """收集可能含签字坐标的栏位：pdf.fields、根 fields、steps/form_schema 嵌套栏。"""
    collected: list[dict] = []
    seen: set[int] = set()

    def _add(f: dict) -> None:
        i = id(f)
        if i in seen:
            return
        seen.add(i)
        collected.append(f)

    pdf = template_parsed.get("pdf")
    if isinstance(pdf, dict) and isinstance(pdf.get("fields"), list):
        for f in pdf["fields"]:
            if isinstance(f, dict):
                _add(f)
    root_fields = template_parsed.get("fields")
    if isinstance(root_fields, list):
        for f in root_fields:
            if isinstance(f, dict):
                _add(f)

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            # 带几何的栏位节点
            if _geometry_from_template_field(obj) is not None and _signature_label_from_field(obj):
                _add(obj)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    for key in ("steps", "form_schema"):
        if key in template_parsed:
            _walk(template_parsed.get(key))
    return collected


def _index_pdf_fields(template_parsed: dict) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in _iter_template_field_dicts(template_parsed):
        label = _signature_label_from_field(f)
        geom = _geometry_from_template_field(f)
        if not label or geom is None:
            continue
        page, rect_xyxy = geom
        fid = str(f.get("pdfFieldId") or "").strip()
        # steps 里 id 常为 f9，label 才是「编制人」——优先用中文标签键
        label_for_map = label
        title = str(f.get("title") or f.get("label") or "").strip()
        if title and _FIELD_LABEL_TO_SLOT.get(title):
            label_for_map = title
        elif _FIELD_LABEL_TO_SLOT.get(label):
            label_for_map = label
        elif title:
            label_for_map = title
        _put_signature_field_entry(
            out,
            label=label_for_map,
            page=page,
            rect_xyxy=rect_xyxy,
            fid=fid,
        )
        # 同时保留原始 id（如 f9）便于默认 f 号回退
        raw_id = str(f.get("id") or "").strip()
        if raw_id and raw_id != label_for_map and raw_id not in out:
            out[raw_id] = {
                "page": page,
                "rect": list(rect_xyxy),
                "pdfFieldId": fid or raw_id,
                "label": label_for_map,
            }
    return out


def _is_exported_report_pdf_filename(name: str) -> bool:
    """识别导出报告文件名（含全本 / 无防护结果 / 仅防护结果后缀）。"""
    n = (name or "").strip()
    if not n.lower().endswith(".pdf"):
        return False
    # 变体：…_报告（无防护结果）.pdf / …_报告（仅防护结果）.pdf
    if (
        "报告（无防护结果）" in n
        or "报告（仅防护结果）" in n
        or "报告(无防护结果)" in n
        or "报告(仅防护结果)" in n
    ):
        return True
    return n.endswith("-报告.pdf") or n.endswith("_报告.pdf")


def find_latest_report_library_file(
    *,
    case: InspectionCase,
    project: LibraryProject,
    task_no: str = "",
    include_issued_snapshot: bool = False,
    export_variant: str | None = None,
) -> LibraryFile | None:
    """取该案件在本项目下最新「当前报告」PDF。

    默认排除「签发定稿」快照；有 ``task_no`` 时优先按文件名匹配任务号。
    ``export_variant`` 指定时只匹配全本 / 无防护结果 / 仅防护结果之一。
    """
    from radiation_detection_report.report_pdf_integrator import (
        REPORT_EXPORT_VARIANT_FULL,
        normalize_report_export_variants,
        report_export_variant_from_filename,
    )

    variant = None
    if export_variant is not None and str(export_variant).strip():
        variant = normalize_report_export_variants([export_variant])[0]

    base = (
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_REPORT,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            projects=project,
            deleted_at__isnull=True,
        )
        .order_by("-created_at", "-id")
    )
    candidates = list(base[:80])
    if not include_issued_snapshot:
        candidates = [f for f in candidates if not is_issued_report_snapshot_file(f)]
    tn = (task_no or "").strip()
    if tn:
        safe = tn.replace("/", "_")
        matched = [
            f
            for f in candidates
            if safe in (f.original_name or "")
            or _is_exported_report_pdf_filename(f.original_name or "")
            or "__task__" in (f.original_name or "")
        ]
        pool = matched or candidates
    else:
        pool = candidates
    if variant:
        pool = [
            f
            for f in pool
            if report_export_variant_from_filename(f.original_name or "") == variant
        ]
    preferred = _prefer_stable_report_file(pool[:20])
    if preferred is not None:
        return preferred
    if variant == REPORT_EXPORT_VARIANT_FULL and not tn:
        return _prefer_stable_report_file(candidates[:20])
    return None


def list_task_report_export_variants(
    *,
    case: InspectionCase,
    project: LibraryProject,
    task_no: str = "",
) -> list[str]:
    """当前任务仍存在的报告导出版本（按 全本→无防护→仅防护 排序）。"""
    from radiation_detection_report.report_pdf_integrator import REPORT_EXPORT_VARIANTS

    out: list[str] = []
    for v in REPORT_EXPORT_VARIANTS:
        if find_latest_report_library_file(
            case=case, project=project, task_no=task_no, export_variant=v
        ):
            out.append(v)
    return out


def normalize_signature_export_variant(export_variant: str | None) -> str:
    from radiation_detection_report.report_pdf_integrator import (
        REPORT_EXPORT_VARIANT_FULL,
        normalize_report_export_variants,
    )

    raw = (export_variant or "").strip()
    if not raw:
        return REPORT_EXPORT_VARIANT_FULL
    return normalize_report_export_variants([raw])[0]


def _prefer_stable_report_file(files: list[LibraryFile]) -> LibraryFile | None:
    """同批候选中优先非 signed_v / 非签发定稿的展示名。"""
    if not files:
        return None
    stable = [
        f
        for f in files
        if "__signed_v" not in (f.original_name or "")
        and not is_issued_report_snapshot_file(f)
    ]
    return (stable or files)[0]


def resolve_report_library_task(
    *,
    project: LibraryProject,
    submission: InspectionSubmission,
    report_file: LibraryFile | None = None,
) -> LibraryTask | None:
    if report_file is not None:
        rt = (
            report_file.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
            .order_by("code", "id")
            .first()
        )
        if rt:
            return rt
    from apps.api.inspection_pdf_service import _resolve_library_task_for_task_no

    site_task = _resolve_library_task_for_task_no(submission.task_no, project)
    if site_task is not None and site_task.output_target == LibraryTask.OUTPUT_REPORT:
        return site_task
    if site_task is not None:
        rt = (
            project.library_tasks.filter(
                output_target=LibraryTask.OUTPUT_REPORT,
                report_source_tasks=site_task,
            )
            .order_by("code", "id")
            .first()
        )
        if rt:
            return rt
    return (
        project.library_tasks.filter(output_target=LibraryTask.OUTPUT_REPORT)
        .order_by("code", "id")
        .first()
    )


def load_report_template_parsed(report_task: LibraryTask | None) -> dict | None:
    from apps.api.inspection_report_make import _load_first_parsed_template_json_for_task

    return _load_first_parsed_template_json_for_task(report_task)


def list_case_report_signature_records(
    *,
    case_id: int,
    task_no: str = "",
    include_voided: bool = False,
    export_variant: str | None = None,
) -> list[CaseReportSignatureRecord]:
    qs = CaseReportSignatureRecord.objects.filter(case_id=case_id).select_related(
        "signer", "user_signature", "report_file"
    )
    if not include_voided:
        qs = qs.filter(voided_at__isnull=True)
    tn = (task_no or "").strip()
    if tn:
        qs = qs.filter(task_no=tn)
    if export_variant is not None and str(export_variant).strip():
        qs = qs.filter(export_variant=normalize_signature_export_variant(export_variant))
    return list(qs.order_by("sign_version", "signed_at", "id"))


def slot_already_signed(
    *,
    case_id: int,
    task_no: str,
    slot: str,
    export_variant: str | None = None,
) -> bool:
    qs = CaseReportSignatureRecord.objects.filter(
        case_id=case_id,
        task_no=(task_no or "").strip(),
        slot=slot,
        voided_at__isnull=True,
    )
    if export_variant is not None and str(export_variant).strip():
        qs = qs.filter(export_variant=normalize_signature_export_variant(export_variant))
    return qs.exists()


def slot_signed_on_all_variants(
    *,
    case: InspectionCase,
    project: LibraryProject,
    task_no: str,
    slot: str,
    variants: list[str] | None = None,
) -> bool:
    """指定签字位是否已在当前存在的全部报告版本上落章。"""
    vs = list(variants or [])
    if not vs:
        vs = list_task_report_export_variants(
            case=case, project=project, task_no=task_no
        )
    if not vs:
        return False
    return all(
        slot_already_signed(
            case_id=case.pk, task_no=task_no, slot=slot, export_variant=v
        )
        for v in vs
    )


def variant_report_stage_from_signed_slots(
    signed_slots: set[str] | frozenset[str] | list[str] | None,
    *,
    case_stage: str = "",
) -> str:
    """
    单个报告版本的有效环节（与其它导出版本独立）。

    签字链：编制人 → 审核人 → 授权签字人；未进入报告签字链时沿用案件环节。
    """
    from apps.core.project_workflow_ui import workflow_stage_index

    slots = {str(s) for s in (signed_slots or []) if s}
    if CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY in slots:
        return InspectionCaseWorkflowState.STAGE_ISSUED
    if CaseReportSignatureRecord.SLOT_REPORT_AUDITOR in slots:
        return InspectionCaseWorkflowState.STAGE_REPORT_SIGN
    if CaseReportSignatureRecord.SLOT_REPORT_AUTHOR in slots:
        return InspectionCaseWorkflowState.STAGE_REPORT_AUDIT

    case = (case_stage or "").strip()
    draft_idx = workflow_stage_index(InspectionCaseWorkflowState.STAGE_REPORT_DRAFT)
    if case and workflow_stage_index(case) >= 0 and workflow_stage_index(case) < draft_idx:
        return case
    if case and workflow_stage_index(case) >= draft_idx:
        # 案件已到编制及之后，但本版本尚未编制人签字 → 仍处编制
        return InspectionCaseWorkflowState.STAGE_REPORT_DRAFT
    return case or InspectionCaseWorkflowState.STAGE_REPORT_DRAFT


def signed_slots_for_export_variant(
    *,
    case_id: int,
    task_no: str,
    export_variant: str,
) -> set[str]:
    qs = CaseReportSignatureRecord.objects.filter(
        case_id=case_id,
        task_no=(task_no or "").strip(),
        export_variant=normalize_signature_export_variant(export_variant),
        voided_at__isnull=True,
    ).exclude(slot=CaseReportSignatureRecord.SLOT_ISSUE_DATE)
    return {str(r.slot) for r in qs}


def void_case_report_signatures(
    *,
    case_id: int,
    task_no: str = "",
    export_variants: set[str] | frozenset[str] | list[str] | None = None,
) -> int:
    """重新导出等场景：作废该案件/任务下尚未作废的签字记录。

    ``export_variants`` 非空时仅作废对应报告版本的签字。
    """
    qs = CaseReportSignatureRecord.objects.filter(
        case_id=case_id,
        voided_at__isnull=True,
    )
    tn = (task_no or "").strip()
    if tn:
        qs = qs.filter(task_no=tn)
    if export_variants is not None:
        from radiation_detection_report.report_pdf_integrator import (
            normalize_report_export_variants,
        )

        vs = normalize_report_export_variants(list(export_variants))
        if not vs:
            return 0
        qs = qs.filter(export_variant__in=vs)
    return qs.update(voided_at=timezone.now())


def reset_report_workflow_after_reexport(
    *,
    case: InspectionCase,
    user: User | None,
    task_no: str = "",
    export_variants: set[str] | frozenset[str] | list[str] | None = None,
) -> None:
    """重导出后：作废对应版本签字链；若触及全本或当前环节所需签字被拆空，则回退编制环节。"""
    from radiation_detection_report.report_pdf_integrator import (
        REPORT_EXPORT_VARIANT_FULL,
        normalize_report_export_variants,
    )

    vs = None
    if export_variants is not None:
        vs = set(normalize_report_export_variants(list(export_variants)))
    void_case_report_signatures(
        case_id=case.pk, task_no=task_no, export_variants=vs
    )
    # 仅重导无防护/仅防护：只作废该版本签字，不回退主流程
    touch_main = vs is None or REPORT_EXPORT_VARIANT_FULL in vs
    if not touch_main:
        return

    from apps.core.workflow_service import get_or_create_workflow_state, transition_case_stage

    st = get_or_create_workflow_state(case)
    report_chain = {
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        InspectionCaseWorkflowState.STAGE_ISSUED,
    }
    if st.stage in report_chain:
        transition_case_stage(
            case,
            InspectionCaseWorkflowState.STAGE_REPORT_DRAFT,
            user=user,
            return_reason="重新导出报告，签字链已作废",
        )
        st.refresh_from_db()
    if st.issue_date is not None and (
        vs is None or REPORT_EXPORT_VARIANT_FULL in vs
    ):
        st.issue_date = None
        st.save(update_fields=["issue_date", "updated_at"])


def _read_user_signature_bytes(user_sig: UserSignature) -> bytes:
    if not user_sig.image:
        return b""
    path = Path(settings.MEDIA_ROOT) / str(user_sig.image)
    if path.is_file():
        return path.read_bytes()
    return b""


def _find_field_underline(
    page: fitz.Page,
    rect: fitz.Rect | list[float] | tuple[float, ...],
    *,
    y_tol: float = 10.0,
) -> tuple[float, float, float, float] | None:
    """
    在栏位矩形附近找最长水平线（模板预印下划线）。
    返回 (x0, x1, y, stroke_width)；找不到则 None。
    """
    r = fitz.Rect(rect) if not isinstance(rect, fitz.Rect) else rect
    best: tuple[float, float, float, float, float] | None = None  # score, x0, x1, y, w
    y_lo = r.y0 - 2.0
    y_hi = r.y1 + y_tol
    x_lo = r.x0 - 4.0
    x_hi = r.x1 + 4.0
    for d in page.get_drawings():
        stroke_w = float(d.get("width") or 0.7)
        for item in d.get("items") or []:
            segs: list[tuple[float, float, float]] = []
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(float(p1.y) - float(p2.y)) > 0.6:
                    continue
                y = (float(p1.y) + float(p2.y)) / 2.0
                xa, xb = sorted((float(p1.x), float(p2.x)))
                segs.append((xa, xb, y))
            elif item[0] == "re":
                box = item[1]
                if float(box.height) > 1.8 or float(box.width) < 20:
                    continue
                segs.append((float(box.x0), float(box.x1), float(box.y0)))
            for xa, xb, y in segs:
                if y < y_lo or y > y_hi:
                    continue
                if xb < x_lo or xa > x_hi:
                    continue
                w = xb - xa
                score = w - abs(y - r.y1) * 0.5
                if best is None or score > best[0]:
                    best = (score, xa, xb, y, stroke_w)
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def _content_rect_above_underline(
    page: fitz.Page,
    rect: fitz.Rect | list[float] | tuple[float, ...],
    *,
    min_height: float = 8.0,
    gap: float = 1.2,
) -> fitz.Rect:
    """叠印内容区：底边收到下划线上方，避免盖住模板线。"""
    r = fitz.Rect(rect) if not isinstance(rect, fitz.Rect) else fitz.Rect(rect)
    ul = _find_field_underline(page, r)
    if ul is None:
        inset = min(4.0, max(0.0, r.height * 0.22))
        if r.height - inset >= min_height:
            r.y1 -= inset
        return r
    _x0, _x1, uy, _sw = ul
    if uy < r.y1 and uy > r.y0:
        new_y1 = uy - gap
        if new_y1 - r.y0 >= min_height:
            r.y1 = new_y1
    return r


def _redraw_field_underline(
    page: fitz.Page,
    underline: tuple[float, float, float] | tuple[float, float, float, float] | None,
) -> None:
    if not underline:
        return
    if len(underline) >= 4:
        x0, x1, y, stroke_w = underline[0], underline[1], underline[2], underline[3]
    else:
        x0, x1, y = underline[0], underline[1], underline[2]
        stroke_w = 0.8
    if x1 - x0 < 8:
        return
    page.draw_line(
        fitz.Point(x0, y),
        fitz.Point(x1, y),
        color=(0, 0, 0),
        width=max(0.7, float(stroke_w)),
        overlay=True,
    )


def _overlay_signature_on_pdf(
    pdf_bytes: bytes,
    *,
    page_1based: int,
    rect_xyxy: list[float],
    image_bytes: bytes,
    date_text: str = "",
    clear_rect_first: bool = False,
) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_idx = max(0, int(page_1based) - 1)
        if page_idx >= doc.page_count:
            raise ValueError(f"签字页码 {page_1based} 超出报告页数")
        page = doc[page_idx]
        r_full = fitz.Rect(*rect_xyxy)
        underline = _find_field_underline(page, r_full)
        r = _content_rect_above_underline(page, r_full)
        if clear_rect_first:
            # 覆盖重签：只擦内容区，不动下划线
            page.draw_rect(r, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
        if image_bytes:
            page.insert_image(r, stream=image_bytes, keep_proportion=True, overlay=True)
        if date_text:
            # 略缩字号以适应窄框；仍尽量完整显示 YYYY年MM月DD日
            fs = 10.5
            if r.width < 90:
                fs = 8.0
            page.insert_textbox(
                r,
                date_text,
                fontsize=fs,
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_CENTER,
                overlay=True,
            )
        # 叠印可能仍轻微压到线：按原坐标重绘完整下划线
        _redraw_field_underline(page, underline)
        from utils.pdf_compress import pdf_document_to_compressed_bytes

        return pdf_document_to_compressed_bytes(doc, subset_fonts=False)
    finally:
        doc.close()


def _overwrite_current_report_file(
    *,
    report_file: LibraryFile,
    pdf_bytes: bytes,
    task_no: str = "",
) -> LibraryFile:
    """原地覆盖当前报告内容，并纠正历史 signed_v 展示名。"""
    from apps.core.library_file_service import overwrite_library_file_bytes

    stable_name = stable_current_report_display_name(
        report_file.original_name or "", task_no=task_no
    )
    return overwrite_library_file_bytes(
        report_file, pdf_bytes, original_name=stable_name
    )


def _find_issued_snapshot_file(
    *,
    case: InspectionCase,
    project: LibraryProject,
    task_no: str,
    current_report: LibraryFile,
) -> LibraryFile | None:
    display = issued_snapshot_display_name(current_report.original_name or "", task_no)
    qs = list(
        LibraryFile.objects.filter(
            category=LibraryFile.CATEGORY_REPORT,
            link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
            link_object_id=case.pk,
            projects=project,
            deleted_at__isnull=True,
        )
        .order_by("-created_at", "-id")[:40]
    )
    snaps = [f for f in qs if is_issued_report_snapshot_file(f)]
    exact = next((f for f in snaps if (f.original_name or "") == display), None)
    if exact is not None:
        return exact
    # 兼容历史「-签发定稿」命名
    legacy = next(
        (f for f in snaps if is_issued_report_snapshot_name(f.original_name or "")),
        None,
    )
    if legacy is not None:
        return legacy
    tn = (task_no or "").replace("/", "_").strip()
    if tn:
        hit = next((f for f in snaps if tn in (f.original_name or "")), None)
        if hit is not None:
            return hit
    return snaps[0] if snaps else None


def _upsert_issued_snapshot(
    *,
    user: User,
    project: LibraryProject,
    report_task: LibraryTask | None,
    case: InspectionCase,
    task_no: str,
    current_report: LibraryFile,
    pdf_bytes: bytes,
) -> LibraryFile:
    """签发定稿：覆盖已有快照，或新建一份只读副本（展示名与当前报告相同）。"""
    from apps.core.library_file_service import (
        overwrite_library_file_bytes,
        report_relative_path,
        safe_library_basename,
    )
    from apps.core.models import LibraryFileProject, LibraryFileTask

    display_name = issued_snapshot_display_name(
        current_report.original_name or "", task_no
    )
    existing = _find_issued_snapshot_file(
        case=case,
        project=project,
        task_no=task_no,
        current_report=current_report,
    )
    if existing is not None:
        return overwrite_library_file_bytes(
            existing, pdf_bytes, original_name=display_name
        )

    disk_name = safe_library_basename(display_name)
    rel = report_relative_path(
        disk_name,
        project=project,
        library_task=report_task,
        issued_snapshot=True,
    )
    abs_p = pipeline_service.library_absolute_path(rel)
    abs_p.parent.mkdir(parents=True, exist_ok=True)
    abs_p.write_bytes(pdf_bytes)
    sha = _sha256_bytes(pdf_bytes)
    lf = LibraryFile.objects.create(
        original_name=display_name,
        relative_path=rel,
        category=LibraryFile.CATEGORY_REPORT,
        content_sha256=sha,
        size=len(pdf_bytes),
        created_by=user,
        link_entity=LibraryFile.LINK_ENTITY_INSPECTION_CASE,
        link_object_id=case.pk,
    )
    LibraryFileProject.objects.get_or_create(
        library_file=lf,
        project_id=project.pk,
        defaults={"created_by": user},
    )
    if report_task is not None:
        LibraryFileTask.objects.get_or_create(
            library_file=lf,
            library_task=report_task,
            defaults={"created_by": user},
        )
    return lf


def _log_signature_usage(
    *,
    owner: User,
    user_sig: UserSignature,
    slot: str,
    actor: User,
    project: LibraryProject,
    case: InspectionCase,
    submission: InspectionSubmission,
    report_file: LibraryFile,
    report_sha256: str,
    workflow_stage: str,
) -> None:
    UserSignatureEvent.objects.create(
        user=owner,
        user_signature=user_sig,
        role=slot,
        event_type=UserSignatureEvent.EVENT_USED_REPORT_SIGN,
        actor=actor,
        project=project,
        case=case,
        submission=submission,
        task_no=(submission.task_no or "").strip(),
        library_file=report_file,
        snapshot_path=report_file.relative_path if report_file else "",
        content_sha256=user_sig.content_sha256 or report_sha256,
        detail={
            "context": "report_workflow_sign",
            "workflow_stage": workflow_stage,
            "report_sha256": report_sha256,
        },
    )


def build_report_signature_status(
    *,
    case: InspectionCase | None,
    project: LibraryProject,
    submission: InspectionSubmission | None,
    viewer: User,
) -> dict[str, Any]:
    """委托管理 UI：报告签字状态面板数据（含各导出版本）。"""
    from radiation_detection_report.report_pdf_integrator import report_export_variant_label

    empty = {
        "enabled": False,
        "report_file": None,
        "report_download_url": "",
        "report_preview_url": "",
        "slots": [],
        "timeline": [],
        "pending_slot": "",
        "pending_label": "",
        "user_has_signature": False,
        "can_sign": False,
        "variants": [],
        "export_variant": "",
    }
    if case is None or submission is None or not (submission.task_no or "").strip():
        return empty

    task_no = submission.task_no
    variants = list_task_report_export_variants(
        case=case, project=project, task_no=task_no
    )
    report_file = None
    if variants:
        report_file = find_latest_report_library_file(
            case=case, project=project, task_no=task_no, export_variant=variants[0]
        )
    records_all = list_case_report_signature_records(case_id=case.pk, task_no=task_no)

    from apps.core.commission_workflow_progress import effective_workflow_stage

    stage = effective_workflow_stage(case, submission)

    user_sig = get_active_user_signature(viewer)
    slots_order = (
        CaseReportSignatureRecord.SLOT_REPORT_AUTHOR,
        CaseReportSignatureRecord.SLOT_REPORT_AUDITOR,
        CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY,
    )

    def _slots_ui_for_records(records: list[CaseReportSignatureRecord]) -> list[dict]:
        signed_slots = {
            r.slot for r in records if r.slot != CaseReportSignatureRecord.SLOT_ISSUE_DATE
        }
        rec_by_slot = {r.slot: r for r in records}
        slots_ui = []
        for slot in slots_order:
            rec = rec_by_slot.get(slot)
            slots_ui.append(
                {
                    "slot": slot,
                    "label": SIGN_SLOT_LABELS.get(slot, slot),
                    "signed": slot in signed_slots,
                    "signer": (
                        (rec.signer.get_full_name() or rec.signer.username)
                        if rec and rec.signer_id
                        else ""
                    ),
                    "signed_at": rec.signed_at if rec else None,
                    "version": rec.sign_version if rec else 0,
                }
            )
        issue_rec = rec_by_slot.get(CaseReportSignatureRecord.SLOT_ISSUE_DATE)
        if issue_rec:
            slots_ui.append(
                {
                    "slot": CaseReportSignatureRecord.SLOT_ISSUE_DATE,
                    "label": "签发日期",
                    "signed": True,
                    "signer": (
                        (issue_rec.signer.get_full_name() or issue_rec.signer.username)
                        if issue_rec.signer_id
                        else ""
                    ),
                    "signed_at": issue_rec.signed_at,
                    "version": issue_rec.sign_version,
                }
            )
        return slots_ui

    variant_rows: list[dict[str, Any]] = []
    for v in variants:
        v_file = find_latest_report_library_file(
            case=case, project=project, task_no=task_no, export_variant=v
        )
        v_recs = [r for r in records_all if (r.export_variant or "full") == v]
        v_signed = {
            r.slot for r in v_recs if r.slot != CaseReportSignatureRecord.SLOT_ISSUE_DATE
        }
        v_stage = variant_report_stage_from_signed_slots(v_signed, case_stage=stage)
        v_target = _pending_sign_target_stage(v_stage)
        v_pending_slot = ADVANCE_TARGET_SIGN_SLOT.get(v_target, "") if v_target else ""
        v_pending = bool(v_pending_slot and v_pending_slot not in v_signed)
        v_can = bool(
            v_pending
            and v_file
            and user_sig
            and _viewer_may_sign_slot(viewer, project, v_pending_slot, v_stage)
        )
        variant_rows.append(
            {
                "export_variant": v,
                "label": report_export_variant_label(v),
                "report_file": v_file,
                "report_file_name": v_file.original_name if v_file else "",
                "report_download_url": (
                    reverse("file_library_download", args=[v_file.pk]) if v_file else ""
                ),
                "report_preview_url": (
                    reverse("file_preview", args=[v_file.pk]) if v_file else ""
                ),
                "slots": _slots_ui_for_records(v_recs),
                "pending_slot": v_pending_slot if v_pending else "",
                "can_sign": v_can,
                "signed_slots": sorted(v_signed),
                "stage_code": v_stage,
                "stage_label": dict(InspectionCaseWorkflowState.STAGE_CHOICES).get(
                    v_stage, v_stage or "—"
                ),
            }
        )

    # 汇总仅作兼容：取最落后版本的待签；各版本独立推进，不要求齐签
    lagging = None
    if variant_rows:
        from apps.core.project_workflow_ui import workflow_stage_index

        lagging = min(
            variant_rows,
            key=lambda vr: workflow_stage_index(str(vr.get("stage_code") or "")),
        )
    pending_slot = str((lagging or {}).get("pending_slot") or "")
    pending_label = ""
    if pending_slot:
        pending_label = f"待您签字（{SIGN_SLOT_LABELS.get(pending_slot, pending_slot)}）"
        if len(variant_rows) > 1:
            pending_label += "：各版本独立签署"

    can_sign = any(bool(vr.get("can_sign")) for vr in variant_rows)

    timeline = [
        {
            "version": r.sign_version,
            "slot_label": r.slot_label,
            "export_variant": r.export_variant or "full",
            "export_variant_label": report_export_variant_label(r.export_variant or "full"),
            "signer": (r.signer.get_full_name() or r.signer.username) if r.signer_id else "—",
            "signed_at": r.signed_at,
            "report_file_id": r.report_file_id,
            "sha256_short": (r.report_sha256_after or "")[:8],
        }
        for r in records_all
        if r.slot != CaseReportSignatureRecord.SLOT_ISSUE_DATE
    ]

    download_url = ""
    preview_url = ""
    if report_file:
        download_url = reverse("file_library_download", args=[report_file.pk])
        preview_url = reverse("file_preview", args=[report_file.pk])

    # 兼容旧 UI：任一版本已签即展示（独立推进）
    any_signed = {
        slot
        for slot in slots_order
        if any(slot in set(vr.get("signed_slots") or []) for vr in variant_rows)
    }
    slots_ui = []
    for slot in slots_order:
        slots_ui.append(
            {
                "slot": slot,
                "label": SIGN_SLOT_LABELS.get(slot, slot),
                "signed": slot in any_signed,
                "signer": "",
                "signed_at": None,
                "version": 0,
            }
        )

    return {
        "enabled": bool(variants),
        "report_file": report_file,
        "report_file_name": report_file.original_name if report_file else "",
        "report_download_url": download_url,
        "report_preview_url": preview_url,
        "slots": slots_ui,
        "timeline": timeline,
        "pending_slot": pending_slot,
        "pending_label": pending_label,
        "user_has_signature": user_sig is not None,
        "can_sign": can_sign,
        "variants": variant_rows,
        "export_variant": variants[0] if len(variants) == 1 else "",
        "no_report_hint": "" if variants else "报告尚未生成，请先在项目工作台导出报告",
        "no_signature_hint": "" if user_sig else "请先在「签名管理」上传个人签名",
    }


def _pending_sign_target_stage(current_stage: str) -> str:
    mapping = {
        InspectionCaseWorkflowState.STAGE_REPORT_DRAFT: InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN: InspectionCaseWorkflowState.STAGE_ISSUED,
    }
    return mapping.get(current_stage or "", "")


def _viewer_may_sign_slot(
    viewer: User,
    project: LibraryProject,
    slot: str,
    current_stage: str,
) -> bool:
    from apps.core.commission_workflow_progress import user_may_manual_advance_to_stage

    target = _pending_sign_target_stage(current_stage)
    if not target or ADVANCE_TARGET_SIGN_SLOT.get(target) != slot:
        return False
    required_role = {
        CaseReportSignatureRecord.SLOT_REPORT_AUTHOR: LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR,
        CaseReportSignatureRecord.SLOT_REPORT_AUDITOR: LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR,
        CaseReportSignatureRecord.SLOT_AUTHORIZED_SIGNATORY: LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY,
    }.get(slot)
    if not required_role:
        return False
    return user_may_manual_advance_to_stage(
        viewer, project, required_workflow_role=required_role
    )


@transaction.atomic
def apply_report_signature_and_advance(
    *,
    user: User,
    project: LibraryProject,
    submission: InspectionSubmission,
    target_stage: str,
    issue_date: date | None = None,
    export_variant: str | None = None,
) -> tuple[bool, str]:
    """
    使用当前用户个人签名叠印报告对应栏位，并推进工作流。
    仅用于 REPORT_AUDIT / REPORT_SIGN / ISSUED 三个推进目标。

    各导出版本（全本 / 无防护结果 / 仅防护结果）签字链彼此独立：签完本版本即推进
    本版本环节；案件总环节取各版本中较前的进度（可向前），不要求其它版本齐签。
    叠印结果原地覆盖该版本「当前报告」；签发时另存定稿快照。
    """
    from radiation_detection_report.report_pdf_integrator import report_export_variant_label

    if submission.case_id is None:
        return False, "该任务尚无关联案件"
    case = submission.case
    if case.library_project_id and int(case.library_project_id) != int(project.pk):
        return False, "案件与委托不匹配"

    sign_slot = ADVANCE_TARGET_SIGN_SLOT.get(target_stage)
    if not sign_slot:
        return False, "该推进步骤不需要报告签字"

    from apps.core.commission_workflow_progress import (
        apply_manual_workflow_advance,
        effective_workflow_stage,
        user_may_manual_advance_to_stage,
    )

    required_role = {
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: LibraryProjectWorkflowMember.ROLE_REPORT_AUTHOR,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN: LibraryProjectWorkflowMember.ROLE_REPORT_AUDITOR,
        InspectionCaseWorkflowState.STAGE_ISSUED: LibraryProjectWorkflowMember.ROLE_AUTH_SIGNATORY,
    }.get(target_stage)
    if not required_role or not user_may_manual_advance_to_stage(
        user, project, required_workflow_role=required_role
    ):
        return False, "无权执行该环节签字"

    existing_variants = list_task_report_export_variants(
        case=case, project=project, task_no=submission.task_no
    )
    if not existing_variants:
        return False, "报告尚未生成，请先在项目工作台导出报告后再签字"

    raw_variant = (export_variant or "").strip()
    if raw_variant:
        variant = normalize_signature_export_variant(raw_variant)
        if variant not in existing_variants:
            return False, f"未找到「{report_export_variant_label(variant)}」报告文件"
    elif len(existing_variants) == 1:
        variant = existing_variants[0]
    else:
        names = "、".join(report_export_variant_label(v) for v in existing_variants)
        return False, f"当前有多个报告版本（{names}），请指定要签字的版本"

    variant_label = report_export_variant_label(variant)

    case_effective = effective_workflow_stage(case, submission)
    variant_signed = signed_slots_for_export_variant(
        case_id=case.pk,
        task_no=submission.task_no,
        export_variant=variant,
    )
    variant_stage = variant_report_stage_from_signed_slots(
        variant_signed, case_stage=case_effective
    )
    expected_current = {
        InspectionCaseWorkflowState.STAGE_REPORT_AUDIT: InspectionCaseWorkflowState.STAGE_REPORT_DRAFT,
        InspectionCaseWorkflowState.STAGE_REPORT_SIGN: InspectionCaseWorkflowState.STAGE_REPORT_AUDIT,
        InspectionCaseWorkflowState.STAGE_ISSUED: InspectionCaseWorkflowState.STAGE_REPORT_SIGN,
    }.get(target_stage)
    slot_label = SIGN_SLOT_LABELS.get(sign_slot, sign_slot)
    is_resign = sign_slot in variant_signed
    if is_resign:
        if variant_stage != target_stage:
            return (
                False,
                f"「{variant_label}」的「{slot_label}」之后环节已推进，无法覆盖该签名；如需重做请先回退后续环节",
            )
    elif variant_stage != expected_current:
        return (
            False,
            f"「{variant_label}」当前环节为「{dict(InspectionCaseWorkflowState.STAGE_CHOICES).get(variant_stage, variant_stage)}」，无法执行此签字",
        )

    user_sig = get_active_user_signature(user)
    if user_sig is None:
        return False, "请先在「签名管理」上传您的个人签名"

    report_file = find_latest_report_library_file(
        case=case,
        project=project,
        task_no=submission.task_no,
        export_variant=variant,
    )
    if report_file is None:
        return False, f"未找到「{variant_label}」报告，请先导出后再签字"

    report_task = resolve_report_library_task(
        project=project, submission=submission, report_file=report_file
    )
    template_parsed = load_report_template_parsed(report_task)
    slots = resolve_report_signature_slots(template_parsed)
    slot_spec = slots.get(sign_slot)
    if not slot_spec:
        return False, "报告模板未配置该签字位坐标，请联系管理员"
    if target_stage == InspectionCaseWorkflowState.STAGE_ISSUED:
        if CaseReportSignatureRecord.SLOT_ISSUE_DATE not in slots and not slots.get(
            "_issueDateParts"
        ):
            return False, "报告模板未配置签发日期栏位，请联系管理员"

    sig_bytes = _read_user_signature_bytes(user_sig)
    if not sig_bytes:
        return False, "无法读取您的签名图片"

    src_path = pipeline_service.library_absolute_path(report_file.relative_path)
    if not src_path.is_file():
        return False, "报告文件不存在或已被删除"
    pdf_before = src_path.read_bytes()
    sha_before = _sha256_bytes(pdf_before)

    page = int(slot_spec.get("page") or 3)
    rect = list(slot_spec.get("rect") or [])
    if len(rect) < 4:
        return False, "签字位坐标无效"

    pdf_after = _overlay_signature_on_pdf(
        pdf_before,
        page_1based=page,
        rect_xyxy=rect,
        image_bytes=sig_bytes,
        clear_rect_first=is_resign,
    )

    issue_page = 0
    issue_rect: list[float] = []
    if target_stage == InspectionCaseWorkflowState.STAGE_ISSUED:
        idate = issue_date or timezone.localdate()
        try:
            pdf_after, issue_page, issue_rect = _overlay_issue_date_on_pdf(
                pdf_after, slots=slots, issue_day=idate
            )
        except ValueError as exc:
            return False, str(exc)

    sign_version = (
        CaseReportSignatureRecord.objects.filter(
            case_id=case.pk, task_no=submission.task_no
        ).count()
        + 1
    )
    current_lf = _overwrite_current_report_file(
        report_file=report_file,
        pdf_bytes=pdf_after,
        task_no=submission.task_no or "",
    )
    sha_after = _sha256_bytes(pdf_after)

    CaseReportSignatureRecord.objects.create(
        case=case,
        project=project,
        submission=submission,
        task_no=(submission.task_no or "").strip(),
        export_variant=variant,
        workflow_stage=variant_stage,
        slot=sign_slot,
        signer=user,
        user_signature=user_sig,
        signature_sha256=user_sig.content_sha256 or _sha256_bytes(sig_bytes),
        report_file=current_lf,
        report_sha256_before=sha_before,
        report_sha256_after=sha_after,
        overlay_page=page,
        overlay_rect=rect,
        sign_version=sign_version,
    )
    _log_signature_usage(
        owner=user,
        user_sig=user_sig,
        slot=sign_slot,
        actor=user,
        project=project,
        case=case,
        submission=submission,
        report_file=current_lf,
        report_sha256=sha_after,
        workflow_stage=variant_stage,
    )

    if target_stage == InspectionCaseWorkflowState.STAGE_ISSUED and len(issue_rect) >= 4:
        CaseReportSignatureRecord.objects.create(
            case=case,
            project=project,
            submission=submission,
            task_no=(submission.task_no or "").strip(),
            export_variant=variant,
            workflow_stage=variant_stage,
            slot=CaseReportSignatureRecord.SLOT_ISSUE_DATE,
            signer=user,
            user_signature=None,
            signature_sha256="",
            report_file=current_lf,
            report_sha256_before=sha_after,
            report_sha256_after=sha_after,
            overlay_page=issue_page,
            overlay_rect=issue_rect,
            sign_version=sign_version + 1,
        )
        _upsert_issued_snapshot(
            user=user,
            project=project,
            report_task=report_task,
            case=case,
            task_no=submission.task_no or "",
            current_report=current_lf,
            pdf_bytes=pdf_after,
        )

    if is_resign:
        return True, f"已重新叠印「{variant_label}」{slot_label}签名（已覆盖上次）"

    # 本版本独立推进：签完即推进案件总环节（若已更前则跳过）；不等待其它版本
    ok, msg = apply_manual_workflow_advance(
        user=user,
        project=project,
        submission=submission,
        target_stage=target_stage,
    )
    if not ok:
        return False, msg
    return True, f"已叠印「{variant_label}」{slot_label}签名并推进本版本流程"
