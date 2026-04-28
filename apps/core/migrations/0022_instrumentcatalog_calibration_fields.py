from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_instrumentcatalog"),
    ]

    operations = [
        migrations.AddField(
            model_name="instrumentcatalog",
            name="calibration_org",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="检定/校准单位"),
        ),
        migrations.AddField(
            model_name="instrumentcatalog",
            name="certificate_no",
            field=models.CharField(blank=True, default="", max_length=128, verbose_name="证书编号"),
        ),
        migrations.AddField(
            model_name="instrumentcatalog",
            name="certificate_valid_until",
            field=models.DateField(blank=True, null=True, verbose_name="证书有效期"),
        ),
        migrations.AddField(
            model_name="instrumentcatalog",
            name="model",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="型号"),
        ),
        migrations.AddField(
            model_name="instrumentcatalog",
            name="remarks",
            field=models.TextField(blank=True, default="", verbose_name="备注说明"),
        ),
        migrations.AlterField(
            model_name="instrumentcatalog",
            name="name",
            field=models.CharField(db_index=True, max_length=255, verbose_name="仪器设备名称"),
        ),
    ]
