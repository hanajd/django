# 用户可见文案：perm_process_pipeline 显示为「OCR处理」；模板编辑器 help_text 与之一致

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_alter_role_perm_htmlpdf_verbose_and_code_choice"),
    ]

    operations = [
        migrations.AlterField(
            model_name="role",
            name="perm_process_pipeline",
            field=models.BooleanField(default=False, verbose_name="OCR处理"),
        ),
        migrations.AlterField(
            model_name="role",
            name="perm_htmlpdf",
            field=models.BooleanField(
                default=False,
                help_text="进入模板编辑器及相关 API；不含 MinerU/Ollama OCR 处理",
                verbose_name="模板编辑器",
            ),
        ),
    ]
