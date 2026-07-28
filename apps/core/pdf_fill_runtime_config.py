"""
PDF 回填运行时配置：修改 ``pdf_fill_runtime.json`` 后无需重启即可生效。

着色：
- test（测试模式）：彩色回填（红字 / 判定绿蓝 / 红勾）
- formal（正式模式）：纯黑回填

字号：
- site_font_pt / report_font_pt：现场记录 / 报告基准字号（pt）
- font_fit：auto=框内自适应缩小；fixed=强制用基准字号不缩小
- font_min_pt：auto 模式下允许的最小字号
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from django.conf import settings

PdfFillMode = Literal["test", "formal"]
PdfFontFit = Literal["auto", "fixed"]

_MODE_ALIASES = {
    "test": "test",
    "testing": "test",
    "debug": "test",
    "color": "test",
    "coloured": "test",
    "colored": "test",
    "formal": "formal",
    "production": "formal",
    "prod": "formal",
    "release": "formal",
    "black": "formal",
}

_FIT_ALIASES = {
    "auto": "auto",
    "fit": "auto",
    "shrink": "auto",
    "adaptive": "auto",
    "fixed": "fixed",
    "force": "fixed",
    "strict": "fixed",
    "no_shrink": "fixed",
}


@dataclass(frozen=True)
class PdfFillRuntimeConfig:
    """PDF 回填着色与字号。"""

    mode: PdfFillMode = "test"
    site_font_pt: float = 10.5
    report_font_pt: float = 12.0
    font_fit: PdfFontFit = "auto"
    font_min_pt: float = 5.0

    @property
    def is_test_mode(self) -> bool:
        return self.mode == "test"

    @property
    def is_formal_mode(self) -> bool:
        return self.mode == "formal"

    @property
    def font_auto_shrink(self) -> bool:
        return self.font_fit == "auto"

    @property
    def mode_label_zh(self) -> str:
        return "测试模式（彩色）" if self.is_test_mode else "正式模式（纯黑）"

    @property
    def font_fit_label_zh(self) -> str:
        return "自适应缩小" if self.font_auto_shrink else "固定字号（不缩小）"


_cache_mtime: float = -1.0
_cache_config: PdfFillRuntimeConfig | None = None


def pdf_fill_runtime_config_path() -> Path:
    raw = getattr(settings, "PDF_FILL_RUNTIME_CONFIG_FILE", None)
    if raw:
        return Path(raw)
    return Path(settings.BASE_DIR) / "pdf_fill_runtime.json"


def _defaults() -> PdfFillRuntimeConfig:
    # 默认保持现有行为：彩色 + 五号/小四 + 框内自适应
    return PdfFillRuntimeConfig()


def _normalize_mode(raw: object) -> PdfFillMode:
    key = str(raw or "").strip().lower()
    return _MODE_ALIASES.get(key, "test")  # type: ignore[return-value]


def _normalize_font_fit(raw: object) -> PdfFontFit:
    key = str(raw or "").strip().lower()
    return _FIT_ALIASES.get(key, "auto")  # type: ignore[return-value]


def _clamp_font_pt(raw: object, default: float, *, lo: float = 5.0, hi: float = 28.0) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(lo, min(hi, v))


def _parse_runtime_file(path: Path) -> PdfFillRuntimeConfig:
    defaults = _defaults()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    mode = _normalize_mode(raw.get("mode") or raw.get("pdf_fill_mode") or raw.get("fill_mode"))
    font_fit = _normalize_font_fit(
        raw.get("font_fit") or raw.get("fontFit") or raw.get("fit_mode")
    )
    # 兼容旧布尔键
    if "font_auto_shrink" in raw and raw.get("font_fit") is None:
        font_fit = "auto" if bool(raw.get("font_auto_shrink")) else "fixed"
    site_font_pt = _clamp_font_pt(
        raw.get("site_font_pt", raw.get("siteFontPt", defaults.site_font_pt)),
        defaults.site_font_pt,
    )
    report_font_pt = _clamp_font_pt(
        raw.get("report_font_pt", raw.get("reportFontPt", defaults.report_font_pt)),
        defaults.report_font_pt,
    )
    font_min_pt = _clamp_font_pt(
        raw.get("font_min_pt", raw.get("fontMinPt", defaults.font_min_pt)),
        defaults.font_min_pt,
        lo=4.0,
        hi=min(site_font_pt, report_font_pt),
    )
    return PdfFillRuntimeConfig(
        mode=mode,
        site_font_pt=site_font_pt,
        report_font_pt=report_font_pt,
        font_fit=font_fit,
        font_min_pt=font_min_pt,
    )


def get_pdf_fill_runtime_config(*, force_reload: bool = False) -> PdfFillRuntimeConfig:
    global _cache_mtime, _cache_config
    path = pdf_fill_runtime_config_path()
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


def write_pdf_fill_runtime_config(
    *,
    mode: str | None = None,
    site_font_pt: float | None = None,
    report_font_pt: float | None = None,
    font_fit: str | None = None,
    font_min_pt: float | None = None,
) -> PdfFillRuntimeConfig:
    """写入 pdf_fill_runtime.json 并刷新进程内缓存（未传字段保留当前值）。"""
    current = get_pdf_fill_runtime_config(force_reload=True)
    next_mode = _normalize_mode(mode if mode is not None else current.mode)
    next_fit = _normalize_font_fit(font_fit if font_fit is not None else current.font_fit)
    next_site = _clamp_font_pt(
        site_font_pt if site_font_pt is not None else current.site_font_pt,
        current.site_font_pt,
    )
    next_report = _clamp_font_pt(
        report_font_pt if report_font_pt is not None else current.report_font_pt,
        current.report_font_pt,
    )
    next_min = _clamp_font_pt(
        font_min_pt if font_min_pt is not None else current.font_min_pt,
        current.font_min_pt,
        lo=4.0,
        hi=min(next_site, next_report),
    )
    path = pdf_fill_runtime_config_path()
    payload = {
        "mode": next_mode,
        "site_font_pt": next_site,
        "report_font_pt": next_report,
        "font_fit": next_fit,
        "font_min_pt": next_min,
        "_comment": (
            "mode: test=彩色 / formal=纯黑；"
            "site_font_pt/report_font_pt: 现场记录/报告基准字号(pt)，五号=10.5、小四=12；"
            "font_fit: auto=框内自适应缩小 / fixed=固定字号；"
            "font_min_pt: auto 模式下最小字号。"
            "修改后无需重启，下次导出 PDF 即生效。"
        ),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return get_pdf_fill_runtime_config(force_reload=True)


def pdf_fill_is_test_mode() -> bool:
    return get_pdf_fill_runtime_config().is_test_mode


def resolve_pdf_fill_font_pt(*, is_report: bool) -> float:
    """导出用基准字号：报告 / 现场记录。"""
    cfg = get_pdf_fill_runtime_config()
    return float(cfg.report_font_pt if is_report else cfg.site_font_pt)
