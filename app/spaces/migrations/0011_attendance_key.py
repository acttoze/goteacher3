from django.db import migrations, models

import spaces.models


def fill(apps, schema_editor):
    Attendance = apps.get_model("spaces", "Attendance")
    for pk in Attendance.objects.filter(key__isnull=True).values_list("pk", flat=True):
        Attendance.objects.filter(pk=pk).update(key=spaces.models.new_token())


class Migration(migrations.Migration):

    dependencies = [
        ("spaces", "0010_plan_request"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendance",
            name="key",
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="attendance",
            name="key",
            field=models.CharField(default=spaces.models.new_token, editable=False, max_length=64, unique=True),
        ),
    ]
