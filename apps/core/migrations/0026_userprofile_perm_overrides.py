from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_alter_role_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="perm_overrides",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="按权限键存储 true/false；未出现的键继承角色。空对象表示完全跟随角色",
                verbose_name="权限个性化覆盖",
            ),
        ),
    ]
