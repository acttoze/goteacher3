# 고티처(구 티로드 T-Road) 작업 규칙

- 공식 명칭은 **고티처**(2026-10-29 확정). 화면·문서의 새 문구는 고티처로 쓴다. 코드 안 식별자(TR·troad 등)는 그대로 둬도 된다.
- 디자인은 **C안 멀티플레이어 협업**으로 확정(2026-10-30). 확정 화면·디자인 시스템은 `design/`(입구 `design/index.html`), 시안 기록은 `drafts/`. 개발 때 `web/static/`으로 이식하는 기준은 `design/assets/gt.css`.
- 1인 개발 프로토타입이다. **워크트리·PR·별도 브랜치를 쓰지 않는다.** main에서 바로 고치고, 끝나면 main에 커밋하고 푸시한다. (GitHub Pages가 main을 보여 주므로 푸시하면 바로 확인 가능)

## 지금 단계 (2026-10-29~)
- **개발 시작 전.** 새 세션은 먼저 `meeting/29_dev_kickoff.md`를 읽고, 작업 목록 `meeting/28_tasks_r1.md`의 A5부터 진행한다. 범위·일정은 `meeting/27_implementation_plan.md`.
- 비밀 값(구글 OAuth 등)은 저장소 밖 `~/.config/troad/`에만 둔다. 저장소에 커밋하지 않는다.
- OCI 서버(`oci_tailscale`)는 다른 서비스 29개와 공용이다. 티로드 외 컨테이너·Traefik 설정은 바꾸지 않는다.
