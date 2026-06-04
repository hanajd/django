from django.db import migrations, models


def _backfill_checkout_project_ids(apps, schema_editor):
    InstrumentCatalog = apps.get_model("core", "InstrumentCatalog")
    for inst in InstrumentCatalog.objects.exclude(checkout_project_id=None).iterator():
        ids = list(inst.checkout_project_ids or [])
        pid = int(inst.checkout_project_id)
        if pid not in ids:
            ids.append(pid)
            inst.checkout_project_ids = ids
            inst.save(update_fields=["checkout_project_ids"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0051_libraryproject_assigned_instrument_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="instrumentcatalog",
            name="checkout_project_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                verbose_name="出库关联项目",
                help_text="允许多个委托/项目共用同一台仪器；结束某一项目时仅移除对应 id。",
            ),
        ),
        migrations.AlterField(
            model_name="instrumentcatalog",
            name="checkout_project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="checked_out_instruments",
                to="core.libraryproject",
                verbose_name="最近出库项目",
                help_text="展示用；实际关联以 checkout_project_ids 为准，可多项目共用。",
            ),
        ),
        migrations.RunPython(_backfill_checkout_project_ids, migrations.RunPython.noop),
    ]
