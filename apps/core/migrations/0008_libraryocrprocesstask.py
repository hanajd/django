# OCR 上传异步流程任务

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0007_libraryfile_content_sha256"),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryOCRProcessTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "待处理"),
                            ("running", "处理中"),
                            ("success", "已完成"),
                            ("failed", "失败"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("source_file_ids", models.JSONField(blank=True, default=list, verbose_name="源文件ID列表")),
                (
                    "generated_json_file_ids",
                    models.JSONField(blank=True, default=list, verbose_name="生成JSON文件ID列表"),
                ),
                ("batch_id", models.CharField(blank=True, default="", max_length=64, verbose_name="批次ID")),
                ("error_message", models.TextField(blank=True, default="", verbose_name="错误信息")),
                ("result_summary", models.JSONField(blank=True, default=dict, verbose_name="结果摘要")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="library_ocr_tasks",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="发起用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "OCR流程任务",
                "verbose_name_plural": "OCR流程任务",
                "ordering": ["-created_at"],
            },
        ),
    ]
