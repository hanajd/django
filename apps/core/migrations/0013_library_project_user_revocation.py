# 项目维度取消 App 用户编辑权限（仍可下载）

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0012_library_file_task"),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryProjectUserRevocation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("revoked_at", models.DateTimeField(auto_now_add=True, verbose_name="取消时间")),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_revocations",
                        to="core.libraryproject",
                        verbose_name="项目",
                    ),
                ),
                (
                    "revoked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="library_project_revocations_made",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="操作人",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="library_project_revocations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "项目用户编辑取消",
                "verbose_name_plural": "项目用户编辑取消",
                "ordering": ["-revoked_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="libraryprojectuserrevocation",
            constraint=models.UniqueConstraint(
                fields=("project", "user"),
                name="uniq_library_project_user_revocation",
            ),
        ),
    ]
