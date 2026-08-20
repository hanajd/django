from django.contrib import admin

from .models import CommissionOrganization, LibraryFile


@admin.register(CommissionOrganization)
class CommissionOrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "level", "is_active")
    list_filter = ("level", "is_active")
    search_fields = ("name",)


@admin.register(LibraryFile)
class LibraryFileAdmin(admin.ModelAdmin):
    list_display = ("original_name", "category", "size", "link_entity", "created_at")
    list_filter = ("category",)
    search_fields = ("original_name", "relative_path")
