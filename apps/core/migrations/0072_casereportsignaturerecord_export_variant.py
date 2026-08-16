from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0071_libraryfile_updated_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="casereportsignaturerecord",
            name="export_variant",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="full",
                help_text="full=全本 / front=无防护结果 / rp=仅防护结果；各版本单独签字。",
                max_length=16,
                verbose_name="报告导出版本",
            ),
        ),
        migrations.AddIndex(
            model_name="casereportsignaturerecord",
            index=models.Index(
                fields=["case", "task_no", "export_variant", "slot"],
                name="core_casere_case_id_export_slot_idx",
            ),
        ),
    ]
