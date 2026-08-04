"""
小数精度运行时配置：修改 ``decimal_precision_runtime.json`` 后无需重启即可生效。

- global_precision：全局默认小数位（非第五章特殊栏、公式栏等）
- 第五章规则：测量均值固定位；报出值量级分档（可改各档位数）
- apply_on_backfill：PDF 回填是否套用精度；关闭则原样回填前端提交值
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings


@dataclass(frozen=True)
class DecimalPrecisionRuntimeConfig:
    """小数精度（全局 + 第五章 + 回填开关）。"""

    global_precision: int = 2
    chapter5_enabled: bool = True
    chapter5_mean_precision: int = 3
    chapter5_reading_precision: int = 2
    chapter5_report_tiered: bool = True
    chapter5_report_lt10_precision: int = 2
    chapter5_report_lt100_precision: int = 1
    chapter5_report_gte100_precision: int = 0
    # 报出值分档关闭时使用的固定小数位
    chapter5_report_fixed_precision: int = 2
    apply_on_backfill: bool = True

    @property
    def apply_on_backfill_label_zh(self) -> str:
        return "回填时套用精度" if self.apply_on_backfill else "回填原样（不调精度）"

    @property
    def chapter5_label_zh(self) -> str:
        if not self.chapter5_enabled:
            return "第五章特殊规则关闭（走全局精度）"
        if self.chapter5_report_tiered:
            return (
                f"均值 {self.chapter5_mean_precision} 位；报出值分档 "
                f"<10→{self.chapter5_report_lt10_precision} / "
                f"10–100→{self.chapter5_report_lt100_precision} / "
                f"≥100→{self.chapter5_report_gte100_precision}"
            )
        return (
            f"均值 {self.chapter5_mean_precision} 位；报出值固定 "
            f"{self.chapter5_report_fixed_precision} 位"
        )


_cache_mtime: float = -1.0
_cache_config: DecimalPrecisionRuntimeConfig | None = None


def decimal_precision_runtime_config_path() -> Path:
    raw = getattr(settings, "DECIMAL_PRECISION_RUNTIME_CONFIG_FILE", None)
    if raw:
        return Path(raw)
    return Path(settings.BASE_DIR) / "decimal_precision_runtime.json"


def _defaults() -> DecimalPrecisionRuntimeConfig:
    return DecimalPrecisionRuntimeConfig()


def _clamp_int(raw: object, default: int, *, lo: int = 0, hi: int = 12) -> int:
    try:
        v = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _as_bool(raw: object, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    key = str(raw).strip().lower()
    if key in ("1", "true", "yes", "on", "y"):
        return True
    if key in ("0", "false", "no", "off", "n"):
        return False
    return default


def _parse_runtime_file(path: Path) -> DecimalPrecisionRuntimeConfig:
    defaults = _defaults()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    return DecimalPrecisionRuntimeConfig(
        global_precision=_clamp_int(
            raw.get("global_precision", raw.get("globalPrecision", defaults.global_precision)),
            defaults.global_precision,
        ),
        chapter5_enabled=_as_bool(
            raw.get("chapter5_enabled", raw.get("chapter5Enabled")),
            defaults.chapter5_enabled,
        ),
        chapter5_mean_precision=_clamp_int(
            raw.get(
                "chapter5_mean_precision",
                raw.get("chapter5MeanPrecision", defaults.chapter5_mean_precision),
            ),
            defaults.chapter5_mean_precision,
        ),
        chapter5_reading_precision=_clamp_int(
            raw.get(
                "chapter5_reading_precision",
                raw.get("chapter5ReadingPrecision", defaults.chapter5_reading_precision),
            ),
            defaults.chapter5_reading_precision,
        ),
        chapter5_report_tiered=_as_bool(
            raw.get("chapter5_report_tiered", raw.get("chapter5ReportTiered")),
            defaults.chapter5_report_tiered,
        ),
        chapter5_report_lt10_precision=_clamp_int(
            raw.get(
                "chapter5_report_lt10_precision",
                raw.get("chapter5ReportLt10Precision", defaults.chapter5_report_lt10_precision),
            ),
            defaults.chapter5_report_lt10_precision,
        ),
        chapter5_report_lt100_precision=_clamp_int(
            raw.get(
                "chapter5_report_lt100_precision",
                raw.get("chapter5ReportLt100Precision", defaults.chapter5_report_lt100_precision),
            ),
            defaults.chapter5_report_lt100_precision,
        ),
        chapter5_report_gte100_precision=_clamp_int(
            raw.get(
                "chapter5_report_gte100_precision",
                raw.get(
                    "chapter5ReportGte100Precision", defaults.chapter5_report_gte100_precision
                ),
            ),
            defaults.chapter5_report_gte100_precision,
        ),
        chapter5_report_fixed_precision=_clamp_int(
            raw.get(
                "chapter5_report_fixed_precision",
                raw.get(
                    "chapter5ReportFixedPrecision", defaults.chapter5_report_fixed_precision
                ),
            ),
            defaults.chapter5_report_fixed_precision,
        ),
        apply_on_backfill=_as_bool(
            raw.get("apply_on_backfill", raw.get("applyOnBackfill")),
            defaults.apply_on_backfill,
        ),
    )


def get_decimal_precision_runtime_config(
    *, force_reload: bool = False
) -> DecimalPrecisionRuntimeConfig:
    global _cache_mtime, _cache_config
    path = decimal_precision_runtime_config_path()
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


def write_decimal_precision_runtime_config(**kwargs: Any) -> DecimalPrecisionRuntimeConfig:
    """写入配置并刷新缓存；未传字段保留当前值。"""
    current = get_decimal_precision_runtime_config(force_reload=True)
    next_cfg = DecimalPrecisionRuntimeConfig(
        global_precision=_clamp_int(
            kwargs["global_precision"]
            if "global_precision" in kwargs
            else current.global_precision,
            current.global_precision,
        ),
        chapter5_enabled=_as_bool(
            kwargs["chapter5_enabled"]
            if "chapter5_enabled" in kwargs
            else current.chapter5_enabled,
            current.chapter5_enabled,
        ),
        chapter5_mean_precision=_clamp_int(
            kwargs["chapter5_mean_precision"]
            if "chapter5_mean_precision" in kwargs
            else current.chapter5_mean_precision,
            current.chapter5_mean_precision,
        ),
        chapter5_reading_precision=_clamp_int(
            kwargs["chapter5_reading_precision"]
            if "chapter5_reading_precision" in kwargs
            else current.chapter5_reading_precision,
            current.chapter5_reading_precision,
        ),
        chapter5_report_tiered=_as_bool(
            kwargs["chapter5_report_tiered"]
            if "chapter5_report_tiered" in kwargs
            else current.chapter5_report_tiered,
            current.chapter5_report_tiered,
        ),
        chapter5_report_lt10_precision=_clamp_int(
            kwargs["chapter5_report_lt10_precision"]
            if "chapter5_report_lt10_precision" in kwargs
            else current.chapter5_report_lt10_precision,
            current.chapter5_report_lt10_precision,
        ),
        chapter5_report_lt100_precision=_clamp_int(
            kwargs["chapter5_report_lt100_precision"]
            if "chapter5_report_lt100_precision" in kwargs
            else current.chapter5_report_lt100_precision,
            current.chapter5_report_lt100_precision,
        ),
        chapter5_report_gte100_precision=_clamp_int(
            kwargs["chapter5_report_gte100_precision"]
            if "chapter5_report_gte100_precision" in kwargs
            else current.chapter5_report_gte100_precision,
            current.chapter5_report_gte100_precision,
        ),
        chapter5_report_fixed_precision=_clamp_int(
            kwargs["chapter5_report_fixed_precision"]
            if "chapter5_report_fixed_precision" in kwargs
            else current.chapter5_report_fixed_precision,
            current.chapter5_report_fixed_precision,
        ),
        apply_on_backfill=_as_bool(
            kwargs["apply_on_backfill"]
            if "apply_on_backfill" in kwargs
            else current.apply_on_backfill,
            current.apply_on_backfill,
        ),
    )
    path = decimal_precision_runtime_config_path()
    payload = {
        "global_precision": next_cfg.global_precision,
        "chapter5_enabled": next_cfg.chapter5_enabled,
        "chapter5_mean_precision": next_cfg.chapter5_mean_precision,
        "chapter5_reading_precision": next_cfg.chapter5_reading_precision,
        "chapter5_report_tiered": next_cfg.chapter5_report_tiered,
        "chapter5_report_lt10_precision": next_cfg.chapter5_report_lt10_precision,
        "chapter5_report_lt100_precision": next_cfg.chapter5_report_lt100_precision,
        "chapter5_report_gte100_precision": next_cfg.chapter5_report_gte100_precision,
        "chapter5_report_fixed_precision": next_cfg.chapter5_report_fixed_precision,
        "apply_on_backfill": next_cfg.apply_on_backfill,
        "_comment": (
            "global_precision: 全局默认小数位；"
            "chapter5_*: 第五章工作场所放射防护表规则；"
            "apply_on_backfill=false 时 PDF 回填不改写前端提交数值。"
            "修改后无需重启。"
        ),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return get_decimal_precision_runtime_config(force_reload=True)
