# -*- coding: utf-8 -*-
"""防护设施和措施 · 个人防护用品：导语 + 表9 + 表10 + 表后总结。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from f1_eval_report.templates.protection_zoning import count_xray_rooms

TABLE_MARK = "<<<F1_TABLE>>>"

TABLE9_TITLE = "表9 个人防护用品和辅助设施配置要求"
TABLE10_TITLE = "表10 本项目个人防护用品和辅助设施配置计划"

DEFAULT_EVAL_BASIS = (
    "本项目C形臂工作人员拟通过脚踏式开关在C形臂机房外进行隔室设备曝光，"
    "其防护用品配备按照隔室操作要求进行评价，"
    "DSA机房及ERCP机房的防护用品按照介入放射学操作要求进行评价。"
)

DEFAULT_INTRO = (
    "根据建设单位提供的信息，{eval_basis}"
    "本项目{room_count}间X射线设备机房放射防护用品配备计划如表10所示，"
    "根据表9的配置要求，符合GBZ 130-2020《放射诊断放射防护要求》的要求。"
)

DEFAULT_AFTER = (
    "综上所述，本项目{room_count}间X射线设备机房个人防护用品配备计划"
    "符合GBZ 130-2020《放射诊断放射防护要求》的要求。"
)

# 表9：GBZ 130-2020 表4（报告称表9）中本项目常用类型
# (检查类型, 工作人员个防, 工作人员辅助, 受检者个防, 受检者辅助, 陪检者个防)
TABLE9_ROWS: List[Tuple[str, str, str, str, str, str]] = [
    (
        "放射诊断学用X射线设备隔室透视、摄影",
        "—（隔室操作，工作人员可不配备）\n注：工作人员、受检者的个人防护用品和辅助设施任选其一即可。",
        "—",
        "铅橡胶性腺防护围裙（方形）或方巾、铅橡胶颈套\n选配：铅橡胶帽子\n儿童：保护相应组织和器官的防护用品铅当量应不小于0.5mmPb",
        "可调节防护窗口的立位防护屏；\n选配：固定特殊受检者体位的各种设备",
        "对陪检者应至少配备铅橡胶防护衣（不小于0.25mmPb）",
    ),
    (
        "CT体层扫描（隔室）",
        "—（隔室操作，工作人员可不配备）",
        "—",
        "铅橡胶性腺防护围裙（方形）或方巾、铅橡胶颈套\n选配：铅橡胶帽子",
        "—",
        "对陪检者应至少配备铅橡胶防护衣（不小于0.25mmPb）",
    ),
    (
        "介入放射学操作",
        "铅橡胶围裙（不小于0.5mmPb）、铅橡胶颈套（不小于0.5mmPb）、"
        "铅防护眼镜（不小于0.25mmPb）、介入防护手套（不小于0.025mmPb）\n"
        "选配：铅橡胶帽子（不小于0.25mmPb）",
        "铅悬挂防护屏/铅防护吊帘、床侧防护帘/床侧防护屏\n选配：移动铅防护屏风",
        "成人：铅橡胶性腺防护围裙（方形）或方巾、铅橡胶颈套\n选配：铅橡胶帽子（不小于0.25mmPb）\n"
        "儿童：应为儿童的X射线检查配备保护相应组织和器官的防护用品，"
        "防护用品和辅助防护设施的铅当量应不小于0.5mmPb",
        "—",
        "对陪检者应至少配备铅橡胶防护衣（不小于0.25mmPb）",
    ),
]

TABLE9_NOTE = (
    "注：①“—”表示相关标准对此不作要求；"
    "②工作人员、受检者的个人防护用品和辅助设施任选其一即可；"
    "③除介入防护手套外，防护用品和辅助防护设施铅当量应不小于0.25mmPb；"
    "介入防护手套不小于0.025mmPb；甲状腺、性腺防护用品不小于0.5mmPb；"
    "移动铅防护屏风不小于2mmPb（GBZ 130-2020 第6.5条）。"
)

# 表10 默认配置计划（参照 NCU2 信息表「配备计划」）
# room: (机房名称块, 检查类型标签, items[])
# item: (类型, 工作人员规格, 工作人员数量或文案, 受检者规格, 受检者数量, 陪检者文案)
RoomItem = Tuple[str, str, str, str, str, str]
RoomPlan = Tuple[str, str, List[RoomItem]]

DEFAULT_TABLE10_ROOMS: List[RoomPlan] = [
    (
        "CT机房\n（4间）",
        "【隔室】",
        [
            ("铅橡胶帽子", "隔室操作，不配备", "", "0.5", "1", "0.5 / 1"),
            ("铅橡胶颈套", "", "", "0.5", "1", ""),
            ("铅方巾", "", "", "0.5", "1", ""),
            ("铅防护毯（包裹式）", "", "", "0.5", "1", ""),
            ("铅橡胶防护衣", "", "", "", "", "0.5 / 1"),
        ],
    ),
    (
        "DR机房\n（4间）",
        "【隔室摄影】",
        [
            ("铅橡胶帽子", "隔室操作，不配备", "", "0.5", "1", "0.5 / 1"),
            ("铅橡胶颈套", "", "", "0.5", "1", ""),
            ("铅方巾", "", "", "0.5", "1", ""),
            ("铅橡胶防护衣", "", "", "", "", "0.5 / 1"),
        ],
    ),
    (
        "全身骨密度仪机房\n（1间）",
        "【隔室透视】",
        [
            ("铅橡胶帽子", "隔室操作，不配备", "", "0.5", "1", "0.5 / 1"),
            ("铅橡胶颈套", "", "", "0.5", "1", ""),
            ("铅方巾", "", "", "0.5", "1", ""),
            ("铅橡胶防护衣", "", "", "", "", "0.5 / 1"),
        ],
    ),
    (
        "乳腺DR机房\n（1间）",
        "【隔室摄影】",
        [
            ("铅橡胶帽子", "隔室操作，不配备", "", "0.5", "1", "不允许陪检，不配备"),
            ("铅橡胶颈套", "", "", "0.5", "1", ""),
            ("铅方巾", "", "", "0.5", "1", ""),
            ("铅橡胶防护衣", "", "", "", "", ""),
        ],
    ),
    (
        "数字胃肠机房\n（1间）",
        "【隔室透视、隔室摄影】",
        [
            ("铅橡胶帽子", "隔室操作，不配备", "", "0.5", "1", "0.5 / 1"),
            ("铅橡胶颈套", "", "", "0.5", "1", ""),
            ("铅方巾", "", "", "0.5", "1", ""),
            ("铅橡胶防护衣", "", "", "", "", "0.5 / 1"),
        ],
    ),
    (
        "ERCP机房\n（1间）",
        "【介入放射学操作】",
        [
            ("分体式铅衣", "0.5", "4", "/", "/", "不允许陪检，不配备"),
            ("铅防护眼镜", "0.5", "4", "/", "/", ""),
            ("介入防护手套", "0.025", "2", "/", "/", ""),
            ("铅橡胶帽子", "0.5", "4", "0.5", "1", ""),
            ("铅橡胶颈套", "0.5", "4", "0.5", "1", ""),
            ("铅方巾", "/", "/", "0.5", "1", ""),
            ("铅橡胶防护衣", "/", "/", "/", "/", ""),
            ("移动铅防护屏风", "0.5", "1", "/", "/", ""),
            ("铅悬挂防护屏", "0.5", "1", "/", "/", ""),
            ("铅防护吊帘", "0.5", "1", "/", "/", ""),
        ],
    ),
    (
        "DSA机房\n（2间）",
        "【介入放射学操作】",
        [
            ("分体式铅衣", "0.5", "10", "/", "/", "不允许陪检，不配备"),
            ("铅防护眼镜", "0.5", "10", "/", "/", ""),
            ("介入防护手套", "0.025", "2", "/", "/", ""),
            ("铅橡胶帽子", "0.5", "10", "0.5", "1", ""),
            ("铅橡胶颈套", "0.5", "10", "0.5", "1", ""),
            ("铅方巾", "/", "/", "0.5", "1", ""),
            ("铅橡胶防护衣", "/", "/", "/", "/", ""),
            ("移动铅防护屏风", "0.5", "2", "/", "/", ""),
            ("铅悬挂防护屏", "0.5", "1", "/", "/", ""),
            ("铅防护吊帘", "0.5", "1", "/", "/", ""),
        ],
    ),
    (
        "C形臂机房\n（2间）",
        "【隔室透视、隔室摄影】",
        [
            ("铅橡胶帽子", "0.5", "1", "0.5", "1", "不允许陪检，不配备"),
            ("铅橡胶颈套", "0.5", "1", "0.5", "1", ""),
            ("铅方巾", "/", "/", "0.5", "1", ""),
            ("铅橡胶防护衣", "/", "/", "/", "/", ""),
        ],
    ),
]


def _load_fixed(rel: str, default: str = "") -> str:
    from f1_eval_report.templates.loader import load_common_text

    try:
        t = load_common_text(rel).strip()
        return t or default
    except Exception:
        return default


def _fill(tpl: str, *, room_count: int, eval_basis: str) -> str:
    n = str(max(1, int(room_count)))
    basis = str(eval_basis or "").strip()
    if basis and not basis.endswith(("。", "；", ";", ".")):
        basis = basis + "。"
    return (
        (tpl or "")
        .replace("{room_count}", n)
        .replace("{机房间数}", n)
        .replace("{eval_basis}", basis)
        .replace("{评价依据}", basis)
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


# ---------- 信息表「个人防护用品和辅助设施配备计划」解析 → 表10 ----------

PPE_ITEM_NAMES: List[str] = sorted(
    [
        "铅防护毯（包裹式）",
        "铅防护毯(包裹式)",
        "移动铅防护屏风",
        "铅悬挂防护屏",
        "铅防护吊帘",
        "分体式铅衣",
        "铅防护眼镜",
        "介入防护手套",
        "铅橡胶防护衣",
        "铅橡胶帽子",
        "铅橡胶颈套",
        "铅方巾",
    ],
    key=len,
    reverse=True,
)

_PPE_ACCOMPANY_RE = re.compile(r"不允许陪检|不配备|隔室操作")
_PPE_NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")
_PPE_ROOM_RE = re.compile(
    r"(?P<name>"
    r"CT\s*机房|DR\s*机房|乳腺\s*DR\s*机房|数字胃肠机房|ERCP\s*机房|DSA\s*机房|"
    r"C\s*形臂机房|全身骨密度仪机房|全身骨密度\s*仪机房"
    r")"
    r"\s*（\s*(?P<n>\d+)\s*间\s*）\s*"
    r"(?P<typ>【[^】]+】)",
    re.S,
)


def _clean_ppe_cell(s: Any) -> str:
    t = str(s or "").replace("\r", "").strip()
    t = t.replace("）", ")").replace("(", "（")  # normalize mixed
    t = re.sub(r"\s+", "", t)
    # restore common punctuation after strip spaces carefully
    return str(s or "").replace("\r", "").strip()


def _normalize_ppe_page_text(text: str) -> str:
    """合并信息表 OCR/提取造成的软换行。"""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    reps = [
        ("（放射检查类\n型）", "（放射检查类型）"),
        ("数量( 件/ 机\n房)", "数量(件/机房)"),
        ("【隔室摄\n影】", "【隔室摄影】"),
        ("【隔室透\n视】", "【隔室透视】"),
        ("【隔室透\n视、隔室摄\n影】", "【隔室透视、隔室摄影】"),
        ("【介入放射\n学操作】", "【介入放射学操作】"),
        ("全身骨密度\n仪机房", "全身骨密度仪机房"),
        ("CT 机房", "CT机房"),
        ("DR 机房", "DR机房"),
        ("乳腺DR 机房", "乳腺DR机房"),
        ("ERCP 机房", "ERCP机房"),
        ("DSA 机房", "DSA机房"),
        ("C 形臂机房", "C形臂机房"),
        ("移动铅防护屏风）", "移动铅防护屏风"),
        ("铅防护毯(包裹式)", "铅防护毯（包裹式）"),
    ]
    for a, b in reps:
        s = s.replace(a, b)
    s = re.sub(r"CT\s+机房", "CT机房", s)
    s = re.sub(r"DR\s+机房", "DR机房", s)
    s = re.sub(r"C\s*形臂", "C形臂", s)
    return s


def _is_ppe_item(tok: str) -> Optional[str]:
    t = tok.strip()
    if not t:
        return None
    t2 = t.replace("）", "").replace(")", "").strip()
    for name in PPE_ITEM_NAMES:
        n2 = name.replace("）", "").replace(")", "")
        if t == name or t2 == n2 or t.startswith(name) or name.startswith(t2):
            return name if name != "铅防护毯(包裹式)" else "铅防护毯（包裹式）"
    return None


def _fmt_room_label(name: str, n: str) -> str:
    name = re.sub(r"\s+", "", name)
    name = name.replace("全身骨密度仪机房", "全身骨密度仪机房")
    return f"{name}\n（{n}间）"


def _parse_item_tail(
    toks: List[str],
    *,
    exam_type: str,
    item_name: str,
    staff_ge_isolated: bool,
) -> Tuple[str, str, str, str, str, int]:
    """
    解析用品名之后的 token，返回
    (w_spec, w_qty, p_spec, p_qty, accompany, consumed).
    """
    i = 0
    n = len(toks)
    # skip trailing noise
    while i < n and toks[i] in {"新增", "备注", "/", "—", "-"}:
        i += 1
    if i >= n:
        return "", "", "", "", "", i

    accompany = ""
    # 隔室操作，不配备
    if "隔室操作" in toks[i] and "不配备" in toks[i]:
        w_spec, w_qty = toks[i], ""
        i += 1
        # optional patient nums
        p_spec = p_qty = ""
        if i < n and _PPE_NUM_RE.match(toks[i] or ""):
            p_spec = toks[i]
            i += 1
            if i < n and _PPE_NUM_RE.match(toks[i] or ""):
                p_qty = toks[i]
                i += 1
        if i < n and ("不允许陪检" in toks[i] or ("不配备" in toks[i] and "隔室" not in toks[i])):
            accompany = toks[i]
            i += 1
        while i < n and toks[i] == "新增":
            i += 1
        # 铅橡胶防护衣在隔室场景常落在陪检者列
        if item_name == "铅橡胶防护衣" and p_spec and not accompany:
            accompany = f"{p_spec} / {p_qty}" if p_qty else p_spec
            p_spec = p_qty = ""
        return w_spec, w_qty, p_spec, p_qty, accompany, i

    nums: List[str] = []
    while i < n:
        if toks[i] == "新增":
            i += 1
            break
        if _is_ppe_item(toks[i]):
            break
        if "不允许陪检" in toks[i] or (
            "不配备" in toks[i] and "隔室操作" not in toks[i]
        ):
            accompany = toks[i]
            i += 1
            continue
        if _PPE_NUM_RE.match(toks[i] or ""):
            nums.append(toks[i])
            i += 1
            continue
        # unknown token — stop
        break
    while i < n and toks[i] == "新增":
        i += 1

    w_spec = w_qty = p_spec = p_qty = ""
    is_aux = item_name in {"移动铅防护屏风", "铅悬挂防护屏", "铅防护吊帘"}

    if len(nums) >= 4:
        w_spec, w_qty, p_spec, p_qty = nums[0], nums[1], nums[2], nums[3]
    elif len(nums) == 3:
        w_spec, w_qty, p_spec = nums[0], nums[1], nums[2]
    elif len(nums) == 2:
        if item_name in {"铅方巾", "铅防护毯（包裹式）"}:
            p_spec, p_qty = nums[0], nums[1]
        elif item_name == "铅橡胶防护衣" and (
            staff_ge_isolated or "隔室" in exam_type
        ):
            accompany = f"{nums[0]} / {nums[1]}"
        elif staff_ge_isolated and not is_aux:
            # 隔室续行：数量落在受检者列
            p_spec, p_qty = nums[0], nums[1]
        else:
            w_spec, w_qty = nums[0], nums[1]
    elif len(nums) == 1:
        w_spec = nums[0]

    return w_spec, w_qty, p_spec, p_qty, accompany, i


def parse_ppe_plan_from_text(text: str) -> List[RoomPlan]:
    """从信息表页文本解析配备计划（find_tables 失败时的回退）。"""
    s = _normalize_ppe_page_text(text)
    if "个人防护用品和辅助设施配备计划" not in s and "个人防护用品" not in s:
        return []
    matches = list(_PPE_ROOM_RE.finditer(s))
    if not matches:
        return []
    plans: List[RoomPlan] = []
    for idx, m in enumerate(matches):
        name = re.sub(r"\s+", "", m.group("name"))
        n = m.group("n")
        typ = m.group("typ").replace("\n", "")
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(s)
        body = s[start:end]
        # tokenize by lines
        raw_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        toks: List[str] = []
        for ln in raw_lines:
            if ln in {"类型", "工作人员", "受检者/患者", "陪检者", "备注", "mmPb", "数量", "规格"}:
                continue
            if ln.startswith("数量") or ln.startswith("（规格") or ln.startswith("(规格"):
                continue
            toks.append(ln)

        items: List[RoomItem] = []
        staff_isolated = False
        room_accompany = ""
        i = 0
        while i < len(toks):
            item_name = _is_ppe_item(toks[i])
            if not item_name:
                i += 1
                continue
            i += 1
            w_spec, w_qty, p_spec, p_qty, accompany, consumed = _parse_item_tail(
                toks[i:],
                exam_type=typ,
                item_name=item_name,
                staff_ge_isolated=staff_isolated,
            )
            i += consumed
            if w_spec and "隔室操作" in w_spec:
                staff_isolated = True
            if accompany and not room_accompany and (
                "陪检" in accompany or ("不配备" in accompany and "/" not in accompany)
            ):
                room_accompany = accompany
            items.append((item_name, w_spec, w_qty, p_spec, p_qty, accompany))

        if items:
            # 机房级陪检说明（如不允许陪检）补到首行，便于表内 rowspan
            if room_accompany and not items[0][5]:
                first = items[0]
                items[0] = (
                    first[0],
                    first[1],
                    first[2],
                    first[3],
                    first[4],
                    room_accompany,
                )
            plans.append((_fmt_room_label(name, n), typ, items))
    return plans


def _parse_ppe_plan_from_find_tables(pdf_path: str) -> List[RoomPlan]:
    """优先用 PyMuPDF find_tables 解析配备计划表。"""
    import fitz

    doc = fitz.open(pdf_path)
    plans: List[RoomPlan] = []
    try:
        for page in doc:
            text = page.get_text() or ""
            if "个人防护用品和辅助设施配备计划" not in text and "配备计划" not in text:
                if not plans:
                    continue
                # 可能续页
                if "铅橡胶" not in text and "分体式铅衣" not in text:
                    continue
            tabs = page.find_tables()
            if not tabs or not tabs.tables:
                continue
            for tab in tabs.tables:
                data = tab.extract()
                if not data or len(data) < 3:
                    continue
                # 找含「类型」的表头行
                header_i = 0
                for ri, row in enumerate(data[:4]):
                    joined = "".join(str(c or "") for c in row)
                    if "类型" in joined and ("工作" in joined or "mmPb" in joined or "受检" in joined):
                        header_i = ri
                        break
                cur_name = ""
                cur_typ = ""
                cur_items: List[RoomItem] = []
                cur_acc = ""

                def flush() -> None:
                    nonlocal cur_name, cur_typ, cur_items, cur_acc
                    if cur_name and cur_items:
                        if cur_acc and not cur_items[0][5]:
                            a = cur_items[0]
                            cur_items[0] = (a[0], a[1], a[2], a[3], a[4], cur_acc)
                        m = re.search(r"（\s*(\d+)\s*间\s*）", cur_name)
                        n = m.group(1) if m else "1"
                        name_only = re.sub(r"（\s*\d+\s*间\s*）", "", cur_name).strip()
                        name_only = re.sub(r"\s+", "", name_only)
                        typ = cur_typ or "【—】"
                        if not typ.startswith("【"):
                            typ = f"【{typ}】"
                        plans.append((_fmt_room_label(name_only, n), typ, list(cur_items)))
                    cur_name, cur_typ, cur_items, cur_acc = "", "", [], ""

                for row in data[header_i + 1 :]:
                    cells = [_clean_ppe_cell(c) for c in (row or [])]
                    if not any(cells):
                        continue
                    joined = "".join(cells)
                    if "机房名称" in joined or joined.startswith("mmPb"):
                        continue
                    # column guess: 0 room, 1 type, 2-3 staff, 4-5 patient, 6 accompany
                    c0 = cells[0] if cells else ""
                    c1 = cells[1] if len(cells) > 1 else ""
                    # expand short rows
                    while len(cells) < 7:
                        cells.append("")

                    room_hit = re.search(
                        r"((?:CT|DR|乳腺DR|数字胃肠|ERCP|DSA|C形臂|全身骨密度仪)[^\n]{0,12}机房)",
                        re.sub(r"\s+", "", c0 + c1),
                    )
                    typ_hit = re.search(r"【[^】]+】", c0 + c1 + (cells[2] if len(cells) > 2 else ""))
                    item = _is_ppe_item(c1) or _is_ppe_item(c0)

                    if room_hit and (typ_hit or "间" in c0):
                        if cur_items:
                            flush()
                        block = re.sub(r"\s+", "", c0)
                        nm = re.search(
                            r"((?:CT|DR|乳腺DR|数字胃肠|ERCP|DSA|C形臂|全身骨密度仪)[^【（]{0,12}机房)",
                            block,
                        )
                        nn = re.search(r"（\s*(\d+)\s*间\s*）", block)
                        cur_name = f"{nm.group(1) if nm else block}（{nn.group(1) if nn else '1'}间）"
                        cur_typ = typ_hit.group(0) if typ_hit else ""
                        # item may be on same row
                        item = _is_ppe_item(c1)
                        if not item:
                            continue

                    if not item:
                        # maybe type col is item when room merged
                        item = _is_ppe_item(c1) or _is_ppe_item(cells[1] if len(cells) > 1 else "")
                    if not item:
                        continue

                    # map columns
                    w_a, w_b = cells[2], cells[3]
                    p_a, p_b = cells[4], cells[5]
                    acc = cells[6] if len(cells) > 6 else ""
                    # merged staff "隔室操作，不配备" may sit in c2 spanning
                    if w_a and "隔室操作" in w_a and not w_b:
                        w_spec, w_qty = w_a, ""
                    elif w_a and "不配备" in w_a and not _PPE_NUM_RE.match(w_a):
                        w_spec, w_qty = w_a, w_b
                    else:
                        w_spec, w_qty = w_a, w_b
                    if acc and ("陪检" in acc or "不配备" in acc or _PPE_NUM_RE.match(acc.split("/")[0].strip() if "/" in acc else "")):
                        if not cur_acc:
                            cur_acc = acc
                    elif acc and not cur_acc and len(cur_items) == 0:
                        cur_acc = acc
                    cur_items.append(
                        (
                            item,
                            w_spec,
                            w_qty,
                            p_a,
                            p_b,
                            acc if len(cur_items) == 0 else "",
                        )
                    )
                flush()
    finally:
        doc.close()
    return plans


def parse_ppe_plan_from_pdf(pdf_path: str) -> List[RoomPlan]:
    """从评价信息表 PDF 解析「个人防护用品和辅助设施配备计划」→ RoomPlan 列表。"""
    plans: List[RoomPlan] = []
    try:
        plans = _parse_ppe_plan_from_find_tables(pdf_path)
    except Exception:
        plans = []
    if plans:
        return plans
    # 回退：全文按页取文本
    try:
        import fitz

        doc = fitz.open(pdf_path)
        try:
            chunks: List[str] = []
            for page in doc:
                t = page.get_text() or ""
                if "个人防护用品和辅助设施配备计划" in t or "铅橡胶帽子" in t:
                    chunks.append(t)
            if chunks:
                plans = parse_ppe_plan_from_text("\n".join(chunks))
        finally:
            doc.close()
    except Exception:
        plans = []
    return plans


def build_table9() -> Dict[str, Any]:
    """表9：GBZ 130 配置要求（固定）。"""
    rows: List[Dict[str, Any]] = [
        {
            "band": 0,
            "cells": [
                _cell(0, 1, "放射检查类型", align="center"),
                _cell(1, 6, "标准要求（GBZ 130-2020）6.5", align="center"),
            ],
        },
        {
            "band": 1,
            "cells": [
                _cell(0, 1, "", align="center"),
                _cell(1, 3, "工作人员", align="center"),
                _cell(3, 5, "受检者", align="center"),
                _cell(5, 6, "陪检者", align="center"),
            ],
        },
        {
            "band": 2,
            "cells": [
                _cell(0, 1, "", align="center"),
                _cell(1, 2, "个人防护用品", align="center"),
                _cell(2, 3, "辅助防护设施", align="center"),
                _cell(3, 4, "个人防护用品", align="center"),
                _cell(4, 5, "辅助防护设施", align="center"),
                _cell(5, 6, "个人防护用品", align="center"),
            ],
        },
    ]
    for i, (typ, w_ppe, w_aux, p_ppe, p_aux, a_ppe) in enumerate(TABLE9_ROWS, start=3):
        rows.append(
            {
                "band": i,
                "cells": [
                    _cell(0, 1, typ, align="left"),
                    _cell(1, 2, w_ppe, align="left"),
                    _cell(2, 3, w_aux, align="left"),
                    _cell(3, 4, p_ppe, align="left"),
                    _cell(4, 5, p_aux, align="left"),
                    _cell(5, 6, a_ppe, align="left"),
                ],
            }
        )
    rows.append(
        {
            "band": len(rows),
            "cells": [_cell(0, 6, TABLE9_NOTE, align="left")],
        }
    )
    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE9_TITLE,
        "source": "personal_protective_equipment",
        "from_info_sheet": False,
        "column_count": 6,
        "column_fractions": [0.0, 0.16, 0.36, 0.52, 0.72, 0.86, 1.0],
        "band_count": len(rows),
        "row_min_height_pt": 20.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 3,
        "note": "表9为 GBZ 130-2020 表4 配置要求，可在常用模板中编辑",
        "rows": rows,
    }


def build_table10(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
    room_plans: Optional[Sequence[RoomPlan]] = None,
    *,
    from_info_sheet: bool = False,
    pdf_path: Optional[str] = None,
) -> Dict[str, Any]:
    """表10：本项目配置计划。优先用信息表解析结果，否则用默认骨架。"""
    plans: List[RoomPlan] = list(room_plans or [])
    sourced = bool(plans)
    if not plans and pdf_path:
        try:
            plans = parse_ppe_plan_from_pdf(pdf_path)
            sourced = bool(plans)
        except Exception:
            plans = []
    if not plans:
        plans = list(DEFAULT_TABLE10_ROOMS)
        sourced = False

    room_count = count_xray_rooms(list(devices or [])) or 0
    total = 0
    for name, _, _ in plans:
        m = re.search(r"（\s*(\d+)\s*间\s*）", name.replace(" ", ""))
        if m:
            total += int(m.group(1))
    if total > 0:
        room_count = total
    elif not room_count:
        room_count = len(plans) or 1

    rows: List[Dict[str, Any]] = [
        {
            "band": 0,
            "cells": [
                _cell(0, 1, "机房名称\n（机房数量）\n（放射检查类型）", align="center"),
                _cell(1, 2, "类型", align="center"),
                _cell(2, 4, "工作人员", align="center"),
                _cell(4, 6, "受检者/患者", align="center"),
                _cell(6, 7, "陪检者", align="center"),
            ],
        },
        {
            "band": 1,
            "cells": [
                _cell(0, 1, "", align="center"),
                _cell(1, 2, "", align="center"),
                _cell(2, 3, "（规格）mmPb", align="center"),
                _cell(3, 4, "数量\n(件/机房)", align="center"),
                _cell(4, 5, "（规格）mmPb", align="center"),
                _cell(5, 6, "数量\n(件/机房)", align="center"),
                _cell(6, 7, "规格 / 数量(件/机房)", align="center"),
            ],
        },
    ]

    band = 2
    for room_name, exam_type, items in plans:
        n_items = max(1, len(items))
        # 若仅首行有陪检者文案，则整机房 rowspan；否则逐行填写
        acc_texts = [str(it[5] or "").strip() for it in items]
        acc_rowspan = bool(acc_texts[0]) and not any(acc_texts[1:])
        for i, (typ, w_spec, w_qty, p_spec, p_qty, accompany) in enumerate(items):
            cells: List[Dict[str, Any]] = []
            if i == 0:
                label = f"{room_name}\n{exam_type}".strip()
                cells.append(_cell(0, 1, label, row_span=n_items, align="center"))
            # 隔室「不配备」合并工作人员两列
            if w_spec and "不配备" in w_spec and not w_qty:
                cells.append(_cell(1, 2, typ, align="left"))
                cells.append(_cell(2, 4, w_spec, align="center"))
            else:
                cells.append(_cell(1, 2, typ, align="left"))
                cells.append(_cell(2, 3, w_spec or "/", align="center"))
                cells.append(_cell(3, 4, w_qty or "/", align="center"))
            cells.append(_cell(4, 5, p_spec or "/", align="center"))
            cells.append(_cell(5, 6, p_qty or "/", align="center"))
            if acc_rowspan:
                if i == 0:
                    cells.append(
                        _cell(6, 7, accompany or "/", row_span=n_items, align="center")
                    )
            else:
                cells.append(_cell(6, 7, accompany or "/", align="center"))
            rows.append({"band": band, "cells": cells})
            band += 1

    return {
        "schema": "embedded_generic_table/v1",
        "title": TABLE10_TITLE,
        "source": "personal_protective_equipment",
        "from_info_sheet": bool(from_info_sheet or sourced),
        "room_count": room_count,
        "column_count": 7,
        "column_fractions": [0.0, 0.18, 0.34, 0.46, 0.56, 0.68, 0.80, 1.0],
        "band_count": len(rows),
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "header_band_count": 2,
        "note": (
            "表10由信息表「个人防护用品和辅助设施配备计划」提取"
            if (from_info_sheet or sourced)
            else "表10可在常用模板中编辑；提取信息表后按配备计划覆盖"
        ),
        "rows": rows,
    }


def load_table9_template() -> Dict[str, Any]:
    from f1_eval_report.templates.loader import load_common_json

    try:
        raw = load_common_json("protection_measures/table9.json")
        if isinstance(raw, dict) and raw.get("rows"):
            raw.setdefault("title", TABLE9_TITLE)
            return raw
    except Exception:
        pass
    return build_table9()


def load_table10_template(
    devices: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    pdf_path: Optional[str] = None,
    prefer_pdf: bool = False,
) -> Dict[str, Any]:
    """加载表10。prefer_pdf=True（提取信息表时）优先从 PDF 解析。"""
    if prefer_pdf and pdf_path:
        try:
            plans = parse_ppe_plan_from_pdf(pdf_path)
            if plans:
                return build_table10(
                    devices, room_plans=plans, from_info_sheet=True, pdf_path=pdf_path
                )
        except Exception:
            pass
    from f1_eval_report.templates.loader import load_common_json

    try:
        raw = load_common_json("protection_measures/table10.json")
        if isinstance(raw, dict) and raw.get("rows"):
            # 提取场景：已有模板但非信息表来源时，仍尝试 PDF 覆盖
            if prefer_pdf and pdf_path and not raw.get("from_info_sheet"):
                plans = parse_ppe_plan_from_pdf(pdf_path)
                if plans:
                    return build_table10(
                        devices, room_plans=plans, from_info_sheet=True
                    )
            raw.setdefault("title", TABLE10_TITLE)
            if devices is not None:
                raw["room_count"] = count_xray_rooms(list(devices)) or raw.get(
                    "room_count"
                )
            return raw
    except Exception:
        pass
    return build_table10(devices, pdf_path=pdf_path)


def assemble_personal_protective_equipment(
    devices: Optional[List[Dict[str, Any]]] = None,
    *,
    include_table: bool = True,
    eval_basis: Optional[str] = None,
    pdf_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    返回:
      personal_protective_equipment: 导语 + <<<F1_TABLE>>> + <<<F1_TABLE>>> + 表后
      personal_protective_equipment_tables: [表9, 表10]
    表10优先从信息表 PDF「配备计划」解析。
    """
    plans: List[RoomPlan] = []
    if pdf_path:
        try:
            plans = parse_ppe_plan_from_pdf(pdf_path)
        except Exception:
            plans = []

    room_count = count_xray_rooms(devices) or 0
    if plans:
        total = 0
        for name, _, _ in plans:
            m = re.search(r"（\s*(\d+)\s*间\s*）", name.replace(" ", ""))
            if m:
                total += int(m.group(1))
        if total:
            room_count = total
    if not room_count:
        room_count = 16

    basis = (eval_basis if eval_basis is not None else DEFAULT_EVAL_BASIS).strip()
    intro = _fill(
        _load_fixed("protection_measures/ppe_intro.md", DEFAULT_INTRO),
        room_count=room_count,
        eval_basis=basis,
    )
    after = _fill(
        _load_fixed("protection_measures/ppe_after.md", DEFAULT_AFTER),
        room_count=room_count,
        eval_basis=basis,
    )
    text = f"{intro}\n\n{TABLE_MARK}\n\n{TABLE_MARK}\n\n{after}".strip() + "\n"
    out: Dict[str, Any] = {
        "personal_protective_equipment": text,
        "room_count": room_count,
        "eval_basis": basis,
        "ppe_room_plans": plans,
    }
    if include_table:
        table10 = (
            build_table10(devices, room_plans=plans, from_info_sheet=True)
            if plans
            else load_table10_template(devices, pdf_path=pdf_path, prefer_pdf=True)
        )
        out["personal_protective_equipment_tables"] = [
            load_table9_template(),
            table10,
        ]
    else:
        out["personal_protective_equipment_tables"] = []
    return out
