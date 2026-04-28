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
_INSTRUMENT_KEYWORDS = ("仪器", "检定", "有效期", "型号")
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
    "doseRateUnit": [
        {"value": "uGyPerSec", "label": "μGy/s"},
        {"value": "uGyPerMin", "label": "μGy/min"},
        {"value": "mGyPerMin", "label": "mGy/min"},
    ],
    "commissionOrgMode": [
        {"value": "sameInspection", "label": "同受检单位"},
        {"value": "customCommission", "label": "委托单位"},
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

_SUBMIT_BUCKET_ORDER = {
    "reportInfo": 0,
    "hospitalInfo": 1,
    "equipmentInfo": 2,
    "instruments": 3,
    "testResult": 4,
    "signatures": 5,
}


def _normalize_brackets_in_segment(text: str) -> str:
    s = str(text or "").replace("□", "").replace("", "").strip()
    if not s:
        return ""
    # 仅清理每个分段最左/最右端的常见标点，不处理中间内容。
    edge_punct = "，。；：、,.!?！？:;·"
    s = s.strip(edge_punct).strip()
    if not s:
        return ""
    while s and s[-1] in ("(", "（"):
        s = s[:-1].rstrip()
    while s and s[:1] in (")", "）"):
        s = s[1:].lstrip()
    if not s:
        return ""
    left_count = sum(1 for ch in s if ch in ("(", "（"))
    right_count = sum(1 for ch in s if ch in (")", "）"))
    if left_count > right_count:
        s = f"{s}{')' * (left_count - right_count)}"
    elif right_count > left_count:
        s = f"{'(' * (right_count - left_count)}{s}"
    return s.strip()


def normalize_field_text_by_underscore_rules(text: str) -> str:
    """
    字段文本标准化：
    - 去掉 OCR 误识别的 `□`
    - 按下划线分段做括号纠偏与补全
    - 保留 `/` 等业务符号（如 gy/Min）
    """
    raw = str(text or "")
    if not raw:
        return ""
    # 统一各类“下划线”字符，避免 OCR/字体差异导致分层失效。
    for sep in ("＿", "﹍", "﹎", "﹏"):
        raw = raw.replace(sep, "_")
    parts = raw.split("_")
    normalized_parts = [_normalize_brackets_in_segment(p) for p in parts]
    normalized_parts = [p for p in normalized_parts if p]
    return "_".join(normalized_parts).strip()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(t in text for t in tokens)


def _assign_submit_bucket(field_obj: Dict[str, Any]) -> Dict[str, str]:
    """
    为前端字段标注提交归属桶，便于前端渲染后直接组装 submit payload：
    - reportInfo / hospitalInfo / equipmentInfo / instruments / testResult / signatures
    """
    label = str(field_obj.get("label") or "")
    fid = str(field_obj.get("id") or "")
    ft = str(field_obj.get("type") or "").lower()
    haystack = f"{label}|{fid}"

    # signatures: 指定三项 + 所有签名语义字段
    if _contains_any(haystack, ("检测员", "受检单位陪同人", "校核员及校核日期")) or ft == "signature":
        return {"bucket": "signatures", "path": f"signatures.{fid}"}

    # instruments: 检测仪器相关
    if _contains_any(haystack, ("检测仪器", "仪器", "校准日期", "有效期")) or fid == "instruments":
        return {"bucket": "instruments", "path": "instruments"}

    # reportInfo
    if _contains_any(haystack, ("委托编号", "受检编号", "检测日期", "环境温度", "湿度", "temperature", "humidity")):
        return {"bucket": "reportInfo", "path": f"reportInfo.{fid}"}

    # hospitalInfo
    if _contains_any(haystack, ("受检单位", "委托单位", "受检单位地址", "检测依据", "联系人", "电话", "检测类型")):
        return {"bucket": "hospitalInfo", "path": f"hospitalInfo.{fid}"}

    # equipmentInfo
    if _contains_any(haystack, ("设备型号", "额定参数", "设备名称", "设备编号", "设备所在场所", "生产厂家")):
        return {"bucket": "equipmentInfo", "path": f"equipmentInfo.{fid}"}

    # 默认归 testResult
    return {"bucket": "testResult", "path": f"testResult.{fid}"}

# Semantic Role Mapping: enforce Maker-Checker-Reviewer style ids for signature roles.
SEMANTIC_ROLE_MAPPINGS = {
    # 1. 基础执行层
    "检测员": {"id_prefix": "inspector", "type": "signature"},
    "测试员": {"id_prefix": "inspector", "type": "signature"},
    "主检": {"id_prefix": "mainInspector", "type": "signature"},
    "主检人": {"id_prefix": "mainInspector", "type": "signature"},
    # 2. 二级复核层 (Checker / Verifier)
    "校核": {"id_prefix": "checker", "type": "signature"},
    "校核员": {"id_prefix": "checker", "type": "signature"},
    "校核人": {"id_prefix": "checker", "type": "signature"},
    "复核": {"id_prefix": "checker", "type": "signature"},
    "复核人": {"id_prefix": "checker", "type": "signature"},
    # 3. 三级审批层 (Reviewer / Approver)
    "审核": {"id_prefix": "reviewer", "type": "signature"},
    "审核员": {"id_prefix": "reviewer", "type": "signature"},
    "审核人": {"id_prefix": "reviewer", "type": "signature"},
    "批准": {"id_prefix": "approver", "type": "signature"},
    "批准人": {"id_prefix": "approver", "type": "signature"},
    "授权签字": {"id_prefix": "authorizedSignatory", "type": "signature"},
    "授权签字人": {"id_prefix": "authorizedSignatory", "type": "signature"},
    # 4. 外部人员
    "陪同人": {"id_prefix": "accompanyingPerson", "type": "signature"},
    "受检单位陪同人": {"id_prefix": "accompanyingPerson", "type": "signature"},
}


def infer_field_properties(label_text: str) -> Tuple[str, str]:
    text = str(label_text or "").strip()
    for key, props in SEMANTIC_ROLE_MAPPINGS.items():
        if key in text:
            return props["id_prefix"], props["type"]
    if "日期" in text or "时间" in text:
        return "date", "date"
    return "", ""


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
    # 关键业务词优先规范化，避免出现 noCommission / noInspection 这类历史别名。
    if "委托编号" in src:
        return "commissionNo"
    if "受检编号" in src:
        return "inspectionNo"
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
    _, semantic_type = infer_field_properties(title)
    if semantic_type:
        return semantic_type
    if rt == "image" or any(k in title for k in _SIGNATURE_KEYWORDS):
        return "signature"
    if rt == "check":
        return "boolean"
    if any(k in title for k in _TYPE_DATE_KEYWORDS):
        return "date"
    if any(k in title.lower() for k in _TYPE_NUMBER_KEYWORDS) or any(k in title for k in _TYPE_NUMBER_KEYWORDS):
        return "number"
    if any(k in title for k in _TYPE_TEXTAREA_KEYWORDS):
        return "textarea"
    return "text"


def _signature_role_from_label(label: str) -> Tuple[str, str] | None:
    """
    前端签名固定三输入：
    1) 检测员
    2) 受检单位陪同人
    3) 校核员及校核日期
    其余签名语义统一归并到上述三类之一。
    """
    text = str(label or "").strip()
    if not text:
        return None
    if any(token in text for token in ("受检单位陪同人", "陪同人")):
        return ("accompanyingPerson", "受检单位陪同人")
    if any(token in text for token in ("检测员", "测试员", "主检")):
        return ("inspector", "检测员")
    if any(token in text for token in ("校核", "复核", "审核", "批准", "授权签字")):
        return ("checker", "校核员及校核日期")
    return None


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
    # 检测仪器优先：
    # - 日期/有效期类字段落到“检测仪器有效期”
    # - 其余仪器字段保留“检测仪器清单”
    if "检测仪器" in title or any(k in title for k in _INSTRUMENT_KEYWORDS):
        if any(k in title for k in ("日期", "有效期")):
            return ("step_instruments", "检测仪器", "sec_instrument_validity", "检测仪器有效期", "form")
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
    if any(k in title for k in ("KAP", "ABC", "剂量", "分辨力", "误差", "比释动能率", "入射屏前空气比释动能率")):
        return ("step_qc_items", "质控检测项目", "sec_qc_items", "检测项目", "form")
    if any(k in title for k in ("温度", "湿度", "环境")):
        return ("step_basic_info", "基本信息", "sec_environment", "环境条件", "form")
    if any(k in title for k in ("报告", "委托", "日期", "编号", "受检")):
        return ("step_basic_info", "基本信息", "sec_report_info", "报告信息", "form")
    # 默认不再归到基本信息，避免“入射屏前空气比释动能率”等检测项误入基本信息。
    return ("step_qc_items", "质控检测项目", "sec_qc_items", "检测项目", "form")


def _classify_by_underscore(raw_key: str) -> Tuple[str, str, str, str] | None:
    """
    下划线分层规则（由命名本身定义层级，不做中文语义归类）：
    - 最后一段为字段名，其前整段 `prefix` 为 section 分组键；
    - 纯中文等无法用 ASCII slug 区分时，对 `prefix` 做稳定哈希生成唯一 section id，避免合并到同一 sec；
    - section 展示标题始终为 `prefix`（或中间段用「 / 」连接）。
    """
    key = normalize_field_text_by_underscore_rules(str(raw_key or "").strip())
    if "_" not in key:
        return None
    parts = [p for p in key.split("_") if p]
    if len(parts) < 2:
        return None
    prefix = "_".join(parts[:-1])
    # 分层展示：A_B_C -> step=A, section=B, field=C
    section_display = " / ".join(parts[1:-1]) if len(parts) > 2 else parts[0]
    if not section_display:
        section_display = prefix if prefix else " / ".join(parts[:-1])
    head = parts[0].lower()
    joined = key.lower()
    # 检测仪器分层强制落到检测仪器步骤
    if "检测仪器" in key or head in {"instrument", "instruments"}:
        return ("step_instruments", "检测仪器", "sec_instruments", "检测仪器")
    # 委托单位互斥勾选不拆独立层级，固定并入基本信息-医院信息
    if key.startswith("委托单位_") and any(token in key for token in ("同受检单位", "委托单位")):
        return ("step_basic_info", "基本信息", "sec_hospital_info", "医院信息")
    # 检测日期（含年月日分段）统一归入基本信息/报告信息
    if any(token in key for token in ("检测日期", "testdate", "test_date")) or (
        head in {"year", "month", "day"} and ("日期" in key or "date" in key.lower())
    ):
        return ("step_basic_info", "基本信息", "sec_report_info", "报告信息")
    # 设备信息关键项（含额定参数）固定归到基本信息/设备信息
    if any(token in key for token in ("设备型号", "额定参数", "设备名称", "设备编号", "设备所在场所", "生产厂家")):
        return ("step_basic_info", "基本信息", "sec_device_info", "设备信息")
    # 下划线层级足够明确（>=3 段）时，优先按层级分组：
    # A_B_C -> step=A, section=B
    # A_B_C_D -> step=A, section=B / C
    if len(parts) >= 3:
        step_key = _underscore_stable_slug(parts[0])
        step_title = parts[0]
        step_id = f"step_{step_key}"
        section_slug = _underscore_stable_slug(prefix)
        sec_id = f"sec_{section_slug}"
        sec_title = section_display
        return step_id, step_title, sec_id, sec_title

    # 质控检测类关键字统一归到 step_qc_items（仅低层级命名时生效）
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
                "比释动能率",
                "入射屏前空气比释动能率",
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
        sec_title = section_display
        return step_id, step_title, sec_id, sec_title

    step_key = _underscore_stable_slug(parts[0])
    step_title = parts[0]
    step_id = f"step_{step_key}"
    section_slug = _underscore_stable_slug(prefix)
    sec_id = f"sec_{section_slug}"
    sec_title = section_display
    return step_id, step_title, sec_id, sec_title


def _underscore_field_id(hierarchy_key: str, fallback_id: str) -> str:
    parts = [p for p in normalize_field_text_by_underscore_rules(str(hierarchy_key or "")).split("_") if p]
    if len(parts) < 2:
        return fallback_id
    suffix = parts[-1]
    mapped = _UNDERSCORE_SUFFIX_MAP.get(suffix, "")
    if not mapped:
        mapped = _to_english_id(suffix, suffix, fallback_id)
    out = _camel_case(mapped, fallback_id)
    return out or fallback_id


def _underscore_field_label(hierarchy_key: str, fallback_label: str) -> str:
    parts = [p for p in normalize_field_text_by_underscore_rules(str(hierarchy_key or "")).split("_") if p]
    if len(parts) < 2:
        return normalize_field_text_by_underscore_rules(fallback_label) or fallback_label
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
    return suffix or mapped or (normalize_field_text_by_underscore_rules(fallback_label) or fallback_label)


def _ensure_pdf_field_id(raw: str, default_num: int = 900000) -> str:
    text = str(raw or "").strip()
    if re.match(r"^f\d+$", text):
        return text
    return ""


def _normalize_pdf_field(item: Dict[str, Any], idx: int) -> Dict[str, Any]:
    page = int(item.get("page") or 1)
    x = float(item.get("x") or 0)
    y = float(item.get("y") or 0)
    w = float(item.get("w") or 0)
    h = float(item.get("h") or 0)
    label = normalize_field_text_by_underscore_rules(
        str(item.get("title") or item.get("placeholder") or item.get("id") or f"字段{idx}").strip()
    )
    source_id = str(item.get("id") or item.get("placeholder") or item.get("pdfFieldId") or f"f{idx}").strip()
    pdf_field_id = str(item.get("pdfFieldId") or item.get("id") or f"f{idx}").strip()
    if not re.match(r"^f\d+$", pdf_field_id):
        pdf_field_id = f"f{idx}"
    return {
        "rawId": source_id,
        "label": label or source_id or f"字段{idx}",
        "fieldType": str(item.get("fieldType") or "text"),
        "pdfFieldId": pdf_field_id,
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
                field.pop("__bbox", None)
    return payload


def _sort_fields_by_coordinate_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    y_tolerance = 10.0

    def _build_row_index_map(section_fields: List[Dict[str, Any]]) -> Dict[int, int]:
        """同页内按 y 分行；相邻 y 误差 <= 10 视为同一行。"""
        by_page: Dict[int, List[Tuple[float, float, int]]] = {}
        for i, f in enumerate(section_fields):
            if not isinstance(f, dict):
                continue
            page, y, x, _ = f.get("__order") or (999, 999999, 999999, 999999)
            try:
                p = int(page)
                yy = float(y)
                xx = float(x)
            except (TypeError, ValueError):
                continue
            by_page.setdefault(p, []).append((yy, xx, i))

        row_map: Dict[int, int] = {}
        for p, rows in by_page.items():
            rows.sort(key=lambda t: (t[0], t[1]))  # 先按 y 扫描形成“行”
            row_idx = -1
            anchor_y = None
            for yy, xx, i in rows:
                if anchor_y is None or abs(yy - anchor_y) > y_tolerance:
                    row_idx += 1
                    anchor_y = yy
                row_map[i] = row_idx
        return row_map

    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    step_min_order: Dict[int, Tuple[int, int, float]] = {}
    for step_idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        sections = step.get("sections")
        if not isinstance(sections, list):
            continue
        section_min_order: Dict[int, Tuple[int, int, float]] = {}
        for sec_idx, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list):
                continue
            row_index_map = _build_row_index_map(fields)
            ordered_with_idx = sorted(
                enumerate(fields),
                key=lambda fi: (
                    # 坐标排序：页码 -> 行(y 容差 10) -> x
                    (fi[1].get("__order") or (999, 999999, 999999, 999999))[0] if isinstance(fi[1], dict) else 999,
                    row_index_map.get(fi[0], 999999),
                    (fi[1].get("__order") or (999, 999999, 999999, 999999))[2] if isinstance(fi[1], dict) else 999999,
                    fi[0],
                ),
            )
            section["fields"] = [x[1] for x in ordered_with_idx]
            if ordered_with_idx:
                first_field = ordered_with_idx[0][1]
                first_order = first_field.get("__order") if isinstance(first_field, dict) else None
                if isinstance(first_order, tuple) and len(first_order) >= 3:
                    page_val = int(first_order[0])
                    row_val = row_index_map.get(ordered_with_idx[0][0], 999999)
                    x_val = float(first_order[2])
                    section_min_order[sec_idx] = (page_val, row_val, x_val)

        # section 顺序也按坐标：页码 -> y行 -> x
        if section_min_order:
            steps[step_idx]["sections"] = [
                sec
                for _, sec in sorted(
                    enumerate(steps[step_idx].get("sections", [])),
                    key=lambda it: section_min_order.get(it[0], (999, 999999, 999999.0)),
                )
            ]
            first_sec_order = min(section_min_order.values(), key=lambda v: (v[0], v[1], v[2]))
            step_min_order[step_idx] = first_sec_order

    # step 顺序按首个 section 坐标排序（签字步骤仍由后续 _force_signature_step_last 兜底放末尾）
    if step_min_order:
        payload["steps"] = [
            st
            for _, st in sorted(
                enumerate(steps),
                key=lambda it: step_min_order.get(it[0], (999, 999999, 999999.0)),
            )
        ]
    return payload


def _normalize_width_ratio_to_token(ratio: float) -> str | float:
    r = max(0.1, min(1.0, float(ratio or 0.0)))
    if abs(r - 1.0) <= 0.08:
        return "full"
    if abs(r - 0.5) <= 0.08:
        return "half"
    if abs(r - (1.0 / 3.0)) <= 0.08:
        return "third"
    return round(r, 1)


def _apply_width_by_coordinate_layout(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    基于坐标设置前端 width：
    - 同页内 y 差 <= 10 视为同一行
    - 每行按 x 从左到右，用 w 占比映射 full/half/third/number
    """
    y_tolerance = 10.0
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    for step in steps:
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list) or not fields:
                continue

            # 只处理常规输入；表格/文本域保留既有宽度策略
            candidate_indices = [
                i
                for i, f in enumerate(fields)
                if isinstance(f, dict) and str(f.get("type") or "").lower() not in {"table", "textarea"}
            ]
            if not candidate_indices:
                continue

            by_page: Dict[int, List[Tuple[int, float, float, float]]] = {}
            # value tuple: (field_idx, y, x, w)
            for i in candidate_indices:
                f = fields[i]
                order = f.get("__order") if isinstance(f.get("__order"), tuple) else (999, 999999, 999999, 999999)
                bbox = f.get("__bbox") if isinstance(f.get("__bbox"), tuple) else (0.0, 0.0, 0.0, 0.0)
                try:
                    page = int(order[0])
                    y = float(order[1])
                    x = float(order[2])
                    w = float(bbox[2]) if len(bbox) >= 3 else 0.0
                except (TypeError, ValueError, IndexError):
                    continue
                by_page.setdefault(page, []).append((i, y, x, w))

            for _page, items in by_page.items():
                items.sort(key=lambda t: (t[1], t[2]))
                rows: List[List[Tuple[int, float, float, float]]] = []
                for node in items:
                    if not rows:
                        rows.append([node])
                        continue
                    last_row = rows[-1]
                    anchor_y = last_row[0][1]
                    if abs(node[1] - anchor_y) <= y_tolerance:
                        last_row.append(node)
                    else:
                        rows.append([node])

                for row in rows:
                    row.sort(key=lambda t: t[2])  # x ascending
                    positive_widths = [max(0.0, float(t[3])) for t in row if float(t[3]) > 0]
                    has_real_w = bool(positive_widths)
                    total_w = sum(positive_widths) if has_real_w else float(len(row))
                    if total_w <= 0:
                        total_w = float(len(row) or 1)
                    for idx, _y, _x, w in row:
                        f = fields[idx]
                        if not isinstance(f, dict):
                            continue
                        ratio = (float(w) / total_w) if has_real_w and w > 0 else (1.0 / float(len(row) or 1))
                        f["width"] = _normalize_width_ratio_to_token(ratio)
    return payload


def _replace_submit_path_leaf(path: str, leaf: str) -> str:
    p = str(path or "").strip()
    if not p:
        return leaf
    if "." not in p:
        return leaf
    head, _tail = p.rsplit(".", 1)
    return f"{head}.{leaf}"


def _collapse_mutually_exclusive_checks_to_radio(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将同一层级同一行内的互斥勾选项合并为 radio：
    - 检测类型：状态检测/验收检测 -> enumRef=testType
    - 控制方式：自动控制/手动控制 -> enumRef=controlMode
    - 单位：μGy/s、μGy/min、mGy/min -> enumRef=doseRateUnit
    - 是/否/有/无 -> enumRef=yesNo
    """
    y_tolerance = 10.0
    unit_label_map = {"μgy/s": "uGyPerSec", "μgy/min": "uGyPerMin", "mgy/min": "mGyPerMin"}
    yes_no_map = {"是": "yes", "否": "no", "有": "yes", "无": "no"}

    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    for step in steps:
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list) or not fields:
                continue

            row_buckets: Dict[Tuple[int, int], List[int]] = {}
            # (page, row_idx) -> field indices
            by_page: Dict[int, List[Tuple[float, float, int]]] = {}
            for i, f in enumerate(fields):
                if not isinstance(f, dict):
                    continue
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                anchor = str(src.get("anchorType") or "").lower()
                if anchor != "check":
                    continue
                order = f.get("__order") if isinstance(f.get("__order"), tuple) else (999, 999999, 999999, 999999)
                try:
                    page = int(order[0])
                    y = float(order[1])
                    x = float(order[2])
                except (TypeError, ValueError, IndexError):
                    continue
                by_page.setdefault(page, []).append((y, x, i))

            for page, items in by_page.items():
                items.sort(key=lambda t: (t[0], t[1]))
                row_idx = -1
                anchor_y = None
                for y, x, i in items:
                    if anchor_y is None or abs(y - anchor_y) > y_tolerance:
                        row_idx += 1
                        anchor_y = y
                    row_buckets.setdefault((page, row_idx), []).append(i)

            if not row_buckets:
                continue

            replace_rows: Dict[Tuple[int, int], Tuple[int, Dict[str, Any], List[int]]] = {}
            for row_key, idxs in row_buckets.items():
                row_fields_with_idx = [(i, fields[i]) for i in idxs if isinstance(fields[i], dict)]
                if len(row_fields_with_idx) < 2:
                    continue

                def _prefix_key(f: Dict[str, Any]) -> str:
                    src = f.get("source") if isinstance(f.get("source"), dict) else {}
                    for raw in (src.get("pdfFieldId"), f.get("id"), f.get("label")):
                        text = normalize_field_text_by_underscore_rules(str(raw or "").strip())
                        if "_" in text:
                            return text.rsplit("_", 1)[0].strip()
                    return ""

                def _family_from_label(label: str) -> str:
                    lb = str(label or "").strip()
                    if lb in {"状态检测", "验收检测"}:
                        return "testType"
                    if lb in {"同受检单位", "委托单位"}:
                        return "commissionOrgMode"
                    if lb in {"自动控制", "手动控制"}:
                        return "controlMode"
                    if lb in {"是", "否", "有", "无"}:
                        return "yesNo"
                    if lb.lower() in unit_label_map:
                        return "doseRateUnit"
                    return ""

                # 按“同前缀”分互斥组；若无前缀则按语义家族兜底分组
                group_map: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
                for fi, ff in row_fields_with_idx:
                    pfx = _prefix_key(ff)
                    fam = _family_from_label(str(ff.get("label") or ""))
                    if pfx:
                        key = f"pfx::{pfx}"
                    elif fam:
                        key = f"fam::{fam}"
                    else:
                        continue
                    group_map.setdefault(key, []).append((fi, ff))

                row_replacements: List[Tuple[int, Dict[str, Any], List[int]]] = []
                for group_key, members in group_map.items():
                    labels = [str(f.get("label") or "").strip() for _, f in members]
                    labels_set = set(labels)
                    if len(labels_set) < 2:
                        continue

                    enum_ref = ""
                    group_name = group_key.split("::", 1)[1] if "::" in group_key else group_key
                    radio_id = _camel_case(group_name, "choice")
                    radio_label = group_name
                    value_by_label: Dict[str, str] = {}
                    default_value = ""
                    submit_bucket = ""
                    submit_path_leaf = radio_id

                    if {"状态检测", "验收检测"} <= labels_set:
                        enum_ref = "testType"
                        radio_id = "testType"
                        radio_label = "检测类型"
                        value_by_label = {"状态检测": "status", "验收检测": "acceptance"}
                        default_value = "status"
                        submit_bucket = "hospitalInfo"
                        submit_path_leaf = "testType"
                    elif {"同受检单位", "委托单位"} <= labels_set:
                        enum_ref = "commissionOrgMode"
                        radio_id = "commissionOrgMode"
                        radio_label = "委托单位"
                        value_by_label = {"同受检单位": "sameInspection", "委托单位": "customCommission"}
                        default_value = "sameInspection"
                        submit_bucket = "hospitalInfo"
                        submit_path_leaf = "commissionOrgMode"
                    elif {"自动控制", "手动控制"} <= labels_set:
                        enum_ref = "controlMode"
                        radio_label = "控制方式"
                        value_by_label = {"自动控制": "auto", "手动控制": "manual"}
                        default_value = "auto"
                        submit_path_leaf = "controlMode"
                    elif labels_set and all((lb in {"是", "否", "有", "无"}) for lb in labels_set):
                        enum_ref = "yesNo"
                        radio_label = "选项"
                        value_by_label = {lb: yes_no_map.get(lb, "no") for lb in labels_set}
                        default_value = "no"
                        submit_path_leaf = "yesNo"
                    elif labels_set and all((lb.lower() in unit_label_map) for lb in labels_set):
                        enum_ref = "doseRateUnit"
                        radio_label = "单位"
                        value_by_label = {lb: unit_label_map.get(lb.lower(), "") for lb in labels_set}
                        default_value = value_by_label.get("mGy/min") or "mGyPerMin"
                        submit_path_leaf = "unit"

                    if not enum_ref:
                        continue

                    first_idx, first = members[0]
                    src0 = first.get("source") if isinstance(first.get("source"), dict) else {}
                    current_bucket = str(src0.get("submitBucket") or "").strip()
                    submit_bucket = submit_bucket or current_bucket or "testResult"
                    submit_path = _replace_submit_path_leaf(str(src0.get("submitPath") or f"{submit_bucket}.{radio_id}"), submit_path_leaf or radio_id)
                    page = int(src0.get("page") or row_key[0])
                    pdf_field_id = str(src0.get("pdfFieldId") or "")

                    picked_default = default_value
                    for _, f in members:
                        if bool(f.get("defaultValue")):
                            lb = str(f.get("label") or "").strip()
                            if lb in value_by_label and value_by_label[lb]:
                                picked_default = value_by_label[lb]
                                break

                    radio_field = {
                        "id": radio_id,
                        "type": "radio",
                        "label": radio_label,
                        "enumRef": enum_ref,
                        "required": True if enum_ref == "testType" else bool(first.get("required", False)),
                        "defaultValue": picked_default or default_value,
                        "width": "half",
                        "source": {
                            "pdfFieldId": pdf_field_id,
                            "page": page,
                            "anchorType": "check",
                            "submitBucket": submit_bucket,
                            "submitPath": submit_path,
                        },
                        "__order": first.get("__order", (999, 999999, 999999, 999999)),
                        "__bbox": first.get("__bbox", (0.0, 0.0, 0.0, 0.0)),
                    }
                    drop_ids = [i for i, _ in members]
                    row_replacements.append((min(drop_ids), radio_field, drop_ids))

                # 仅当该行命中“同前缀互斥组”才替换
                if row_replacements:
                    row_replacements.sort(key=lambda t: t[0])
                    # 一个 row 可有多个互斥组；此处按出现顺序逐个插入
                    for insert_at, radio_field, drop_ids in row_replacements:
                        key = (row_key[0], row_key[1] * 1000 + insert_at)
                        replace_rows[key] = (insert_at, radio_field, drop_ids)

            if not replace_rows:
                continue

            drop_indices = set()
            insert_items: List[Tuple[int, Dict[str, Any]]] = []
            for _k, pack in replace_rows.items():
                insert_at, radio_field, drop_ids = pack
                if not drop_ids:
                    continue
                drop_indices.update(drop_ids)
                insert_items.append((insert_at, radio_field))

            rebuilt: List[Dict[str, Any]] = []
            insert_map = {idx: field for idx, field in insert_items}
            for i, f in enumerate(fields):
                if i in insert_map:
                    rebuilt.append(insert_map[i])
                if i in drop_indices:
                    continue
                rebuilt.append(f)
            section["fields"] = rebuilt
    return payload


def _prioritize_commission_fields_in_basic_info(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    基本信息中将委托单位相关字段前置到受检单位字段前。
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("id") or "") != "step_basic_info":
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list) or not fields:
                continue
            def _field_sort_key(f: Dict[str, Any]):
                if not isinstance(f, dict):
                    return (99, 99, 999, 999999, 999999)
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                submit_bucket = str(src.get("submitBucket") or "")
                label = str(f.get("label") or "")
                ft = str(f.get("type") or "").lower()
                order = f.get("__order") or (999, 999999, 999999, 999999)

                # 一级：先保留报告信息顺序，再排医院信息
                if submit_bucket == "reportInfo":
                    bucket_rank = 0
                elif submit_bucket == "equipmentInfo":
                    bucket_rank = 1
                elif submit_bucket == "hospitalInfo":
                    bucket_rank = 2
                else:
                    bucket_rank = 3

                # 二级：仅在 hospitalInfo 内做“委托单位勾选前置”
                local_rank = 9
                if submit_bucket == "hospitalInfo":
                    if ft == "boolean" and label in {"同受检单位", "委托单位"}:
                        local_rank = 0
                    elif "委托单位" in label:
                        local_rank = 1
                    elif "受检单位" in label:
                        local_rank = 2
                    else:
                        local_rank = 3

                return (
                    bucket_rank,
                    local_rank,
                    order[0],
                    order[1],
                    order[2],
                )

            section["fields"] = sorted(fields, key=_field_sort_key)
    return payload


