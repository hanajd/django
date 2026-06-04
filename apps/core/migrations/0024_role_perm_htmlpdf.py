from django.db import migrations, models


def forwards_grant_htmlpdf_where_pipeline(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    Role.objects.filter(perm_process_pipeline=True).update(perm_htmlpdf=True)


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_menu_user_management_icon_remix"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="perm_htmlpdf",
            field=models.BooleanField(
                default=False,
                help_text="进入 HTMLPDF 编辑器及相关 API；不含 MinerU/Ollama 流程处理",
                verbose_name="HTMLPDF 模板编辑器",
            ),
        ),
        migrations.RunPython(forwards_grant_htmlpdf_where_pipeline, backwards_noop),
    ]
