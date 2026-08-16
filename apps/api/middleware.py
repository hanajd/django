"""API 相关中间件。"""

from django.middleware.gzip import GZipMiddleware
from django.utils.deprecation import MiddlewareMixin

from apps.api.jwt_runtime_config import sync_jwt_runtime_settings_if_changed


class JwtRuntimeSettingsMiddleware(MiddlewareMixin):
    """在 /api/ 请求前同步 jwt_runtime.json，使校验容差与新签发策略无需重启即可更新。"""

    def process_request(self, request):
        path = request.path or ""
        if path.startswith("/api/"):
            sync_jwt_runtime_settings_if_changed()
        return None


class SmartGZipMiddleware(GZipMiddleware):
    """
    与 GZipMiddleware 相同，但对 APK/二进制下载跳过压缩。

    原因：流式 FileResponse 一经 gzip，中间件会删除 Content-Length，
    App 下载进度条依赖该头；且对已压缩的 APK 再 gzip 无收益。
    """

    _SKIP_PATH_PREFIXES = (
        "/api/v2/app/apk/",
    )
    _SKIP_CONTENT_TYPE_MARKERS = (
        "application/vnd.android.package-archive",
        "application/octet-stream",
        "application/zip",
        # 浏览器内置 PDF 查看器对 gzip 流式 PDF 常显示空白
        "application/pdf",
    )

    def process_response(self, request, response):
        path = getattr(request, "path", "") or ""
        if any(path.startswith(p) for p in self._SKIP_PATH_PREFIXES):
            return response
        ctype = (response.get("Content-Type") or "").split(";")[0].strip().lower()
        if any(m in ctype for m in self._SKIP_CONTENT_TYPE_MARKERS):
            return response
        return super().process_response(request, response)
