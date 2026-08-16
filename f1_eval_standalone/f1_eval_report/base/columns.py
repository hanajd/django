"""基础表格列宽（与 GBZ/T 表 F.1 / 250075YP 样例 PDF 实测一致，可整体缩放 / 按宽度覆盖）。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

MM_TO_PT = 72.0 / 25.4

# 模板实测表宽（gbzt_f1_ncu2_250075YP.pdf 表外框）
F1_OLD_TABLE_LEFT = 79.37
F1_OLD_TABLE_RIGHT = 515.93

# 小四宋体汉字宽 ≈ 字号；单元格左右各留 2pt 内边距
_CHAR_W = 12.0
_CELL_PAD = 2.0
_W5 = 5 * _CHAR_W + 2 * _CELL_PAD  # 64：可容纳五个汉字
_W4 = 4 * _CHAR_W + 2 * _CELL_PAD  # 52：可容纳四个汉字
_W2 = 2 * _CHAR_W + 2 * _CELL_PAD  # 28：可容纳两个汉字
_W2_LOOSE = _W2 + 4.0  # 半宽表头略留余量，避免贴边

# 右栏「负责人/邮编/传真」（PDF：380.07–431.88–515.93）
_PAIR_X0 = 380.07
_PAIR_LABEL_X1 = 431.88

_MAIN_X0 = F1_OLD_TABLE_LEFT
_MAIN_X1 = 143.14  # 主表头右界（单位名称/项目名称等）

# 联系人行：值格 → 电话标签 → 电话值（至负责人栏）
_CONTACT_X1 = 206.91
_PHONE_LABEL_X1 = 234.81

# 项目用途/项目性质 中线；投资额/建设面积
_MID_X0 = 318.87
_INVEST_LABEL_X1 = 361.42
_AREA_LABEL_X1 = 459.31

# 辐射源项起半宽表头：主/子同宽，均按两字换行（wrap_chars=2）
# PDF 半宽竖线 ≈ 111.25
_HALF_MAIN_X1 = _MAIN_X0 + _W2_LOOSE  # 111.37
_HALF_SUB_W = _W2_LOOSE
_HALF_SUB_X1 = _HALF_MAIN_X1 + _HALF_SUB_W

F1_COLUMNS: Dict[str, Dict[str, float]] = {
    "label_main": {"x0": _MAIN_X0, "x1": _MAIN_X1},
    "value_left": {"x0": _MAIN_X1, "x1": _MID_X0},
    "value_left_to_pair": {"x0": _MAIN_X1, "x1": _PAIR_X0},
    "value_contact": {"x0": _MAIN_X1, "x1": _CONTACT_X1},
    "label_phone": {"x0": _CONTACT_X1, "x1": _PHONE_LABEL_X1},
    "value_phone": {"x0": _PHONE_LABEL_X1, "x1": _MID_X0},
    "value_phone_to_pair": {"x0": _PHONE_LABEL_X1, "x1": _PAIR_X0},
    "label_pair": {"x0": _PAIR_X0, "x1": _PAIR_LABEL_X1},
    "value_pair": {"x0": _PAIR_LABEL_X1, "x1": F1_OLD_TABLE_RIGHT},
    # 项目用途右侧「计划配备人员」表头通到值格左界
    "label_mid": {"x0": _MID_X0, "x1": _PAIR_LABEL_X1},
    "value_right": {"x0": _PAIR_LABEL_X1, "x1": F1_OLD_TABLE_RIGHT},
    "value_full": {"x0": _MAIN_X1, "x1": F1_OLD_TABLE_RIGHT},
    "sub_label_left": {"x0": _MAIN_X1, "x1": 254.35},
    "sub_label": {"x0": 254.35, "x1": _MID_X0},
    "value_device": {"x0": _MID_X0, "x1": F1_OLD_TABLE_RIGHT},
    "sub_value_merge": {"x0": 254.35, "x1": F1_OLD_TABLE_RIGHT},
    "sub_label_row": {"x0": _MAIN_X1, "x1": 274.65},
    "value_wide": {"x0": 274.65, "x1": F1_OLD_TABLE_RIGHT},
    "label_invest": {"x0": _MID_X0, "x1": _INVEST_LABEL_X1},
    "value_invest": {"x0": _INVEST_LABEL_X1, "x1": _PAIR_LABEL_X1},
    "label_area": {"x0": _PAIR_LABEL_X1, "x1": _AREA_LABEL_X1},
    "value_area": {"x0": _AREA_LABEL_X1, "x1": F1_OLD_TABLE_RIGHT},
    # 自「辐射源项」起的更窄主/子表头
    "label_main_half": {"x0": _MAIN_X0, "x1": _HALF_MAIN_X1},
    "value_full_half": {"x0": _HALF_MAIN_X1, "x1": F1_OLD_TABLE_RIGHT},
    "sub_label_row_half": {"x0": _HALF_MAIN_X1, "x1": _HALF_SUB_X1},
    "value_wide_half": {"x0": _HALF_SUB_X1, "x1": F1_OLD_TABLE_RIGHT},
}

F1_VALUE_CENTER: Dict[str, Any] = {"mode": "center"}

# UI 可调列宽（mm）：影响主表骨架与 PDF 列线
EDITABLE_COLUMN_KEYS = [
    ("label_main", "主表头宽"),
    ("label_main_half", "半宽主表头"),
    ("sub_label_row_half", "半宽子表头"),
    ("label_mid", "中间表头宽"),
    ("label_pair", "右侧表头宽"),
    ("value_contact", "联系人值格"),
    ("label_phone", "电话表头宽"),
    ("label_invest", "投资额表头"),
    ("value_invest", "投资额值格"),
    ("label_area", "建设面积表头"),
]


def _col_w(col: Dict[str, float]) -> float:
    return float(col["x1"]) - float(col["x0"])


def default_column_widths_mm() -> Dict[str, float]:
    """从模板绝对坐标导出默认可编辑列宽（mm）。"""
    out: Dict[str, float] = {}
    for key, _label in EDITABLE_COLUMN_KEYS:
        out[key] = round(_col_w(F1_COLUMNS[key]) / MM_TO_PT, 2)
    # 中间区分界：左区终点相对表左（用于 pairs / grid）
    out["mid_offset"] = round((_MID_X0 - F1_OLD_TABLE_LEFT) / MM_TO_PT, 2)
    out["pair_offset"] = round((_PAIR_X0 - F1_OLD_TABLE_LEFT) / MM_TO_PT, 2)
    return out


def _mm(widths: Dict[str, float], key: str, fallback_pt: float) -> float:
    if key in widths and widths[key] is not None:
        try:
            return max(4.0, float(widths[key]) * MM_TO_PT)
        except (TypeError, ValueError):
            pass
    return float(fallback_pt)


def rebuild_columns(
    table_left: float,
    table_right: float,
    widths_mm: Optional[Dict[str, float]] = None,
) -> Dict[str, Dict[str, float]]:
    """
    按可编辑列宽重建 F.1 列坐标。
    widths_mm 缺省时等价于默认模板比例缩放到 [table_left, table_right]。
    """
    widths = widths_mm or {}
    # 若未提供任何覆盖，走整体等比缩放（保持旧行为）
    if not widths:
        old_w = F1_OLD_TABLE_RIGHT - F1_OLD_TABLE_LEFT
        new_w = table_right - table_left
        return {
            name: {
                "x0": round(table_left + (col["x0"] - F1_OLD_TABLE_LEFT) / old_w * new_w, 2),
                "x1": round(table_left + (col["x1"] - F1_OLD_TABLE_LEFT) / old_w * new_w, 2),
            }
            for name, col in F1_COLUMNS.items()
        }

    L, R = float(table_left), float(table_right)
    table_w = max(40.0, R - L)

    w_main = _mm(widths, "label_main", _MAIN_X1 - _MAIN_X0)
    w_half = _mm(widths, "label_main_half", _W2_LOOSE)
    # 半宽子表头与半宽主表头同宽（两字列）；若单独传入则尊重，否则跟主表头
    if "sub_label_row_half" in widths and widths.get("sub_label_row_half") is not None:
        w_half_sub = _mm(widths, "sub_label_row_half", w_half)
    else:
        w_half_sub = w_half
    w_mid = _mm(widths, "label_mid", _PAIR_LABEL_X1 - _MID_X0)
    w_pair = _mm(widths, "label_pair", _PAIR_LABEL_X1 - _PAIR_X0)
    w_contact = _mm(widths, "value_contact", _CONTACT_X1 - _MAIN_X1)
    w_phone = _mm(widths, "label_phone", _PHONE_LABEL_X1 - _CONTACT_X1)
    w_invest = _mm(widths, "label_invest", _INVEST_LABEL_X1 - _MID_X0)
    w_invest_val = _mm(widths, "value_invest", _PAIR_LABEL_X1 - _INVEST_LABEL_X1)
    w_area = _mm(widths, "label_area", _AREA_LABEL_X1 - _PAIR_LABEL_X1)

    mid_off = _mm(widths, "mid_offset", _MID_X0 - F1_OLD_TABLE_LEFT)
    pair_off = _mm(widths, "pair_offset", _PAIR_X0 - F1_OLD_TABLE_LEFT)

    main_x1 = L + w_main
    half_x1 = L + w_half
    half_sub_x1 = half_x1 + w_half_sub
    mid_x0 = min(L + mid_off, R - w_mid - 20)
    mid_x0 = max(mid_x0, main_x1 + 40)
    mid_x1 = min(mid_x0 + w_mid, R - 20)
    pair_x0 = min(L + pair_off, R - w_pair - 30)
    pair_x0 = max(pair_x0, main_x1 + 60)
    pair_x1 = min(pair_x0 + w_pair, R - 20)

    contact_x1 = min(main_x1 + w_contact, mid_x0 - 4)
    phone_x1 = min(contact_x1 + w_phone, mid_x0 - 2)

    invest_x1 = min(mid_x0 + w_invest, R - 40)
    invest_val_x1 = min(invest_x1 + w_invest_val, R - 20)
    area_x1 = min(invest_val_x1 + w_area, R - 10)

    # 子表头（全宽段）
    sub_row_x1 = main_x1 + max(40.0, (mid_x0 - main_x1) * 0.55)
    sub_left_x1 = main_x1 + max(30.0, (mid_x0 - main_x1) * 0.4)

    def box(x0: float, x1: float) -> Dict[str, float]:
        return {"x0": round(max(L, x0), 2), "x1": round(min(R, max(x0 + 4, x1)), 2)}

    return {
        "label_main": box(L, main_x1),
        "value_left": box(main_x1, mid_x0),
        "value_left_to_pair": box(main_x1, pair_x0),
        "value_contact": box(main_x1, contact_x1),
        "label_phone": box(contact_x1, phone_x1),
        "value_phone": box(phone_x1, mid_x0),
        "value_phone_to_pair": box(phone_x1, pair_x0),
        "label_pair": box(pair_x0, pair_x1),
        "value_pair": box(pair_x1, R),
        "label_mid": box(mid_x0, mid_x1),
        "value_right": box(mid_x1, R),
        "value_full": box(main_x1, R),
        "sub_label_left": box(main_x1, sub_left_x1),
        "sub_label": box(sub_left_x1, mid_x0),
        "value_device": box(mid_x0, R),
        "sub_value_merge": box(sub_left_x1, R),
        "sub_label_row": box(main_x1, sub_row_x1),
        "value_wide": box(sub_row_x1, R),
        "label_invest": box(mid_x0, invest_x1),
        "value_invest": box(invest_x1, invest_val_x1),
        "label_area": box(invest_val_x1, area_x1),
        "value_area": box(area_x1, R),
        "label_main_half": box(L, half_x1),
        "value_full_half": box(half_x1, R),
        "sub_label_row_half": box(half_x1, half_sub_x1),
        "value_wide_half": box(half_sub_x1, R),
    }


def scale_columns_to_margins(
    page_width: float,
    margins_mm: Dict[str, float],
    widths_mm: Optional[Dict[str, float]] = None,
) -> Tuple[float, float, Dict[str, Dict[str, float]], Dict[str, float]]:
    ml = margins_mm["left"] * MM_TO_PT
    mr = margins_mm["right"] * MM_TO_PT
    mt = margins_mm["top"] * MM_TO_PT
    mb = margins_mm["bottom"] * MM_TO_PT
    table_left = ml
    table_right = page_width - mr
    columns = rebuild_columns(table_left, table_right, widths_mm)
    margins_pt = {
        "top": round(mt, 2),
        "bottom": round(mb, 2),
        "left": round(ml, 2),
        "right": round(mr, 2),
    }
    return table_left, table_right, columns, margins_pt
