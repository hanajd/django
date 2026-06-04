from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_backfill_userprofile_for_existing_users"),
    ]

    operations = [
        migrations.AddField(
            model_name="librarytask",
            name="bound_instrument_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="有序的主数据仪器 id 列表（对应 InstrumentCatalog）。合并到提交的 instruments 中用于下拉与 PDF 展示；与提交去重后追加。",
                verbose_name="模板默认检测仪器",
            ),
        ),
    ]
