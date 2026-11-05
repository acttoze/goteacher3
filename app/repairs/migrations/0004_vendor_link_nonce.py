from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def revoke_expired(apps, schema_editor):
    """사용 기간이 이미 끝난 계정은 until이 사라져도 되살아나지 않게 해제한다."""
    VendorAccount = apps.get_model("repairs", "VendorAccount")
    VendorAccount.objects.filter(revoked_at__isnull=True, until__lt=timezone.localdate()).update(revoked_at=timezone.now())


def fill_until(apps, schema_editor):
    """되돌릴 때: 지운 값은 알 수 없으니 살아 있는 계정이 끊기지 않게 1년 뒤로 채운다(0003의 NOT NULL로 돌아가려면 값이 있어야 한다)."""
    VendorAccount = apps.get_model("repairs", "VendorAccount")
    VendorAccount.objects.filter(until__isnull=True).update(until=timezone.localdate() + timedelta(days=365))


class Migration(migrations.Migration):
    """역방향: 컬럼을 NULL 허용으로 다시 만들고(RemoveField의 역) fill_until로 채운 뒤 AlterField의 역으로 NOT NULL. 해제한 계정은 되살리지 않는다."""

    dependencies = [
        ("repairs", "0003_vendor_plan_cut"),
    ]

    operations = [
        migrations.RunPython(revoke_expired, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="vendoraccount",
            name="until",
            field=models.DateField(null=True, verbose_name="사용 기간 끝"),
        ),
        migrations.RunPython(migrations.RunPython.noop, fill_until),
        migrations.RemoveField(model_name="vendoraccount", name="until"),
        migrations.AddField(
            model_name="vendoraccount",
            name="nonce",
            field=models.CharField(blank=True, max_length=64, verbose_name="링크를 다시 만드는 값"),
        ),
    ]
