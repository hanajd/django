from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_alter_libraryfile_library_tasks_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="InspectionSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("task_no", models.CharField(db_index=True, max_length=64, unique=True, verbose_name="任务编号")),
                ("report_type", models.CharField(db_index=True, max_length=64, verbose_name="报告类型")),
                ("created_at_remote", models.DateTimeField(verbose_name="客户端创建时间")),
                ("updated_at_remote", models.DateTimeField(verbose_name="客户端更新时间")),
                ("report_info", models.JSONField(blank=True, default=dict, verbose_name="报告信息")),
                ("hospital_info", models.JSONField(blank=True, default=dict, verbose_name="医院信息")),
                ("equipment_info", models.JSONField(blank=True, default=dict, verbose_name="设备信息")),
                ("test_result", models.JSONField(blank=True, default=dict, verbose_name="检测结果")),
                ("conclusion", models.JSONField(blank=True, default=dict, verbose_name="结论")),
                ("raw_payload", models.JSONField(blank=True, default=dict, verbose_name="原始请求体")),
                ("sign_author_png", models.BinaryField(blank=True, null=True, verbose_name="编制人签名PNG")),
                ("sign_reviewer_png", models.BinaryField(blank=True, null=True, verbose_name="审核人签名PNG")),
                ("sign_approver_png", models.BinaryField(blank=True, null=True, verbose_name="批准人签名PNG")),
                ("sign_date", models.DateTimeField(blank=True, null=True, verbose_name="签发日期")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inspection_submissions",
                        to="core.inspectioncase",
                        verbose_name="关联案件",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inspection_submissions",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="提交用户",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inspection_submissions",
                        to="core.libraryproject",
                        verbose_name="关联项目",
                    ),
                ),
            ],
            options={
                "verbose_name": "检测报告提交",
                "verbose_name_plural": "检测报告提交",
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="InspectionSubmissionInstrument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=128, verbose_name="仪器名称")),
                ("identifier", models.CharField(max_length=128, verbose_name="仪器编号")),
                ("certificate_no", models.CharField(blank=True, default="", max_length=128, verbose_name="证书号")),
                ("valid_until", models.DateTimeField(blank=True, null=True, verbose_name="有效期至")),
                ("enabled", models.BooleanField(default=True, verbose_name="启用")),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="instruments",
                        to="core.inspectionsubmission",
                        verbose_name="提交记录",
                    ),
                ),
            ],
            options={
                "verbose_name": "检测报告仪器",
                "verbose_name_plural": "检测报告仪器",
                "ordering": ["id"],
            },
        ),
    ]
