# 36. 기관 공간 설계안 (T1~T3)

- 작성: 2026-11-10 · gt-web(설계만, 코드 수정 없음). 작업은 `32_tasks_r2.md` "## T. 기관 공간"
- 사용자 결정(11/10): B안 기관용 공간. ① 관리자가 그룹(=채널)을 만들고 **사람 배정까지 관리자가** 한다(가입자는 '미배정'). ② 기관에는 **급식·'학교' 메뉴·복무 채널이 없다.** 일정 학교 탭은 그룹 채널 줄. 관리자 화면의 핵심은 "채널 설정 + 배정". ③ **시작 화면에서 학교인지 기관인지 고른다**(기관은 NEIS 검색 대신 이름 직접 입력 + 시도교육청 선택).
- 가정: 기관도 학교 공간처럼 계정 회원만 입장하고, 관리자(`is_manager`)·플랜 기한(`writable`)·제출 링크 규칙을 그대로 쓴다.

## 1. 데이터 모델

| 무엇 | 설계 | 까닭 |
|---|---|---|
| 공간 종류 | `Space.Kind.ORG="org"`, grade=0. 검사 제약을 `Q(kind__in=[school, org], grade=0) \| Q(kind=grade, grade≥1)`로 바꾼다 | 학교 공간과 같은 자리(grade=0, School당 하나) |
| `is_school`의 뜻 | **"학교판 공간"(school 또는 org)으로 넓힌다.** 새 `is_org` 하나를 더한다 | `is_school` 분기 62곳 중 대부분(계정 회원만·관리자 권한·받는 링크 명단 숨김·`writable_by`·첨부 한도·플랜 게이트)이 기관에도 맞다. 기관에서 달라야 하는 곳은 적어서 거기에만 `is_org`를 건다(아래 표) |
| 기관의 School 행 | `neis_atpt`=고른 시도교육청 코드, **`neis_code=""`**, name=직접 입력. 유일 제약은 `condition=~Q(neis_code="")`로 바꾼다. `School.is_org = neis_code == ""` | 공간이 생기기 전(플랜 신청·푸시 묶음)에도 기관인지 알 수 있는 표시가 하나만 있으면 된다 |
| NEIS를 부르지 않기 | `spaces/neis._get`에서 `SD_SCHUL_CODE`가 비면 바로 빈 결과(실패 아님)를 돌려준다. 그리고 화면에서 NEIS 줄·급식 카드를 `is_org`로 숨긴다 | 호출부 8곳(`today/schedule`·`today/views` 급식·학사일정·`print_view`·`collect/push.day_off`·`meals/*`·`class_names`)을 하나하나 막지 않아도 된다. "NEIS 코드 없으면 요청 0번"을 테스트로 고정 |
| 기관 그룹 | **새 모델 대신 `Space.org_groups` JSON 목록** `[{"k": "t1a2b3c4", "n": "학교혁신팀"}, …]`. 순서가 곧 줄 순서다. 키는 `"t"+token_hex(4)`(9자, 고정 키 `g1…`·`all`·`me`와 겹치지 않음). 많아야 30개 | 학교의 `group_order`(Q2)와 같은 방식이다. 이름 바꾸기·순서·지우기를 행 하나 잠금(`select_for_update`)으로 처리하고, 조인이 필요 없다 |
| 회원 소속 | `Member.group`에 그 키를 넣는다(미배정=`""`). `cls`=그룹 이름(미배정이면 "미배정")이고, 이름을 바꾸면 그 그룹 회원의 `cls`를 함께 고친다. `position`은 직위 글자 | 학교의 학년 밖 소속과 같은 규칙(cls=소속 이름)이라 `cls_text`·이름 겹침 검사가 그대로 돈다 |
| 고정 choices | `Member.group`·`Notice.group`의 `choices=`를 뺀다(DB 변화 없는 상태 마이그레이션). `Member.Group` 클래스는 학교용으로 남긴다. 이름은 `space.group_label(k)`, 목록은 `space.group_choices`(org면 `org_groups`, school이면 지금처럼) 하나로 읽는다 | 고정 choices로 이름을 찾는 곳이 기관 키를 만나면 `ValueError`(`Event.scope_label`)가 나거나 키가 그대로 보인다(`get_group_display`) |
| 일정이 줄을 가리키는 법 | **`Event.scope`에 그룹 키를 넣는다**(새 FK 없음). `me`는 그대로 | scope는 이미 자유 글자(10자)이고, 공지 audience·취합 Row.group도 같은 키를 쓴다 |
| 전원이 봄 | `my_range_q(me)`: org면 `~Q(scope=ME) \| Q(by=me)`, `reachable_by`도 같게 바꾼다. 학교 분기는 그대로 | 기관의 그룹 줄은 공간 전원이 보고, 남의 '나' 일정만 막는다. 관리자 판정(`reachable_q`)과 결과가 같아진다 |
| 보이는 줄(사람마다) | `Member.lines_off` CharField(320, 끈 키를 쉼표로 이어서, 기본 빈 값 = 모두 켬) | Q1 `days_on`과 같은 자리다. **끈 줄**을 저장하므로 새 그룹이 생기면 처음부터 보인다. **보기 필터일 뿐 권한이 아니다**(주소로 열면 보인다) |

