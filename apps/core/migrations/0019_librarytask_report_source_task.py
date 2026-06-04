from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_librarytask_output_target"),
    ]

    operations = [
        migrations.AddField(
            model_name="librarytask",
            name="report_source_task",
            field=models.ForeignKey(
                blank=True,
                help_text="当本任务输出为报告时，可指定用于填充报告的现场记录 JSON 来源任务",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="report_target_tasks",
                to="core.librarytask",
                verbose_name="报告来源现场记录任务",
            ),
        ),
    ]

