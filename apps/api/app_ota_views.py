"""Android App OTA：版本检查与 APK 下载 API。"""
from __future__ import annotations

import logging

from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.app_ota_service import (
    apk_file_path,
    build_version_check_payload,
    get_app_ota_runtime_config,
    parse_client_build,
    sanitize_apk_filename,
)

logger = logging.getLogger(__name__)


class AppVersionAPIView(APIView):
    """
    GET /api/v2/app/version
    Query: platform=android&current_version=&current_build=
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        platform = (request.query_params.get("platform") or "").strip().lower()
        if platform and platform != "android":
            return Response(
                {"detail": "仅支持 platform=android"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        current_version = (request.query_params.get("current_version") or "").strip()
        current_build = parse_client_build(request.query_params.get("current_build"))
        payload = build_version_check_payload(
            request=request,
            current_build=current_build,
            current_version=current_version,
        )
        return Response(payload, status=status.HTTP_200_OK)


class AppApkDownloadAPIView(APIView):
    """
    GET /api/v2/app/apk/<filename>
    内网可匿名下载当前目录下安全命名的 APK；生产亦可经 Nginx 反代同路径。
    始终返回 Content-Length，供前端进度条。
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # 避免无效 Bearer 导致 401，便于系统下载器直链

    def get(self, request, filename: str):
        try:
            safe = sanitize_apk_filename(filename)
            path = apk_file_path(safe)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not path.is_file():
            raise Http404("APK 不存在")
        # 可选：仅允许下载「当前发布包」，防止目录枚举扫到旧包文件名
        cfg = get_app_ota_runtime_config()
        if cfg.enabled and cfg.filename and cfg.filename != safe:
            # 仍允许下载同目录历史包（运维有时需要）；若要收紧可改为 404
            logger.info("OTA download non-current apk: %s (current=%s)", safe, cfg.filename)
        try:
            size = path.stat().st_size
            fh = path.open("rb")
        except OSError as exc:
            logger.warning("OTA apk open failed: %s", exc)
            raise Http404("APK 不可读") from exc
        # FileResponse 为 streaming；全局 GZip 会删掉 Content-Length，已由 SmartGZipMiddleware 对本路径跳过
        resp = FileResponse(
            fh,
            as_attachment=True,
            filename=safe,
            content_type="application/vnd.android.package-archive",
        )
        # 显式写入，供下载进度条（勿依赖中间件事后推断）
        resp.headers["Content-Length"] = str(size)
        resp.headers["Accept-Ranges"] = "bytes"
        if cfg.sha256 and cfg.filename == safe:
            resp.headers["X-Checksum-SHA256"] = cfg.sha256
        return resp
