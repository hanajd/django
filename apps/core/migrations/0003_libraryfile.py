# Generated manually for LibraryFile model

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0002_userprofile_role'),
    ]

    operations = [
        migrations.CreateModel(
            name='LibraryFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('original_name', models.CharField(max_length=255, verbose_name='原始文件名')),
                ('relative_path', models.CharField(max_length=512, verbose_name='库内相对路径')),
                ('category', models.CharField(choices=[('upload', '上传文件'), ('json', 'JSON 文件'), ('temp', '临时文件')], db_index=True, max_length=20, verbose_name='分类')),
                ('batch_id', models.CharField(blank=True, db_index=True, default='', max_length=64, verbose_name='批次 ID')),
                ('size', models.BigIntegerField(default=0, verbose_name='大小(字节)')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='library_files', to=settings.AUTH_USER_MODEL, verbose_name='创建者')),
            ],
            options={
                'verbose_name': '文件库文件',
                'verbose_name_plural': '文件库文件',
                'ordering': ['-created_at'],
            },
        ),
    ]
