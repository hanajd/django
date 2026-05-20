from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0045_commission_org_equipment_inspection"),
    ]

    operations = [
        migrations.CreateModel(
            name="LibraryProjectEquipment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="排序")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="加入时间")),
                (
                    "equipment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_links",
                        to="core.commissionorgequipment",
                        verbose_name="受检设备",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_equipments",
                        to="core.libraryproject",
                        verbose_name="所属项目",
                    ),
                ),
                (
                    "report_task",
                    models.ForeignKey(
                        blank=True,
                        help_text="本项目内该设备使用的任务模板链根节点，默认取自设备主数据",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="project_equipment_links",
                        to="core.librarytask",
                        verbose_name="报告/检测任务模板",
                    ),
                ),
            ],
            options={
                "verbose_name": "项目委托设备",
                "verbose_name_plural": "项目委托设备",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="libraryprojectequipment",
            constraint=models.UniqueConstraint(
                fields=("project", "equipment"), name="uniq_project_equipment"
            ),
        ),
    ]
