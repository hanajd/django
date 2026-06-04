# 文件库任务分配 + 角色 perm_assign_tasks

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def set_assign_task_perm_for_builtin_roles(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    for r in Role.objects.filter(code__in=("super_admin", "admin")):
        r.perm_assign_tasks = True
        r.save(update_fields=["perm_assign_tasks"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0005_library_categories_and_attachment_upload_perm"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="perm_assign_tasks",
            field=models.BooleanField(
                default=False,
                help_text="向 App 用户分配任务并挂载模板文件",
                verbose_name="分配文件库任务",
            ),
        ),
        migrations.RunPython(
            set_assign_task_perm_for_builtin_roles, migrations.RunPython.noop
        ),
        migrations.CreateModel(
            name="LibraryTaskAssignment",
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
                (
                    "task_code",
                    models.CharField(
                        choices=[
                            ("performance_acceptance", "性能验收检测"),
                        ],
                        max_length=64,
                        verbose_name="任务",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "assignee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="received_library_task_assignments",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="接收用户",
                    ),
                ),
                (
                    "assigned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sent_library_task_assignments",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="分配人",
                    ),
                ),
            ],
            options={
                "verbose_name": "文件库任务分配",
                "verbose_name_plural": "文件库任务分配",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="librarytaskassignment",
            name="template_files",
            field=models.ManyToManyField(
                blank=True,
                related_name="library_task_assignments",
                to="core.libraryfile",
                verbose_name="模板文件",
            ),
        ),
    ]
