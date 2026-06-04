# 文件库新分类（模板 / 现场记录 / 附件）+ 附件上传独立权限

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_role_feature_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="perm_file_upload_attachment",
            field=models.BooleanField(
                default=True,
                help_text="仅控制「附件」分类；可与「文件上传」分开授权",
                verbose_name="附件上传",
            ),
        ),
        migrations.AlterField(
            model_name="libraryfile",
            name="category",
            field=models.CharField(
                choices=[
                    ("upload", "OCR文件"),
                    ("json", "JSON 文件"),
                    ("template", "模板"),
                    ("site_record", "现场记录"),
                    ("attachment", "附件"),
                    ("temp", "临时文件"),
                ],
                db_index=True,
                max_length=20,
                verbose_name="分类",
            ),
        ),
    ]
