# ops — 배포·운영 (gt-ops)

스테이징 `https://t.goteacher.xyz` = 서버 `oci_tailscale`의 `/home/ubuntu/prod/goteacher` (이 저장소 clone).

- `compose.yml` — compose 프로젝트 `goteacher`: `goteacher-app`(gunicorn) · `db`(PostgreSQL 17) · `redis` · `goteacher-worker`(RQ, 같은 이미지, 작업당 300초·메모리 2.5GB).
- `Dockerfile` — 빌드 컨텍스트는 저장소 루트(`.dockerignore`).
- gunicorn은 gthread 워커 2개·스레드 4개(요청 슬롯 8개, DB 연결 최대 8개, 교체 중 16개). 느린 업로드가 슬롯 하나만 쥐므로 다른 요청이 계속 응답한다.
  gthread에서 `--timeout`(30초)은 워커 생존 신호 제한이라 느린 요청을 끊지 않는다. 대신 멈춘 요청도 끊지 않으므로 앱의 외부 호출에는 자체 제한이 있어야 한다.
  `--keep-alive 0`: 앱이 본문을 읽기 전에 응답하면(로그아웃·없는 공간 등) 켜 둔 연결에서는 Traefik이 대부분 502를 준다. 요청마다 닫으면 크게 줄지만 간헐적으로 남는다(앱에서 본문을 읽고 응답해야 없어진다).
- 본문 버퍼링(라벨만, 공용 Traefik 전역 설정은 그대로): Traefik이 본문을 다 받은 뒤 앱에 넘기므로 느린 본문이 슬롯을 쥐지 못한다.
  | 라우터 | 대상 | 본문 상한 | 동시 |
  |---|---|---|---|
  | `gt-staging` | GET·HEAD. 버퍼링 없음(buffering은 응답도 쌓는다) | — | — |
  | `gt-staging-body` | 그 밖의 메서드 | 3MB | — |
  | `gt-staging-up101` | POST `/s/*/board/new`, `/s/*/board/*/edit` | 101MB | 8 |
  | `gt-staging-up31` | POST `/s/*/hwp/upload` | 31MB | 8 |
  | `gt-staging-up9` | POST `/s/*/repairs/new`, `/s/*/repairs/*/edit`, `/s/*/meals/*/upload`, `/m/*` | 9MB | 16 |

  상한을 넘으면 Traefik의 413, 동시 건수를 넘으면 429(앱 화면이 아니라 Traefik 글자 응답). 파일을 받는 새 경로를 만들면 여기와 `compose.yml`에 추가한다(빠뜨리면 3MB에서 413).
  64KB를 넘는 본문은 **Traefik 컨테이너 `/tmp`, 곧 공용 호스트 디스크**에 쌓인다. 파일은 만들자마자 unlink되어 `ls`·`du`에 보이지 않고, 열린 동안만 공간을 쓴다(연결이 끊기면 수십 초 안에, Traefik이 재시작되면 바로 풀린다).
  인증 전에 받으므로 동시 건수로 최대량을 묶었다: 8×101MB + 8×31MB + 16×9MB = 약 1.2GB(그날 여유 59GB). 업로드 동시 건수가 차도 다른 요청은 영향이 없다. 건수를 올리면 이 합을 다시 계산한다.
  body 오류 시 공용 Traefik 로그에 라우터 이름만 있는 ERR 한 줄이 남는다(경로·토큰 없음).
- **업로드 상한 시간은 Traefik이 정한다**: 공용 Traefik은 시간 제한 인자가 없어 기본 `readTimeout` 60초다(로컬 v3.7.5 재현, 공용 설정은 바꾸지 않는다).
  60초 안에 끝나려면 8MB 약 1.1Mbps, 30MB 약 4Mbps, 100MB 약 13.3Mbps가 필요하다.
- 기반 이미지 4개(python·uv·postgres·redis)는 `@sha256:` 다이제스트로 고정. 갱신: `docker buildx imagetools inspect <이름:태그>`의 Digest로 바꿔 푸시(앱 이미지는 다음 배포에 반영, db·redis는 deploy가 다시 만들지 않으므로 승인 뒤 서버에서 `docker compose up -d db redis`).
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
쓰는 중인 `*.part`는 실패해도 지운다(디스크가 찬 채로 남지 않게).

저장 한도 근거(2026-11-04): 기본값 서비스 전체 `HWP_TOTAL_QUOTA` 5GB·학교 하나 `HWP_SCHOOL_QUOTA` 2GB(`app/config/settings.py`, 서버 `.env`에 따로 두지 않음).
사진·한글은 거의 압축되지 않으므로 미디어 S를 채우면 원본 S + 백업 7벌 + 쓰는 중 1벌 = 약 9S를 쓴다. 5GB면 45GB(20GB였다면 180GB).
그날 서버 여유는 59GB였고 공용 디스크라 다른 서비스로도 줄어든다. 디스크 감시 기준 20GB = 안전 여유 15GB + 다음 백업 1벌 최대 5GB.
여유 59GB에서 다른 서비스가 그대로라면 미디어 약 4.3GB((59-20)/9)에서 경보가 울린다. 한도를 올릴 때는 이 계산을 다시 한다.

