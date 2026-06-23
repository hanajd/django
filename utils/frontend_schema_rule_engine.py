import copy
import hashlib
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from utils.unified_template_fields import frontend_rect_from_field, materialize_unified_pdf_fields
from utils.pdf_field_formulas import merge_field_formulas_into_frontend


_FORM_SCHEMA_EXTRA_KEYS = (
    "lookupTables",
    "fieldVerdictPlan",
    "fieldFormulas",
    "pdfFieldFormulas",
    "radiationProtectionChapter",
)


def _schema_extras_from_template(template_obj: Dict[str, Any], form_schema_top: Dict[str, Any]) -> Dict[str, Any]:
    """Keep frontend schema extensions while rebuilding steps from PDF fields."""
    out: Dict[str, Any] = {}
    for key in _FORM_SCHEMA_EXTRA_KEYS:
        for container in (form_schema_top, template_obj):
            if not isinstance(container, dict):
                continue
            val = container.get(key)
            if val in (None, "", [], {}):
                continue
            if isinstance(val, (dict, list)):
                if key == "radiationProtectionChapter" and isinstance(val, dict):
                    try:
                        from radiation_detection_report.chapter5_field_sync import export_chapter_config_for_frontend

                        out[key] = export_chapter_config_for_frontend(val)
                    except Exception:
                        out[key] = copy.deepcopy(val)
                else:
                    out[key] = copy.deepcopy(val)
                break
    return out


def _apply_schema_extras(payload: Dict[str, Any], extras: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(extras, dict):
        return payload
    for key, val in extras.items():
        if val not in (None, "", [], {}):
            payload[key] = copy.deepcopy(val)
    return payload


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
    ("%rh", "%RH"),
    ("℃", "℃"),
    ("°c", "℃"),
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
    "影增器": "imageIntensifier",
    "平板": "flatPanel",
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
    "kapAreaProductUnit": [
        {"value": "mGy_cm2", "label": "mGycm^2"},
        {"value": "mGy_m2", "label": "mGym^2"},
        {"value": "uGy_m2", "label": "μGym^2"},
        {"value": "uGy_cm2", "label": "μGycm^2"},
    ],
    "commissionOrgMode": [
        {"value": "sameInspection", "label": "同受检单位"},
        {"value": "customCommission", "label": "委托单位"},
    ],
}

_UNDERSCORE_SUFFIX_MAP = {
    "℃": "environmentTempC",
    "°c": "environmentTempC",
    "°C": "environmentTempC",
    "%rh": "environmentHumidity",
    "%RH": "environmentHumidity",
    "kv": "kv",
    "ma": "ma",
    "ww": "ww",
    "wl": "wl",
    "lp": "lp",
    "rv": "rv",
    "roi": "roi",
    "报出值": "reportValue",
    "判定": "verdict",
    # 「计算结果」必须整段匹配：若走 _to_english_id，子串「结果」会误映射成 result，与单列「结果」混淆
    "计算结果": "computedResult",
    "结果": "result",
    "测量值": "measureValue",
    "真实长度": "realLength",
    "测量长度": "measureLength",
    "影增器": "imageIntensifier",
    "平板": "flatPanel",
}

_UNDERSCORE_LABEL_MAP = {
    "environmentTempC": "℃",
    "environmentHumidity": "%RH",
    "kv": "kV",
    "ma": "mA",
    "ww": "WW",
    "wl": "WL",
    "lp": "LP",
    "rv": "RV",
    "roi": "ROI",
    "reportValue": "报出值",
    "verdict": "判定",
    "computedResult": "计算结果",
    "result": "结果",
    "measureValue": "测量值",
    "realLength": "真实长度",
    "measureLength": "测量长度",
    "imageIntensifier": "影增器",
    "flatPanel": "平板",
}

# KAP 指示偏离：同一格内多单位勾选（mGycm^2 / mGym^2 / μGym^2 / μGycm^2）合并为单选
_KAP_AREA_UNIT_LABELS = frozenset({"mGycm^2", "mGym^2", "μGym^2", "μGycm^2"})
_KAP_AREA_LABEL_TO_VALUE = {
    "mGycm^2": "mGy_cm2",
    "mGym^2": "mGy_m2",
    "μGym^2": "uGy_m2",
    "μGycm^2": "uGy_cm2",
}

_SUBMIT_BUCKET_ORDER = {
    "reportInfo": 0,
    "hospitalInfo": 1,
    "equipmentInfo": 2,
    "instruments": 3,
    "testResult": 4,
    "signatures": 5,
}

# visibleWhen 与 Flutter FieldDef / ExpressionEngine 对齐（与「勾选控件配置」不是同一概念）：
# - type==boolean：Checkbox 存 true/false；被其控制的字段写 controllerId == true / == false（字面量无引号）。
#   普通勾选仅作输入时不要在勾选字段上写 visibleWhen；只有要「控制别的字段显隐」时，才在「被控字段」上写。
# - type==radio + enum（如 yesNo、commissionOrgMode）：存枚举 value 字符串；被控字段写 == 'yes'、== 'customCommission' 等。
# - 勿用 == 1 / == 0 代替 boolean（与 BooleanFieldWidget 的 bool 值不一致）。
_VISIBLE_WHEN_KERMA_MAX_HIGH_SECTION = "hasAec == true"
_VISIBLE_WHEN_KERMA_MAX_HIGH_FIELD = "hasHighDoseMode == true"
# 委托单位名称：互斥合并后为 radio(enumRef=commissionOrgMode)，选「委托单位」时为 customCommission。
_VISIBLE_WHEN_COMMISSION_ORG_NAME = "commissionOrgMode == 'customCommission'"


def _field_is_commission_organization_name(field: Dict[str, Any]) -> bool:
    """委托单位名称输入（非联系人/电话）。"""
    if not isinstance(field, dict):
        return False
    fid = str(field.get("id") or "")
    if fid in {"commissionname", "commissionName", "commissionOrganization"}:
        return True
    if "委托单位名称" in fid and "联系人" not in fid:
        return True
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    sp = str(src.get("submitPath") or "")
    if sp.endswith(("commissionname", "commissionName", "commissionOrganization")):
        return True
    hk = str(src.get("hierarchyKey") or "")
    if "委托单位名称" in hk and "联系人" not in hk and "电话" not in hk:
        return True
    label = str(field.get("label") or "")
    if "委托单位名称" in label and "联系人" not in label and "电话" not in label:
        return True
    return False


