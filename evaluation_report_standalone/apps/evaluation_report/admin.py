from django.contrib import admin

from .models import (
    EvaluationKeywordSchema,
    EvaluationLatexTemplate,
    EvaluationReport,
    EvaluationReportKeyword,
    EvaluationReportUpload,
    EvaluationReportUploadFile,
)


class EvaluationReportUploadFileInline(admin.TabularInline):
    model = EvaluationReportUploadFile
    extra = 0
    raw_id_fields = ("library_file",)
    fields = ("library_file", "sort_order", "created_at")
    readonly_fields = ("created_at",)


class EvaluationReportUploadInline(admin.TabularInline):
    model = EvaluationReportUpload
    extra = 0
    readonly_fields = ("slot_key", "label", "library_category", "sort_order", "required")
    fields = ("slot_key", "label", "appendix_section", "appendix_anchor", "preview_md", "preview_tex", "required", "sort_order")
    show_change_link = True


class EvaluationReportKeywordInline(admin.TabularInline):
    model = EvaluationReportKeyword
    extra = 0
    fields = ("command", "value")


@admin.register(EvaluationLatexTemplate)
class EvaluationLatexTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "source", "report_subtype", "is_system", "has_main_tex", "updated_at")
    list_filter = ("source", "is_system", "report_subtype")
    search_fields = ("name", "key", "description")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(boolean=True, description="main.tex")
    def has_main_tex(self, obj):
        return obj.has_main_tex


@admin.register(EvaluationReport)
class EvaluationReportAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "hospital",
        "report_subtype",
        "latex_template_key",
        "status",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "report_subtype", "latex_template_key")
    search_fields = ("title", "hospital__name", "error_message")
    readonly_fields = ("status", "convert_log", "compile_log", "error_message", "work_subdir", "created_at", "updated_at")
    raw_id_fields = ("hospital",)
    inlines = (EvaluationReportUploadInline, EvaluationReportKeywordInline)


@admin.register(EvaluationReportUpload)
class EvaluationReportUploadAdmin(admin.ModelAdmin):
    list_display = ("report", "slot_key", "label", "appendix_section", "sort_order", "required")
    list_filter = ("library_category", "required")
    search_fields = ("label", "slot_key", "report__title")
    raw_id_fields = ("report",)
    inlines = (EvaluationReportUploadFileInline,)


@admin.register(EvaluationReportUploadFile)
class EvaluationReportUploadFileAdmin(admin.ModelAdmin):
    list_display = ("upload", "library_file", "sort_order", "created_at")
    list_filter = ("upload__slot_key",)
    search_fields = ("upload__label", "library_file__original_name")
    raw_id_fields = ("upload", "library_file")


@admin.register(EvaluationKeywordSchema)
class EvaluationKeywordSchemaAdmin(admin.ModelAdmin):
    list_display = ("command", "label_cn", "updated_at")
    search_fields = ("command", "label_cn")


@admin.register(EvaluationReportKeyword)
class EvaluationReportKeywordAdmin(admin.ModelAdmin):
    list_display = ("report", "command", "value", "updated_at")
    list_filter = ("command",)
    search_fields = ("report__title", "command", "value")
    raw_id_fields = ("report",)
