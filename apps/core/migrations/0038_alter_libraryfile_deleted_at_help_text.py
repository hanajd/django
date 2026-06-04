# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_userprofile_web_session_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="libraryfile",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="非空表示文件在回收站；超过约 30 天保留期后由系统自动彻底删除",
                null=True,
                verbose_name="移入回收站时间",
            ),
        ),
    ]
