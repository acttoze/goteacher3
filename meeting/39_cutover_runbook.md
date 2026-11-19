# 39. 운영 도메인 전환 런북 (G7, 2026-11-19 준비 · gt-ops)

지금 구조: 구 고티처(`goteacher-prod`, PHP·공용 `mysql`)가 `goteacher.xyz`에서 운영 중이고, 새 고티처는 `t.goteacher.xyz`에서
38곳을 동기화(`goteacher-legacy-sync`, 5초 증분·30초 대조, `LEGACY_READONLY=1`)로 함께 쓴다. 전환은 **구를 쓰기 중지 → 동기화 마지막 대조 → 새 쪽을 goteacher.xyz로**다.
35번 6절의 "최종 덤프 → `[시연]` 삭제 → `import_legacy`"는 이 구조에서는 하지 않는다(아래 판단).

## 0. 판단: `import_legacy` 전체 재적재는 필요 없다

- 이관 학교 38곳의 회원·일정·복무·댓글·첨부·게시판·관리자는 동기화가 30초마다 구 DB 전체와 지문으로 대조한다. 11/19 14:10 대조 결과 바뀐 행 0·실패 0(24시간 실패 0줄).
- 동기화가 **보지 않는 것**은 이관 밖 학교 회원(D10, 계정만)이다. 11/16 재적재 뒤 새 가입·비밀번호 변경·탈퇴가 안 들어온다. `ops/legacy_gap.py`로 센다. **11/19 14:30 결과 0건**(구 회원 2,930 중 계정 없음 0 · 비밀번호 뒤처짐 0 · 탈퇴 미반영 0).
- `[시연]` 데이터는 이미 없다(V4에서 삭제, 11/19 확인 일정 0·자리 0). 대신 이관 밖 데이터가 남아 있다: 김해봉황초 학교 플랜(~2027-12-31, 공간 1) · 아이디 계정 3개(11/10 가입, 시험 계정으로 보임) · 운영자 구글 계정 1. → 🙋 리드 결정 D-1.
- 그러므로 전환 날 할 일은 "쓰기 중지 → 대조 2번 → 계정 차이 0 확인"뿐이다. 차이가 0이 아니면 3-b.

## 1. 미리 할 것(전환 날 전, 되돌릴 수 있음)

| # | 할 일 | 누가 | 명령·확인 | 되돌리기 |
|---|---|---|---|---|
| 1-1 | 이 준비물 커밋·푸시(`ops/compose.prod.yml`·`cutover.sh`·`legacy_gap.py`·`monitor.sh`·`g7/`·`.gitignore`·README) | 리드 | 3분 안에 서버 반영. `compose.prod.yml`은 링크가 없으면 읽히지 않는다(로컬 compose config로 확인: 링크 없을 때 라벨 69개 = 서버 실행 라벨과 같음) | `git revert` |
| 1-2 | 구 사이트 쓰기 중지 코드 배포(플래그 없으면 동작 변화 0) | 🙋 승인 뒤 리드 | 아래 4절 | 구 서버 `git reset --hard <이전>` |
| 1-3 | 구글 OAuth 클라이언트에 원본 `https://goteacher.xyz`, 리디렉션 `https://goteacher.xyz/auth/google/callback` **추가**(t 주소는 그대로 둠) | 🙋 사람(구글 콘솔) | 추가는 지금 해도 아무 영향 없다. **안 하면 전환 순간 구글 로그인 전부 실패**(운영자 `dev moon` 포함) | 항목 삭제 |
| 1-4 | 동의 화면 프로덕션 게시 | 🙋 사람 | 승인 도메인 `goteacher.xyz`는 하위 주소를 포함하므로 지금 `https://t.goteacher.xyz/privacy`·`/terms`로 게시할 수 있다(전환 뒤 주소만 바꿈). 범위가 openid·email·profile뿐이라 심사 없음(로고를 올리면 브랜드 확인 며칠). **전환을 막지 않는다** — 이관 회원은 아이디 로그인, 다만 게시 전에는 테스트 사용자 외 새 구글 가입 불가 | 테스트로 되돌림 |
| 1-5 | 학교 안내(쓰기 중지 시각, 같은 주소·같은 아이디, 구 사이트는 `old.goteacher.xyz` 3개월 보기 전용) | 🙋 사람 | 35번은 12/7~11 안내 → 12/12 전환이었음. "최대한 빨리"면 안내 기간을 사용자가 정한다. 구 사이트 공지·팝업(V9)으로 같은 날 알릴 수 있다 | — |
| 1-6 | 전환 전날 마른 실행 | 리드 | `ssh -t oci_tailscale 'cd ~/prod/goteacher && ops/cutover.sh'` → "걸림"이 플래그 하나만 남아야 | — |

