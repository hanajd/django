"""JWT 登录/刷新：统一响应结构与 test 沙箱多端策略。"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.api.jwt_runtime_config import jwt_runtime_token_settings
from apps.core.library_access import library_user_is_test_peer
from apps.core.session_lease import revoke_user_refresh_tokens

User = get_user_model()


def should_revoke_prior_refresh_tokens_on_login(user) -> bool:
    """平板登录是否吊销该用户此前签发的 refresh（test 沙箱账号允许多端并行）。"""
    if not getattr(settings, "AUTH_REVOKE_PRIOR_REFRESH_TOKENS_ON_LOGIN", True):
        return False
    if library_user_is_test_peer(user):
        return False
    return True


def revoke_prior_refresh_tokens_if_needed(user) -> int:
    if not should_revoke_prior_refresh_tokens_on_login(user):
        return 0
    return revoke_user_refresh_tokens(user)


def _user_role_payload(user) -> tuple[str | None, str | None]:
    role_code = None
    role_name = None
    try:
        if hasattr(user, "profile") and user.profile.role:
            role_code = user.profile.role.code
            role_name = user.profile.role.name
    except Exception:
        pass
    return role_code, role_name


def build_jwt_login_payload(user, refresh: RefreshToken) -> dict:
    """登录成功：access + refresh + 过期秒数 + 用户信息（refresh 须在运行时配置上下文中创建）。"""
    role_code, role_name = _user_role_payload(user)
    cfg = sync_jwt_runtime_settings_for_response()
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "access_expires_in": cfg.access_expires_in,
        "refresh_expires_in": cfg.refresh_expires_in,
        "token_type": "Bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role_code": role_code,
            "role_name": role_name,
        },
    }


def sync_jwt_runtime_settings_for_response():
    from apps.api.jwt_runtime_config import apply_jwt_runtime_settings, get_jwt_runtime_config

    return apply_jwt_runtime_settings(get_jwt_runtime_config(force_reload=True))


def issue_login_jwt_payload(user) -> dict:
    """签发登录 token（读取 jwt_runtime.json，无需重启 Django）。"""
    with jwt_runtime_token_settings():
        refresh = RefreshToken.for_user(user)
        return build_jwt_login_payload(user, refresh)


def refresh_jwt_tokens(refresh_raw: str) -> dict:
    """
    使用 SimpleJWT 官方序列化器刷新 access（并在配置开启时轮换 refresh）。
    失败抛 InvalidToken / TokenError。
    """
    with jwt_runtime_token_settings() as cfg:
        serializer = TokenRefreshSerializer(data={"refresh": refresh_raw})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data["access_expires_in"] = cfg.access_expires_in
        data["token_type"] = "Bearer"
        if "refresh" in data:
            data["refresh_expires_in"] = cfg.refresh_expires_in
        return data


def jwt_refresh_error_message(exc: Exception) -> str:
    if isinstance(exc, InvalidToken):
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            code = detail.get("code") or ""
            if code == "token_not_valid":
                return "Token 无效或已过期"
        return "Token 无效或已过期"
    if isinstance(exc, TokenError):
        return "Token 无效或已过期"
    return "Token 无效或已过期"
