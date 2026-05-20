from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _set_existing_as_hospitals(apps, schema_editor):
    CommissionOrganization = apps.get_model("core", "CommissionOrganization")
    CommissionOrganization.objects.all().update(level="hospital", parent_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0041_commissionorganization_and_fk"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="commissionorganization",
            name="level",
            field=models.CharField(
                choices=[
                    ("hospital", "医院"),
                    ("campus", "院区"),
                    ("department", "科室"),
                ],
                db_index=True,
                default="hospital",
                max_length=16,
                verbose_name="层级",
            ),
        ),
        migrations.AddField(
            model_name="commissionorganization",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="core.commissionorganization",
                verbose_name="上级",
            ),
        ),
        migrations.RunPython(_set_existing_as_hospitals, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="commissionorganization",
            name="name",
            field=models.CharField(db_index=True, max_length=255, verbose_name="本级名称"),
        ),
        migrations.AddConstraint(
            model_name="commissionorganization",
            constraint=models.UniqueConstraint(
                condition=models.Q(("parent__isnull", True)),
                fields=("name",),
                name="uniq_commission_hospital_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="commissionorganization",
            constraint=models.UniqueConstraint(
                condition=models.Q(("parent__isnull", False)),
                fields=("parent", "name"),
                name="uniq_commission_org_sibling_name",
            ),
        ),
        migrations.AlterModelOptions(
            name="commissionorganization",
            options={
                "ordering": ["level", "name", "id"],
                "verbose_name": "委托单位",
                "verbose_name_plural": "委托单位",
            },
        ),
    ]
