"""API 相关中间件。"""

from django.utils.deprecation import MiddlewareMixin

from apps.api.jwt_runtime_config import sync_jwt_runtime_settings_if_changed


class JwtRuntimeSettingsMiddleware(MiddlewareMixin):
    """在 /api/ 请求前同步 jwt_runtime.json，使校验容差与新签发策略无需重启即可更新。"""

    def process_request(self, request):
        path = request.path or ""
        if path.startswith("/api/"):
            sync_jwt_runtime_settings_if_changed()
        return None
