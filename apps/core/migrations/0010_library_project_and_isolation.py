# 项目管理 + 文件项目关联 + 任务/流程任务绑定项目

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0009_biz_registry_and_report_category"),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryProject",
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
                ("code", models.CharField(db_index=True, max_length=64, unique=True, verbose_name="项目编码")),
                ("name", models.CharField(db_index=True, max_length=128, verbose_name="项目名称")),
                ("description", models.CharField(blank=True, default="", max_length=255, verbose_name="描述")),
                ("is_active", models.BooleanField(default=True, verbose_name="启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="library_projects",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
            ],
            options={
                "verbose_name": "文件库项目",
                "verbose_name_plural": "文件库项目",
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="LibraryFileProject",
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
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="library_file_project_links",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="关联人",
                    ),
                ),
                (
                    "library_file",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_links",
                        to="core.libraryfile",
                        verbose_name="文件",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="file_links",
                        to="core.libraryproject",
                        verbose_name="项目",
                    ),
                ),
            ],
            options={
                "verbose_name": "文件项目关联",
                "verbose_name_plural": "文件项目关联",
            },
        ),
        migrations.AddField(
            model_name="libraryfile",
            name="projects",
            field=models.ManyToManyField(
                blank=True,
                related_name="library_files",
                through="core.LibraryFileProject",
                to="core.libraryproject",
                verbose_name="关联项目",
            ),
        ),
        migrations.AddConstraint(
            model_name="libraryfileproject",
            constraint=models.UniqueConstraint(
                fields=("library_file", "project"),
                name="uniq_library_file_project",
            ),
        ),
        migrations.AddField(
            model_name="libraryocrprocesstask",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ocr_tasks",
                to="core.libraryproject",
                verbose_name="项目",
            ),
        ),
        migrations.AddField(
            model_name="librarytaskassignment",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="task_assignments",
                to="core.libraryproject",
                verbose_name="项目",
            ),
        ),
    ]
