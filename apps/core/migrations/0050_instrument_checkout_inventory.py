import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0049_librarytasktemplatebindinghistory"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="instrumentcatalog",
            name="checkout_project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="checked_out_instruments",
                to="core.libraryproject",
                verbose_name="当前出库项目",
                help_text="非空表示该编号的仪器已出库至该项目；同一时间仅能归属一个项目。",
            ),
        ),
        migrations.AddField(
            model_name="instrumentcatalog",
            name="checked_out_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="出库时间"),
        ),
        migrations.AddField(
            model_name="instrumentcatalog",
            name="checked_out_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="instrument_checkouts_performed",
                to=settings.AUTH_USER_MODEL,
                verbose_name="出库操作人",
            ),
        ),
        migrations.CreateModel(
            name="InstrumentCheckoutLog",
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
                    "event_type",
                    models.CharField(
                        choices=[("checkout", "出库"), ("checkin", "入库")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                (
                    "performed_at",
                    models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="操作时间"),
                ),
                (
                    "note",
                    models.CharField(blank=True, default="", max_length=255, verbose_name="备注"),
                ),
                (
                    "instrument",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="checkout_logs",
                        to="core.instrumentcatalog",
                        verbose_name="仪器",
                    ),
                ),
                (
                    "performed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="instrument_checkout_logs",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="操作人",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="instrument_checkout_logs",
                        to="core.libraryproject",
                        verbose_name="关联项目",
                    ),
                ),
            ],
            options={
                "verbose_name": "仪器出入库记录",
                "verbose_name_plural": "仪器出入库记录",
                "ordering": ["-performed_at", "-id"],
            },
        ),
    ]
