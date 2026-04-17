"""
API 路由配置
提供 /api/v1/ 前缀的接口
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from apps.api.api_views import (
    AuthAPIView,
    LibraryFileDownloadAPIView,
    LibraryOCRTaskAutofillAPIView,
    LibraryOCRTaskStatusAPIView,
    LibraryTemplatePreparePdfDownloadAPIView,
    LibraryTemplateTempPdfDownloadAPIView,
    LibraryTemplateDownloadListAPIView,
    LibraryOCRUploadAPIView,
    MenuViewSet,
    RoleViewSet,
    TokenRefreshAPIView,
    UserViewSet,
)
from apps.api.inspection_views import (
    InspectionDetailAPIView,
    InspectionDraftAPIView,
    InspectionHistoryAPIView,
    InspectionPendingAPIView,
    InspectionSignatureDownloadAPIView,
    InspectionStartAPIView,
    InspectionSubmitAPIView,
    InspectionSubmitByTaskAPIView,
    InspectionTaskOCRAutofillAPIView,
    InspectionTaskOCRStatusAPIView,
    InspectionTaskOCRUploadAPIView,
    InspectionTaskFileDownloadAPIView,
    InspectionTaskFileListAPIView,
    InspectionTaskFileUploadAPIView,
)
from apps.api.registry_views import (
    BizContactViewSet,
    BizDeviceViewSet,
    InspectionCaseViewSet,
    InspectedOrganizationViewSet,
    RegistryLinkedFileUploadAPIView,
    ReportViewSet,
    SiteRecordViewSet,
)

# 创建路由器
router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'roles', RoleViewSet, basename='role')
router.register(r'menus', MenuViewSet, basename='menu')
router.register(
    r'registry/organizations',
    InspectedOrganizationViewSet,
    basename='registry-organization',
)
router.register(r'registry/contacts', BizContactViewSet, basename='registry-contact')
router.register(r'registry/devices', BizDeviceViewSet, basename='registry-device')
router.register(r'registry/cases', InspectionCaseViewSet, basename='registry-case')
router.register(r'registry/site-records', SiteRecordViewSet, basename='registry-site-record')
router.register(r'registry/reports', ReportViewSet, basename='registry-report')

urlpatterns = [
    # 认证接口
    path('auth/login/', AuthAPIView.as_view(), name='api_login'),
    path('auth/token/refresh/', TokenRefreshAPIView.as_view(), name='api_token_refresh'),
    path(
        "library/files/ocr/",
        LibraryOCRUploadAPIView.as_view(),
        name="api_library_ocr_upload",
    ),
    path(
        "library/files/<int:pk>/download/",
        LibraryFileDownloadAPIView.as_view(),
        name="api_library_file_download",
    ),
    path(
        "library/templates/downloadable/",
        LibraryTemplateDownloadListAPIView.as_view(),
        name="api_library_template_download_list",
    ),
    path(
        "library/templates/<int:pk>/prepare-pdf-download/",
        LibraryTemplatePreparePdfDownloadAPIView.as_view(),
        name="api_library_template_prepare_pdf_download",
    ),
    path(
        "library/templates/temp-pdf-download/",
        LibraryTemplateTempPdfDownloadAPIView.as_view(),
        name="api_library_template_temp_pdf_download",
    ),
    path(
        "library/files/ocr/tasks/<int:task_id>/status/",
        LibraryOCRTaskStatusAPIView.as_view(),
        name="api_library_ocr_task_status",
    ),
    path(
        "library/files/ocr/tasks/<int:task_id>/autofill/",
        LibraryOCRTaskAutofillAPIView.as_view(),
        name="api_library_ocr_task_autofill",
    ),
    path(
        "library/files/upload-linked/",
        RegistryLinkedFileUploadAPIView.as_view(),
        name="api_library_file_upload_linked",
    ),
    path(
        "inspections/submit",
        InspectionSubmitAPIView.as_view(),
        name="api_inspection_submit",
    ),
    path(
        "inspections/pending",
        InspectionPendingAPIView.as_view(),
        name="api_inspection_pending",
    ),
    path(
        "inspections/history",
        InspectionHistoryAPIView.as_view(),
        name="api_inspection_history",
    ),
    path(
        "inspections/<str:task_no>",
        InspectionDetailAPIView.as_view(),
        name="api_inspection_detail",
    ),
    path(
        "inspections/<str:task_no>/start",
        InspectionStartAPIView.as_view(),
        name="api_inspection_start",
    ),
    path(
        "inspections/<str:task_no>/draft",
        InspectionDraftAPIView.as_view(),
        name="api_inspection_draft",
    ),
    path(
        "inspections/<str:task_no>/submit",
        InspectionSubmitByTaskAPIView.as_view(),
        name="api_inspection_submit_by_task",
    ),
    path(
        "inspections/<str:task_no>/signatures/<str:role>",
        InspectionSignatureDownloadAPIView.as_view(),
        name="api_inspection_signature_download",
    ),
    path(
        "inspections/<str:task_no>/files/upload",
        InspectionTaskFileUploadAPIView.as_view(),
        name="api_inspection_task_file_upload",
    ),
    path(
        "inspections/<str:task_no>/ocr/upload",
        InspectionTaskOCRUploadAPIView.as_view(),
        name="api_inspection_task_ocr_upload",
    ),
    path(
        "inspections/<str:task_no>/ocr/tasks/<int:ocr_task_id>/status",
        InspectionTaskOCRStatusAPIView.as_view(),
        name="api_inspection_task_ocr_status",
    ),
    path(
        "inspections/<str:task_no>/ocr/tasks/<int:ocr_task_id>/autofill",
        InspectionTaskOCRAutofillAPIView.as_view(),
        name="api_inspection_task_ocr_autofill",
    ),
    path(
        "inspections/<str:task_no>/files/<str:category>",
        InspectionTaskFileListAPIView.as_view(),
        name="api_inspection_task_file_list",
    ),
    path(
        "inspections/<str:task_no>/files/<int:pk>/download/<str:category>",
        InspectionTaskFileDownloadAPIView.as_view(),
        name="api_inspection_task_file_download",
    ),
    
    # API 视图集路由
    path('', include(router.urls)),
]
