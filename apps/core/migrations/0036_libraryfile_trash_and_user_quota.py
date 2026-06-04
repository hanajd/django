# 文件库回收站（软删）与用户容量配额

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_alter_role_perm_process_pipeline_verbose_ocr"),
    ]

    operations = [
        migrations.AddField(
            model_name="libraryfile",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="非空表示在回收站；超过 31 天可由定时任务或管理命令永久清除",
                null=True,
                verbose_name="移入回收站时间",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="file_library_quota_bytes",
            field=models.BigIntegerField(
                default=5368709120,
                help_text="默认 5GiB（5368709120）。超级用户或角色为超级管理员/普通管理员时不校验配额",
                verbose_name="文件库容量配额（字节）",
            ),
        ),
    ]
