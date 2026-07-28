"""
OCR / 管线 LLM 运行时配置（热更新）。

含：
- API：provider（ollama / openai_compatible）、base_url、api_key、model、options
- Prompt：设备铭牌抽取、前端模板转换（占位符见各默认模板注释）

修改 ``llm_runtime.json`` 或调试设置页保存后，无需重启即可生效。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from django.conf import settings

LlmProvider = Literal["ollama", "openai_compatible"]

# 设备抽取：须含 {struct}、{md}；可选 {source_file_hint}
DEFAULT_DEVICES_PROMPT = """你是设备铭牌提取专家，从文本中提取所有独立设备铭牌，输出**严格JSON数组**，无其他内容。文本中可能有部分拼写错误或者识别错误，请根据上下文进行简单修正，避免改动过大。
===== 提取规则（必须逐条遵守） =====
【1. 设备名称】
- 定义：文本里直接说明设备是什么的短语/英文，比如“X-RAY TUBE HOUSING ASSEMBLY”“X射线管组件”
- 匹配关键词：找文本里大写的设备功能/部件名词，比如TUBE、ASSEMBLY、SYSTEM、SCANNER等
- 示例：如果文本里有“X-RAY TUBE HOUSING ASSEMBLY”，设备名称尽量翻译成中文名称
- 注意：不要把厂家名、型号代码当成设备名称

【2. 设备型号】
- 定义：厂家给设备/部件的型号代码，通常是字母+数字的组合
- 匹配关键词：前面通常跟着“Model:”“型号:”“REF:”，格式类似“XXX 1234”“ABC-123/45”
- 示例：文本里“Model MRC 200 0407 ROT-GS 1004”“REF 9890 000 86502”都是型号相关内容
- 注意：要把所有标注为Model/REF的内容都列出来，区分“组件型号”和“核心部件型号”

【3. 设备编号/序列号（SN号）】
- 定义：设备唯一的出厂编号，通常是一串数字+字母
- 匹配关键词：前面跟着“SN:”“Serial No.:”“序列号:”，格式类似“72320M168874”“168874”
- 示例：文本里“SN 72320M168874”就是序列号
- 注意：序列号是唯一的串号，不是型号代码

【4. 生产厂家】
- 定义：设备的制造商全称，不要包含地址、城市、国家，只用写出公司全称即可
- 匹配关键词：找文本开头/上方的公司名，通常包含“Medical Systems”“GmbH”“Co., Ltd”，后面跟着地址、城市、国家
- 示例：文本里“Philips Medical Systems DMC GmbH, Röntgenstraße 24, 22335 Hamburg / GERMANY”就是厂家信息
- 注意：不要只写“Philips”，要写完整识别到的内容

【5. 额定参数】
- 定义：设备的核心工作参数，通常是kV/mA/mAs/mAs范围，当前表单中的额定参数主要是球管参数，如果没有球管参数，则不用填入内容
- 匹配关键词：前面跟着“Rated kV:”“额定kV:”“Rated mA:”“额定mA:”“Rated mAs:”“额定mAs:”，格式类似“100 kV”“200 mA”
- 示例：文本里“Rated kV 100”“Rated mA 200”都是额定参数
- 注意：要把所有标注为Rated的内容都列出来，区分kV/mA/mAs

格式：
{struct}

文本：
{md}
"""

# 前端模板：须含占位符 {sample_compact}、{source_file_hint}、{compact}
# （其余花括号为 JSON 示例，渲染时用安全替换，勿用 str.format）
DEFAULT_FRONTEND_TEMPLATE_PROMPT = """# 角色
你是放射医疗设备质量控制检测表单JSON架构专家、Flutter动态表单Schema工程师，严格遵循统一动态表单规范 + 参考样板格式，标准化转换所有新旧放射检测记录JSON模板。

固定统一顶层JSON结构（一字不改，顺序固定）：
{
  "templateId": "",
  "templateName": "",
  "version": "1.0.0",
  "reportType": "",
  "standard": "",
  "pdfUrl": "",
  "locale": "zh-CN",
  "constants": {},
  "enums": {},
  "steps": []
}

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

