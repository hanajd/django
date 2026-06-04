from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_default_task_folders(apps, schema_editor):
    LibraryTaskFolder = apps.get_model("core", "LibraryTaskFolder")
    defaults = ["验收检测", "状态检测", "环保检测"]
    for i, name in enumerate(defaults):
        LibraryTaskFolder.objects.get_or_create(
            parent=None,
            name=name,
            defaults={"sort_order": i * 10, "is_active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0046_libraryprojectequipment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryTaskFolder",
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
                ("name", models.CharField(max_length=128, verbose_name="分类名称")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="排序")),
                ("notes", models.CharField(blank=True, default="", max_length=500, verbose_name="备注")),
                ("is_active", models.BooleanField(default=True, verbose_name="启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="library_task_folders_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="children",
                        to="core.librarytaskfolder",
                        verbose_name="上级分类",
                    ),
                ),
            ],
            options={
                "verbose_name": "检测任务分类",
                "verbose_name_plural": "检测任务分类",
                "ordering": ["sort_order", "name", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="librarytaskfolder",
            constraint=models.UniqueConstraint(
                fields=("parent", "name"), name="uniq_task_folder_sibling_name"
            ),
        ),
        migrations.AddField(
            model_name="librarytask",
            name="task_folder",
            field=models.ForeignKey(
                blank=True,
                help_text="仅报告类模板需选择；现场记录通过报告关联",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="report_tasks",
                to="core.librarytaskfolder",
                verbose_name="所属任务分类",
            ),
        ),
        migrations.RunPython(seed_default_task_folders, migrations.RunPython.noop),
    ]