`is_school`을 넓힐 때 기관에서 **달라야 하는 곳**(`is_org` 분기 또는 새 게이트 `no_org` → 404): 학교 화면 `spaces/school.py`의 업무분장·요약·복무 입력(`attendance`), `meals/*`·TV, 정보수리방 `repairs`(5절 결정 3), 일정 복무 줄·`duty_add/delete`, `Space.label`("학교" → 기관 이름), 아바타 글자("학" → "기"), 초대 문구(`_invite`의 "학교 공간"), `_join_school`의 소속 고르기, `class_choices`(학년 반 없음). 나머지 분기는 그대로 둔다.

**Member.group 고정 값을 쓰는 곳(고칠 범위)**

| 파일 | 지금 | 기관에서 |
|---|---|---|
| `spaces/models.py` `group_choices`·`cls_group`·`in_grade` | `Member.Group` | org면 `org_groups`. `cls_group`은 학년 반 전용이라 그대로 둔다(기관은 빈 값) |
| `today/models.py` `scope_label`·`scope_choices`, `today/views.py` `_scope_ok`·`_cell_scopes`·`_read_scope`, `today/schedule.py` `_dialog_scopes`·`tabs` | 고정 값 + 내 소속 | org: 쓸 수 있는 줄 = 모든 그룹 키 + `me`. 기본값은 내 그룹(미배정이면 첫 줄) |
| `notices/models.py`(labels)·`notices/views.py:130,228` | `Member.Group.choices` | `space.group_choices` |
| `collect/recipients.py` `GROUPS`·`GRADES` | 고정 dict | 이름은 `space.group_label`. `GRADES`(학년 반 모드)는 기관에서 쓰지 않는다 |
| `spaces/school.py` `arrange`·`_move_member`·`_group_cards`·`edit_me` | `in Member.Group.values`, 학년이면 반 고르기 | org: `in space.group_keys`, 반 없이 cls=그룹 이름 |
| `spaces/views.py` `read_place`·`place_values` | 고정 소속 + 직책 목록 | 기관 입장은 이름(+직위)만, 소속은 쓰지 않는다 |
| `spaces/attendance.py:381`·`spaces/summary.py`·`repairs/views.py:82,145` | 고정 값 | 기관에서는 화면이 없어 손대지 않는다 |
| `collect/bell.py`·`lists.py`·`submit.py`·`views.py:478-483`, `notices/seen.py` | 키를 서로 견주기만 함 | 그대로 쓴다(이름은 `group_choices`를 거친다) |

## 2. 화면별 변화

