"""Bootstrap demo admin user + sample hospital for local testing."""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.core.models import CommissionOrganization
from apps.evaluation_report.keywords_service import seed_schema_from_csv
from apps.evaluation_report.latex_template_service import seed_bundled_templates


class Command(BaseCommand):
    help = "Create demo admin (admin/admin123) and a sample hospital"

    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True, "email": "admin@example.com"},
        )
        if created:
            user.set_password("admin123")
            user.save()
            self.stdout.write(self.style.SUCCESS("Created user admin / admin123"))
        else:
            self.stdout.write("User admin already exists")

        hospital, h_created = CommissionOrganization.objects.get_or_create(
            name="演示医院（Standalone）",
            level=CommissionOrganization.LEVEL_HOSPITAL,
            defaults={"is_active": True, "address": "本地测试"},
        )
        if h_created:
            self.stdout.write(self.style.SUCCESS(f"Created hospital: {hospital.name}"))
        else:
            self.stdout.write(f"Hospital exists: {hospital.name}")

        seed_bundled_templates()
        n = seed_schema_from_csv()
        self.stdout.write(
            self.style.SUCCESS(f"Bundled LaTeX templates ready; keyword schema +{n}")
        )
        self.stdout.write("Open http://127.0.0.1:22335/evaluation-reports/")
