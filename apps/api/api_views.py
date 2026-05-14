"""
API 视图
使用 Django REST Framework 提供 JSON 接口
"""
import json as json_std
import threading
import uuid

from django.conf import settings
from django.core import signing
from django.db import close_old_connections
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from apps.core.library_access import (
    library_file_access_allowed,
    library_user_can_assign_tasks_to_participants,
    role_can_upload_library_category,
    role_has,
)
from apps.core.library_file_service import (
    attach_files_to_projects,
    convert_template_word_to_temp_pdf,
    library_file_download_response,
    parse_project_ids,
    save_library_binary_uploads,
)
from apps.core import pipeline_service
from apps.core.session_lease import revoke_user_refresh_tokens
from apps.core.models import LibraryFile, LibraryProject, Menu, Role, UserProfile
from apps.core.models import LibraryOCRProcessTask
from apps.core.serializers import (
    MenuSerializer,
    RoleSerializer,
    UserCreateSerializer,
    UserSerializer,
)
from apps.core.permissions import IsAdminOrSuperAdmin, IsAppUser

_TEMPLATE_PDF_SIGNER = signing.TimestampSigner(salt="template-pdf-download")


class LibraryOCRUploadAPIView(APIView):
    """
    OCR 文件上传后异步 OCR 处理（与 Web 文件库「OCR文件」分类一致）。

    - 认证：``Authorization: Bearer <access>``（与 ``/api/v1/auth/login/`` 返回的 access 一致）。
    - 请求：``multipart/form-data``，字段 ``files`` 可多文件；也支持单字段 ``file``。
    - 权限：需 ``perm_file_library`` 且具备 OCR 分类上传权（与 Web 端 ``perm_file_upload`` 一致）。
    - 行为：上传成功后立即返回任务 ID，后台异步执行流程。

    前端异步示例（不阻塞 UI）::

        const fd = new FormData();
        for (const blob of blobs) fd.append('files', blob, blob.name);
        const res = await fetch('/api/v1/library/files/ocr/', {
          method: 'POST',
          headers: { Authorization: `Bearer ${accessToken}` },
          body: fd,
        });
        const data = await res.json();
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @staticmethod
    def _run_ocr_pipeline_task(task_id: int):
        close_old_connections()
        task = LibraryOCRProcessTask.objects.filter(pk=task_id).first()
        if not task:
            return
        try:
            task.status = LibraryOCRProcessTask.STATUS_RUNNING
            task.error_message = ""
            task.save(update_fields=["status", "error_message", "updated_at"])

            rows = list(
                LibraryFile.objects.filter(
                    pk__in=task.source_file_ids,
                    category=LibraryFile.CATEGORY_UPLOAD,
                ).order_by("id")
            )
            payloads = []
            skipped = []
            for lf in rows:
                ext = (lf.original_name.rsplit(".", 1)[-1].lower() if "." in lf.original_name else "")
                if ext not in ("pdf", "jpg", "jpeg", "png"):
                    skipped.append({"id": lf.pk, "name": lf.original_name, "reason": "unsupported_format"})
                    continue
                try:
                    p = pipeline_service.library_absolute_path(lf.relative_path)
                    payloads.append((lf.original_name, p.read_bytes()))
                except (ValueError, OSError):
                    skipped.append({"id": lf.pk, "name": lf.original_name, "reason": "file_missing"})

            if not payloads:
                task.status = LibraryOCRProcessTask.STATUS_FAILED
                task.error_message = "无可处理的 OCR 文件（仅支持 PDF/JPG/JPEG/PNG 且文件存在）"
                task.result_summary = {"skipped": skipped}
                task.save(update_fields=["status", "error_message", "result_summary", "updated_at"])
                return

            batch_id = str(uuid.uuid4())
            devices, pipeline, image_data = pipeline_service.run_process_all_files(payloads, batch_id)
            n_json = pipeline_service.write_split_device_json_files(batch_id, devices, task.created_by)
            pipeline_service.sync_temp_and_json_records(batch_id, task.created_by)
            json_ids = list(
                LibraryFile.objects.filter(
                    category=LibraryFile.CATEGORY_JSON,
                    batch_id=batch_id,
                )
                .order_by("created_at")
                .values_list("id", flat=True)
            )
            task.status = LibraryOCRProcessTask.STATUS_SUCCESS
            task.batch_id = batch_id
            task.generated_json_file_ids = json_ids
            task.result_summary = {
                "generated_json_count": n_json,
                "skipped": skipped,
                "devices_count": len(devices) if isinstance(devices, list) else 0,
                "pipeline_keys": list(pipeline.keys()) if isinstance(pipeline, dict) else [],
                "images_count": len(image_data) if isinstance(image_data, dict) else 0,
            }
            task.save(
                update_fields=[
                    "status",
                    "batch_id",
                    "generated_json_file_ids",
                    "result_summary",
                    "updated_at",
                ]
            )
            if task.project_id and json_ids:
                attach_files_to_projects(json_ids, [task.project_id], task.created_by)
        except Exception as exc:
            task.status = LibraryOCRProcessTask.STATUS_FAILED
            task.error_message = str(exc)
            task.save(update_fields=["status", "error_message", "updated_at"])
        finally:
            close_old_connections()

    def post(self, request):
        if not role_has(request.user, "perm_file_library"):
            return Response(
                {"detail": "无权访问文件库"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not role_can_upload_library_category(request.user, LibraryFile.CATEGORY_UPLOAD):
            return Response(
                {"detail": "无权上传 OCR 文件"},
                status=status.HTTP_403_FORBIDDEN,
            )

        files = list(request.FILES.getlist("files"))
        if not files:
            one = request.FILES.get("file")
            if one:
                files = [one]
        if not files:
            return Response(
                {
                    "detail": "缺少上传文件。请使用 multipart 字段 `files`（多文件）或 `file`（单文件）。"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_project_ids = request.data.getlist("project_ids") if hasattr(request.data, "getlist") else []
        if not raw_project_ids:
            raw_one = (request.data.get("project_id") or "").strip() if hasattr(request.data, "get") else ""
            if raw_one:
                raw_project_ids = [raw_one]
        project_ids = parse_project_ids(raw_project_ids)
        if project_ids:
            active_project_ids = set(
                LibraryProject.objects.filter(pk__in=project_ids, is_active=True).values_list("id", flat=True)
            )
            if not active_project_ids:
                return Response({"detail": "project_id 无效"}, status=status.HTTP_400_BAD_REQUEST)
            if len(active_project_ids) > 1:
                return Response({"detail": "OCR 接口仅支持单项目上传"}, status=status.HTTP_400_BAD_REQUEST)
            project_id = next(iter(active_project_ids))
        else:
            project_id = None

        created, skipped = save_library_binary_uploads(
            request.user,
            files,
            LibraryFile.CATEGORY_UPLOAD,
            project_ids=[project_id] if project_id else [],
        )
        for row in created:
            pk = row["id"]
            row["urls"] = {
                "download": request.build_absolute_uri(
                    reverse("api_library_file_download", kwargs={"pk": pk})
                ),
            }

        task = LibraryOCRProcessTask.objects.create(
            created_by=request.user,
            project_id=project_id,
            source_file_ids=[row["id"] for row in created],
            status=LibraryOCRProcessTask.STATUS_PENDING,
        )
        threading.Thread(
            target=self._run_ocr_pipeline_task,
            args=(task.pk,),
            daemon=True,
        ).start()
        return Response(
            {
                "uploaded": created,
                "skipped": skipped,
                "count": len(created),
                "task": {
                    "id": task.pk,
                    "status": task.status,
                    "status_url": request.build_absolute_uri(
                        reverse("api_library_ocr_task_status", kwargs={"task_id": task.pk})
                    ),
                    "project_id": task.project_id,
                    "autofill_url": request.build_absolute_uri(
                        reverse("api_library_ocr_task_autofill", kwargs={"task_id": task.pk})
                    ),
                },
            },
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class LibraryOCRTaskStatusAPIView(APIView):
    """查询 OCR 异步流程任务状态。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_id: int):
        task = get_object_or_404(LibraryOCRProcessTask, pk=task_id)
        if task.created_by_id != request.user.id and not library_user_can_assign_tasks_to_participants(request.user):
            return Response({"detail": "无权查看该任务"}, status=status.HTTP_403_FORBIDDEN)
        files = list(
            LibraryFile.objects.filter(pk__in=task.generated_json_file_ids, category=LibraryFile.CATEGORY_JSON)
            .order_by("created_at")
            .values("id", "original_name", "size", "created_at")
        )
        for f in files:
            f["download_url"] = request.build_absolute_uri(
                reverse("api_library_file_download", kwargs={"pk": f["id"]})
            )
        return Response(
            {
                "id": task.pk,
                "status": task.status,
                "project_id": task.project_id,
                "batch_id": task.batch_id,
                "error_message": task.error_message,
                "result_summary": task.result_summary,
                "generated_json_files": files,
                "autofill_url": request.build_absolute_uri(
                    reverse("api_library_ocr_task_autofill", kwargs={"task_id": task.pk})
                ),
            }
        )


