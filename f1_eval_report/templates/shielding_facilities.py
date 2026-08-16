# -*- coding: utf-8 -*-
"""防护设施和措施 · 屏蔽设施：导语 + 表7（信息表组稿，同屏蔽合并）+ 表后总结。"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from f1_eval_report.templates.protection_zoning import count_xray_rooms

TABLE_MARK = "<<<F1_TABLE>>>"

TABLE7_TITLE = "表7 本项目X射线设备机房屏蔽设计情况及评价"

DEFAULT_INTRO = (
    "建设单位根据拟配备的射线装置类型、参数对本项目所在共计{room_count}间X射线设备机房"
    "屏蔽设施进行设计，其设计情况见表7所示，各机房屏蔽体的设计厚度均不低于"
    "GBZ 130-2020《放射诊断放射防护要求》的相关要求。"
)

DEFAULT_AFTER = (
    "综上所述，本项目{room_count}间X射线设备机房拟采取的各屏蔽体等效铅当量均不小于"
    "GBZ 130-2020《放射诊断放射防护要求》要求的屏蔽铅当量厚度，但部分屏蔽体设计偏保守。\n"
    "建议：建设单位根据表7建议，在满足标准要求屏蔽厚度的前提下，可适当减少各X射线设备机房的"
    "部分屏蔽体屏蔽材料厚度。\n"
    "要求：本项目墙体砌砖时砂浆饱满、不留缝隙，各机房推拉门应尽可能减小缝隙泄漏辐射；"
    "注意机房防护门设置防护地槽，推拉门宽于门洞的部分应大于“门一墙”间隙的十倍；"
    "注意平开防护门及防护观察窗与屏蔽墙体搭接处应有同等屏蔽厚度；"
    "注意管线穿墙口应与屏蔽墙体同等屏蔽厚度的防护，同时注意施工质量，确保防护效果。"
)

# 表注（①② 用 ^ ^ 上标；密度单位用 ^3^）
TABLE7_NOTE = (
    "注：^①^根据建设单位提供的屏蔽材料信息：混凝土密度≥2.35g/cm^3^，实心红砖密度≥1.65g/cm^3^，"
    "铅密度≥11.30g/cm^3^。"
    "^②^140kV（CT）下：120mm混凝土等效铅当量约为1.2mmPb，180mm混凝土等效铅当量约为2.0mmPb，"
    "370mm实心红砖等效铅当量约为3.5mmPb。"
    "150kV（DR、数字胃肠机）下：有用线束方向：180mm混凝土等效铅当量约为1.9mmPb，"
    "370mm实心红砖等效铅当量约为3.4mmPb；非有用线束方向：120mm混凝土等效铅当量约为1.4mmPb，"
    "180mm混凝土等效铅当量约为2.4mmPb，370mm实心红砖等效铅当量约为4.1mmPb。"
    "150kV（全身骨密度仪）下：180mm混凝土等效铅当量约为2.4mmPb，370mm实心红砖等效铅当量约为4.1mmPb。"
    "50kV（乳腺DR）下：180mm混凝土等效铅当量约为1.7mmPb，370mm实心红砖等效铅当量约为2.8mmPb。"
    "125kV（DSA、C形臂、ERCP）下：110mm混凝土等效铅当量约为1.3mmPb，120mm混凝土等效铅当量约为1.4mmPb，"
    "370mm实心红砖等效铅当量约为3.9mmPb，空心砖、镀锌方管等效铅当量忽略不计。"
    "*参考《放射防护实用手册》；*依据GBZ130-2020《放射诊断放射防护要求》。"
)

# 设备类兜底（模板文件缺失时使用）；正式文案以 shielding_standards.json 为准
_DEVICE_STD_FALLBACK: Dict[str, Tuple[str, str]] = {
    "ct": ("CT机房：2.5mmPb", "ct"),
    "dr": (
        "125kV以上的摄影机房：\n有用线束方向铅当量3.0mmPb\n非有用线束方向铅当量2.0mmPb",
        "dr",
    ),
    "gastro": (
        "125kV以上的摄影机房：\n有用线束方向铅当量3.0mmPb\n非有用线束方向铅当量2.0mmPb",
        "dr",
    ),
    "bmd": (
        "骨密度仪机房：有用线束方向铅当量1.0mmPb；非有用线束方向铅当量1.0mmPb",
        "bmd",
    ),
    "mammo": (
        "乳腺摄影机房：有用线束方向铅当量1.0mmPb；非有用线束方向铅当量1.0mmPb",
        "mammo",
    ),
    "dsa": (
        "C形臂X射线设备机房要求：\n有用线束方向：2.0mmPb\n非有用线束方向：2.0mmPb",
        "dsa",
    ),
    "ercp": (
        "C形臂X射线设备机房要求：\n有用线束方向：2.0mmPb\n非有用线束方向：2.0mmPb",
        "dsa",
    ),
    "carm": (
        "C形臂X射线设备机房要求：\n有用线束方向：2.0mmPb\n非有用线束方向：2.0mmPb",
        "dsa",
    ),
    "other": ("按GBZ 130-2020相应机房要求", "dr"),
}

# 兼容旧名
_DEVICE_STD = _DEVICE_STD_FALLBACK

# 材质厚度 → 等效铅当量 (mmPb)，按设备换算键
_EQ_TABLE: Dict[str, Dict[str, float]] = {
    "ct": {"concrete_110": 1.1, "concrete_120": 1.2, "concrete_180": 2.0, "brick_370": 3.5},
    "dr": {
        "concrete_110": 1.3,
        "concrete_120": 1.4,
        "concrete_180": 2.4,
        "brick_370": 4.1,
    },
    "bmd": {"concrete_110": 1.3, "concrete_120": 1.4, "concrete_180": 2.4, "brick_370": 4.1},
    "mammo": {"concrete_110": 1.0, "concrete_120": 1.1, "concrete_180": 1.7, "brick_370": 2.8},
    "dsa": {"concrete_110": 1.3, "concrete_120": 1.4, "concrete_180": 2.2, "brick_370": 3.9},
}


def _standards_json_candidates() -> List[str]:
    import os

    from f1_eval_report.templates.loader import PACKAGE_TEMPLATES, templates_dir

    rel = os.path.join("common", "protection_measures", "shielding_standards.json")
    return [
        os.path.join(templates_dir(), rel),
        os.path.join(PACKAGE_TEMPLATES, rel),
    ]


def load_shielding_standards() -> Dict[str, Any]:
    """读取可编辑的 GBZ130 屏蔽标准模板。"""
    import json
    import os

    for p in _standards_json_candidates():
        if os.path.isfile(p):
            try:
                data = json.loads(open(p, encoding="utf-8").read())
                if isinstance(data, dict) and isinstance(data.get("items"), dict):
                    return data
            except Exception:
                continue
    # 兜底：由内置 FALLBACK 拼出
    items = {}
    order = []
    for key, (text, eq_key) in _DEVICE_STD_FALLBACK.items():
        items[key] = {
            "label": key,
            "eq_key": eq_key,
            "standard_text": text,
            "note": "",
        }
        order.append(key)
    return {
        "schema": "f1_shielding_standards/v1",
        "description": "GBZ 130-2020 机房屏蔽铅当量标准要求",
        "order": order,
        "items": items,
    }


def device_std_for_class(device_class: str) -> Tuple[str, str]:
    """返回 (标准要求文案, 铅当量换算键)。优先读 shielding_standards.json。"""
    cls = (device_class or "other").strip().lower() or "other"
    cfg = load_shielding_standards()
    items = cfg.get("items") if isinstance(cfg.get("items"), dict) else {}
    it = items.get(cls) if isinstance(items.get(cls), dict) else None
    if not it and cls != "other":
        it = items.get("other") if isinstance(items.get("other"), dict) else None
    if isinstance(it, dict):
        text = str(it.get("standard_text") or "").strip()
        eq_key = str(it.get("eq_key") or cls).strip() or "dr"
        if text:
            return text, eq_key
    return _DEVICE_STD_FALLBACK.get(cls, _DEVICE_STD_FALLBACK["other"])


_SECTION_TITLE_RE = re.compile(
    r"^[一二三四五六七八九十百千零〇两\d]+[、.．]"
)


def _is_table7_section_title(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("注") or "表注" in t[:6] or t.startswith("①") or "屏蔽设施材料" in t:
        return False
    return bool(_SECTION_TITLE_RE.match(t))


def apply_standards_to_table7(table: Dict[str, Any]) -> Dict[str, Any]:
    """
    按 shielding_standards.json 回填表7「标准要求」列。
    不改动屏蔽设计列；按节标题 / 已存 device_class 识别设备类型。
    """
    if not isinstance(table, dict):
        return table
    rows = table.get("rows")
    if not isinstance(rows, list) or not rows:
        return table

    i = 0
    changed = False
    while i < len(rows):
        band = rows[i] if isinstance(rows[i], dict) else {}
        cells = band.get("cells") if isinstance(band.get("cells"), list) else []
        title = ""
        if len(cells) == 1:
            c0 = cells[0] if isinstance(cells[0], dict) else {}
            title = str(c0.get("text") or "")
            spans = int(c0.get("c0") or 0) == 0 and int(c0.get("c1") or 0) >= 4
        else:
            spans = False

        is_section = bool(band.get("section_kind") == "room_group") or (
            spans and _is_table7_section_title(title)
        )
        if not is_section:
            i += 1
            continue

        cls = str(band.get("device_class") or "").strip().lower()
        if not cls:
            cls = classify_shielding_device(title)
        std_text, _ = device_std_for_class(cls)
        band["device_class"] = cls
        band["section_kind"] = "room_group"

        # 在本节体内找到「标准要求」合并格并回填
        j = i + 1
        while j < len(rows):
            b2 = rows[j] if isinstance(rows[j], dict) else {}
            c2 = b2.get("cells") if isinstance(b2.get("cells"), list) else []
            if len(c2) == 1 and isinstance(c2[0], dict):
                t2 = str(c2[0].get("text") or "")
                if int(c2[0].get("c0") or 0) == 0 and int(c2[0].get("c1") or 0) >= 4:
                    if _is_table7_section_title(t2) or t2.startswith("注") or "表注" in t2[:8]:
                        break
            for c in c2:
                if not isinstance(c, dict):
                    continue
                if int(c.get("c0") or -1) != 3 or int(c.get("c1") or 0) != 4:
                    continue
                txt = str(c.get("text") or "")
                if "标准要求" in txt and "GBZ" in txt:
                    continue  # 表头
                if txt != std_text:
                    c["text"] = std_text
                    changed = True
                c["device_class"] = cls
            j += 1
        i = j if j > i else i + 1

    if changed:
        table["standards_from_template"] = True
    return table


def _simple_std_table(title: str, headers: List[str], data_rows: List[List[str]]) -> Dict[str, Any]:
    ncol = len(headers)
    rows: List[Dict[str, Any]] = [
        {
            "band": 0,
            "cells": [
                {"c0": i, "c1": i + 1, "row_span": 1, "col_span": 1, "text": h}
                for i, h in enumerate(headers)
            ],
        }
    ]
    for bi, cells in enumerate(data_rows, 1):
        padded = list(cells) + [""] * max(0, ncol - len(cells))
        rows.append(
            {
                "band": bi,
                "cells": [
                    {
                        "c0": i,
                        "c1": i + 1,
                        "row_span": 1,
                        "col_span": 1,
                        "text": str(padded[i] if i < len(padded) else ""),
                    }
                    for i in range(ncol)
                ],
            }
        )
    return {
        "schema": "embedded_generic_table/v1",
        "title": title,
        "column_count": ncol,
        "band_count": len(rows),
        "column_fractions": [i / ncol for i in range(ncol + 1)],
        "row_min_height_pt": 18.0,
        "rows": rows,
    }


def shielding_standards_to_embedded_table(
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """标准 JSON → 常用模板表格编辑。"""
    cfg = cfg if isinstance(cfg, dict) else load_shielding_standards()
    items = cfg.get("items") if isinstance(cfg.get("items"), dict) else {}
    order = list(cfg.get("order") or items.keys())
    headers = ["类型键", "显示名称", "换算键", "标准要求文案", "备注"]
    rows: List[List[str]] = []
    for key in order:
        it = items.get(key) or {}
        if not isinstance(it, dict):
            continue
        rows.append(
            [
                key,
                str(it.get("label") or key),
                str(it.get("eq_key") or key),
                str(it.get("standard_text") or ""),
                str(it.get("note") or ""),
            ]
        )
    return _simple_std_table("GBZ130 机房屏蔽铅当量标准要求（表7）", headers, rows)


def embedded_table_to_shielding_standards(
    table: Dict[str, Any], base: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    base = dict(base or {})
    items: Dict[str, Any] = {}
    order: List[str] = []
    for band in (table.get("rows") or [])[1:]:
        cells = (band or {}).get("cells") or []
        texts = [""] * 5
        for c in cells:
            if not isinstance(c, dict):
                continue
            c0 = int(c.get("c0") or 0)
            if 0 <= c0 < 5:
                texts[c0] = str(c.get("text") or "").strip()
        key = texts[0]
        if not key or key in {"类型键", "key"}:
            continue
        items[key] = {
            "label": texts[1] or key,
            "eq_key": texts[2] or key,
            "standard_text": texts[3],
            "note": texts[4],
        }
        order.append(key)
    return {
        "schema": "f1_shielding_standards/v1",
        "description": base.get("description")
        or "GBZ 130-2020 机房屏蔽铅当量标准要求（供表7「标准要求」列按设备类型预填）",
        "order": order,
        "items": items,
        "notes": base.get("notes")
        or {
            "source": "GBZ 130-2020《放射诊断放射防护要求》",
            "编辑说明": "类型键须与表7设备归类一致；保存后重新组稿/提取信息表时写入表7标准要求列。",
        },
    }


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


def _cn_section(n: int) -> str:
    digits = "零一二三四五六七八九"
    if n <= 0:
        return str(n)
    if n < 10:
        return digits[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + digits[n - 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        return digits[tens] + "十" + (digits[ones] if ones else "")
    return str(n)


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _normalize_location(loc: str) -> str:
    s = _norm_space(loc)
    s = (
        s.replace("一层", "一楼")
        .replace("二层", "二楼")
        .replace("三层", "三楼")
        .replace("四层", "四楼")
    )
    rules = [
        (r"^门诊楼", "门急诊楼"),
        (r"^发热门诊楼", "发热门诊"),
        (r"^住院楼二楼内镜中心", "医技楼二楼内镜中心"),
        (r"^住院楼二楼介入中心", "医技楼二楼介入中心"),
        (r"^住院楼四楼手术中心", "医技楼四楼手术中心"),
        (r"^医技楼一楼(?:影像中心)?$", "医技楼一楼影像中心"),
        (r"^医技楼一层(?:影像中心)?$", "医技楼一楼影像中心"),
    ]
    for pat, repl in rules:
        if re.match(pat, s):
            return re.sub(pat, repl, s)
    if s in {"医技楼一楼", "医技楼一层"}:
        return "医技楼一楼影像中心"
    if s.startswith("医技楼一层"):
        s = "医技楼一楼" + s[len("医技楼一层") :]
    return s


def _normalize_room_name(room: str) -> str:
    s = _norm_space(room)
    s = s.replace("C形臂", "C形臂").replace("C臂", "C形臂")
    s = re.sub(r"手术室(\d+)（C形臂机房(\d+)）", r"手术室\1（C形臂机房\2）", s)
    return s


def classify_shielding_device(room: str) -> str:
    s = _norm_space(room)
    if "CT" in s and "CBCT" not in s:
        return "ct"
    if "DSA" in s:
        return "dsa"
    if "ERCP" in s:
        return "ercp"
    if "C形臂" in s or "C臂" in s:
        return "carm"
    if "骨密度" in s:
        return "bmd"
    if "乳腺" in s:
        return "mammo"
    if "胃肠" in s:
        return "gastro"
    if "DR" in s:
        return "dr"
    return "other"


def _clean_cell(s: str) -> str:
    t = (s or "").replace("\r", "\n")
    t = re.sub(r"[ \t\u3000]+", "", t)
    t = re.sub(r"\n+", "", t)
    return t.strip()


def _format_material(raw: str) -> str:
    """墙体/顶板/地板材料描述规范化。"""
    s = _clean_cell(raw)
    if not s or s in {"/", "—", "-"}:
        return ""
    s = s.replace("硫酸钡防护层", "硫酸钡水泥防护涂层")
    s = s.replace("对应上层地面", "")
    s = re.sub(r"\++", "及", s)
    s = re.sub(r"及+", "及", s)
    # 50*30*1.5镀锌方管 → 保留
    return s


def _extract_lead_mm(raw: str) -> Optional[float]:
    s = _clean_cell(raw)
    if not s or s in {"/", "—", "-"}:
        return None
    # 优先「屏蔽厚度：x mmPb」
    m = re.search(r"屏蔽厚度[:：]?\s*([\d.]+)\s*mmPb", s, re.I)
    if m:
        return float(m.group(1))
    ms = re.findall(r"([\d.]+)\s*mmPb", s, re.I)
    if ms:
        return max(float(x) for x in ms)
    return None


def _format_lead_only(raw: str) -> str:
    v = _extract_lead_mm(raw)
    if v is None:
        s = _clean_cell(raw)
        return "" if s in {"/", "—", "-", ""} else s
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v))}.0mmPb"
    return f"{v:g}mmPb"


def _eq_for_base(material: str, eq_key: str) -> float:
    """混凝土/砖等基材等效铅当量；空心砖与镀锌方管忽略。"""
    s = _clean_cell(material)
    table = _EQ_TABLE.get(eq_key) or _EQ_TABLE["dr"]
    total = 0.0
    # 空心砖忽略
    if "空心砖" in s and "实心" not in s:
        pass
    else:
        mb = re.search(r"(\d+)\s*mm\s*实心红砖", s)
        if mb and int(mb.group(1)) >= 350:
            total += float(table.get("brick_370", 0))
        mc = re.search(r"(\d+)\s*mm\s*混凝土", s)
        if mc:
            th = int(mc.group(1))
            if th <= 115:
                total += float(table.get("concrete_110", 1.3))
            elif th <= 150:
                total += float(table.get("concrete_120", 1.4))
            else:
                total += float(table.get("concrete_180", 2.0))
    # 附加铅板 / 硫酸钡涂层（直接加 mmPb）
    for m in re.finditer(r"([\d.]+)\s*mmPb", s, re.I):
        total += float(m.group(1))
    return round(total, 1)


def _fmt_eq(v: float) -> str:
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v))}.0" if v < 10 else str(int(round(v)))
    return f"{v:g}"


def parse_shielding_design_from_pdf(pdf_path: str) -> List[Dict[str, Any]]:
    """从信息表《放射防护设施屏蔽设计情况表》解析各机房屏蔽字段。"""
    import fitz

    doc = fitz.open(pdf_path)
    rooms: List[Dict[str, Any]] = []
    pending_extra: Dict[str, str] = {}
    try:
        for page in doc:
            text = page.get_text() or ""
            if "放射防护设施屏蔽设计情况表" not in text and "墙体" not in text[:200]:
                # 续页：有表头关键词
                if not (re.search(r"控制室防护门", text) and re.search(r"机房防护门", text)):
                    continue
            tabs = page.find_tables()
            if not tabs or not tabs.tables:
                continue
            data = tabs.tables[0].extract()
            if not data or len(data) < 2:
                continue
            for row in data[1:]:
                if not row or len(row) < 9:
                    continue
                seq = _clean_cell(str(row[0] or ""))
                if not seq.isdigit():
                    # 续行：并入上一间机房（常见于观察窗/门跨页）
                    if rooms:
                        for i, key in enumerate(
                            ("window", "control_door", "room_door", "other_door")
                        ):
                            col = 6 + i
                            if col < len(row) and row[col]:
                                extra = _clean_cell(str(row[col]))
                                if extra:
                                    rooms[-1][key] = (rooms[-1].get(key) or "") + extra
                    else:
                        for i, key in enumerate(
                            ("window", "control_door", "room_door", "other_door")
                        ):
                            col = 6 + i
                            if col < len(row) and row[col]:
                                pending_extra[key] = _clean_cell(str(row[col]))
                    continue
                loc = _normalize_location(str(row[1] or ""))
                room = _normalize_room_name(str(row[2] or ""))
                item = {
                    "seq": int(seq),
                    "location": loc,
                    "room": room,
                    "wall": _format_material(str(row[3] or "")),
                    "ceiling": _format_material(str(row[4] or "")),
                    "floor": _format_material(str(row[5] or "")),
                    "window": _clean_cell(str(row[6] or "")),
                    "control_door": _clean_cell(str(row[7] or "")),
                    "room_door": _clean_cell(str(row[8] or "")),
                    "other_door": _clean_cell(str(row[9] or "")) if len(row) > 9 else "",
                    "device_class": classify_shielding_device(room),
                }
                if pending_extra:
                    for k, v in pending_extra.items():
                        if v:
                            item[k] = (item.get(k) or "") + v
                    pending_extra = {}
                rooms.append(item)
    finally:
        doc.close()
    rooms.sort(key=lambda r: int(r.get("seq") or 0))
    return rooms


def _merge_key(room: Dict[str, Any]) -> Tuple[str, ...]:
    """同位置 + 屏蔽数据 + 设备类相同才合并（不同楼栋/楼层不合并）。"""
    return (
        room.get("location") or "",
        room.get("wall") or "",
        room.get("ceiling") or "",
        room.get("floor") or "",
        room.get("window") or "",
        room.get("control_door") or "",
        room.get("room_door") or "",
        room.get("other_door") or "",
        room.get("device_class") or "",
    )


def _group_title(rooms: List[Dict[str, Any]]) -> str:
    """同位置合并：医技楼二楼介入中心DSA机房1和DSA机房2。"""
    if not rooms:
        return ""
    loc = rooms[0].get("location") or ""
    names = [r.get("room") or "" for r in rooms]
    if len(names) == 1:
        return f"{loc}{names[0]}"
    return f"{loc}{'和'.join(names)}"


def _body_rows_for_room(
    room: Dict[str, Any],
) -> List[Tuple[str, str, str]]:
    """返回 (屏蔽体, 材料及厚度, 总等效铅当量)。"""
    cls = room.get("device_class") or "other"
    _, eq_key = device_std_for_class(cls)
    rows: List[Tuple[str, str, str]] = []

    wall = room.get("wall") or ""
    if wall:
        rows.append(("四周墙体", wall, _fmt_eq(_eq_for_base(wall, eq_key))))

    win = room.get("window") or ""
    win_mat = _format_lead_only(win) if "mmPb" in win or "屏蔽" in win else _format_material(win)
    if not win_mat and win and win not in {"/", "—"}:
        win_mat = win
    if win_mat:
        lead = _extract_lead_mm(win)
        rows.append(("观察窗", win_mat, _fmt_eq(lead) if lead is not None else ""))

    door = room.get("room_door") or ""
    door_mat = _format_lead_only(door)
    if door_mat:
        lead = _extract_lead_mm(door)
        rows.append(("机房防护门", door_mat, _fmt_eq(lead) if lead is not None else ""))

    ctrl = room.get("control_door") or ""
    ctrl_mat = _format_lead_only(ctrl)
    if ctrl_mat:
        lead = _extract_lead_mm(ctrl)
        rows.append(("控制室防护门", ctrl_mat, _fmt_eq(lead) if lead is not None else ""))

    other = room.get("other_door") or ""
    if other and other not in {"/", "—", "-"}:
        other_mat = _format_lead_only(other) or other
        lead = _extract_lead_mm(other)
        rows.append(("其他防护门", other_mat, _fmt_eq(lead) if lead is not None else ""))

    ceil = room.get("ceiling") or ""
    if ceil:
        rows.append(("顶板", ceil, _fmt_eq(_eq_for_base(ceil, eq_key))))

    floor = room.get("floor") or ""
    if floor:
        rows.append(("地板", floor, _fmt_eq(_eq_for_base(floor, eq_key))))

    return rows


def _std_min_pb(std_text: str) -> float:
    ms = re.findall(r"([\d.]+)\s*mmPb", std_text or "")
    if not ms:
        return 0.0
    return min(float(x) for x in ms)


def _eval_cell(lead_text: str, std_text: str) -> str:
    try:
        lead = float(lead_text)
    except Exception:
        return "符合"
    need = _std_min_pb(std_text)
    if need <= 0:
        return "符合"
    return "符合" if lead + 1e-6 >= need else "不符合"


def build_table7(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    shielding_rooms: Optional[Sequence[Dict[str, Any]]] = None,
    pdf_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    表7：按信息表屏蔽设计组稿；屏蔽数据完全一致且设备类相同的机房合并。
    表头注释 ①② 使用 ^①^ 上标标记。
    """
    rooms: List[Dict[str, Any]] = list(shielding_rooms or [])
    if not rooms and pdf_path:
        try:
            rooms = parse_shielding_design_from_pdf(pdf_path)
        except Exception:
            rooms = []

    header0 = {
        "band": 0,
        "cells": [
            _cell(0, 1, "屏蔽体", row_span=2, align="center"),
            _cell(1, 3, "屏蔽设计^①^", align="center"),
            _cell(3, 4, "标准要求\n(GBZ130-2020)", row_span=2, align="center"),
            _cell(4, 5, "评价", row_span=2, align="center"),
        ],
    }
    header1 = {
        "band": 1,
        "cells": [
            _cell(1, 2, "屏蔽设施材料及厚度", align="center"),
            _cell(2, 3, "总等效铅当量厚度(mmPb)^②^", align="center"),
        ],
    }

    bands: List[Dict[str, Any]] = [header0, header1]
    band_i = 2

    if not rooms:
        # 无信息表时保留可编辑骨架一行节标题
        bands.append(
            {
                "band": band_i,
                "cells": [_cell(0, 5, "一、（请提取信息表后自动填充各机房屏蔽设计）")],
            }
        )
        band_i += 1
    else:
        groups: "OrderedDict[Tuple[str, ...], List[Dict[str, Any]]]" = OrderedDict()
        for r in rooms:
            groups.setdefault(_merge_key(r), []).append(r)

        for gi, (_key, grp) in enumerate(groups.items(), start=1):
            title = f"{_cn_section(gi)}、{_group_title(grp)}"
            sample = grp[0]
            body = _body_rows_for_room(sample)
            cls = sample.get("device_class") or "other"
            std_text, _ = device_std_for_class(cls)
            n_body = len(body) or 1
            if not body:
                body = [("四周墙体", "", "")]
                n_body = 1

            bands.append(
                {
                    "band": band_i,
                    "cells": [_cell(0, 5, title)],
                    "device_class": cls,
                    "section_kind": "room_group",
                }
            )
            band_i += 1

            for bi, (name, mat, lead) in enumerate(body):
                cells = [
                    _cell(0, 1, name, align="left"),
                    _cell(1, 2, mat, align="left"),
                    _cell(2, 3, lead, align="center"),
                ]
                if bi == 0:
                    sc = _cell(3, 4, std_text, row_span=n_body, align="center")
                    sc["device_class"] = cls
                    cells.append(sc)
                cells.append(
                    _cell(4, 5, _eval_cell(lead, std_text), align="center")
                )
                bands.append({"band": band_i, "cells": cells})
                band_i += 1

    bands.append(
        {
            "band": band_i,
            "cells": [_cell(0, 5, TABLE7_NOTE, align="left")],
        }
    )
    bands[-1]["cells"][0]["first_indent"] = True
    band_i += 1

    room_count = count_xray_rooms(list(devices or [])) or len(rooms) or 0
    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE7_TITLE,
        "source": "shielding_facilities",
        "from_info_sheet": bool(rooms),
        "room_count": room_count,
        "column_count": 5,
        "column_fractions": [0.0, 0.14, 0.42, 0.58, 0.78, 1.0],
        "band_count": band_i,
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "note": "表7由信息表屏蔽设计组稿；同屏蔽同设备类机房已合并；①②为上标标记 ^①^",
        "rows": bands,
    }


