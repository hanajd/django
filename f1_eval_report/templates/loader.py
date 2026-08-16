"""加载正文栏目 Markdown 模板，供组稿/生成调用。"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

PACKAGE_TEMPLATES = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_ROOT: Optional[str] = None


def templates_dir() -> str:
    """当前模板根目录：工作区 templates/（若已切换）或包内默认。"""
    return _TEMPLATES_ROOT or PACKAGE_TEMPLATES


@contextmanager
def templates_root_context(root: Optional[str]) -> Iterator[None]:
    """临时切换模板根目录（用于读取用户工作区常用文本模板）。"""
    global _TEMPLATES_ROOT
    prev = _TEMPLATES_ROOT
    _TEMPLATES_ROOT = root
    try:
        yield
    finally:
        _TEMPLATES_ROOT = prev


def _catalog_path() -> str:
    return os.path.join(templates_dir(), "catalog.json")


def load_catalog(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or _catalog_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def _read_md(rel_path: str) -> str:
    full = os.path.join(templates_dir(), rel_path.replace("/", os.sep))
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


def list_body_sections() -> List[Dict[str, Any]]:
    return list(load_catalog().get("body") or [])


def get_body_section(section_id: str) -> Dict[str, Any]:
    for item in list_body_sections():
        if item.get("id") == section_id:
            return item
    raise KeyError(f"未知正文栏目: {section_id}")


def load_body_template(section_id: str) -> str:
    """按栏目 id 读取正文模板 Markdown 全文。"""
    item = get_body_section(section_id)
    return _read_md(str(item["path"]))


def load_body_template_by_order(order: int) -> str:
    for item in list_body_sections():
        if int(item.get("order", -1)) == order:
            return _read_md(str(item["path"]))
    raise KeyError(f"无序号码为 {order} 的正文栏目")


def load_front_template(name: str = "cover") -> str:
    front = load_catalog().get("front") or {}
    if name not in front:
        raise KeyError(f"未知封面/目录模板: {name}")
    return _read_md(str(front[name]))


def load_back_template(name: str = "attachments") -> str:
    back = load_catalog().get("back") or {}
    if name not in back:
        raise KeyError(f"未知附件模板: {name}")
    return _read_md(str(back[name]))


def list_library_files(subdir: str) -> List[str]:
    """列出设备原理/流程等子模板文件名（优先 common/，回退 library/）。"""
    root_base = templates_dir()
    for base in ("common", "library"):
        root = os.path.join(root_base, base, subdir)
        if os.path.isdir(root):
            return sorted(f for f in os.listdir(root) if f.lower().endswith(".md"))
    return []


def load_library_template(subdir: str, name: str) -> str:
    """
    读取库模板。
    name 可为 'ct' 或 'ct.md'；subdir 如 'device_principles' / 'workflows'。
    优先 common/，兼容旧路径 library/。
    """
    if not name.endswith(".md"):
        name = f"{name}.md"
    for base in ("common", "library"):
        rel = os.path.join(base, subdir, name).replace("\\", "/")
        full = os.path.join(templates_dir(), rel.replace("/", os.sep))
        if os.path.isfile(full):
            return _read_md(rel)
    raise FileNotFoundError(f"未找到库模板: {subdir}/{name}")


def load_device_principle(device_key: str) -> str:
    return load_library_template("device_principles", device_key)


def load_workflow(workflow_key: str) -> str:
    return load_library_template("workflows", workflow_key)


# ---------------------------------------------------------------------------
# 常用文本模板（templates/common）
# ---------------------------------------------------------------------------


def _common_dir() -> str:
    return os.path.join(templates_dir(), "common")


def _common_catalog_path() -> str:
    return os.path.join(_common_dir(), "catalog.json")


def load_common_catalog() -> Dict[str, Any]:
    path = _common_catalog_path()
    if not os.path.isfile(path):
        # 工作区尚未拷贝时回退包内
        pkg = os.path.join(PACKAGE_TEMPLATES, "common", "catalog.json")
        if not os.path.isfile(pkg):
            return {"schema": "f1_common_text_templates/v1", "groups": []}
        path = pkg
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {"groups": []}


def list_common_groups() -> List[Dict[str, Any]]:
    """按正文栏目顺序排列常用模板分组（与表头栏目一致）。"""
    groups = list(load_common_catalog().get("groups") or [])
    # 正文栏目顺序；设备原理/流程库挂在危害因素分析之后
    body_order: List[str] = []
    try:
        for item in list_body_sections():
            sid = str(item.get("id") or "")
            if sid:
                body_order.append(sid)
            libs = item.get("libraries") if isinstance(item.get("libraries"), dict) else {}
            for lib_id in libs.keys():
                if lib_id and lib_id not in body_order:
                    # 紧随所属栏目
                    body_order.append(str(lib_id))
    except Exception:
        body_order = [
            "evaluation_basis",
            "evaluation_objective",
            "hazard_analysis",
            "device_principles",
            "workflows",
            "workplace_layout",
            "protection_measures",
        ]
    # 库分组兜底：若正文未声明 libraries，仍插在 hazard_analysis 后
    if "hazard_analysis" in body_order:
        hi = body_order.index("hazard_analysis")
        for lib_id in ("device_principles", "workflows"):
            if lib_id not in body_order:
                body_order.insert(hi + 1, lib_id)
                hi += 1

    by_id = {str(g.get("id") or ""): g for g in groups}
    ordered: List[Dict[str, Any]] = []
    seen = set()
    for gid in body_order:
        if gid in by_id and gid not in seen:
            ordered.append(by_id[gid])
            seen.add(gid)
    for g in groups:
        gid = str(g.get("id") or "")
        if gid not in seen:
            ordered.append(g)
            seen.add(gid)
    return ordered


def list_common_files() -> List[Dict[str, str]]:
    """列出常用文本模板文件，供工作台文件树展示。"""
    items: List[Dict[str, str]] = []
    common_root = _common_dir()
    for group in list_common_groups():
        gtitle = str(group.get("title") or group.get("id") or "")
        gid = str(group.get("id") or "")
        explicit = group.get("items") or []
        if explicit:
            for it in explicit:
                rel = str(it.get("path") or "").replace("\\", "/")
                if not rel:
                    continue
                full = os.path.join(common_root, rel.replace("/", os.sep))
                if not os.path.isfile(full):
                    # 回退包内
                    full = os.path.join(PACKAGE_TEMPLATES, "common", rel.replace("/", os.sep))
                    if not os.path.isfile(full):
                        continue
                entry = {
                    "path": f"templates/common/{rel}",
                    "label": str(it.get("title") or it.get("id") or rel),
                    "group": "常用文本模板",
                    "group_id": gid,
                    "group_title": gtitle,
                    "kind": str(it.get("kind") or ""),
                }
                if it.get("tables_key"):
                    entry["tables_key"] = str(it.get("tables_key"))
                    entry["table_index"] = int(it.get("table_index") or 0)
                items.append(entry)
            continue
        glob_pat = str(group.get("items_glob") or "")
        if not glob_pat:
            continue
        if "/*.md" in glob_pat:
            sub = glob_pat.split("/*.md")[0]
            root = os.path.join(common_root, sub.replace("/", os.sep))
            if not os.path.isdir(root):
                root = os.path.join(PACKAGE_TEMPLATES, "common", sub.replace("/", os.sep))
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                if not name.lower().endswith(".md"):
                    continue
                items.append(
                    {
                        "path": f"templates/common/{sub}/{name}",
                        "label": os.path.splitext(name)[0],
                        "group": "常用文本模板",
                        "group_id": gid,
                        "group_title": gtitle,
                        "kind": "library",
                    }
                )
    return items


def load_common_text(rel_path: str) -> str:
    """读取 common/ 下文本模板（md/txt）。rel_path 相对 common/。"""
    rel = str(rel_path or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError(f"非法 common 路径: {rel_path!r}")
    full = os.path.join(_common_dir(), rel.replace("/", os.sep))
    if not os.path.isfile(full):
        full = os.path.join(PACKAGE_TEMPLATES, "common", rel.replace("/", os.sep))
    if not os.path.isfile(full):
        raise FileNotFoundError(f"常用文本模板不存在: {rel}")
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


def load_common_json(rel_path: str) -> Dict[str, Any]:
    text = load_common_text(rel_path)
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def assemble_evaluation_basis_text() -> str:
    """由 common/evaluation_basis 各小节模板拼装主要评价依据全文。"""
    assemble_path = os.path.join(_common_dir(), "evaluation_basis", "assemble.json")
    if not os.path.isfile(assemble_path):
        assemble_path = os.path.join(
            PACKAGE_TEMPLATES, "common", "evaluation_basis", "assemble.json"
        )
    if os.path.isfile(assemble_path):
        meta = load_common_json("evaluation_basis/assemble.json")
        parts = meta.get("parts") or []
        joiner = str(meta.get("joiner") if meta.get("joiner") is not None else "\n")
        chunks: List[str] = []
        for part in parts:
            name = str(part)
            if not name.endswith(".md"):
                name = f"{name}.md"
            rel = f"evaluation_basis/{name}"
            try:
                chunks.append(load_common_text(rel).strip())
            except FileNotFoundError:
                continue
        text = joiner.join(c for c in chunks if c)
        if text.strip():
            return text.strip()
    # 回退 defaults JSON（避免与 load_field_default 循环）
    full = os.path.join(templates_dir(), "defaults", "evaluation_basis.json")
    if not os.path.isfile(full):
        full = os.path.join(PACKAGE_TEMPLATES, "defaults", "evaluation_basis.json")
    if os.path.isfile(full):
        with open(full, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return str(data.get("text") or "").strip()
    return ""


def load_field_default(field_key: str) -> Dict[str, Any]:
    """
    读取固定栏目默认模板。
    优先：common/<field_key>/assemble.json 拼装；
    其次：templates/defaults/<field_key>.json。
    """
    key = str(field_key or "").strip()
    if not key:
        return {}
    if key == "evaluation_basis":
        assemble_path = os.path.join(_common_dir(), "evaluation_basis", "assemble.json")
        if not os.path.isfile(assemble_path):
            assemble_path = os.path.join(
                PACKAGE_TEMPLATES, "common", "evaluation_basis", "assemble.json"
            )
        if os.path.isfile(assemble_path):
            meta = load_common_json("evaluation_basis/assemble.json")
            text = assemble_evaluation_basis_text()
            out = {
                "schema": "f1_eval_field_default/v1",
                "field_key": "evaluation_basis",
                "title": meta.get("title") or "主要评价依据",
                "source": meta.get("source") or "",
                "text": text,
                "field_style": meta.get("field_style") or {},
                "assembled_from": "common/evaluation_basis",
            }
            if text:
                return out
    full = os.path.join(templates_dir(), "defaults", f"{key}.json")
    if not os.path.isfile(full):
        full = os.path.join(PACKAGE_TEMPLATES, "defaults", f"{key}.json")
    if not os.path.isfile(full):
        return {}
    with open(full, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def load_default_evaluation_basis_text() -> str:
    """主要评价依据默认正文（南昌二附院 250075YP 样例全文）。"""
    return assemble_evaluation_basis_text()


def apply_evaluation_basis_default(
    data: Dict[str, Any],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """
    将主要评价依据默认模板写入 data（缺省或 force 时）。
    默认不从评价信息表取数，统一使用固定模板。
    """
    if not isinstance(data, dict):
        return data
    item = load_field_default("evaluation_basis")
    text = str(item.get("text") or "").strip()
    if not text:
        return data
    fields = data.get("fields")
    if not isinstance(fields, dict):
        fields = {}
        data["fields"] = fields
    cur = str(fields.get("evaluation_basis") or "").strip()
    if force or not cur:
        fields["evaluation_basis"] = text
    style_default = item.get("field_style")
    if isinstance(style_default, dict) and style_default:
        styles = data.get("field_styles")
        if not isinstance(styles, dict):
            styles = {}
            data["field_styles"] = styles
        if force or not isinstance(styles.get("evaluation_basis"), dict):
            styles["evaluation_basis"] = dict(style_default)
    return data


def _classify_shielding_classes(device_keys: Optional[List[str]] = None) -> List[str]:
    """按装置清单归入透视 / 普通 / 短时高剂量率。"""
    meta = {}
    try:
        meta = load_common_json("evaluation_objective/assemble.json")
    except Exception:
        meta = {}
    class_map = meta.get("shielding_class_map") or {
        "fluoroscopy": ["dsa", "gi", "ercp", "c_arm"],
        "general": ["ct", "mammo_dr", "bmd"],
        "short_high_dose_rate": ["dr"],
    }
    keys = [str(x).strip().lower() for x in (device_keys or []) if str(x).strip()]
    if not keys:
        # 未提供清单时默认三类都展示标准条文
        return ["fluoroscopy", "general", "short_high_dose_rate"]
    hit: List[str] = []
    for cls, aliases in class_map.items():
        alias_l = [str(a).strip().lower() for a in (aliases or [])]
        if any(k in alias_l or any(a in k for a in alias_l) for k in keys):
            hit.append(str(cls))
    return hit or ["fluoroscopy", "general", "short_high_dose_rate"]


def assemble_evaluation_objective(
    *,
    device_keys: Optional[List[str]] = None,
    devices: Optional[List[Dict[str, Any]]] = None,
    shielding_rooms_summary: str = "",
    shielding_project_summary: str = "",
    fluoroscopy_rooms_summary: str = "",
    include_table3_default: bool = True,
) -> Dict[str, Any]:
    """
    拼装评价目标正文与表。
    - ①④ 固定模板
    - ② 固定一整段（引言+a/b/c）；「综上」「WS76」来自评价信息表/装置清单自动生成（可编辑模板）
    - ③ 固定文字；表2接在①后；表3接在③文字后（正式应由信息表覆盖）
    """
    # 排版顺序：①原则 → 表2 → ②屏蔽/综上/WS76 → ③剂量目标文字 → 表3 → ④安全管理
    # 用 <<<F1_TABLE>>> 标记插表位置（与 content embed / 工作台编辑器一致）
    table_mark = "<<<F1_TABLE>>>"
    principles = load_common_text("evaluation_objective/01_principles.md").strip()
    shielding_parts: List[str] = [
        load_common_text("evaluation_objective/02_shielding.md").strip()
    ]

    # 综上 / WS76：优先显式入参 → 装置清单自动生成 → 模板文件
    project_para = shielding_project_summary.strip()
    fluoro_rooms = fluoroscopy_rooms_summary.strip()
    if devices:
        try:
            from f1_eval_report.templates.shielding_summary import (
                build_shielding_project_paragraph,
                build_ws76_rooms_summary,
            )

            if not project_para and not shielding_rooms_summary.strip():
                project_para = build_shielding_project_paragraph(devices)
            if not fluoro_rooms:
                fluoro_rooms = build_ws76_rooms_summary(devices)
        except Exception:
            pass
    if not project_para and shielding_rooms_summary.strip():
        body = shielding_rooms_summary.strip()
        project_para = body if body.startswith("综上") else f"综上，本项目涉及的{body}"
    if not project_para:
        project_para = load_common_text("evaluation_objective/02_project_summary.md").strip()
    if project_para:
        shielding_parts.append(project_para)

    if not fluoro_rooms:
        # 模板可能已是整句
        ws_tpl = load_common_text("evaluation_objective/02_ws76.md").strip()
        if "{fluoroscopy_rooms_summary}" not in ws_tpl:
            shielding_parts.append(ws_tpl)
    else:
        ws_tpl = load_common_text("evaluation_objective/02_ws76.md").strip()
        if "{fluoroscopy_rooms_summary}" in ws_tpl:
            shielding_parts.append(
                ws_tpl.replace("{fluoroscopy_rooms_summary}", fluoro_rooms).strip()
            )
        elif fluoro_rooms.startswith("根据WS"):
            shielding_parts.append(fluoro_rooms)
        else:
            shielding_parts.append(
                "根据WS 76-2020《医用X射线诊断设备质量控制检测规范》附录B表B.1的要求，本项目"
                f"{fluoro_rooms}透视防护区检测平面上周围剂量当量率应不大于400μSv/h。"
            )

    dose_intro = load_common_text("evaluation_objective/03_dose_target_intro.md").strip()
    safety = load_common_text("evaluation_objective/04_safety_management.md").strip()
    mid = "\n".join(p for p in shielding_parts + [dose_intro] if p)
    intro = "\n\n".join(
        p
        for p in [principles, table_mark, mid, table_mark, safety]
        if p
    )
    tables: List[Dict[str, Any]] = []
    try:
        tables.append(load_common_json("evaluation_objective/table2_dose_limits.json"))
    except Exception:
        pass
    if include_table3_default:
        try:
            tables.append(
                load_common_json("evaluation_objective/table3_dose_targets.default.json")
            )
        except Exception:
            pass
    classes = _classify_shielding_classes(device_keys)
    return {
        "evaluation_objective_intro": intro,
        "evaluation_objective_tables": tables,
        "shielding_classes": classes,
        "shielding_project_summary": project_para,
        "fluoroscopy_rooms_summary": fluoro_rooms,
    }


def ensure_evaluation_objective_table_marks(intro: str) -> str:
    """
    为旧版评价目标正文补齐插表标记：
    表2 接在「1.放射防护基本原则」后；表3 接在「3.剂量管理目标值」文字后。
    """
    import re

    s = str(intro or "")
    if not s.strip():
        return s
    if "<<<F1_TABLE>>>" in s:
        return s
    # 去掉旧的「全部表居中」标记，改为分点插入
    s = s.replace("<<<F1_AFTER_TABLES>>>", "").strip()
    if "<<<F1_TABLE>>>" not in s:
        m2 = re.search(r"\n\s*2[.\s、．]*工作场所", s)
        if m2:
            s = s[: m2.start()].rstrip() + "\n\n<<<F1_TABLE>>>\n" + s[m2.start() :].lstrip("\n")
        m4 = re.search(r"\n\s*4[.\s、．]*放射防护安全措施", s)
        if m4:
            s = s[: m4.start()].rstrip() + "\n\n<<<F1_TABLE>>>\n" + s[m4.start() :].lstrip("\n")
    return s

