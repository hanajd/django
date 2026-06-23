"""
JWT 运行时配置：修改 ``jwt_runtime.json`` 后无需重启 Django，下次 API 请求/登录/刷新即生效。

仅影响**新签发**的 access/refresh；已发出的 token 仍按自身 exp 过期。
校验时同步应用 ``leeway_seconds``（过期容差）。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from rest_framework_simplejwt.settings import api_settings as jwt_settings


@dataclass(frozen=True)
class JwtRuntimeConfig:
    access_token_lifetime: timedelta
    refresh_token_lifetime: timedelta
    leeway_seconds: int

    @property
    def access_expires_in(self) -> int:
        return int(self.access_token_lifetime.total_seconds())

    @property
    def refresh_expires_in(self) -> int:
        return int(self.refresh_token_lifetime.total_seconds())


_cache_mtime: float = -1.0
_cache_config: JwtRuntimeConfig | None = None


def jwt_runtime_config_path() -> Path:
    raw = getattr(settings, "JWT_RUNTIME_CONFIG_FILE", None)
    if raw:
        return Path(raw)
    return Path(settings.BASE_DIR) / "jwt_runtime.json"


def _defaults_from_settings() -> JwtRuntimeConfig:
    sj = getattr(settings, "SIMPLE_JWT", {}) or {}
    access = sj.get("ACCESS_TOKEN_LIFETIME")
    refresh = sj.get("REFRESH_TOKEN_LIFETIME")
    if not isinstance(access, timedelta):
        access = timedelta(hours=float(os.environ.get("JWT_ACCESS_TOKEN_HOURS", "8")))
    if not isinstance(refresh, timedelta):
        refresh = timedelta(days=float(os.environ.get("JWT_REFRESH_TOKEN_DAYS", "30")))
    leeway = int(sj.get("LEEWAY") or os.environ.get("JWT_LEEWAY_SECONDS") or 120)
    return JwtRuntimeConfig(
        access_token_lifetime=access,
        refresh_token_lifetime=refresh,
        leeway_seconds=leeway,
    )


def _parse_runtime_file(path: Path) -> JwtRuntimeConfig:
    defaults = _defaults_from_settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return defaults
    if not isinstance(raw, dict):
        return defaults

    access_hours = raw.get("access_token_hours", raw.get("access_hours"))
    refresh_days = raw.get("refresh_token_days", raw.get("refresh_days"))
    leeway = raw.get("leeway_seconds", raw.get("leeway"))

    access = defaults.access_token_lifetime
    if access_hours is not None:
        try:
            access = timedelta(hours=float(access_hours))
        except (TypeError, ValueError):
            pass

    refresh = defaults.refresh_token_lifetime
    if refresh_days is not None:
        try:
            refresh = timedelta(days=float(refresh_days))
        except (TypeError, ValueError):
            pass

    leeway_seconds = defaults.leeway_seconds
    if leeway is not None:
        try:
            leeway_seconds = max(0, int(leeway))
        except (TypeError, ValueError):
            pass

    if access.total_seconds() <= 0:
        access = defaults.access_token_lifetime
    if refresh.total_seconds() <= 0:
        refresh = defaults.refresh_token_lifetime

    return JwtRuntimeConfig(
        access_token_lifetime=access,
        refresh_token_lifetime=refresh,
        leeway_seconds=leeway_seconds,
    )


def get_jwt_runtime_config(*, force_reload: bool = False) -> JwtRuntimeConfig:
    global _cache_mtime, _cache_config
    path = jwt_runtime_config_path()
    if not path.is_file():
        cfg = _defaults_from_settings()
        _cache_mtime = -1.0
        _cache_config = cfg
        return cfg

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _defaults_from_settings()

    if not force_reload and _cache_config is not None and mtime == _cache_mtime:
        return _cache_config

    cfg = _parse_runtime_file(path)
    _cache_mtime = mtime
    _cache_config = cfg
    return cfg


def apply_jwt_runtime_settings(config: JwtRuntimeConfig | None = None) -> JwtRuntimeConfig:
    """将运行时配置写入 SimpleJWT api_settings（进程内即时生效）。"""
    cfg = config or get_jwt_runtime_config()
    jwt_settings.ACCESS_TOKEN_LIFETIME = cfg.access_token_lifetime
    jwt_settings.REFRESH_TOKEN_LIFETIME = cfg.refresh_token_lifetime
    jwt_settings.LEEWAY = cfg.leeway_seconds
    return cfg


def sync_jwt_runtime_settings_if_changed() -> JwtRuntimeConfig:
    """API 请求入口：文件有变更时重新加载。"""
    path = jwt_runtime_config_path()
    global _cache_mtime
    if path.is_file():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = -1.0
        if _cache_config is not None and mtime == _cache_mtime:
            return _cache_config
    return apply_jwt_runtime_settings(get_jwt_runtime_config())


@contextmanager
def jwt_runtime_token_settings():
    """登录/刷新时确保使用最新运行时有效期。"""
    cfg = apply_jwt_runtime_settings(get_jwt_runtime_config(force_reload=True))
    yield cfg


def write_jwt_runtime_config(
    *,
    access_token_hours: float | None = None,
    refresh_token_days: float | None = None,
    leeway_seconds: int | None = None,
) -> JwtRuntimeConfig:
    """写入 jwt_runtime.json 并立即应用到当前进程。"""
    path = jwt_runtime_config_path()
    current = get_jwt_runtime_config(force_reload=True)
    payload = {
        "access_token_hours": float(
            access_token_hours
            if access_token_hours is not None
            else current.access_token_lifetime.total_seconds() / 3600
        ),
        "refresh_token_days": float(
            refresh_token_days
            if refresh_token_days is not None
            else current.refresh_token_lifetime.total_seconds() / 86400
        ),
        "leeway_seconds": int(
            leeway_seconds if leeway_seconds is not None else current.leeway_seconds
        ),
        "_comment": "修改本文件后无需重启 Django；新登录/刷新的 token 立即使用新有效期。",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return apply_jwt_runtime_settings(get_jwt_runtime_config(force_reload=True))