## 2. 전환 당일(사용 적은 시간, 예: 18시 이후)

| 단계 | 명령 | 시간 | 확인 | 되돌리기 |
|---|---|---|---|---|
| 2-1 🙋 쓰기 중지 승인 | — | — | 사용자 "중지" 한마디 | — |
| 2-2 구 사이트 쓰기 중지 | `ssh oci_tailscale 'touch ~/prod/goteacher_prod/data/readonly.on'` | 1초 | 구 사이트 글쓰기 단추 → "읽기 전용" 경고, 로그인·보기는 됨 | `rm …/data/readonly.on` |
| 2-3 전환 | `ssh -t oci_tailscale 'cd ~/prod/goteacher && ops/cutover.sh --execute'` → 확인 문구 `goteacher.xyz 전환` | 약 2~3분 | 스크립트 6단계 확인이 모두 ok. 새 앱 끊김은 라벨 교체 순간 수 초 | 3절 |
| 2-4 사람 눈 확인 | 브라우저: `https://goteacher.xyz` 이관 계정 아이디 로그인 · 구글 로그인 · 이관 학교 일정에 쓰기 가능(읽기 전용 띠 없음) · `https://t.goteacher.xyz/…` 옛 링크가 같은 경로로 이동 · `https://old.goteacher.xyz` 보기 | 10분 | | 3절 |
| 2-5 다음 날 | `GT_DRY=1 ops/monitor.sh` · `var/backup.log`(04:30) · 로그인 실패 문의 | | | |

`cutover.sh --execute`가 하는 일(순서대로, 실패하면 그 자리에서 멈춤):
1. 0단계 점검: `.env` 600, 링크 없음, SITE_URL이 t, 구 사이트 중지 코드·플래그 있음, 동기화 실행 중, 디스크 20GB 이상, 스테이징 healthz 200, 다른 서비스 3개(shamar-prod-web·lecture2-web·memos)의 컨테이너 ID·시작 시각·응답 코드 기록. 배포 잠금(`var/deploy.lock`)을 잡아 cron 배포가 끼지 않게 한다.
2. 백업 `var/backups/before-g7-<시각>.dump`(pg_dump, `pg_restore -l`로 읽힘 확인, 4초). 지우지 않는다.
3. 동기화 정지 → `legacy_sync --once` 두 번, 두 번째가 `바뀐 행 0 · 반영 0 · 실패 0`이어야.
4. `legacy_gap.py` → `GAP_TOTAL=0`이어야. 그 뒤 동기화 컨테이너 제거.
5. `.env` 백업 `ops/.env.before-g7-<시각>`(600) → `SITE_URL=https://goteacher.xyz` · `SECURE_HSTS_SECONDS=31536000` · `LEGACY_READONLY=` · `LEGACY_DB_URL=` · `LEGACY_SITE_URL=https://old.goteacher.xyz`.
6. `ln -s compose.prod.yml ops/compose.override.yml` → `bash ops/rollout.sh`(라벨이 바뀌어 옛 앱을 먼저 내림 → 수 초 끊김).
7. 확인: goteacher.xyz healthz 200 · `curl -I /` 200·302 · http→https · t → goteacher.xyz 같은 경로 · old.goteacher.xyz 200 · 구글 콜백이 앱에 닿음 · 공용 Traefik 접근 로그에 운영 라우터 줄 0 · 다른 서비스 3개 그대로 · `GT_DRY=1 monitor.sh`(감시 주소는 SITE_URL을 따라감).

### 3-b. 계정 차이가 0이 아닐 때(지금은 0)
동기화를 멈춘 상태 그대로: 맥에서 `ops/legacy_extract.sh`(3분 20초, M1) → 서버로 → `import_legacy <덤프> --school <이관 학교 하나>`(계정 단계는 덤프의 모든 회원을 보므로 빠진 계정만 더한다, 멱등) → 다시 `cutover.sh --execute`. 비밀번호 뒤처짐·탈퇴 미반영은 importer가 고치지 않으므로 건수가 적으면 운영자 임시 비밀번호(P1)·계정 끄기로 손으로 맞춘다.

## 3. 되돌리기

