# LibraryFile 内容哈希（用于 OCR 去重）

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_library_task_assignment"),
    ]

    operations = [
        migrations.AddField(
            model_name="libraryfile",
            name="content_sha256",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=64,
                verbose_name="内容哈希",
            ),
        ),
    ]
