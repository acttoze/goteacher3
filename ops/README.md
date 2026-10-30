# ops — 배포·운영 (gt-ops)

스테이징 `https://t.goteacher.xyz` = 서버 `oci_tailscale`의 `/home/ubuntu/prod/goteacher` (이 저장소 clone).

- `compose.yml` — compose 프로젝트 `goteacher`: `goteacher-app`(gunicorn) · `db`(PostgreSQL 17) · `redis` · `goteacher-worker`(RQ, 같은 이미지, 작업당 300초·메모리 2.5GB).
- `Dockerfile` — 빌드 컨텍스트는 저장소 루트(`.dockerignore`).
- `.env.example` — 서버 `ops/.env`(chmod 600) 견본. 실제 값은 서버에서 생성하고 저장소·출력에 남기지 않는다.

배포(B2, 자동): main 푸시 → GitHub Actions `ci`(ruff·테스트, 작업 이름 `test`) → 서버 crontab이 1분마다
`ops/deploy.sh` 실행. origin/main의 체크 `test`가 success일 때만 reset → build → migrate → up.
실패한 커밋은 `var/deploy-skip`에 적고 넘어간다(CI를 다시 돌려 통과했다면 이 파일을 지우면 다시 시도).
기록은 `var/deploy.log`, 마지막 반영 커밋은 `var/deployed`.

- 수동 반영(CI 없이): `GT_SKIP_CI=1 ops/deploy.sh`
- 되돌리기: `git revert` 뒤 푸시(서버에서 손으로 reset하지 않는다 — 다음 분에 main으로 돌아간다)

백업(B2): crontab 매일 04:30 `ops/backup.sh` → `var/backups/goteacher-날짜.dump`(pg_dump -Fc), 최근 7개 보관.
복구 시험: `ops/restore_test.sh [파일]` — 네트워크 없는 임시 컨테이너 `goteacher-restoretest`에 풀어
표마다 행 수를 운영 db와 비교하고 지운다. 운영 db에 실제로 복구하는 것은 리드 승인 뒤:
`docker compose exec -T db pg_restore -U goteacher -d goteacher --clean --if-exists < 파일`.

공용 서버 규칙: 이 프로젝트 밖의 컨테이너·네트워크·볼륨과 Traefik 설정은 건드리지 않는다.
`docker system prune`·`volume prune` 금지. DB·Redis 포트는 호스트에 열지 않는다.
