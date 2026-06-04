# Generated manually for equipment inspection tracking

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_commission_org_report_files"),
    ]

    operations = [
        migrations.AddField(
            model_name="commissionorgequipment",
            name="last_commission_no",
            field=models.CharField(
                blank=True,
                default="",
                help_text="最近一次完成检测对应的案件/任务编号",
                max_length=128,
                verbose_name="最近委托编号",
            ),
        ),
        migrations.AddField(
            model_name="commissionorgequipment",
            name="last_inspected_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="最近检测时间"),
        ),
        migrations.AddField(
            model_name="commissionorgequipment",
            name="report_task",
            field=models.ForeignKey(
                blank=True,
                help_text="未检测设备绑定的现场记录/报告任务模板，便于加入新项目开展检测",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="commission_org_equipments",
                to="core.librarytask",
                verbose_name="检测任务模板",
            ),
        ),
        migrations.CreateModel(
            name="CommissionOrgEquipmentHistory",
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
                (
                    "commission_no",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        max_length=128,
                        verbose_name="委托编号",
                    ),
                ),
                (
                    "inspected_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="检测完成时间"),
                ),
                (
                    "equipment_info",
                    models.JSONField(blank=True, default=dict, verbose_name="设备信息快照"),
                ),
                (
                    "test_result",
                    models.JSONField(blank=True, default=dict, verbose_name="检测结果快照"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="记录时间")),
                (
                    "equipment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inspection_histories",
                        to="core.commissionorgequipment",
                        verbose_name="设备",
                    ),
                ),
                (
                    "inspection_case",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="equipment_history_entries",
                        to="core.inspectioncase",
                        verbose_name="检验案件",
                    ),
                ),
                (
                    "library_project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="equipment_history_entries",
                        to="core.libraryproject",
                        verbose_name="所属项目",
                    ),
                ),
                (
                    "report_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="equipment_history_entries",
                        to="core.libraryfile",
                        verbose_name="报告文件",
                    ),
                ),
            ],
            options={
                "verbose_name": "委托单位设备检测历史",
                "verbose_name_plural": "委托单位设备检测历史",
                "ordering": ["-inspected_at", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="commissionorgequipmenthistory",
            constraint=models.UniqueConstraint(
                condition=models.Q(("report_file__isnull", False)),
                fields=("equipment", "report_file"),
                name="uniq_equipment_history_report_file",
            ),
        ),
    ]
