from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0053_libraryproject_instrument_share_across_tasks"),
    ]

    operations = [
        migrations.AddField(
            model_name="libraryprojectequipment",
            name="inspection_type",
            field=models.CharField(
                blank=True,
                default="",
                help_text="本次委托该设备执行的检测类型（如验收检测、状态检测），对应设备主数据中的模板绑定",
                max_length=64,
                verbose_name="本次检测类型",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="libraryprojectequipment",
            name="uniq_project_equipment",
        ),
        migrations.AddConstraint(
            model_name="libraryprojectequipment",
            constraint=models.UniqueConstraint(
                fields=("project", "equipment", "inspection_type"),
                name="uniq_project_equipment_inspection_type",
            ),
        ),
    ]
