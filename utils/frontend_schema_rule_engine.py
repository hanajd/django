import hashlib
import re
from typing import Any, Dict, List, Tuple


_TYPE_NUMBER_KEYWORDS = (
    "kv",
    "ma",
    "温度",
    "湿度",
    "长度",
    "百分比",
    "分辨力",
    "误差",
    "剂量",
    "厚度",
    "直径",
    "电流",
    "电压",
    "时间",
    "次数",
    "距离",
    "角度",
    "%",
)
_TYPE_DATE_KEYWORDS = ("日期", "年", "月", "日", "date")
_TYPE_TEXTAREA_KEYWORDS = ("备注", "结论", "说明", "描述", "意见", "comment")
_SIGNATURE_KEYWORDS = ("签字", "签名", "检测员", "校核", "审核", "批准", "review", "approve", "sign")
_INSTRUMENT_KEYWORDS = ("仪器", "检定", "有效期", "型号", "编号")
_DEVICE_INFO_KEYWORDS = ("设备型号", "额定参数", "设备名称", "设备编号", "设备所在场所", "生产厂家", "设备场所")

_UNIT_HINTS = (
    ("温度", "℃"),
    ("湿度", "%RH"),
    ("kv", "kV"),
    ("ma", "mA"),
    ("长度", "mm"),
    ("距离", "mm"),
    ("剂量", "mGy/min"),
    ("时间", "s"),
    ("角度", "°"),
)

_TERM_MAP = {
    "报告": "report",
    "编号": "no",
    "委托": "commission",
    "受检": "inspection",
    "检测": "test",
    "日期": "date",
    "年": "year",
    "月": "month",
    "日": "day",
    "环境": "environment",
    "温度": "temperature",
    "湿度": "humidity",
    "设备": "device",
    "名称": "name",
    "型号": "model",
    "厂家": "manufacturer",
    "生产": "production",
    "单位": "unit",
    "地址": "address",
    "联系人": "contact",
    "电话": "phone",
    "类型": "type",
    "状态": "status",
    "验收": "acceptance",
    "仪器": "instrument",
    "签字": "signature",
    "签名": "signature",
    "结论": "conclusion",
    "备注": "remark",
    "结果": "result",
    "高对比": "highContrast",
    "低对比": "lowContrast",
    "分辨力": "resolution",
    "均匀性": "uniformity",
    "测距": "distance",
    "误差": "error",
    "剂量": "dose",
    "偏差": "deviation",
    "亮度": "brightness",
    "自动": "auto",
    "控制": "control",
    "图像": "image",
}

_DEFAULT_QC_CONSTANTS = {
    "highContrastResolutionLimit": 5.0,
    "lowContrastResolutionLimit": 0.3,
    "imageUniformityLimit": 5.0,
    "distanceErrorLimit": 2.0,
    "kapDeviationLimit": 10.0,
}

_DEFAULT_QC_ENUMS = {
    "unitType": [
        {"value": "kV", "label": "千伏"},
        {"value": "mA", "label": "毫安"},
        {"value": "mm", "label": "毫米"},
        {"value": "℃", "label": "摄氏度"},
        {"value": "%RH", "label": "相对湿度"},
        {"value": "%", "label": "百分比"},
        {"value": "lp/mm", "label": "线对/毫米"},
        {"value": "cd/m2", "label": "坎德拉/平方米"},
    ],
    "deviceType": [
        {"value": "ct", "label": "CT"},
        {"value": "dr", "label": "DR"},
        {"value": "dsa", "label": "DSA"},
        {"value": "mammography", "label": "乳腺机"},
        {"value": "cArm", "label": "C臂机"},
    ],
    "controlMode": [
        {"value": "auto", "label": "自动控制"},
        {"value": "manual", "label": "手动控制"},
    ],
}

_UNDERSCORE_SUFFIX_MAP = {
    "kv": "kv",
    "ma": "ma",
    "ww": "ww",
    "wl": "wl",
    "lp": "lp",
    "rv": "rv",
    "roi": "roi",
    "报出值": "reportValue",
    "判定": "verdict",
    "结果": "result",
    "测量值": "measureValue",
    "真实长度": "realLength",
    "测量长度": "measureLength",
}

