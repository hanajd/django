# 业务主数据 + 案件 / 原始记录 / 报告；文件库 report 分类与 link 字段；角色 perm_biz_registry

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def set_perm_biz_registry_defaults(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    Role.objects.filter(code__in=("super_admin", "admin", "app_user")).update(
        perm_biz_registry=True
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0008_libraryocrprocesstask"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="perm_biz_registry",
            field=models.BooleanField(
                default=False,
                help_text="维护受检单位/设备/联系人及案件、原始记录、报告与文件联动",
                verbose_name="业务登记",
            ),
        ),
        migrations.RunPython(set_perm_biz_registry_defaults, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="libraryfile",
            name="category",
            field=models.CharField(
                choices=[
                    ("upload", "OCR文件"),
                    ("json", "JSON 文件"),
                    ("template", "模板"),
                    ("site_record", "现场记录"),
                    ("report", "报告"),
                    ("attachment", "附件"),
                    ("temp", "临时文件"),
                ],
                db_index=True,
                max_length=20,
                verbose_name="分类",
            ),
        ),
        migrations.AddField(
            model_name="libraryfile",
            name="link_entity",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="inspection_case / site_record / report 等，空表示未绑定",
                max_length=32,
                verbose_name="关联业务类型",
            ),
        ),
        migrations.AddField(
            model_name="libraryfile",
            name="link_object_id",
            field=models.PositiveBigIntegerField(
                blank=True, db_index=True, null=True, verbose_name="关联业务主键"
            ),
        ),
        migrations.AddIndex(
            model_name="libraryfile",
            index=models.Index(
                fields=["link_entity", "link_object_id"],
                name="libraryfile_link_idx",
            ),
        ),
        migrations.CreateModel(
            name="InspectedOrganization",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        db_index=True, max_length=255, verbose_name="单位名称"
                    ),
                ),
                (
                    "address",
                    models.CharField(
                        blank=True, default="", max_length=512, verbose_name="地址"
                    ),
                ),
                (
                    "credit_code",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        max_length=32,
                        verbose_name="统一社会信用代码",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inspected_organizations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
            ],
            options={
                "verbose_name": "受检单位",
                "verbose_name_plural": "受检单位",
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="BizContact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        db_index=True, max_length=128, verbose_name="姓名"
                    ),
                ),
                (
                    "phone",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        max_length=64,
                        verbose_name="联系电话",
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True, default="", max_length=128, verbose_name="职务"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="biz_contacts",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="contacts",
                        to="core.inspectedorganization",
                        verbose_name="所属单位",
                    ),
                ),
            ],
            options={
                "verbose_name": "业务联系人",
                "verbose_name_plural": "业务联系人",
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="BizDevice",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        db_index=True, max_length=255, verbose_name="设备名称"
                    ),
                ),
                (
                    "model",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        max_length=255,
                        verbose_name="设备型号",
                    ),
                ),
                (
                    "serial_no",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        max_length=255,
                        verbose_name="设备编号",
                    ),
                ),
                (
                    "manufacturer",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=255,
                        verbose_name="生产厂家",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="biz_devices",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="devices",
                        to="core.inspectedorganization",
                        verbose_name="关联单位",
                    ),
                ),
            ],
            options={
                "verbose_name": "设备",
                "verbose_name_plural": "设备",
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="InspectionCase",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "case_no",
                    models.CharField(
                        db_index=True, max_length=64, unique=True, verbose_name="案件编号"
                    ),
                ),
                (
                    "notes",
                    models.TextField(blank=True, default="", verbose_name="备注"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inspection_cases",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
                (
                    "inspected_organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inspection_cases",
                        to="core.inspectedorganization",
                        verbose_name="受检单位",
                    ),
                ),
                (
                    "primary_contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="primary_inspection_cases",
                        to="core.bizcontact",
                        verbose_name="主联系人",
                    ),
                ),
                (
                    "devices",
                    models.ManyToManyField(
                        blank=True,
                        related_name="inspection_cases",
                        to="core.bizdevice",
                        verbose_name="关联设备",
                    ),
                ),
            ],
            options={
                "verbose_name": "检验案件",
                "verbose_name_plural": "检验案件",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SiteRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "record_no",
                    models.CharField(
                        db_index=True, max_length=128, unique=True, verbose_name="原始记录编号"
                    ),
                ),
                (
                    "record_date",
                    models.DateField(blank=True, null=True, verbose_name="记录日期"),
                ),
                (
                    "payload_json",
                    models.JSONField(
                        blank=True, default=dict, verbose_name="扩展数据(JSON)"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="site_records",
                        to="core.inspectioncase",
                        verbose_name="所属案件",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="site_records",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
            ],
            options={
                "verbose_name": "现场原始记录",
                "verbose_name_plural": "现场原始记录",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="Report",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "report_no",
                    models.CharField(
                        db_index=True, max_length=128, unique=True, verbose_name="报告编号"
                    ),
                ),
                (
                    "version",
                    models.PositiveSmallIntegerField(default=1, verbose_name="版本"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("draft", "草稿"), ("issued", "已签发")],
                        db_index=True,
                        default="draft",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                (
                    "issued_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="签发时间"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="更新时间"),
                ),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reports",
                        to="core.inspectioncase",
                        verbose_name="所属案件",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reports",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="创建者",
                    ),
                ),
                (
                    "site_record",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reports",
                        to="core.siterecord",
                        verbose_name="对应原始记录",
                    ),
                ),
            ],
            options={
                "verbose_name": "报告",
                "verbose_name_plural": "报告",
                "ordering": ["-created_at"],
            },
        ),
    ]
