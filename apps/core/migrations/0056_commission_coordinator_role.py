# 委托统筹角色：精简后台 + 可创建委托/项目/流程用户

from django.db import migrations, models


def seed_commission_coordinator_role(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    defaults = {
        "name": "委托统筹",
        "description": (
            "精简后台：文件库（限定分类）、项目工作台、委托管理、医院信息、仪器台账；"
            "可调用全部任务模板创建委托，并创建检测流程岗位用户。"
        ),
        "perm_manage_users": True,
        "perm_manage_roles": False,
        "perm_manage_menus": False,
        "perm_file_library": True,
        "perm_file_upload": True,
        "perm_file_upload_attachment": True,
        "perm_file_download": True,
        "perm_file_preview": True,
        "perm_file_delete": True,
        "perm_file_scope_own_only": False,
        "perm_process_pipeline": False,
        "perm_htmlpdf": False,
        "perm_assign_tasks": True,
        "perm_create_library_project": True,
        "perm_biz_registry": True,
    }
    Role.objects.update_or_create(code="commission_coordinator", defaults=defaults)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0055_commissionorgequipment_instance_no"),
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
                    ("commission_coordinator", "委托统筹"),
                ],
                max_length=50,
                unique=True,
                verbose_name="角色代码",
            ),
        ),
        migrations.RunPython(seed_commission_coordinator_role, migrations.RunPython.noop),
    ]
