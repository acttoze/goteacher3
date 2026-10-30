# 고티처(구 티로드 T-Road) 작업 규칙

- 공식 명칭은 **고티처**(2026-10-29 확정). 화면·문서의 새 문구는 고티처로 쓴다. 코드 안 식별자(TR·troad 등)는 그대로 둬도 된다.
- 디자인은 **C안 멀티플레이어 협업**으로 확정(2026-10-30). 확정 화면·디자인 시스템은 `design/`(입구 `design/index.html`), 시안 기록은 `drafts/`. 개발 때 `web/static/`으로 이식하는 기준은 `design/assets/gt.css`.
- 1인 개발 프로토타입이다. **워크트리·PR·별도 브랜치를 쓰지 않는다.** main에서 바로 고치고, 끝나면 main에 커밋하고 푸시한다. (GitHub Pages가 main을 보여 주므로 푸시하면 바로 확인 가능)

## 지금 단계 (2026-10-29~)
- **개발 시작 전.** 새 세션은 먼저 `meeting/29_dev_kickoff.md`를 읽고, 작업 목록 `meeting/28_tasks_r1.md`의 A5부터 진행한다. 범위·일정은 `meeting/27_implementation_plan.md`.
- 비밀 값(구글 OAuth 등)은 저장소 밖 `~/.config/troad/`에만 둔다. 저장소에 커밋하지 않는다.
- OCI 서버(`oci_tailscale`)는 다른 서비스 29개와 공용이다. 티로드 외 컨테이너·Traefik 설정은 바꾸지 않는다.

## 개발 팀 (`.claude/agents/gt-*`)
메인 세션이 **리드**다. 작업을 나눠 맡기고, 결과를 합치고, 커밋·푸시는 리드만 한다(에이전트는 커밋하지 않는다).

| 에이전트 | 맡는 곳 | 작업 ID |
|---|---|---|
| `gt-backend` | `app/` 파이썬(모델·격리·로그인·NEIS·모으기·RQ) | B3–B7, C1–C7, D1–D6, G1–G2 |
| `gt-frontend` | 템플릿·`web/static/`, `design/` 이식, 브라우저 동작 | B6·B7·C2·C5·C6·D4 화면, G1, G5 |
| `gt-hwp` | `hwp/` 패키지, rhwp 미리보기·표 고르기, 한글 회귀 | E1–E6, F1–F4 |
| `gt-ops` | `ops/`, 저장소 뼈대, 배포·CI·백업·도메인 전환 | A5, B1, B2, G6, G7 |
| `gt-qa` | 테스트만 씀, "끝났다는 기준" 판정 | 모든 작업의 마무리 |
| `gt-security` | 읽기 전용 보안·개인정보 검토 | ★ 작업 커밋 전, G3 |

- 진행 순서: 리드가 `28_tasks_r1.md`에서 작업 ID 선택 → 구현 담당(폴더가 겹치지 않으면 병렬) → `gt-qa` 판정 → ★ 작업이면 `gt-security` → 리드가 커밋·푸시.
- 기획·문구·현장성 검토는 기존 페르소나(`troad-teacher`·`troad-admin`·`troad-productivity`·`troad-uiux`)를 쓴다. 이들은 코드를 고치지 않는다.
- 되돌리기 어려운 서버 작업(운영 마이그레이션·도메인 전환·백업 삭제)은 `gt-ops`가 계획을 먼저 보고하고 사용자 승인 뒤 실행한다.
