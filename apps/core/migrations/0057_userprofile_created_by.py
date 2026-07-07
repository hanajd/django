# UserProfile.created_by：记录后台创建者（委托统筹仅管理本人创建的用户）

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0056_commission_coordinator_role"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text="后台创建该账号的操作人；委托统筹仅可管理本人创建的用户",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="user_profiles_created",
                to=settings.AUTH_USER_MODEL,
                verbose_name="创建者",
            ),
        ),
    ]