def assemble_shielding_facilities(
    devices: Optional[List[Dict[str, Any]]] = None,
    *,
    include_table: bool = True,
    pdf_path: Optional[str] = None,
    shielding_rooms: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    返回:
      shielding: 导语 + <<<F1_TABLE>>> + 表后总结
      shielding_tables: [表7]
      shielding_rooms: 解析结果（可选）
    """
    room_count = count_xray_rooms(devices) or 1
    rooms = list(shielding_rooms or [])
    if not rooms and pdf_path:
        try:
            rooms = parse_shielding_design_from_pdf(pdf_path)
        except Exception:
            rooms = []
    if rooms:
        room_count = max(room_count, len(rooms))

    intro = _fill(
        _load_fixed("protection_measures/shielding_intro.md", DEFAULT_INTRO),
        room_count=room_count,
    )
    after = _fill(
        _load_fixed("protection_measures/shielding_after.md", DEFAULT_AFTER),
        room_count=room_count,
    )
    # 常用模板里若已是填好的数字，避免被 {room_count} 模板再次替换；此处 _fill 已处理
    text = f"{intro}\n\n{TABLE_MARK}\n\n{after}".strip() + "\n"
    out: Dict[str, Any] = {
        "shielding": text,
        "room_count": room_count,
        "shielding_rooms": rooms,
    }
    if include_table:
        out["shielding_tables"] = [
            build_table7(devices, shielding_rooms=rooms, pdf_path=pdf_path)
        ]
    else:
        out["shielding_tables"] = []
    return out
