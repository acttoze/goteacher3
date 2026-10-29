# 29. 개발 시작 안내 (새 세션용)

- 작성: 2026-10-29 · 이 문서를 먼저 읽고 `28_tasks_r1.md`의 A5부터 시작한다.

## 지금 단계

- 기획·검토·프로토타입·계획은 끝났고, **실제 서비스 코드는 아직 없다.**
- 첫 출시: **R1 동학년판** — 베타 11/30, 정식 **12/14(월)**. 범위와 일정은 `27_implementation_plan.md`, 작업은 `28_tasks_r1.md`(A~G, ★ = 출시를 막는 작업).
- 화면 사양은 `prototype/`. 특히 `grade.html`(동학년 만들기) · `join.html`(초대 입장) · `assets/app.js`의 모으기(`collectNew`·`collectDetail`) · `recv.html`(제출) · `assets/core.js`의 `TR.htmlTableToTsv`(한글 표 붙여넣기).
- 한글 채우기 엔진은 `research/hwp_poc/troad_hwp.py`(python-hwpx 6.10). 근거는 `research/04_hwp_feasibility.md`, 학교 PC 실측은 `research/05_field_check.md`.

## 확정된 것 (다시 묻지 말 것)

| 항목 | 값 |
|---|---|
| 스택 | Python 3.12 + Django 5 + htmx, PostgreSQL 17, Redis + RQ, python-hwpx(서버) + rhwp WASM(브라우저) |
| 서버 | OCI 춘천 `ap-chuncheon-1` 무료 A1(2 OCPU·12GB) — SSH 별칭 `oci_tailscale`(공인 168.107.34.70의 22번은 막힘). 80·443은 **Traefik** 컨테이너가 받음 → 티로드 컨테이너에 `Host(t.goteacher.xyz)` 라벨. 컨테이너 29개 공용이므로 다른 서비스는 건드리지 않는다 |
| 도메인 | **운영은 `goteacher.xyz`**. 구버전이 본 도메인에서 운영 중이라 **개발·베타는 `t.goteacher.xyz`**(와일드카드 → OCI), 출시 때 전환(작업 G7). 코드에 주소를 박지 말고 `SITE_URL` 설정으로만 |
| 로그인 | **구글만**. GCP 프로젝트 `troad-goteacher`(landmandev@gmail.com), 리디렉션 `/auth/google/callback`(운영·localhost:8000) |
| 비밀 | 구글 클라이언트 JSON: `~/.config/troad/google_oauth_client.json`(저장소 밖, 600). 서버에는 `.env`로만. **저장소에 비밀 값 금지**(.gitignore에 `client_secret_*.json`, `.env`) |
| 학생 정보 | 받지 않는다. 교직원 정보 취합까지만(학생 열은 감지·경고) |
| 받는 사람 | 링크 하나, 인증 없음, 대리 제출 허용 |
| 작업 방식 | main에서 바로 작업·커밋·푸시(워크트리·PR 없음). 화면은 PC 전용·큰 글씨(본문 16px·버튼 40px+) |

## 첫 작업 순서

1. **A5** 저장소 구조 — 이 저장소 안에 `app/`(Django 프로젝트) · `hwp/`(POC 패키지화) · `web/static/`(프로토타입 CSS·JS 이식) · `ops/`(docker compose·Traefik 라벨). `prototype/`과 `research/`는 그대로 둔다.
2. **B1** 배포 — docker compose(앱·작업 큐·PostgreSQL·Redis) + Traefik 라벨로 `https://t.goteacher.xyz`에 "hello". 서버 작업 전에는 Traefik 네트워크 이름·인증서 리졸버 이름을 읽어서 확인만 하고, 기존 컨테이너는 바꾸지 않는다.
3. **A6** 개인정보 처리방침·이용약관 초안 → B4(구글 로그인)·B3(공간 격리)로 이어간다.

## 남은 확인

- 구글 동의 화면은 **'테스트 중'으로 유지**(테스트 사용자로 개발·베타). 프로덕션 게시는 출시 때 도메인 전환(G7)과 함께, 처리방침·약관을 `goteacher.xyz`에 올린 뒤. 처리방침·약관 초안은 `prototype/privacy.html`·`terms.html`(노란 칸 채우기 필요). 그전에는 테스트 사용자로 로그인
- 학교판(R2) 전: OCI의 CSAP 보유 여부, 전용 인스턴스 분리
