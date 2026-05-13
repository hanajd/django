# 默认审核人、授权签字人可查看全库/全项目文件（关闭「仅本人数据」）

from django.db import migrations


def widen_audit_signatory_file_scope(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    Role.objects.filter(code__in=("report_auditor", "authorized_signatory")).update(
        perm_file_scope_own_only=False
    )


def noop_reverse(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    Role.objects.filter(code="report_auditor").update(perm_file_scope_own_only=True)
    Role.objects.filter(code="authorized_signatory").update(perm_file_scope_own_only=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_role_template_editor_and_signatory_perms"),
    ]

    operations = [
        migrations.RunPython(widen_audit_signatory_file_scope, noop_reverse),
    ]
