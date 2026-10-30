"""B4: 공간 주소용 token을 초대 토큰과 나눈다. 기존 행은 하나씩 새 토큰을 받는다."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import spaces.models


def fill_tokens(apps, schema_editor):
    Space = apps.get_model("spaces", "Space")
    for s in Space.objects.filter(token__isnull=True):
        s.token = spaces.models.new_token()
        s.save(update_fields=["token"])


class Migration(migrations.Migration):

    dependencies = [
        ("spaces", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="space",
            name="token",
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
        migrations.RunPython(fill_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="space",
            name="token",
            field=models.CharField(default=spaces.models.new_token, editable=False, max_length=64, unique=True),
        ),
        migrations.AddField(
            model_name="member",
            name="user",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="members", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name="member",
            constraint=models.UniqueConstraint(fields=("space", "user"), name="uniq_member_space_user"),
        ),
    ]
