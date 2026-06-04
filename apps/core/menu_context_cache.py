"""全局菜单/权限上下文缓存（进程内 LocMem，无第三方依赖）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache


def _cache_version() -> int:
    return int(cache.get("menu_context_cache_version", 1) or 1)


def menu_context_cache_key(user) -> Optional[str]:
    if not getattr(user, "is_authenticated", False):
        return None
    role_id = 0
    try:
        role_id = int(user.profile.role_id or 0)
    except Exception:
        role_id = 0
    super_flag = 1 if getattr(user, "is_superuser", False) else 0
    return f"menu_ctx:v{_cache_version()}:u{user.pk}:r{role_id}:s{super_flag}"


def get_cached_menu_context_payload(user) -> Optional[Dict[str, Any]]:
    key = menu_context_cache_key(user)
    if not key:
        return None
    payload = cache.get(key)
    if isinstance(payload, dict):
        return payload
    return None


def set_cached_menu_context_payload(user, payload: Dict[str, Any]) -> None:
    key = menu_context_cache_key(user)
    if not key:
        return
    timeout = int(getattr(settings, "MENU_CONTEXT_CACHE_TIMEOUT", 300) or 300)
    cache.set(key, payload, timeout=timeout)


def invalidate_menu_context_cache() -> None:
    """菜单/角色变更后调用，使所有用户的菜单上下文缓存失效。"""
    version = _cache_version()
    cache.set("menu_context_cache_version", version + 1, timeout=None)
