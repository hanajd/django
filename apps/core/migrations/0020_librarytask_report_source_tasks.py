from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_librarytask_report_source_task"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="librarytask",
            name="report_source_task",
        ),
        migrations.AddField(
            model_name="librarytask",
            name="report_source_tasks",
            field=models.ManyToManyField(
                blank=True,
                help_text="当本任务输出为报告时，可指定一个或多个现场记录任务作为报告填充来源",
                related_name="report_target_tasks",
                to="core.librarytask",
                verbose_name="报告来源现场记录任务",
            ),
        ),
    ]

