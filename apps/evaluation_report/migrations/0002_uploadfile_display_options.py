from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("evaluation_report", "0001_evaluation_report_embed"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluationreportuploadfile",
            name="page_mode",
            field=models.CharField(
                blank=True,
                choices=[("", "随正文纸张"), ("a3landscape", "A3 横置整页")],
                default="",
                help_text="空=A4 正文宽度；a3landscape=单独 A3 横置页",
                max_length=32,
                verbose_name="页面模式",
            ),
        ),
        migrations.AddField(
            model_name="evaluationreportuploadfile",
            name="rotate",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="0 / 90 / 180 / 270",
                verbose_name="旋转角度",
            ),
        ),
        migrations.AddField(
            model_name="evaluationreportuploadfile",
            name="width_percent",
            field=models.PositiveSmallIntegerField(
                default=100,
                help_text="相对版心宽度，30–100；A3 横置时忽略",
                verbose_name="图片宽度百分比",
            ),
        ),
    ]