- **2-3 전**: 구 사이트 플래그만 지운다(`rm ~/prod/goteacher_prod/data/readonly.on`). 새 쪽은 바뀐 것이 없다.
- **2-3이 멈춘 경우**: 동기화를 멈춘 채 멈춘다. 다시 켜기 `cd ops && docker compose up -d --no-deps goteacher-legacy-sync`, 플래그 삭제.
- **2-3 뒤**: `ssh -t oci_tailscale 'cd ~/prod/goteacher && ops/cutover.sh --rollback ops/.env.before-g7-<시각>'`(확인 문구 `전환 되돌리기`, 약 1분, 수 초 끊김): 링크 삭제 → `.env` 복원 → 앱 교체 → 동기화 다시 켬 → goteacher.xyz는 다시 구 컨테이너(구 라우터는 그대로 있었고 가려져 있었을 뿐). 플래그를 지워 구 사이트 쓰기를 연다.
  그 사이 새 쪽에 쓴 글은 새 DB에 남고 구에는 없다(35번 6절과 같음, 따로 받아 둠). 이관 학교는 다시 읽기 전용이 된다.
- HSTS 1년: goteacher.xyz에 붙는다(하위 주소 미포함). 구 사이트도 HTTPS라 되돌려도 문제없다.
- 운영 DB 복구가 필요할 때만(리드 승인): `docker compose exec -T db pg_restore -U goteacher -d goteacher --clean --if-exists < var/backups/before-g7-<시각>.dump`.

## 4. 구 사이트 쓰기 중지(준비만, 배포·푸시 안 함)

- 패치 `ops/g7/old_site_readonly.patch`: 구 저장소에 `extend/zz_readonly.extend.php` 하나 추가(기존 파일 수정 없음, 그누보드가 `extend/*.php`를 자동으로 읽음). `data/readonly.on`이 있을 때만:
  GET·HEAD가 아닌 요청 전부 막음(로그인·로그아웃·비밀글 확인·세션 확인만 예외) + GET으로 쓰는 파일(글쓰기 화면·삭제·추천·회원 가입/탈퇴·`db.php`·`ajax.work.php`·`ajax.add/delete/edit/update…`) 막음.
  화면은 "새 고티처(goteacher.xyz)에서 같은 아이디로" 경고 후 goteacher.xyz로, ajax는 403 글자. cron(명령줄)은 막지 않는다(급식 동기화·메일 큐 계속).
- 시험: PHP 7.4.33(운영과 같은 계열)·8.5에서 문법 통과, 판정 25건 틀림 0(`php ops/g7/old_site_readonly_test.php <extend 파일>`). 구 사이트 전체를 띄운 시험은 못 했다 → 배포 뒤 플래그를 1분 켜서 글쓰기·로그인을 눈으로 보고 끄는 것을 권장.
- 배포(🙋 승인 뒤, 구 저장소 규칙: 운영자가 시킬 때만):
  ```
  cd ~/moon/dev/goteacher && git fetch origin && git switch -c g7-readonly origin/master
  git apply ~/moon/marketing/troad/ops/g7/old_site_readonly.patch && git add extend/zz_readonly.extend.php
  git commit -m "feat: G7 읽기 전용 보관 스위치(data/readonly.on)" && git push -u origin g7-readonly   # → PR → master 병합
  ./scripts/deploy-prod.sh        # 출력의 되돌리기 커밋을 기록
  ```
- 구 사이트를 old.goteacher.xyz로: **구 컨테이너·라벨은 바꾸지 않는다.** 새 쪽 `compose.prod.yml`의 `gt-old` 라우터가 구 서비스 `goteacher-xyz@docker`로 잇는다(로컬 Traefik 3.7.5 재현 확인). 그누보드 `G5_DOMAIN`이 비어 있어 주소는 요청 호스트를 따른다(코드 수정 불필요, `sitemap.php`만 goteacher.xyz 고정 — 보관 사이트라 무시).
  old.goteacher.xyz 인증서는 첫 요청 때 letsencrypt(TLS-ALPN)가 새로 받는다(DNS는 이미 이 서버). goteacher.xyz 인증서는 이미 저장소에 있어 새로 받지 않는다.
- 선택(사용자 결정): 구 헤더 '새 고티처 이용해 보기'는 그대로 둬도 t → goteacher.xyz로 넘어가 입장권 로그인이 된다(LEGACY_SSO_KEY 유지). 2027-03 삭제 때 구 컨테이너·`gt-old` 라벨·DB 계정 `gt_sync`(`DROP USER`, 승인 뒤)·덤프를 함께 정리.

## 5. 사전 점검 결과(2026-11-19 14:09~14:30, 읽기 전용)

