# 티로드(T-Road) — 고티처 대항마 기획 + 프로토타입

## 보기
- 기획서: http://localhost:4780/ (Tailscale: http://100.97.163.119:4780/)
- 프로토타입: http://localhost:4780/app.html#today
- 서버 재시작: `cd ~/moon/marketing/troad/prototype && nohup python3 -m http.server 4780 --bind 0.0.0.0 >../server.log 2>&1 &`
- 시연 데이터 초기화: 앱 상단 오른쪽 ↺ 버튼

## 폴더
- `research/` 근거 자료 — 운영 DB 실측(01), 마케팅·사이트·경쟁(02), 고티처 코드 인벤토리(03), 한글(HWP) 취합 기술 타당성 실측(04) + POC(`hwp_poc/`)
- `meeting/` 회의 — 브리프(00), 1라운드 독립 제안(r1_*), 의장 종합안(10), 2라운드 비판(r2_*), **최종 기획서(20_final_plan.md, v1.3)**, 1차 피드백 반영 기록(21_feedback_r1.md), 2차 피드백 검토·반영(22_feedback_r2.md), 3차 피드백 검토·반영(24_feedback_r3.md), **4차 피드백 검토·반영(25_feedback_r4.md)**, 3라운드 현장 현실성 검토(r3_*), **검토 종합(26_reality_review.md)**
- `archive/` v1.0·v1.1·v1.2 기획서·프로토타입 백업
- `prototype/` 정적 HTML 프로토타입 (빌드 없음, 바닐라 JS)
  - `index.html` 기획서 페이지 · `app.html` 앱 · `recv.html` 받는 사람 비로그인 PC 화면(쪽지 링크) · `tv.html?s=S10-9091016` 급식소 현황판 TV(김해봉황초 실제 NEIS 급식 연동, 8개 화면 자동 전환) · `meal-admin.html` 영양사 입력(매일: 당일 식판 사진 / 선택: 화면 순서·화면별 노출 시간(기본 7초)·켜고 끄기·위생 주제 고정). 영양 기준은 학교급식법 시행규칙 별표3 초등 1~6학년 남녀 평균 표준값 내장. 연동 모듈 `assets/meal.js`
  - 화면은 **교내**(좌상단 학교 이름, 청록 · 교직원만)와 **교외**(좌상단 '티로드', 남색 · 외부 공개: 양식 자료실·협력업체·정보 수리요청, 주소 `#out/…`)로 나뉨 — `meeting/23_inside_outside.md`
  - `assets/data.js` 가상 '티로드초' 샘플 데이터 · `vendors.js` 협력업체 가상 데이터(블록·양식 프리셋·수리요청 포함) · `forms.js` 양식 자료실 79건(고티처 아카이브 변환) · `core.js` 상태·한 줄 입력 파서·시간 정밀도 보조 · `app.js` 화면(오늘·일정·모으기·게시판·학교)

## 회의 구성
의장 Claude + 서브에이전트 4명(일반 교사 / 생산성 전문가 / 학교업무 전문 교사 / UI/UX 전문가), 2라운드.
페르소나는 `.claude/agents/`에 저장되어 다시 부를 수 있다: `troad-teacher`(일반 교사) · `troad-productivity`(생산성 전문가) · `troad-admin`(학교업무 전문 교사) · `troad-uiux`(UI/UX 전문가). 예: "troad-teacher 에이전트로 급식 현황판 검토해 줘".

## 운영 DB 접근
읽기 전용 SELECT 집계만 수행했고(oci_tailscale → mysql 컨테이너 `goteacher`), 개인 식별 정보는 산출물에 넣지 않았다.
