# 任务-文件改为通过表 LibraryFileTask（全分类），迁移原 template_files M2M

import django.db.models.deletion
from django.conf import settings
from django.db import connection, migrations, models


def copy_task_template_files_to_links(apps, schema_editor):
    LibraryFileTask = apps.get_model("core", "LibraryFileTask")
    try:
        with connection.cursor() as c:
            c.execute(
                "SELECT librarytask_id, libraryfile_id FROM core_librarytask_template_files"
            )
            rows = c.fetchall()
    except Exception:
        rows = []
    for tid, fid in rows:
        if tid and fid:
            LibraryFileTask.objects.get_or_create(
                library_task_id=tid,
                library_file_id=fid,
                defaults={"created_by_id": None},
            )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0011_library_task_and_registry_links"),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryFileTask",
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
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="library_file_task_links",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="关联人",
                    ),
                ),
                (
                    "library_file",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_links",
                        to="core.libraryfile",
                        verbose_name="文件",
                    ),
                ),
                (
                    "library_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="file_links",
                        to="core.librarytask",
                        verbose_name="任务",
                    ),
                ),
            ],
            options={
                "verbose_name": "文件任务关联",
                "verbose_name_plural": "文件任务关联",
            },
        ),
        migrations.AddConstraint(
            model_name="libraryfiletask",
            constraint=models.UniqueConstraint(
                fields=("library_file", "library_task"),
                name="uniq_library_file_task",
            ),
        ),
        migrations.RunPython(copy_task_template_files_to_links, migrations.RunPython.noop),
        migrations.AddField(
            model_name="libraryfile",
            name="library_tasks",
            field=models.ManyToManyField(
                blank=True,
                help_text="后台任务挂载的文件，与项目为多对多；分配后同步写入项目关联",
                related_name="library_files",
                through="LibraryFileTask",
                to="core.librarytask",
                verbose_name="关联任务",
            ),
        ),
        migrations.RemoveField(
            model_name="librarytask",
            name="template_files",
        ),
    ]
