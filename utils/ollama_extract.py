import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from ollama import Client
from outlines import Generator
from outlines.models import Ollama
from outlines.types import json_schema

from utils import pipeline_config
from utils.json_utils import validate_json
from utils.unified_template_fields import materialize_unified_pdf_fields

logger = logging.getLogger(__name__)


def _get_llm_cfg():
    try:
        from apps.core.llm_runtime_config import get_llm_runtime_config

        return get_llm_runtime_config()
    except Exception as e:
        logger.debug("读取 llm_runtime 失败，回退环境变量: %s", e)
        return None


def _ollama_default_runner_options() -> dict:
    opts: Dict[str, Any] = {"num_gpu": 999}
    cfg = _get_llm_cfg()
    if cfg is not None and isinstance(cfg.options, dict) and cfg.options:
        opts = dict(cfg.options)
    else:
        raw = os.environ.get("OLLAMA_OPTIONS", "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    opts = parsed
                else:
                    logger.warning("OLLAMA_OPTIONS 须为 JSON 对象，已使用默认 num_gpu")
            except json.JSONDecodeError:
                logger.warning("OLLAMA_OPTIONS 不是合法 JSON，已忽略: %s", raw)
    try:
        from utils.gpu_scheduler import merge_ollama_options_for_gpu_headroom

        opts = merge_ollama_options_for_gpu_headroom(opts)
    except Exception as e:
        logger.debug("Ollama GPU 动态选项合并跳过: %s", e)
    return opts


def _resolve_llm_host_model_retries() -> tuple[str, str, int]:
    cfg = _get_llm_cfg()
    if cfg is not None:
        host = (cfg.base_url or "").strip() or "http://127.0.0.1:11434"
        model = (cfg.model or "").strip() or pipeline_config.MODEL_NAME
        retries = int(cfg.max_retries or pipeline_config.MAX_RETRIES)
        return host, model, max(1, retries)
    host = (os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434") or "http://127.0.0.1:11434").strip()
    return host, pipeline_config.MODEL_NAME, pipeline_config.MAX_RETRIES


def _resolve_chat_completions_url(base_url: str) -> str:
    u = (base_url or "").strip().rstrip("/")
    if not u:
        raise ValueError("LLM base_url 为空")
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/v1"):
        return f"{u}/chat/completions"
    return f"{u}/v1/chat/completions"


def _openai_compatible_chat(prompt: str) -> str:
    """调用 OpenAI 兼容 /v1/chat/completions，返回 message.content 文本。"""
    cfg = _get_llm_cfg()
    if cfg is None:
        raise RuntimeError("无法读取 LLM 运行时配置")
    endpoint = _resolve_chat_completions_url(cfg.base_url)
    body: Dict[str, Any] = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(cfg.temperature),
    }
    if cfg.json_mode:
        body["response_format"] = {"type": "json_object"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"OpenAI 兼容 API HTTP {e.code}: {detail}") from e
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"OpenAI 兼容 API 无 choices: {str(payload)[:400]}")
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if content is None:
        raise RuntimeError("OpenAI 兼容 API 返回空 content")
    if isinstance(content, list):
        # 部分网关返回多段 content
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        content = "".join(parts)
    return str(content)