人员签名类字段 ID 命名字典（强制执行）：
- 执行层：“检测员 / 测试员” -> id: "inspector"
- 检验主责：“主检 / 主检人” -> id: "mainInspector"
- 复核层：“校核员 / 校核人 / 复核人” -> id: "checker"（不可与审核混淆）
- 审批层：“审核员 / 审核人” -> id: "reviewer"
- 终审层：“批准人 / 授权签字人” -> id: "approver" 或 "authorizedSignatory"
- 外部人员：“受检单位陪同人” -> id: "accompanyingPerson"

警告：
- 遇到“校核员及校核日期”这类组合文本，必须判定为 signature，id 使用 "checker"；
- 严禁将此类签字字段命名为 dateDay/date 或混合多个角色语义；
- source.pdfFieldId 必须使用 PDF 原生唯一物理占位符（如 f78/f79），不得用语义字段名替代。

参考标准样板JSON：
{sample_compact}

待转换原始JSON（文件数提示: {source_file_hint}）：
{compact}
"""


@dataclass(frozen=True)
class LlmRuntimeConfig:
    provider: LlmProvider = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    api_key: str = ""
    model: str = "qwen3:14b-q4_K_M"
    # Ollama runner options（JSON 对象）；openai_compatible 时忽略 num_gpu 等
    options: dict[str, Any] | None = None
    temperature: float = 0.2
    max_retries: int = 3
    # openai_compatible：是否请求 response_format=json_object（部分网关不支持可关）
    json_mode: bool = True
    devices_prompt: str = DEFAULT_DEVICES_PROMPT
    frontend_template_prompt: str = DEFAULT_FRONTEND_TEMPLATE_PROMPT

    @property
    def provider_label_zh(self) -> str:
        if self.provider == "openai_compatible":
            return "OpenAI 兼容 API"
        return "Ollama（本地）"

    @property
    def options_json(self) -> str:
        opts = self.options if isinstance(self.options, dict) else {"num_gpu": 999}
        return json.dumps(opts, ensure_ascii=False, indent=2)


_cache_mtime: float = -1.0
_cache_config: LlmRuntimeConfig | None = None


def llm_runtime_config_path() -> Path:
    raw = getattr(settings, "LLM_RUNTIME_CONFIG_FILE", None)
    if raw:
        return Path(raw)
    return Path(settings.BASE_DIR) / "llm_runtime.json"


def _defaults() -> LlmRuntimeConfig:
    host = ""
    try:
        host = str(getattr(settings, "OLLAMA_HOST", "") or "").strip()
    except Exception:
        host = ""
    if not host:
        import os

        host = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").strip()
    import os

    model = (os.environ.get("EQUIPMENT_MODEL_NAME") or "qwen3:14b-q4_K_M").strip()
    return LlmRuntimeConfig(base_url=host or "http://127.0.0.1:11434", model=model or "qwen3:14b-q4_K_M")


def _normalize_provider(raw: object) -> LlmProvider:
    key = str(raw or "").strip().lower()
    if key in ("openai", "openai_compatible", "openai-compatible", "compatible", "vllm", "lmstudio"):
        return "openai_compatible"
    return "ollama"


def _parse_options(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"num_gpu": 999}


def _clamp_retries(raw: object, default: int = 3) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(8, v))


def _clamp_temperature(raw: object, default: float = 0.2) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return max(0.0, min(2.0, v))


def _parse_bool(raw: object, default: bool = True) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw or "").strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _parse_runtime_file(path: Path) -> LlmRuntimeConfig:
    defaults = _defaults()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    provider = _normalize_provider(raw.get("provider") or raw.get("llm_provider"))
    base_url = str(raw.get("base_url") or raw.get("host") or raw.get("OLLAMA_HOST") or defaults.base_url).strip()
    if not base_url:
        base_url = defaults.base_url
    api_key = str(raw.get("api_key") or raw.get("apiKey") or "").strip()
    model = str(raw.get("model") or raw.get("model_name") or raw.get("EQUIPMENT_MODEL_NAME") or defaults.model).strip()
    if not model:
        model = defaults.model
    options = _parse_options(raw.get("options") or raw.get("ollama_options") or raw.get("OLLAMA_OPTIONS"))
    devices = str(raw.get("devices_prompt") or "").strip() or DEFAULT_DEVICES_PROMPT
    frontend = str(raw.get("frontend_template_prompt") or "").strip() or DEFAULT_FRONTEND_TEMPLATE_PROMPT
    return LlmRuntimeConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        options=options,
        temperature=_clamp_temperature(raw.get("temperature"), defaults.temperature),
        max_retries=_clamp_retries(raw.get("max_retries"), defaults.max_retries),
        json_mode=_parse_bool(raw.get("json_mode"), True),
        devices_prompt=devices,
        frontend_template_prompt=frontend,
    )


def get_llm_runtime_config(*, force_reload: bool = False) -> LlmRuntimeConfig:
    global _cache_mtime, _cache_config
    path = llm_runtime_config_path()
    if not path.is_file():
        cfg = _defaults()
        _cache_mtime = -1.0
        _cache_config = cfg
        return cfg
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _defaults()
    if not force_reload and _cache_config is not None and mtime == _cache_mtime:
        return _cache_config
    cfg = _parse_runtime_file(path)
    _cache_mtime = mtime
    _cache_config = cfg
    return cfg


def write_llm_runtime_config(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    options: dict[str, Any] | str | None = None,
    temperature: float | None = None,
    max_retries: int | None = None,
    json_mode: bool | None = None,
    devices_prompt: str | None = None,
    frontend_template_prompt: str | None = None,
    preserve_missing_prompts: bool = True,
) -> LlmRuntimeConfig:
    """写入配置。prompt 传 None 且 preserve_missing_prompts 时保留文件中原值。"""
    global _cache_mtime, _cache_config
    cur = get_llm_runtime_config(force_reload=True)
    opts = cur.options
    if options is not None:
        opts = _parse_options(options)
    next_devices = cur.devices_prompt
    next_frontend = cur.frontend_template_prompt
    if devices_prompt is not None:
        next_devices = devices_prompt.strip() or DEFAULT_DEVICES_PROMPT
    elif not preserve_missing_prompts:
        next_devices = DEFAULT_DEVICES_PROMPT
    if frontend_template_prompt is not None:
        next_frontend = frontend_template_prompt.strip() or DEFAULT_FRONTEND_TEMPLATE_PROMPT
    elif not preserve_missing_prompts:
        next_frontend = DEFAULT_FRONTEND_TEMPLATE_PROMPT

    cfg = LlmRuntimeConfig(
        provider=_normalize_provider(provider if provider is not None else cur.provider),
        base_url=str(base_url if base_url is not None else cur.base_url).strip().rstrip("/")
        or cur.base_url,
        api_key=str(api_key if api_key is not None else cur.api_key).strip(),
        model=str(model if model is not None else cur.model).strip() or cur.model,
        options=opts if isinstance(opts, dict) else {"num_gpu": 999},
        temperature=_clamp_temperature(
            temperature if temperature is not None else cur.temperature, cur.temperature
        ),
        max_retries=_clamp_retries(
            max_retries if max_retries is not None else cur.max_retries, cur.max_retries
        ),
        json_mode=_parse_bool(json_mode if json_mode is not None else cur.json_mode, cur.json_mode),
        devices_prompt=next_devices,
        frontend_template_prompt=next_frontend,
    )
    path = llm_runtime_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(cfg)
    tmp = path.with_suffix(path.suffix + f".{int(time.time())}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    _cache_mtime = path.stat().st_mtime
    _cache_config = cfg
    return cfg


def _safe_replace_placeholders(tpl: str, mapping: dict[str, Any]) -> str:
    """只替换已知占位符，避免 prompt 内 JSON 花括号被 str.format 误伤。"""
    out = tpl
    for key, val in mapping.items():
        out = out.replace("{" + str(key) + "}", str(val))
    return out


def render_devices_prompt(md: str, source_file_hint: int, struct_json: str) -> str:
    cfg = get_llm_runtime_config()
    tpl = cfg.devices_prompt or DEFAULT_DEVICES_PROMPT
    return _safe_replace_placeholders(
        tpl,
        {
            "struct": struct_json,
            "md": md,
            "source_file_hint": source_file_hint,
        },
    )


def render_frontend_template_prompt(
    *,
    template_compact: str,
    sample_compact: str,
    source_file_hint: int,
) -> str:
    cfg = get_llm_runtime_config()
    tpl = cfg.frontend_template_prompt or DEFAULT_FRONTEND_TEMPLATE_PROMPT
    return _safe_replace_placeholders(
        tpl,
        {
            "sample_compact": sample_compact,
            "source_file_hint": source_file_hint,
            "compact": template_compact,
        },
    )
