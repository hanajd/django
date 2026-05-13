# Generated manually: workflow roles + project members + case workflow state

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _app_side_perm_defaults():
    return {
        "perm_manage_users": False,
        "perm_manage_roles": False,
        "perm_manage_menus": False,
        "perm_file_library": True,
        "perm_file_upload": True,
        "perm_file_upload_attachment": True,
        "perm_file_download": True,
        "perm_file_preview": True,
        "perm_file_delete": False,
        "perm_file_scope_own_only": True,
        "perm_process_pipeline": False,
        "perm_htmlpdf": False,
        "perm_assign_tasks": False,
        "perm_biz_registry": True,
    }


def seed_workflow_roles(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    base = _app_side_perm_defaults()
    rows = [
        ("检测员", "field_inspector", "现场记录填写与上传"),
        ("校核员", "site_reviewer", "现场记录校核与签字"),
        ("编制人", "report_author", "依据现场记录编制报告并签字"),
        ("审核人", "report_auditor", "报告审核并签字"),
        ("授权签字人", "authorized_signatory", "最终签发与签发日期"),
    ]
    for name, code, desc in rows:
        if Role.objects.filter(code=code).exists():
            continue
        Role.objects.create(name=name, code=code, description=desc, **base)


def backfill_case_workflow_states(apps, schema_editor):
    InspectionCase = apps.get_model("core", "InspectionCase")
    InspectionCaseWorkflowState = apps.get_model("core", "InspectionCaseWorkflowState")
    for c in InspectionCase.objects.all().iterator():
        InspectionCaseWorkflowState.objects.get_or_create(
            case=c,
            defaults={"stage": "SITE_FILL"},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_librarytask_bound_instrument_ids"),
    ]

    operations = [
        migrations.AlterField(
            model_name="role",
            name="code",
            field=models.CharField(
                choices=[
                    ("super_admin", "超级管理员"),
                    ("admin", "普通管理员"),
                    ("app_user", "App 用户（兼容旧版，等同检测侧参与人）"),
                    ("field_inspector", "检测员"),
                    ("site_reviewer", "校核员"),
                    ("report_author", "编制人"),
                    ("report_auditor", "审核人"),
                    ("authorized_signatory", "授权签字人"),
                    ("template_tester", "模板与 HTMLPDF 测试"),
                ],
                max_length=50,
                unique=True,
                verbose_name="角色代码",
            ),
        ),
        migrations.CreateModel(
            name="LibraryProjectWorkflowMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "workflow_role",
                    models.CharField(
                        choices=[
                            ("field_inspector", "检测员"),
                            ("site_reviewer", "校核员"),
                            ("report_author", "编制人"),
                            ("report_auditor", "审核人"),
                            ("authorized_signatory", "授权签字人"),
                        ],
                        db_index=True,
                        max_length=32,
                        verbose_name="流程岗位",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="workflow_members",
                        to="core.libraryproject",
                        verbose_name="项目",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="library_project_workflow_memberships",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "项目流程成员",
                "verbose_name_plural": "项目流程成员",
                "ordering": ["project_id", "workflow_role", "user_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="libraryprojectworkflowmember",
            constraint=models.UniqueConstraint(fields=("project", "user"), name="uniq_project_workflow_member_user"),
        ),
        migrations.CreateModel(
            name="InspectionCaseWorkflowState",
            fields=[
                (
                    "stage",
                    models.CharField(
                        choices=[
                            ("SITE_FILL", "检测员填写现场记录"),
                            ("SITE_REVIEW", "校核员校核现场记录"),
                            ("REPORT_DRAFT", "编制人编制报告"),
                            ("REPORT_AUDIT", "审核人审核报告"),
                            ("REPORT_SIGN", "授权签字人签发"),
                            ("ISSUED", "已签发"),
                        ],
                        db_index=True,
                        default="SITE_FILL",
                        max_length=32,
                        verbose_name="当前环节",
                    ),
                ),
                ("return_reason", models.TextField(blank=True, default="", verbose_name="最近一次退回说明")),
                ("issue_date", models.DateField(blank=True, null=True, verbose_name="签发日期")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="环节更新时间")),
                (
                    "case",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="workflow_state",
                        serialize=False,
                        to="core.inspectioncase",
                        verbose_name="案件",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inspection_case_workflow_updates",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="最后操作人",
                    ),
                ),
            ],
            options={
                "verbose_name": "案件流程状态",
                "verbose_name_plural": "案件流程状态",
            },
        ),
        migrations.RunPython(seed_workflow_roles, migrations.RunPython.noop),
        migrations.RunPython(backfill_case_workflow_states, migrations.RunPython.noop),
    ]
