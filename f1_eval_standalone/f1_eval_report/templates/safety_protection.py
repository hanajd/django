# -*- coding: utf-8 -*-
"""防护设施和措施 · 安全防护措施：导语 + 表8 + 样式附图 + 通风管道图（A3）。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from f1_eval_report.templates.protection_zoning import count_xray_rooms

TABLE_MARK = "<<<F1_TABLE>>>"

TABLE8_TITLE = "表8 本项目X射线设备机房安全防护措施设计情况评价"

DEFAULT_INTRO = (
    "本项目{room_count}间X射线设备机房安全防护措施计划及评价如表8所示，"
    "符合GBZ 130-2020《放射诊断放射防护要求》的要求。"
)

# 表8行：(措施名称, 设计情况默认, 标准要求)
TABLE8_ROWS: List[Tuple[str, str, str]] = [
    (
        "观察窗或摄像监控装置",
        "拟设置，便于观察受检者状态及防护门开闭情况",
        "GBZ 130-2020 第6.4.1条：机房应设有观察窗或摄像监控装置，"
        "其设置的位置应便于观察到受检者状态及防护门开闭情况",
    ),
    (
        "电离辐射警告标志",
        "拟在机房门外设置（样式见图）",
        "GBZ 130-2020 第6.4.4条：机房门外应有电离辐射警告标志",
    ),
    (
        "工作状态指示灯及灯箱可视警示语句",
        "拟在机房门上方设置，灯箱警示语为“射线有害，灯亮勿入”（样式见图）",
        "GBZ 130-2020 第6.4.4条：机房门上方应有醒目的工作状态指示灯，"
        "灯箱上应设置如“射线有害、灯亮勿入”的可视警示语句",
    ),
    (
        "放射防护注意事项告知栏",
        "拟在候诊区设置（样式见图）",
        "GBZ 130-2020 第6.4.4条：候诊区应设置放射防护注意事项告知栏",
    ),
    (
        "机房门与工作状态指示灯联动",
        "拟设置门—灯联动；推拉门拟落实曝光时关闭机房门的管理措施；"
        "平开门拟设自动闭门装置；电动推拉门拟设防夹装置",
        "GBZ 130-2020 第6.4.5～6.4.6条：工作状态指示灯能与机房门有效关联；"
        "平开门应有自动闭门装置；电动推拉门宜设置防夹装置",
    ),
    (
        "候诊与陪检管理",
        "拟落实：受检者不应在机房内候诊；非特殊情况检查过程中陪检者不应滞留机房内",
        "GBZ 130-2020 第6.4.7条",
    ),
    (
        "地面警戒线",
        "拟在机房入口等处设置地面警戒线",
        "便于提示无关人员勿入控制区（按项目防护计划落实）",
    ),
    (
        "动力通风装置",
        "拟按机房设置动力通风，通风管道布局见附图",
        "机房应保持良好通风；通风管道布局示意图由信息表提取（A3横置）",
    ),
]

# 表8下方三张样式图（题注不含固定图号，由提取后统一编号）
STYLE_FIGURE_SPECS: List[Dict[str, str]] = [
    {
        "id": "warning_sign",
        "filename": "style_warning_sign.png",
        "caption": "电离辐射警告标志（样式）",
    },
    {
        "id": "lightbox_text",
        "filename": "style_lightbox_warning.png",
        "caption": "灯箱处可视警示语句（样式）",
    },
    {
        "id": "notice_board",
        "filename": "style_notice_board.png",
        "caption": "放射防护注意事项告知栏（样式）",
    },
]


def _load_fixed(rel: str, default: str = "") -> str:
    from f1_eval_report.templates.loader import load_common_text

    try:
        t = load_common_text(rel).strip()
        return t or default
    except Exception:
        return default


def _fill(tpl: str, *, room_count: int) -> str:
    n = str(max(1, int(room_count)))
    return (
        (tpl or "")
        .replace("{room_count}", n)
        .replace("{机房间数}", n)
        .strip()
    )


def _cell(
    c0: int,
    c1: int,
    text: str,
    *,
    row_span: int = 1,
    align: str = "",
) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "c0": c0,
        "c1": c1,
        "row_span": row_span,
        "col_span": max(1, c1 - c0),
        "text": text,
    }
    if align:
        d["align"] = align
    return d


def build_table8(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """表8：安全防护措施设计情况评价。"""
    room_count = count_xray_rooms(list(devices or [])) or 1
    header = {
        "band": 0,
        "cells": [
            _cell(0, 1, "安全防护措施", align="center"),
            _cell(1, 2, "设计情况", align="center"),
            _cell(2, 3, "标准要求\n(GBZ130-2020)", align="center"),
            _cell(3, 4, "评价", align="center"),
        ],
    }
    rows: List[Dict[str, Any]] = [header]
    for i, (name, design, standard) in enumerate(TABLE8_ROWS, start=1):
        rows.append(
            {
                "band": i,
                "cells": [
                    _cell(0, 1, name, align="left"),
                    _cell(1, 2, design, align="left"),
                    _cell(2, 3, standard, align="left"),
                    _cell(3, 4, "符合", align="center"),
                ],
            }
        )
    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE8_TITLE,
        "source": "safety_protection",
        "from_info_sheet": False,
        "room_count": room_count,
        "column_count": 4,
        "column_fractions": [0.0, 0.18, 0.48, 0.82, 1.0],
        "band_count": len(rows),
        "row_min_height_pt": 22.0,
        "repeat_header_on_new_page": True,
        "note": "表8可在常用模板中编辑；样式图与通风管道图挂在安全防护措施栏目之后",
        "rows": rows,
    }


def _strip_style_caption(caption: str) -> str:
    import re

    s = str(caption or "").strip()
    return re.sub(r"^图\s*\d+\s*[.、:：\-—\s]*", "", s).strip() or s


def _package_style_asset_dir() -> Path:
    """优先工作区常用模板中的样式图，回退包内默认。"""
    from f1_eval_report.templates.loader import PACKAGE_TEMPLATES, templates_dir

    cand = Path(templates_dir()) / "common" / "protection_measures" / "style_figures"
    if cand.is_dir():
        return cand
    return Path(PACKAGE_TEMPLATES) / "common" / "protection_measures" / "style_figures"


def ensure_style_figure_png(path: Path, title: str) -> None:
    """生成简洁占位样式图（可被用户替换同名文件）。"""
    if path.is_file() and path.stat().st_size > 200:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fitz

        # A4 竖向示意页
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        rect = fitz.Rect(80, 200, 515, 620)
        page.draw_rect(rect, color=(0.2, 0.2, 0.2), width=2)
        # 图题不写进 PNG，由 caption 在图外统一编号
        page.insert_textbox(
            fitz.Rect(80, 220, 515, 280),
            "（样式示意，可替换为正式图样）",
            fontsize=12,
            fontname="china-s",
            align=1,
        )
        # 辐射三叶草示意（简化圆+文字）
        if "警告" in title:
            page.draw_circle((297, 420), 48, color=(1, 0.55, 0), width=3)
            page.insert_textbox(
                fitz.Rect(220, 400, 375, 450),
                "☢",
                fontsize=36,
                align=1,
            )
        elif "灯箱" in title or "警示语句" in title:
            box = fitz.Rect(160, 360, 435, 470)
            page.draw_rect(box, color=(0.1, 0.1, 0.1), fill=(1, 0.85, 0.2), width=1)
            page.insert_textbox(
                box,
                "射线有害\n灯亮勿入",
                fontsize=18,
                fontname="china-s",
                align=1,
            )
        else:
            board = fitz.Rect(140, 350, 455, 490)
            page.draw_rect(board, color=(0.15, 0.35, 0.7), width=2)
            page.insert_textbox(
                fitz.Rect(150, 370, 445, 470),
                "放射防护注意事项\n告知栏（样式）",
                fontsize=14,
                fontname="china-s",
                align=1,
            )
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        pix.save(str(path))
        doc.close()
    except Exception:
        # 最小合法 PNG
        import struct
        import zlib

        def _chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        raw = b"".join(b"\x00" + bytes([240] * 3 * 40) for _ in range(30))
        png = (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 40, 30, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b"")
        )
        path.write_bytes(png)


def build_style_figures(
    assets_dir: str | Path,
    *,
    ensure_placeholders: bool = True,
) -> List[Dict[str, Any]]:
    """表8下方三张样式附图（A4竖向插页）。"""
    out_dir = Path(assets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pkg = _package_style_asset_dir()
    figs: List[Dict[str, Any]] = []
    for spec in STYLE_FIGURE_SPECS:
        name = spec["filename"]
        dest = out_dir / name
        src = pkg / name
        if src.is_file():
            try:
                shutil.copy2(src, dest)
            except Exception:
                pass
        if ensure_placeholders:
            ensure_style_figure_png(dest, _strip_style_caption(spec["caption"]))
            # 同步回包内，便于常用模板目录可见
            try:
                pkg.mkdir(parents=True, exist_ok=True)
                if dest.is_file():
                    shutil.copy2(dest, pkg / name)
            except Exception:
                pass
        if not dest.is_file():
            continue
        figs.append(
            {
                "image": str(dest.resolve()),
                "caption": spec["caption"],
                "standalone_page": True,
                "page_mode": "a4_portrait",
                "page_width_pt": 595.3,
                "page_height_pt": 841.9,
                "style_figure_id": spec["id"],
                # 分类标记；PDF/工作台靠 style_figure_id 合页，不用图号
                "pack_group": "style",
                "caption_outside_image": True,
            }
        )
    return figs


def merge_safety_extra_figures(
    *,
    assets_dir: str | Path,
    ventilation_figs: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """样式图在前，信息表通风管道图（A3横置）在后；图号由 assign_sequential_figure_numbers 统一分配。"""
    style = build_style_figures(assets_dir)
    style_out: List[Dict[str, Any]] = []
    for f in style:
        if not isinstance(f, dict) or not f.get("image"):
            continue
        item = dict(f)
        item["caption"] = _strip_style_caption(str(item.get("caption") or ""))
        item.setdefault("caption_outside_image", True)
        style_out.append(item)
    vents: List[Dict[str, Any]] = []
    for f in ventilation_figs or []:
        if not isinstance(f, dict) or not f.get("image"):
            continue
        item = dict(f)
        item.setdefault("page_mode", "a3_landscape")
        item.setdefault("standalone_page", True)
        item.setdefault("caption_outside_image", True)
        item["caption"] = _strip_style_caption(str(item.get("caption") or ""))
        vents.append(item)
    return style_out + vents


def assemble_safety_protection(
    devices: Optional[List[Dict[str, Any]]] = None,
    *,
    include_table: bool = True,
    assets_dir: Optional[str] = None,
    ventilation_figs: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    返回:
      safety_protection: 导语 + <<<F1_TABLE>>>
      safety_protection_tables: [表8]
      extra_figures（可选）: 样式图 + 通风图
    """
    room_count = count_xray_rooms(devices) or 1
    intro = _fill(
        _load_fixed("protection_measures/safety_intro.md", DEFAULT_INTRO),
        room_count=room_count,
    )
    text = f"{intro}\n\n{TABLE_MARK}\n".strip() + "\n"
    out: Dict[str, Any] = {
        "safety_protection": text,
        "room_count": room_count,
    }
    if include_table:
        out["safety_protection_tables"] = [build_table8(devices)]
    else:
        out["safety_protection_tables"] = []
    if assets_dir:
        out["extra_figures"] = merge_safety_extra_figures(
            assets_dir=assets_dir,
            ventilation_figs=ventilation_figs,
        )
    return out