def _llm_provider() -> str:
    cfg = _get_llm_cfg()
    if cfg is not None:
        return str(cfg.provider or "ollama")
    return "ollama"


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
    try:
        from apps.core.llm_runtime_config import render_devices_prompt

        return render_devices_prompt(md, source_file_hint, struct)
    except Exception as e:
        logger.warning("渲染设备抽取 prompt 失败，使用内置默认: %s", e)
        from apps.core.llm_runtime_config import DEFAULT_DEVICES_PROMPT, _safe_replace_placeholders

        return _safe_replace_placeholders(
            DEFAULT_DEVICES_PROMPT,
            {"struct": struct, "md": md, "source_file_hint": source_file_hint},
        )


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
    try:
        from apps.core.llm_runtime_config import render_frontend_template_prompt

        return render_frontend_template_prompt(
            template_compact=compact,
            sample_compact=sample_compact,
            source_file_hint=source_file_hint,
        )
    except Exception as e:
        logger.warning("渲染前端模板 prompt 失败，使用内置默认: %s", e)
        from apps.core.llm_runtime_config import (
            DEFAULT_FRONTEND_TEMPLATE_PROMPT,
            _safe_replace_placeholders,
        )

        return _safe_replace_placeholders(
            DEFAULT_FRONTEND_TEMPLATE_PROMPT,
            {
                "sample_compact": sample_compact,
                "source_file_hint": source_file_hint,
                "compact": compact,
            },
        )


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
    pdf_fields = materialize_unified_pdf_fields(pdf.get("fields") if isinstance(pdf.get("fields"), list) else [])
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
    Generate frontend-renderable template JSON from htmlpdf/unified template via LLM.
    Returns empty dict when generation fails.
    """
    sample_obj = sample_template_obj if isinstance(sample_template_obj, dict) else _load_standard_sample_template()
    prompt = build_frontend_template_prompt(template_obj, source_file_hint, sample_obj)
    host, model_name, max_retries = _resolve_llm_host_model_retries()
    provider = _llm_provider()

    if provider == "openai_compatible":
        for _ in range(max_retries):
            try:
                raw = _openai_compatible_chat(prompt)
                parsed = validate_json(raw, None) if isinstance(raw, str) else raw
                normalized = _normalize_generated_frontend_payload(parsed, template_obj)
                if normalized.get("steps"):
                    return normalized
            except Exception as e:
                logger.warning("前端模板 AI 生成重试失败(openai_compatible): %s", str(e))
                continue
        return {}

    try:
        client = OllamaClientGpuPreference(host=host)
        model = Ollama(client, model_name=model_name)
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

        for _ in range(max_retries):
            try:
                gen = Generator(model, output_type=json_schema(schema))
                raw = gen(prompt)
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
    prompt = build_devices_prompt(md_content, file_count)
    host, model_name, max_retries = _resolve_llm_host_model_retries()
    provider = _llm_provider()
    fallback = [_normalize_device_item({})]

    def _parse_devices(raw: Any) -> List[Dict[str, Any]] | None:
        parsed = None
        if isinstance(raw, list):
            parsed = raw
        elif isinstance(raw, dict):
            # openai json_object 有时包一层
            for key in ("devices", "items", "data", "结果"):
                if isinstance(raw.get(key), list):
                    parsed = raw[key]
                    break
            if parsed is None:
                parsed = [raw]
        elif isinstance(raw, str):
            parsed = validate_json(raw, None)
            if isinstance(parsed, dict):
                for key in ("devices", "items", "data", "结果"):
                    if isinstance(parsed.get(key), list):
                        parsed = parsed[key]
                        break
                else:
                    parsed = [parsed]
        if isinstance(parsed, list) and len(parsed) >= 1:
            return [_normalize_device_item(x) for x in parsed]
        return None

    if provider == "openai_compatible":
        for _ in range(max_retries):
            try:
                raw = _openai_compatible_chat(prompt)
                got = _parse_devices(raw)
                if got:
                    return got
            except Exception as e:
                logger.warning("AI调用重试失败(openai_compatible): %s", str(e))
                continue
        return fallback

    try:
        client = OllamaClientGpuPreference(host=host)
        model = Ollama(client, model_name=model_name)
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
                },
                "required": ["设备名称", "设备型号", "设备编号", "生产厂家"],
            },
        }

        for _ in range(max_retries):
            try:
                gen = Generator(model, output_type=json_schema(schema))
                raw = gen(prompt)
                got = _parse_devices(raw)
                if got:
                    return got
            except Exception as e:
                logger.warning("AI调用重试失败: %s", str(e))
                continue
        return fallback
    except Exception as e:
        logger.error("Ollama连接失败: %s", e)
        return fallback
