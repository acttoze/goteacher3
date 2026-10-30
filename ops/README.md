# ops — 배포·운영 (gt-ops)

스테이징 `https://t.goteacher.xyz` = 서버 `oci_tailscale`의 `/home/ubuntu/prod/goteacher` (이 저장소 clone).

- `compose.yml` — compose 프로젝트 `goteacher`: `goteacher-app`(gunicorn) · `db`(PostgreSQL 17) · `redis` · `goteacher-worker`(RQ, 같은 이미지, 작업당 300초·메모리 2.5GB).
- `Dockerfile` — 빌드 컨텍스트는 저장소 루트(`.dockerignore`).
- `.env.example` — 서버 `ops/.env`(chmod 600) 견본. 실제 값은 서버에서 생성하고 저장소·출력에 남기지 않는다.

배포(B2, 자동): main 푸시 → GitHub Actions `ci`(ruff·테스트, 작업 이름 `test`) → 서버 crontab이 1분마다
`ops/deploy.sh` 실행. origin/main의 체크 `test`가 success일 때만 reset → build → migrate → up.
실패한 커밋은 `var/deploy-skip`에 적고 넘어간다(CI를 다시 돌려 통과했다면 이 파일을 지우면 다시 시도).
기록은 `var/deploy.log`, 마지막 반영 커밋은 `var/deployed`.

- 무중단(G6): 앱은 `ops/rollout.sh`가 새 컨테이너를 먼저 띄워 healthy 확인 → 옛 것에 `/tmp/gt-drain`(→ `/healthz` 503,
  Traefik 서비스 health check가 1초 안에 뺌) → 옛 것을 내린다. 새 것이 안 뜨면 새 것만 지우고 옛 것이 계속 응답.
  `compose.yml`의 traefik 라벨을 바꾼 배포만 공존할 수 없어(설정이 다른 같은 라우터 → Traefik이 둘 다 버림) 수 초 끊긴다.
  앱 컨테이너 번호는 교체마다 바뀐다(`goteacher-goteacher-app-N`). 이름 대신 `docker compose ps -q goteacher-app`.
- 수동 반영(CI 없이): `GT_SKIP_CI=1 ops/deploy.sh`
- 되돌리기: `git revert` 뒤 푸시(서버에서 손으로 reset하지 않는다 — 다음 분에 main으로 돌아간다)

백업(B2): crontab 매일 04:30 `ops/backup.sh` → `var/backups/goteacher-날짜.dump`(pg_dump -Fc), 최근 7개 보관.
복구 시험: `ops/restore_test.sh [파일]` — 네트워크 없는 임시 컨테이너 `goteacher-restoretest`에 풀어
표마다 행 수를 운영 db와 비교하고 지운다. 운영 db에 실제로 복구하는 것은 리드 승인 뒤:
`docker compose exec -T db pg_restore -U goteacher -d goteacher --clean --if-exists < 파일`.

감시(G6): crontab 5분마다 `ops/monitor.sh` — Traefik 경유 `/healthz`·worker·db·디스크 여유 15GB·백업 26시간 안·
최근 5분 500 오류(`config/logsafe.py`: 경로 토큰 가림, 예외 메시지 없이 종류·위치만)·배포 실패. 문제 목록이 바뀔 때만
`var/alerts.log`에 남긴다(같은 문제는 6시간마다). 보내는 수단은 미정 → `notify()`. 지금 상태만 보기: `GT_DRY=1 ops/monitor.sh`.

공용 서버 규칙: 이 프로젝트 밖의 컨테이너·네트워크·볼륨과 Traefik 설정은 건드리지 않는다.
`docker system prune`·`volume prune` 금지. DB·Redis 포트는 호스트에 열지 않는다.

정기 작업(D3·G2): crontab 5분마다 `remind`(🔔 리마인드 쌓기) 다음 `push_digest`(08:20·12:20·14:40 묶음 웹 푸시).
같은 줄에서 순서대로 부른다(08:20 리마인드가 아침 묶음에 들어가게). 두 명령 모두 여러 번 불려도 한 번만 보낸다.

VAPID 키(G2) 만들기 — 서버에서 한 번, 출력은 `ops/.env`에만 붙이고 화면·저장소에 남기지 않는다:
`docker compose -f ops/compose.yml exec -T goteacher-app python -c "from py_vapid import Vapid; from py_vapid.utils import b64urlencode as e; from cryptography.hazmat.primitives import serialization as s; v=Vapid(); v.generate_keys(); print('VAPID_PRIVATE_KEY='+e(v.private_key.private_numbers().private_value.to_bytes(32,'big'))); print('VAPID_PUBLIC_KEY='+e(v.public_key.public_bytes(s.Encoding.X962, s.PublicFormat.UncompressedPoint)))" >> ops/.env`
