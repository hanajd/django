from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("core", "UserProfile")
    for u in User.objects.all().iterator():
        UserProfile.objects.get_or_create(user_id=u.pk)


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_userprofile_perm_overrides"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
