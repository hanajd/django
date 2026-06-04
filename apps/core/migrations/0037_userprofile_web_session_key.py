# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_libraryfile_trash_and_user_quota"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="web_session_key",
            field=models.CharField(
                blank=True,
                help_text="用于单浏览器会话：新浏览器登录后台后会更新，旧会话随即失效；与平板 JWT 无关",
                max_length=64,
                null=True,
                verbose_name="当前 Web 会话键",
            ),
        ),
    ]
