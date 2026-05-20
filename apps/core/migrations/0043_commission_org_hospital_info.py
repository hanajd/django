# Generated manually for hospital info management

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_commissionorganization_hierarchy"),
    ]

    operations = [
        migrations.AddField(
            model_name="commissionorganization",
            name="address",
            field=models.CharField(blank=True, default="", max_length=512, verbose_name="地址/位置"),
        ),
        migrations.AddField(
            model_name="commissionorganization",
            name="introduction",
            field=models.TextField(blank=True, default="", verbose_name="简介"),
        ),
        migrations.AddField(
            model_name="commissionorganization",
            name="merged_report_task",
            field=models.ForeignKey(
                blank=True,
                help_text="医院/院区/科室层级的合并报告，对应任务模板库中输出为「报告」的任务模板",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="commission_orgs_merged_report",
                to="core.librarytask",
                verbose_name="合并报告任务",
            ),
        ),
        migrations.CreateModel(
            name="CommissionOrgContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=128, verbose_name="联系人")),
                ("phone", models.CharField(blank=True, default="", max_length=64, verbose_name="联系电话")),
                ("title", models.CharField(blank=True, default="", max_length=128, verbose_name="职务")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="排序")),
                ("is_active", models.BooleanField(default=True, verbose_name="启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="contacts",
                        to="core.commissionorganization",
                        verbose_name="所属层级",
                    ),
                ),
            ],
            options={
                "verbose_name": "委托单位联系人",
                "verbose_name_plural": "委托单位联系人",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.CreateModel(
            name="CommissionOrgEquipment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, verbose_name="设备名称")),
                ("model", models.CharField(blank=True, default="", max_length=255, verbose_name="设备型号")),
                ("serial_no", models.CharField(blank=True, default="", max_length=255, verbose_name="设备编号")),
                ("manufacturer", models.CharField(blank=True, default="", max_length=255, verbose_name="生产厂家")),
                ("location", models.CharField(blank=True, default="", max_length=512, verbose_name="设备位置")),
                ("notes", models.CharField(blank=True, default="", max_length=500, verbose_name="备注")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="排序")),
                ("is_active", models.BooleanField(default=True, verbose_name="启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="equipments",
                        to="core.commissionorganization",
                        verbose_name="所属科室",
                    ),
                ),
                (
                    "report_task",
                    models.ForeignKey(
                        blank=True,
                        help_text="任务模板库中输出为「报告」的任务，对应该设备的检测报告模板",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="commission_org_equipments",
                        to="core.librarytask",
                        verbose_name="单台设备报告",
                    ),
                ),
            ],
            options={
                "verbose_name": "委托单位设备",
                "verbose_name_plural": "委托单位设备",
                "ordering": ["sort_order", "id"],
            },
        ),
    ]
