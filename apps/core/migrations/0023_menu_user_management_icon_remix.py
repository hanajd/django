from django.db import migrations


def forwards(apps, schema_editor):
    Menu = apps.get_model("core", "Menu")
    Menu.objects.filter(name="用户管理", parent__isnull=True, icon="users").update(icon="team")


def backwards(apps, schema_editor):
    Menu = apps.get_model("core", "Menu")
    Menu.objects.filter(name="用户管理", parent__isnull=True, icon="team").update(icon="users")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_instrumentcatalog_calibration_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