| 화면 | 학교 공간(그대로) | 기관 공간 |
|---|---|---|
| 메뉴 `templates/shell.html` | 오늘·취합·일정·게시판·학교·급식 / 교외 | 오늘·취합·일정·게시판·**채널**(학교 메뉴 자리) / 양식 자료실·협력업체·커뮤니티. 급식·학교·정보수리방·"선생님 n명"은 없다 |
| 오늘 | NEIS 급식·학사일정, 복무 | 급식·학사일정 카드 없음. 일정 목록은 '전원이 봄' 규칙에 끈 줄을 빼고 보인다 |
| 일정 학교 탭(`schedule.py _channels`) | 학교(all+NEIS)·취합·복무 | **그룹 줄(관리자 순서, 끈 줄 뺌) + 취합 줄**. 탭은 "기관 이름"·"개인" 둘이다(그룹 탭 없음, 그룹 줄이 이미 다 보여서). NEIS 없음, `neis_empty`·`neis_fail` 안내 없음. 주·월·일 모두 가로 줄 = 그룹 |
| 취합 줄(추천: 남긴다) | 전 교직원 취합 | 받는 사람이 기관 전체·그룹 고르기·그룹별 1칸인 취합을 모두 이 줄에 둔다. 사람 고르기는 아래 마감 목록에만 둔다(학교와 같다) |
| 일정 쓰기 대화상자·칸 ＋ | 볼 사람: 학교 전체·내 그룹·나 | **줄 고르기**: 모든 그룹 + 개인(나만). 칸 ＋는 그 줄의 키로 저장. 한 줄 입력은 내 그룹(미배정이면 첫 줄). 누구나 아무 줄에나 쓴다(구 채널도 구독자가 썼다). 고치기·지우기는 쓴 사람과 관리자(지금 `writable_by`) |
| 인쇄·주간계획 붙여넣기 | NEIS 포함 | NEIS 없이 그대로. 붙여넣기는 내 그룹 줄로 넣는다 |
| 설정 > 설정 탭 | 표시 요일 | 표시 요일 + **"보이는 그룹 줄"**(그룹마다 토글, 최소 1개 켬, 표시 요일처럼 htmx 저장) |
| 채널(관리자 화면, `school.py` 재사용) | 학년분장·업무분장 | 탭 하나: 맨 앞 **미배정 카드** + 그룹 카드. 관리자는 ① 그룹 만들기(이름 입력) ② 이름 바꾸기(카드 머리) ③ 지우기 ④ 카드 끌기로 순서(Q2 `what=groups`) ⑤ 사람 끌기·단추로 배정(Q2 `what=members`, 반 고르기 없음, MemberLog·🔔 그대로). 초대 링크·관리자 지정·임시 비밀번호 카드도 이 화면에. 관리자가 아니면 보기만 하고 내 직위만 고친다 |
| 가입(초대 링크 `/join`) | 계정 회원 + 소속·직책·반 | 계정 회원 + **이름(+직위 선택)**만. group="", cls="미배정". 관리자 🔔은 그대로 |
| 시작 화면(`home`→`start_school.html`) | 학교 찾기(NEIS) → 동학년 만들기 | 맨 위 고르기 **[학교] [기관(교육청·지원청 부서 등)]**. 기관을 고르면 시도교육청 선택 + 기관 이름 + 담당자 이름·직위·메일 → **기관 플랜 신청**(PlanRequest, 견적서)으로 간다. 동학년 같은 무료판이 없어서다(5절 결정 1) |
| 학교 플랜 신청 `/school-plan`(O1) | NEIS 검색 → 신청 | 같은 고르기를 둔다. 기관은 검색 대신 이름·시도 직접 입력으로 `School(neis_code="")`를 만들고 신청한다. 같은 사람이 같은 이름으로 대기 신청을 보내면 하나로 고친다 |
| 공지(게시판) 받는 곳 | 전체·소속 고르기 | 전체·**그룹 고르기**(키 목록, 미배정은 '전체'에만 든다) |
| 취합 받는 사람 | 전 교직원·소속·소속별 1칸·사람·부서 | 기관 전체·그룹·그룹별 1칸·사람(부서 없음). 그룹순 = 채널 순서 |

## 3. 운영자 명령과 이관(T3)