_UNDERSCORE_LABEL_MAP = {
    "kv": "kV",
    "ma": "mA",
    "ww": "WW",
    "wl": "WL",
    "lp": "LP",
    "rv": "RV",
    "roi": "ROI",
    "reportValue": "报出值",
    "verdict": "判定",
    "result": "结果",
    "measureValue": "测量值",
    "realLength": "真实长度",
    "measureLength": "测量长度",
}


def _slug_ascii(text: str, fallback: str) -> str:
    raw = (text or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw:
        raw = fallback
    if not re.match(r"^[a-z_]", raw):
        raw = f"id_{raw}"
    return raw


def _underscore_stable_slug(label: str) -> str:
    """
    下划线分层里用作 step/section 的 id：优先 ASCII slug；纯中文等会塌成 id_/空时，
    对完整 label 做稳定哈希，使「前缀_字段」中每一层前缀互不合并。
    """
    slug = _slug_ascii(label, "")
    if slug and slug != "id_" and len(slug) >= 2:
        return slug
    h = hashlib.sha256((label or "").encode("utf-8")).hexdigest()[:12]
    return f"u{h}"


def _camel_case(text: str, fallback: str) -> str:
    parts = [p for p in _slug_ascii(text, fallback).split("_") if p]
    if not parts:
        return fallback
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _to_english_id(label: str, source_id: str, fallback: str) -> str:
    src = str(label or source_id or "").strip()
    if src and re.match(r"^[a-z][A-Za-z0-9]*$", src):
        return src
    # 语义特判：
    # - 单独出现“单位/单位1/单位2...”表示计量单位(unit)
    # - 组织语境（受检单位/委托单位/单位名称/单位地址等）表示机构(organization)
    if re.match(r"^单位\\d*$", src):
        return _camel_case(src.replace("单位", "unit"), fallback)
    if any(token in src for token in ("受检单位", "委托单位", "单位名称", "单位地址", "单位联系人")):
        src = src.replace("单位", "organization")
    lowered = src.lower()
    if re.match(r"^[a-z0-9_ -]+$", lowered):
        return _camel_case(lowered, fallback)
    tokens: List[str] = []
    for zh, en in _TERM_MAP.items():
        if zh in src:
            tokens.append(en)
    if not tokens:
        return fallback
    out = tokens[0] + "".join(t[:1].upper() + t[1:] for t in tokens[1:])
    return out if re.match(r"^[a-z][A-Za-z0-9]*$", out) else fallback


def _infer_report_type(template_id: str, template_name: str) -> str:
    text = f"{template_id} {template_name}".lower()
    if "ct" in text:
        return "ct"
    if "dr" in text:
        return "dr"
    if "cr" in text:
        return "cr"
    if "dsa" in text or "血管" in text:
        return "dsa"
    if "透视" in text or "fluoro" in text:
        return "xray_fluoroscopy"
    if "乳腺" in text:
        return "mammography"
    return ""


def _infer_standard(template_name: str) -> str:
    text = template_name or ""
    if "WS76" in text or "透视" in text or "DSA" in text:
        return "WS76-2020"
    return ""


def _infer_type(raw_type: str, label: str) -> str:
    rt = (raw_type or "").strip().lower()
    title = (label or "").strip()
    if rt == "image" or any(k in title for k in _SIGNATURE_KEYWORDS):
        return "signature"
    if rt == "check":
        if "验收" in title or "状态" in title or "模式" in title:
            return "radio"
        return "boolean"
    if any(k in title for k in _TYPE_DATE_KEYWORDS):
        return "date"
    if any(k in title.lower() for k in _TYPE_NUMBER_KEYWORDS) or any(k in title for k in _TYPE_NUMBER_KEYWORDS):
        return "number"
    if any(k in title for k in _TYPE_TEXTAREA_KEYWORDS):
        return "textarea"
    return "text"


def _pick_unit(label: str) -> str:
    low = (label or "").lower()
    for k, unit in _UNIT_HINTS:
        if k.lower() in low or k in label:
            return unit
    return ""


def _classify_group(label: str, field_type: str) -> Tuple[str, str, str, str, str]:
    title = label or ""
    if field_type == "signature" or any(k in title for k in _SIGNATURE_KEYWORDS):
        return ("step_signature", "综合结论与签字", "sec_signature", "签字与结论", "form")
    # 检测仪器优先：包括“检测仪器_*”以及日期类字段
    if "检测仪器" in title or any(k in title for k in _INSTRUMENT_KEYWORDS):
        return ("step_instruments", "检测仪器", "sec_instruments", "检测仪器清单", "table")
    # 设备信息强制归集
    if any(k in title for k in _DEVICE_INFO_KEYWORDS):
        return ("step_basic_info", "基本信息", "sec_device_info", "设备信息", "form")
    if any(k in title for k in ("判定", "合格", "结论")):
        return ("step_judgement", "结果判定", "sec_judgement", "判定结果", "form")
    if any(k in title for k in ("高对比",)):
        return ("step_qc_items", "质控检测项目", "sec_high_contrast", "高对比分辨力", "form")
    if any(k in title for k in ("低对比",)):
        return ("step_qc_items", "质控检测项目", "sec_low_contrast", "低对比分辨力", "form")
    if any(k in title for k in ("图像均匀性", "均匀性")):
        return ("step_qc_items", "质控检测项目", "sec_image_uniformity", "图像均匀性", "form")
    if any(k in title for k in ("测距误差", "测距", "距离误差")):
        return ("step_qc_items", "质控检测项目", "sec_distance_error", "测距误差", "form")
    if any(k in title for k in ("KAP", "ABC", "剂量", "分辨力", "误差")):
        return ("step_qc_items", "质控检测项目", "sec_qc_items", "检测项目", "form")
    if any(k in title for k in ("温度", "湿度", "环境")):
        return ("step_basic_info", "基本信息", "sec_environment", "环境条件", "form")
    if any(k in title for k in ("报告", "委托", "日期", "编号", "受检")):
        return ("step_basic_info", "基本信息", "sec_report_info", "报告信息", "form")
    return ("step_basic_info", "基本信息", "sec_device_info", "设备信息", "form")


def _classify_by_underscore(raw_key: str) -> Tuple[str, str, str, str] | None:
    """
    下划线分层规则（由命名本身定义层级，不做中文语义归类）：
    - 最后一段为字段名，其前整段 `prefix` 为 section 分组键；
    - 纯中文等无法用 ASCII slug 区分时，对 `prefix` 做稳定哈希生成唯一 section id，避免合并到同一 sec；
    - section 展示标题始终为 `prefix`（或中间段用「 / 」连接）。
    """
    key = str(raw_key or "").strip()
    if "_" not in key:
        return None
    parts = [p for p in key.split("_") if p]
    if len(parts) < 2:
        return None
    prefix = "_".join(parts[:-1])
    head = parts[0].lower()
    joined = key.lower()
    # 检测仪器分层强制落到检测仪器步骤
    if "检测仪器" in key or head in {"instrument", "instruments"}:
        return ("step_instruments", "检测仪器", "sec_instruments", "检测仪器")
    # 质控检测类关键字统一归到 step_qc_items，避免 step 被拆得过细
    if (
        head in {"kerma", "contrast", "uniformity", "distance", "kap", "abc", "dose"}
        or any(
            token in joined
            for token in (
                "高对比",
                "低对比",
                "均匀性",
                "测距",
                "kap",
                "abc",
                "剂量",
                "kerma",
                "contrast",
                "uniformity",
                "distance",
            )
        )
    ):
        step_id = "step_qc_items"
        step_title = "质控检测项目"
        # section 由「除最后一段外」整段前缀唯一确定，不猜中文语义；纯中文前缀用稳定哈希避免塌成同一 sec
        section_slug = _underscore_stable_slug(prefix)
        sec_id = f"sec_{section_slug}"
        sec_title = prefix if prefix else " / ".join(parts[:-1])
        return step_id, step_title, sec_id, sec_title

    step_key = _underscore_stable_slug(parts[0])
    step_title = parts[0]
    step_id = f"step_{step_key}"
    section_slug = _underscore_stable_slug(prefix)
    sec_id = f"sec_{section_slug}"
    sec_title = " / ".join(parts[:-1])
    return step_id, step_title, sec_id, sec_title


def _underscore_field_id(hierarchy_key: str, fallback_id: str) -> str:
    parts = [p for p in str(hierarchy_key or "").split("_") if p]
    if len(parts) < 2:
        return fallback_id
    suffix = parts[-1]
    mapped = _UNDERSCORE_SUFFIX_MAP.get(suffix, "")
    if not mapped:
        mapped = _to_english_id(suffix, suffix, fallback_id)
    out = _camel_case(mapped, fallback_id)
    return out or fallback_id


def _underscore_field_label(hierarchy_key: str, fallback_label: str) -> str:
    parts = [p for p in str(hierarchy_key or "").split("_") if p]
    if len(parts) < 2:
        return fallback_label
    suffix = parts[-1]
    mapped = _UNDERSCORE_SUFFIX_MAP.get(suffix, "")
    if not mapped:
        mapped = _to_english_id(suffix, suffix, _camel_case(suffix, "value"))
    display = _UNDERSCORE_LABEL_MAP.get(mapped, "")
    if display:
        return display
    # 中文后缀直接显示中文；英文后缀做简单格式化显示
    if re.search(r"[\u4e00-\u9fff]", suffix):
        return suffix
    return mapped or suffix or fallback_label


def _normalize_pdf_field(item: Dict[str, Any], idx: int) -> Dict[str, Any]:
    page = int(item.get("page") or 1)
    x = float(item.get("x") or 0)
    y = float(item.get("y") or 0)
    w = float(item.get("w") or 0)
    h = float(item.get("h") or 0)
    label = str(item.get("title") or item.get("placeholder") or item.get("id") or f"字段{idx}").strip()
    source_id = str(item.get("id") or item.get("placeholder") or item.get("pdfFieldId") or f"f{idx}").strip()
    return {
        "rawId": source_id,
        "label": label or source_id or f"字段{idx}",
        "fieldType": str(item.get("fieldType") or "text"),
        "pdfFieldId": str(item.get("pdfFieldId") or item.get("id") or f"f{idx}").strip(),
        "page": page,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "order": (page, y, x, idx),
    }


def _strip_coordinate_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    for step in payload.get("steps", []):
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            for field in section.get("fields", []):
                if not isinstance(field, dict):
                    continue
                for key in ("pdfAnchor", "page", "x", "y", "w", "h", "pdfFieldId"):
                    field.pop(key, None)
                field.pop("__order", None)
    return payload


def _sort_fields_by_coordinate_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    for step in payload.get("steps", []):
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list):
                continue
            section["fields"] = sorted(
                fields,
                key=lambda f: (
                    (f.get("__order") or (999, 999999, 999999, 999999))[0],
                    (f.get("__order") or (999, 999999, 999999, 999999))[1],
                    (f.get("__order") or (999, 999999, 999999, 999999))[2],
                    str(f.get("id") or ""),
                ) if isinstance(f, dict) else (999, 999999, 999999, "zzz"),
            )
    return payload


