from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _backfill_commission_orgs(apps, schema_editor):
    CommissionOrganization = apps.get_model("core", "CommissionOrganization")
    LibraryProject = apps.get_model("core", "LibraryProject")
    cache: dict[str, int] = {}
    for p in LibraryProject.objects.all().iterator():
        name = (p.commission_organization or "").strip()
        if not name:
            continue
        oid = cache.get(name)
        if oid is None:
            org, _ = CommissionOrganization.objects.get_or_create(name=name)
            oid = org.pk
            cache[name] = oid
        p.commission_org_id = oid
        p.save(update_fields=["commission_org_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_libraryproject_commission_organization"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CommissionOrganization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(db_index=True, max_length=255, unique=True, verbose_name="委托单位名称")),
                ("notes", models.CharField(blank=True, default="", max_length=500, verbose_name="备注")),
                ("is_active", models.BooleanField(default=True, verbose_name="启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="commission_organizations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
            ],
            options={
                "verbose_name": "委托单位",
                "verbose_name_plural": "委托单位",
                "ordering": ["name", "id"],
            },
        ),
        migrations.AddField(
            model_name="libraryproject",
            name="commission_org",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="library_projects",
                to="core.commissionorganization",
                verbose_name="委托单位",
            ),
        ),
        migrations.RunPython(_backfill_commission_orgs, migrations.RunPython.noop),
    ]
