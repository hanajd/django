# 报告环节电子签字记录

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0060_user_signature_one_per_user"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CaseReportSignatureRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("task_no", models.CharField(blank=True, db_index=True, default="", max_length=64, verbose_name="任务编号")),
                (
                    "workflow_stage",
                    models.CharField(blank=True, default="", max_length=32, verbose_name="签字时环节"),
                ),
                (
                    "slot",
                    models.CharField(
                        choices=[
                            ("reportAuthor", "报告编制人签字位"),
                            ("reportAuditor", "报告审核人签字位"),
                            ("authorizedSignatory", "报告授权人签字位"),
                            ("issueDate", "报告签发日期"),
                        ],
                        db_index=True,
                        max_length=32,
                        verbose_name="签字位",
                    ),
                ),
                (
                    "signature_sha256",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="签名内容哈希"),
                ),
                (
                    "report_sha256_before",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="叠印前报告哈希"),
                ),
                (
                    "report_sha256_after",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="叠印后报告哈希"),
                ),
                ("overlay_page", models.PositiveSmallIntegerField(default=0, verbose_name="叠印页码")),
                ("overlay_rect", models.JSONField(blank=True, default=list, verbose_name="叠印区域")),
                ("sign_version", models.PositiveIntegerField(default=1, verbose_name="签字序号")),
                ("signed_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="签字时间")),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report_signature_records",
                        to="core.inspectioncase",
                        verbose_name="案件",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report_signature_records",
                        to="core.libraryproject",
                        verbose_name="项目",
                    ),
                ),
                (
                    "report_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="report_signature_records",
                        to="core.libraryfile",
                        verbose_name="叠印后报告",
                    ),
                ),
                (
                    "signer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="report_signature_records",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="签名人",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="report_signature_records",
                        to="core.inspectionsubmission",
                        verbose_name="检测提交",
                    ),
                ),
                (
                    "user_signature",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="report_signature_records",
                        to="core.usersignature",
                        verbose_name="签名版本",
                    ),
                ),
            ],
            options={
                "verbose_name": "报告签字记录",
                "verbose_name_plural": "报告签字记录",
                "ordering": ["case_id", "task_no", "sign_version", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="casereportsignaturerecord",
            index=models.Index(fields=["case", "task_no", "-signed_at"], name="core_caserep_case_task_sa"),
        ),
        migrations.AddIndex(
            model_name="casereportsignaturerecord",
            index=models.Index(fields=["project", "-signed_at"], name="core_caserep_proj_signed"),
        ),
        migrations.AlterField(
            model_name="usersignatureevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("uploaded", "首次上传"),
                    ("replaced", "重新上传/替换"),
                    ("used_site_record", "用于现场记录"),
                    ("used_report", "用于报告"),
                    ("used_submit", "用于检测提交"),
                    ("used_report_sign", "用于报告环节签字"),
                ],
                db_index=True,
                max_length=32,
                verbose_name="事件类型",
            ),
        ),
        migrations.AlterField(
            model_name="usersignatureevent",
            name="role",
            field=models.CharField(
                blank=True,
                choices=[
                    ("inspector", "检测员签字位"),
                    ("checker", "校核员签字位"),
                    ("accompanyingPerson", "陪同人签字位"),
                    ("reportAuthor", "报告编制人签字位"),
                    ("reportAuditor", "报告审核人签字位"),
                    ("authorizedSignatory", "报告授权人签字位"),
                    ("issueDate", "报告签发日期"),
                ],
                db_index=True,
                default="",
                help_text="现场/报告模板签字槽；个人签名库不按此分类",
                max_length=32,
                verbose_name="项目签字位置",
            ),
        ),
    ]
