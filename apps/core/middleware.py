"""可选的演示账号加固（见 tablet_backend.settings PARTY_A_DEMO_*）。"""
import ipaddress

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin

from apps.core.library_access import library_user_has_party_a_demo_restrictions
from apps.core.session_lease import bind_web_session_for_user


def _client_ip(request):
    if getattr(settings, "PARTY_A_DEMO_TRUST_X_FORWARDED_FOR", False):
        raw = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
        if raw:
            return raw.split(",")[0].strip()
    return (request.META.get("REMOTE_ADDR") or "").strip() or "unknown"


def _allowed_entries():
    raw = getattr(settings, "PARTY_A_DEMO_ALLOWED_IPS", "") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


def _ip_permitted(client: str, allowlist: list) -> bool:
    if not allowlist:
        return True
    if not client or client == "unknown":
        return False
    try:
        addr = ipaddress.ip_address(client)
    except ValueError:
        return False
    for entry in allowlist:
        if "/" in entry:
            try:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
        elif entry == client:
            return True
    return False


class PartyADemoSecurityMiddleware(MiddlewareMixin):
    """对带 party_a_demo_restrictions 的已登录用户校验来源 IP（可选）。"""

    def process_request(self, request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None
        if not library_user_has_party_a_demo_restrictions(user):
            return None
        allow = _allowed_entries()
        if not allow:
            return None
        client = _client_ip(request)
        if _ip_permitted(client, allow):
            return None
        return HttpResponseForbidden(
            "演示账号仅允许在指定网络访问；请联系管理员配置 PARTY_A_DEMO_ALLOWED_IPS。"
        )


class WebSessionLeaseMiddleware(MiddlewareMixin):
    """
    同一账号在 Web 后台（Django Session）仅保留最后一次登录的浏览器会话；
    与平板 App（JWT）独立，互不注销。

    关闭：settings.AUTH_SINGLE_WEB_SESSION_PER_USER = False
    """

    def process_request(self, request):
        if not getattr(settings, "AUTH_SINGLE_WEB_SESSION_PER_USER", True):
            return None
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None
        if getattr(settings, "AUTH_SINGLE_WEB_SESSION_SKIP_SUPERUSER", False) and user.is_superuser:
            return None

        session = getattr(request, "session", None)
        if not session or not session.session_key:
            return None
        # 仅当本请求由 Session 认证为已登录用户时校验（平板纯 JWT 请求通常无 _auth_user_id）
        auth_uid = session.get("_auth_user_id")
        if auth_uid is None or str(user.pk) != str(auth_uid):
            return None

        try:
            prof = user.profile
        except Exception:
            return None
        expected = prof.web_session_key
        if not expected:
            if getattr(settings, "AUTH_SINGLE_WEB_SESSION_PER_USER", True):
                bind_web_session_for_user(user, session.session_key)
            return None
        if session.session_key == expected:
            return None

        logout(request)
        if request.path.startswith("/api/"):
            return JsonResponse(
                {"detail": "此账号已在其它浏览器登录后台，当前会话已失效。", "code": "web_session_superseded"},
                status=401,
            )
        url = reverse("login") + "?reason=web_elsewhere"
        return redirect(url)