def _basic_info_commission_visibility_controllers(
    basic_step: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    返回 (是否存在 commissionOrgMode radio, 同受检单位 boolean 的字段 id)。
    合并 radio 后通常仅有前者；未合并模板可能仍有后者。
    """
    has_radio = False
    same_inspection_bool_id = ""
    for sec in basic_step.get("sections", []):
        if not isinstance(sec, dict):
            continue
        for f in sec.get("fields", []):
            if not isinstance(f, dict):
                continue
            fid = str(f.get("id") or "")
            ft = str(f.get("type") or "").lower()
            if fid == "commissionOrgMode" and ft == "radio":
                has_radio = True
            if ft == "boolean":
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                hk = str(src.get("hierarchyKey") or "")
                lb = str(f.get("label") or "").strip()
                if fid == "委托单位_同受检单位" or "委托单位_同受检单位" in hk:
                    same_inspection_bool_id = fid
                elif lb == "同受检单位" and "委托单位" in fid:
                    same_inspection_bool_id = fid
    return has_radio, same_inspection_bool_id


def _apply_commission_organization_name_visibility(steps: List[Dict[str, Any]]) -> None:
    """委托单位名称：同受检单位时隐藏，选委托单位时显示。强制覆盖 visibleWhen 以免模板旧值导致一直隐藏。"""
    basic = next((s for s in steps if isinstance(s, dict) and str(s.get("id") or "") == "step_basic_info"), None)
    if not isinstance(basic, dict):
        return
    has_radio, same_bool_id = _basic_info_commission_visibility_controllers(basic)
    if not has_radio and not same_bool_id:
        return
    expr = ""
    if has_radio:
        expr = _VISIBLE_WHEN_COMMISSION_ORG_NAME
    elif same_bool_id:
        # boolean：勾选「同受检单位」为 true 时隐藏名称 → 名称在 false 时显示
        expr = f"{same_bool_id} == false"
    if not expr:
        return
    for sec in basic.get("sections", []):
        if not isinstance(sec, dict):
            continue
        for field in sec.get("fields", []):
            if isinstance(field, dict) and _field_is_commission_organization_name(field):
                field["visibleWhen"] = expr


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


def _pick_hierarchy_underscore_key(label_key: str, raw_key: str) -> str:
    """
    下划线分层键：label 与 rawId（placeholder/title）可能一长一短，
    优先取分段更多、更长的路径，避免「高对比…_状态_影增器」被截成「…_状态」导致 id 误为 status。
    """
    lk = str(label_key or "").strip()
    rk = str(raw_key or "").strip()
    has_l = "_" in lk
    has_r = "_" in rk
    if not has_l and not has_r:
        return lk or rk
    if has_l and not has_r:
        return lk
    if has_r and not has_l:
        return rk
    if rk.count("_") > lk.count("_"):
        return rk
    if lk.count("_") > rk.count("_"):
        return lk
    if len(rk) > len(lk) + 3:
        return rk
    if len(lk) > len(rk) + 3:
        return lk
    return lk


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


_INSTRUMENT_SCOPE_QC_MARKERS = (
    "主要检测仪器_质量控制",
    "主要检测仪器-质量控制",
)
_INSTRUMENT_SCOPE_RADIATION_MARKERS = (
    "主要检测仪器_工作场所放射防护",
    "主要检测仪器_放射防护",
    "主要检测仪器-工作场所放射防护",
    "主要检测仪器-放射防护",
)


def _infer_instrument_scope_from_text(*texts: str) -> str | None:
    """新版模板：第三章「受检设备主要检测仪器及检测人员」内各有一个「主要检测仪器_*」仪器选择格。"""
    blob = "|".join(str(t or "").strip() for t in texts if str(t or "").strip())
    if not blob:
        return None
    norm = normalize_field_text_by_underscore_rules(blob)
    # 防护/质控结果章标题、单元格常含「工作场所放射防护检测」「质量控制（性能）检测」等子串，不得误判为仪器栏。
    if "主要检测仪器" not in blob and "主要检测仪器" not in norm:
        return None
    if any(m in blob or m in norm for m in _INSTRUMENT_SCOPE_QC_MARKERS):
        return "qualityControl"
    if any(m in blob or m in norm for m in _INSTRUMENT_SCOPE_RADIATION_MARKERS):
        return "radiationProtection"
    return None


def _apply_scoped_instrument_select_field(field_obj: Dict[str, Any]) -> bool:
    """将分章节「主要检测仪器_*」标为 instrument_select，并写入 instruments.{scope} 提交路径。"""
    if not isinstance(field_obj, dict):
        return False
    src0 = field_obj.get("source") if isinstance(field_obj.get("source"), dict) else {}
    scope = _infer_instrument_scope_from_text(
        str(field_obj.get("label") or ""),
        str(field_obj.get("id") or ""),
        str(src0.get("hierarchyKey") or ""),
        str(src0.get("key") or ""),
    )
    if not scope:
        return False
    slot = 1 if scope == "qualityControl" else 2
    field_obj["type"] = "instrument_select"
    field_obj["instrumentScope"] = scope
    field_obj["registrySlot"] = slot
    if not str(field_obj.get("label") or "").strip():
        field_obj["label"] = (
            "主要检测仪器_质量控制（性能）检测"
            if scope == "qualityControl"
            else "主要检测仪器_工作场所放射防护检测"
        )
    src = src0 if isinstance(src0, dict) else {}
    src["submitBucket"] = "instruments"
    src["submitPath"] = f"instruments.{scope}"
    field_obj["source"] = src
    field_obj["submitBucket"] = "instruments"
    field_obj["submitPath"] = f"instruments.{scope}"
    return True


_RP_INSTRUMENT_META_MARKERS = (
    "使用的检测仪器相关信息",
    "仪器校准因子",
    "校准因子",
    "修正系数",
    "探测下限",
    "响应时间修正",
)


def _field_template_section_key(field_obj: Mapping[str, Any]) -> str:
    src = field_obj.get("source") if isinstance(field_obj.get("source"), dict) else {}
    return str(
        src.get("templateSectionKey")
        or field_obj.get("templateSectionKey")
        or field_obj.get("sectionKey")
        or ""
    ).strip()


def _is_radiation_chapter_instrument_meta_field(haystack: str, section_key: str) -> bool:
    """第五章表前「使用的检测仪器相关信息」区：参与报出值公式，不是第三章仪器清单。"""
    if section_key == "site_radiation_protection":
        return True
    return any(marker in haystack for marker in _RP_INSTRUMENT_META_MARKERS)


def _is_legacy_chapter3_instrument_list_field(haystack: str, section_key: str) -> bool:
    """第三章旧版检测仪器清单格（检测仪器N / 校准日期 / 有效期）。"""
    if section_key not in ("", "site_instruments_staff"):
        return False
    # 新版模板：instruments 仅 instrument_select（主要检测仪器_质控/防护）；勿把表内下划线日期格误判为仪器清单。
    if _contains_any(haystack, ("主要检测仪器",)) and not _infer_instrument_scope_from_text(haystack):
        return False
    if re.search(r"检测仪器\s*\d", haystack, re.I):
        return True
    if re.search(r"(?:^|[|])仪器\d", haystack, re.I):
        return True
    return _contains_any(haystack, ("校准日期", "有效期"))


def _assign_submit_bucket(field_obj: Dict[str, Any]) -> Dict[str, str]:
    """
    为前端字段标注提交归属桶，便于前端渲染后直接组装 submit payload：
    - reportInfo / hospitalInfo / equipmentInfo / instruments / testResult / signatures
    """
    label = str(field_obj.get("label") or "")
    fid = str(field_obj.get("id") or "")
    ft = str(field_obj.get("type") or "").lower()
    src0 = field_obj.get("source") if isinstance(field_obj.get("source"), dict) else {}
    hierarchy_key = str(src0.get("hierarchyKey") or "")
    haystack = f"{label}|{fid}|{hierarchy_key}"
    auto_semantic = src0.get("autoSemantic") if isinstance(src0.get("autoSemantic"), dict) else {}

    # 与 inspection_submit_placeholder_maps / 报告回填一致：温湿度走 testResult.temperature、humidity
    if fid == "environmentTempC":
        return {"bucket": "testResult", "path": "testResult.temperature"}
    if fid == "environmentHumidity":
        return {"bucket": "testResult", "path": "testResult.humidity"}

    pdf_fid = str(src0.get("pdfFieldId") or fid or "").strip()
    sig_role = _resolve_canonical_signature_role(label, pdf_fid, field_type=ft)
    if sig_role:
        return {"bucket": "signatures", "path": f"signatures.{sig_role[0]}"}
    if fid in CANONICAL_SIGNATURE_ROLE_IDS:
        return {"bucket": "signatures", "path": f"signatures.{fid}"}
    # signatures: 仅模板规定的三个签字栏位（兜底）
    if _contains_any(
        haystack,
        ("参与主要检测人员", "参与主要检测人员名单", "校核员及校核日期", "受检单位陪同人"),
    ) or ft == "signature":
        return {"bucket": "signatures", "path": f"signatures.{fid}"}

    inst_scope = _infer_instrument_scope_from_text(label, hierarchy_key, fid)
    if inst_scope == "qualityControl":
        return {"bucket": "instruments", "path": "instruments.qualityControl"}
    if inst_scope == "radiationProtection":
        return {"bucket": "instruments", "path": "instruments.radiationProtection"}

    section_key = _field_template_section_key(field_obj)

    if ft == "instrument_select":
        return {
            "bucket": "instruments",
            "path": f"instruments.{inst_scope}" if inst_scope else "instruments",
        }

    if _is_radiation_chapter_instrument_meta_field(haystack, section_key):
        return {"bucket": "testResult", "path": f"testResult.{pdf_fid or fid}"}

    # instruments 仅第三章仪器清单；禁止「仪器」子串误伤第五章表前区、质控结果格等
    if _is_legacy_chapter3_instrument_list_field(haystack, section_key) or fid == "instruments":
        return {"bucket": "instruments", "path": "instruments"}

    # reportInfo
    if _contains_any(haystack, ("委托编号", "受检编号", "检测日期", "环境温度", "湿度", "temperature", "humidity")):
        return {"bucket": "reportInfo", "path": f"reportInfo.{fid}"}

    # hospitalInfo
    if _contains_any(haystack, ("受检单位", "委托单位", "受检单位地址", "检测依据", "联系人", "电话", "检测类型")):
        return {"bucket": "hospitalInfo", "path": f"hospitalInfo.{fid}"}

    # equipmentInfo（设备所在场所统一 equipmentInfo.location，与回填 _inject_hospital_equipment_cn_aliases 一致）
    if _contains_any(haystack, ("设备型号", "额定参数", "设备名称", "设备编号", "设备所在场所", "生产厂家")):
        if _contains_any(haystack, ("设备所在场所",)):
            return {"bucket": "equipmentInfo", "path": "equipmentInfo.location"}
        return {"bucket": "equipmentInfo", "path": f"equipmentInfo.{fid}"}

    # 自动结构校对字段：提交路径由结构生成，避免多个“检测结果/报出值”挤到同一个 testResult.measuredValue。
    if auto_semantic:
        item_name = str(auto_semantic.get("itemName") or "").strip()
        role_name = str(auto_semantic.get("typeName") or "").strip()
        field_name = str(auto_semantic.get("fieldName") or "").strip()
        if item_name or role_name or field_name:
            item_slug = _underscore_stable_slug(item_name or field_name or "auto")
            leaf = fid if re.match(r"^[a-z][A-Za-z0-9]*$", fid) else _camel_case(role_name or field_name or fid, "value")
            coords: List[str] = []
            for key, prefix in (("tableId", "t"), ("row", "r"), ("col", "c"), ("slotNo", "s")):
                raw = auto_semantic.get(key)
                if raw is None or raw == "":
                    continue
                try:
                    coords.append(f"{prefix}{int(raw)}")
                except (TypeError, ValueError):
                    coords.append(f"{prefix}{_slug_ascii(str(raw), 'x')}")
            coord_part = "." + "_".join(coords) if coords else ""
            return {"bucket": "testResult", "path": f"testResult.auto.{item_slug}{coord_part}.{leaf}"}

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

# 旧版现场记录 PDF 签名框（只读兼容历史提交 dynamicData，禁止用于新模板 schema 推断）
_LEGACY_SIGNATURE_PDF_FIELD_ROLES: Dict[str, Tuple[str, str]] = {
    "f36": ("inspector", "参与主要检测人员（签字）"),
    "f35": ("checker", "校核员及校核日期（签字）"),
    "f34": ("accompanyingPerson", "受检单位陪同人（签字）"),
    "f76": ("inspector", "参与主要检测人员（签字）"),
    "f78": ("checker", "校核员及校核日期（签字）"),
    "f77": ("accompanyingPerson", "受检单位陪同人（签字）"),
}

CANONICAL_SIGNATURE_ROLE_IDS = frozenset({"inspector", "checker", "accompanyingPerson"})


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

    含中文的路径若仅用 ASCII 抽槽，常会只剩 mgy/min 等单位片段，导致「典型值」与「最大」
    等不同表头被误合并为同一 step/sec（例如 step_mgy_min / sec_mgy_min），故含 CJK 时一律
    以完整字符串哈希为 id，不再采用抽槽结果。
    """
    text = str(label or "").strip()
    if re.search(r"[\u4e00-\u9fff]", text):
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return f"u{h}"
    slug = _slug_ascii(text, "")
    if slug and slug != "id_" and len(slug) >= 2:
        return slug
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
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


_FLOOR_PLAN_LABEL_HINTS = (
    "平面布局示意图",
    "平面布局图",
    "布局示意图",
    "平面示意图",
)


def _label_indicates_floor_plan_image(label: str) -> bool:
    """标签本身即平面图（不含签字栏）。"""
    text = str(label or "").strip()
    if not text or _signature_role_from_label(text):
        return False
    if any(h in text for h in _FLOOR_PLAN_LABEL_HINTS):
        return True
    if "平面图" in text and "签字" not in text and "签名" not in text:
        return True
    return bool(
        "平面" in text
        and ("布局" in text or "示意" in text)
        and "签字" not in text
        and "签名" not in text
    )


def _field_anchor_type(field: Dict[str, Any]) -> str:
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    return str(src.get("anchorType") or field.get("anchorType") or "").strip().lower()


def _field_is_signature_image(field: Dict[str, Any]) -> bool:
    """签字/签名类图片栏位，不可标为 floorPlan。"""
    label = str(field.get("label") or "")
    if _signature_role_from_label(label):
        return True
    ft = str(field.get("type") or "").lower()
    if ft == "signature":
        return True
    if any(k in label for k in _SIGNATURE_KEYWORDS):
        return True
    if "签字" in label or "签名" in label:
        return True
    return False


def _field_is_floor_plan_image(field: Dict[str, Any], *, in_layout_section: bool = False) -> bool:
    """
    平面图影像：非签字类 image 框；在「平面布局示意图」章节内一律视为 floorPlan（兜底误命名）。
    """
    if _field_is_signature_image(field):
        return False
    label = str(field.get("label") or "")
    anchor = _field_anchor_type(field)
    ft = str(field.get("type") or "").lower()
    is_image_anchor = anchor == "image" or ft in ("image", "floorPlan")
    if not is_image_anchor:
        return False
    if _label_indicates_floor_plan_image(label):
        return True
    if in_layout_section:
        return True
    return False


def _infer_type(raw_type: str, label: str) -> str:
    rt = (raw_type or "").strip().lower()
    title = (label or "").strip()
    _, semantic_type = infer_field_properties(title)
    if semantic_type:
        return semantic_type
    if rt == "image":
        if _label_indicates_floor_plan_image(title):
            return "floorPlan"
        if any(k in title for k in _SIGNATURE_KEYWORDS):
            return "signature"
        return "image"
    if any(k in title for k in _SIGNATURE_KEYWORDS):
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


def _signature_role_from_pdf_field_id(pdf_field_id: str) -> Tuple[str, str] | None:
    """仅识别旧版固定 f 槽；新模板签名必须靠标签 + image 栏位。"""
    pid = str(pdf_field_id or "").strip().lower()
    if not pid:
        return None
    return _LEGACY_SIGNATURE_PDF_FIELD_ROLES.get(pid)


def _is_pdf_field_row_signature_image(row: Dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    label = str(row.get("id") or row.get("placeholder") or row.get("title") or "").strip()
    ft = str(row.get("fieldType") or "text").strip().lower()
    if ft not in ("image", "signature"):
        return False
    return _signature_role_from_label(label) is not None


def collect_signature_pdf_bindings(
    template_obj: Optional[Dict[str, Any]] = None,
    *,
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    从模板 pdf.fields（image + 签字标签）或 formSchema/payload steps（type=signature）
    收集 ``role -> pdfFieldId`` 与 ``pdfFieldId -> role``。不依赖固定 f 号。
    """
    role_to_pdf: Dict[str, str] = {}
    pdf_to_role: Dict[str, str] = {}

    def _register(role_id: str, pid: str) -> None:
        r = str(role_id or "").strip()
        p = str(pid or "").strip()
        if not r or not p or not re.match(r"^f\d+$", p, re.I):
            return
        p = p.lower()
        if p not in pdf_to_role:
            pdf_to_role[p] = r
        if r not in role_to_pdf:
            role_to_pdf[r] = p

    if isinstance(template_obj, dict):
        pdf = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
        fields = pdf.get("fields") if isinstance(pdf.get("fields"), list) else []
        if not fields:
            fields = template_obj.get("fields") if isinstance(template_obj.get("fields"), list) else []
        for row in fields:
            if not _is_pdf_field_row_signature_image(row):
                continue
            role = _signature_role_from_label(
                str(row.get("id") or row.get("placeholder") or row.get("title") or "")
            )
            if role:
                _register(role[0], str(row.get("pdfFieldId") or ""))

    steps_sources: List[Any] = []
    if isinstance(payload, dict):
        steps_sources.append(payload.get("steps"))
    if isinstance(template_obj, dict):
        fs = template_obj.get("formSchema")
        if isinstance(fs, dict):
            steps_sources.append(fs.get("steps"))
        steps_sources.append(template_obj.get("steps"))

    for steps in steps_sources:
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            for section in step.get("sections") or []:
                if not isinstance(section, dict):
                    continue
                for field in section.get("fields") or []:
                    if not isinstance(field, dict):
                        continue
                    if str(field.get("type") or "").lower() != "signature":
                        continue
                    role_id = str(field.get("id") or "").strip()
                    if role_id not in CANONICAL_SIGNATURE_ROLE_IDS:
                        continue
                    src = field.get("source") if isinstance(field.get("source"), dict) else {}
                    pid = str(field.get("pdfFieldId") or src.get("pdfFieldId") or "").strip()
                    _register(role_id, pid)

    return role_to_pdf, pdf_to_role


def _signature_role_from_label(label: str) -> Tuple[str, str] | None:
    """仅识别现场记录模板规定的三个签字栏位，不做宽泛「检测员/校核」归并。"""
    text = str(label or "").strip()
    if not text:
        return None
    if text in (
        "参与主要检测人员名单（签字）",
        "参与主要检测人员名单",
        "参与主要检测人员（签字）",
        "参与主要检测人员",
    ) or ("参与主要检测人员" in text and ("签字" in text or "签名" in text)):
        display = "参与主要检测人员（签字）" if "签字" in text else "参与主要检测人员"
        return ("inspector", display)
    if text in ("校核员及校核日期（签字）", "校核员及校核日期") or (
        "校核" in text and ("签字" in text or "签名" in text)
    ):
        return ("checker", text if "签字" in text else "校核员及校核日期（签字）")
    if text in ("受检单位陪同人（签字）", "受检单位陪同人") or (
        "陪同" in text and ("签字" in text or "签名" in text)
    ):
        return ("accompanyingPerson", text if "签字" in text else "受检单位陪同人（签字）")
    return None


def _resolve_canonical_signature_role(
    label: str,
    pdf_field_id: str,
    *,
    field_type: str = "",
) -> Tuple[str, str] | None:
    """标签 + image 栏位解析三角色签名；禁止把 checkbox/文本 f 槽误当签名。"""
    role = _signature_role_from_label(label)
    if role:
        return role
    ft = str(field_type or "").lower()
    if ft in ("check", "number", "boolean", "radio", "select", "text", "textarea"):
        return None
    if ft in ("image", "signature") or _field_is_signature_image(
        {"label": label, "type": field_type or "text"}
    ):
        return _signature_role_from_pdf_field_id(pdf_field_id)
    return None


def _pick_unit(label: str) -> str:
    low = (label or "").lower()
    for k, unit in _UNIT_HINTS:
        if k.lower() in low or k in label:
            return unit
    return ""


# WS76 透视/DSA 原始记录：下划线首段为「检测大项」时统一归入质控步，避免每个大项独占一个 step_*。
_QC_INS_EXCLUDE_FIRST_SEG = (
    "委托",
    "受检单位",
    "设备型号",
    "设备名称",
    "设备编号",
    "生产厂家",
    "额定参数",
    "设备所在",
    "环境温度",
    "环境湿度",
    "检测日期",
    "检测类型",
    "检测仪器",
    "医院",
    "报告信息",
    "综合结论",
    "判定",
    "合格",
)
_QC_INS_MARKERS_IN_FIRST_SEG = (
    "比释动能率",
    "分辨力",
    "入射屏前空气比释动能率",
    "自动亮度控制",
    "周围剂量当量率",
    "透视防护区",
    "DSA动态范围",
    "DSA对比灵敏度",
    "以下仅为DSA",
    "伪影",
    "减影中是否有",
)
_SHORT_QC_HEADS = frozenset({"伪影"})

# 编辑器常把 CT/WS521 分项拆成「一步」；标题较短时不满足 WS76 透视首段>=10 字规则，须显式并入质控步。
_STEP_TITLES_ALWAYS_MERGE_TO_QC = frozenset(
    {
        "图像均匀性",
        "测距误差",
        "KAP指示偏离",
    }
)


def _orphan_step_sections_only_test_result(st: Dict[str, Any]) -> bool:
    """未在标题白名单时：仅当该步内各栏位均为 testResult（且无 matrix）时，视为质控子项，可并入 step_qc_items。"""
    secs = st.get("sections")
    if not isinstance(secs, list) or not secs:
        return False
    saw_field = False
    for sec in secs:
        if not isinstance(sec, dict):
            continue
        if isinstance(sec.get("matrix"), dict) and sec.get("matrix"):
            return False
        for fld in sec.get("fields") or []:
            if not isinstance(fld, dict):
                continue
            if str(fld.get("type") or "").lower() == "signature":
                return False
            inner = fld.get("fields")
            if isinstance(inner, list) and inner:
                for sub in inner:
                    if not isinstance(sub, dict):
                        continue
                    if str(sub.get("type") or "").lower() == "signature":
                        return False
                    src = sub.get("source") if isinstance(sub.get("source"), dict) else {}
                    b = str(src.get("submitBucket") or "").strip()
                    if not b or b != "testResult":
                        return False
                    saw_field = True
                continue
            src = fld.get("source") if isinstance(fld.get("source"), dict) else {}
            b = str(src.get("submitBucket") or "").strip()
            if not b or b != "testResult":
                return False
            saw_field = True
    return saw_field


def _underscore_first_segment_is_ws76_style_inspection_item(seg: str) -> bool:
    s = str(seg or "").strip()
    if not s:
        return False
    if s in _SHORT_QC_HEADS:
        return True
    if len(s) < 10:
        return False
    if any(x in s for x in _QC_INS_EXCLUDE_FIRST_SEG):
        return False
    if any(m in s for m in _QC_INS_MARKERS_IN_FIRST_SEG):
        return True
    if s.startswith("DSA") and len(s) <= 24:
        return True
    return False


def _qc_semantic_rank_for_anchor_text(anchor: str) -> int:
    """
    质控步内「大项」阅读顺序：与 WS76 透视原始记录 PDF 常见栏目一致；未命中则 9000 退化为坐标排序。
    """
    s = str(anchor or "").strip()
    if not s:
        return 9000
    tiers: List[int] = []
    if "典型值" in s and "比释动能" in s:
        tiers.append(10)
    if "最大" in s and "比释动能" in s and "透视" in s:
        tiers.append(20)
    if ("高对比" in s and "分辨" in s) or "高对比度分辨力" in s:
        tiers.append(30)
    if ("低对比" in s and "分辨" in s) or "低对比度分辨力" in s:
        tiers.append(40)
    if "入射屏前空气比释动能率" in s or ("入射屏前" in s and "比释动能" in s):
        tiers.append(50)
    if "自动亮度" in s:
        tiers.append(60)
    if "透视防护区" in s or "周围剂量当量率" in s:
        tiers.append(70)
    # 防护区逐点表格行：标题常为「床侧…术者位…」，不含完整栏目名
    if ("术者位" in s or "球管中心" in s) and "床侧" in s:
        tiers.append(70)
    if "以下仅为DSA" in s:
        tiers.append(75)
    if "DSA动态范围" in s:
        tiers.append(80)
    if "DSA对比灵敏度" in s:
        tiers.append(90)
    # 避免「帧/s 伪影_检测结果」等 DSA 子字段名命中泛化「伪影」栏
    if "伪影" in s and ("减影" in s or "明显" in s or "是否有" in s or s.strip() == "伪影"):
        tiers.append(100)
    # WS521 CT / JS-117 等：分项标题短，不参与 WS76 透视长标题启发式，单独给序以便并入质控步与节内排序。
    if "图像均匀性" in s:
        tiers.append(200)
    if "测距误差" in s or ("测距" in s and "误差" in s):
        tiers.append(201)
    if ("KAP" in s or "kap" in s.lower()) and ("指示" in s or "偏离" in s or "面积" in s):
        tiers.append(202)
    return min(tiers) if tiers else 9000


def _first_segment_from_underscore_label(label: str) -> str:
    nk = normalize_field_text_by_underscore_rules(str(label or ""))
    if "_" not in nk:
        return ""
    return nk.split("_")[0]


def _section_qc_semantic_rank(section: Dict[str, Any]) -> int:
    """从节标题、matrix 行、字段下划线首段推断最小语义序。"""
    if not isinstance(section, dict):
        return 9000
    candidates: List[str] = []
    title = str(section.get("title") or "").strip()
    if title:
        candidates.append(title)
    sid = str(section.get("id") or "")
    if "sv_h" in sid.lower():
        candidates.append("透视防护区检测平面上周围剂量当量率")
    if _section_is_matrix_like(section):
        m = section.get("matrix") if isinstance(section.get("matrix"), dict) else {}
        for row in m.get("rows") or []:
            if not isinstance(row, dict):
                continue
            hdr = row.get("headers") if isinstance(row.get("headers"), dict) else {}
            ii = str(hdr.get("inspectionItem") or "").strip()
            if ii:
                candidates.append(ii)
    for f in section.get("fields") or []:
        if not isinstance(f, dict):
            continue
        src = f.get("source") if isinstance(f.get("source"), dict) else {}
        hk = str(src.get("hierarchyKey") or "").strip()
        if hk:
            fs0 = _first_segment_from_underscore_label(hk)
            if fs0:
                candidates.append(fs0)
        lab = str(f.get("label") or "")
        fs = _first_segment_from_underscore_label(lab)
        if fs:
            candidates.append(fs)
        elif lab:
            candidates.append(lab)
    ranks = [_qc_semantic_rank_for_anchor_text(c) for c in candidates if c]
    known = [r for r in ranks if r < 9000]
    if not known:
        return 9000
    # 仅「典型值」与「最大」两栏字段误入同一节时取较晚栏位；其它大项仍取 min，避免 DSA 节因个别伪影相关文案被抬到伪影层之后
    dose_tiers = sorted({t for t in known if t in (10, 20)})
    if len(dose_tiers) >= 2:
        return max(dose_tiers)
    if dose_tiers:
        return dose_tiers[0]
    return min(known)


def _section_order_key(step_id: str, section: Dict[str, Any], stable_idx: int) -> Tuple[int, int, float, float, int]:
    pyx = _section_min_pyx_for_sort(section)
    rk = _section_qc_semantic_rank(section) if step_id == "step_qc_items" else 0
    return (rk, pyx[0], pyx[1], pyx[2], stable_idx)


def _consolidate_orphan_inspection_steps(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将误生成的「一检测大项一步」合并回 step_qc_items（兼容旧导出）；正文结构以质控步 + 语义序为准。
    """
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return payload
    fixed = frozenset({"step_basic_info", "step_instruments", "step_qc_items", "step_judgement", "step_signature"})
    qc_step: Dict[str, Any] | None = None
    out: List[Dict[str, Any]] = []
    orphan_sections: List[Dict[str, Any]] = []

    for st in steps:
        if not isinstance(st, dict):
            continue
        sid = str(st.get("id") or "")
        if sid == "step_qc_items":
            qc_step = st
            out.append(st)
            continue
        if sid in fixed:
            out.append(st)
            continue
        st_title = str(st.get("title") or "").strip()
        merge = (
            _underscore_first_segment_is_ws76_style_inspection_item(st_title)
            or _qc_semantic_rank_for_anchor_text(st_title) < 9000
            or st_title in _STEP_TITLES_ALWAYS_MERGE_TO_QC
            or (
                bool(re.fullmatch(r"step_u[0-9a-f]{6,32}", sid, re.I))
                and _orphan_step_sections_only_test_result(st)
            )
        )
        if merge:
            secs = st.get("sections") if isinstance(st.get("sections"), list) else []
            for sec in secs:
                if not isinstance(sec, dict):
                    continue
                sec_out = copy.deepcopy(sec)
                subt = str(sec_out.get("title") or "").strip()
                if st_title:
                    if subt and st_title not in subt:
                        sec_out["title"] = f"{st_title} / {subt}"
                    elif not subt:
                        sec_out["title"] = st_title
                orphan_sections.append(sec_out)
            continue
        out.append(st)

    if orphan_sections:
        if qc_step is None:
            ins_idx = next((i for i, s in enumerate(out) if isinstance(s, dict) and str(s.get("id") or "") == "step_instruments"), -1)
            qc_step = {"id": "step_qc_items", "title": "质控检测项目", "sections": []}
            insert_at = ins_idx + 1 if ins_idx >= 0 else len(out)
            out.insert(insert_at, qc_step)
        qsecs = qc_step.get("sections")
        if not isinstance(qsecs, list):
            qsecs = []
            qc_step["sections"] = qsecs
        qsecs.extend(orphan_sections)

    payload["steps"] = out
    return payload


def _classify_group(label: str, field_type: str) -> Tuple[str, str, str, str, str]:
    title = label or ""
    if field_type == "signature" or any(k in title for k in _SIGNATURE_KEYWORDS):
        return ("step_signature", "综合结论与签字", "sec_signature", "签字与结论", "form")
    # 含下划线的标题：与 build 阶段 hierarchyKey 缺失时兜底一致，优先走下划线分层，避免落进泛化「质控检测项目」
    nt = normalize_field_text_by_underscore_rules(str(title).strip())
    if "_" in nt:
        uh = _classify_by_underscore(nt)
        if uh is not None:
            a, b, c, d = uh
            return (a, b, c, d, "form")
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
    if any(k in title for k in ("KAP", "ABC", "分辨力", "误差")):
        return ("step_qc_items", "质控检测项目", "sec_qc_items", "检测项目", "form")
    # 「剂量/比释动能率」等字样在长表头里极常见；仅当标题整体较短时才当作泛化质控项，避免整页透视表堆进同一节
    if any(k in title for k in ("剂量", "比释动能率", "入射屏前空气比释动能率")) and len(str(title).strip()) < 36:
        return ("step_qc_items", "质控检测项目", "sec_qc_items", "检测项目", "form")
    if any(k in title for k in ("温度", "湿度", "环境")):
        return ("step_basic_info", "基本信息", "sec_environment", "环境条件", "form")
    if any(k in title for k in ("报告", "委托", "日期", "编号", "受检")):
        return ("step_basic_info", "基本信息", "sec_report_info", "报告信息", "form")
    # 默认不再归到基本信息，避免“入射屏前空气比释动能率”等检测项误入基本信息。
    return ("step_qc_items", "质控检测项目", "sec_qc_items", "检测项目", "form")


def _classify_by_pdf_template_section(
    section_key: str,
    item: Dict[str, Any],
    *,
    signature_role: Tuple[str, str] | None = None,
    field_type: str = "",
) -> Tuple[str, str, str, str, str] | None:
    """
    新版现场记录 PDF：按章节标题纵坐标归属（templateSectionKey）。
    与旧版 submit 桶兼容：reportInfo/hospitalInfo/equipmentInfo/instruments/signatures/testResult。
    """
    key = str(section_key or "").strip()
    if not key:
        return None
    label = str(item.get("label") or "")
    ft = str(field_type or item.get("fieldType") or "").lower()
    is_signature = ft == "signature" or signature_role is not None or any(
        k in label
        for k in (
            "参与主要检测人员",
            "参与主要检测人员名单",
            "校核员及校核日期",
            "受检单位陪同人",
        )
    )
    if is_signature:
        return ("step_signature", "综合结论与签字", "sec_signature", "签字与结论", "form")

    if key == "site_unit_basic":
        if _contains_any(
            label,
            ("委托编号", "受检编号", "检测日期", "报告编号", "commissionNo", "inspectionNo"),
        ):
            return ("step_basic_info", "基本信息", "sec_report_info", "报告信息", "form")
        if _contains_any(label, ("受检单位", "委托单位", "联系人", "电话", "检测依据", "检测类型", "地址")):
            return ("step_basic_info", "基本信息", "sec_hospital_info", "医院信息", "form")
        return ("step_basic_info", "基本信息", "sec_site_unit_basic", "受检单位基本信息", "form")

    if key == "site_device_basic":
        return ("step_basic_info", "基本信息", "sec_device_info", "受检设备基本信息", "form")

    if key == "site_instruments_staff":
        if "检测仪器" in label or re.match(r"^仪器\d+$", label):
            return (
                "step_instruments",
                "检测仪器与人员",
                "sec_instruments",
                "检测仪器清单",
                "table",
            )
        if any(k in label for k in ("日期", "有效期", "校准")):
            return (
                "step_instruments",
                "检测仪器与人员",
                "sec_instrument_validity",
                "检测仪器有效期",
                "form",
            )
        return (
            "step_instruments",
            "检测仪器与人员",
            "sec_instruments_staff",
            "受检设备主要检测仪器及检测人员",
            "form",
        )

    if key == "site_qc_performance":
        auto = _classify_by_auto_semantic(item)
        if auto:
            step_id, step_title, sec_id, sec_title = auto
            return (step_id, step_title, sec_id, sec_title, "form")
        return ("step_qc_items", "质控检测项目", "sec_site_qc_performance", "质量控制（性能）检测项目及结果", "form")

    if key == "site_radiation_protection":
        return (
            "step_qc_items",
            "质控检测项目",
            "sec_site_radiation_protection",
            "工作场所放射防护检测结果",
            "form",
        )

    if key == "site_layout_diagram":
        return ("step_basic_info", "基本信息", "sec_site_layout_diagram", "平面布局示意图", "form")

    return None


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
    # 检测仪器分层：仅第三章「主要检测仪器_*」或旧版「检测仪器N」清单
    if _infer_instrument_scope_from_text(key) or re.search(r"检测仪器\s*\d", key, re.I):
        return ("step_instruments", "检测仪器", "sec_instruments", "检测仪器")
    if head in {"instrument", "instruments"}:
        return ("step_instruments", "检测仪器", "sec_instruments", "检测仪器")
    # 委托单位_*（互斥勾选项、委托单位名称等）统一并入基本信息-医院信息。
    # 勿用「委托单位」子串匹配：否则「委托单位_委托单位名称」等虽能命中，但条件易误读；
    # 且与「同受检单位/委托单位」两枚勾选项的意图一致——全部落在医院信息节。
    if key.startswith("委托单位_"):
        return ("step_basic_info", "基本信息", "sec_hospital_info", "医院信息")
    # 检测日期（含年月日分段）统一归入基本信息/报告信息
    if any(token in key for token in ("检测日期", "testdate", "test_date")) or (
        head in {"year", "month", "day"} and ("日期" in key or "date" in key.lower())
    ):
        return ("step_basic_info", "基本信息", "sec_report_info", "报告信息")
    # 设备信息关键项（含额定参数）固定归到基本信息/设备信息
    if any(token in key for token in ("设备型号", "额定参数", "设备名称", "设备编号", "设备所在场所", "生产厂家")):
        return ("step_basic_info", "基本信息", "sec_device_info", "设备信息")
    # 环境温度/湿度：常见为表头「环境温度/湿度」+ 子字段（°C、%RH），勿拆成独立 step
    env_anchor = f"{prefix}|{key}"
    if any(t in env_anchor for t in ("环境温度", "环境湿度")) or (
        "温度" in prefix and "湿度" in prefix.replace("/", "").replace("／", "")
    ):
        return ("step_basic_info", "基本信息", "sec_environment", "环境条件")
    # 检测类型：状态/验收勾选项共享前缀「检测类型」，并入基本信息/报告信息
    if "检测类型" in prefix or "检测类型" in key:
        return ("step_basic_info", "基本信息", "sec_report_info", "报告信息")
    # 透视/DSA 检测大项：首段即栏目名，统一并入「质控检测项目」，再按语义序 + 坐标排节内顺序
    if len(parts) >= 2 and _underscore_first_segment_is_ws76_style_inspection_item(parts[0]):
        section_slug = _underscore_stable_slug(prefix)
        sec_id = f"sec_{section_slug}"
        return ("step_qc_items", "质控检测项目", sec_id, section_display)
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

    # 质控检测类关键字：仅命中「短表头 / 典型质控小节」时并入 step_qc_items。
    # 不在此用「比释动能率/剂量」等做子串匹配：长中文表头（透视原始记录等）两段命名会误进泛化质控步。
    p0 = parts[0]
    long_zh_header = bool(re.search(r"[\u4e00-\u9fff]", p0)) and len(p0) >= 16
    if not long_zh_header and (
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


_AUTO_SEMANTIC_FIELD_ID_MAP = {
    "检测条件": "condition",
    "检测结果": "measuredValue",
    "计算结果": "computedValue",
    "报出值": "reportValue",
    "验收": "acceptanceVerdict",
    "状态": "statusVerdict",
    "单项判定": "verdict",
    "判定": "verdict",
    "检测值": "measuredValue",
}


def _auto_semantic_clean_text(value: Any) -> str:
    text = normalize_field_text_by_underscore_rules(str(value or "").strip())
    text = re.sub(r"\s+", "", text)
    return text.strip("_")


def _auto_semantic_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    sem = item.get("autoSemantic")
    return sem if isinstance(sem, dict) else {}


def _classify_by_auto_semantic(item: Dict[str, Any]) -> Tuple[str, str, str, str] | None:
    """
    自动划框已经能拿到表格行列语义时，优先按结构化信息分层。
    这样前端 schema 不再依赖人工把字段名写成「大项_字段」。
    """
    sem = _auto_semantic_from_item(item)
    item_name = _auto_semantic_clean_text(sem.get("itemName"))
    field_name = _auto_semantic_clean_text(sem.get("fieldName"))
    if not item_name and field_name and "_" in field_name:
        item_name = field_name.split("_", 1)[0]
    if not item_name:
        return None
    sec_id = f"sec_{_underscore_stable_slug(item_name)}"
    return ("step_qc_items", "质控检测项目", sec_id, item_name)


def _auto_semantic_field_id_label(item: Dict[str, Any], fallback_id: str, fallback_label: str) -> Tuple[str, str]:
    sem = _auto_semantic_from_item(item)
    if not sem:
        return fallback_id, fallback_label

    type_name = _auto_semantic_clean_text(sem.get("typeName"))
    underline_part = _auto_semantic_clean_text(sem.get("underlinePartName"))
    tail_name = _auto_semantic_clean_text(sem.get("tailName"))
    field_name = _auto_semantic_clean_text(sem.get("fieldName"))
    item_name = _auto_semantic_clean_text(sem.get("itemName"))

    suffix = ""
    if field_name and item_name and field_name.startswith(f"{item_name}_"):
        suffix = field_name[len(item_name) + 1 :]
    if not suffix and field_name and "_" in field_name:
        suffix = field_name.rsplit("_", 1)[-1]
    role = type_name or suffix or underline_part or tail_name or field_name
    if not role:
        return fallback_id, fallback_label

    mapped = _AUTO_SEMANTIC_FIELD_ID_MAP.get(role, "")
    if not mapped and type_name and underline_part and underline_part != type_name:
        mapped = f"{_AUTO_SEMANTIC_FIELD_ID_MAP.get(type_name, _to_english_id(type_name, type_name, 'value'))}_{_to_english_id(underline_part, underline_part, 'slot')}"
    if not mapped:
        mapped = _to_english_id(role, role, fallback_id)
    field_id = str(mapped) if re.match(r"^[a-z][A-Za-z0-9]*$", str(mapped)) else _camel_case(mapped, fallback_id)
    label = role
    if type_name and underline_part and underline_part not in {type_name, role}:
        label = f"{type_name}-{underline_part}"
    return field_id or fallback_id, label or fallback_label


def _underscore_field_id(hierarchy_key: str, fallback_id: str) -> str:
    parts = [p for p in normalize_field_text_by_underscore_rules(str(hierarchy_key or "")).split("_") if p]
    if len(parts) < 2:
        return fallback_id
    suffix = parts[-1]
    mapped = _UNDERSCORE_SUFFIX_MAP.get(suffix, "") or _UNDERSCORE_SUFFIX_MAP.get(suffix.lower(), "")
    if not mapped:
        # 「…_状态」仅两段时，suffix「状态」会经 _TERM_MAP 变成全局 status，与检测类型等冲突
        if suffix == "状态" and len(parts) == 2:
            base_slug = _underscore_stable_slug(parts[0])
            return _camel_case(f"fieldState_{base_slug}", fallback_id)
        mapped = _to_english_id(suffix, suffix, fallback_id)
    # 映射表已为合法 camelCase id 时勿再经 _slug_ascii，否则会破坏大小写（如 environmentTempC）。
    if mapped and re.match(r"^[a-z][A-Za-z0-9]*$", str(mapped)):
        return str(mapped)
    out = _camel_case(mapped, fallback_id)
    return out or fallback_id


def _underscore_field_label(hierarchy_key: str, fallback_label: str) -> str:
    parts = [p for p in normalize_field_text_by_underscore_rules(str(hierarchy_key or "")).split("_") if p]
    if len(parts) < 2:
        return normalize_field_text_by_underscore_rules(fallback_label) or fallback_label
    suffix = parts[-1]
    mapped = _UNDERSCORE_SUFFIX_MAP.get(suffix, "") or _UNDERSCORE_SUFFIX_MAP.get(suffix.lower(), "")
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


def _best_semantic_underscore_key_from_pdf_item(item: Dict[str, Any], idx: int) -> str:
    """
    分层用：在 title / placeholder / id 中取「下划线分段最多、字符串最长」的一条。
    避免 id 仅为 f 序号时盖住带完整语义路径的 title，导致互斥勾选落到 sec_qc_items 等泛化层级。
    """
    candidates: List[str] = []
    for key in ("title", "placeholder", "id"):
        raw = item.get(key)
        if raw is None:
            continue
        s = normalize_field_text_by_underscore_rules(str(raw).strip())
        if not s:
            continue
        if key == "id" and re.match(r"^f\d+$", s, re.I):
            continue
        candidates.append(s)
    if not candidates:
        return normalize_field_text_by_underscore_rules(
            str(item.get("title") or item.get("placeholder") or item.get("id") or f"字段{idx}").strip()
        )
    return max(candidates, key=lambda t: (t.count("_"), len(t)))


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
    out: Dict[str, Any] = {
        "rawId": source_id,
        "label": label or source_id or f"字段{idx}",
        "fieldType": str(item.get("fieldType") or "text"),
        "pdfFieldId": pdf_field_id,
        "page": page,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "listIndex": idx,
        "order": (page, y, x, idx),
    }
    cp = item.get("checkboxPair")
    if isinstance(cp, dict) and cp:
        out["checkboxPair"] = cp
    auto_semantic = item.get("autoSemantic")
    if isinstance(auto_semantic, dict) and auto_semantic:
        out["autoSemantic"] = {
            str(k): v
            for k, v in auto_semantic.items()
            if isinstance(k, str) and v is not None and v != ""
        }
    ts_key = str(item.get("templateSectionKey") or "").strip()
    if ts_key:
        out["templateSectionKey"] = ts_key
    ts_title = str(item.get("templateSectionTitle") or "").strip()
    if ts_title:
        out["templateSectionTitle"] = ts_title
    jct = str(item.get("judgmentCriterionText") or "").strip()
    if jct:
        out["judgmentCriterionText"] = jct[:500]
    out["hierarchyKey"] = _best_semantic_underscore_key_from_pdf_item(item, idx)
    ts_key = str(item.get("templateSectionKey") or "").strip()
    ts_title = str(item.get("templateSectionTitle") or "").strip()
    if ts_key:
        out["templateSectionKey"] = ts_key
    if ts_title:
        out["templateSectionTitle"] = ts_title
    return out


def apply_table_row_col_to_source(
    src: Dict[str, Any],
    *,
    table_id: Any | None = None,
    row: Any | None = None,
    col: Any | None = None,
    auto_semantic: Dict[str, Any] | None = None,
) -> None:
    """表格行列语义仅写入 source.autoSemantic，避免与顶层 tableId/row/col 重复。"""
    if not isinstance(src, dict):
        return
    sem: Dict[str, Any] = {}
    existing = src.get("autoSemantic")
    if isinstance(existing, dict):
        sem.update(existing)
    if isinstance(auto_semantic, dict):
        sem.update(auto_semantic)
    if table_id is not None and table_id != "":
        sem["tableId"] = table_id
    if row is not None and row != "":
        sem["row"] = row
    if col is not None and col != "":
        sem["col"] = col
    if sem:
        src["autoSemantic"] = sem
    for key in ("tableId", "row", "col", "cellId", "slotNo"):
        src.pop(key, None)


def inject_table_row_col_meta(field: Dict[str, Any]) -> None:
    """导出收尾：表格信息只保留 source.autoSemantic，去掉顶层重复键。"""
    if not isinstance(field, dict):
        return
    src = field.get("source")
    if not isinstance(src, dict):
        return
    if isinstance(src.get("autoSemantic"), dict):
        for key in ("tableId", "row", "col", "cellId", "slotNo"):
            src.pop(key, None)


def _iter_form_field_nodes(payload: Dict[str, Any]):
    """遍历 steps 内所有表单栏位（含 matrix 单元格）。"""
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for section in step.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for field in section.get("fields") or []:
                if isinstance(field, dict):
                    yield section, field
            matrix = section.get("matrix")
            if not isinstance(matrix, dict):
                continue
            for field in matrix.get("headerFields") or []:
                if isinstance(field, dict):
                    yield section, field
            for row in matrix.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells")
                if isinstance(cells, dict):
                    for cell in cells.values():
                        if isinstance(cell, dict):
                            yield section, cell


def _field_pdf_ids(field: Dict[str, Any]) -> List[str]:
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    ids: List[str] = []
    raw_list = src.get("pdfFieldIds")
    if isinstance(raw_list, list):
        for x in raw_list:
            pid = str(x or "").strip()
            if pid and pid not in ids:
                ids.append(pid)
    pid = str(src.get("pdfFieldId") or field.get("pdfFieldId") or "").strip()
    if pid and pid not in ids:
        ids.append(pid)
    return ids


def _rect_from_field_for_binding(field: Dict[str, Any]) -> List[float] | None:
    rect = field.get("rect")
    if isinstance(rect, (list, tuple)) and len(rect) >= 5:
        try:
            return [
                int(rect[0]),
                round(float(rect[1]), 2),
                round(float(rect[2]), 2),
                round(float(rect[3]), 2),
                round(float(rect[4]), 2),
            ]
        except (TypeError, ValueError):
            pass
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    pb = src.get("pdfBinding")
    if isinstance(pb, dict) and isinstance(pb.get("rect"), (list, tuple)) and len(pb["rect"]) >= 5:
        try:
            r = pb["rect"]
            return [int(r[0]), round(float(r[1]), 2), round(float(r[2]), 2), round(float(r[3]), 2), round(float(r[4]), 2)]
        except (TypeError, ValueError):
            pass
    return frontend_rect_from_field(field)


def _is_pdf_overlay_payload(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    value = payload.get("pdfOverlay")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _pdf_anchor_from_field(field: Dict[str, Any]) -> Dict[str, Any] | None:
    """Return Flutter-friendly PDF anchor: ``{page, rect:[x0,y0,x1,y1]}``."""
    if not isinstance(field, dict):
        return None

    existing = field.get("pdfAnchor")
    if isinstance(existing, dict):
        rect = existing.get("rect")
        if isinstance(rect, (list, tuple)) and len(rect) >= 4:
            try:
                explicit_page = existing.get("page") or field.get("page")
                if len(rect) >= 5 and explicit_page in (None, ""):
                    page = int(rect[0])
                    x = float(rect[1])
                    y = float(rect[2])
                    w = float(rect[3])
                    h = float(rect[4])
                    x0, y0, x1, y1 = x, y, x + max(w, 0.0), y + max(h, 0.0)
                else:
                    page = int(explicit_page or 1)
                    x0 = float(rect[0])
                    y0 = float(rect[1])
                    x1 = float(rect[2])
                    y1 = float(rect[3])
            except (TypeError, ValueError):
                pass
            else:
                out = {
                    "page": page,
                    "rect": [
                        round(min(x0, x1), 2),
                        round(min(y0, y1), 2),
                        round(max(x0, x1), 2),
                        round(max(y0, y1), 2),
                    ],
                }
                at = str(existing.get("anchorType") or field.get("anchorType") or "").strip()
                if at:
                    out["anchorType"] = at
                return out

    rect = _rect_from_field_for_binding(field)
    if not rect or len(rect) < 5:
        return None
    try:
        page = int(rect[0])
        x = float(rect[1])
        y = float(rect[2])
        w = float(rect[3])
        h = float(rect[4])
    except (TypeError, ValueError):
        return None
    if w <= 0 and h <= 0:
        return None

    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    out = {
        "page": page,
        "rect": [
            round(x, 2),
            round(y, 2),
            round(x + max(w, 0.0), 2),
            round(y + max(h, 0.0), 2),
        ],
    }
    at = str(field.get("anchorType") or src.get("anchorType") or "").strip()
    if at:
        out["anchorType"] = at
    return out


def _attach_pdf_anchors_to_form_schema(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep page/coords for PDF overlay mode while removing legacy coordinate noise."""
    if not isinstance(payload, dict):
        return payload
    payload.pop("pdfBindings", None)
    payload.pop("pdf", None)
    payload["pdfOverlay"] = True
    for _section, field in _iter_form_field_nodes(payload):
        inject_table_row_col_meta(field)
        anchor = _pdf_anchor_from_field(field)
        if anchor is not None:
            field["pdfAnchor"] = anchor
        for key in ("rect", "page", "x", "y", "w", "h", "__order", "__bbox"):
            field.pop(key, None)
        src = field.get("source")
        if isinstance(src, dict):
            for key in ("page", "x", "y", "w", "h", "pages", "pdfBinding", "anchorType"):
                src.pop(key, None)
    return payload


def _attach_rect_and_strip_legacy_coords(payload: Dict[str, Any]) -> Dict[str, Any]:
    """兼容旧调用名：仅剥离表单 steps 中的坐标键（回填靠 ``pdfFieldId``，不写 pdfBindings）。"""
    return _strip_pdf_coords_from_form_schema(payload)


def _extract_pdf_bindings_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """兼容旧调用名：与 ``_strip_pdf_coords_from_form_schema`` 相同，不再输出 pdfBindings。"""
    return _strip_pdf_coords_from_form_schema(payload)


def _strip_pdf_coords_from_form_schema(payload: Dict[str, Any]) -> Dict[str, Any]:
    """前端表单 JSON 不含 PDF 坐标；回填仅依赖各栏位 ``pdfFieldId``（或 ``pdfFieldIds``）。"""
    if not isinstance(payload, dict):
        return payload
    if _is_pdf_overlay_payload(payload):
        return _attach_pdf_anchors_to_form_schema(payload)
    payload.pop("pdfBindings", None)
    payload.pop("pdf", None)
    for _section, field in _iter_form_field_nodes(payload):
        inject_table_row_col_meta(field)
        field.pop("rect", None)
        field.pop("pdfAnchor", None)
        field.pop("page", None)
        field.pop("x", None)
        field.pop("y", None)
        field.pop("w", None)
        field.pop("h", None)
        field.pop("__order", None)
        field.pop("__bbox", None)
        field.pop("__editorOrder", None)
        src = field.get("source")
        if isinstance(src, dict):
            for key in ("page", "x", "y", "w", "h", "pages", "pdfBinding", "anchorType"):
                src.pop(key, None)
    return payload


def _minimal_cell_ref(sem: Dict[str, Any]) -> str:
    cell = str(sem.get("cellId") or "").strip()
    if cell:
        return cell
    try:
        tid = int(sem.get("tableId") or 0)
        row = int(sem.get("row"))
        col = int(sem.get("col"))
        return f"t{tid}_r{row}_c{col}"
    except (TypeError, ValueError):
        return ""


def _build_export_table_semantic(sem: Dict[str, Any], section_key: str = "") -> Dict[str, Any] | None:
    """
    导出表格语义（写入栏位顶层 table）：
    - 行列/cellId、itemName/typeName/fieldName：供前端组表与副标题（label 常为「检测结果」等短名）
    - 防护表额外：radiationPoint、radiationColumn、readingIndex 等
    PDF 坐标 rect 仍在坐标模板 JSON，不在此块。
    """
    if not isinstance(sem, dict) or not sem:
        return None

    block: Dict[str, Any] = {}
    cell = _minimal_cell_ref(sem)
    if cell:
        block["cellId"] = cell
    for key in ("tableId", "row", "col", "readingIndex"):
        raw = sem.get(key)
        if raw is None or raw == "":
            continue
        try:
            block[key] = int(raw)
        except (TypeError, ValueError):
            block[key] = raw
    slot = sem.get("slotNo")
    if slot is not None and slot != "":
        try:
            block["slotNo"] = int(slot)
        except (TypeError, ValueError):
            block["slotNo"] = slot
    for key in (
        "radiationPoint",
        "radiationColumn",
        "itemName",
        "typeName",
        "fieldName",
        "underlinePartName",
        "tailName",
    ):
        val = str(sem.get(key) or "").strip()
        if val:
            block[key] = val
    if sem.get("meanOfReadings"):
        block["meanOfReadings"] = True

    return block if block else None


def _attach_export_table_semantic(field: Dict[str, Any], section_key: str = "") -> None:
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    sem = src.get("autoSemantic") if isinstance(src.get("autoSemantic"), dict) else {}
    if not sem and isinstance(field.get("table"), dict):
        return
    block = _build_export_table_semantic(sem, section_key)
    if block:
        field["table"] = block
        if block.get("meanOfReadings"):
            field["mean"] = True


def _compact_single_form_field(
    field: Dict[str, Any],
    section_key: str = "",
    *,
    keep_pdf_anchor: bool = False,
) -> Dict[str, Any]:
    """单栏位最小结构；PDF 覆盖模式额外保留 ``pdfAnchor``。"""
    if not isinstance(field, dict):
        return field
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    out: Dict[str, Any] = {}

    fid = str(field.get("id") or "").strip()
    ftype = str(field.get("type") or "text").strip() or "text"
    label = str(field.get("label") or "").strip()
    if fid:
        out["id"] = fid
    if ftype:
        out["type"] = ftype
    if label:
        out["label"] = label

    primary_pid = str(src.get("pdfFieldId") or field.get("pdfFieldId") or "").strip()
    pdf_ids = src.get("pdfFieldIds")
    merged_ids: List[str] = []
    if primary_pid:
        merged_ids.append(primary_pid)
    if isinstance(pdf_ids, list):
        for x in pdf_ids:
            pid = str(x or "").strip()
            if pid and pid not in merged_ids:
                merged_ids.append(pid)
    if len(merged_ids) > 1:
        out["pdfFieldIds"] = merged_ids
        out["pdfFieldId"] = merged_ids[0]
    elif len(merged_ids) == 1:
        out["pdfFieldId"] = merged_ids[0]
    elif primary_pid:
        out["pdfFieldId"] = primary_pid

    if keep_pdf_anchor:
        anchor = field.get("pdfAnchor")
        if not isinstance(anchor, dict):
            anchor = _pdf_anchor_from_field(field)
        if isinstance(anchor, dict):
            out["pdfAnchor"] = anchor

    sp = str(field.get("submitPath") or src.get("submitPath") or "").strip()
    if sp:
        out["submitPath"] = sp

    bucket = str(field.get("submitBucket") or src.get("submitBucket") or "").strip()
    if bucket:
        out["submitBucket"] = bucket
    elif sp and "." in sp:
        out["submitBucket"] = sp.split(".", 1)[0]

    schema_key = str(field.get("schemaKey") or src.get("key") or "").strip()
    if schema_key:
        out["schemaKey"] = schema_key
    hk = str(field.get("hierarchyKey") or src.get("hierarchyKey") or "").strip()
    if hk:
        out["hierarchyKey"] = hk
    jct = str(field.get("judgmentCriterionText") or src.get("judgmentCriterionText") or "").strip()
    if jct:
        out["judgmentCriterionText"] = jct[:500]
    jctt = field.get("judgmentCriteriaByTestType")
    if not isinstance(jctt, dict) or not jctt:
        jctt = src.get("judgmentCriteriaByTestType")
    if isinstance(jctt, dict) and jctt:
        compact_jct: Dict[str, Any] = {}
        for k in ("acceptance", "status"):
            v = str(jctt.get(k) or "").strip()
            if v:
                compact_jct[k] = v
        if compact_jct:
            from utils.conditional_field_rules import normalize_logic_connectors_for_frontend_export

            out["judgmentCriteriaByTestType"] = {
                k: normalize_logic_connectors_for_frontend_export(v)
                for k, v in compact_jct.items()
            }
    if field.get("judgmentCriteriaManual") or src.get("judgmentCriteriaManual"):
        out["judgmentCriteriaManual"] = True
    if field.get("fieldFormulaUserOverride") or src.get("fieldFormulaUserOverride"):
        out["fieldFormulaUserOverride"] = True
    fv = field.get("fieldVerdict")
    if not isinstance(fv, dict) or not fv:
        fv = src.get("fieldVerdict")
    if isinstance(fv, dict) and fv:
        out["fieldVerdict"] = fv
    vw = field.get("visibleWhen")
    if vw not in (None, "", {}):
        out["visibleWhen"] = vw

    sem = src.get("autoSemantic") if isinstance(src.get("autoSemantic"), dict) else {}
    table_block = _build_export_table_semantic(sem, section_key)
    if table_block:
        out["table"] = table_block
        if table_block.get("meanOfReadings"):
            out["mean"] = True
    elif isinstance(field.get("table"), dict):
        out["table"] = dict(field["table"])
        if out["table"].get("meanOfReadings"):
            out["mean"] = True

    if field.get("required"):
        out["required"] = True
    dv = field.get("defaultValue")
    if dv is not None and dv is not False and dv != "":
        out["defaultValue"] = dv
    er = str(field.get("enumRef") or "").strip()
    if er:
        out["enumRef"] = er
    width = field.get("width")
    if width not in (None, "", "half", 0.5):
        out["width"] = width
    ftype_l = str(out.get("type") or "text").lower()
    pfx = str(
        field.get("formula")
        or field.get("fieldExpression")
        or src.get("pdfFieldExpression")
        or src.get("fieldExpression")
        or ""
    ).strip()
    if pfx:
        out["fieldExpression"] = pfx
        if ftype_l in ("computed", "number") or field.get("formula") or src.get("pdfFieldExpression"):
            out["formula"] = pfx
    rules_val = field.get("formulaRules")
    if not isinstance(rules_val, list) or not rules_val:
        rules_val = src.get("formulaRules")
    if not isinstance(rules_val, list) or not rules_val:
        rules_val = field.get("fieldExpressionRules") or src.get("fieldExpressionRules")
    if isinstance(rules_val, list) and rules_val:
        out["formulaRules"] = copy.deepcopy(rules_val)
    for key in (
        "unit",
        "precision",
        "formula",
        "fieldExpression",
        "dependsOn",
        "rule",
        "minLines",
        "maxLines",
        "columns",
        "initialRows",
        "options",
        "instrumentScope",
        "registrySlot",
    ):
        val = field.get(key)
        if val is None or val == "" or val == []:
            val = src.get(key)
        if val is None or val == "" or val == []:
            continue
        if key in ("precision", "unit") and ftype_l not in ("number", "computed"):
            continue
        if key == "unit" and section_key == "site_radiation_protection":
            continue
        out[key] = copy.deepcopy(val) if isinstance(val, (dict, list)) else val

    try:
        from utils.conditional_field_rules import enrich_frontend_field_with_conditional_rules

        merged = dict(out)
        if isinstance(out.get("formulaRules"), list):
            merged["formulaRules"] = out["formulaRules"]
        elif isinstance(field.get("formulaRules"), list):
            merged["formulaRules"] = field["formulaRules"]
        elif isinstance(src.get("formulaRules"), list):
            merged["formulaRules"] = src["formulaRules"]
        elif isinstance(field.get("fieldExpressionRules"), list):
            merged["formulaRules"] = field["fieldExpressionRules"]
        elif isinstance(src.get("fieldExpressionRules"), list):
            merged["formulaRules"] = src["fieldExpressionRules"]
        out = enrich_frontend_field_with_conditional_rules(merged)
        out.pop("fieldExpressionRules", None)
    except Exception:
        pass
    return out


def _compact_matrix_object(
    matrix: Dict[str, Any],
    section_key: str = "",
    *,
    keep_pdf_anchor: bool = False,
) -> Dict[str, Any]:
    if not isinstance(matrix, dict):
        return matrix
    out: Dict[str, Any] = {}
    if matrix.get("id"):
        out["id"] = matrix["id"]
    if matrix.get("title"):
        out["title"] = matrix["title"]
    headers = matrix.get("headerFields")
    if isinstance(headers, list) and headers:
        out["headerFields"] = [
            _compact_single_form_field(f, section_key, keep_pdf_anchor=keep_pdf_anchor)
            for f in headers
            if isinstance(f, dict)
        ]
    rows_out: List[Dict[str, Any]] = []
    for row in matrix.get("rows") or []:
        if not isinstance(row, dict):
            continue
        row_id = row.get("id")
        cells = row.get("cells")
        if not isinstance(cells, dict):
            continue
        cells_compact = {
            k: _compact_single_form_field(v, section_key, keep_pdf_anchor=keep_pdf_anchor)
            for k, v in cells.items()
            if isinstance(v, dict)
        }
        if cells_compact:
            rows_out.append({"id": row_id, "cells": cells_compact} if row_id else {"cells": cells_compact})
    if rows_out:
        out["rows"] = rows_out
    cols = matrix.get("columns")
    if isinstance(cols, list) and cols:
        out["columns"] = cols
    return out


def _prune_enums_to_used(payload: Dict[str, Any]) -> Dict[str, Any]:
    used: set = set()
    for _sec, field in _iter_form_field_nodes(payload):
        er = str(field.get("enumRef") or "").strip()
        if er:
            used.add(er)
    enums = payload.get("enums")
    if isinstance(enums, dict) and used:
        payload["enums"] = {k: v for k, v in enums.items() if k in used}
    elif isinstance(enums, dict) and not used:
        payload["enums"] = {}
    return payload


def _compact_form_schema_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """压平 steps 栏位结构；PDF 覆盖模式保留 ``pdfAnchor``。"""
    if not isinstance(payload, dict):
        return payload
    keep_pdf_anchor = _is_pdf_overlay_payload(payload)
    payload.pop("pdfBindings", None)
    payload.pop("pdf", None)
    payload.pop("sections", None)

    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            sk = str(sec.get("sectionKey") or sec.get("id") or "").strip()
            if sk:
                sec["id"] = sk
                sec.pop("sectionKey", None)
            sec.pop("templateSectionKey", None)
            sec.pop("templateSectionTitle", None)
            sec.pop("section", None)
            layout = str(sec.get("layout") or "form").strip().lower()
            if layout == "form":
                sec.pop("layout", None)
            elif layout:
                sec["layout"] = layout

            matrix = sec.get("matrix")
            if isinstance(matrix, dict):
                sec["matrix"] = _compact_matrix_object(
                    matrix,
                    sk,
                    keep_pdf_anchor=keep_pdf_anchor,
                )
            fields = sec.get("fields")
            if isinstance(fields, list):
                compacted: List[Any] = []
                for f in fields:
                    if not isinstance(f, dict):
                        continue
                    if str(f.get("type") or "").lower() == "table" and isinstance(f.get("columns"), list):
                        tbl = _compact_single_form_field(
                            f,
                            sk,
                            keep_pdf_anchor=keep_pdf_anchor,
                        )
                        if f.get("columns"):
                            tbl["columns"] = f["columns"]
                        if f.get("initialRows"):
                            tbl["initialRows"] = f["initialRows"]
                        compacted.append(tbl)
                    else:
                        compacted.append(
                            _compact_single_form_field(
                                f,
                                sk,
                                keep_pdf_anchor=keep_pdf_anchor,
                            )
                        )
                if compacted:
                    sec["fields"] = compacted
                else:
                    sec.pop("fields", None)
    return _prune_enums_to_used(payload)


def _coerce_radio_select_defaults_to_string(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Flutter 侧 radio/select 的 defaultValue 须为字符串；模板或合并若留下 bool 会导致 type cast 失败。"""

    def _fix(field: Dict[str, Any]) -> None:
        if not isinstance(field, dict):
            return
        ft = str(field.get("type") or "").lower()
        if ft == "table":
            for col in field.get("columns") or []:
                if isinstance(col, dict):
                    _fix(col)
            return
        if ft not in ("radio", "select"):
            return
        dv = field.get("defaultValue")
        if dv is None:
            return
        if isinstance(dv, bool):
            er = str(field.get("enumRef") or "")
            if er == "yesNo":
                field["defaultValue"] = "yes" if dv else "no"
            else:
                field["defaultValue"] = ""
        elif not isinstance(dv, str):
            field["defaultValue"] = str(dv)

    for step in payload.get("steps", []):
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            for field in section.get("fields", []):
                if isinstance(field, dict):
                    _fix(field)
            matrix = section.get("matrix")
            if not isinstance(matrix, dict):
                continue
            for field in matrix.get("headerFields", []):
                if isinstance(field, dict):
                    _fix(field)
            for row in matrix.get("rows", []):
                if not isinstance(row, dict):
                    continue
                cells = row.get("cells")
                if not isinstance(cells, dict):
                    continue
                for cell in cells.values():
                    if isinstance(cell, dict):
                        _fix(cell)
    return payload


def _field_editor_list_order(field: Dict[str, Any]) -> int:
    """HTMLPDF 侧栏列表顺序（第 N 项），导出时用于恢复栏位顺序而非坐标/f 号排序。"""
    if not isinstance(field, dict):
        return 999999
    try:
        return int(field.get("__editorOrder"))
    except (TypeError, ValueError):
        pass
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    try:
        return int(src.get("editorOrder"))
    except (TypeError, ValueError):
        return 999999


def _sort_field_list_by_editor_order(fields: List[Any]) -> List[Any]:
    indexed = [(i, f) for i, f in enumerate(fields or []) if isinstance(f, dict)]
    indexed.sort(key=lambda it: (_field_editor_list_order(it[1]), it[0]))
    return [f for _, f in indexed]


def _sort_fields_by_editor_list_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    """导出收尾：各 section 内按编辑器侧栏列表顺序排列（__editorOrder），不按坐标或 f 号升序。"""
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    def _walk_sort(field_list: List[Any]) -> None:
        if not isinstance(field_list, list):
            return
        field_list[:] = _sort_field_list_by_editor_order(field_list)
        for f in field_list:
            if not isinstance(f, dict):
                continue
            nested = f.get("fields")
            if isinstance(nested, list):
                _walk_sort(nested)

    for step in steps:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            flds = sec.get("fields")
            if isinstance(flds, list):
                _walk_sort(flds)
            matrix = sec.get("matrix")
            if isinstance(matrix, dict):
                hf = matrix.get("headerFields")
                if isinstance(hf, list):
                    matrix["headerFields"] = _sort_field_list_by_editor_order(hf)
    return payload


def _field_order_pyx(field: Dict[str, Any]) -> Tuple[int, float, float]:
    """字段在 PDF 上的阅读序键：页码、y（上→下）、x（左→右）。"""
    o = field.get("__order") if isinstance(field.get("__order"), tuple) else (999, 999999.0, 999999.0, 999999)
    try:
        return (int(o[0]), float(o[1]), float(o[2]))
    except (TypeError, ValueError, IndexError):
        return (999, 999999.0, 999999.0)


def _section_is_matrix_like(section: Dict[str, Any]) -> bool:
    if not isinstance(section, dict):
        return False
    if str(section.get("layout") or "").lower() == "matrixtable":
        return True
    return isinstance(section.get("matrix"), dict)


def _section_min_pyx_for_sort(section: Dict[str, Any]) -> Tuple[int, float, float]:
    """用于区块/初始组装的阅读序锚点：form 取 fields；matrix 取 headerFields。"""
    if not isinstance(section, dict):
        return (999, 999999.0, 999999.0)
    if _section_is_matrix_like(section):
        m = section.get("matrix") if isinstance(section.get("matrix"), dict) else {}
        hf = m.get("headerFields") if isinstance(m.get("headerFields"), list) else []
        pyx_m = [_field_order_pyx(f) for f in hf if isinstance(f, dict)]
        if pyx_m:
            return min(pyx_m)
    flds = section.get("fields") if isinstance(section.get("fields"), list) else []
    best = (999, 999999.0, 999999.0)
    for f in flds:
        if isinstance(f, dict):
            best = min(best, _field_order_pyx(f))
    return best


def _sort_fields_by_coordinate_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    """按 PDF 坐标调整 **section / step** 顺序，便于规则引擎合并区块。

    不重排 ``section.fields``：栏位顺序与 ``pdfFieldId`` 以 HTMLPDF 编辑器侧栏为准，
    由 ``_sort_fields_by_editor_list_order`` 在 finalize 末尾恢复。
    """

    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    step_min_order: Dict[int, Tuple[int, float, float]] = {}
    for step_idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        sections = step.get("sections")
        if not isinstance(sections, list):
            continue
        section_min_order: Dict[int, Tuple[int, float, float]] = {}
        for sec_idx, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            if _section_is_matrix_like(section):
                section_min_order[sec_idx] = _section_min_pyx_for_sort(section)
                continue
            fields = section.get("fields")
            if not isinstance(fields, list):
                continue
            pyx_list = [_field_order_pyx(f) for f in fields if isinstance(f, dict)]
            if pyx_list:
                section_min_order[sec_idx] = min(pyx_list)

        # section 顺序：按本节内所有字段的最早 (页,y,x)，贴近 PDF 从上到下的区块顺序
        if section_min_order:
            step_id_cur = str(steps[step_idx].get("id") or "")

            def _sec_sort_key(it: Tuple[int, Any]) -> Tuple[int, int, float, float, int]:
                idx, sec = it
                base = section_min_order.get(idx, (999, 999999.0, 999999.0))
                rk = _section_qc_semantic_rank(sec) if step_id_cur == "step_qc_items" else 0
                return (rk, base[0], base[1], base[2], idx)

            steps[step_idx]["sections"] = [
                sec for _, sec in sorted(enumerate(steps[step_idx].get("sections", [])), key=_sec_sort_key)
            ]
            # step 顺序：本步内全部字段的全局最早坐标（不再只用「第一节首字段」）
            step_pyx: List[Tuple[int, float, float]] = []
            for sec in steps[step_idx].get("sections", []):
                if not isinstance(sec, dict):
                    continue
                if _section_is_matrix_like(sec):
                    m = sec.get("matrix") if isinstance(sec.get("matrix"), dict) else {}
                    hf = m.get("headerFields") if isinstance(m.get("headerFields"), list) else []
                    for f in hf:
                        if isinstance(f, dict):
                            step_pyx.append(_field_order_pyx(f))
                    continue
                for f in sec.get("fields") or []:
                    if isinstance(f, dict):
                        step_pyx.append(_field_order_pyx(f))
            if step_pyx:
                step_min_order[step_idx] = min(step_pyx)

    # step 顺序按本步内最早字段坐标排序（签字步骤仍由后续 _force_signature_step_last 兜底放末尾）
    if step_min_order:
        payload["steps"] = [
            st
            for _, st in sorted(
                enumerate(steps),
                key=lambda it: step_min_order.get(it[0], (999, 999999, 999999.0)),
            )
        ]
    return payload


_MUTEX_TABLE_PAIR_KINDS = frozenset({"dose_rate_unit", "自动/手动", "是/否", "有/无"})


def _is_mutex_table_check_field(f: Dict[str, Any]) -> bool:
    if not isinstance(f, dict):
        return False
    src = f.get("source") if isinstance(f.get("source"), dict) else {}
    if str(src.get("anchorType") or "").lower() != "check":
        return False
    cp = src.get("checkboxPair")
    if not isinstance(cp, dict):
        return False
    return str(cp.get("pairKind") or "") in _MUTEX_TABLE_PAIR_KINDS


def _is_row_content_anchor_field(f: Dict[str, Any]) -> bool:
    """版式行内用于判断主内容归属哪一节：数值/文本/日期（不含任意勾选/互斥勾选）。"""
    if not isinstance(f, dict):
        return False
    t = str(f.get("type") or "").lower()
    if t in {"table", "textarea", "signature", "computed", "verdict", "boolean"}:
        return False
    if _is_mutex_table_check_field(f):
        return False
    return t in {"number", "text", "date", "radio", "select"}


def _rehome_mutex_pair_widgets_by_visual_row(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将「单位 / 控制方式 / 是否」等表格互斥勾选挪到与其同一视觉行、且已有内容输入最多的 section，
    以便后续在同节内合并为 radio，并与 kV、mA 等同前缀层级对齐。
    """
    y_tolerance = 10.0
    skip_step_ids = frozenset({"step_basic_info", "step_instruments", "step_signature"})
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    def _ord(ff: Dict[str, Any]) -> Tuple[int, float, float]:
        o = ff.get("__order") if isinstance(ff.get("__order"), tuple) else (999, 999999.0, 999999.0)
        try:
            return (int(o[0]), float(o[1]), float(o[2]))
        except (TypeError, ValueError, IndexError):
            return (999, 999999.0, 999999.0)

    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("id") or "") in skip_step_ids:
            continue
        sections = step.get("sections")
        if not isinstance(sections, list) or len(sections) < 2:
            continue

        flat: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            fields = sec.get("fields")
            if not isinstance(fields, list):
                continue
            for f in fields:
                if isinstance(f, dict):
                    flat.append((sec, f))

        if len(flat) < 2:
            continue

        flat.sort(key=lambda t: _ord(t[1]))
        row_id = -1
        anchor_y: float | None = None
        indexed: List[Tuple[Dict[str, Any], Dict[str, Any], int]] = []
        for sec, f in flat:
            _p, y, _x = _ord(f)
            if anchor_y is None or abs(y - float(anchor_y)) > y_tolerance:
                row_id += 1
                anchor_y = y
            indexed.append((sec, f, row_id))

        row_groups: Dict[int, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
        for sec, f, rid in indexed:
            row_groups.setdefault(rid, []).append((sec, f))

        for _rid, members in row_groups.items():
            anchor_counts: Dict[str, int] = {}
            mutex_refs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
            for sec, f in members:
                sid = str(sec.get("id") or "")
                if _is_mutex_table_check_field(f):
                    mutex_refs.append((sec, f))
                elif _is_row_content_anchor_field(f):
                    anchor_counts[sid] = anchor_counts.get(sid, 0) + 1

            if not mutex_refs or not anchor_counts:
                continue

            best = max(anchor_counts.values())
            target_candidates = sorted([k for k, v in anchor_counts.items() if v == best])
            target_sid = target_candidates[0]
            target_sec = next(
                (s for s in sections if isinstance(s, dict) and str(s.get("id") or "") == target_sid),
                None,
            )
            if not isinstance(target_sec, dict):
                continue

            for sec, f in mutex_refs:
                if str(sec.get("id") or "") == target_sid:
                    continue
                fl = sec.get("fields")
                if not isinstance(fl, list):
                    continue
                try:
                    fl.remove(f)
                except ValueError:
                    continue
                tgt_fields = target_sec.get("fields")
                if not isinstance(tgt_fields, list):
                    tgt_fields = []
                    target_sec["fields"] = tgt_fields
                tgt_fields.append(f)

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
                    # 无有效 PDF 宽度时（编辑器导出常无 __bbox 或 w=0）：保留既有 width，勿用 1/n 均分把栏位压得过窄
                    if not has_real_w:
                        continue
                    total_w = sum(positive_widths)
                    if total_w <= 0:
                        continue
                    for idx, _y, _x, w in row:
                        f = fields[idx]
                        if not isinstance(f, dict):
                            continue
                        wv = float(w)
                        if wv <= 0:
                            continue
                        ratio = wv / total_w
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


def _upgrade_sv_h_sections_to_matrix_table(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 DSA 术者位剂量率区块从多个 form section 升级为单个 matrixTable：
    - sec_sv_h 作为 matrix.headerFields
    - sec_sv_h_60cm_20cm ... 作为 matrix.rows
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    row_id_re = re.compile(r"^sec_sv_h_(\d+cm)_(\d+cm)$")

    for step in steps:
        if not isinstance(step, dict):
            continue
        sections = step.get("sections")
        if not isinstance(sections, list) or not sections:
            continue

        header_sec = None
        row_secs: List[Dict[str, Any]] = []
        keep_secs: List[Dict[str, Any]] = []
        for sec in sections:
            if not isinstance(sec, dict):
                keep_secs.append(sec)
                continue
            sid = str(sec.get("id") or "")
            if sid == "sec_sv_h":
                header_sec = sec
                continue
            if row_id_re.match(sid):
                row_secs.append(sec)
                continue
            keep_secs.append(sec)

        if not row_secs:
            continue

        def _first_order(sec_obj: Dict[str, Any]) -> Tuple[int, float, float]:
            fields = sec_obj.get("fields") if isinstance(sec_obj.get("fields"), list) else []
            orders = [f.get("__order") for f in fields if isinstance(f, dict) and isinstance(f.get("__order"), tuple)]
            if not orders:
                return (999, 999999.0, 999999.0)
            p, y, x, *_ = orders[0]
            return (int(p), float(y), float(x))

        row_secs.sort(key=_first_order)

        # --- headerFields ---
        header_fields: List[Dict[str, Any]] = []
        header_src_fields = header_sec.get("fields") if isinstance(header_sec, dict) and isinstance(header_sec.get("fields"), list) else []
        header_key_map = {
            "kv": ("kv", "kV"),
            "ma": ("ma", "mA"),
            "s": ("exposureTime", "s"),
            "校准因子": ("calibrationFactorCf", ""),
            "cf": ("calibrationFactorCf", ""),
        }
        for src in header_src_fields:
            if not isinstance(src, dict):
                continue
            item = copy.deepcopy(src)
            label = str(item.get("label") or "").strip()
            low = label.lower()
            semantic_key = ""
            unit = ""
            for token, mapped in header_key_map.items():
                if token in low or token in label:
                    semantic_key, unit = mapped
                    break
            if not semantic_key:
                semantic_key = _camel_case(_to_english_id(label, str(item.get("id") or ""), "value"), "value")
            item["id"] = f"sv_h_{semantic_key}"
            if unit:
                item["unit"] = unit
            src_obj = item.get("source") if isinstance(item.get("source"), dict) else {}
            legacy = str(src_obj.get("submitPath") or "")
            src_obj["submitPath"] = f"testResult.svH.header.{semantic_key}"
            if legacy:
                src_obj["legacySubmitPath"] = legacy
            src_obj["key"] = f"{step.get('id')}.sec_sv_h_matrix.header.{semantic_key}"
            item["source"] = src_obj
            header_fields.append(item)

        # --- rows/cells ---
        matrix_rows: List[Dict[str, Any]] = []
        serial_by_position: Dict[str, int] = {}
        serial_seq = 1
        row_seq = 0

        for sec in row_secs:
            sid = str(sec.get("id") or "")
            m = row_id_re.match(sid)
            if not m:
                continue
            dist = m.group(1)        # 60cm / 120cm
            height = m.group(2)      # 20cm / 80cm / ...

            sec_title = str(sec.get("title") or "").strip()
            title_parts = [p.strip() for p in sec_title.split("/") if p.strip()]
            operator_position = title_parts[0] if title_parts else f"术者位{dist}"
            height_type = title_parts[1] if len(title_parts) >= 2 else "距离地板高度"
            point = title_parts[-1] if title_parts else f"{height}"

            if operator_position not in serial_by_position:
                serial_by_position[operator_position] = serial_seq
                serial_seq += 1
            serial_no = str(serial_by_position[operator_position])

            measured_src = None
            report_src = None
            for f in (sec.get("fields") if isinstance(sec.get("fields"), list) else []):
                if not isinstance(f, dict):
                    continue
                label = str(f.get("label") or "")
                if "报出值" in label:
                    report_src = f
                elif "检测值" in label:
                    measured_src = f

            def _build_cell(src_field: Dict[str, Any] | None, cell_key: str) -> Dict[str, Any]:
                item = copy.deepcopy(src_field) if isinstance(src_field, dict) else {
                    "type": "number",
                    "label": "检测值" if cell_key == "measuredValue" else "报出值",
                    "required": False,
                    "defaultValue": None,
                    "precision": 1,
                    "source": {},
                }
                # cells 必须保持完整 field schema，便于前端复用动态字段渲染器
                item["type"] = str(item.get("type") or "number")
                item["label"] = str(item.get("label") or ("检测值" if cell_key == "measuredValue" else "报出值"))
                item["required"] = bool(item.get("required", False))
                item["defaultValue"] = item.get("defaultValue", None)
                item["precision"] = int(item.get("precision") or 1)
                item["unit"] = str(item.get("unit") or "μSv/h")
                item["id"] = f"sv_h_row_{row_seq}_{'measured_value' if cell_key == 'measuredValue' else 'report_value'}"
                src_obj = item.get("source") if isinstance(item.get("source"), dict) else {}
                legacy = str(src_obj.get("submitPath") or "")
                src_obj["submitPath"] = f"testResult.svH.rows[{row_seq}].{cell_key}"
                if legacy:
                    src_obj["legacySubmitPath"] = legacy
                src_obj["key"] = f"{step.get('id')}.sec_sv_h_matrix.rows[{row_seq}].{cell_key}"
                item["source"] = src_obj
                return item

            row_id = f"sv_h_{dist}_{height}_{row_seq}"
            matrix_rows.append(
                {
                    "id": row_id,
                    "headers": {
                        "serialNo": serial_no,
                        "inspectionItem": "透视防护区检测平面上周围剂量当量率(μSv/h)",
                        "operatorPosition": operator_position,
                        "heightType": height_type,
                        "point": point,
                    },
                    "cells": {
                        "measuredValue": _build_cell(measured_src, "measuredValue"),
                        "reportValue": _build_cell(report_src, "reportValue"),
                    },
                }
            )
            row_seq += 1

        matrix_section = {
            "id": "sec_sv_h_matrix",
            "title": "透视防护区检测平面上周围剂量当量率(μSv/h)",
            "layout": "matrixTable",
            "matrix": {
                "headerFields": header_fields,
                "rowHeaderColumns": [
                    {"id": "serialNo", "title": "序号", "merge": "auto", "width": 0.08},
                    {"id": "inspectionItem", "title": "检测项目", "merge": "auto", "width": 0.18},
                    {"id": "operatorPosition", "title": "检测位置", "merge": "auto", "width": 0.24},
                    {"id": "heightType", "title": "距离地板高度", "merge": "auto", "width": 0.12},
                    {"id": "point", "title": "检测点位", "merge": "none", "width": 0.16},
                ],
                "valueColumns": [
                    {"id": "measuredValue", "title": "检测值", "fieldType": "number", "unit": "μSv/h", "width": 0.14},
                    {"id": "reportValue", "title": "报出值", "fieldType": "number", "unit": "μSv/h", "width": 0.14},
                ],
                "rows": matrix_rows,
            },
        }

        # 在原 sec_sv_h 位置插入 matrix section，保持阅读顺序稳定
        if header_sec and isinstance(header_sec, dict):
            insert_idx = next((i for i, s in enumerate(sections) if isinstance(s, dict) and s.get("id") == "sec_sv_h"), len(keep_secs))
            keep_secs.insert(min(insert_idx, len(keep_secs)), matrix_section)
        else:
            keep_secs.append(matrix_section)
        step["sections"] = keep_secs

    return payload


def _dose_rate_unit_radio_id_from_pair_id(pair_id: str) -> str:
    m = re.match(r"^mutex_t(\d+)_r(\d+)_c(\d+)_rateUnit$", str(pair_id or "").strip())
    if m:
        return _camel_case(f"dose_rate_unit_t{m.group(1)}_r{m.group(2)}_c{m.group(3)}", "doseRateUnitCell")
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", str(pair_id or "").strip()).strip("_")
    return _camel_case(slug or "doseRateUnitCell", "doseRateUnitCell")


def _radio_label_without_trailing_unit(text: str, options: List[str]) -> str:
    s = str(text or "").strip()
    if not s:
        return "单位"
    opts = [str(x).strip() for x in options if str(x).strip()]
    opts.sort(key=len, reverse=True)
    for u in opts:
        if s.endswith(u):
            head = s[: -len(u)].rstrip("_").rstrip("-").strip()
            return head if head else "单位"
        suf = "_" + u
        if s.endswith(suf):
            head = s[: -len(suf)].strip()
            return head if head else "单位"
    return s


def _dose_rate_unit_slug_from_checkbox_option(opt: str, field_label: str = "") -> str:
    """将 checkboxPair.option 或字段 label 规范为 doseRateUnit 枚举 value slug；无法识别则空串。"""
    raw = str(opt or "").strip()
    if not raw:
        raw = str(field_label or "").strip()
    if not raw:
        return ""
    s = raw.strip().lower().replace("／", "/").replace(" ", "").replace("\u00b5", "μ")
    # 常见 OCR：拉丁 micro 与希腊 mu 混用
    if "ugy/" in s and "μ" not in s:
        s = s.replace("ugy/", "μgy/")
    aliases = {
        "μgy/s": "uGyPerSec",
        "μgy/min": "uGyPerMin",
        "mgy/min": "mGyPerMin",
    }
    return aliases.get(s, "")


def _field_order_tuple(f: Dict[str, Any]) -> Tuple[int, float, float, int]:
    o = f.get("__order") if isinstance(f.get("__order"), tuple) else (999, 999999.0, 999999.0, 999999)
    try:
        return (int(o[0]), float(o[1]), float(o[2]), int(o[3]) if len(o) > 3 else 999999)
    except (TypeError, ValueError, IndexError):
        return (999, 999999.0, 999999.0, 999999)


def _merge_dose_rate_unit_checkbox_pairs_to_radio(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将带 source.checkboxPair（pairKind=dose_rate_unit、同 pairId）的布尔勾选合并为单选。
    在整步（step）内按 pairId 聚合：避免互斥单位勾被拆到不同 section 后无法成组、残留单独 mGy/min。
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    for step in steps:
        if not isinstance(step, dict):
            continue
        sections = step.get("sections")
        if not isinstance(sections, list) or not sections:
            continue

        groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
        for section in sections:
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list):
                continue
            for f in fields:
                if not isinstance(f, dict):
                    continue
                if str(f.get("type") or "").lower() != "boolean":
                    continue
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                if str(src.get("anchorType") or "").lower() != "check":
                    continue
                cp = src.get("checkboxPair")
                if not isinstance(cp, dict):
                    continue
                if str(cp.get("pairKind") or "") != "dose_rate_unit":
                    continue
                if str(cp.get("mode") or "") != "mutually_exclusive":
                    continue
                pid = str(cp.get("pairId") or "").strip()
                if not pid:
                    continue
                groups.setdefault(pid, []).append((section, f))

        for pid, members in groups.items():
            if len(members) < 2:
                continue

            members.sort(key=lambda t: _field_order_tuple(t[1]))
            first_sec, first = members[0]
            src0 = first.get("source") if isinstance(first.get("source"), dict) else {}

            label_to_slug: Dict[str, str] = {}
            for _sec, ff in members:
                sc = ff.get("source") if isinstance(ff.get("source"), dict) else {}
                cp2 = sc.get("checkboxPair")
                if not isinstance(cp2, dict):
                    continue
                opt_raw = str(cp2.get("option") or "").strip()
                lb = str(ff.get("label") or "").strip()
                slug = _dose_rate_unit_slug_from_checkbox_option(opt_raw, lb)
                if not slug:
                    continue
                display_key = opt_raw or lb
                if display_key:
                    label_to_slug[display_key] = slug

            if len(set(label_to_slug.values())) < 2:
                continue

            radio_id = _dose_rate_unit_radio_id_from_pair_id(pid)
            fl = str(first.get("label") or first.get("id") or "")
            exp0 = src0.get("checkboxPair")
            exp_list: List[str] = []
            if isinstance(exp0, dict):
                raw_exp = exp0.get("expected")
                if isinstance(raw_exp, list):
                    exp_list = [str(x).strip() for x in raw_exp if str(x).strip()]
            radio_label = _radio_label_without_trailing_unit(fl, list(label_to_slug.keys()) or exp_list)

            picked_default = ""
            for _sec, ff in members:
                if not bool(ff.get("defaultValue")):
                    continue
                sc = ff.get("source") if isinstance(ff.get("source"), dict) else {}
                cp2 = sc.get("checkboxPair")
                if not isinstance(cp2, dict):
                    continue
                opt_raw = str(cp2.get("option") or "").strip()
                lb = str(ff.get("label") or "").strip()
                slug = _dose_rate_unit_slug_from_checkbox_option(opt_raw, lb)
                if slug:
                    picked_default = slug
                    break
            if not picked_default:
                for disp, sl in label_to_slug.items():
                    if "mgy" in disp.lower() and "min" in disp.lower():
                        picked_default = sl
                        break
            if not picked_default:
                picked_default = next(iter(label_to_slug.values()), "")

            current_bucket = str(src0.get("submitBucket") or "").strip() or "testResult"
            submit_path = _replace_submit_path_leaf(
                str(src0.get("submitPath") or f"{current_bucket}.{radio_id}"),
                radio_id,
            )
            try:
                page = int(src0.get("page") or 1)
            except (TypeError, ValueError):
                page = 1
            pdf_field_id = str(src0.get("pdfFieldId") or "")

            target_sec = first_sec
            sid_t = str(target_sec.get("id") or "")
            radio_field: Dict[str, Any] = {
                "id": radio_id,
                "type": "radio",
                "label": radio_label,
                "enumRef": "doseRateUnit",
                "required": bool(first.get("required", False)),
                "defaultValue": picked_default,
                "width": "half",
                "source": {
                    "pdfFieldId": pdf_field_id,
                    "page": page,
                    "anchorType": "check",
                    "submitBucket": current_bucket,
                    "submitPath": submit_path,
                    "key": f"{step.get('id')}.{sid_t}.{radio_id}",
                    "doseRateUnitPairId": pid,
                },
                "__order": first.get("__order", (999, 999999, 999999, 999999)),
                "__bbox": first.get("__bbox", (0.0, 0.0, 0.0, 0.0)),
            }

            member_set = {id(ff) for _s, ff in members}
            for section in sections:
                if not isinstance(section, dict):
                    continue
                flist = section.get("fields")
                if not isinstance(flist, list):
                    continue
                section["fields"] = [x for x in flist if not (isinstance(x, dict) and id(x) in member_set)]

            tgt_fields = target_sec.get("fields")
            if not isinstance(tgt_fields, list):
                tgt_fields = []
                target_sec["fields"] = tgt_fields
            ro = _field_order_tuple(radio_field)
            ins_pos = len(tgt_fields)
            for i, tf in enumerate(tgt_fields):
                if not isinstance(tf, dict):
                    continue
                if _field_order_tuple(tf) > ro:
                    ins_pos = i
                    break
            tgt_fields.insert(ins_pos, radio_field)

    return payload


def _collapse_mutually_exclusive_checks_to_radio(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将同一层级同一行内的互斥勾选项合并为 radio：
    - 检测类型：状态检测/验收检测 -> enumRef=testType
    - 控制方式：自动控制/手动控制 -> enumRef=controlMode
    - 单位：μGy/s、μGy/min、mGy/min -> enumRef=doseRateUnit
    - 是/否/有/无 -> enumRef=yesNo

    前端已不再支持互斥 radio，保留各 boolean 勾选独立提交。
    """
    return payload

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
                cp_skip = src.get("checkboxPair")
                if isinstance(cp_skip, dict) and str(cp_skip.get("pairKind") or "") == "dose_rate_unit":
                    continue
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
                    if lb in {"CT", "DR", "DSA", "乳腺机", "C臂机", "CR", "胃肠机", "透视"}:
                        return "deviceType"
                    if lb in {"同受检单位", "委托单位"}:
                        return "commissionOrgMode"
                    if lb in {"自动控制", "手动控制"}:
                        return "controlMode"
                    if lb in {"是", "否", "有", "无"}:
                        return "yesNo"
                    if lb in _KAP_AREA_UNIT_LABELS:
                        return "kapAreaProductUnit"
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
                    elif labels_set and all(
                        lb in {"CT", "DR", "DSA", "乳腺机", "C臂机", "CR", "胃肠机", "透视"} for lb in labels_set
                    ):
                        enum_ref = "deviceType"
                        radio_id = "deviceType"
                        radio_label = "设备类型"
                        value_by_label = {
                            "CT": "ct",
                            "DR": "dr",
                            "DSA": "dsa",
                            "乳腺机": "mammography",
                            "C臂机": "cArm",
                            "CR": "dr",
                            "胃肠机": "dr",
                            "透视": "dr",
                        }
                        default_value = "ct"
                        submit_bucket = "equipmentInfo"
                        submit_path_leaf = "deviceType"
                    elif {"自动控制", "手动控制"} <= labels_set:
                        enum_ref = "controlMode"
                        radio_label = "控制方式"
                        value_by_label = {"自动控制": "auto", "手动控制": "manual"}
                        default_value = "auto"
                        # 关键：控制方式必须按组隔离，避免不同检测项目共享同一状态键/路径。
                        # 若 group_name 退化为 controlMode/family，则追加行锚点避免串联。
                        if group_name and group_name not in {"controlMode", "yesNo", "doseRateUnit", "testType"}:
                            radio_id = _camel_case(f"{group_name}_controlMode", "controlMode")
                        else:
                            radio_id = _camel_case(f"controlMode_{row_key[0]}_{row_key[1]}", "controlMode")
                        submit_path_leaf = radio_id
                    elif labels_set and all((lb in {"是", "否", "有", "无"}) for lb in labels_set):
                        enum_ref = "yesNo"
                        value_by_label = {lb: yes_no_map.get(lb, "no") for lb in labels_set}
                        default_value = "no"
                        sec_title = str(section.get("title") or "").strip()
                        # 避免多组 是/否 共用 testResult.yesNo；PDF 勾选项常无下划线 id，需结合所在 section 标题识别 DSA 门槛
                        if ("以下仅为DSA" in sec_title or ("DSA" in sec_title and "设备检测项目" in sec_title)) and group_key.startswith(
                            "fam::"
                        ):
                            radio_id = "dsaEquipmentSectionApplicable"
                            radio_label = "以下仅为DSA设备检测项目"
                        elif group_key.startswith("pfx::"):
                            stem = str(group_name or "").strip()
                            if "以下仅为DSA" in stem or ("DSA" in stem and "设备检测项目" in stem):
                                radio_id = "dsaEquipmentSectionApplicable"
                                radio_label = "以下仅为DSA设备检测项目"
                            else:
                                radio_id = _camel_case(f"yesno_{_underscore_stable_slug(stem)}", "yesnoBranch")
                                radio_label = "选项"
                        else:
                            radio_id = _camel_case(f"yesno_p{row_key[0]}_r{row_key[1]}", "yesnoRow")
                            radio_label = "选项"
                        submit_path_leaf = radio_id
                    elif labels_set and labels_set <= _KAP_AREA_UNIT_LABELS and len(labels_set) >= 2:
                        enum_ref = "kapAreaProductUnit"
                        radio_label = "KAP 结果单位"
                        value_by_label = {lb: _KAP_AREA_LABEL_TO_VALUE[lb] for lb in labels_set if lb in _KAP_AREA_LABEL_TO_VALUE}
                        default_value = "mGy_m2"
                        radio_id = "kapAreaProductUnit"
                        submit_path_leaf = "kapAreaProductUnit"
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
                    # 检测类型/委托单位模式：原勾选框常在 testResult 桶，若仅替换 leaf 会得到错误的 testResult.testType
                    if enum_ref == "testType":
                        submit_path = "hospitalInfo.testType"
                        submit_bucket = "hospitalInfo"
                    elif enum_ref == "commissionOrgMode":
                        submit_path = "hospitalInfo.commissionOrgMode"
                        submit_bucket = "hospitalInfo"
                    elif enum_ref == "deviceType":
                        submit_path = "equipmentInfo.deviceType"
                        submit_bucket = "equipmentInfo"
                    else:
                        submit_path = _replace_submit_path_leaf(
                            str(src0.get("submitPath") or f"{submit_bucket}.{radio_id}"), submit_path_leaf or radio_id
                        )
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
                            "key": f"{step.get('id')}.{section.get('id')}.row{row_key[0]}_{row_key[1]}.{radio_id}",
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

    前端已不再使用 radio，跳过 boolean 去重。
    """
    return payload

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
                    or label in _KAP_AREA_UNIT_LABELS
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
                # 报告编号/委托编号/受检编号/检测类型仍留在报告信息（与委托编号同区展示）
                if str(f.get("id") or "") in {"reportNo", "commissionNo", "inspectionNo", "testType"}:
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
    # 分章节仪器格：质控=1，防护=2（与 registrySlot / instrumentBindings 一致）
    scope = str(field.get("instrumentScope") or "").strip()
    if scope == "qualityControl":
        return 1
    if scope == "radiationProtection":
        return 2
    try:
        slot = int(field.get("registrySlot") or 0)
        if slot > 0:
            return slot
    except (TypeError, ValueError):
        pass
    sp = str(field.get("submitPath") or "")
    if "instruments.qualityControl" in sp:
        return 1
    if "instruments.radiationProtection" in sp:
        return 2
    # 编号提取优先级：label > pdfFieldId > id
    label = str(field.get("label") or "")
    src = field.get("source") if isinstance(field.get("source"), dict) else {}
    pdf_id = str(src.get("pdfFieldId") or "")
    fid = str(field.get("id") or "")
    scope_from_text = _infer_instrument_scope_from_text(label, fid, sp)
    if scope_from_text == "qualityControl":
        return 1
    if scope_from_text == "radiationProtection":
        return 2
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
    - 每台仪器封装为一个 type=group 的 slot（id=instrumentSlot{n}），子字段顺序 boolean -> text -> date，
      子字段同一行展示、整条仪器信息不跨行（由 slot 上 slotNoWrap + section.instrumentSlotsWrap 提示前端）。
    - section.instrumentSlotsWrap=true：多个 slot 横向流式换行，尽量填满一行后再折到下一行。
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
            scoped_instrument_selects: List[Dict[str, Any]] = []
            grouped: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
            for f in fields:
                if not isinstance(f, dict):
                    continue
                if str(f.get("type") or "").lower() == "instrument_select":
                    scoped_instrument_selects.append(f)
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
                # slot 内子字段宽度（同一 Row 内分配，总和约 1）：
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
                inner_fields: List[Dict[str, Any]] = []
                for kind in ("boolean", "text", "date"):
                    candidates = row.get(kind) or []
                    if not candidates:
                        continue
                    candidates = sorted(candidates, key=lambda f: _name_score(f, idx, kind))
                    ff = candidates[0]
                    if not isinstance(ff, dict):
                        continue
                    if kind == "boolean":
                        ff["id"] = f"instrument{idx}Enabled"
                    elif kind == "text":
                        ff["id"] = f"testInstrument{idx}"
                    elif kind == "date":
                        ff["id"] = f"testDate{idx}"
                    ff["width"] = width_by_kind.get(kind, ff.get("width", "half"))
                    inner_fields.append(ff)
                if not inner_fields:
                    continue
                rebuilt.append(
                    {
                        "id": f"instrumentSlot{idx}",
                        "type": "group",
                        "label": "",
                        "layout": "row",
                        "width": "auto",
                        "instrumentSlot": True,
                        "instrumentSlotIndex": idx,
                        "slotNoWrap": True,
                        "fields": inner_fields,
                    }
                )
            section["fields"] = scoped_instrument_selects + rebuilt
            if rebuilt:
                section["instrumentSlotsWrap"] = True

    # 将误归到检测仪器区的非仪器字段回流到质控检测项目，避免干扰仪器行布局。
    # instrument_select（质控/防护）始终留在「仪器及检测人员」章，不进入 spillover。
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


def _relocate_signatures_to_signature_step(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    签字栏位只保留在 step_signature，避免与「仪器及检测人员」等章节重复展示。
    同角色多处 PDF 框已在 build 阶段合并为单个 signature 字段（pdfFieldIds）。
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    collected: Dict[str, Dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            kept: List[Dict[str, Any]] = []
            for field in section.get("fields", []):
                if not isinstance(field, dict):
                    kept.append(field)
                    continue
                if str(field.get("type") or "").lower() != "signature":
                    kept.append(field)
                    continue
                fid = str(field.get("id") or "").strip()
                if not fid:
                    kept.append(field)
                    continue
                if fid not in collected:
                    collected[fid] = copy.deepcopy(field)
            section["fields"] = kept
            matrix = section.get("matrix")
            if isinstance(matrix, dict):
                for bucket_key in ("headerFields",):
                    rows = matrix.get(bucket_key)
                    if isinstance(rows, list):
                        matrix[bucket_key] = [
                            f
                            for f in rows
                            if not (isinstance(f, dict) and str(f.get("type") or "").lower() == "signature")
                        ]
                for row in matrix.get("rows") or []:
                    if not isinstance(row, dict):
                        continue
                    cells = row.get("cells")
                    if isinstance(cells, dict):
                        row["cells"] = {
                            k: v
                            for k, v in cells.items()
                            if not (isinstance(v, dict) and str(v.get("type") or "").lower() == "signature")
                        }

    if not collected:
        return payload

    sig_fields = sorted(collected.values(), key=lambda f: str(f.get("id") or ""))
    sig_step = {
        "id": "step_signature",
        "title": "综合结论与签字",
        "sections": [
            {
                "id": "sec_signature",
                "title": "签字与结论",
                "layout": "form",
                "fields": sig_fields,
            }
        ],
    }
    out_steps: List[Dict[str, Any]] = [
        s for s in steps if isinstance(s, dict) and str(s.get("id") or "") != "step_signature"
    ]
    out_steps.append(sig_step)
    payload["steps"] = out_steps
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
        if sid == "step_signature":
            signature_steps.append(step)
        else:
            normal_steps.append(step)
    payload["steps"] = normal_steps + signature_steps
    return payload


def _inject_underscore_section_rules(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    对下划线分层形成的 section 注入基础可见性规则（控制项为 boolean，被控条件用 == true）：
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
                section.setdefault("visibleWhen", _VISIBLE_WHEN_KERMA_MAX_HIGH_SECTION)
                for field in section.get("fields", []):
                    if not isinstance(field, dict):
                        continue
                    if field.get("id") == "hasHighDoseMode":
                        continue
                    field.setdefault("visibleWhen", _VISIBLE_WHEN_KERMA_MAX_HIGH_FIELD)
    return payload


def _inject_visibility_conditional_rules(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    visibleWhen 仅加在「被联动展示」的字段上（见模块常量说明）。

    - 委托单位名称：选「同受检单位」时隐藏，选「委托单位」时显示（commissionOrgMode == 'customCommission' 等）。
    - 不在此注入 DSA 门槛以下的 visibleWhen：ExpressionEngine 与表单状态易不一致，导致部分输入框被误藏、回填看似异常；
      需要时再在模板或 Flutter 侧按实际状态配置。

    与 Flutter 对齐：boolean 用 == true/false；radio 枚举用带引号的 value。
    """
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    _apply_commission_organization_name_visibility(steps)

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

    # 中文规则：仅短标签或明确含日期语义，避免「……有效期至…年月日」等长句以「日」结尾被误合并
    label_t = str(label or "").strip()
    if label_t in ("年", "月", "日"):
        return ""
    date_hint = re.compile(r"(日期|年月|校准|有效|报告|检测|测试)")
    for text in (fid, key):
        t = str(text or "").strip()
        if not t or re.match(r"^f\d+$", t, re.I):
            continue
        if t.endswith("年") or t.endswith("月") or t.endswith("日"):
            if len(t) <= 28 and date_hint.search(t):
                return _clean_base(t[:-1])
    if label_t and (label_t.endswith("年") or label_t.endswith("月") or label_t.endswith("日")):
        if len(label_t) <= 28 and date_hint.search(label_t):
            return _clean_base(label_t[:-1])

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
            has_standalone_test_date = any(
                isinstance(f, dict) and str(f.get("id") or "").strip() == "testDate" for f in fields
            )
            sec_id = str(section.get("id") or "").strip()
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            passthrough: List[Dict[str, Any]] = []
            for f in fields:
                if not isinstance(f, dict):
                    continue
                src0 = f.get("source") if isinstance(f.get("source"), dict) else {}
                if str(src0.get("anchorType") or "").lower() == "check":
                    passthrough.append(f)
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
                if has_standalone_test_date and sec_id == "sec_report_info":
                    # 已有独立 testDate 时不再参与「年/月/日」合并，直接省略拆分栏位，避免与 testDate 重复。
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
                if sec_id == "sec_report_info":
                    base_label_seed = "检测日期"
                else:
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
                    # 分区标题常为「报告信息」，不可用作合并日期标签，否则前端出现第二条「报告信息」日期项。
                    st = str(section.get("title") or "").strip()
                    if sec_id == "sec_report_info" or st in {"报告信息", "基本信息"}:
                        base_label = "检测日期"
                    else:
                        base_label = str(step.get("title") or "检测日期").strip() or "检测日期"
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
    # 不在此强制插入「报告编号」：现场/原始记录类模板由编辑器导出 steps，报告单字段应在模板中显式配置。
    for f in fields:
        if not isinstance(f, dict):
            continue
        if f.get("id") == "commissionOrganization":
            f["type"] = "text"
            f.setdefault("placeholder", "输入委托单位名称")
            f["width"] = "half"
    report_sec["fields"] = fields
    return payload


def _site_record_template_section_catalog() -> List[Dict[str, Any]]:
    """现场记录 PDF 章节目录（与 htmlpdf.full_text_coordinate_boxing 定义一致）。"""
    try:
        from htmlpdf.full_text_coordinate_boxing import SITE_RECORD_TEMPLATE_SECTIONS

        specs = SITE_RECORD_TEMPLATE_SECTIONS
    except Exception:
        specs = []
    out: List[Dict[str, Any]] = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        key = str(spec.get("key") or "").strip()
        if not key:
            continue
        out.append(
            {
                "key": key,
                "title": str(spec.get("title") or key).strip(),
                "order": int(spec.get("order") or 0),
            }
        )
    out.sort(key=lambda s: (int(s.get("order") or 0), str(s.get("key") or "")))
    return out


def _template_section_title_for_key(section_key: str, catalog: List[Dict[str, Any]]) -> str:
    key = str(section_key or "").strip()
    if not key:
        return ""
    for row in catalog:
        if str(row.get("key") or "").strip() == key:
            return str(row.get("title") or key).strip()
    return key


def _section_ref_from_keys(section_key: str, section_title: str, catalog: List[Dict[str, Any]]) -> Dict[str, str]:
    key = str(section_key or "").strip()
    if not key:
        return {}
    title = str(section_title or "").strip() or _template_section_title_for_key(key, catalog)
    return {"key": key, "title": title}


def _section_dominant_chapter_key(section: Dict[str, Any]) -> str:
    sk = str(section.get("templateSectionKey") or section.get("sectionKey") or "").strip()
    if sk:
        return sk
    sec_ref = section.get("section")
    if isinstance(sec_ref, dict):
        sk = str(sec_ref.get("key") or "").strip()
        if sk:
            return sk
    votes: Dict[str, int] = {}
    for field in section.get("fields") or []:
        if not isinstance(field, dict):
            continue
        src = field.get("source") if isinstance(field.get("source"), dict) else {}
        fk = str(field.get("templateSectionKey") or src.get("templateSectionKey") or "").strip()
        if fk:
            votes[fk] = votes.get(fk, 0) + 1
    matrix = section.get("matrix")
    if isinstance(matrix, dict):
        for field in (matrix.get("headerFields") or []):
            if not isinstance(field, dict):
                continue
            src = field.get("source") if isinstance(field.get("source"), dict) else {}
            fk = str(field.get("templateSectionKey") or src.get("templateSectionKey") or "").strip()
            if fk:
                votes[fk] = votes.get(fk, 0) + 1
    if votes:
        return max(votes.items(), key=lambda kv: kv[1])[0]
    return ""


def _merge_sections_by_site_record_chapters(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    现场记录类模板：按六大 PDF 章节合并零碎 section，避免一表拆成几十个 section。
    保留：受检单位基本信息、受检设备基本信息、仪器及检测人员、质控项目、防护检测、平面布局图。
    """
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return payload
    catalog = _site_record_template_section_catalog()
    if not catalog:
        return payload
    chapter_order = [str(r["key"]) for r in catalog if r.get("key")]
    chapter_titles = {str(r["key"]): str(r["title"]) for r in catalog if r.get("key")}

    seen_keys: set = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            sk = _section_dominant_chapter_key(sec)
            if sk in chapter_order:
                seen_keys.add(sk)
    if len(seen_keys) < 2:
        return payload

    buckets: Dict[str, Dict[str, Any]] = {
        k: {"fields": [], "matrix": None, "layout": "form", "table": None} for k in chapter_order
    }
    signature_fields: List[Dict[str, Any]] = []
    misc_sections: List[Dict[str, Any]] = []

    def _append_fields(chapter: str, fields: List[Any]) -> None:
        for f in fields or []:
            if not isinstance(f, dict):
                continue
            if str(f.get("type") or "").lower() == "signature":
                signature_fields.append(copy.deepcopy(f))
                continue
            buckets[chapter]["fields"].append(copy.deepcopy(f))

    def _maybe_take_matrix(chapter: str, sec: Dict[str, Any]) -> None:
        matrix = sec.get("matrix")
        if not isinstance(matrix, dict):
            return
        cur = buckets[chapter]["matrix"]
        row_count = len(matrix.get("rows") or [])
        if cur is None or row_count >= len(cur.get("rows") or []):
            buckets[chapter]["matrix"] = copy.deepcopy(matrix)
            buckets[chapter]["layout"] = "matrix"
        for field in sec.get("fields") or []:
            if isinstance(field, dict) and str(field.get("type") or "").lower() == "table":
                if buckets[chapter]["table"] is None:
                    buckets[chapter]["table"] = copy.deepcopy(field)

    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = str(step.get("id") or "")
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            ch = _section_dominant_chapter_key(sec)
            if ch not in buckets:
                misc_sections.append(copy.deepcopy(sec))
                continue
            _append_fields(ch, sec.get("fields") if isinstance(sec.get("fields"), list) else [])
            _maybe_take_matrix(ch, sec)
            if str(sec.get("layout") or "").lower() == "table":
                buckets[ch]["layout"] = "table"

    merged_sections: List[Dict[str, Any]] = []
    for ch in chapter_order:
        bucket = buckets[ch]
        has_matrix = isinstance(bucket["matrix"], dict) and bool(bucket["matrix"].get("rows") or bucket["matrix"].get("headerFields"))
        has_fields = bool(bucket["fields"])
        has_table = isinstance(bucket["table"], dict)
        if not has_matrix and not has_fields and not has_table:
            continue
        sec_out: Dict[str, Any] = {
            "id": ch,
            "title": chapter_titles.get(ch) or ch,
            "sectionKey": ch,
            "layout": bucket["layout"] if has_matrix else ("table" if has_table else "form"),
        }
        if has_matrix:
            sec_out["matrix"] = bucket["matrix"]
            flat = [f for f in bucket["fields"] if str(f.get("type") or "").lower() != "table"]
            if flat:
                sec_out["fields"] = flat
        elif has_table and not has_fields:
            sec_out["fields"] = [bucket["table"]]
        else:
            sec_out["fields"] = bucket["fields"]
        merged_sections.append(sec_out)

    new_steps: List[Dict[str, Any]] = [
        {
            "id": "step_site_record",
            "title": "检测原始记录",
            "sections": merged_sections,
        }
    ]
    if misc_sections:
        new_steps[0]["sections"].extend(misc_sections)
    if signature_fields:
        by_id: Dict[str, Dict[str, Any]] = {}
        for f in signature_fields:
            fid = str(f.get("id") or "").strip()
            if fid:
                by_id[fid] = f
        if by_id:
            new_steps.append(
                {
                    "id": "step_signature",
                    "title": "综合结论与签字",
                    "sections": [
                        {
                            "id": "sec_signature",
                            "title": "签字与结论",
                            "layout": "form",
                            "fields": sorted(by_id.values(), key=lambda x: str(x.get("id") or "")),
                        }
                    ],
                }
            )
    payload["steps"] = new_steps
    return payload


_LAYOUT_DIAGRAM_SECTION_KEYS = frozenset(
    {
        "site_layout_diagram",
        "floor_plan",
        "sec_site_layout_diagram",
        "sec_floor_plan",
    }
)


def _section_is_layout_diagram(section: Dict[str, Any]) -> bool:
    ch = str(
        section.get("sectionKey")
        or section.get("templateSectionKey")
        or section.get("id")
        or ""
    ).strip()
    sec_title = str(section.get("title") or "")
    if ch in _LAYOUT_DIAGRAM_SECTION_KEYS:
        return True
    if sec_title in ("平面布局示意图", "平面布局图"):
        return True
    return str(section.get("sectionType") or "").strip() == "floorPlan"


def _layout_section_field_targets(section: Dict[str, Any]) -> List[Any]:
    targets = list(section.get("fields") or [])
    matrix = section.get("matrix")
    if isinstance(matrix, dict):
        targets.extend(matrix.get("headerFields") or [])
        for row in matrix.get("rows") or []:
            if isinstance(row, dict) and isinstance(row.get("cells"), dict):
                targets.extend(row["cells"].values())
    return targets


def _apply_floor_plan_field_types(payload: Dict[str, Any]) -> Dict[str, Any]:
    """平面布局示意图章：任意 image 栏 floorPlan（兜底误命名）；其余 number 改为 text。"""
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for section in step.get("sections") or []:
            if not isinstance(section, dict) or not _section_is_layout_diagram(section):
                continue
            for field in _layout_section_field_targets(section):
                if not isinstance(field, dict):
                    continue
                if _field_is_floor_plan_image(field, in_layout_section=True):
                    field["type"] = "floorPlan"
                    field["defaultValue"] = None
                    field.pop("enumRef", None)
                    continue
                if str(field.get("type") or "").lower() == "number":
                    field["type"] = "text"
                    field.pop("precision", None)
                    field.pop("unit", None)
    return payload


def _slim_frontend_schema_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    """兼容旧名：执行完整表单 JSON 压平。"""
    return _compact_form_schema_payload(payload)


def _inject_template_section_metadata(result: Dict[str, Any]) -> Dict[str, Any]:
    """统一 section.id/title（章节目录已在 steps.sections 中体现，不再写根级 sections 副本）。"""
    if not isinstance(result, dict):
        return result
    result.pop("templateSections", None)
    result.pop("sections", None)
    catalog = _site_record_template_section_catalog()
    title_by_key = {str(r["key"]): str(r["title"]) for r in catalog if r.get("key")}

    for step in result.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            sk = str(sec.get("sectionKey") or sec.get("templateSectionKey") or sec.get("id") or "").strip()
            if not sk:
                sk = _section_dominant_chapter_key(sec)
            if sk:
                sec["id"] = sk
                sec["title"] = title_by_key.get(sk) or str(sec.get("title") or sk).strip() or sk
            sec.pop("sectionKey", None)
            sec.pop("templateSectionKey", None)
            sec.pop("templateSectionTitle", None)
    return result


def _finalize_frontend_schema_result(result: Dict[str, Any], *, merge_split_dates: bool) -> Dict[str, Any]:
    """统一后处理链（从 PDF 规则拼装或从 formSchema.steps 直出后共用）。"""
    tid = str(result.get("templateId") or "")
    tnm = str(result.get("templateName") or "")
    result = _ensure_basic_defaults(result)
    if "js-117" in (tid.lower() + " " + tnm.lower()):
        result = _post_optimize_js117(result)
    result = _force_signature_semantics(result)
    if merge_split_dates:
        result = _merge_split_date_fields(result)
    # 以下坐标排序仅调整 section/step 区块顺序；栏位数组顺序不在此改动。
    result = _sort_fields_by_coordinate_order(result)
    result = _rehome_mutex_pair_widgets_by_visual_row(result)
    result = _sort_fields_by_coordinate_order(result)
    result = _merge_dose_rate_unit_checkbox_pairs_to_radio(result)
    result = _collapse_mutually_exclusive_checks_to_radio(result)
    result = _remove_boolean_duplicates_after_radio(result)
    result = _relocate_equipment_fields_to_basic_info(result)
    result = _normalize_basic_info_sections(result)
    result = _prioritize_commission_fields_in_basic_info(result)
    if merge_split_dates:
        result = _arrange_instruments_row_layout(result)
    result = _apply_width_by_coordinate_layout(result)
    result = _upgrade_sv_h_sections_to_matrix_table(result)
    result = _inject_underscore_section_rules(result)
    result = _consolidate_orphan_inspection_steps(result)
    result = _merge_sections_by_site_record_chapters(result)
    result = _sort_fields_by_coordinate_order(result)
    result = _relocate_signatures_to_signature_step(result)
    result = _force_signature_step_last(result)
    result = _inject_visibility_conditional_rules(result)
    result = _apply_floor_plan_field_types(result)
    result = _strip_pdf_coords_from_form_schema(result)
    result = _coerce_radio_select_defaults_to_string(result)
    result = _apply_radiation_protection_chapter_formulas(result)
    result = _apply_conditional_field_rules_globally(result)
    result = _inject_template_section_metadata(result)
    result = _sort_fields_by_editor_list_order(result)
    result = _compact_form_schema_payload(result)
    if isinstance(result, dict):
        result.setdefault("schema", "frontend_form_schema/v1")
    return result


def _apply_conditional_field_rules_globally(payload: Dict[str, Any]) -> Dict[str, Any]:
    """将栏位级条件公式/判定规则写入前端 steps。"""
    try:
        from utils.conditional_field_rules import enrich_frontend_field_with_conditional_rules
    except Exception:
        return payload
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    def _walk(field: Dict[str, Any]) -> None:
        if not isinstance(field, dict):
            return
        enrich_frontend_field_with_conditional_rules(field)
        for nested in field.get("fields") or []:
            _walk(nested)
        for cell in (field.get("cells") or {}).values():
            if isinstance(cell, dict):
                _walk(cell)

    for step in steps:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            for fld in sec.get("fields") or []:
                _walk(fld)
            matrix = sec.get("matrix")
            if isinstance(matrix, dict):
                for row in matrix.get("rows") or []:
                    if not isinstance(row, dict):
                        continue
                    for cell in (row.get("cells") or {}).values():
                        if isinstance(cell, dict):
                            _walk(cell)
    return payload


def _apply_radiation_protection_chapter_formulas(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    规则引擎阶段仅保留轻量均值兜底；完整章节公式（含 reportValueRules 编译、pdf.fields 绑定）
    在 ``finalize_runtime_frontend_export`` 中执行（此时 pdf.fields 可用且不会覆盖根级章节配置）。
    """
    if not isinstance(payload, dict):
        return payload
    if isinstance(payload.get("radiationProtectionChapter"), dict):
        return payload
    return _apply_radiation_protection_mean_formulas(payload)


def _apply_radiation_protection_mean_formulas(payload: Dict[str, Any]) -> Dict[str, Any]:
    """工作场所防护表：测量均值列默认 avg(同点位三次测量读数)。"""
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload
    for step in steps:
        if not isinstance(step, dict):
            continue
        for sec in step.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            title = str(sec.get("title") or "")
            if "工作场所" not in title and "放射防护" not in title:
                continue
            fields = sec.get("fields")
            if not isinstance(fields, list):
                continue
            by_point: Dict[str, list] = {}
            mean_fields: list = []
            def _field_table_sem(f: Dict[str, Any]) -> Dict[str, Any]:
                if isinstance(f.get("table"), dict):
                    return f["table"]
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                sem = src.get("autoSemantic") if isinstance(src.get("autoSemantic"), dict) else {}
                return sem if isinstance(sem, dict) else {}

            for f in fields:
                if not isinstance(f, dict):
                    continue
                sem = _field_table_sem(f)
                if sem.get("meanOfReadings") or f.get("mean"):
                    mean_fields.append(f)
                    continue
                if str(sem.get("radiationColumn") or "") == "测量读数M":
                    pt = str(sem.get("radiationPoint") or f.get("label") or "")
                    by_point.setdefault(pt, []).append(f)
            for mf in mean_fields:
                sem = _field_table_sem(mf)
                pt = str(sem.get("radiationPoint") or mf.get("label") or "")
                refs = sorted(
                    by_point.get(pt, []),
                    key=lambda rf: int(_field_table_sem(rf).get("readingIndex") or 99),
                )
                pids = [
                    str(
                        (rf.get("pdfFieldId") or (rf.get("source") or {}).get("pdfFieldId") or "")
                    ).strip()
                    for rf in refs
                    if str(
                        (rf.get("pdfFieldId") or (rf.get("source") or {}).get("pdfFieldId") or "")
                    ).strip()
                ]
                if (
                    len(pids) >= 2
                    and not str(mf.get("fieldExpression") or "").strip()
                    and not mf.get("fieldFormulaUserOverride")
                ):
                    mf["fieldExpression"] = f"avg({','.join(pids[:3])})"
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
    form_schema_top = template_obj.get("formSchema") if isinstance(template_obj.get("formSchema"), dict) else {}
    schema_extras = _schema_extras_from_template(template_obj, form_schema_top)
    constants = template_obj.get("constants") if isinstance(template_obj.get("constants"), dict) else {}
    if not constants and isinstance(form_schema_top.get("constants"), dict):
        constants = form_schema_top["constants"]
    enums = template_obj.get("enums") if isinstance(template_obj.get("enums"), dict) else {}
    if not enums and isinstance(form_schema_top.get("enums"), dict):
        enums = form_schema_top["enums"]

    id_hay = (template_id + " " + template_name).lower()
    editor_steps = form_schema_top.get("steps") if isinstance(form_schema_top.get("steps"), list) else None
    if "js-117" in id_hay and editor_steps:
        result = {
            "schema": "frontend_form_schema/v1",
            "templateId": template_id or "template_frontend",
            "templateName": template_name or "template_frontend.json",
            "version": version or "1.0.0",
            "reportType": report_type,
            "standard": standard,
            "pdfUrl": pdf_url,
            "pdfOverlay": True,
            "locale": locale,
            "constants": constants,
            "enums": enums,
            "steps": copy.deepcopy(editor_steps),
        }
        _apply_schema_extras(result, schema_extras)
        return merge_field_formulas_into_frontend(
            _finalize_frontend_schema_result(result, merge_split_dates=merge_split_dates),
            template_obj,
        )

    pdf = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
    fields_raw = pdf.get("fields") if isinstance(pdf.get("fields"), list) else []
    if not fields_raw:
        fields_raw = template_obj.get("fields") if isinstance(template_obj.get("fields"), list) else []
    # 编辑器传入的 pdf.fields 已按侧栏顺序编号，勿在此按数组下标再次整体重排 f 号
    fields_raw = materialize_unified_pdf_fields(fields_raw, reindex_pdf_field_ids=False)

    # 保留模板中的每一条栏位；同 section 内仅按 pdfFieldId 跳过重复槽位。label 可重名，导出 id=pdfFieldId。
    normalized_items: List[Dict[str, Any]] = []
    for idx, row in enumerate(fields_raw, start=1):
        if not isinstance(row, dict):
            continue
        normalized_items.append(_normalize_pdf_field(row, idx))

    step_order = ["step_basic_info", "step_instruments", "step_qc_items", "step_judgement", "step_signature"]
    step_title_map = {
        "step_basic_info": "基本信息",
        "step_instruments": "检测仪器",
        "step_qc_items": "质控检测项目",
        "step_judgement": "结果判定",
        "step_signature": "综合结论与签字",
    }
    section_bucket: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in step_order}
    found_test_type_checks: Dict[str, str] = {}
    instrument_check_labels: List[str] = []

    # 与 HTMLPDF 侧栏一致：保持 pdf.fields 传入顺序，不按 PDF 坐标重排后再编号
    sorted_items = sorted(normalized_items, key=lambda it: (it.get("listIndex") or 999999,))
    section_catalog = _site_record_template_section_catalog()
    rpc_layout_json = None
    for container in (schema_extras, form_schema_top, template_obj):
        if not isinstance(container, dict):
            continue
        rpc = container.get("radiationProtectionChapter")
        if isinstance(rpc, dict):
            rpc_layout_json = rpc.get("layout") if isinstance(rpc.get("layout"), dict) else rpc
            break
    try:
        from radiation_detection_report.chapter5_field_sync import infer_protection_table_column_layout

        protection_column_layout = infer_protection_table_column_layout(
            normalized_items,
            layout_json=rpc_layout_json,
        )
    except Exception:
        protection_column_layout = {}
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
        signature_role = _resolve_canonical_signature_role(
            item["label"],
            str(item.get("pdfFieldId") or ""),
            field_type=ft,
        )
        if signature_role:
            ft = "signature"
        pdf_section_key = str(item.get("templateSectionKey") or "").strip()
        if (
            pdf_section_key == "site_layout_diagram"
            and str(item.get("fieldType") or "").lower() == "image"
            and not signature_role
        ):
            ft = "floorPlan"
        # 规则1：新版 PDF 章节锚点（纵坐标区间）优先
        pdf_hierarchy = None
        if pdf_section_key:
            pdf_hierarchy = _classify_by_pdf_template_section(
                pdf_section_key,
                item,
                signature_role=signature_role,
                field_type=ft,
            )
        # 规则2：下划线自动分层（同前缀归同一层级）
        hierarchy_key = str(item.get("hierarchyKey") or "").strip()
        if not hierarchy_key:
            label_key = normalize_field_text_by_underscore_rules(str(item.get("label") or "").strip())
            raw_key = normalize_field_text_by_underscore_rules(str(item.get("rawId") or "").strip())
            hierarchy_key = _pick_hierarchy_underscore_key(label_key, raw_key)
        # 表格行列语义；签名字段在「仪器及检测人员」章节内仍走 pdf 分层，其它章节跳过下划线/auto 以免拆散
        skip_auto_for_sig = signature_role and pdf_section_key != "site_instruments_staff"
        auto_hierarchy = None if skip_auto_for_sig else _classify_by_auto_semantic(item)
        hierarchy = (
            pdf_hierarchy
            or auto_hierarchy
            or (None if signature_role and not pdf_section_key else _classify_by_underscore(hierarchy_key))
        )
        if hierarchy:
            if len(hierarchy) >= 5:
                step_id, step_title, sec_id, sec_title, layout = hierarchy
            else:
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
        if pdf_section_key:
            sec.setdefault("templateSectionKey", pdf_section_key)
            ts_title_for_sec = str(item.get("templateSectionTitle") or "").strip()
            if ts_title_for_sec:
                sec.setdefault("templateSectionTitle", ts_title_for_sec)
        fallback_id = f"field{idx}"
        current_pdf_id = str(item.get("pdfFieldId") or "").strip()
        if not re.match(r"^f\d+$", current_pdf_id):
            current_pdf_id = fallback_id
        semantic_id_prefix, semantic_type = infer_field_properties(item["label"])
        if signature_role:
            field_id, field_label = signature_role
        else:
            # 允许 label/占位命名重名：表单 id 与 PDF 回填键统一为 pdfFieldId，submitPath 仍由标签/层级推断。
            field_id = current_pdf_id
            field_label = item["label"]
            if current_pdf_id and any(
                isinstance(ff, dict)
                and str(
                    (ff.get("source") if isinstance(ff.get("source"), dict) else {}).get("pdfFieldId")
                    or ff.get("pdfFieldId")
                    or ""
                ).strip()
                == current_pdf_id
                for ff in sec.get("fields", [])
            ):
                continue
        if signature_role:
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
                    if _is_pdf_field_row_signature_image(
                        {
                            "id": item.get("label") or item.get("rawId"),
                            "fieldType": item.get("fieldType"),
                            "pdfFieldId": current_pdf_id,
                        }
                    ) or str(item.get("fieldType") or "").lower() in ("image", "signature"):
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
                "pdfFieldId": current_pdf_id,
                "page": item["page"],
                "anchorType": item["fieldType"],
            },
            "__order": item.get("order", (999, 999999, 999999, idx)),
            "__editorOrder": int(item.get("listIndex") or idx),
            "__bbox": (item.get("x", 0.0), item.get("y", 0.0), item.get("w", 0.0), item.get("h", 0.0)),
        }
        icp = item.get("checkboxPair")
        if isinstance(icp, dict) and icp:
            field_obj["source"]["checkboxPair"] = icp
        if pdf_section_key:
            field_obj["source"]["templateSectionKey"] = pdf_section_key
            ts_title = str(item.get("templateSectionTitle") or "").strip()
            if ts_title:
                field_obj["source"]["templateSectionTitle"] = ts_title
            field_obj["section"] = _section_ref_from_keys(pdf_section_key, ts_title, section_catalog)
        if hierarchy and str(hierarchy_key or "").strip():
            field_obj["source"]["hierarchyKey"] = str(hierarchy_key).strip()
        auto_semantic = item.get("autoSemantic")
        if isinstance(auto_semantic, dict) and auto_semantic:
            apply_table_row_col_to_source(field_obj["source"], auto_semantic=auto_semantic)
            item_name = _auto_semantic_clean_text(auto_semantic.get("itemName"))
            type_name = _auto_semantic_clean_text(auto_semantic.get("typeName"))
            if item_name or type_name:
                field_obj["source"]["hierarchyKey"] = "_".join(p for p in (item_name, type_name) if p)
            key_parts = []
            for key, prefix in (("tableId", "t"), ("row", "r"), ("col", "c"), ("slotNo", "s")):
                raw = auto_semantic.get(key)
                if raw is None or raw == "":
                    continue
                try:
                    key_parts.append(f"{prefix}{int(raw)}")
                except (TypeError, ValueError):
                    key_parts.append(f"{prefix}{_slug_ascii(str(raw), 'x')}")
            key_suffix = "." + "_".join(key_parts) if key_parts else ""
            field_obj["source"]["key"] = f"{step_id}.{sec_id}{key_suffix}.{field_id}"
        jct = str(item.get("judgmentCriterionText") or "").strip()
        if jct:
            field_obj["source"]["judgmentCriterionText"] = jct[:500]
        submit_binding = _assign_submit_bucket(field_obj)
        field_obj["source"]["submitBucket"] = submit_binding["bucket"]
        field_obj["source"]["submitPath"] = submit_binding["path"]
        _apply_scoped_instrument_select_field(field_obj)
        # 约束：归到 testResult 的输入统一按 number 渲染与提交。
        if submit_binding["bucket"] == "testResult":
            field_obj["type"] = "number"
            field_obj["defaultValue"] = None
            field_obj["precision"] = 1
        if field_obj.get("type") == "number":
            field_obj["precision"] = 1
            # 防护表单位在 matrix valueColumns 表头展示，单元格不写 unit。
            if pdf_section_key != "site_radiation_protection":
                unit = _pick_unit(field_label)
                if unit:
                    field_obj["unit"] = unit
        try:
            from radiation_detection_report.chapter5_field_sync import apply_protection_field_export_typing

            apply_protection_field_export_typing(
                field_obj,
                item,
                column_layout=protection_column_layout,
            )
        except Exception:
            pass
        if str(field_obj.get("source", {}).get("anchorType") or "").lower() == "check":
            field_obj["type"] = "boolean"
            field_obj["defaultValue"] = bool(field_obj.get("defaultValue", False))
            field_obj.pop("enumRef", None)
            field_obj.pop("precision", None)
            field_obj.pop("unit", None)
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

    # 归位：第一页“仪器1..n”转为检测仪器表格（旧版 PDF）；已有质控/防护 instrument_select 时不再生成，避免与两栏绑定重复。
    has_scoped_instrument_select = False
    for _sec in section_bucket.get("step_instruments", {}).values():
        if not isinstance(_sec, dict):
            continue
        for _fld in _sec.get("fields", []):
            if isinstance(_fld, dict) and str(_fld.get("type") or "").lower() == "instrument_select":
                has_scoped_instrument_select = True
                break
        if has_scoped_instrument_select:
            break
    if instrument_check_labels and not has_scoped_instrument_select:
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
        # section：质控步内先按 WS76 栏目语义序，再按 PDF 坐标细分；其余步仍以坐标为主
        sections_raw = list(sections_map.values())
        sections = [sec for _, sec in sorted(enumerate(sections_raw), key=lambda it: _section_order_key(sid, it[1], it[0]))]
        steps.append({"id": sid, "title": step_title_map.get(sid, sid), "sections": sections})

    if not steps:
        steps = [{"id": "step_basic_info", "title": "基本信息", "sections": [{"id": "sec_misc", "title": "杂项", "layout": "form", "fields": []}]}]

    result = {
        "schema": "frontend_form_schema/v1",
        "templateId": template_id or "template_frontend",
        "templateName": template_name or "template_frontend.json",
        "version": version or "1.0.0",
        "reportType": report_type,
        "standard": standard,
        "pdfUrl": pdf_url,
        "pdfOverlay": True,
        "locale": locale,
        "constants": constants,
        "enums": enums,
        "steps": steps,
    }
    _apply_schema_extras(result, schema_extras)
    return merge_field_formulas_into_frontend(
        _finalize_frontend_schema_result(result, merge_split_dates=merge_split_dates),
        template_obj,
    )

