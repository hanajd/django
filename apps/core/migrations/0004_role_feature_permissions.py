# Role feature permission booleans + defaults for built-in roles

from django.db import migrations, models


def set_builtin_role_permissions(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    all_true = {
        "perm_manage_users": True,
        "perm_manage_roles": True,
        "perm_manage_menus": True,
        "perm_file_library": True,
        "perm_file_upload": True,
        "perm_file_download": True,
        "perm_file_preview": True,
        "perm_file_delete": True,
        "perm_file_scope_own_only": False,
        "perm_process_pipeline": True,
    }
    app_user = {
        "perm_manage_users": False,
        "perm_manage_roles": False,
        "perm_manage_menus": False,
        "perm_file_library": True,
        "perm_file_upload": True,
        "perm_file_download": True,
        "perm_file_preview": True,
        "perm_file_delete": False,
        "perm_file_scope_own_only": True,
        "perm_process_pipeline": False,
    }
    for r in Role.objects.all():
        if r.code == "super_admin":
            for k, v in all_true.items():
                setattr(r, k, v)
        elif r.code == "admin":
            for k, v in all_true.items():
                setattr(r, k, v)
        elif r.code == "app_user":
            for k, v in app_user.items():
                setattr(r, k, v)
        else:
            for k, v in app_user.items():
                setattr(r, k, v)
        r.save(update_fields=list(all_true.keys()))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_libraryfile"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="perm_manage_users",
            field=models.BooleanField(default=False, verbose_name="用户管理"),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_manage_roles",
            field=models.BooleanField(default=False, verbose_name="角色管理"),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_manage_menus",
            field=models.BooleanField(default=False, verbose_name="菜单管理"),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_file_library",
            field=models.BooleanField(default=True, verbose_name="文件库访问"),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_file_upload",
            field=models.BooleanField(default=True, verbose_name="文件上传"),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_file_download",
            field=models.BooleanField(default=True, verbose_name="文件下载"),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_file_preview",
            field=models.BooleanField(default=True, verbose_name="文件预览"),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_file_delete",
            field=models.BooleanField(default=True, verbose_name="文件删除"),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_file_scope_own_only",
            field=models.BooleanField(
                default=False,
                help_text="开启后仅能访问 created_by 为当前用户的库文件",
                verbose_name="文件库仅本人数据",
            ),
        ),
        migrations.AddField(
            model_name="role",
            name="perm_process_pipeline",
            field=models.BooleanField(default=False, verbose_name="流程处理"),
        ),
        migrations.RunPython(set_builtin_role_permissions, migrations.RunPython.noop),
    ]
