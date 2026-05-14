# 用户可见文案：perm_htmlpdf 显示为「模板编辑器」；template_tester 角色选项改为「模板编辑器（测试）」

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_workflow_member_unique_per_role_slot"),
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
                    ("template_editor", "模板编辑"),
                    ("template_tester", "模板编辑器（测试）"),
                ],
                max_length=50,
                unique=True,
                verbose_name="角色代码",
            ),
        ),
        migrations.AlterField(
            model_name="role",
            name="perm_htmlpdf",
            field=models.BooleanField(
                default=False,
                help_text="进入模板编辑器及相关 API；不含 MinerU/Ollama 流程处理",
                verbose_name="模板编辑器",
            ),
        ),
    ]
