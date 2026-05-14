"""
Django Admin 配置
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from apps.core.models import (
    BizContact,
    BizDevice,
    InspectionCase,
    InspectionCaseWorkflowState,
    InspectedOrganization,
    LibraryFile,
    LibraryFileTask,
    LibraryProject,
    LibraryProjectWorkflowMember,
    LibraryOCRProcessTask,
    LibraryTask,
    LibraryTaskAssignment,
    Menu,
    Report,
    Role,
    SiteRecord,
    UserProfile,
)


class UserProfileInline(admin.StackedInline):
    """用户资料内联"""
    model = UserProfile
    can_delete = False
    verbose_name_plural = '用户资料'


class CustomUserAdmin(UserAdmin):
    """自定义用户管理"""
    inlines = (UserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'is_active')
    list_filter = ('is_staff', 'is_active', 'groups')


class RoleAdmin(admin.ModelAdmin):
    """角色管理"""
    list_display = ('name', 'code', 'description', 'created_at')
    list_filter = ('code',)
    search_fields = ('name', 'code')


class MenuAdmin(admin.ModelAdmin):
    """菜单管理"""
    list_display = ('name', 'path', 'icon', 'parent', 'sort_order', 'is_visible')
    list_filter = ('is_visible', 'parent')
    search_fields = ('name', 'path')
    list_editable = ('sort_order', 'is_visible')


class LibraryFileAdmin(admin.ModelAdmin):
    list_display = (
        'original_name',
        'category',
        'link_entity',
        'link_object_id',
        'content_sha256',
        'batch_id',
        'size',
        'created_by',
        'created_at',
    )
    list_filter = ('category', 'link_entity')
    search_fields = ('original_name', 'relative_path', 'batch_id', 'content_sha256')


class LibraryFileTaskInline(admin.TabularInline):
    model = LibraryFileTask
    extra = 0


class LibraryTaskAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "output_target", "created_by", "created_at")
    list_filter = ("output_target",)
    search_fields = ("code", "name")
    inlines = (LibraryFileTaskInline,)
    filter_horizontal = ("report_source_tasks",)


class LibraryTaskAssignmentAdmin(admin.ModelAdmin):
    list_display = ("library_task", "project", "assignee", "assigned_by", "created_at")
    list_filter = ("library_task", "project")
    search_fields = ("assignee__username", "assigned_by__username", "library_task__code", "library_task__name")


class LibraryOCRProcessTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "status", "created_by", "batch_id", "created_at", "updated_at")
    list_filter = ("status", "project")
    search_fields = ("id", "batch_id", "created_by__username")


class InspectedOrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "credit_code", "created_at")
    search_fields = ("name", "address", "credit_code")


class BizContactAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "organization", "created_at")
    search_fields = ("name", "phone")


class BizDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "model", "serial_no", "manufacturer", "organization", "created_at")
    search_fields = ("name", "model", "serial_no")


class InspectionCaseAdmin(admin.ModelAdmin):
    list_display = ("case_no", "inspected_organization", "library_project", "primary_contact", "created_at")
    search_fields = ("case_no", "notes")
    filter_horizontal = ("devices",)


class SiteRecordAdmin(admin.ModelAdmin):
    list_display = ("record_no", "case", "library_task", "record_date", "created_at")
    search_fields = ("record_no",)


class ReportAdmin(admin.ModelAdmin):
    list_display = ("report_no", "case", "site_record", "library_task", "status", "version", "created_at")
    search_fields = ("report_no",)


class LibraryProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_by", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    filter_horizontal = ("library_tasks",)


class LibraryProjectWorkflowMemberAdmin(admin.ModelAdmin):
    list_display = ("project", "user", "workflow_role", "updated_at")
    list_filter = ("workflow_role", "project")
    search_fields = ("user__username", "project__code")


class InspectionCaseWorkflowStateAdmin(admin.ModelAdmin):
    list_display = ("case", "stage", "issue_date", "updated_by", "updated_at")
    list_filter = ("stage",)
    search_fields = ("case__case_no", "return_reason")


# 重新注册 User 模型
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)

# 注册自定义模型（UserProfile 通过 User 内联编辑，含「文件库容量配额」等字段）
admin.site.register(Role, RoleAdmin)
admin.site.register(Menu, MenuAdmin)
admin.site.register(LibraryFile, LibraryFileAdmin)
admin.site.register(LibraryTask, LibraryTaskAdmin)
admin.site.register(LibraryTaskAssignment, LibraryTaskAssignmentAdmin)
admin.site.register(LibraryOCRProcessTask, LibraryOCRProcessTaskAdmin)
admin.site.register(LibraryProject, LibraryProjectAdmin)
admin.site.register(LibraryProjectWorkflowMember, LibraryProjectWorkflowMemberAdmin)
admin.site.register(InspectionCaseWorkflowState, InspectionCaseWorkflowStateAdmin)
admin.site.register(InspectedOrganization, InspectedOrganizationAdmin)
admin.site.register(BizContact, BizContactAdmin)
admin.site.register(BizDevice, BizDeviceAdmin)
admin.site.register(InspectionCase, InspectionCaseAdmin)
admin.site.register(SiteRecord, SiteRecordAdmin)
admin.site.register(Report, ReportAdmin)
