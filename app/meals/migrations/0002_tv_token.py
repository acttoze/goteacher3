from django.db import migrations, models

import meals.models


def fill_tokens(apps, schema_editor):
    """이미 있는 학교 설정에 하나씩 다른 TV 토큰을 넣는다."""
    rows = apps.get_model("meals", "MealSettings").objects.all()
    for row in rows:
        row.tv_token = meals.models.new_tv_token()
        row.save(update_fields=["tv_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("meals", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mealsettings",
            name="tv_token",
            field=models.CharField(max_length=64, null=True, verbose_name="TV 주소 토큰"),
        ),
        migrations.RunPython(fill_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mealsettings",
            name="tv_token",
            field=models.CharField(default=meals.models.new_tv_token, max_length=64, unique=True, verbose_name="TV 주소 토큰"),
        ),
    ]