class LibraryOCRTaskAutofillAPIView(APIView):
    """返回 OCR 任务生成的首个 JSON 内容，用于前端“自动填写”按钮。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, task_id: int):
        task = get_object_or_404(LibraryOCRProcessTask, pk=task_id)
        if task.created_by_id != request.user.id and not library_user_can_assign_tasks_to_participants(request.user):
            return Response({"detail": "无权访问该任务"}, status=status.HTTP_403_FORBIDDEN)
        task.refresh_from_db()
        status_url = request.build_absolute_uri(
            reverse("api_library_ocr_task_status", kwargs={"task_id": task.pk})
        )
        if task.status != LibraryOCRProcessTask.STATUS_SUCCESS:
            if task.status == LibraryOCRProcessTask.STATUS_FAILED:
                detail = "任务处理失败，无可用自动填写数据"
            else:
                detail = "任务处理中，请稍后再试"
            return Response(
                {
                    "detail": detail,
                    "status": task.status,
                    "error_message": (task.error_message or "") or None,
                    "status_url": status_url,
                },
                status=status.HTTP_409_CONFLICT,
            )

        ids = list(task.generated_json_file_ids or [])
        if not ids and task.batch_id:
            recovered = list(
                LibraryFile.objects.filter(
                    category=LibraryFile.CATEGORY_JSON,
                    batch_id=task.batch_id,
                )
                .order_by("created_at")
                .values_list("id", flat=True)
            )
            if recovered:
                task.generated_json_file_ids = recovered
                task.save(update_fields=["generated_json_file_ids", "updated_at"])
                ids = recovered

        if not ids:
            return Response({"detail": "任务未生成 JSON 文件"}, status=status.HTTP_404_NOT_FOUND)

        by_pk = {
            lf.pk: lf
            for lf in LibraryFile.objects.filter(
                pk__in=ids,
                category=LibraryFile.CATEGORY_JSON,
            )
        }
        ordered_files = [by_pk[i] for i in ids if i in by_pk]
        if not ordered_files:
            return Response(
                {"detail": "关联的 JSON 文件记录已不存在", "status_url": status_url},
                status=status.HTTP_404_NOT_FOUND,
            )

        generated_json_files = []
        lf_payload = None
        payload = None
        last_read_error = None
        for lf in ordered_files:
            if not library_file_access_allowed(request.user, lf):
                continue
            generated_json_files.append(
                {
                    "id": lf.pk,
                    "name": lf.original_name,
                    "download_url": request.build_absolute_uri(
                        reverse("api_library_file_download", kwargs={"pk": lf.pk})
                    ),
                }
            )
            if lf_payload is not None:
                continue
            try:
                p = pipeline_service.library_absolute_path(lf.relative_path)
                text = p.read_text(encoding="utf-8", errors="replace")
                payload = json_std.loads(text)
                lf_payload = lf
            except (ValueError, OSError, json_std.JSONDecodeError) as exc:
                last_read_error = str(exc)
                continue

        if not generated_json_files:
            return Response({"detail": "无权访问生成的 JSON 文件"}, status=status.HTTP_403_FORBIDDEN)
        if lf_payload is None or payload is None:
            return Response(
                {
                    "detail": "JSON 文件读取或解析失败",
                    "last_error": last_read_error,
                    "status_url": status_url,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "task_id": task.pk,
                "ready": True,
                "json_file": {
                    "id": lf_payload.pk,
                    "name": lf_payload.original_name,
                    "download_url": request.build_absolute_uri(
                        reverse("api_library_file_download", kwargs={"pk": lf_payload.pk})
                    ),
                },
                "generated_json_files": generated_json_files,
                "payload": payload,
            }
        )


class LibraryFileDownloadAPIView(APIView):
    """
    使用 JWT 下载文件库中的文件（便于纯前端 SPA 携带 ``Authorization: Bearer``）。
    权限与 Web 端 ``/files/<pk>/download/`` 一致（含任务分配模板等规则）。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        lf = get_object_or_404(LibraryFile, pk=pk)
        if not role_has(request.user, "perm_file_download"):
            return Response(
                {"detail": "无权下载文件"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not library_file_access_allowed(request.user, lf):
            return Response(
                {"detail": "无权下载该文件"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return library_file_download_response(lf)


class LibraryTemplateDownloadListAPIView(APIView):
    """
    当前用户可下载的模板文件列表（templates 分类）。
    用于前端渲染“可下载模板”清单。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not role_has(request.user, "perm_file_library"):
            return Response({"detail": "无权访问文件库"}, status=status.HTTP_403_FORBIDDEN)
        if not role_has(request.user, "perm_file_download"):
            return Response({"detail": "无权下载文件"}, status=status.HTTP_403_FORBIDDEN)

        rows = (
            LibraryFile.objects.filter(category=LibraryFile.CATEGORY_TEMPLATE)
            .select_related("created_by")
            .order_by("-created_at")
        )
        data = []
        for lf in rows:
            if not library_file_access_allowed(request.user, lf):
                continue
            data.append(
                {
                    "id": lf.pk,
                    "original_name": lf.original_name,
                    "size": lf.size,
                    "created_at": lf.created_at.isoformat(),
                    "created_by": (
                        {
                            "id": lf.created_by_id,
                            "username": getattr(lf.created_by, "username", ""),
                        }
                        if lf.created_by_id
                        else None
                    ),
                    "download_url": request.build_absolute_uri(
                        reverse("api_library_file_download", kwargs={"pk": lf.pk})
                    ),
                    "prepare_pdf_url": (
                        request.build_absolute_uri(
                            reverse(
                                "api_library_template_prepare_pdf_download",
                                kwargs={"pk": lf.pk},
                            )
                        )
                        if lf.original_name.lower().endswith((".doc", ".docx"))
                        else None
                    ),
                }
            )
        return Response({"count": len(data), "results": data}, status=status.HTTP_200_OK)


class LibraryTemplatePreparePdfDownloadAPIView(APIView):
    """
    将模板 Word 转换为临时 PDF，并返回可下载链接。
    """

    permission_classes = [IsAuthenticated]

    def _prepare(self, request, pk):
        lf = get_object_or_404(
            LibraryFile,
            pk=pk,
            category=LibraryFile.CATEGORY_TEMPLATE,
        )
        if not role_has(request.user, "perm_file_download"):
            return Response({"detail": "无权下载文件"}, status=status.HTTP_403_FORBIDDEN)
        if not library_file_access_allowed(request.user, lf):
            return Response({"detail": "无权访问该模板"}, status=status.HTTP_403_FORBIDDEN)
        try:
            rel, output_name = convert_template_word_to_temp_pdf(lf, request.user.id)
        except ValueError:
            return Response({"detail": "该模板不是 Word 文件"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            # 服务器缺少 LibreOffice 依赖时降级为原文件下载，避免前端流程中断。
            return Response(
                {
                    "template_id": lf.pk,
                    "pdf_ready": False,
                    "detail": f"Word 转 PDF 失败: {exc}",
                    "fallback_download_url": request.build_absolute_uri(
                        reverse("api_library_file_download", kwargs={"pk": lf.pk})
                    ),
                },
                status=status.HTTP_200_OK,
            )

        token_payload = {"lf_id": lf.pk, "uid": request.user.id, "rel": rel}
        token = _TEMPLATE_PDF_SIGNER.sign_object(token_payload)
        return Response(
            {
                "template_id": lf.pk,
                "pdf_ready": True,
                "pdf_name": output_name,
                "download_url": request.build_absolute_uri(
                    reverse("api_library_template_temp_pdf_download") + f"?token={token}"
                ),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, pk):
        return self._prepare(request, pk)

    def get(self, request, pk):
        return self._prepare(request, pk)


class LibraryTemplateTempPdfDownloadAPIView(APIView):
    """下载由后端临时生成的模板 PDF。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        token = request.GET.get("token", "").strip()
        if not token:
            return Response({"detail": "缺少 token"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = _TEMPLATE_PDF_SIGNER.unsign_object(token, max_age=1800)
        except signing.BadSignature:
            return Response({"detail": "无效或过期的下载 token"}, status=status.HTTP_400_BAD_REQUEST)

        if payload.get("uid") != request.user.id:
            return Response({"detail": "下载 token 与当前用户不匹配"}, status=status.HTTP_403_FORBIDDEN)
        lf = get_object_or_404(
            LibraryFile,
            pk=payload.get("lf_id"),
            category=LibraryFile.CATEGORY_TEMPLATE,
        )
        if not role_has(request.user, "perm_file_download"):
            return Response({"detail": "无权下载文件"}, status=status.HTTP_403_FORBIDDEN)
        if not library_file_access_allowed(request.user, lf):
            return Response({"detail": "无权访问该模板"}, status=status.HTTP_403_FORBIDDEN)

        try:
            p = pipeline_service.library_absolute_path(payload.get("rel", ""))
        except ValueError:
            return Response({"detail": "临时文件路径无效"}, status=status.HTTP_400_BAD_REQUEST)
        if not p.is_file():
            return Response({"detail": "临时 PDF 不存在，请重新发起转换"}, status=status.HTTP_404_NOT_FOUND)
        return library_file_download_response(
            LibraryFile(
                original_name=p.name,
                relative_path=payload["rel"],
            )
        )


class AuthAPIView(APIView):
    """认证 API 视图"""
    permission_classes = [AllowAny]
    
    def post(self, request):
        """用户登录接口"""
        username = request.data.get('username')
        password = request.data.get('password')
        
        # 验证用户名和密码
        user = authenticate(request, username=username, password=password)
        if user:
            if getattr(settings, "AUTH_REVOKE_PRIOR_REFRESH_TOKENS_ON_LOGIN", True):
                revoke_user_refresh_tokens(user)
            # 生成 JWT Token
            refresh = RefreshToken.for_user(user)
            
            # 获取用户角色信息
            role_code = None
            role_name = None
            try:
                if hasattr(user, 'profile') and user.profile.role:
                    role_code = user.profile.role.code
                    role_name = user.profile.role.name
            except:
                pass
            
            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'role_code': role_code,
                    'role_name': role_name,
                }
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'error': '用户名或密码错误'
            }, status=status.HTTP_401_UNAUTHORIZED)


class TokenRefreshAPIView(APIView):
    """Token 刷新接口"""
    permission_classes = [AllowAny]
    
    def post(self, request):
        """刷新 Token"""
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({
                'error': '缺少 refresh token'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            refresh = RefreshToken(refresh_token)
            return Response({
                'access': str(refresh.access_token),
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({
                'error': 'Token 无效或已过期'
            }, status=status.HTTP_401_UNAUTHORIZED)


class UserViewSet(viewsets.ModelViewSet):
    """用户 API 视图集"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get_serializer_class(self):
        """根据操作选择序列化器"""
        if self.action == 'create':
            return UserCreateSerializer
        return UserSerializer
    
    def list(self, request):
        """用户列表"""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    def retrieve(self, request, pk=None):
        """获取单个用户"""
        user = self.get_object()
        serializer = self.get_serializer(user)
        return Response(serializer.data)
    
    def create(self, request):
        """创建用户"""
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            # 创建接口用 UserCreateSerializer，其 phone/role_code 等不在 User 模型上，
            # 若直接 serializer.data 会在 to_representation 时读错属性。
            return Response(
                UserSerializer(user, context={"request": request}).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def update(self, request, pk=None):
        """更新用户"""
        user = self.get_object()
        serializer = self.get_serializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def destroy(self, request, pk=None):
        """删除用户"""
        user = self.get_object()
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        """获取当前用户信息"""
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    """角色 API 视图集（只读）"""
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated]


class MenuViewSet(viewsets.ReadOnlyModelViewSet):
    """菜单 API 视图集（只读）"""
    queryset = Menu.objects.filter(is_visible=True)
    serializer_class = MenuSerializer
    permission_classes = [IsAuthenticated]
    
    def list(self, request):
        """获取菜单列表"""
        # 获取顶级菜单
        menus = Menu.objects.filter(parent=None, is_visible=True).prefetch_related('children')
        
        # 根据用户角色过滤
        if not request.user.is_superuser:
            try:
                role = request.user.profile.role
                menus = menus.filter(roles=role)
            except:
                menus = []
        
        serializer = self.get_serializer(menus, many=True)
        return Response(serializer.data)
