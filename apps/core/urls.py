"""
Web 路由配置
前后端不分离的页面路由
"""
from django.urls import path
from apps.core import views

urlpatterns = [
    path('static/preview/<str:name>', views.pipeline_preview_static, name='pipeline_preview_static'),
    # 认证路由
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # 主页和仪表盘
    path('', views.dashboard, name='dashboard'),
    
    # 用户管理路由
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:user_id>/delete/', views.user_delete, name='user_delete'),
    
    # 角色管理路由
    path('roles/', views.role_list, name='role_list'),
    path('roles/create/', views.role_create, name='role_create'),
    path('roles/<int:role_id>/edit/', views.role_edit, name='role_edit'),
    path('roles/<int:role_id>/delete/', views.role_delete, name='role_delete'),
    
    # 菜单管理路由
    path('menus/', views.menu_list, name='menu_list'),
    path('menus/create/', views.menu_create, name='menu_create'),
    path('menus/<int:menu_id>/edit/', views.menu_edit, name='menu_edit'),
    path('menus/<int:menu_id>/delete/', views.menu_delete, name='menu_delete'),

    # 文件库与处理流程
    path('files/', views.file_library, name='file_library'),
    path('files/projects/', views.library_projects, name='library_projects'),
    path('files/task-management/', views.library_task_management, name='library_task_management'),
    path('files/library-tasks/', views.redirect_to_task_management),
    path('files/tasks/', views.redirect_to_task_management),
    path('files/<int:pk>/delete/', views.file_library_delete, name='file_library_delete'),
    path('files/batch-delete/', views.file_library_batch_delete, name='file_library_batch_delete'),
    path('files/<int:pk>/preview/', views.file_preview, name='file_preview'),
    path('files/<int:pk>/raw/', views.file_library_raw, name='file_library_raw'),
    path('files/<int:pk>/download/', views.file_library_download, name='file_library_download'),
    path('files/process/', views.process_pipeline, name='process_pipeline'),
    path('files/htmlpdf/', views.htmlpdf_editor, name='htmlpdf_editor'),
    path('files/htmlpdf/editor/<int:pk>/', views.htmlpdf_editor_open, name='htmlpdf_editor_open'),
    path('files/htmlpdf/template/<int:pk>/', views.htmlpdf_template_file, name='htmlpdf_template_file'),
    path('files/htmlpdf/api/template-pdfs/', views.htmlpdf_api_template_pdfs, name='htmlpdf_api_template_pdfs'),
    path('files/htmlpdf/api/template-jsons/', views.htmlpdf_api_template_jsons, name='htmlpdf_api_template_jsons'),
    path('files/htmlpdf/api/import-json-from-library/', views.htmlpdf_api_import_json_from_library, name='htmlpdf_api_import_json_from_library'),
    path('files/htmlpdf/api/use-template-pdf/', views.htmlpdf_api_use_template_pdf, name='htmlpdf_api_use_template_pdf'),
    path('files/htmlpdf/api/upload-pdf/', views.htmlpdf_api_upload_pdf, name='htmlpdf_api_upload_pdf'),
    path('files/htmlpdf/api/import-json/', views.htmlpdf_api_import_json, name='htmlpdf_api_import_json'),
    path('files/htmlpdf/api/export-json/', views.htmlpdf_api_export_json, name='htmlpdf_api_export_json'),
    path('files/htmlpdf/api/save-pdf/', views.htmlpdf_api_save_pdf, name='htmlpdf_api_save_pdf'),
    path('files/temp/<str:batch_id>/', views.file_temp_batch, name='file_temp_batch'),
]