def _remove_boolean_duplicates_after_radio(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    去重：若同一 section 已存在 radio，则移除与其同组的重复 boolean 勾选。
    避免“同一组既有 boolean 又有 radio”。
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    for step in steps:
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list) or not fields:
                continue

            radio_signatures: set[tuple[str, str]] = set()
            for f in fields:
                if not isinstance(f, dict):
                    continue
                if str(f.get("type") or "").lower() != "radio":
                    continue
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                submit_path = str(src.get("submitPath") or "")
                enum_ref = str(f.get("enumRef") or "")
                if submit_path or enum_ref:
                    radio_signatures.add((submit_path, enum_ref))

            if not radio_signatures:
                continue

            filtered: List[Dict[str, Any]] = []
            for f in fields:
                if not isinstance(f, dict):
                    continue
                ft = str(f.get("type") or "").lower()
                if ft != "boolean":
                    filtered.append(f)
                    continue
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                submit_path = str(src.get("submitPath") or "")
                label = str(f.get("label") or "").strip()

                # 1) 同 submitPath 命中 radio -> 去掉 boolean
                hit_by_path = any(sig[0] and sig[0] == submit_path for sig in radio_signatures)
                # 2) 语义命中互斥家族 -> 去掉 boolean
                hit_by_family = (
                    label in {"状态检测", "验收检测", "同受检单位", "委托单位", "自动控制", "手动控制", "是", "否", "有", "无"}
                    or label.lower() in {"μgy/s", "μgy/min", "mgy/min"}
                )
                if hit_by_path or hit_by_family:
                    continue
                filtered.append(f)
            section["fields"] = filtered

    return payload