- **`org_plan`(새 명령, `school_plan` 옆)**: `org_plan requests`(기관 대기 신청), `org_plan --request=<신청 ID> --on --until YYYY-MM-DD [--identity-checked]` → School(이미 있음)에 `Space(kind=org, grade=0, name=기관 이름)`을 만들고, 신청자를 관리자로(`merge._seat_applicant` 재사용), 첫 그룹 하나("공통")를 둔다. `org_plan <School pk> --on/--off/--remove-manager`는 `school_plan`과 같은 규칙이다. School pk는 운영자 셸에서만 쓰는 값이라 외부 주소에 나오지 않는다. 기관은 회원 대조가 불가능하므로 **`--identity-checked`를 필수**로 한다(공문·전화 확인).
- `school_plan`은 `neis_code=""` 학교를 거부한다(기관을 학교로 켜는 실수 방지). `open_school`의 동학년 합치기는 기관에 쓰지 않는다.
- **T3 이관 매핑(`legacy/importer.py`, `import_legacy --org 9000003`)**

| 구 | 새 | 규칙 |
|---|---|---|
| 학교 9000003 | `School(neis_atpt=code_sido, neis_code="", name)` + `Space(kind=org)` | `LegacyMap("school")`로 다시 돌림을 판단한다(neis_code 조회 없음). 이미 공간이 있으면 멈춘다 |
| 그 학교의 사용자 채널(`g5_write_channel`, 7개) | `org_groups` 한 줄씩(채널 이름, `wr_id` 순) + 맨 앞 "공통" | `LegacyMap("org_group", 채널 wr_id → 키)` |
| wr_4≥5 일정 | scope=그 채널 키, **`[채널명]` 접두 없음** | 중복 묶음은 scope가 같을 때만 합친다(다른 채널 복사본은 각 줄에 남김) |
| wr_4 0·1 | "공통" 줄 | T 절 그대로 |
| wr_4 3·wr_8 | `me` | 지금 규칙 |
| wr_4 4(내 그룹) | `me`(작성자) | 옛 그룹이 기본 13개(학년 등)라 대응하는 줄이 없다. 넓히지 않고 좁힌다. 건수는 보고서에 남긴다 |
| wr_4 2(복무) | 넣지 않음(보고서 "기관은 복무 없음") | 사용자 결정 ② |
| 회원 | group="", cls="미배정", position=`mb_4` | 관리자가 채널 화면에서 배정한다 |
| 게시판·첨부·댓글 | 지금 규칙 그대로 | 공지 group=""(미배정) |

- 학교들의 자체 채널(느리울초 등)은 5절 결정 3 전까지 지금 D5(학교 전체 + `[채널명]`)를 유지한다.

## 4. 단계와 확인

| 단계 | 바뀌는 파일(예상) | 마이그레이션 |
|---|---|---|
| T1 기관 공간 | `spaces/models.py`(Kind·is_org·label·School.is_org·제약), `spaces/neis.py`(`_get` 한 줄), `spaces/gates.py`(`no_org`), `spaces/views.py`(home 고르기·`_join_school` 기관 분기·`_invite` 문구), `spaces/plan.py`(기관 신청), `spaces/management/commands/org_plan.py`(새), `school_plan.py`(거부), `templates/shell.html`, `today/views.py`·`schedule.py`(NEIS·복무 줄 숨김), `meals`·`repairs`·`school.py` 게이트, `start_school.html`·`plan.html`·`join_school.html` | `spaces` 1개: Space 제약 교체, School 유일 제약 조건부 |
| T2 그룹 = 줄 | `spaces/models.py`(org_groups·lines_off·group_choices·group_label·choices 제거), `spaces/school.py`(채널 탭·그룹 CRUD·arrange 분기), `today/models.py`·`views.py`·`schedule.py`·`more.py`, `accounts` 설정 탭, `notices/models.py`·`views.py`, `collect/recipients.py`, `_week.html`·`_school.html`·설정 템플릿, `web/static/js`(Q2 끌기 재사용, 새 JS 최소) | `spaces` 1개(필드 2 + choices 상태), `notices` 1개(choices 상태) |
| T3 이관 | `legacy/importer.py`·`rules.py`·관리 명령 인자, `legacy/tests.py` | 없음 |

