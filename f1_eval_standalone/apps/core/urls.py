"""F.1 预评价报告表制作路由（与真实后台 apps/core/urls.py 中同名段落逐条一致）。

回嵌真实后台时：把「F.1 预评价报告表制作」区块整段拷回真实 urls.py 即可，
URL 名称必须保持不变（模板 f1_eval_workbench.html 通过 {% url %} 引用）。
dashboard 一条仅为独立版兜底（views 权限不足时 redirect(reverse("dashboard"))）。
"""
from django.urls import path
from django.views.generic import RedirectView

from apps.core import f1_eval_views

urlpatterns = [
    # 独立版首页 / dashboard 兜底：真实后台已有 dashboard，回嵌时删除本行
    path('', RedirectView.as_view(pattern_name='f1_eval_workbench', permanent=False), name='dashboard'),

    # F.1 预评价报告表制作
    path('files/f1-eval/', f1_eval_views.f1_eval_workbench, name='f1_eval_workbench'),
    path('files/f1-eval/api/section/', f1_eval_views.f1_eval_api_section, name='f1_eval_api_section'),
    path('files/f1-eval/api/save-section/', f1_eval_views.f1_eval_api_save_section, name='f1_eval_api_save_section'),
    path('files/f1-eval/api/table/', f1_eval_views.f1_eval_api_table, name='f1_eval_api_table'),
    path('files/f1-eval/api/save-table/', f1_eval_views.f1_eval_api_save_table, name='f1_eval_api_save_table'),
    path('files/f1-eval/api/table-op/', f1_eval_views.f1_eval_api_table_op, name='f1_eval_api_table_op'),
    path('files/f1-eval/api/add-table/', f1_eval_views.f1_eval_api_add_table, name='f1_eval_api_add_table'),
    path('files/f1-eval/api/delete-table/', f1_eval_views.f1_eval_api_delete_table, name='f1_eval_api_delete_table'),
    path('files/f1-eval/api/import-excel/', f1_eval_views.f1_eval_api_import_excel, name='f1_eval_api_import_excel'),
    path('files/f1-eval/api/export-excel/', f1_eval_views.f1_eval_api_export_excel, name='f1_eval_api_export_excel'),
    path('files/f1-eval/api/upload-figure/', f1_eval_views.f1_eval_api_upload_figure, name='f1_eval_api_upload_figure'),
    path('files/f1-eval/api/save-figure-caption/', f1_eval_views.f1_eval_api_save_figure_caption, name='f1_eval_api_save_figure_caption'),
    path('files/f1-eval/api/delete-figure/', f1_eval_views.f1_eval_api_delete_figure, name='f1_eval_api_delete_figure'),
    path('files/f1-eval/api/file/', f1_eval_views.f1_eval_api_file, name='f1_eval_api_file'),
    path('files/f1-eval/api/save-file/', f1_eval_views.f1_eval_api_save_file, name='f1_eval_api_save_file'),
    path('files/f1-eval/api/common-templates/', f1_eval_views.f1_eval_api_common_templates, name='f1_eval_api_common_templates'),
    path('files/f1-eval/api/reset-common-template/', f1_eval_views.f1_eval_api_reset_common_template, name='f1_eval_api_reset_common_template'),
    path('files/f1-eval/api/upload-common-template/', f1_eval_views.f1_eval_api_upload_common_template, name='f1_eval_api_upload_common_template'),
    path('files/f1-eval/media/common-template/', f1_eval_views.f1_eval_common_template_media, name='f1_eval_common_template_media'),
    path('files/f1-eval/api/save-format/', f1_eval_views.f1_eval_api_save_format_fields, name='f1_eval_api_save_format_fields'),
    path('files/f1-eval/api/preview-format/', f1_eval_views.f1_eval_api_preview_format, name='f1_eval_api_preview_format'),
    path('files/f1-eval/api/table-grid/', f1_eval_views.f1_eval_api_table_grid, name='f1_eval_api_table_grid'),
    path('files/f1-eval/api/upload-info-sheet/', f1_eval_views.f1_eval_api_upload_info_sheet, name='f1_eval_api_upload_info_sheet'),
    path('files/f1-eval/api/cover-meta/', f1_eval_views.f1_eval_api_cover_meta, name='f1_eval_api_cover_meta'),
    path('files/f1-eval/api/save-cover-meta/', f1_eval_views.f1_eval_api_save_cover_meta, name='f1_eval_api_save_cover_meta'),
    path('files/f1-eval/api/upload-cover-certificate/', f1_eval_views.f1_eval_api_upload_cover_certificate, name='f1_eval_api_upload_cover_certificate'),
    path('files/f1-eval/api/upload-attachments/', f1_eval_views.f1_eval_api_upload_attachments, name='f1_eval_api_upload_attachments'),
    path('files/f1-eval/api/attachment-album/', f1_eval_views.f1_eval_api_attachment_album, name='f1_eval_api_attachment_album'),
    path('files/f1-eval/api/update-attachment/', f1_eval_views.f1_eval_api_update_attachment, name='f1_eval_api_update_attachment'),
    path('files/f1-eval/api/delete-attachment/', f1_eval_views.f1_eval_api_delete_attachment, name='f1_eval_api_delete_attachment'),
    path('files/f1-eval/api/generate/', f1_eval_views.f1_eval_api_generate, name='f1_eval_api_generate'),
    path('files/f1-eval/api/projects/', f1_eval_views.f1_eval_api_project_list, name='f1_eval_api_project_list'),
    path('files/f1-eval/api/projects/create/', f1_eval_views.f1_eval_api_project_create, name='f1_eval_api_project_create'),
    path('files/f1-eval/api/projects/save/', f1_eval_views.f1_eval_api_project_save, name='f1_eval_api_project_save'),
    path('files/f1-eval/api/projects/open/', f1_eval_views.f1_eval_api_project_open, name='f1_eval_api_project_open'),
    path('files/f1-eval/media/attachments/<str:filename>', f1_eval_views.f1_eval_attachment_media, name='f1_eval_attachment_media'),
    path('files/f1-eval/media/assets/<str:filename>', f1_eval_views.f1_eval_asset_media, name='f1_eval_asset_media'),
    path('files/f1-eval/download/<str:kind>/', f1_eval_views.f1_eval_download, name='f1_eval_download'),
]

