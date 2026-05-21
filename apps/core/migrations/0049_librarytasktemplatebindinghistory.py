import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0048_commissionorgequipment_device_type_bindings"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryTaskTemplateBindingHistory",
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
                ("file_id_snapshot", models.PositiveIntegerField(verbose_name="文件 ID 快照")),
                (
                    "original_name_snapshot",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=512,
                        verbose_name="文件名快照",
                    ),
                ),
                (
                    "file_role",
                    models.CharField(
                        choices=[("pdf", "PDF 模板"), ("json", "JSON 模板")],
                        db_index=True,
                        max_length=8,
                        verbose_name="模板角色",
                    ),
                ),
                (
                    "replaced_at",
                    models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="替换时间"),
                ),
                (
                    "source",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="如 editor_export_json、restore_history",
                        max_length=64,
                        verbose_name="来源",
                    ),
                ),
                (
                    "library_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="template_binding_history_rows",
                        to="core.libraryfile",
                        verbose_name="被替换的模板文件",
                    ),
                ),
                (
                    "library_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="template_binding_history",
                        to="core.librarytask",
                        verbose_name="任务模板",
                    ),
                ),
                (
                    "replaced_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="template_binding_history_actions",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="操作人",
                    ),
                ),
                (
                    "replaced_by_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.libraryfile",
                        verbose_name="替换为的新文件",
                    ),
                ),
            ],
            options={
                "verbose_name": "任务模板绑定历史",
                "verbose_name_plural": "任务模板绑定历史",
                "ordering": ["-replaced_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="librarytasktemplatebindinghistory",
            index=models.Index(
                fields=["library_task", "file_role", "-replaced_at"],
                name="core_libtas_library_8a1f2d_idx",
            ),
        ),
    ]
