# 项目主要负责人；流程岗位支持同一项目同一岗位多人

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Count, Min


def forwards_backfill_primary_responsible(apps, schema_editor):
    Project = apps.get_model("core", "LibraryProject")
    for row in Project.objects.filter(primary_responsible_id__isnull=True, created_by_id__isnull=False):
        row.primary_responsible_id = row.created_by_id
        row.save(update_fields=["primary_responsible_id"])


def forwards_dedupe_workflow_member_triples(apps, schema_editor):
    Member = apps.get_model("core", "LibraryProjectWorkflowMember")
    dupes = (
        Member.objects.values("project_id", "user_id", "workflow_role")
        .annotate(n=Count("id"), keep_pk=Min("id"))
        .filter(n__gt=1)
    )
    for row in dupes:
        Member.objects.filter(
            project_id=row["project_id"],
            user_id=row["user_id"],
            workflow_role=row["workflow_role"],
        ).exclude(pk=row["keep_pk"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_alter_libraryfile_deleted_at_help_text"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="libraryproject",
            name="primary_responsible",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="primary_responsible_library_projects",
                to=settings.AUTH_USER_MODEL,
                verbose_name="主要负责人",
                help_text="本项目业务主责人，拥有该项目工作台内的完整配置与分配权限",
            ),
        ),
        migrations.RunPython(forwards_backfill_primary_responsible, migrations.RunPython.noop),
        migrations.RunPython(forwards_dedupe_workflow_member_triples, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="libraryprojectworkflowmember",
            name="uniq_project_workflow_role_slot",
        ),
        migrations.AddConstraint(
            model_name="libraryprojectworkflowmember",
            constraint=models.UniqueConstraint(
                fields=("project", "user", "workflow_role"),
                name="uniq_project_workflow_member_user_role",
            ),
        ),
    ]
