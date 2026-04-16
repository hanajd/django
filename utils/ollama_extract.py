import json
import logging
import os
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
