# E3: 보관 기간 활동 기준(마지막 수정). 이미 있는 모으기는 이 마이그레이션 시각(늦게 지우는 쪽).
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("collect", "0003_push")]

    operations = [
        migrations.AddField(
            model_name="collection",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
