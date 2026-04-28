from django.db import migrations, models


INSTRUMENT_ROWS = [
    ("JXFS/YQ-001", "多功能X射线质量检测仪"),
    ("JXFS/YQ-002", "放射诊断质控工具箱"),
    ("JXFS/YQ-003", "CT头部剂量模体"),
    ("JXFS/YQ-004", "核医学设备性能监测系统"),
    ("JXFS/YQ-005", "X、γ射线巡测仪"),
    ("JXFS/YQ-007", "中子剂量当量仪"),
    ("JXFS/YQ-008", "CT性能检测模体"),
    ("JXFS/YQ-009", "CT体部剂量模体"),
    ("JXFS/YQ-010", "磁共振(MRI)模体"),
    ("JXFS/YQ-011", "乳腺摄影性能检测工具箱"),
    ("JXFS/YQ-013", "DSA性能检测模体"),
    ("JXFS/YQ-014", "牙科X射线性能检测模体"),
    ("JXFS/YQ-015", "标准水模"),
    ("JXFS/YQ-016", "头部γ刀性能检测模体"),
    ("JXFS/YQ-017", "体部γ刀性能检测模体"),
    ("JXFS/YQ-018", "SPECT性能测试模体"),
    ("JXFS/YQ-019", "PET性能测试模体"),
    ("JXFS/YQ-020", "放疗三维水箱"),
    ("JXFS/YQ-021", "放疗小水箱"),
    ("JXFS/YQ-023", "胶片分析软件（含V33胶片扫描仪）"),
    ("JXFS/YQ-024", "放疗剂量仪"),
    ("JXFS/YQ-025", "井型电离室"),
    ("JXFS/YQ-026", "TOMO专用模体"),
    ("JXFS/YQ-033", "十字铅尺"),
    ("JXFS/YQ-034", "DXR尺"),
    ("JXFS/YQ-036", "空盒气压表"),
    ("JXFS/YQ-037", "电子秒表"),
    ("JXFS/YQ-039", "钢直尺"),
    ("JXFS/YQ-046", "CT水模体"),
    ("JXFS/YQ-047", "数显水平尺"),
    ("JXFS/YQ-049", "α、β表面污染仪"),
    ("JXFS/YQ-050", "X、γ辐射空气比释动能率（吸收剂量率）仪"),
    ("JXFS/YQ-052", "玻璃液体温度计"),
    ("JXFS/YQ-053", "CT头部剂量模体"),
    ("JXFS/YQ-054", "CT头部剂量模体"),
    ("JXFS/YQ-055", "乳腺阈对比度细节模体"),
    ("JXFS/YQ-061", "VacuDap剂量面积乘积仪"),
    ("JXFS/YQ-062", "OCTP-200口腔CBCT性能检测模体"),
    ("JXFS/YQ-063", "MCTP-100乳腺CBCT性能检测模体"),
    ("JXFS/YQ-064", "X、γ辐射检测仪"),
    ("JXFS/YQ-703", "放射诊断质控工具箱"),
    ("JXFS/YQ-704", "十字铅尺"),
    ("JXFS/YQ-705", "钢直尺"),
    ("JXFS/YQ-706", "X、γ辐射检测仪"),
    ("JXFS/YQ-707", "CT性能检测模体"),
    ("JXFS/YQ-708", "CT头部剂量模体"),
    ("JXFS/YQ-709", "CT体部剂量模体"),
    ("JXFS/YQ-710", "CT水模体"),
    ("JXFS/YQ-711", "DSA性能检测模体"),
    ("JXFS/YQ-712", "牙科X射线性能检测模体"),
    ("JXFS/YQ-713", "标准水模"),
    ("JXFS/YQ-714", "乳腺摄影性能检测工具箱"),
    ("JXFS/YQ-716", "多功能X射线质量检测仪"),
    ("JXFS/YQ-718", "VacuDap剂量面积乘积仪"),
    ("JXFS/YQ-719", "OCTP-200口腔CBCT性能检测模体"),
]


def seed_instrument_catalog(apps, schema_editor):
    InstrumentCatalog = apps.get_model("core", "InstrumentCatalog")
    for code, name in INSTRUMENT_ROWS:
        InstrumentCatalog.objects.update_or_create(
            code=code,
            defaults={"name": name, "is_active": True},
        )


def unseed_instrument_catalog(apps, schema_editor):
    InstrumentCatalog = apps.get_model("core", "InstrumentCatalog")
    codes = [code for code, _ in INSTRUMENT_ROWS]
    InstrumentCatalog.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_librarytask_report_source_tasks"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstrumentCatalog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(db_index=True, max_length=64, unique=True, verbose_name="仪器编号")),
                ("name", models.CharField(db_index=True, max_length=255, verbose_name="仪器名称")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="启用")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "仪器主数据",
                "verbose_name_plural": "仪器主数据",
                "ordering": ["code"],
            },
        ),
        migrations.RunPython(seed_instrument_catalog, unseed_instrument_catalog),
    ]
