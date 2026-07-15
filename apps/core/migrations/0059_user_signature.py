# 用户手写签名主档与使用/变更审计

import apps.core.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0058_commission_org_unique_active_only"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserSignature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("inspector", "检测员（参与主要检测人员）"),
                            ("checker", "校核员"),
                            ("accompanyingPerson", "受检单位陪同人"),
                        ],
                        db_index=True,
                        max_length=32,
                        verbose_name="签名角色",
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        upload_to=apps.core.models.user_signature_image_upload_to,
                        verbose_name="签名图片",
                    ),
                ),
                (
                    "content_sha256",
                    models.CharField(blank=True, db_index=True, default="", max_length=64, verbose_name="内容哈希"),
                ),
                (
                    "original_filename",
                    models.CharField(blank=True, default="", max_length=255, verbose_name="原始文件名"),
                ),
                ("version", models.PositiveIntegerField(default=1, verbose_name="版本号")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="user_signature_uploads",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="上传操作人",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_signatures",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "用户签名",
                "verbose_name_plural": "用户签名",
                "ordering": ["user_id", "role", "-version"],
            },
        ),
        migrations.CreateModel(
            name="UserSignatureEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("inspector", "检测员（参与主要检测人员）"),
                            ("checker", "校核员"),
                            ("accompanyingPerson", "受检单位陪同人"),
                        ],
                        db_index=True,
                        max_length=32,
                        verbose_name="签名角色",
                    ),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("uploaded", "首次上传"),
                            ("replaced", "重新上传/替换"),
                            ("used_site_record", "用于现场记录"),
                            ("used_report", "用于报告"),
                            ("used_submit", "用于检测提交"),
                        ],
                        db_index=True,
                        max_length=32,
                        verbose_name="事件类型",
                    ),
                ),
                (
                    "task_no",
                    models.CharField(blank=True, db_index=True, default="", max_length=64, verbose_name="任务编号"),
                ),
                ("task_code", models.CharField(blank=True, default="", max_length=128, verbose_name="任务模板代码")),
                ("output_target", models.CharField(blank=True, default="", max_length=32, verbose_name="产出类型")),
                (
                    "snapshot_path",
                    models.CharField(blank=True, default="", max_length=512, verbose_name="使用时的文件路径"),
                ),
                (
                    "content_sha256",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="内容哈希快照"),
                ),
                ("detail", models.JSONField(blank=True, default=dict, verbose_name="附加信息")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="发生时间")),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="signature_event_actions",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="操作人",
                    ),
                ),
                (
                    "case",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="signature_events",
                        to="core.inspectioncase",
                        verbose_name="检测案件",
                    ),
                ),
                (
                    "library_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="signature_events",
                        to="core.libraryfile",
                        verbose_name="关联文件",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="signature_events",
                        to="core.libraryproject",
                        verbose_name="项目",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="signature_events",
                        to="core.inspectionsubmission",
                        verbose_name="检测提交",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="signature_events",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="签名所属用户",
                    ),
                ),
                (
                    "user_signature",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="events",
                        to="core.usersignature",
                        verbose_name="签名版本",
                    ),
                ),
            ],
            options={
                "verbose_name": "用户签名事件",
                "verbose_name_plural": "用户签名事件",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="usersignature",
            index=models.Index(fields=["user", "role", "-updated_at"], name="core_usersig_user_role_upd"),
        ),
        migrations.AddConstraint(
            model_name="usersignature",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("user", "role"),
                name="uniq_user_signature_role_active",
            ),
        ),
        migrations.AddIndex(
            model_name="usersignatureevent",
            index=models.Index(fields=["user", "-created_at"], name="core_usersigev_user_created"),
        ),
        migrations.AddIndex(
            model_name="usersignatureevent",
            index=models.Index(fields=["user", "event_type", "-created_at"], name="core_usersigev_user_type_cr"),
        ),
    ]
