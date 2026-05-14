"""Web Session 与平板 JWT 的「各一线」并发控制（互不挤占）。"""

from django.apps import apps as django_apps


def bind_web_session_for_user(user, session_key: str | None) -> None:
    """将当前 Django session 登记为该用户在 Web 后台的唯一有效会话。"""
    if not session_key:
        return
    UserProfile = django_apps.get_model("core", "UserProfile")
    prof, _ = UserProfile.objects.get_or_create(user=user)
    if prof.web_session_key != session_key:
        prof.web_session_key = session_key
        prof.save(update_fields=["web_session_key", "updated_at"])


def clear_web_session_lease(user) -> None:
    """登出 Web 时释放登记，避免同一会话键阻碍下次登录。"""
    UserProfile = django_apps.get_model("core", "UserProfile")
    try:
        prof = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        return
    if prof.web_session_key:
        prof.web_session_key = None
        prof.save(update_fields=["web_session_key", "updated_at"])


def revoke_user_refresh_tokens(user) -> int:
    """
    吊销该用户已签发的 JWT refresh（OutstandingToken 加入黑名单）。
    在平板 App 使用「账号密码登录换 token」时调用，使仅保留最后一次平板登录的 refresh 链。
    若未安装 token_blacklist，则跳过并返回 0。
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )
    except Exception:
        return 0
    n = 0
    for ot in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=ot)
        if created:
            n += 1
    return n
