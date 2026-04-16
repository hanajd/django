# 文件库任务模型、项目-任务多对多、案件/登记关联项目与任务、迁移旧任务分配数据

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def migrate_legacy_task_assignments(apps, schema_editor):
    LibraryTask = apps.get_model("core", "LibraryTask")
    Assignment = apps.get_model("core", "LibraryTaskAssignment")
    LibraryProject = apps.get_model("core", "LibraryProject")
    task_labels = {"performance_acceptance": "性能验收检测"}
    for a in Assignment.objects.all().iterator():
        tc = getattr(a, "task_code", "") or ""
        label = task_labels.get(tc, tc or "任务")
        base = f"mig-{a.pk}"
        code = base
        suffix = 0
        while LibraryTask.objects.filter(code=code).exists():
            suffix += 1
            code = f"{base}-{suffix}"
        lt = LibraryTask.objects.create(
            code=code,
            name=label,
            created_by_id=getattr(a, "assigned_by_id", None),
        )
        ids = list(a.template_files.values_list("pk", flat=True))
        if ids:
            lt.template_files.set(ids)
        a.library_task_id = lt.pk
        a.save(update_fields=["library_task_id"])
        pid = getattr(a, "project_id", None)
        if pid:
            p = LibraryProject.objects.filter(pk=pid).first()
            if p:
                p.library_tasks.add(lt)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0010_library_project_and_isolation"),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryTask",
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
                    "code",
                    models.SlugField(max_length=64, unique=True, verbose_name="任务编码"),
                ),
                ("name", models.CharField(max_length=128, verbose_name="任务名称")),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="library_tasks_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
            ],
            options={
                "verbose_name": "文件库任务",
                "verbose_name_plural": "文件库任务",
                "ordering": ["code"],
            },
        ),
        migrations.AddField(
            model_name="librarytask",
            name="template_files",
            field=models.ManyToManyField(
                blank=True,
                related_name="library_tasks",
                to="core.libraryfile",
                help_text="现场记录、报告分类中的文件",
                verbose_name="关联文件",
            ),
        ),
        migrations.AddField(
            model_name="libraryproject",
            name="library_tasks",
            field=models.ManyToManyField(
                blank=True,
                help_text="项目启用哪些任务；分配任务时仅能选择已关联的任务",
                related_name="projects",
                to="core.librarytask",
                verbose_name="关联任务",
            ),
        ),
        migrations.AddField(
            model_name="inspectioncase",
            name="library_project",
            field=models.ForeignKey(
                blank=True,
                help_text="可选；用于将现场记录/报告的创建者解析为该项目下的任务接收人",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="inspection_cases",
                to="core.libraryproject",
                verbose_name="文件库项目",
            ),
        ),
        migrations.AddField(
            model_name="siterecord",
            name="library_task",
            field=models.ForeignKey(
                blank=True,
                help_text="可选；指定时按该任务下的分配记录解析创建者",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="site_records",
                to="core.librarytask",
                verbose_name="文件库任务",
            ),
        ),
        migrations.AddField(
            model_name="report",
            name="library_task",
            field=models.ForeignKey(
                blank=True,
                help_text="可选；指定时按该任务下的分配记录解析创建者",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reports",
                to="core.librarytask",
                verbose_name="文件库任务",
            ),
        ),
        migrations.AddField(
            model_name="librarytaskassignment",
            name="library_task",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="assignments",
                to="core.librarytask",
                verbose_name="任务",
            ),
        ),
        migrations.RunPython(
            migrate_legacy_task_assignments,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="librarytaskassignment",
            name="task_code",
        ),
        migrations.RemoveField(
            model_name="librarytaskassignment",
            name="template_files",
        ),
        migrations.AlterField(
            model_name="librarytaskassignment",
            name="library_task",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="assignments",
                to="core.librarytask",
                verbose_name="任务",
            ),
        ),
    ]
