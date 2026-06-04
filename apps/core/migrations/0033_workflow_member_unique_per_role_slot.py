# 流程成员：同一项目下每个岗位唯一；允许同一用户兼任多岗（测试账号一人全流程）

from django.db import migrations, models
from django.db.models import Count, Min


def forwards_dedupe_workflow_slots(apps, schema_editor):
    Member = apps.get_model("core", "LibraryProjectWorkflowMember")
    dupes = (
        Member.objects.values("project_id", "workflow_role")
        .annotate(n=Count("id"), keep_pk=Min("id"))
        .filter(n__gt=1)
    )
    for row in dupes:
        Member.objects.filter(
            project_id=row["project_id"],
            workflow_role=row["workflow_role"],
        ).exclude(pk=row["keep_pk"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_role_perm_create_library_project"),
    ]

    operations = [
        migrations.RunPython(forwards_dedupe_workflow_slots, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="libraryprojectworkflowmember",
            name="uniq_project_workflow_member_user",
        ),
        migrations.AddConstraint(
            model_name="libraryprojectworkflowmember",
            constraint=models.UniqueConstraint(
                fields=("project", "workflow_role"),
                name="uniq_project_workflow_role_slot",
            ),
        ),
    ]
