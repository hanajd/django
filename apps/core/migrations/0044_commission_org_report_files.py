# 医院信息管理：报告绑定由任务模板改为文件库实际报告文件

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_commission_org_hospital_info"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="commissionorganization",
            name="merged_report_task",
        ),
        migrations.RemoveField(
            model_name="commissionorgequipment",
            name="report_task",
        ),
        migrations.AddField(
            model_name="commissionorganization",
            name="merged_report_file",
            field=models.ForeignKey(
                blank=True,
                help_text="绑定该委托单位下属项目中已生成并归入文件库「报告」分类的 PDF 等成品文件",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="commission_orgs_merged_report",
                to="core.libraryfile",
                verbose_name="合并报告",
            ),
        ),
        migrations.AddField(
            model_name="commissionorgequipment",
            name="report_file",
            field=models.ForeignKey(
                blank=True,
                help_text="该科室下属项目中已生成并归入文件库「报告」分类的成品文件",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="commission_org_equipments",
                to="core.libraryfile",
                verbose_name="单台设备报告",
            ),
        ),
    ]
