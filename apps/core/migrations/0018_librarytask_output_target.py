from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_libraryfile_inspection_submit_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="librarytask",
            name="output_target",
            field=models.CharField(
                choices=[("site_record", "现场记录"), ("report", "报告")],
                db_index=True,
                default="site_record",
                help_text="决定该任务生成的 PDF 默认保存到现场记录或报告分类",
                max_length=20,
                verbose_name="PDF 输出目标",
            ),
        ),
    ]
