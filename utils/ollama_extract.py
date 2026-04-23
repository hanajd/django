import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from ollama import Client
from outlines import Generator
from outlines.models import Ollama
from outlines.types import json_schema

from utils import pipeline_config
from utils.json_utils import validate_json

logger = logging.getLogger(__name__)


def _ollama_default_runner_options() -> dict:
    raw = os.environ.get("OLLAMA_OPTIONS", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("OLLAMA_OPTIONS 不是合法 JSON，已忽略: %s", raw)
            return {}
    return {"num_gpu": 999}


class OllamaClientGpuPreference(Client):
    @staticmethod
    def _merge_options(kwargs):
        kwargs = dict(kwargs)
        defaults = _ollama_default_runner_options()
        if not defaults:
            return kwargs
        opts = dict(defaults)
        extra = kwargs.get("options")
        if extra is not None:
            if hasattr(extra, "model_dump"):
                extra = extra.model_dump(exclude_none=True)
            opts.update(dict(extra))
        kwargs["options"] = opts
        return kwargs

    def chat(self, *args, **kwargs):
        return super().chat(*args, **self._merge_options(kwargs))

    def generate(self, *args, **kwargs):
        return super().generate(*args, **self._merge_options(kwargs))


def build_devices_prompt(md: str, source_file_hint: int) -> str:
    struct = json.dumps(pipeline_config.SINGLE_DEVICE_STRUCT, ensure_ascii=False, indent=2)
    return f"""你是设备铭牌提取专家，从文本中提取所有独立设备铭牌，输出**严格JSON数组**，无其他内容。文本中可能有部分拼写错误或者识别错误，请根据上下文进行简单修正，避免改动过大。
格式：
{struct}

文本：
{md}
"""


def _to_frontend_field_type(field_type: str) -> str:
    ft = (field_type or "").strip().lower()
    if ft == "check":
        return "boolean"
    if ft == "image":
        return "signature"
    return "text"


def build_frontend_template_prompt(
    template_obj: Dict[str, Any],
    source_file_hint: int,
    sample_template_obj: Dict[str, Any] | None = None,
) -> str:
    """Build prompt for converting htmlpdf/unified template to frontend-renderable JSON."""
    compact = json.dumps(template_obj, ensure_ascii=False, separators=(",", ":"))
    sample_compact = json.dumps(sample_template_obj or {}, ensure_ascii=False, separators=(",", ":"))
    return f"""# 角色
你是放射医疗设备质量控制检测表单JSON架构专家、Flutter动态表单Schema工程师，严格遵循统一动态表单规范 + 参考样板格式，标准化转换所有新旧放射检测记录JSON模板。

固定统一顶层JSON结构（一字不改，顺序固定）：
{{
  "templateId": "",
  "templateName": "",
  "version": "1.0.0",
  "reportType": "",
  "standard": "",
  "pdfUrl": "",
  "locale": "zh-CN",
  "constants": {{}},
  "enums": {{}},
  "steps": []
}}

强制规则：
1) 只输出纯JSON，不允许解释文本。
2) 必须删除冗余废弃字段：schema、meta、pdf、formSchema、bindings、独立fields数组、source_pdf。
3) 层级必须是 steps -> sections -> fields；layout 仅允许 form/table/grid。
4) 步骤ID格式：step_英文语义；区块ID格式：sec_英文语义。
5) 表单字段ID必须英文小驼峰，禁止中文ID；根据label语义命名。
6) 字段类型只允许：text/date/number/radio/boolean/select/group/computed/verdict/textarea/signature/table。
7) number必须包含precision；radio/select必须用enumRef关联enums。
8) 前端导出JSON不包含坐标信息（不输出rect/xywh/xyxy/page/x/y/w/h/pdfAnchor），仅在source中保留 key/pdfFieldId/page/anchorType 作为关联引用。
9) visibleWhen 使用简单表达式：== > < && || !
10) computed 必须包含 formula 与 dependsOn；verdict 必须包含 rule。
11) constants/enums 如无数据必须返回空对象；steps不能为空。
12) 输出必须可被Flutter动态表单直接读取渲染。

参考标准样板JSON：
{sample_compact}

待转换原始JSON（文件数提示: {source_file_hint}）：
{compact}
"""


def _slugify_ascii_id(text: str, fallback: str, max_len: int = 64) -> str:
    raw = (text or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw:
        raw = fallback
    if not re.match(r"^[a-z_]", raw):
        raw = f"id_{raw}"
    return raw[:max_len]


def _to_camel_case_id(text: str, fallback: str) -> str:
    base = _slugify_ascii_id(text, fallback)
    parts = [p for p in base.split("_") if p]
    if not parts:
        return fallback
    head = parts[0]
    tail = "".join(p[:1].upper() + p[1:] for p in parts[1:])
    camel = f"{head}{tail}"
    if not re.match(r"^[a-z][A-Za-z0-9]*$", camel):
        return fallback
    return camel


def _load_standard_sample_template() -> Dict[str, Any]:
    sample_path = Path(
        os.environ.get(
            "FRONTEND_TEMPLATE_SAMPLE_PATH",
            "/home/raydose/Projects/django/media/file_library/templates/365af054b1d14cf9938440cd8c62f1f8_template_frontend.json",
        )
    )
    try:
        if sample_path.is_file():
            return json.loads(sample_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("读取标准样板JSON失败: %s", exc)
    return {}


def _build_pdf_field_map(template_obj: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    pdf = template_obj.get("pdf") if isinstance(template_obj.get("pdf"), dict) else {}
    pdf_fields = pdf.get("fields") if isinstance(pdf.get("fields"), list) else []
    for row in pdf_fields:
        if not isinstance(row, dict):
            continue
        candidates = [
            str(row.get("id") or "").strip(),
            str(row.get("title") or "").strip(),
            str(row.get("placeholder") or "").strip(),
            str(row.get("pdfFieldId") or "").strip(),
        ]
        payload = {
            "pdfFieldId": str(row.get("pdfFieldId") or row.get("id") or "").strip(),
            "page": int(row.get("page") or 1),
            "x": float(row.get("x") or 0),
            "y": float(row.get("y") or 0),
            "w": float(row.get("w") or 0),
            "h": float(row.get("h") or 0),
            "fieldType": str(row.get("fieldType") or "text").strip().lower() or "text",
        }
        for key in candidates:
            if key:
                out[key] = payload
    return out


def _normalize_frontend_field_item(item: Any, fallback_idx: int) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "id": f"auto_field_{fallback_idx}",
            "type": "text",
            "label": f"自动字段{fallback_idx}",
            "required": False,
            "defaultValue": None,
            "source": {"key": f"auto_field_{fallback_idx}", "pdfFieldId": f"f{fallback_idx}", "page": 1, "anchorType": "text"},
        }
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    field_id = str(item.get("id") or f"auto_field_{fallback_idx}").strip() or f"auto_field_{fallback_idx}"
    field_type = str(item.get("type") or "text").strip().lower() or "text"
    if field_type not in {"text", "number", "date", "textarea", "select", "radio", "boolean", "table", "group", "signature", "computed", "verdict"}:
        field_type = "text"
    label = str(item.get("label") or item.get("title") or field_id).strip() or field_id
    return {
        "id": field_id,
        "type": field_type,
        "label": label,
        "required": bool(item.get("required", False)),
        "defaultValue": item.get("defaultValue", None),
        "source": {
            "key": str(source.get("key") or field_id),
            "pdfFieldId": str(source.get("pdfFieldId") or field_id),
            "page": int(source.get("page") or 1),
            "anchorType": str(source.get("anchorType") or "text"),
        },
    }


def _normalize_generated_frontend_payload(obj: Any, template_obj: Dict[str, Any]) -> Dict[str, Any]:
    data = obj if isinstance(obj, dict) else {}
    meta = template_obj.get("meta") if isinstance(template_obj.get("meta"), dict) else {}
    pdf_field_map = _build_pdf_field_map(template_obj)
    steps_in = data.get("steps") if isinstance(data.get("steps"), list) else []
    steps: List[Dict[str, Any]] = []
    used_ids = set()
    used_step_ids = set()
    used_section_ids = set()
    for si, step in enumerate(steps_in, start=1):
        if not isinstance(step, dict):
            continue
        step_title = str(step.get("title") or "").strip()
        step_id = _slugify_ascii_id(str(step.get("id") or ""), f"step_auto_{si}")
        if not step_id.startswith("step_"):
            step_id = f"step_{step_id}"
        if step_id in used_step_ids:
            n = 2
            base_step_id = step_id
            while f"{base_step_id}_{n}" in used_step_ids:
                n += 1
            step_id = f"{base_step_id}_{n}"
        used_step_ids.add(step_id)
        sections_in = step.get("sections") if isinstance(step.get("sections"), list) else []
        sections: List[Dict[str, Any]] = []
        for sj, section in enumerate(sections_in, start=1):
            if not isinstance(section, dict):
                continue
            section_title = str(section.get("title") or "").strip()
            section_id = _slugify_ascii_id(str(section.get("id") or ""), f"sec_auto_{si}_{sj}")
            if not section_id.startswith("sec_"):
                section_id = f"sec_{section_id}"
            if section_id in used_section_ids:
                n = 2
                base_section_id = section_id
                while f"{base_section_id}_{n}" in used_section_ids:
                    n += 1
                section_id = f"{base_section_id}_{n}"
            used_section_ids.add(section_id)
            fields_in = section.get("fields") if isinstance(section.get("fields"), list) else []
            fields: List[Dict[str, Any]] = []
            for fk, field in enumerate(fields_in, start=1):
                normalized = _normalize_frontend_field_item(field, fk)
                label_for_id = str(normalized.get("label") or "").strip()
                base_id = _to_camel_case_id(normalized["id"], f"field{si}{sj}{fk}")
                # 优先基于 label 语义命名；若模型已给英文 id 则保留。
                if re.search(r"[\u4e00-\u9fff]", normalized["id"]) or not re.match(r"^[a-z][A-Za-z0-9]*$", normalized["id"]):
                    base_id = _to_camel_case_id(label_for_id, base_id)
                n = 1
                current = base_id
                while current in used_ids:
                    n += 1
                    current = f"{base_id}{n}"
                normalized["id"] = current
                normalized["source"]["key"] = normalized["source"]["key"] or current
                used_ids.add(current)
                anchor_candidates = [
                    str(normalized["source"].get("key") or "").strip(),
                    str(normalized["source"].get("pdfFieldId") or "").strip(),
                    label_for_id,
                    str(field.get("label") or "").strip() if isinstance(field, dict) else "",
                ]
                anchor = None
                for candidate in anchor_candidates:
                    if candidate and candidate in pdf_field_map:
                        anchor = pdf_field_map[candidate]
                        break
                # 前端导出 JSON 以可渲染 schema 为目标，不输出坐标字段。
                # 仅保留 source 中的锚点引用信息供后续回填链路使用。
                if anchor:
                    normalized["source"]["pdfFieldId"] = anchor["pdfFieldId"]
                    normalized["source"]["page"] = anchor["page"]
                    normalized["source"]["anchorType"] = anchor["fieldType"]
                    normalized["__order"] = (anchor["page"], anchor["y"], anchor["x"], fk)
                else:
                    normalized["__order"] = (int(normalized["source"].get("page") or 999), 999999, 999999, fk)
                ft = str(normalized.get("type") or "text")
                if ft == "number" and not isinstance(normalized.get("precision"), int):
                    normalized["precision"] = 2
                if ft in {"radio", "select"} and not normalized.get("enumRef"):
                    normalized["enumRef"] = "defaultOptions"
                if ft == "computed":
                    normalized["dependsOn"] = normalized.get("dependsOn") if isinstance(normalized.get("dependsOn"), list) else []
                    normalized["formula"] = str(normalized.get("formula") or "")
                if ft == "verdict":
                    normalized["rule"] = str(normalized.get("rule") or "")
                columns = field.get("columns") if isinstance(field, dict) and isinstance(field.get("columns"), list) else []
                if columns:
                    used_col_ids = set()
                    fixed_columns = []
                    for ci, col in enumerate(columns, start=1):
                        if not isinstance(col, dict):
                            continue
                        col_label = str(col.get("label") or "").strip()
                        col_raw_id = str(col.get("id") or "").strip()
                        col_id = _to_camel_case_id(col_raw_id, f"column{si}{sj}{fk}{ci}")
                        if re.search(r"[\u4e00-\u9fff]", col_raw_id) or not re.match(r"^[a-z][A-Za-z0-9]*$", col_raw_id):
                            col_id = _to_camel_case_id(col_label, col_id)
                        cidx = 1
                        base_col_id = col_id
                        while col_id in used_col_ids:
                            cidx += 1
                            col_id = f"{base_col_id}{cidx}"
                        used_col_ids.add(col_id)
                        fixed_col = dict(col)
                        fixed_col["id"] = col_id
                        fixed_columns.append(fixed_col)
                    normalized["columns"] = fixed_columns
                fields.append(normalized)
            if not fields:
                continue
            sections.append(
                {
                    "id": section_id,
                    "title": section_title or f"分组{si}-{sj}",
                    "layout": str(section.get("layout") or "form"),
                    "fields": sorted(
                        fields,
                        key=lambda f: (
                            (f.get("__order") or (999, 999999, 999999, 999999))[0],
                            (f.get("__order") or (999, 999999, 999999, 999999))[1],
                            (f.get("__order") or (999, 999999, 999999, 999999))[2],
                            str(f.get("id") or ""),
                        ),
                    ),
                }
            )
        if not sections:
            continue
        steps.append(
            {
                "id": step_id,
                "title": step_title or f"步骤{si}",
                "sections": sections,
            }
        )
    result = {
        "templateId": str(data.get("templateId") or template_obj.get("templateId") or meta.get("templateId") or ""),
        "templateName": str(data.get("templateName") or template_obj.get("templateName") or meta.get("templateName") or ""),
        "version": str(data.get("version") or template_obj.get("version") or meta.get("version") or "1.0.0"),
        "reportType": str(data.get("reportType") or template_obj.get("reportType") or meta.get("reportType") or "") or "radiationQc",
        "standard": str(data.get("standard") or template_obj.get("standard") or meta.get("standard") or "") or "WS 521-2017 医用X射线诊断设备质量控制检测规范",
        "pdfUrl": str(data.get("pdfUrl") or template_obj.get("pdfUrl") or meta.get("pdfUrl") or ""),
        "locale": str(data.get("locale") or template_obj.get("locale") or meta.get("locale") or "zh-CN"),
        "constants": data.get("constants") if isinstance(data.get("constants"), dict) else {},
        "enums": data.get("enums") if isinstance(data.get("enums"), dict) else {},
        "steps": steps,
    }
    if not result["pdfUrl"] and result["templateId"]:
        result["pdfUrl"] = f"/pdf/templates/{result['templateId']}.pdf"
    for step in result.get("steps", []):
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            for field in section.get("fields", []):
                if isinstance(field, dict):
                    field.pop("__order", None)
    # 年月日拆分字段合并：仅保留主字段 date（如 test_date_year/month/day -> testDate）
    for step in result.get("steps", []):
        if not isinstance(step, dict):
            continue
        for section in step.get("sections", []):
            if not isinstance(section, dict):
                continue
            fields = section.get("fields")
            if not isinstance(fields, list) or not fields:
                continue
            grouped = {}
            kept = []
            for f in fields:
                if not isinstance(f, dict):
                    continue
                fid = str(f.get("id") or "")
                src = f.get("source") if isinstance(f.get("source"), dict) else {}
                key = str(src.get("key") or "")
                base = ""
                for text in (key, fid):
                    low = text.lower()
                    for suffix in ("_year", "_month", "_day"):
                        if low.endswith(suffix):
                            base = text[: -len(suffix)]
                            break
                    if base:
                        break
                if not base:
                    for suffix in ("Year", "Month", "Day"):
                        if fid.endswith(suffix):
                            base = fid[: -len(suffix)]
                            break
                if not base:
                    kept.append(f)
                else:
                    grouped.setdefault(base, []).append(f)
            merged = []
            for base, items in grouped.items():
                seed = items[0]
                src = seed.get("source") if isinstance(seed.get("source"), dict) else {}
                base_id = _to_camel_case_id(base, _to_camel_case_id(str(seed.get("id") or "testDate"), "testDate"))
                merged.append(
                    {
                        "id": base_id,
                        "type": "date",
                        "label": str(seed.get("label") or base).rstrip("年月日") or base,
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
                )
            section["fields"] = kept + merged
    # 签名步骤始终置于最后，保证前端渲染顺序稳定。
    steps = result.get("steps")
    if isinstance(steps, list) and steps:
        normal_steps = []
        signature_steps = []
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
        result["steps"] = normal_steps + signature_steps
    return result


def generate_frontend_template_with_ollama(
    template_obj: Dict[str, Any],
    source_file_hint: int = 1,
    sample_template_obj: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Generate frontend-renderable template JSON from htmlpdf/unified template via local Ollama model.
    Returns empty dict when generation fails.
    """
    try:
        host = (os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434") or "http://127.0.0.1:11434").strip()
        client = OllamaClientGpuPreference(host=host)
        model = Ollama(client, model_name=pipeline_config.MODEL_NAME)
        schema = {
            "type": "object",
            "properties": {
                "templateId": {"type": "string"},
                "templateName": {"type": "string"},
                "version": {"type": "string"},
                "reportType": {"type": "string"},
                "standard": {"type": "string"},
                "pdfUrl": {"type": "string"},
                "locale": {"type": "string"},
                "constants": {"type": "object"},
                "enums": {"type": "object"},
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "sections": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "title": {"type": "string"},
                                        "layout": {"type": "string"},
                                        "fields": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "id": {"type": "string"},
                                                    "type": {"type": "string"},
                                                    "label": {"type": "string"},
                                                    "required": {"type": "boolean"},
                                                    "defaultValue": {},
                                                    "source": {
                                                        "type": "object",
                                                        "properties": {
                                                            "key": {"type": "string"},
                                                            "pdfFieldId": {"type": "string"},
                                                            "page": {"type": "integer"},
                                                            "anchorType": {"type": "string"},
                                                        },
                                                        "required": ["key", "pdfFieldId", "page", "anchorType"],
                                                    },
                                                },
                                                "required": ["id", "type", "label", "required", "defaultValue", "source"],
                                            },
                                        },
                                    },
                                    "required": ["id", "title", "layout", "fields"],
                                },
                            },
                        },
                        "required": ["id", "title", "sections"],
                    },
                },
            },
            "required": ["templateId", "templateName", "version", "reportType", "standard", "pdfUrl", "locale", "constants", "enums", "steps"],
        }

        sample_obj = sample_template_obj if isinstance(sample_template_obj, dict) else _load_standard_sample_template()
        for _ in range(pipeline_config.MAX_RETRIES):
            try:
                gen = Generator(model, output_type=json_schema(schema))
                raw = gen(build_frontend_template_prompt(template_obj, source_file_hint, sample_obj))
                parsed = raw
                if isinstance(raw, str):
                    parsed = validate_json(raw, None)
                normalized = _normalize_generated_frontend_payload(parsed, template_obj)
                if normalized.get("steps"):
                    return normalized
            except Exception as e:
                logger.warning("前端模板 AI 生成重试失败: %s", str(e))
                continue
        return {}
    except Exception as e:
        logger.error("Ollama连接失败(前端模板生成): %s", e)
        return {}


def _normalize_device_item(obj: Any) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return pipeline_config.SINGLE_DEVICE_STRUCT.copy()
    out = pipeline_config.SINGLE_DEVICE_STRUCT.copy()
    for k in out:
        if k in obj:
            out[k] = obj[k] if k != "额定参数" else (obj[k] if isinstance(obj[k], dict) else {})
    return out


def extract_devices_with_ollama(md_content: str, file_count: int) -> List[Dict[str, Any]]:
    try:
        host = (os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434") or "http://127.0.0.1:11434").strip()
        client = OllamaClientGpuPreference(host=host)
        model = Ollama(client, model_name=pipeline_config.MODEL_NAME)
        schema = {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "设备名称": {"type": "string"},
                    "设备型号": {"type": "string"},
                    "设备编号": {"type": "string"},
                    "生产厂家": {"type": "string"},
                    "额定参数": {"type": "object"},
                },
                "required": ["设备名称", "设备型号", "设备编号", "生产厂家", "额定参数"],
            },
        }
        fallback = [_normalize_device_item({})]

        for _ in range(pipeline_config.MAX_RETRIES):
            try:
                gen = Generator(model, output_type=json_schema(schema))
                raw = gen(build_devices_prompt(md_content, file_count))

                parsed = None
                if isinstance(raw, list):
                    parsed = raw
                elif isinstance(raw, dict):
                    parsed = [raw]
                elif isinstance(raw, str):
                    parsed = validate_json(raw, None)

                if isinstance(parsed, list) and len(parsed) >= 1:
                    return [_normalize_device_item(x) for x in parsed]
            except Exception as e:
                logger.warning("AI调用重试失败: %s", str(e))
                continue
        return fallback
    except Exception as e:
        logger.error("Ollama连接失败: %s", e)
        return [_normalize_device_item({})]
