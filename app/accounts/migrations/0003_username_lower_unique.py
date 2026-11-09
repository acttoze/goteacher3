"""P1: 아이디는 대소문자 무시로 유일(리드 결정). auth_user에 lower(username) 유일 인덱스(PostgreSQL·SQLite 공통 문법).
구글(google:)·손님(guest:) username도 함께 걸리지만 sub·임의 토큰이라 대소문자만 다른 값이 생기지 않는다."""
from django.db import migrations


class Migration(migrations.Migration):
    # auth 마지막 마이그레이션 뒤에: SQLite는 auth_user 열을 바꿀 때 표를 다시 만들어 이 인덱스를 지운다
    dependencies = [("accounts", "0002_password_change_required"), ("auth", "0012_alter_user_first_name_max_length")]

    operations = [
        migrations.RunSQL(
            "CREATE UNIQUE INDEX accounts_username_lower_uniq ON auth_user (lower(username));",
            reverse_sql="DROP INDEX accounts_username_lower_uniq;",
        ),
    ]