- **학교 공간 회귀 위험**: ① `is_school`을 넓혀 학교에만 맞는 분기가 기관에 새는 것 → `no_org` 게이트 대상 주소마다 "기관이면 404" 테스트를 둔다. ② choices를 빼면 `get_group_display`가 사라진다 → 모든 사용처를 `group_label`로 바꾸고 학교 표시 스냅숏 테스트로 확인한다. ③ `my_range_q`의 org 분기가 학교에 걸리면 소속 일정이 전원에게 보인다(개인정보) → 학교 범위 테스트(`today` 기존 + 새 "학교 g4 일정은 g5에게 안 보임")를 유지한다. ④ 조건부 유일 제약을 바꾸는 마이그레이션 → 운영 DB에 `neis_code=""` 행이 없는지 먼저 확인한다.
- **확인**: 기관 공간의 오늘·일정·인쇄·푸시 묶음에서 NEIS 요청 0번(urlopen 목) / 기관 그룹 줄 일정을 미배정 회원도 봄, 남의 '나'는 관리자도 안 봄 / 끈 줄은 그 사람 판에서만 빠짐 / 다른 기관·학교 공간의 그룹 키로 쓰면 400·404 / 그룹 지우기·이름 바꾸기 뒤 회원 cls·일정 처리(5절 결정 2) / 시작 화면 기관 고르기 → 신청 → `org_plan --on` → 관리자 입장 → 초대 → 가입자 미배정 → 배정 / 학교 공간 전체 테스트 통과 / 1536×750 기관 일정(줄 8개) 첫 화면 / T3은 35번 5절 + 9000003 채널별 건수 대조, 스테이징 재적재.

## 5. 남은 결정 (사용자)

1. **기관 공간을 언제 열까** — 추천: **신청 → 견적서 → 운영자가 켬**(학교 플랜과 같다). 시작 화면에서 "기관"을 고르면 바로 신청서로 간다. 바로 열어 체험 기간을 주는 방식은 새 가격 정책이 되므로 사용자가 정할 일이다.
2. **그룹을 지우면 그 줄의 일정은** — 추천: 일정이 있으면 **옮길 줄을 골라야 지울 수 있게** 한다(옮긴 일정 제목은 그대로, 바뀐 것 목록에 남음). 회원은 미배정이 된다. 마지막 그룹은 못 지운다.
3. **학교 자체 채널(5곳)에도 이 줄 기능을 켤까**, 그리고 기관의 **정보수리방**을 숨길까 — 추천: 학교는 T 이후 별도 작업으로 미루고 지금은 `[채널명]`을 유지한다(학교 탭에 학교·취합·복무 줄 뒤에 "추가 줄"을 붙이는 방식이 가장 작다). 정보수리방은 학교 시설 전제라 기관에서 숨기고, 게시판·협력업체·양식 자료실·커뮤니티는 남긴다.

## 6. 사용자 결정 (2026-11-10 확정)

1. **기관 공간은 바로 연다 + 체험 기간.** 시작 화면에서 '기관'을 고르고 시도교육청·기관 이름·담당자 정보를 넣으면 곧바로 기관 공간이 열리고 고른 사람이 첫 관리자가 된다. 체험 기간은 설정값 `ORG_TRIAL_DAYS`(**임시 기본 30일 — 리드가 넣은 값, 사용자 확정 전**)로 `paid_until`을 채운다. 끝나면 학교 플랜 만료와 같은 읽기 전용, 그 뒤 신청 → 견적서 → 운영자가 켬(`org_plan`). 계정당 체험 기관 하나(같은 사람이 여러 번 체험 금지). 운영자 명령 `org_plan`은 연장·끄기·관리자 해제용으로 남긴다.
2. **그룹 지우기**: 일정이 있으면 옮길 줄을 골라야 지울 수 있다(추천안 그대로). 회원은 미배정, 마지막 그룹은 못 지움.
3. **학교 자체 채널**: 기관(T) 다음에 학교 탭에도 '추가 채널 줄'을 붙이는 작업(T4)을 한다. 그때까지 이관은 학교 전체 + `[채널명]`, T4 뒤 줄로 옮긴다. 기관의 정보수리방은 숨긴다.
