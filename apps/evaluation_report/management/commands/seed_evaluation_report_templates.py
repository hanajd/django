"""登记内置 LaTeX 模板并（可选）导入关键词 CSV。"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed bundled evaluation-report LaTeX templates and keyword schema"

    def add_arguments(self, parser):
        parser.add_argument(
            "--keywords",
            action="store_true",
            help="Also seed keyword schema from converter/data/project_keywords.csv",
        )

    def handle(self, *args, **options):
        from apps.evaluation_report.latex_template_service import seed_bundled_templates

        seed_bundled_templates()
        self.stdout.write(self.style.SUCCESS("Bundled LaTeX templates registered."))
        if options.get("keywords"):
            from apps.evaluation_report.keywords_service import seed_schema_from_csv

            n = seed_schema_from_csv()
            self.stdout.write(self.style.SUCCESS(f"Keyword schema rows upserted: {n}"))
