from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_inspection_submission"),
    ]

    operations = [
        migrations.AddField(
            model_name="inspectionsubmission",
            name="draft_saved_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="草稿保存时间"),
        ),
        migrations.AddField(
            model_name="inspectionsubmission",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="开始检测时间"),
        ),
        migrations.AddField(
            model_name="inspectionsubmission",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "待检测"),
                    ("in_progress", "检测中"),
                    ("submitted", "已提交"),
                    ("approved", "已通过"),
                    ("rejected", "已驳回"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
                verbose_name="状态",
            ),
        ),
        migrations.AddField(
            model_name="inspectionsubmission",
            name="submitted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="提交时间"),
        ),
    ]