def _normalize_basic_info_sections(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    基本信息结构收敛：
    - 医院信息：委托单位、委托单位联系人电话、受检单位、受检单位地址
    - 报告信息：委托编号在受检编号前
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload
    basic_step = next((s for s in steps if isinstance(s, dict) and str(s.get("id") or "") == "step_basic_info"), None)
    if not isinstance(basic_step, dict):
        return payload
    sections = basic_step.get("sections")
    if not isinstance(sections, list):
        sections = []
        basic_step["sections"] = sections

    sec_report = next((s for s in sections if isinstance(s, dict) and str(s.get("id") or "") == "sec_report_info"), None)
    if not isinstance(sec_report, dict):
        sec_report = {"id": "sec_report_info", "title": "报告信息", "layout": "form", "fields": []}
        sections.append(sec_report)
    sec_hospital = next((s for s in sections if isinstance(s, dict) and str(s.get("id") or "") == "sec_hospital_info"), None)
    if not isinstance(sec_hospital, dict):
        sec_hospital = {"id": "sec_hospital_info", "title": "医院信息", "layout": "form", "fields": []}
        sections.append(sec_hospital)

    hospital_tokens = ("委托单位", "委托单位联系人", "受检单位", "受检单位地址")
    move_to_hospital: List[Dict[str, Any]] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        fields = sec.get("fields")
        if not isinstance(fields, list):
            continue
        kept: List[Dict[str, Any]] = []
        for f in fields:
            if not isinstance(f, dict):
                continue
            label = str(f.get("label") or "")
            src = f.get("source") if isinstance(f.get("source"), dict) else {}
            submit_bucket = str(src.get("submitBucket") or "")
            if any(token in label for token in hospital_tokens) or submit_bucket == "hospitalInfo":
                # 报告编号/委托编号/受检编号仍留在报告信息
                if str(f.get("id") or "") in {"reportNo", "commissionNo", "inspectionNo"}:
                    kept.append(f)
                else:
                    move_to_hospital.append(f)
            else:
                kept.append(f)
        sec["fields"] = kept

    if move_to_hospital:
        hf = sec_hospital.get("fields")
        if not isinstance(hf, list):
            hf = []
        hf.extend(move_to_hospital)
        sec_hospital["fields"] = sorted(
            hf,
            key=lambda f: (
                0 if "委托单位" in str(f.get("label") or "") else 1,
                1 if "受检单位" in str(f.get("label") or "") else 0,
                (f.get("__order") or (999, 999999, 999999, 999999))[0] if isinstance(f, dict) else 999,
                (f.get("__order") or (999, 999999, 999999, 999999))[1] if isinstance(f, dict) else 999999,
                (f.get("__order") or (999, 999999, 999999, 999999))[2] if isinstance(f, dict) else 999999,
            ),
        )

    rf = sec_report.get("fields")
    if isinstance(rf, list) and rf:
        sec_report["fields"] = sorted(
            rf,
            key=lambda f: (
                0 if str(f.get("id") or "") == "commissionNo" or "委托编号" in str(f.get("label") or "") else 1,
                1 if str(f.get("id") or "") == "inspectionNo" or "受检编号" in str(f.get("label") or "") else 0,
                (f.get("__order") or (999, 999999, 999999, 999999))[0] if isinstance(f, dict) else 999,
                (f.get("__order") or (999, 999999, 999999, 999999))[1] if isinstance(f, dict) else 999999,
                (f.get("__order") or (999, 999999, 999999, 999999))[2] if isinstance(f, dict) else 999999,
            ),
        )
    return payload


def _relocate_equipment_fields_to_basic_info(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    兜底：submitBucket=equipmentInfo 的字段强制回到 基本信息/设备信息。
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload
    moved: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list):
                continue
            kept: List[Dict[str, Any]] = []
            for f in fields:
                if not isinstance(f, dict):
                    continue
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                if str(src.get("submitBucket") or "") == "equipmentInfo":
                    moved.append(f)
                else:
                    kept.append(f)
            section["fields"] = kept
    if not moved:
        return payload

    basic_step = next((s for s in steps if isinstance(s, dict) and str(s.get("id") or "") == "step_basic_info"), None)
    if not isinstance(basic_step, dict):
        basic_step = {"id": "step_basic_info", "title": "基本信息", "sections": []}
        steps.insert(0, basic_step)
    sections = basic_step.get("sections")
    if not isinstance(sections, list):
        sections = []
        basic_step["sections"] = sections
    sec = next((x for x in sections if isinstance(x, dict) and str(x.get("id") or "") == "sec_device_info"), None)
    if not isinstance(sec, dict):
        sec = {"id": "sec_device_info", "title": "设备信息", "layout": "form", "fields": []}
        sections.append(sec)
    existing = sec.get("fields")
    if not isinstance(existing, list):
        existing = []
    existing.extend(moved)
    sec["fields"] = sorted(
        existing,
        key=lambda f: (
            (f.get("__order") or (999, 999999, 999999, 999999))[0] if isinstance(f, dict) else 999,
            (f.get("__order") or (999, 999999, 999999, 999999))[1] if isinstance(f, dict) else 999999,
            (f.get("__order") or (999, 999999, 999999, 999999))[2] if isinstance(f, dict) else 999999,
        ),
    )
    return payload


def _extract_instrument_index(field: Dict[str, Any]) -> int:
    # 编号提取优先级：label > pdfFieldId > id
    label = str(field.get("label") or "")
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    pdf_id = str(src.get("pdfFieldId") or "")
    fid = str(field.get("id") or "")
    for text in (label, pdf_id, fid):
        m = re.search(r"(?:检测仪器|仪器|instrument)\s*([0-9]+)", text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                continue
    # 兜底：从末尾数字推断
    for text in (label, pdf_id, fid):
        m = re.search(r"([0-9]+)$", text)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                continue
    return 0


def _arrange_instruments_row_layout(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    检测仪器区布局：
    - 每个仪器占一行
    - 顺序：boolean -> 名称(text) -> 有效期(date)
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload
    spillover_fields: List[Dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict) or str(step.get("id") or "") != "step_instruments":
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list) or not fields:
                continue
            grouped: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
            for f in fields:
                if not isinstance(f, dict):
                    continue
                idx = _extract_instrument_index(f)
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                label = str(f.get("label") or "")
                fid = str(f.get("id") or "")
                ft = str(f.get("type") or "").lower()
                is_instrument_like = (
                    idx > 0
                    and (
                        "检测仪器" in label
                        or "仪器" in label
                        or "instrument" in fid.lower()
                        or str(src.get("submitBucket") or "") == "instruments"
                        or ft == "date"
                    )
                )
                if not is_instrument_like:
                    spillover_fields.append(f)
                    continue
                bucket = grouped.setdefault(idx, {"boolean": [], "text": [], "date": []})
                if ft == "boolean":
                    bucket["boolean"].append(f)
                elif ft == "date":
                    # 保留用户原始命名，不改 label。
                    bucket["date"].append(f)
                else:
                    bucket["text"].append(f)

            if not grouped:
                continue

            # 顺序严格按命名（检测仪器N/仪器N）：
            # 不再根据 fxx 坐标或页面位置重新归并。
            def _name_score(f: Dict[str, Any], idx: int, kind: str) -> Tuple[int, int]:
                label = str(f.get("label") or "")
                fid = str(f.get("id") or "")
                score = 9
                if f"检测仪器{idx}" in label or f"检测仪器{idx}" in fid:
                    score = 0
                elif f"仪器{idx}" in label or f"instrument{idx}" in fid.lower():
                    score = 1
                elif kind == "date" and (f"{idx}" in fid or f"{idx}" in label):
                    score = 2
                return (score, len(fid))

            rebuilt: List[Dict[str, Any]] = []
            for idx in sorted(grouped.keys()):
                row = grouped[idx]
                has_bool = bool(row.get("boolean"))
                has_text = bool(row.get("text"))
                has_date = bool(row.get("date"))
                # 检测仪器一行自适应宽度：
                # - boolean + text + date: 0.1 / 0.5 / 0.3
                # - boolean + text: 0.1 / 0.8
                # - text + date: 0.5 / 0.3
                # - 仅 text: full
                width_by_kind: Dict[str, str | float] = {"boolean": 0.1, "text": "full", "date": 0.3}
                if has_bool and has_text and has_date:
                    width_by_kind = {"boolean": 0.1, "text": 0.5, "date": 0.3}
                elif has_bool and has_text and not has_date:
                    width_by_kind = {"boolean": 0.1, "text": 0.8, "date": 0.3}
                elif (not has_bool) and has_text and has_date:
                    width_by_kind = {"boolean": 0.1, "text": 0.5, "date": 0.3}
                elif has_text and (not has_bool) and (not has_date):
                    width_by_kind = {"boolean": 0.1, "text": "full", "date": 0.3}
                for kind in ("boolean", "text", "date"):
                    candidates = row.get(kind) or []
                    if not candidates:
                        continue
                    candidates = sorted(candidates, key=lambda f: _name_score(f, idx, kind))
                    ff = candidates[0]
                    if not isinstance(ff, dict):
                        continue
                    # 保留用户原始命名，不改 label。
                    # 统一重建稳定 id，避免历史 id 与命名不一致导致后续再错位。
                    if kind == "boolean":
                        ff["id"] = f"instrument{idx}Enabled"
                    elif kind == "text":
                        ff["id"] = f"testInstrument{idx}"
                    elif kind == "date":
                        ff["id"] = f"testDate{idx}"
                    ff["width"] = width_by_kind.get(kind, ff.get("width", "half"))
                    rebuilt.append(ff)
            section["fields"] = rebuilt

    # 将误归到检测仪器区的非仪器字段回流到质控检测项目，避免干扰仪器行布局。
    if spillover_fields:
        qc_step = next((s for s in steps if isinstance(s, dict) and str(s.get("id") or "") == "step_qc_items"), None)
        if not isinstance(qc_step, dict):
            qc_step = {"id": "step_qc_items", "title": "质控检测项目", "sections": []}
            steps.append(qc_step)
        sections = qc_step.get("sections")
        if not isinstance(sections, list):
            sections = []
            qc_step["sections"] = sections
        qc_sec = next((sec for sec in sections if isinstance(sec, dict) and str(sec.get("id") or "") == "sec_qc_items"), None)
        if not isinstance(qc_sec, dict):
            qc_sec = {"id": "sec_qc_items", "title": "检测项目", "layout": "form", "fields": []}
            sections.append(qc_sec)
        target_fields = qc_sec.get("fields")
        if not isinstance(target_fields, list):
            target_fields = []
        target_fields.extend(spillover_fields)
        qc_sec["fields"] = target_fields
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
    key = str(src.get("pdfFieldId") or "")
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

            # 兜底：处理独立 year/month/day（例如 id=year,month,day；label=年/月/日）。
            loose_parts_by_page: Dict[int, List[Dict[str, Any]]] = {}
            remain_passthrough: List[Dict[str, Any]] = []
            for f in passthrough:
                if not isinstance(f, dict):
                    continue
                fid = str(f.get("id") or "").strip().lower()
                label = str(f.get("label") or "").strip()
                token = ""
                if fid in {"year", "month", "day"}:
                    token = fid
                elif label in {"年", "月", "日"}:
                    token = {"年": "year", "月": "month", "日": "day"}[label]
                if not token:
                    remain_passthrough.append(f)
                    continue
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                page = int(src.get("page") or 1)
                ff = dict(f)
                ff["__dateToken"] = token
                loose_parts_by_page.setdefault(page, []).append(ff)
            passthrough = remain_passthrough

            if loose_parts_by_page:
                sec_title = str(section.get("title") or "").strip()
                step_title = str(step.get("title") or "").strip()
                base_label_seed = sec_title or step_title or "检测日期"
                base_id_seed = _camel_case(base_label_seed, "testDate")
                for page, items in loose_parts_by_page.items():
                    # 同页按字段尾号再分组，避免把多个仪器有效期合并成一个。
                    by_num: Dict[str, List[Dict[str, Any]]] = {}
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        fid_raw = str(it.get("id") or "")
                        m = re.search(r"(\d+)$", fid_raw)
                        num = m.group(1) if m else ""
                        by_num.setdefault(num, []).append(it)
                    for num, sub_items in by_num.items():
                        tokens = {str(x.get("__dateToken") or "") for x in sub_items if isinstance(x, dict)}
                        if not ({"year", "month"} <= tokens or {"month", "day"} <= tokens or {"year", "day"} <= tokens):
                            passthrough.extend(sub_items)
                            continue
                        suffix = num or ("" if len(by_num) == 1 and len(loose_parts_by_page) == 1 else str(page))
                        grouped[f"{base_id_seed}{suffix}"] = sub_items

            merged: List[Dict[str, Any]] = []
            for base, items in grouped.items():
                if not items:
                    continue
                # 取第一个作为模板，聚合 required/default/width/source
                seed = dict(items[0])
                base_id = _camel_case(base, _camel_case(str(seed.get("id") or "testDate"), "testDate"))
                src = seed.get("source") if isinstance(seed.get("source"), dict) else {}
                base_label = str(seed.get("label") or base).rstrip("年月日").rstrip("_-/:： ").strip() or base
                if str(seed.get("label") or "").strip() in {"年", "月", "日"}:
                    base_label = str(section.get("title") or step.get("title") or "检测日期").strip() or "检测日期"
                if base_label in {"检测仪器", "instrument", "instruments", "检测仪器清单"}:
                    base_label = "检测仪器有效期"
                if "检测仪器" in str(section.get("title") or "") or "检测仪器" in str(step.get("title") or ""):
                    m = re.search(r"(\d+)$", str(base_id))
                    if m:
                        base_label = f"检测仪器{m.group(1)}有效期"
                merged_field: Dict[str, Any] = {
                    "id": base_id,
                    "type": "date",
                    "label": base_label,
                    "required": any(bool(x.get("required")) for x in items if isinstance(x, dict)),
                    "defaultValue": None,
                    "width": str(seed.get("width") or "half"),
                    "source": {
                        "pdfFieldId": str(src.get("pdfFieldId") or ""),
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
                inferred_id, inferred_type = infer_field_properties(label)
                if inferred_type == "signature":
                    field["type"] = "signature"
                    field["defaultValue"] = None
                    current_id = str(field.get("id") or "")
                    if not current_id or current_id.startswith("date"):
                        field["id"] = inferred_id
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

    def _pick_pdf_field_id(*keywords: str) -> str:
        def _iter_all_fields():
            for step in payload.get("steps", []):
                if not isinstance(step, dict):
                    continue
                for sec in step.get("sections", []):
                    if not isinstance(sec, dict):
                        continue
                    for ff in sec.get("fields", []):
                        if isinstance(ff, dict):
                            yield ff

        # 先在 report_info 内找，再全局找
        for f in list(fields) + list(_iter_all_fields()):
            if not isinstance(f, dict):
                continue
            src = f.get("source") if isinstance(f.get("source"), dict) else {}
            pdf_id = str(src.get("pdfFieldId") or "").strip()
            fid = str(f.get("id") or "").strip()
            label = str(f.get("label") or "").strip()
            hay = f"{fid}|{label}"
            if keywords and any(k in hay for k in keywords) and re.match(r"^f\d+$", pdf_id):
                return pdf_id
        return ""

    test_date_pdf_id = _pick_pdf_field_id("检测日期", "testDate", "year", "month", "day")
    commission_pdf_id = _pick_pdf_field_id("委托编号", "commissionNo")
    # 删除拆分日期字段
    fields = [f for f in fields if not (isinstance(f, dict) and f.get("id") in {"testDateYear", "testDateMonth", "testDateDay"})]
    existing_ids = {f.get("id") for f in fields if isinstance(f, dict)}
    if "testDate" not in existing_ids:
        fields.insert(
            0,
            {
                "id": "testDate",
                "type": "date",
                "label": "检测日期",
                "required": True,
                "defaultValue": None,
                "width": "half",
                "source": {
                    "pdfFieldId": _ensure_pdf_field_id(test_date_pdf_id),
                    "page": 1,
                    "anchorType": "text",
                    "submitBucket": "reportInfo",
                    "submitPath": "reportInfo.testDate",
                },
            },
        )
    if "commissionNo" not in existing_ids:
        fields.insert(
            0,
            {
                "id": "commissionNo",
                "type": "text",
                "label": "委托编号",
                "required": True,
                "width": "half",
                "source": {
                    "pdfFieldId": _ensure_pdf_field_id(commission_pdf_id),
                    "page": 1,
                    "anchorType": "text",
                    "submitBucket": "reportInfo",
                    "submitPath": "reportInfo.commissionNo",
                },
            },
        )
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


def build_frontend_schema_by_rules(template_obj: Dict[str, Any], *, merge_split_dates: bool = True) -> Dict[str, Any]:
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
    found_test_type_checks: Dict[str, str] = {}
    instrument_check_labels: List[str] = []

    sorted_items = sorted(latest_by_key.values(), key=lambda it: it.get("order", (999, 999999, 999999, 999999)))
    for idx, item in enumerate(sorted_items, start=1):
        # 规则1：第一页勾选项归位（状态检测/验收检测）
        if item.get("page") == 1 and item.get("fieldType", "").lower() == "check":
            if item["label"] in {"状态检测", "验收检测"}:
                found_test_type_checks[item["label"]] = str(item.get("pdfFieldId") or "")
                continue
            if re.match(r"^仪器\\d+$", item["label"]):
                instrument_check_labels.append(item["label"])
                continue

        ft = _infer_type(item["fieldType"], item["label"])
        signature_role = _signature_role_from_label(item["label"]) if ft == "signature" else None
        # 规则2：下划线自动分层优先（同前缀归同一层级）
        label_key = normalize_field_text_by_underscore_rules(str(item.get("label") or "").strip())
        raw_key = str(item.get("rawId") or "").strip()
        hierarchy_key = label_key if "_" in label_key else raw_key
        # 签名字段不参与下划线分层，避免被拆成多个独立 step/section。
        hierarchy = None if signature_role else _classify_by_underscore(hierarchy_key)
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
        semantic_id_prefix, semantic_type = infer_field_properties(item["label"])
        if signature_role:
            field_id, field_label = signature_role
        elif hierarchy:
            field_id = _underscore_field_id(hierarchy_key, fallback_id)
            field_label = _underscore_field_label(hierarchy_key, item["label"])
            # 仪器勾选常见命名如：检测仪器1_2，会被下划线后缀误识别成“2”。
            # 这里强制恢复到“检测仪器N”，确保后续能与 text/date 正确配对成同一行。
            hparts = [p for p in normalize_field_text_by_underscore_rules(str(hierarchy_key or "")).split("_") if p]
            if hparts and re.match(r"^检测仪器\d+$", hparts[0]) and str(item.get("fieldType") or "").lower() == "check":
                field_label = hparts[0]
                field_id = _camel_case(hparts[0], field_id or fallback_id)
        else:
            if semantic_id_prefix:
                field_id = semantic_id_prefix
            else:
                field_id = _to_english_id(item["label"], item["rawId"], fallback_id)
            field_label = item["label"]
        # 历史别名收敛，避免导出 noCommission/noInspection。
        if field_id == "noCommission":
            field_id = "commissionNo"
        if field_id == "noInspection":
            field_id = "inspectionNo"

        # 对编号类语义，避免重复字段占用同一 pdfFieldId。
        if field_id in {"commissionNo", "inspectionNo"}:
            current_pdf_id = str(item.get("pdfFieldId") or "").strip()
            if current_pdf_id:
                dup = any(
                    isinstance(ff, dict)
                    and str(ff.get("id") or "") == field_id
                    and str(((ff.get("source") if isinstance(ff.get("source"), dict) else {}) or {}).get("pdfFieldId") or "").strip()
                    == current_pdf_id
                    for ff in sec.get("fields", [])
                )
                if dup:
                    continue
        if not signature_role:
            base_id = field_id
            n = 2
            while field_id in used_ids:
                field_id = f"{base_id}{n}"
                n += 1
            used_ids.add(field_id)
        else:
            # 签名固定 3 个前端输入：同角色多处 PDF 签名框合并到一个输入。
            merged_target = next(
                (
                    ff
                    for ff in sec.get("fields", [])
                    if isinstance(ff, dict)
                    and str(ff.get("id") or "") == field_id
                    and str(ff.get("type") or "").lower() == "signature"
                ),
                None,
            )
            if isinstance(merged_target, dict):
                src = merged_target.get("source") if isinstance(merged_target.get("source"), dict) else {}
                existed_pdf_ids = src.get("pdfFieldIds")
                if not isinstance(existed_pdf_ids, list):
                    existed_pdf_ids = []
                current_pdf_id = str(item.get("pdfFieldId") or "").strip()
                if current_pdf_id and current_pdf_id not in existed_pdf_ids:
                    existed_pdf_ids.append(current_pdf_id)
                src["pdfFieldIds"] = existed_pdf_ids

                existed_pages = src.get("pages")
                if not isinstance(existed_pages, list):
                    existed_pages = []
                try:
                    current_page = int(item.get("page") or 1)
                except (TypeError, ValueError):
                    current_page = 1
                if current_page not in existed_pages:
                    existed_pages.append(current_page)
                src["pages"] = sorted(existed_pages)
                merged_target["source"] = src
                continue
            used_ids.add(field_id)
        # Semantic role mapping can override inferred type for strict role semantics.
        if semantic_type:
            ft = semantic_type
        field_obj: Dict[str, Any] = {
            "id": field_id,
            "type": ft,
            "label": field_label,
            "required": False,
            "defaultValue": False if ft == "boolean" else None,
            "width": "half",
            "source": {
                "pdfFieldId": item["pdfFieldId"],
                "page": item["page"],
                "anchorType": item["fieldType"],
            },
            "__order": item.get("order", (999, 999999, 999999, idx)),
            "__bbox": (item.get("x", 0.0), item.get("y", 0.0), item.get("w", 0.0), item.get("h", 0.0)),
        }
        submit_binding = _assign_submit_bucket(field_obj)
        field_obj["source"]["submitBucket"] = submit_binding["bucket"]
        field_obj["source"]["submitPath"] = submit_binding["path"]
        # 约束：归到 testResult 的输入统一按 number 渲染与提交。
        if submit_binding["bucket"] == "testResult":
            field_obj["type"] = "number"
            field_obj["defaultValue"] = None
            field_obj["precision"] = 1
        if field_obj.get("type") == "number":
            field_obj["precision"] = 1
            unit = _pick_unit(item["label"])
            if unit:
                field_obj["unit"] = unit
        if str(field_obj.get("source", {}).get("anchorType") or "").lower() == "check":
            field_obj["type"] = "boolean"
            field_obj["defaultValue"] = bool(field_obj.get("defaultValue", False))
            field_obj.pop("enumRef", None)
        if field_obj.get("type") in {"radio", "select"}:
            field_obj["enumRef"] = "testType" if ("验收" in item["label"] or "状态" in item["label"]) else "yesNo"
            field_obj["defaultValue"] = "status" if field_obj["enumRef"] == "testType" else "no"
        if field_obj.get("type") == "textarea":
            field_obj["minLines"] = 3
            field_obj["maxLines"] = 8
            field_obj["width"] = "full"
        if field_obj.get("type") == "computed":
            field_obj["dependsOn"] = []
            field_obj["formula"] = ""
        if field_obj.get("type") == "verdict":
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
                    "required": True,
                    "enumRef": "testType",
                    "defaultValue": "status",
                    "width": "half",
                    "source": {
                        "pdfFieldId": found_test_type_checks.get("状态检测") or found_test_type_checks.get("验收检测") or "",
                        "page": 1,
                        "anchorType": "check",
                        "submitBucket": "hospitalInfo",
                        "submitPath": "hospitalInfo.testType",
                    },
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
                    "source": {
                        "pdfFieldId": "",
                        "page": 1,
                        "anchorType": "table",
                        "submitBucket": "instruments",
                        "submitPath": "instruments",
                    },
                }
            )

    steps: List[Dict[str, Any]] = []
    for sid in step_order:
        sections_map = section_bucket.get(sid, {})
        if not sections_map:
            continue
        # 层级优先：section 按层级键稳定排序（同层级内字段再按坐标排序）。
        sections = [sections_map[k] for k in sorted(sections_map.keys())]
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
    if merge_split_dates:
        result = _merge_split_date_fields(result)
    result = _sort_fields_by_coordinate_order(result)
    result = _collapse_mutually_exclusive_checks_to_radio(result)
    result = _remove_boolean_duplicates_after_radio(result)
    result = _relocate_equipment_fields_to_basic_info(result)
    result = _normalize_basic_info_sections(result)
    result = _prioritize_commission_fields_in_basic_info(result)
    if merge_split_dates:
        result = _arrange_instruments_row_layout(result)
    result = _apply_width_by_coordinate_layout(result)
    result = _inject_underscore_section_rules(result)
    result = _force_signature_step_last(result)
    result = _strip_coordinate_keys(result)
    return result