감시(G6): crontab 5분마다 `ops/monitor.sh` — Traefik 경유 `/healthz`·worker·db·디스크 여유 20GB·백업 26시간 안·
최근 5분 500 오류(`config/logsafe.py`: 경로 토큰 가림, 예외 메시지 없이 종류·위치만)·배포 실패·purge 성공 26시간 안·
Redis 메모리 80% 이하·gunicorn 워커 강제 종료(줄 수)·앱 `oom_kill` 증가·`/healthz` 확인 중 만난 502. 접근 로그를 끈 라우터라 사용자가 받은 502 전체는 셀 수 없다.
문제 목록이 바뀔 때만 `var/alerts.log`와 텔레그램(`ops/notify.sh`)으로 알린다(같은 문제는 6시간마다).
지금 상태만 보기: `GT_DRY=1 ops/monitor.sh`.
문제와 별도로 처리할 건수만 알린다: 학교 플랜 신청(대기가 늘었을 때 한 번, `var/plan-pending`), 후기 한 줄 대기(있으면 하루 한 번
09시 뒤, `var/review-day`). 알림에는 숫자만 나간다. 이름·메일·글은 서버에서 아래 운영 명령으로 본다.

공용 서버 규칙: 이 프로젝트 밖의 컨테이너·네트워크·볼륨과 Traefik 설정은 건드리지 않는다.
`docker system prune`·`volume prune` 금지. DB·Redis 포트는 호스트에 열지 않는다.

정기 작업(D3·G2): crontab 5분마다 `remind`(🔔 리마인드 쌓기) 다음 `push_digest`(08:20·12:20·14:40 묶음 웹 푸시).
같은 줄에서 순서대로 부른다(08:20 리마인드가 아침 묶음에 들어가게). 두 명령 모두 여러 번 불려도 한 번만 보낸다.

보관 기간 삭제(E3): crontab 하루 한 번 03:40(04:30 DB 백업 전) `purge`. 건수만 출력한다. 지우는 것(앞에서부터 차례로):
- 복무: 지난 날짜 7일
- 수리 요청: 완료 90일 · 미완료 1년
- 식판 사진: 30일(폰 올리기 링크는 만료되면 바로)
- 업체 접속 기록 30일 · 업체 계정: 해제되거나 기간이 끝나고 30일
- 명부 이력(권한 변경 기록): 1년
- 업체 추천: 처리한 것은 다음 purge에서, 대기 중이어도 1년
- 후기: 3년 · 조치 이력: 3년
- 공지·첨부·봄 기록: 플랜이 끝난 날부터 1년
- 플랜 신청 정보: 처리한 날부터 1년, 대기 중이면 접수한 날부터 1년
- 나간 회원의 나만 일정, 멈춘 한글 작업
- 모으기(제출 값·올린 파일·결과 파일): 마지막 활동 1년 · 만든 뒤 3년
- 모으기에 안 쓴 올린 파일: 24시간
- 고아 파일(1시간 유예), 저장 크기 정합

단계 사이에 예외를 잡지 않는다. 한 단계가 실패하면 앞 단계는 지워진 채로 남고 뒤 단계는 그날 돌지 않으며, 종료 코드가 0이 아니라
성공 표시가 갱신되지 않는다 → 감시가 26시간 뒤 경보. 고아 파일 "못 지움 N개"는 실패로 치지 않는다(`var/purge.log`에서 본다).

로그인 정리(G6): crontab 하루 한 번 03:50 `cleanup_sessions`(purge 뒤·백업 전, 기록은 같은 `var/purge.log`, 건수만) — 만료 세션, 1년 지났거나 비활성 사용자의 기기 토큰,
회원 자리 없이 30일 넘게 안 들어온 손님. purge와 줄이 달라 서로 막지 않는다. 성공 표시·감시는 없다.

```
40 3 * * * cd /home/ubuntu/prod/goteacher/ops && docker compose exec -T goteacher-app python app/manage.py purge >> /home/ubuntu/prod/goteacher/var/purge.log 2>&1 && touch /home/ubuntu/prod/goteacher/var/purge-ok
```
먼저 보기: `purge --dry-run`(지우지 않고 건수만, 성공 표시는 손으로 갱신하지 않는다).

운영 명령: `ops/`에서 `docker compose exec -T goteacher-app python app/manage.py <명령>`. 출력은 터미널에서만 보고 로그·알림으로 보내지 않는다.

