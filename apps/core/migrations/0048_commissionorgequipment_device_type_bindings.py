from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0047_librarytaskfolder"),
    ]

    operations = [
        migrations.AddField(
            model_name="commissionorgequipment",
            name="device_type",
            field=models.CharField(
                blank=True,
                default="",
                max_length=128,
                verbose_name="设备类型",
                help_text="与任务模板库中「设备类型」文件夹名称对应，如 CT、DR",
            ),
        ),
        migrations.AddField(
            model_name="commissionorgequipment",
            name="report_task_bindings",
            field=models.JSONField(
                blank=True,
                default=list,
                verbose_name="检测类型与报告模板",
                help_text='列表项如 {"inspection_type":"验收检测","report_task_id":1}；未列出的类型使用默认 report_task',
            ),
        ),
    ]
