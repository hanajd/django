# -*- coding: utf-8 -*-
"""职业病危害因素分析：按装置清单组装「工作原理 + 工作流程」正文。"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

# 原理展开顺序（出现在项目中的才写入）
PRINCIPLE_ORDER: List[Tuple[str, str]] = [
    ("ct", "CT"),
    ("dr", "DR"),
    ("bmd", "全身骨密度仪"),
    ("mammo_dr", "乳腺DR"),
    ("gi", "数字胃肠机"),
    ("ercp", "ERCP设备"),
    ("dsa", "DSA"),
    ("c_arm", "C形臂"),
    ("g_arm", "G形臂"),
    ("vehicle_ct", "车载CT"),
    ("vehicle_dr", "车载DR"),
    ("oral_cbct_combo", "口腔CBCT（四合一）"),
    ("oral_cbct", "口腔CBCT"),
    ("intraoral", "口内牙片机"),
    ("dental_panorama", "牙科全景机"),
    ("mobile_dr", "移动DR"),
    ("dynamic_dr", "动态DR"),
]

# 介入放射学 vs X射线影像诊断
INTERVENTION_KEYS = {"dsa", "ercp"}

# 设备种类 → 工作流程模板键（库文件 workflows/{key}.md；无独立文件时用此映射取图题）
DEVICE_WORKFLOW_KEY: Dict[str, str] = {
    "ct": "room_separated",
    "dr": "room_separated",
    "bmd": "room_separated",
    "mammo_dr": "room_separated",
    "gi": "room_separated",
    "c_arm": "room_separated",
    "g_arm": "room_separated",
    "vehicle_ct": "room_separated",
    "vehicle_dr": "room_separated",
    "oral_cbct": "room_separated",
    "oral_cbct_combo": "room_separated",
    "intraoral": "room_separated",
    "dental_panorama": "room_separated",
    "mobile_dr": "room_separated",
    "dynamic_dr": "room_separated",
    "dsa": "dsa_intervention",
    "ercp": "ercp",
}

# 隔室操作类（用于引导句备注等）
ROOM_SEPARATED_KEYS = set(k for k, w in DEVICE_WORKFLOW_KEY.items() if w == "room_separated")


def _cn_join(parts: List[str]) -> str:
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]}和{parts[1]}"
    return "、".join(parts[:-1]) + "和" + parts[-1]


def _strip_md(text: str) -> str:
    lines: List[str] = []
    in_fence = False
    for ln in text.splitlines():
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if ln.startswith("#"):
            ln = ln.lstrip("#").strip()
        ln = re.sub(r"\*\*([^*]+)\*\*", r"\1", ln)
        ln = ln.replace("　", " ").strip()
        if ln.startswith("图题："):
            # 图题：图 {图号}　xxx → 仅取标题词
            m = re.search(r"图题[:：]\s*图\s*\{?图号\}?\s*[　\s]*(.+)$", ln)
            if m:
                lines.append(m.group(1).strip())
            continue
        lines.append(ln)
    out: List[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip()


def _principle_body(key: str) -> str:
    from f1_eval_report.templates.loader import load_device_principle

    try:
        raw = load_device_principle(key)
    except Exception:
        return ""
    body = _strip_md(raw)
    # 去掉重复的「Xxx工作原理」标题行（正文里通常另起一句「Xxx工作原理：」）
    lines = body.splitlines()
    if lines and re.match(r"^.+工作原理\s*$", lines[0].strip()):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _workflow_caption(workflow_key: str, fallback: str) -> str:
    from f1_eval_report.templates.loader import load_workflow

    try:
        raw = load_workflow(workflow_key)
    except Exception:
        return fallback
    for ln in raw.splitlines():
        s = ln.strip()
        if s.startswith("**图题：**") or s.startswith("图题："):
            s = re.sub(r"^\*?\*?图题[:：]\*?\*?\s*", "", s)
            s = re.sub(r"^图\s*\{?图号\}?\s*[　\s]*", "", s).strip()
            if s:
                return s
        if s.startswith("#"):
            s = s.lstrip("#").strip()
            s = re.sub(r"^图\s*[　\s]*", "", s).strip()
            if s:
                return s
    return fallback


def _load_fixed(rel: str, default: str = "") -> str:
    from f1_eval_report.templates.loader import load_common_text

    try:
        t = load_common_text(rel).strip()
        return t or default
    except Exception:
        return default


def _label_map() -> Dict[str, str]:
    return {k: v for k, v in PRINCIPLE_ORDER}


def classify_devices(devices: List[Dict[str, Any]]) -> "OrderedDict[str, List[Dict[str, Any]]]":
    """按信息表清单出现顺序归类（同类型合并，顺序=首次出现顺序）。"""
    from f1_eval_report.templates.shielding_summary import classify_device_room

    groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for d in devices or []:
        if not isinstance(d, dict):
            continue
        key = classify_device_room(d)
        if key == "other":
            name = str(d.get("name") or d.get("type") or "").strip()
            for k, label in PRINCIPLE_ORDER:
                if label and (label in name or name in label):
                    key = k
                    break
                if k.upper() == name.upper():
                    key = k
                    break
            if key == "other" and name:
                # 未知类型：用规范化名称作键，保证仍会出现在原理列表中
                key = f"custom:{name}"
        groups.setdefault(key, []).append(d)
    return groups


def _default_label(key: str) -> str:
    if key.startswith("custom:"):
        return key.split(":", 1)[1]
    return _label_map().get(key, key)


def _display_label(key: str, devices: List[Dict[str, Any]], default: str = "") -> str:
    label = default or _default_label(key)
    if key == "c_arm":
        notes = " ".join(str(d.get("note") or "") for d in devices)
        if "骨科" in notes:
            return "C形臂（骨科使用）"
        if "ERCP" in notes:
            return "C形臂（ERCP使用）"
    if key == "ercp":
        return "ERCP设备"
    return label


def _count_phrase(key: str, devices: List[Dict[str, Any]], default_label: str = "") -> str:
    n = len(devices)
    label = _display_label(key, devices, default_label)
    return f"{n}台{label}"


def _iter_group_keys(groups: "OrderedDict[str, List[Dict[str, Any]]]") -> List[str]:
    """信息表顺序（OrderedDict 插入序）。"""
    return list(groups.keys())


def build_principles_intro(groups: "OrderedDict[str, List[Dict[str, Any]]]") -> str:
    intervention: List[str] = []
    imaging: List[str] = []
    # 介入类：仍按 DSA → ERCP；其余严格按信息表出现顺序
    for key in ("dsa", "ercp"):
        if key in groups:
            intervention.append(_count_phrase(key, groups[key]))
    for key in _iter_group_keys(groups):
        if key in INTERVENTION_KEYS:
            continue
        imaging.append(_count_phrase(key, groups[key]))
    bits: List[str] = []
    if intervention:
        bits.append(f"本项目涉及的{_cn_join(intervention)}用于开展介入放射学")
    if imaging:
        prefix = "涉及的" if intervention else "本项目涉及的"
        bits.append(f"{prefix}{_cn_join(imaging)}用于开展X射线影像诊断")
    if not bits:
        return "本项目涉及的射线装置用于开展相关放射诊疗，其工作原理如下。"
    return "；".join(bits) + "，其工作原理如下。"


def build_principles_blocks(groups: "OrderedDict[str, List[Dict[str, Any]]]") -> List[str]:
    """
    按信息表设备种类出现顺序列出工作原理：
    （1）CT
    CT工作原理：……（另起一行；排版时该行首行缩进）
    """
    blocks: List[str] = []
    idx = 0
    for key in _iter_group_keys(groups):
        title = _display_label(key, groups[key])
        lookup = key.split(":", 1)[1] if key.startswith("custom:") else key
        body = _principle_body(lookup)
        if not body:
            body = (
                f"{title}工作原理：（常用模板中尚无该设备原理，"
                "请在「设备工作原理」库中补充后重新提取信息表。）"
            )
        # 确保原理正文不以重复的「（n）标题」开头
        body = body.strip()
        # 若库文件只有原理句而无「Xxx工作原理：」前缀，补上
        if body and "工作原理" not in body.split("\n", 1)[0][:40]:
            if not body.startswith(title):
                body = f"{title}工作原理：{body}"
        idx += 1
        blocks.append(f"（{idx}）{title}\n{body}")
    return blocks


def _workflow_key_for_device(device_key: str) -> str:
    if device_key.startswith("custom:"):
        return "room_separated"
    return DEVICE_WORKFLOW_KEY.get(device_key, "room_separated")


def _workflow_caption_for_device(device_key: str, title: str) -> str:
    """优先读 workflows/{device_key}.md 图题；否则用共享流程模板图题或「{title}工作流程」。"""
    # 设备专属流程文件
    own = _workflow_caption(device_key, "")
    if own and "工作流程" in own:
        return own
    # 共享流程模板
    shared = _workflow_key_for_device(device_key)
    if shared != device_key:
        cap = _workflow_caption(shared, "")
        # 共享图题偏笼统时，改成该设备专名
        if cap and shared == "room_separated":
            return f"{title}工作流程"
        if cap:
            return cap
    return f"{title}工作流程"


def _workflow_lead_for_device(
    device_key: str,
    title: str,
    devices: List[Dict[str, Any]],
    fig: int,
    item: int,
) -> str:
    short = title.replace("（骨科使用）", "").replace("（ERCP使用）", "")
    # 可编辑引导句模板（占位：{title}{short}{fig}）
    tpl_name = f"hazard_analysis/workflow_lead_{device_key}.md"
    default = f"本项目{title}工作流程如图{fig}所示。"
    if device_key == "c_arm":
        notes = " ".join(str(d.get("note") or "") for d in devices)
        if "骨科" in notes:
            default = (
                f"本项目C形臂（骨科用）拟通过脚踏式开关进行隔室操作，"
                f"工作流程如图{fig}所示。"
            )
    elif device_key == "dsa":
        default = f"本项目DSA设备用于开展介入放射学，其工作流程如图{fig}所示。"
    elif device_key == "ercp":
        default = f"本项目ERCP设备用于开展介入放射学，其工作流程如图{fig}所示。"

    lead = _load_fixed(tpl_name, default)
    lead = (
        lead.replace("{title}", title)
        .replace("{short}", short)
        .replace("{fig}", str(fig))
        .strip()
    )
    if not lead.startswith("（"):
        lead = f"（{item}）{lead}"
    return lead


def build_workflow_blocks(groups: "OrderedDict[str, List[Dict[str, Any]]]") -> List[str]:
    """
    按信息表设备种类出现顺序，逐类插入对应流程图占位：
    （n）引导句 → 【此处插入流程图】 → 图 n  图题
    """
    blocks: List[str] = []
    item = 0
    fig = 0
    for key in _iter_group_keys(groups):
        item += 1
        fig += 1
        title = _display_label(key, groups[key])
        lead = _workflow_lead_for_device(key, title, groups[key], fig, item)
        cap = _workflow_caption_for_device(key, title)
        blocks.append(f"{lead}\n【此处插入流程图】\n图 {fig}  {cap}")
    return blocks


def build_summary(groups: "OrderedDict[str, List[Dict[str, Any]]]") -> str:
    phrases: List[str] = []
    total = 0
    for key in _iter_group_keys(groups):
        devices = groups[key]
        total += len(devices)
        phrases.append(_count_phrase(key, devices))
    tpl = _load_fixed(
        "hazard_analysis/01_summary.md",
        "综上所述，本项目涉及{device_list}总计{total}台射线装置正常运行情况下产生的职业病危害因素为电离辐射（X射线），作用于人体可能造成伤害。",
    )
    device_list = _cn_join(phrases) if phrases else "射线装置"
    return (
        tpl.replace("{device_list}", device_list)
        .replace("{total}", str(total))
        .strip()
    )


def assemble_hazard_analysis(devices: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    组稿结构（与样例一致）：
    1.正常运行状态下职业病危害因素
    1.1本项目涉及的射线装置工作原理
       引言（介入/影像诊断台数）+ （1）（2）…各设备原理
    1.2本项目工作流程
       （1）（2）…流程说明 + 【此处插入流程图】 + 图题
       综上所述…
    2.异常或事故状态下职业病危害因素
    """
    groups = classify_devices(devices or [])
    parts: List[str] = []

    parts.append(
        _load_fixed("hazard_analysis/01_header.md", "1.正常运行状态下职业病危害因素")
    )
    parts.append(
        _load_fixed(
            "hazard_analysis/01_1_header.md", "1.1本项目涉及的射线装置工作原理"
        )
    )
    parts.append(build_principles_intro(groups))
    parts.extend(build_principles_blocks(groups))

    parts.append(_load_fixed("hazard_analysis/01_2_header.md", "1.2本项目工作流程"))
    wf = build_workflow_blocks(groups)
    if wf:
        parts.extend(wf)
    else:
        parts.append("本项目工作流程见图示。")
    parts.append(build_summary(groups))

    parts.append(
        _load_fixed(
            "hazard_analysis/02_abnormal.md",
            "2.异常或事故状态下职业病危害因素\n"
            "本项目异常或事故状态下产生的职业病危害因素仍为电离辐射（X射线）。",
        )
    )
    return "\n".join(p for p in parts if p)
