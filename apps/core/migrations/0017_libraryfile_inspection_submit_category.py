from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_inspection_submission_status_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="libraryfile",
            name="category",
            field=models.CharField(
                choices=[
                    ("upload", "OCR文件"),
                    ("json", "JSON 文件"),
                    ("template", "模板"),
                    ("site_record", "现场记录"),
                    ("report", "报告"),
                    ("attachment", "附件"),
                    ("inspection_submit", "检测提交"),
                    ("temp", "临时文件"),
                ],
                db_index=True,
                max_length=20,
                verbose_name="分类",
            ),
        ),
    ]
