# -*- coding: utf-8 -*-
"""从《南昌大学第二附属医院评价信息表(终稿)》提取并组装 F.1 数据。"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import fitz

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PACKAGE_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f1_eval_report.templates.loader import load_body_template, load_device_principle, load_workflow

OUT_DIR = os.path.join(PACKAGE_DIR, "examples", "ncu2_extract")


def find_pdf() -> str:
    for pat in (os.path.join(ROOT, "*终稿*.pdf"), os.path.join(ROOT, "*评价信息表*.pdf")):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    raise FileNotFoundError("未找到评价信息表 PDF")


def page_texts(pdf_path: str) -> List[str]:
    doc = fitz.open(pdf_path)
    texts = [doc[i].get_text() for i in range(len(doc))]
    doc.close()
    return texts


def _flat(s: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", s.replace("\r", "")).strip()


def _join_lines(text: str) -> str:
    # merge soft line breaks inside Chinese paragraphs
    lines = [ln.strip() for ln in text.splitlines()]
    out: List[str] = []
    buf = ""
    for ln in lines:
        if not ln:
            if buf:
                out.append(buf)
                buf = ""
            continue
        if not buf:
            buf = ln
        elif buf.endswith(("。", "；", "：", "!", "？", "”")) or ln.startswith(("（", "(", "1.", "2.", "表")):
            out.append(buf)
            buf = ln
        else:
            buf += ln
    if buf:
        out.append(buf)
    return "\n".join(out)


def parse_cover(p1: str) -> Dict[str, str]:
    fields: Dict[str, str] = {
        "org_name": "南昌大学第二附属医院",
        "principal": "祝新根",
        "address": "东湖院区：江西省南昌市民德路1号；红角洲院区：江西省南昌市学府大道566号",
        "postal_code": "330000",
        "contact": "贺桂凤",
        "phone": "13007207117",
        "fax": "",
        "project_name": "南昌大学第二附属医院（新建院区）医用X射线影像诊断及介入放射学建设项目",
        "project_purpose": "医用X射线影像诊断、介入放射学",
        "construction_address": "江西省南昌市新建区望城镇青城路以南、金枝玉路东侧、敏学路北侧、进贤路西侧",
        "construction_nature": "新建☑扩建□改建□\n技术引进□ 技术改造□",
        "planned_radiation_workers": "",
        "investment": "/",
        "construction_area": "/",
    }
    # refine from text if present
    if "祝新根" in p1:
        fields["principal"] = "祝新根"
    if "贺桂凤" in p1:
        fields["contact"] = "贺桂凤"
    m = re.search(r"13007207117", p1)
    if m:
        fields["phone"] = m.group(0)
    if "330000" in p1:
        fields["postal_code"] = "330000"
    return fields


def _split_section(p1: str, start: str, stops: Tuple[str, ...]) -> str:
    if start not in p1:
        return ""
    body = p1.split(start, 1)[1]
    for stop in stops:
        if stop in body:
            body = body.split(stop, 1)[0]
            break
    return _join_lines(_flat(body))


def parse_org_intro(p1: str) -> str:
    return _split_section(p1, "单位简介", ("项目简介", "管理目", "项目用途", "我单位承诺"))


def parse_project_intro_raw(p1: str) -> str:
    proj = _split_section(p1, "项目简介", ("管理目", "项目用途", "我单位承诺", "工作人员"))
    if "贵委托进行" in proj:
        proj = proj.replace(
            "根据国家相关要求，贵委托进行放射性职业病危害预评价",
            "根据国家相关要求，委托评价单位开展放射性职业病危害预评价。",
        )
    return proj.rstrip("。．. \n")


def parse_commission_date(p1: str) -> str:
    """信息表首页右下角委托日期，如 2025年1月17日。"""
    m = re.search(r"(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", p1)
    if not m:
        return ""
    return re.sub(r"\s+", "", m.group(1))


def parse_evaluation_org(p1: str) -> str:
    """首页抬头评价单位名称。"""
    m = re.search(r"(江西[^：:\n]{2,40}公司)\s*[：:]", p1)
    if m:
        return m.group(1).strip()
    if "江西辐射剂量检测院有限公司" in p1:
        return "江西辐射剂量检测院有限公司"
    return "江西辐射剂量检测院有限公司"


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def parse_shielding_rooms(pages: List[str]) -> List[Dict[str, str]]:
    """
    从《本项目放射防护设施屏蔽设计情况表》提取「位置 + 机房名称」。
    OCR 分行较多，按序号块解析；失败时回退到射线装置清单场所。
    """
    chunks: List[str] = []
    capture = False
    for t in pages:
        if "放射防护设施屏蔽设计情况表" in t:
            capture = True
            chunks.append(t)
            continue
        if capture:
            # 续页：仍为屏蔽表行列
            if re.search(r"^\s*序\s*\n?\s*号", t) or (
                "机房" in t[:80] and "位置" in t[:80]
            ):
                chunks.append(t)
            elif "放射分区计划" in t or "防护安全设施" in t:
                break
            elif chunks:
                chunks.append(t)
                break

    text = "\n".join(chunks)
    rooms: List[Dict[str, str]] = []
    # 按行号切分：行首数字 1..16
    parts = re.split(r"(?m)(?=^\s*\d{1,2}\s*$)", text)
    for part in parts:
        m = re.match(r"^\s*(\d{1,2})\s*\n(.*)$", part, re.S)
        if not m:
            continue
        body = m.group(2)
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        # 位置/机房名通常在墙体材料描述之前
        cut = len(lines)
        for i, ln in enumerate(lines):
            if re.search(r"\d+\s*mm|实心红砖|空心砖|镀锌|混凝土|铅板|硫酸钡", ln):
                cut = i
                break
        head = lines[:cut]
        if len(head) < 2:
            continue
        # 末若干行为机房名，前面为位置
        # 典型：["门诊楼","一楼","CT 机房"] 或 ["住院楼","二楼介","入中心","DSA 机","房1"]
        loc_room = _norm_space("".join(head))
        # 尝试拆出机房名
        room = ""
        loc = ""
        for pat in (
            r"(.+?)(手术室\d+[（(]C形臂机房\d+[）)].*)",
            r"(.+?)((?:CT|DR|DSA|ERCP|全身骨密度仪|乳腺DR|数字胃肠)机房\d*)",
            r"(.+?)((?:CT|DR|DSA|ERCP)机房\d*)",
        ):
            mm = re.match(pat, loc_room)
            if mm:
                loc, room = mm.group(1), mm.group(2)
                break
        if not room:
            # 回退：最后含「机房/手术室」的片段
            if "机房" in loc_room or "手术室" in loc_room:
                mm = re.search(r"(.+?)((?:手术室|.*机房).*)$", loc_room)
                if mm:
                    loc, room = mm.group(1), mm.group(2)
        if loc and room:
            rooms.append({"location": loc, "room": room, "seq": m.group(1)})

    if rooms:
        return rooms

    # 回退：装置清单场所
    fallback: List[Dict[str, str]] = []
    for i, d in enumerate(DEVICES, 1):
        place = _norm_space(d["place"])
        mm = re.match(
            r"(.+?)((?:CT|DR|DSA|ERCP|全身骨密度仪|乳腺DR|数字胃肠)机房\d*|手术室\d+.*)$",
            place,
        )
        if mm:
            fallback.append({"location": mm.group(1), "room": mm.group(2), "seq": str(i)})
        else:
            fallback.append({"location": place, "room": d["name"] + "机房", "seq": str(i)})
    return fallback


def _normalize_location_label(loc: str) -> str:
    """屏蔽表/装置清单位置名 → 概述用语（与平面图一致）。"""
    s = _norm_space(loc)
    s = s.replace("一层", "一楼").replace("二层", "二楼").replace("四层", "四楼")
    rules = [
        (r"^门诊楼", "门急诊楼"),
        (r"^发热门诊楼", "发热门诊"),
        (r"^住院楼二楼内镜中心", "医技楼二楼内镜中心"),
        (r"^住院楼二楼介入中心", "医技楼二楼介入中心"),
        (r"^住院楼四楼手术中心", "医技楼四楼手术中心"),
        (r"^医技楼一楼(?:影像中心)?$", "医技楼一楼影像中心"),
        (r"^医技楼一楼影像中心", "医技楼一楼影像中心"),
    ]
    for pat, rep in rules:
        if re.search(pat, s):
            if pat.startswith(r"^门诊楼"):
                return re.sub(r"^门诊楼", "门急诊楼", s)
            if pat.startswith(r"^发热门诊楼"):
                return re.sub(r"^发热门诊楼", "发热门诊", s)
            if "住院楼二楼内镜" in pat:
                return "医技楼二楼内镜中心"
            if "住院楼二楼介入" in pat:
                return "医技楼二楼介入中心"
            if "住院楼四楼" in pat:
                return "医技楼四楼手术中心"
            if "医技楼一楼" in pat:
                return "医技楼一楼影像中心"
            return rep
    return s


def _room_type(room: str) -> Tuple[str, str]:
    r = _norm_space(room)
    if "DSA" in r:
        return "DSA", "DSA机房"
    if "ERCP" in r:
        return "ERCP", "ERCP机房"
    if "C形臂" in r or "手术室" in r:
        return "C臂", "C形臂机房"
    if "骨密度" in r:
        return "BMD", "全身骨密度仪机房"
    if "乳腺" in r:
        return "MAMMO", "乳腺DR机房"
    if "胃肠" in r:
        return "GI", "数字胃肠机房"
    if re.search(r"CT", r):
        return "CT", "CT机房"
    if re.search(r"DR", r):
        return "DR", "DR机房"
    return "OTHER", r or "机房"


def _format_count_list(items: List[str], *, total: int = 0, total_label: str = "") -> str:
    if not items:
        return ""
    if len(items) == 1:
        s = items[0]
    elif len(items) == 2:
        s = f"{items[0]}和{items[1]}"
    else:
        s = "、".join(items[:-1]) + "和" + items[-1]
    if total_label and total >= 3:
        s += f"共计{total}间{total_label}"
    return s


def build_room_plan_lines(rooms: List[Dict[str, str]]) -> List[str]:
    """按楼栋/楼层汇总机房，生成（1）（2）…条目。"""
    # 规范后的条目
    entries: List[Dict[str, str]] = []
    for r in rooms:
        loc = _normalize_location_label(r.get("location", ""))
        room = _norm_space(r.get("room", ""))
        # 手术室 OR 标注
        or_tag = ""
        m = re.search(r"手术室(\d+)", room)
        if m:
            or_tag = f"OR{m.group(1)}室"
        typ, typ_name = _room_type(room)
        entries.append({"loc": loc, "type": typ, "type_name": typ_name, "or_tag": or_tag})

    # 分组顺序（与 250075YP 样例一致）
    group_specs = [
        {
            "id": "menzhen",
            "match": lambda loc: loc.startswith("门急诊楼"),
            "place": "门急诊楼一楼",
            "with_total": False,
        },
        {
            "id": "yiji1",
            "match": lambda loc: "医技楼一楼" in loc or loc == "医技楼一楼影像中心",
            "place": "医技楼一楼影像中心",
            "with_total": True,
            "total_label": "X射线设备机房",
        },
        {
            "id": "yiji2",
            "match": lambda loc: "医技楼二楼" in loc,
            "place": None,  # 特殊拼装
            "with_total": True,
            "total_label": "X射线设备机房",
        },
        {
            "id": "yiji4",
            "match": lambda loc: "医技楼四楼" in loc,
            "place": "医技楼四楼手术中心",
            "with_total": False,
        },
        {
            "id": "fare",
            "match": lambda loc: loc.startswith("发热门诊"),
            "place": "发热门诊一楼",
            "with_total": False,
        },
    ]

    type_order = ["CT", "DR", "BMD", "MAMMO", "GI", "DSA", "ERCP", "C臂", "OTHER"]
    lines: List[str] = []
    idx = 1
    for spec in group_specs:
        group = [e for e in entries if spec["match"](e["loc"])]
        if not group:
            continue
        if spec["id"] == "yiji2":
            # 介入中心 DSA + 内镜中心 ERCP
            dsa = [e for e in group if e["type"] == "DSA"]
            ercp = [e for e in group if e["type"] == "ERCP"]
            other = [e for e in group if e["type"] not in ("DSA", "ERCP")]
            bits: List[str] = []
            if dsa:
                bits.append(f"介入中心建设{len(dsa)}间DSA机房")
            if ercp:
                bits.append(f"内镜中心建设{len(ercp)}间ERCP机房")
            for e in other:
                bits.append(f"建设1间{e['type_name']}")
            total = len(group)
            body = "及".join(bits) if len(bits) > 1 else (bits[0] if bits else "")
            if total >= 3:
                body += f"，共计{total}间X射线设备机房"
            lines.append(f"（{idx}）拟在医技楼二楼{body}；")
            idx += 1
            continue

        counts: Dict[str, int] = {}
        names: Dict[str, str] = {}
        or_tags: List[str] = []
        for e in group:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
            names[e["type"]] = e["type_name"]
            if e["or_tag"]:
                or_tags.append(e["or_tag"])
        items = [f"{counts[t]}间{names[t]}" for t in type_order if counts.get(t)]
        total = sum(counts.values())
        detail = _format_count_list(
            items,
            total=total if spec.get("with_total") else 0,
            total_label=str(spec.get("total_label") or ""),
        )
        if spec["id"] == "yiji4" and or_tags:
            # 2间C形臂机房（OR7室和OR8室）
            or_part = "和".join(or_tags) if len(or_tags) <= 2 else "、".join(or_tags[:-1]) + "和" + or_tags[-1]
            detail = f"{total}间C形臂机房（{or_part}）"
        place = spec["place"] or group[0]["loc"]
        lines.append(f"（{idx}）拟在{place}建设{detail}；")
        idx += 1

    if lines:
        # 最后一项句号
        lines[-1] = lines[-1].rstrip("；;") + "。"
    return lines


_LAW_QUOTE = (
    "新建、扩建、改建建设项目和技术改造、技术引进项目可能产生职业病危害的，"
    "建设单位在可行性论证阶段应当进行职业病危害预评价。"
    "医疗机构建设项目可能产生放射性职业病危害的，建设单位应当向卫生行政部门提交放射性职业病危害预评价报告；"
    "未提交预评价报告或者预评价报告未经卫生行政部门审核同意的，不得开工建设"
)

_SUMMARY_CLOSING = (
    "评价单位接受委托后，在认真分析论证建设单位提供的本项目相关文件的基础上，结合调查情况，"
    "根据国家相关法律、法规、标准、规范编制了本项目放射性职业病危害预评价报告表。"
)


def build_enriched_project_intro(
    *,
    raw_intro: str,
    address: str,
    rooms: List[Dict[str, str]],
    commission_date: str,
    evaluation_org: str,
    project_short_name: str = "南昌大学第二附属医院（新建院区）介入放射学和X射线影像诊断建设项目",
) -> str:
    """
    将信息表项目简介补全为：建设地址 + 机房明细 + 委托依据/日期/评价单位。
    """
    # 取简介前半：截止到「拟建设…」动机句，去掉末尾委托残句
    lead = raw_intro
    for cut in ("根据国家相关要求", "贵委托", "委托评价单位"):
        if cut in lead:
            lead = lead.split(cut, 1)[0].rstrip("，,；; ")
            break
    # 将「拟建设…并在该院区建设…」改写为带地址与建设范围的表述
    # 保留动机句至「公共卫生服务，」
    m = re.search(r"^(.+公共卫生服务)，", lead)
    motive = m.group(1) if m else "为保障新建区群众健康，全面加快医疗卫生事业发展，大力实施国家基本药物制度和公共卫生服务"
    addr = _flat(address).replace("\n", "")
    room_lines = build_room_plan_lines(rooms)
    room_block = "\n".join(room_lines) if room_lines else ""

    date = commission_date or "____年__月__日"
    org = evaluation_org or "评价单位"

    parts = [
        f"{motive}，建设单位拟在{addr}建设南昌大学第二附属医院（新建院区），"
        f"并拟在该院区门急诊楼、医技楼及发热门诊楼建设以下介入放射学和X射线影像诊断项目：",
    ]
    if room_block:
        parts.append(room_block)
    parts.append(
        "上述射线装置均拟新购，厂家和型号待定，射线装置在运行过程中会产生电离辐射，"
        f"根据《中华人民共和国职业病防治法》《放射诊疗管理规定》等法律法规关于：“{_LAW_QUOTE}”的要求，"
        f"建设单位于{date}委托{org}（以下简称“评价单位”）对{project_short_name}"
        f"（以下简称“本项目”）进行放射性职业病危害预评价（委托单见附件一）。"
    )
    return "\n".join(parts)


def build_project_summary(
    p1: str,
    pages: List[str],
    *,
    address: str,
) -> str:
    """单位简介 + 完善后的项目简介 + 固定总结段。"""
    org = parse_org_intro(p1)
    raw_proj = parse_project_intro_raw(p1)
    rooms = parse_shielding_rooms(pages)
    date = parse_commission_date(p1)
    eval_org = parse_evaluation_org(p1)
    enriched = build_enriched_project_intro(
        raw_intro=raw_proj,
        address=address,
        rooms=rooms,
        commission_date=date,
        evaluation_org=eval_org,
    )
    parts = [p for p in (org, enriched, _SUMMARY_CLOSING) if p]
    return "\n".join(parts)


def build_project_classification_text() -> str:
    return (
        "根据《关于发布〈射线装置分类〉的公告》（环境保护部、国家卫生和计划生育委员会2017年第66号公告）规定，"
        "本项目涉及的射线装置（CT、DR、全身骨密度仪、乳腺DR、数字胃肠机、C形臂机、DSA等）属于Ⅱ类射线装置；"
        "根据《放射诊疗建设项目卫生审查管理规定》相关规定，本项目属于放射性危害程度与诊疗风险危害一般类放射诊疗建设项目。"
    )


def parse_org_project_blurb(p1: str) -> str:
    """兼容旧调用：仅单位简介+原始项目简介（不含机房明细）。"""
    org = parse_org_intro(p1)
    proj = parse_project_intro_raw(p1)
    return "\n".join(p for p in (org, proj) if p)



DEVICES: List[Dict[str, str]] = [
    {
        "name": "CT",
        "note": "128排及以上",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：140kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "门诊楼一楼CT机房",
        "source": "新购",
    },
    {
        "name": "DR",
        "note": "单管头",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：150kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "门诊楼一楼DR机房",
        "source": "新购",
    },
    {
        "name": "CT",
        "note": "256排及以上",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：140kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "医技楼一楼影像中心CT机房1",
        "source": "新购",
    },
    {
        "name": "CT",
        "note": "128排及以上",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：140kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "医技楼一楼影像中心CT机房2",
        "source": "新购",
    },
    {
        "name": "DR",
        "note": "单管头",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：150kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "医技楼一楼影像中心DR机房1",
        "source": "新购",
    },
    {
        "name": "DR",
        "note": "单管头",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：150kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "医技楼一楼影像中心DR机房2",
        "source": "新购",
    },
    {
        "name": "全身骨密度仪",
        "note": "全身",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：150kV\n最大管电流：100mA",
        "tubes": "1个",
        "place": "医技楼一楼影像中心全身骨密度仪机房",
        "source": "新购",
    },
    {
        "name": "乳腺DR",
        "note": "/",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：50kV\n最大管电流：200mA",
        "tubes": "1个",
        "place": "医技楼一楼影像中心乳腺DR机房",
        "source": "新购",
    },
    {
        "name": "数字胃肠机",
        "note": "单管头",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：150kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "医技楼一楼影像中心数字胃肠机房",
        "source": "新购",
    },
    {
        "name": "C形臂机",
        "note": "ERCP使用",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：125kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "住院楼二楼内镜中心ERCP机房",
        "source": "新购",
    },
    {
        "name": "DSA",
        "note": "单管头、带类CT功能",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：150kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "住院楼二楼介入中心DSA机房1",
        "source": "新购",
    },
    {
        "name": "DSA",
        "note": "单管头、带类CT功能",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：150kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "住院楼二楼介入中心DSA机房2",
        "source": "新购",
    },
    {
        "name": "C形臂机",
        "note": "骨科使用（隔室曝光）",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：125kV\n最大管电流：200mA",
        "tubes": "1个",
        "place": "住院楼四楼手术中心手术室7",
        "source": "新购",
    },
    {
        "name": "C形臂机",
        "note": "骨科使用（隔室曝光）",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：125kV\n最大管电流：200mA",
        "tubes": "1个",
        "place": "住院楼四楼手术中心手术室8",
        "source": "新购",
    },
    {
        "name": "CT",
        "note": "128排及以上",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：140kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "发热门诊楼一楼CT机房",
        "source": "新购",
    },
    {
        "name": "DR",
        "note": "单管头",
        "model": "待定",
        "manufacturer": "待定",
        "device_id": "待定",
        "param": "最大管电压：150kV\n最大管电流：1000mA",
        "tubes": "1个",
        "place": "发热门诊楼一楼DR机房",
        "source": "新购",
    },
]


def _device_type_with_note(d: Dict[str, str]) -> str:
    name = (d.get("name") or "").strip()
    note = (d.get("note") or "").strip()
    if not note or note in {"/", "-", "—", "无"}:
        return name
    return f"{name}（{note}）"


def build_radiation_table() -> Dict[str, Any]:
    header = [
        "序号",
        "设备类型（备注）",
        "型号",
        "生产厂家",
        "设备编号",
        "主要参数",
        "球管个数",
        "所在场所",
        "设备来源",
    ]
    rows = [
        {
            "band": 0,
            "cells": [
                {
                    "c0": i,
                    "c1": i + 1,
                    "text": h,
                    **({"wrap_chars": 2} if h in {"生产厂家", "设备编号", "设备来源", "球管个数"} else {}),
                }
                for i, h in enumerate(header)
            ],
        }
    ]
    for i, d in enumerate(DEVICES, start=1):
        vals = [
            str(i),
            _device_type_with_note(d),
            d.get("model", "待定"),
            d.get("manufacturer", "待定"),
            d.get("device_id", "待定"),
            d.get("param", ""),
            d.get("tubes", "1个"),
            d.get("place", ""),
            d.get("source", "新购"),
        ]
        rows.append(
            {
                "band": i,
                "cells": [{"c0": j, "c1": j + 1, "text": v} for j, v in enumerate(vals)],
            }
        )
    return {
        "schema": "embedded_generic_table/v1",
        "title": "表1  本项目射线装置清单",
        "cell_align": "center",
        "header_bold": True,
        "column_fractions": [
            0.0,
            0.05,
            0.20,
            0.28,
            0.36,
            0.44,
            0.60,
            0.68,
            0.92,
            1.0,
        ],
        "column_count": 9,
        "row_min_height_pt": 18.0,
        "repeat_header_on_new_page": True,
        "rows": rows,
    }


def parse_planned_radiation_workers(pages: List[str]) -> Tuple[str, int]:
    """从《本项目人员配备计划》提取「N 人」并求和（不计兼职无人数项）。"""
    text = ""
    for t in pages:
        if "人员配备计划" in t:
            text = t
            break
    if not text:
        return "", 0
    nums = [int(n) for n in re.findall(r"(\d+)\s*人", text)]
    total = sum(nums)
    if total <= 0:
        return "", 0
    return f"{total}人", total


def _strip_md(text: str) -> str:
    """去掉模板中的 markdown 标记，便于写入 F.1 单元格。"""
    lines: List[str] = []
    in_fence = False
    for ln in text.splitlines():
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if ln.startswith("#"):
            ln = ln.lstrip("#").strip()
        ln = re.sub(r"\*\*([^*]+)\*\*", r"\1", ln)
        ln = ln.replace("　", " ").strip()
        if ln.startswith("图题："):
            continue
        if ln.startswith("图 ") and "工作流程" in ln and not ln.startswith("图　"):
            # keep workflow titles as plain lines
            pass
        lines.append(ln)
    # collapse excess blank lines
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


def build_hazard_analysis(devices: Optional[List[Dict[str, Any]]] = None) -> str:
    """职业病危害因素分析：按样例结构组稿（1 / 1.1原理 / 1.2流程 / 2异常）。"""
    from f1_eval_report.templates.hazard_analysis import assemble_hazard_analysis

    return assemble_hazard_analysis(devices if devices is not None else DEVICES)


def build_workplace_intro(devices: Optional[List[Dict[str, Any]]] = None) -> str:
    """工作场所布局正文（按区位分组；表4/表5另见 workplace_layout_tables）。"""
    from f1_eval_report.templates.hazard_analysis import classify_devices
    from f1_eval_report.templates.workplace_layout import assemble_workplace_layout

    devs = devices if devices is not None else DEVICES
    # 图号接在危害因素分析流程图之后
    fig_start = max(1, len(classify_devices(devs)) + 1)
    obj = assemble_workplace_layout(devs, fig_start=fig_start, include_tables=False)
    return str(obj.get("workplace_layout_intro") or "")


def build_workplace_tables(devices: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    from f1_eval_report.templates.workplace_layout import assemble_workplace_layout

    devs = devices if devices is not None else DEVICES
    obj = assemble_workplace_layout(devs, include_tables=True)
    return list(obj.get("workplace_layout_tables") or [])


def build_staff_text(pages: List[str]) -> str:
    for t in pages:
        if "人员配备计划" in t:
            return (
                "本项目放射工作人员配置计划见《本项目人员配备计划》。"
                "X射线影像诊断拟配备放射影像医师10人、放射影像技师16人；"
                "介入放射学（ERCP/DSA）及C形臂手术相关岗位按计划配备医师、技师及护理人员。"
                "人员资质与数量对照《放射诊疗管理规定》相关要求执行。"
            )
    return "本项目放射工作人员按《放射诊疗管理规定》配备，详见人员配备计划。"


def load_fixed_basis() -> str:
    """主要评价依据：固定模板全文（不从评价信息表提取）。"""
    try:
        from f1_eval_report.templates.loader import load_default_evaluation_basis_text

        text = load_default_evaluation_basis_text()
        if text:
            return text
    except Exception:
        pass
    try:
        return load_body_template("evaluation_basis")
    except Exception:
        return ""


def build_data(pdf_path: Optional[str] = None) -> Dict[str, Any]:
    pdf = pdf_path or find_pdf()
    pages = page_texts(pdf)
    p1 = pages[0] if pages else ""
    fields = parse_cover(p1)
    workers_text, workers_total = parse_planned_radiation_workers(pages)
    fields["planned_radiation_workers"] = workers_text or "/"
    fields["project_summary"] = build_project_summary(
        p1, pages, address=fields.get("construction_address") or ""
    )
    fields["project_classification"] = build_project_classification_text()
    # 主要评价依据：默认使用固定模板，不从信息表获取
    fields["evaluation_basis"] = load_fixed_basis()

    fields["hazard_analysis"] = build_hazard_analysis()
    from f1_eval_report.templates.protection_zoning import assemble_protection_zoning
    from f1_eval_report.templates.shielding_facilities import assemble_shielding_facilities

    # 放射防护分区：导语 + 表6（机房间数来自装置清单）
    _zoning = assemble_protection_zoning(DEVICES if DEVICES else None, table_mode="summary")
    fields["protection_zoning"] = _zoning["protection_zoning"]
    # 屏蔽设施：导语 + 表7 + 表后总结（机房间数来自装置清单）
    _shield = assemble_shielding_facilities(
        DEVICES if DEVICES else None, pdf_path=pdf
    )
    fields["shielding"] = _shield["shielding"]
    fields["interlock_protection"] = (
        "本项目拟设置观察窗或摄像监控、电离辐射警告标志、工作状态指示灯及门灯联动、"
        "防护注意事项告知栏、推拉门防夹、地面警戒线、动力通风等，详见《本项目防护安全设施和措施设置计划》。"
    )
    fields["warning_signs"] = "各防护门设置电离辐射警告标志；机房上方设置工作状态指示灯，灯箱警示语为“射线有害，灯亮勿入”。"
    from f1_eval_report.templates.safety_protection import assemble_safety_protection

    _safety = assemble_safety_protection(DEVICES if DEVICES else None, include_table=True)
    fields["safety_protection"] = _safety["safety_protection"]
    fields["personal_protective_equipment"] = (
        "个人防护用品及辅助设施按机房类型配备铅橡胶衣帽颈套、铅方巾、介入分体铅衣、铅眼镜、铅屏风等，"
        "详见《本项目个人防护用品和辅助设施配备计划》。"
    )
    fields["waste_disposal"] = (
        "本项目X射线设备在工作过程主要是产生低能X射线，不存在放射性污染，"
        "无放射性废气、废水、废物等“三废”产生，故无需采取防放射性污染措施及放射性三废处理措施。"
    )
    try:
        from f1_eval_report.templates.loader import load_common_text

        t = load_common_text("protection_measures/waste_disposal.md").strip()
        if t:
            fields["waste_disposal"] = t
    except Exception:
        pass
    fields["other_measures"] = "无"
    try:
        from f1_eval_report.templates.health_impact import assemble_normal_condition

        _hi = assemble_normal_condition(DEVICES if DEVICES else None, include_table=True)
        fields["normal_condition"] = _hi["normal_condition"]
    except Exception:
        fields["normal_condition"] = (
            "正常运行情况下工作人员及公众受照剂量估算见健康影响评价相关表格（待完善剂量估算数据后填入）。"
        )
    from f1_eval_report.templates.radiation_management import (
        assemble_abnormal_condition,
        assemble_radiation_management,
        build_conclusion,
    )

    fields["abnormal_condition"] = assemble_abnormal_condition()
    _rm = assemble_radiation_management(
        staff_total=workers_total or None,
        include_tables=True,
    )
    fields["organization"] = _rm["organization"]
    fields["management_system"] = _rm["management_system"]
    fields["staff_management"] = _rm["staff_management"]
    fields["personal_monitoring"] = _rm["personal_monitoring"]
    fields["health_surveillance"] = _rm["health_surveillance"]
    fields["radiation_training"] = _rm["radiation_training"]
    fields["archive_management"] = _rm["archive_management"]
    fields["conclusion"] = build_conclusion(
        DEVICES if DEVICES else None,
        staff_count=int(_rm.get("staff_count") or workers_total or 59),
    )
    project = fields["project_name"]
    fields["attachments_list"] = "\n".join(
        [
            "附件一 建设项目放射性职业病危害评价委托单",
            "附件二 《事业单位法人证书》《医疗机构执业许可证》",
            "附件三 本项目基本信息",
            "附件四 本项目相关图纸",
            "附件五 本项目放射工作人员配备计划",
            "附件六 应急预案",
            "附件七 其他放射防护管理制度",
            f"附件八 关于《{project}放射性职业病危害预评价报告表》的评审意见",
            f"附件九 关于《{project}放射性职业病危害预评价报告表》的评审意见修改说明",
        ]
    )

    data: Dict[str, Any] = {
        "schema": "f1_eval_data/v1",
        "source_pdf": os.path.basename(pdf),
        "fields": fields,
        "radiation_source_intro": f"本项目拟配备射线装置共{len(DEVICES)}台，不涉及放射性同位素的使用，射线装置清单见表1。",
        "radiation_source_tables": [build_radiation_table()],
        "workplace_layout_intro": build_workplace_intro(),
        "workplace_layout_tables": build_workplace_tables(),
        "protection_zoning": fields["protection_zoning"],
        "protection_zoning_tables": _zoning.get("protection_zoning_tables") or [],
        "shielding": fields["shielding"],
        "shielding_tables": _shield.get("shielding_tables") or [],
        "safety_protection": fields["safety_protection"],
        "safety_protection_tables": _safety.get("safety_protection_tables") or [],
        "management_system_tables": _rm.get("management_system_tables") or [],
        "staff_management_tables": _rm.get("staff_management_tables") or [],
        "personal_monitoring_tables": _rm.get("personal_monitoring_tables") or [],
        "health_surveillance_tables": _rm.get("health_surveillance_tables") or [],
        "radiation_training_tables": _rm.get("radiation_training_tables") or [],
        "archive_management_tables": _rm.get("archive_management_tables") or [],
    }
    from f1_eval_report.templates.loader import (
        apply_evaluation_basis_default,
        assemble_evaluation_objective,
    )
    from f1_eval_report.templates.shielding_summary import (
        build_shielding_project_paragraph,
        build_ws76_rooms_summary,
    )

    apply_evaluation_basis_default(data, force=True)
    # 评价目标：固定模板 + 按装置/机房自动生成「综上」与 WS76 句
    obj = assemble_evaluation_objective(devices=DEVICES)
    data["evaluation_objective_intro"] = obj.get("evaluation_objective_intro") or ""
    data["evaluation_objective_tables"] = obj.get("evaluation_objective_tables") or []
    # 同步写回可编辑模板文件的默认内容（工作区侧由 service 再落盘）
    data["_generated_shielding"] = {
        "project_summary": build_shielding_project_paragraph(DEVICES),
        "ws76_rooms": build_ws76_rooms_summary(DEVICES),
    }
    return data


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    data = build_data()
    out = os.path.join(OUT_DIR, "gbzt_f1_ncu2_from_info_sheet.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("written", out)
    print("project:", data["fields"]["project_name"])
    print("purpose:", data["fields"]["project_purpose"])
    print("workers:", data["fields"]["planned_radiation_workers"])
    print("devices:", len(DEVICES))
    print("table_title:", data["radiation_source_tables"][0].get("title"))
    print("row1_type:", data["radiation_source_tables"][0]["rows"][1]["cells"][1]["text"])


if __name__ == "__main__":
    main()
