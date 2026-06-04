from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0039_project_primary_responsible_and_workflow_multi"),
    ]

    operations = [
        migrations.AddField(
            model_name="libraryproject",
            name="commission_organization",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=255,
                verbose_name="委托单位名称",
            ),
        ),
    ]
