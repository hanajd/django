# 个人签名改为每人一份；role 仅保留在使用审计（项目签字位）

from django.db import migrations, models


def consolidate_active_signatures_per_user(apps, schema_editor):
    UserSignature = apps.get_model("core", "UserSignature")
    user_ids = UserSignature.objects.filter(is_active=True).values_list("user_id", flat=True).distinct()
    for uid in user_ids:
        rows = list(
            UserSignature.objects.filter(user_id=uid, is_active=True).order_by(
                "-updated_at", "-version", "-id"
            )
        )
        if len(rows) <= 1:
            continue
        for row in rows[1:]:
            UserSignature.objects.filter(pk=row.pk).update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0059_user_signature"),
    ]

    operations = [
        migrations.RunPython(consolidate_active_signatures_per_user, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="usersignature",
            name="uniq_user_signature_role_active",
        ),
        migrations.RemoveIndex(
            model_name="usersignature",
            name="core_usersig_user_role_upd",
        ),
        migrations.RemoveField(
            model_name="usersignature",
            name="role",
        ),
        migrations.AlterModelOptions(
            name="usersignature",
            options={
                "ordering": ["user_id", "-version"],
                "verbose_name": "用户签名",
                "verbose_name_plural": "用户签名",
            },
        ),
        migrations.AddIndex(
            model_name="usersignature",
            index=models.Index(fields=["user", "-updated_at"], name="core_usersig_user_upd"),
        ),
        migrations.AddConstraint(
            model_name="usersignature",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("user",),
                name="uniq_user_signature_active",
            ),
        ),
        migrations.AlterField(
            model_name="usersignatureevent",
            name="role",
            field=models.CharField(
                blank=True,
                choices=[
                    ("inspector", "检测员签字位"),
                    ("checker", "校核员签字位"),
                    ("accompanyingPerson", "陪同人签字位"),
                ],
                db_index=True,
                default="",
                help_text="仅在使用审计中记录该签名被填入了哪个模板签字槽；个人签名库不按此分类",
                max_length=32,
                verbose_name="项目签字位置",
            ),
        ),
    ]
