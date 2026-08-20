"""Thin models that satisfy evaluation_report FK / constants without tablet_backend."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import gettext_lazy as _


class ActiveLibraryFileManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class LibraryFile(models.Model):
    CATEGORY_UPLOAD = "upload"
    CATEGORY_JSON = "json"
    CATEGORY_TEMP = "temp"
    CATEGORY_TEMPLATE = "template"
    CATEGORY_SITE_RECORD = "site_record"
    CATEGORY_REPORT = "report"
    CATEGORY_ATTACHMENT = "attachment"
    CATEGORY_EVALUATION_FORM = "evaluation_form"
    CATEGORY_INSPECTION_SUBMIT = "inspection_submit"
    CATEGORY_CHOICES = (
        (CATEGORY_UPLOAD, _("OCR文件")),
        (CATEGORY_JSON, _("JSON 文件")),
        (CATEGORY_TEMPLATE, _("模板")),
        (CATEGORY_SITE_RECORD, _("现场记录")),
        (CATEGORY_REPORT, _("报告")),
        (CATEGORY_ATTACHMENT, _("附件")),
        (CATEGORY_EVALUATION_FORM, _("评价信息表")),
        (CATEGORY_INSPECTION_SUBMIT, _("检测提交")),
        (CATEGORY_TEMP, _("临时文件")),
    )

    LINK_ENTITY_NONE = ""
    LINK_ENTITY_EVALUATION_REPORT = "evaluation_report"

    original_name = models.CharField(max_length=255, verbose_name=_("原始文件名"))
    relative_path = models.CharField(max_length=512, verbose_name=_("库内相对路径"))
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        db_index=True,
        verbose_name=_("分类"),
    )
    batch_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    content_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    size = models.BigIntegerField(default=0)
    link_entity = models.CharField(max_length=32, blank=True, default="", db_index=True)
    link_object_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="library_files",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = ActiveLibraryFileManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = _("文件库文件")
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_category_display()}: {self.original_name}"


class CommissionOrganization(models.Model):
    LEVEL_HOSPITAL = "hospital"
    LEVEL_CAMPUS = "campus"
    LEVEL_DEPARTMENT = "department"
    LEVEL_CHOICES = (
        (LEVEL_HOSPITAL, _("医院")),
        (LEVEL_CAMPUS, _("院区")),
        (LEVEL_DEPARTMENT, _("科室")),
    )

    level = models.CharField(
        max_length=16,
        choices=LEVEL_CHOICES,
        default=LEVEL_HOSPITAL,
        db_index=True,
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    name = models.CharField(max_length=255, db_index=True)
    notes = models.CharField(max_length=500, blank=True, default="")
    address = models.CharField(max_length=512, blank=True, default="")
    introduction = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("委托单位/医院")
        verbose_name_plural = verbose_name
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
