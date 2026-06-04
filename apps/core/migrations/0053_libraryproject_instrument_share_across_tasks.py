from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0052_instrumentcatalog_checkout_project_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="libraryproject",
            name="instrument_share_across_tasks",
            field=models.BooleanField(
                default=False,
                verbose_name="同种仪器跨模板共用",
                help_text=(
                    "未勾选：各现场记录任务模板分别分配物理编号（同模板下多台设备自动共用）。"
                    "勾选：多个任务模板若要求同种仪器，整项目只分配一台。"
                ),
            ),
        ),
    ]
