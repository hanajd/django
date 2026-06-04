from django.db import migrations, models


def renumber_existing_equipment_instances(apps, schema_editor):
    Equipment = apps.get_model("core", "CommissionOrgEquipment")
    buckets: dict[tuple[int, str], list] = {}
    for eq in Equipment.objects.filter(is_active=True).order_by(
        "department_id", "device_type", "sort_order", "id"
    ):
        key = (eq.department_id, (eq.device_type or "").strip())
        buckets.setdefault(key, []).append(eq)
    for eqs in buckets.values():
        for idx, eq in enumerate(eqs, start=1):
            eq.instance_no = idx
            dt = (eq.device_type or "").strip()
            name = (eq.name or "").strip()
            if dt and (not name or name == dt):
                eq.name = f"{dt} · 设备{idx}"
            eq.save(update_fields=["instance_no", "name"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0054_libraryprojectequipment_inspection_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="commissionorgequipment",
            name="instance_no",
            field=models.PositiveIntegerField(
                default=1,
                verbose_name="同类型台次",
                help_text="同一科室、同一设备类型下的序号（设备1、设备2…）",
            ),
        ),
        migrations.RunPython(renumber_existing_equipment_instances, migrations.RunPython.noop),
    ]
