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
- 한글 회귀(F4): 표본 100개(`ops/regress_pick.py`, seed 1030)로 `hwp.bench read`·`fill`을 새 이미지에서 돌린다
  (네트워크 없는 임시 컨테이너 `goteacher-regress-*`, 스왑 없는 메모리 상한, CPU 우선순위 낮춤, 병렬 2). 100개 전체는
  서버 실측 약 4분(read 26초·fill 218초)이고 푸시→반영이 이미 약 2분 45초(CI 99초·cron 대기·배포 27초)라 3분 기준 안에
  못 넣는다 → 둘로 나눈다.
  - `quick`(관문): build 뒤 migrate·교체 **전에**. 빠른 묶음 49개(`quick.txt` — 2026-10-30 서버 실측 read+fill 1.5초 이하,
    hwp 28·채우기 후보 34)만, 약 15초·90초 제한. 미달이면 반영하지 않는다: 새 이미지를 버리고 latest를 직전 이미지로 → `var/deploy-skip`·`var/alerts.log`.
    도는 동안 뒤의 full은 `docker pause`로 멈춘다(시간 비교가 흔들리지 않게).
  - `full`(100개 전부, 약 4분·900초 제한): 반영이 끝난 뒤 뒤에서(`var/regress.log`). 미달이면 `var/alerts.log`에 경보만 — 이미 운영 중이므로
    되돌리기(`git revert` 푸시)는 사람이 판단. 이미 도는 중에 새 배포가 오면 끝난 뒤 그때의 latest로 한 번 더 돈다.
  - 기준: `var/regress-baseline-{quick,full}.json`(직전 통과 결과) 대비 통과 수(read_ok·read_tables·fill_hwpx·fill_hwp·
    roundtrip)가 하나라도 줄거나 시간이 2배를 넘으면 미달. 통과하면 이번 결과가 새 기준. 기준 파일이 없으면 이번 결과가 기준.
    결과 `var/regress/{quick,full}/`(파일 이름=해시·통과 여부·시간만, 오류 문구·문서 내용 없음).
  - 표본은 실제 학교 문서라 **저장소·GitHub Actions에 두지 않는다.** 서버 `/home/ubuntu/goteacher-samples`(700, 파일 600)에만.
    표본이 없으면 회귀를 생략하고 경보만 남긴 채 배포한다.
  - 표본 선정(로컬, 아카이브 `goteacher-forms-archive/raw`): `python3 -I ops/regress_pick.py <raw> > list.txt`
    — hwp 70·hwpx 30, 종류마다 크기 4등분에서 고루, seed 1030(같은 아카이브면 같은 목록). 출력은 상대 경로 목록뿐.
  - 올리기(로컬에서): 목록대로 저장소 밖 폴더 `<표본>/`에 복사하고 `chmod 600` →
    `ssh oci_tailscale 'install -d -m 700 ~/goteacher-samples'` → `rsync -a <표본>/ oci_tailscale:goteacher-samples/`
    (권한 보존). `quick.txt`(파일 이름 목록)도 같은 폴더에.
  - 의도한 변화로 통과 수가 줄었으면(예: 위험 파일을 새로 거부): 확인 뒤 서버에서 해당 `var/regress-baseline-*.json`과
    `var/deploy-skip`을 지운다 → 다음 분 배포가 그 결과를 새 기준으로 잡는다. 표본을 바꿨을 때도 기준 파일을 지운다.
  - 수동 실행: `ops/regress.sh quick|full [이미지]` (종료 코드 0 통과 · 1 미달 · 2 표본 없음 · 3 full 이미 도는 중)
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

보관 기간 삭제(E3): crontab 하루 한 번 03:40(04:30 DB 백업 전) `purge` — 마지막 활동 1년·만든 뒤 3년 지난 모으기(제출 값·
올린 파일·결과 파일), 모으기에 안 쓴 올린 파일(24시간), 멈춘 한글 작업, 고아 파일, 저장 크기 정합. 건수만 출력한다.
`40 3 * * * cd <저장소> && docker compose -f ops/compose.yml exec -T goteacher-app python manage.py purge`
먼저 보기: 같은 명령 끝에 `--dry-run`(지우지 않고 건수만).

VAPID 키(G2) 만들기 — 서버에서 한 번, 출력은 `ops/.env`에만 붙이고 화면·저장소에 남기지 않는다:
`docker compose -f ops/compose.yml exec -T goteacher-app python -c "from py_vapid import Vapid; from py_vapid.utils import b64urlencode as e; from cryptography.hazmat.primitives import serialization as s; v=Vapid(); v.generate_keys(); print('VAPID_PRIVATE_KEY='+e(v.private_key.private_numbers().private_value.to_bytes(32,'big'))); print('VAPID_PUBLIC_KEY='+e(v.public_key.public_bytes(s.Encoding.X962, s.PublicFormat.UncompressedPoint)))" >> ops/.env`
