import json
import re
from typing import Any


def clean_json_string(raw_str: str) -> str:
    if not raw_str:
        return ""
    raw_str = re.sub(r"```json|```", "", raw_str)
    raw_str = re.sub(r"^[^{[]+", "", raw_str)
    raw_str = re.sub(r"[^}\]]+$", "", raw_str)
    return raw_str.replace("“", '"').replace("”", '"').strip()


def repair_common_json_errors(s: str) -> str:
    s += "}" * max(0, s.count("{") - s.count("}"))
    s += "]" * max(0, s.count("[") - s.count("]"))
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    return s


def validate_json(raw: str, default: Any) -> Any:
    try:
        return json.loads(repair_common_json_errors(clean_json_string(raw)))
    except Exception:
        return default