| 항목 | 결과 |
|---|---|
| 메모리 | 11GiB 중 available 6.6GiB, 스왑 6.6GiB 사용(공용 서버 기존 상태) |
| 디스크 | `/` 여유 58GB(193GB 중 71%). 미디어 백업 893MB×7 |
| 백업 cron | 04:30 매일 성공(11/17·18·19 DB 6.6MB·미디어 893MB·파일 1,552, 7개 보관) |
| 복구 시험 | 새 덤프(4초) → `restore_test.sh`(임시 `goteacher-restoretest`, 44초): **표 58개 행 수 일치, 미디어 1,552/1,552 일치**. 시험 덤프는 이름으로 삭제, 임시 컨테이너 없음 |
| 동기화 | 대조 1.0~1.7초 · 바뀐 행 0 · 실패 0, 최근 24시간 실패·오류 0줄. 구 일정 마지막 작성 11/19 13:47(구 사이트는 계속 쓰이는 중) |
| 계정 차이 | `legacy_gap.py` GAP_TOTAL=0 |
| DNS | goteacher.xyz · www · t · old 모두 168.107.34.70(이 서버) |
| 인증서 | Traefik `letsencrypt` = ACME TLS-ALPN(`tlschallenge`). 저장소에 goteacher.xyz·t.goteacher.xyz 있음, old.goteacher.xyz·www는 없음 |
| 구 라우터 | `goteacher-xyz`(websecure)·`goteacher-xyz-web`(web), 둘 다 `Host(goteacher.xyz)`, 우선순위 지정 없음(=21) |
| 구글 OAuth | 콜백 = `SITE_URL` + `/auth/google/callback` → goteacher.xyz 콜백 추가 필요(1-3). 동의 화면 '테스트 중' |
| ALLOWED_HOSTS | `localhost` + SITE_URL 호스트 → 전환 뒤 앱은 t 호스트를 받지 않는다. 그래서 t는 Traefik에서 308 이동 |
| 서버 저장소 | d0bf509 반영, 추적 밖 파일 `ops/var_env_backup_1793337641`(600, 10/30) — 손대지 않음, 리드 확인 |

로컬 리허설(맥, Traefik 3.7.5 + whoami, 이름 `g7r-*` 만들고 지움): 구 라벨 그대로 + 스테이징 라벨 → 지금 서버와 같음. 겹침 적용 →
goteacher.xyz GET·POST는 새 쪽, 4MB 일반 POST 413, 공지·한글·사진 업로드 경로 통과, http→https, t GET 301·POST 308로 같은 경로·질의 유지,
old.goteacher.xyz → 구 쪽(http는 https로), **운영 라우터 접근 로그 0줄**. 링크 제거 → goteacher.xyz 다시 구 쪽.
업로드 동시 건수: t 요청은 전부 이동되므로 업로드 라우터는 goteacher.xyz 것만 받아 디스크 최대량은 그대로 약 1.2GB.

## 6. 시간과 가장 빠른 날짜

- 최소 소요: 전환 당일 약 **20분**(플래그 1초 → 스크립트 2~3분 → 사람 확인 10분). 새 고티처 끊김은 수 초, 구 사이트는 플래그 시각부터 읽기 전용.
- 미리 할 것(1-1·1-2·1-3)은 합쳐 30분 안, 모두 지금 해도 동작 변화가 없다.
- **기술적으로 가장 빠른 전환: 1-1~1-3이 끝난 다음 날 저녁(예: 11/20 금 18시 이후)**. 막는 항목:
  1. 🙋 구 사이트 쓰기 중지 승인과 시각, 학교 안내 기간(1-5) — 일정을 정하는 유일한 사람 결정
  2. 🙋 구글 OAuth 콜백 추가(1-3) — 안 하면 구글 로그인 전부 실패
  3. 🙋 구 저장소 패치 배포 승인(1-2)
  4. 리드: 준비물 커밋·푸시(1-1)
  5. 리드 결정 D-1: 김해봉황초 플랜·시험 계정 3개를 운영에 남길지(남겨도 전환은 됨)
  6. 리드 결정 D-2: t.goteacher.xyz를 이동(308)으로 둘지 — G7 표의 "스테이징 주소는 남겨 둠"을 "주소는 살아 있되 운영으로 넘김"으로 해석했다. 따로 된 스테이징이 필요하면 별도 compose 프로젝트가 필요(이번 범위 밖)
  7. 사용자 선택: 35번 12/1~5 "이관 학교 2~3곳 구 화면과 나란히 비교"를 건너뛸지(동시 사용으로 이미 실사용 중)
- 전환을 막지 않는 것: 동의 화면 프로덕션 게시(1-4), robots.txt 운영 도메인(G7 리드 결정 항목, gt-web), 공용 Traefik 접근 로그 458MB 처리(사람 결정 7번).