def _force_signature_step_last(payload: Dict[str, Any]) -> Dict[str, Any]:
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return payload
    normal_steps: List[Dict[str, Any]] = []
    signature_steps: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = str(step.get("id") or "")
        has_signature = False
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            for field in section.get("fields", []):
                if isinstance(field, dict) and str(field.get("type") or "").lower() == "signature":
                    has_signature = True
                    break
            if has_signature:
                break
        if sid == "step_signature" or has_signature:
            signature_steps.append(step)
        else:
            normal_steps.append(step)
    payload["steps"] = normal_steps + signature_steps
    return payload


def _inject_underscore_section_rules(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    对下划线分层形成的 section 注入基础可见性规则：
    - sec_kerma_max_high -> section.visibleWhen = hasAec == true
    - 该 section 内除 hasHighDoseMode 外字段 -> visibleWhen = hasHighDoseMode == true
    """
    for step in payload.get("steps", []):
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            sid = str(section.get("id") or "")
            if sid == "sec_kerma_max_high":
                section.setdefault("visibleWhen", "hasAec == true")
                for field in section.get("fields", []):
                    if not isinstance(field, dict):
                        continue
                    if field.get("id") == "hasHighDoseMode":
                        continue
                    field.setdefault("visibleWhen", "hasHighDoseMode == true")
    return payload


def _split_date_base_from_field(field: Dict[str, Any]) -> str:
    fid = str(field.get("id") or "")
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    key = str(src.get("key") or "")
    label = str(field.get("label") or "")
    # 签名类字段不得参与年月日合并
    if any(token in label for token in _SIGNATURE_KEYWORDS) or any(token in key for token in _SIGNATURE_KEYWORDS):
        return ""

    def _clean_base(text: str) -> str:
        # 去掉“年月日”拆分后遗留的尾部分隔符，避免导出 label 出现“检测仪器_”这类脏值。
        return str(text or "").strip().rstrip("_-/:： ").strip()

    # 优先按下划线规则：xxx_year / xxx_month / xxx_day -> xxx
    for text in (key, fid):
        low = text.lower()
        for suffix in ("_year", "_month", "_day"):
            if low.endswith(suffix):
                return _clean_base(text[: -len(suffix)])

    # 驼峰规则：xxxYear / xxxMonth / xxxDay -> xxx
    for text in (fid,):
        for suffix in ("Year", "Month", "Day"):
            if text.endswith(suffix) and len(text) > len(suffix):
                return _clean_base(text[: -len(suffix)])

    # 中文规则：检测日期年/月/日 -> 检测日期
    for text in (key, label):
        if text.endswith("年") or text.endswith("月") or text.endswith("日"):
            return _clean_base(text[:-1])

    return ""


def _merge_split_date_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    前端导出时将年月日拆分字段合并成一个 date 输入。
    规则：只保留下划线前面的主字段（如 test_date_year -> testDate）。
    """
    for step in payload.get("steps", []):
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list) or not fields:
                continue
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            passthrough: List[Dict[str, Any]] = []
            for f in fields:
                if not isinstance(f, dict):
                    continue
                base = _split_date_base_from_field(f)
                if base:
                    grouped.setdefault(base, []).append(f)
                else:
                    passthrough.append(f)

            merged: List[Dict[str, Any]] = []
            for base, items in grouped.items():
                if not items:
                    continue
                # 取第一个作为模板，聚合 required/default/width/source
                seed = dict(items[0])
                base_id = _camel_case(base, _camel_case(str(seed.get("id") or "testDate"), "testDate"))
                src = seed.get("source") if isinstance(seed.get("source"), dict) else {}
                base_label = str(seed.get("label") or base).rstrip("年月日").rstrip("_-/:： ").strip() or base
                if base_label in {"检测仪器", "instrument", "instruments"}:
                    base_label = "检测仪器日期"
                merged_field: Dict[str, Any] = {
                    "id": base_id,
                    "type": "date",
                    "label": base_label,
                    "required": any(bool(x.get("required")) for x in items if isinstance(x, dict)),
                    "defaultValue": None,
                    "width": str(seed.get("width") or "half"),
                    "source": {
                        "key": base,
                        "pdfFieldId": str(src.get("pdfFieldId") or src.get("key") or base),
                        "page": int(src.get("page") or 1),
                        "anchorType": "text",
                    },
                }
                merged.append(merged_field)

            # 保持原顺序：先放保留字段，再把合并字段补在末尾
            section["fields"] = passthrough + merged
    return payload


