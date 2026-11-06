"""이미 저장된 '바뀐 부분'의 옛 문구 '시간 미정'을 '종일'로(QC 2026-11-06). 되돌리기는 하지 않는다."""
from django.db import migrations

OLD, NEW = "시간 미정", "종일"


def forward(apps, schema_editor):
    Event = apps.get_model("today", "Event")
    for pk, parts in Event.objects.filter(changed_from__isnull=False).values_list("pk", "changed_from"):
        if not isinstance(parts, list):
            continue
        new = [[x.replace(OLD, NEW) if isinstance(x, str) else x for x in p] if isinstance(p, list) else p for p in parts]
        if new != parts:
            Event.objects.filter(pk=pk).update(changed_from=new)  # updated_at(바뀐 것 순서)은 그대로


class Migration(migrations.Migration):

    dependencies = [
        ("today", "0002_event_scope"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
