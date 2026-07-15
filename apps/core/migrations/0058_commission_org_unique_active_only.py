# 委托单位名称唯一性仅约束启用中的记录，停用（软删除）后可复用同名。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0057_userprofile_created_by"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="commissionorganization",
            name="uniq_commission_hospital_name",
        ),
        migrations.RemoveConstraint(
            model_name="commissionorganization",
            name="uniq_commission_org_sibling_name",
        ),
        migrations.AddConstraint(
            model_name="commissionorganization",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True), ("parent__isnull", True)),
                fields=("name",),
                name="uniq_commission_hospital_name_active",
            ),
        ),
        migrations.AddConstraint(
            model_name="commissionorganization",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True), ("parent__isnull", False)),
                fields=("parent", "name"),
                name="uniq_commission_org_sibling_name_active",
            ),
        ),
    ]
