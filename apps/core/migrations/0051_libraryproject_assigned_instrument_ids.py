from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0050_instrument_checkout_inventory"),
    ]

    operations = [
        migrations.AddField(
            model_name="libraryproject",
            name="assigned_instrument_ids",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='人员派工时为项目确定的具体仪器编号：{"qualityControl":[id,...],"radiationProtection":[id,...]}。任务模板仅绑种类时不含编号。',
                verbose_name="已分配检测仪器",
            ),
        ),
    ]