학교 플랜 `school_plan`:
- `requests` — 대기 신청 목록. 첫 줄 "대기 N건", 다음 줄부터 담당자 이름·메일
- `<NEIS 코드> --on --until YYYY-MM-DD` — 켜기·기한 연장(동학년 합침, 다시 돌려도 안전). 학교에 관리자가 없으면 아래 하나가 필수
  - `--manager=<회원 key>` · `--manager-request=<신청 ID>`(requests의 #번호. 회원 0명 학교도 가능)
- `<NEIS 코드> --remove-manager=<회원 key>` — 관리자 해제(마지막 관리자는 안 됨)
- `<NEIS 코드> --off` — 플랜만 끈다(기한을 오늘로 앞당겨 공지 보관 기간 시작). 합친 공간은 되돌리지 않는다
- 학교 코드가 겹치면 `--atpt <시도교육청 코드>`. 회원 key는 `-`로 시작할 수 있어 `=`로 붙여 쓴다

협력업체 `partners`:
- `add --name 이름 --cat 분류 --area 지역 --desc 소개 [--phone 번호] [--url 주소] [--tags "가, 나"] [--public]`
- `publish <slug>` · `hide <slug>`
- `suggestions`(대기 추천 목록, 추천인 이름·연락처 포함) · `suggestion-done <id> [--reject]`
- `reviews --partner <slug>` · `reviews --pending`(확인 기다리는 한 줄, 글 포함)
- `review-text-ok <번호>` · `review-text-no <번호> [--reason 까닭]`
- `review-hide <번호> --reason 까닭 [--note 메모] [--by 이름]` · `review-show <번호> [--reason 까닭]` · `review-delete <번호> --reason 까닭`
- `<번호>`는 화면의 신고 번호(R-…). 까닭·메모에 개인정보를 적지 않는다

도메인 전환 체크리스트(G7). 실행은 승인 뒤, 구버전 처리는 사용자 결정:
- [ ] **운영 라우터 6개 모두 접근 로그 끄기 라벨** — 라우터가 `gt-staging`·`-body`·`-up101`·`-up31`·`-up9`·`-http` 6개다. 운영용으로 같은 구성을 만들 때 하나도 빠짐없이
  `traefik.http.routers.<이름>.observability.accesslogs: "false"`를 붙인다. 처리방침이 "요청 주소가 담긴 접속 기록은 남기지 않습니다"이고
  공용 Traefik은 `--accesslog=true`라 빠뜨리면 바로 어긋난다. 공용 Traefik 설정은 바꾸지 않는다.
- [ ] **운영 라우터에도 같은 버퍼링·동시 건수 미들웨어**(위 표). 업로드 라우터의 priority가 일반 라우터보다 높아야 한다. 두 Host를 함께 받으면 동시 건수의 합으로 디스크 최대량을 다시 계산한다.
- [ ] **토큰이 경로에 있는 주소**: `/tv/` `/m/` `/v/` `/n/`, 그 밖에 `/s/` `/c/` `/j/` `/school-plan/q/`. 라벨이 빠진 라우터나 리다이렉트를 거치면 전부 로그에 남는다.
- [ ] 전환 뒤 확인: 운영 주소로 요청을 보내고 `docker logs --since 10m traefik 2>&1 | grep -cE '<운영 라우터>|goteacher\.xyz'`를 본다. 구버전 라우터(`goteacher-xyz`)가 남아 있으면 줄이 잡히므로 라우터 이름으로 구별한다. 내용을 화면에 찍지 않고 건수만 본다.
- [ ] `traefik:latest`(2026-11-04 v3.7.5). 라우터 라벨은 v3.1 이상 기능이다. 공용 Traefik이 바뀌면 위 확인을 다시 한다.
- [ ] 같은 Host를 쓰는 구버전 라우터 `goteacher-xyz`와 겹치지 않게 구버전을 먼저 정리(사용자 결정).
- [ ] 라벨을 바꾸는 배포는 수 초 끊긴다(위 무중단 설명). 사용이 적은 때 한다.
- [ ] `ops/.env`: `SITE_URL=https://goteacher.xyz`, `SECURE_HSTS_SECONDS=31536000`.
- [ ] `ops/monitor.sh`의 `host=t.goteacher.xyz`를 운영 주소로(스테이징을 끄지 않으면 둘 다 보는지 결정).
- [ ] 구글 클라이언트에 `https://goteacher.xyz` 원본·`/auth/google/callback` 추가, 동의 화면 브랜딩 → 프로덕션 게시(`meeting/28_tasks_r1.md` G7).

VAPID 키(G2) 만들기 — 서버에서 한 번, 출력은 `ops/.env`에만 붙이고 화면·저장소에 남기지 않는다:
`docker compose -f ops/compose.yml exec -T goteacher-app python -c "from py_vapid import Vapid; from py_vapid.utils import b64urlencode as e; from cryptography.hazmat.primitives import serialization as s; v=Vapid(); v.generate_keys(); print('VAPID_PRIVATE_KEY='+e(v.private_key.private_numbers().private_value.to_bytes(32,'big'))); print('VAPID_PUBLIC_KEY='+e(v.public_key.public_bytes(s.Encoding.X962, s.PublicFormat.UncompressedPoint)))" >> ops/.env`