def _force_signature_semantics(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    修正特例：校核员及校核日期属于签名，不应转为 date。
    """
    for step in payload.get("steps", []):
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            for field in section.get("fields", []):
                if not isinstance(field, dict):
                    continue
                label = str(field.get("label") or "")
                if "校核员及校核日期" in label:
                    field["type"] = "signature"
                    field["defaultValue"] = None
    return payload


def _ensure_basic_defaults(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload["reportType"] = payload.get("reportType") or "radiationQc"
    payload["standard"] = payload.get("standard") or "WS 521-2017 医用X射线诊断设备质量控制检测规范"
    tid = str(payload.get("templateId") or "template_frontend")
    payload["pdfUrl"] = payload.get("pdfUrl") or f"/pdf/templates/{tid}.pdf"
    constants = payload.get("constants") if isinstance(payload.get("constants"), dict) else {}
    for k, v in _DEFAULT_QC_CONSTANTS.items():
        constants.setdefault(k, v)
    payload["constants"] = constants
    enums = payload.get("enums") if isinstance(payload.get("enums"), dict) else {}
    for k, v in _DEFAULT_QC_ENUMS.items():
        enums.setdefault(k, v)
    enums.setdefault("testType", [{"value": "acceptance", "label": "验收检测"}, {"value": "status", "label": "状态检测"}])
    enums.setdefault("yesNo", [{"value": "yes", "label": "是"}, {"value": "no", "label": "否"}])
    payload["enums"] = enums
    return payload


def _post_optimize_js117(payload: Dict[str, Any]) -> Dict[str, Any]:
    # 针对 JS-117 的结构优化：合并分拆日期、补充关键基础字段、修正委托单位类型。
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    report_sec = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if isinstance(section, dict) and section.get("id") == "sec_report_info":
                report_sec = section
                break
        if report_sec:
            break
    if not isinstance(report_sec, dict):
        return payload
    fields = report_sec.get("fields") if isinstance(report_sec.get("fields"), list) else []
    # 删除拆分日期字段
    fields = [f for f in fields if not (isinstance(f, dict) and f.get("id") in {"testDateYear", "testDateMonth", "testDateDay"})]
    existing_ids = {f.get("id") for f in fields if isinstance(f, dict)}
    if "testDate" not in existing_ids:
        fields.insert(0, {"id": "testDate", "type": "date", "label": "检测日期", "required": True, "defaultValue": None, "width": "half"})
    if "commissionNo" not in existing_ids:
        fields.insert(0, {"id": "commissionNo", "type": "text", "label": "委托编号", "required": True, "width": "half"})
    if "reportNo" not in existing_ids:
        fields.insert(0, {"id": "reportNo", "type": "text", "label": "报告编号", "placeholder": "如：JS-117-2024-xxx", "required": True, "width": "half"})
    for f in fields:
        if not isinstance(f, dict):
            continue
        if f.get("id") == "commissionOrganization":
            f["type"] = "text"
            f.setdefault("placeholder", "输入委托单位名称")
            f["width"] = "half"
    report_sec["fields"] = fields
    return payload


def build_frontend_schema_by_rules(template_obj: Dict[str, Any]) -> Dict[str, Any]:
    meta = template_obj.get("meta") if isinstance(template_obj.get("meta"), dict) else {}
    template_id = str(template_obj.get("templateId") or meta.get("templateId") or "").strip()
    template_name = str(template_obj.get("templateName") or meta.get("templateName") or "").strip()
    version = str(template_obj.get("version") or meta.get("version") or "1.0.0").strip()
    report_type = str(template_obj.get("reportType") or meta.get("reportType") or "").strip() or _infer_report_type(template_id, template_name)
    standard = str(template_obj.get("standard") or meta.get("standard") or "").strip() or _infer_standard(template_name)
    pdf_url = str(template_obj.get("pdfUrl") or meta.get("pdfUrl") or "").strip()
    locale = str(template_obj.get("locale") or meta.get("locale") or "zh-CN").strip() or "zh-CN"
    constants = template_obj.get("constants") if isinstance(template_obj.get("constants"), dict) else {}
    enums = template_obj.get("enums") if isinstance(template_obj.get("enums"), dict) else {}

    pdf = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
    fields_raw = pdf.get("fields") if isinstance(pdf.get("fields"), list) else []
    if not fields_raw:
        fields_raw = template_obj.get("fields") if isinstance(template_obj.get("fields"), list) else []

    latest_by_key: Dict[str, Dict[str, Any]] = {}
    for idx, row in enumerate(fields_raw, start=1):
        if not isinstance(row, dict):
            continue
        item = _normalize_pdf_field(row, idx)
        key = item["pdfFieldId"] or item["rawId"] or f"f{idx}"
        latest_by_key[key] = item

    step_order = ["step_basic_info", "step_instruments", "step_qc_items", "step_judgement", "step_signature"]
    step_title_map = {
        "step_basic_info": "基本信息",
        "step_instruments": "检测仪器",
        "step_qc_items": "质控检测项目",
        "step_judgement": "结果判定",
        "step_signature": "综合结论与签字",
    }
    section_bucket: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in step_order}
    used_ids = set()
    found_test_type_checks = set()
    instrument_check_labels: List[str] = []

    sorted_items = sorted(latest_by_key.values(), key=lambda it: it.get("order", (999, 999999, 999999, 999999)))
    for idx, item in enumerate(sorted_items, start=1):
        # 规则1：第一页勾选项归位（状态检测/验收检测）
        if item.get("page") == 1 and item.get("fieldType", "").lower() == "check":
            if item["label"] in {"状态检测", "验收检测"}:
                found_test_type_checks.add(item["label"])
                continue
            if re.match(r"^仪器\\d+$", item["label"]):
                instrument_check_labels.append(item["label"])
                continue

        ft = _infer_type(item["fieldType"], item["label"])
        # 规则2：下划线自动分层优先（同前缀归同一层级）
        label_key = str(item.get("label") or "").strip()
        raw_key = str(item.get("rawId") or "").strip()
        hierarchy_key = label_key if "_" in label_key else raw_key
        hierarchy = _classify_by_underscore(hierarchy_key)
        if hierarchy:
            step_id, step_title, sec_id, sec_title = hierarchy
            layout = "form"
        else:
            step_id, step_title, sec_id, sec_title, layout = _classify_group(item["label"], ft)
        if step_id not in section_bucket:
            section_bucket[step_id] = {}
            step_order.append(step_id)
            step_title_map[step_id] = step_title
        sec = section_bucket[step_id].setdefault(
            sec_id,
            {"id": sec_id, "title": sec_title, "layout": "table" if layout == "table" else "form", "fields": []},
        )
        fallback_id = f"field{idx}"
        # 下划线命名时，字段 id 使用最后一段语义（同前缀字段归一分组）
        # 例如：高对比分辨力_kv / 高对比分辨力_报出值 -> kv / reportValue
        if hierarchy:
            field_id = _underscore_field_id(hierarchy_key, fallback_id)
            field_label = _underscore_field_label(hierarchy_key, item["label"])
        else:
            field_id = _to_english_id(item["label"], item["rawId"], fallback_id)
            field_label = item["label"]
        base_id = field_id
        n = 2
        while field_id in used_ids:
            field_id = f"{base_id}{n}"
            n += 1
        used_ids.add(field_id)
        field_obj: Dict[str, Any] = {
            "id": field_id,
            "type": ft,
            "label": field_label,
            "required": False,
            "defaultValue": False if ft == "boolean" else None,
            "width": "half",
            "source": {
                "key": item["rawId"] or field_id,
                "pdfFieldId": item["pdfFieldId"],
                "page": item["page"],
                "anchorType": item["fieldType"],
            },
            "__order": item.get("order", (999, 999999, 999999, idx)),
        }
        if ft == "number":
            field_obj["precision"] = 1
            unit = _pick_unit(item["label"])
            if unit:
                field_obj["unit"] = unit
        if ft in {"radio", "select"}:
            field_obj["enumRef"] = "testType" if ("验收" in item["label"] or "状态" in item["label"]) else "yesNo"
            field_obj["defaultValue"] = "status" if field_obj["enumRef"] == "testType" else "no"
        if ft == "textarea":
            field_obj["minLines"] = 3
            field_obj["maxLines"] = 8
            field_obj["width"] = "full"
        if ft == "computed":
            field_obj["dependsOn"] = []
            field_obj["formula"] = ""
        if ft == "verdict":
            field_obj["rule"] = ""
        sec["fields"].append(field_obj)

    # 归位：第一页“状态检测/验收检测”合并成 testType（报告信息）
    if found_test_type_checks:
        sec = section_bucket["step_basic_info"].setdefault(
            "sec_report_info",
            {"id": "sec_report_info", "title": "报告信息", "layout": "form", "fields": []},
        )
        has_test_type = any(isinstance(f, dict) and f.get("id") == "testType" for f in sec.get("fields", []))
        if not has_test_type:
            sec["fields"].insert(
                0,
                {
                    "id": "testType",
                    "type": "radio",
                    "label": "检测类型",
                    "enumRef": "testType",
                    "required": True,
                    "defaultValue": "status" if "状态检测" in found_test_type_checks else "acceptance",
                    "width": "half",
                    "source": {"key": "检测类型", "pdfFieldId": "检测类型", "page": 1, "anchorType": "check"},
                },
            )

    # 归位：第一页“仪器1..n”转为检测仪器表格（无坐标，仅前端渲染）
    if instrument_check_labels:
        instrument_check_labels = sorted(set(instrument_check_labels), key=lambda x: int(re.sub(r"\\D+", "", x) or 0))
        sec = section_bucket["step_instruments"].setdefault(
            "sec_instruments",
            {"id": "sec_instruments", "title": "检测仪器清单", "layout": "table", "fields": []},
        )
        has_table = any(isinstance(f, dict) and f.get("id") == "instruments" and f.get("type") == "table" for f in sec.get("fields", []))
        if not has_table:
            sec["fields"].append(
                {
                    "id": "instruments",
                    "type": "table",
                    "label": "检测仪器",
                    "required": True,
                    "width": "full",
                    "columns": [
                        {"id": "instrumentName", "label": "仪器名称", "type": "text"},
                        {"id": "instrumentModel", "label": "型号规格", "type": "text"},
                        {"id": "instrumentSerialNo", "label": "仪器编号", "type": "text"},
                        {"id": "calibrationDate", "label": "校准日期", "type": "date"},
                        {"id": "isSelected", "label": "选用", "type": "boolean", "defaultValue": False},
                    ],
                    "initialRows": [{"instrumentName": name, "isSelected": False} for name in instrument_check_labels],
                    "source": {"key": "检测仪器清单", "pdfFieldId": "检测仪器清单", "page": 1, "anchorType": "table"},
                }
            )

    steps: List[Dict[str, Any]] = []
    for sid in step_order:
        sections_map = section_bucket.get(sid, {})
        if not sections_map:
            continue
        sections = [sections_map[k] for k in sections_map]
        steps.append({"id": sid, "title": step_title_map.get(sid, sid), "sections": sections})

    if not steps:
        steps = [{"id": "step_basic_info", "title": "基本信息", "sections": [{"id": "sec_misc", "title": "杂项", "layout": "form", "fields": []}]}]

    result = {
        "templateId": template_id or "template_frontend",
        "templateName": template_name or "template_frontend.json",
        "version": version or "1.0.0",
        "reportType": report_type,
        "standard": standard,
        "pdfUrl": pdf_url,
        "locale": locale,
        "constants": constants,
        "enums": enums,
        "steps": steps,
    }
    result = _ensure_basic_defaults(result)
    if "js-117" in (result.get("templateId", "").lower() + " " + result.get("templateName", "").lower()):
        result = _post_optimize_js117(result)
    result = _force_signature_semantics(result)
    result = _merge_split_date_fields(result)
    result = _sort_fields_by_coordinate_order(result)
    result = _inject_underscore_section_rules(result)
    result = _force_signature_step_last(result)
    result = _strip_coordinate_keys(result)
    return result

