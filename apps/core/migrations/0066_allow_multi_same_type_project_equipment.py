from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0065_sales_product_listed_at_device_counts"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="libraryprojectequipment",
            name="uniq_project_equipment_inspection_type",
        ),
        migrations.AddIndex(
            model_name="libraryprojectequipment",
            index=models.Index(
                fields=["project", "equipment", "inspection_type"],
                name="idx_proj_eq_insp_type",
            ),
        ),
        migrations.AlterField(
            model_name="libraryprojectequipment",
            name="inspection_type",
            field=models.CharField(
                blank=True,
                default="",
                help_text="本次委托该设备执行的检测类型（如验收检测、状态检测）。同一项目内允许同一设备多次加入同类型（对应不同报告模板行）。",
                max_length=64,
                verbose_name="本次检测类型",
            ),
        ),
    ]
