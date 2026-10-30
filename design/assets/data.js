// 고티처 디자인 샘플 데이터 — 모든 화면이 같은 하루(2026-10-26 월, 11:05)를 보여 주도록 한 곳에 둔다.
// 모두 가상의 학교·인물이다. 실제 서비스에서는 NEIS·모으기 데이터로 바뀐다.
// 합계·개수는 여기서 손으로 적지 않고 아래 계산부에서 행으로부터 만든다.
window.GT = {
  product: '고티처',
  school: '새솔초등학교',
  schoolShort: '새솔초',
  office: '경기도교육청',
  me: { name: '정하은', cls: '4-2', role: '4학년 2반 담임', grade: 4, initial: '정', google: 'haeun.jung@gmail.com' },
  date: { iso: '2026-10-26', label: '10월 26일 월요일', short: '10/26(월)', week: '2학기 9주차' },
  now: '11:05',
  nowSlot: 'b2',
  nextBundle: { at: '12:20', label: '2블록 끝' },

  // 동학년 공간
  space: {
    name: '새솔초 4학년', kind: '동학년', grade: 4, classes: 5,
    by: '송민재', made: '2026-09-01', invite: 'goteacher.xyz/j/4Q2K'
  },
  // 동학년 선생님(만든 순서). via: 어떻게 들어왔나
  members: [
    { name: '송민재', cls: '4-1', role: '학년 대표', via: '구글 가입 · 만든 사람', at: '9/1' },
    { name: '정하은', cls: '4-2', role: '', via: '구글 가입', at: '9/1' },
    { name: '김도윤', cls: '4-3', role: '', via: '초대 링크 · 이름만', at: '9/2' },
    { name: '이수빈', cls: '4-4', role: '', via: '구글 가입', at: '9/2' },
    { name: '박서준', cls: '4-5', role: '', via: '초대 링크 · 이름만', at: '9/7' }
  ],
  // 아직 안 들어온 칸
  waiting: [{ cls: '4-전담', label: '전담', hint: '체육 노태윤 선생님' }],

  slots: [
    { k: 'b1', label: '1블록', from: '08:40', to: '10:30' },
    { k: 'b2', label: '2블록', from: '10:30', to: '12:20' },
    { k: 'b3', label: '3블록', from: '12:20', to: '14:40' },
    { k: 'pm', label: '방과후', from: '14:40', to: '' }
  ],

  meal: ['잡곡밥', '쇠고기미역국', '돈육장조림', '시금치나물', '깍두기', '감귤'],
  mealKcal: 642,
  neis: '독서주간 1일차 (10/26~10/30)',

  timetable: [
    { k: 'b1', sub: '국어 · 수학', room: '4-2' },
    { k: 'b2', sub: '과학 수업공개 · 사회', room: '4-2', note: '참관: 교감 · 4학년부' },
    { k: 'b3', sub: '체육 · 창체', room: '체육관 → 4-2', note: '체육은 노태윤 선생님(전담)' }
  ],

  // 오늘 일정(동학년·학교). s 없으면 시간 미정
  events: [
    { id: 'e1', s: 'b1', title: '독서주간 아침 10분 독서', place: '각 교실', who: '전교' },
    { id: 'e2', s: 'b2', title: '4-2 수업공개', place: '4-2 교실', who: '참관: 교감 · 4학년부', mine: true },
    { id: 'e3', s: 'b3', title: '3학년 수영교육 출발 13:10 정문', place: '정문 버스', who: '3학년', changed: { what: '출발', from: '13:00', to: '13:10' } },
    { id: 'e4', s: 'pm', title: '학년부장 협의회 15:30', place: '교무실', who: '학년부장' },
    { id: 'e5', title: '4학년 학년협의 — 학예회 순서 정하기', place: '4-1 교실', who: '4학년부', changed: { what: '장소', from: '4-3 교실', to: '4-1 교실' } },
    { id: 'e6', title: '학예회 소품 준비물 확인', place: '', who: '4학년', mine: true }
  ],

  // 이번 주 동학년 일정(일정 화면). d = 요일 인덱스 0(월)~4(금)
  week: {
    label: '10월 26일 ~ 30일',
    days: ['10/26 월', '10/27 화', '10/28 수', '10/29 목', '10/30 금'],
    today: 0,
    items: [
      { d: 0, s: 'b1', title: '아침 10분 독서', place: '각 교실', who: '전교', src: 'neis' },
      { d: 0, s: 'b2', title: '4-2 수업공개', place: '4-2 교실', who: '정하은', mine: true },
      { d: 0, s: 'b3', title: '3학년 수영교육 출발', place: '정문 버스', who: '3학년', changed: '13:00 → 13:10' },
      { d: 0, s: 'pm', title: '학년부장 협의회', place: '교무실', who: '송민재' },
      { d: 0, s: null, title: '4학년 학년협의 — 학예회 순서', place: '4-1 교실', who: '4학년부', changed: '4-3 교실 → 4-1 교실' },
      { d: 1, s: 'b1', title: '아침 10분 독서', place: '각 교실', who: '전교', src: 'neis' },
      { d: 1, s: 'b2', title: '과학실 안전교육', place: '과학실', who: '4학년', by: '김도윤' },
      { d: 1, s: 'b3', title: '학예회 반별 참가 인원 · 12:00', place: '', who: '정하은', due: true },
      { d: 2, s: 'b1', title: '아침 10분 독서', place: '각 교실', who: '전교', src: 'neis' },
      { d: 2, s: 'pm', title: '학예회 의상 대여 수량', place: '', who: '송민재', due: true },
      { d: 3, s: 'b1', title: '아침 10분 독서', place: '각 교실', who: '전교', src: 'neis' },
      { d: 3, s: 'b3', title: '학예회 리허설', place: '체육관', who: '4학년', changed: '강당 · 2블록 → 체육관 · 3블록' },
      { d: 4, s: 'b1', title: '독서주간 마무리 — 독서 골든벨', place: '강당', who: '전교', src: 'neis' },
      { d: 4, s: 'b2', title: '교육과정 평가 설문', place: '', who: '교무부', due: true },
      { d: 4, s: 'pm', title: '4학년 학년협의', place: '4-1 교실', who: '4학년부' }
    ]
  },

  // 어제 퇴근 이후 바뀐 것
  changes: [
    { title: '4학년 학년협의', what: '장소', from: '4-3 교실', to: '4-1 교실', by: '송민재', when: '어제 17:42' },
    { title: '3학년 수영교육', what: '출발', from: '13:00', to: '13:10', by: '김도윤', when: '어제 18:05' },
    { title: '학예회 리허설(10/29)', what: '장소 · 시간', from: '강당 · 2블록', to: '체육관 · 3블록', by: '송민재', when: '어제 19:10' }
  ],

  // 나에게 온 것 — 내가 낼 것(마감순)
  todo: [
    { id: 't1', title: '방과후 수요조사 (반별 인원)', from: '석가영 · 방과후돌봄부', due: '오늘 14:00', dueIso: '2026-10-26', urgent: true, kind: '제출', form: '한글 양식', fields: 3 },
    { id: 't3', title: '학예회 의상 대여 수량', from: '송민재 · 4학년 대표', due: '10/28(수)', dueIso: '2026-10-28', kind: '제출', form: '한글 양식', fields: 2 },
    { id: 't2', title: '2학기 교육과정 평가 설문', from: '오지현 · 교무부', due: '10/30(금)', dueIso: '2026-10-30', kind: '제출', form: '웹 표', fields: 6 },
    { id: 't4', title: '개인정보 보호 연수 이수 확인', from: '차민호 · 정보부', due: '11/6(금)', dueIso: '2026-11-06', kind: '확인', form: '', fields: 1 }
  ],

  // 모으기(내가 보낸 것 + 지난 것). rows의 v는 fields 순서. 비어 있으면 null
  collections: [
    {
      id: 'c1', title: '학예회 반별 참가 인원', status: 'open', source: 'hwp',
      file: '2026_학예회_참가인원.hwpx', table: '표 2 (표 2개 중)', made: '10/23(금) 16:32', due: '10/27(화) 12:00', dueLeft: '내일 12:00까지 · 24시간 55분 남음',
      reminder: '10/27(화) 08:20 아침 묶음으로 자동 리마인드',
      fields: [{ n: '참가(명)', t: 'number' }, { n: '불참(명)', t: 'number' }, { n: '비고', t: 'text' }],
      rows: [
        { cls: '4-1', who: '송민재', v: [24, 1, ''], at: '10/24 08:55' },
        { cls: '4-2', who: '정하은', v: [23, 2, '전학 예정 1명 포함'], at: '10/23 16:40' },
        { cls: '4-3', who: '김도윤', v: [25, 0, ''], at: '10/26 09:12' },
        { cls: '4-4', who: '이수빈', v: [null, null, null], at: null, live: [22, 1, ''] },
        { cls: '4-5', who: '박서준', v: [null, null, null], at: null }
      ],
      link: 'goteacher.xyz/c/7HJ3', footer: true
    },
    {
      id: 'c2', title: '현장체험학습 버스 좌석 수요', status: 'done', source: 'paste',
      file: '', made: '10/19(월) 09:10', due: '10/23(금) 15:00', closedAt: '10/22 14:03',
      fields: [{ n: '학생(명)', t: 'number' }, { n: '인솔 교사(명)', t: 'number' }, { n: '보조 인력(명)', t: 'number' }],
      rows: [
        { cls: '4-1', who: '송민재', v: [25, 1, 0], at: '10/19 10:02' },
        { cls: '4-2', who: '정하은', v: [25, 1, 1], at: '10/19 09:15' },
        { cls: '4-3', who: '김도윤', v: [25, 1, 0], at: '10/20 08:47' },
        { cls: '4-4', who: '이수빈', v: [23, 1, 1], at: '10/21 15:30' },
        { cls: '4-5', who: '박서준', v: [24, 1, 0], at: '10/22 14:03', by: '이수빈' }
      ],
      link: 'goteacher.xyz/c/2KD9', footer: true
    },
    {
      id: 'c0', title: '2학기 학습준비물 신청', status: 'past', source: 'preset',
      file: '', made: '9/7(월) 13:20', due: '9/18(금) 16:00', closedAt: '9/18 16:00',
      fields: [{ n: '물품 수', t: 'number' }, { n: '신청액(원)', t: 'number' }],
      rows: [
        { cls: '4-1', who: '송민재', v: [7, 96400], at: '9/14 11:20' },
        { cls: '4-2', who: '정하은', v: [5, 88000], at: '9/9 16:05' },
        { cls: '4-3', who: '김도윤', v: [6, 99500], at: '9/17 09:40' },
        { cls: '4-4', who: '이수빈', v: [4, 71200], at: '9/15 14:10' },
        { cls: '4-5', who: '박서준', v: [8, 100000], at: '9/18 15:52' }
      ],
      link: 'goteacher.xyz/c/9PQ1', footer: true
    }
  ],

  // 묶음 알림(🔔). 오늘 08:20 묶음이 왔고, 다음은 12:20
  bundles: [
    { at: '08:20', label: '아침 묶음', items: [
      { kind: '마감', text: '방과후 수요조사 (반별 인원) — 오늘 14:00 마감', href: 'submit.html' },
      { kind: '바뀜', text: '4학년 학년협의 장소 4-3 교실 → 4-1 교실 · 송민재', href: 'today.html' },
      { kind: '제출', text: '학예회 반별 참가 인원 — 김도윤 선생님이 냈어요 (3/5)', href: 'collect-detail.html' }
    ] }
  ],

  // 양식 자료실: 분류별 건수(합 79)와 보이는 목록
  formCats: [
    { k: 'all', n: '전체' }, { k: 'gyomu', n: '교무' }, { k: 'curr', n: '교육과정' }, { k: 'life', n: '생활·인성' },
    { k: 'after', n: '방과후·돌봄' }, { k: 'admin', n: '행정·예산' }, { k: 'grade', n: '학년 운영' }
  ],
  formCount: { gyomu: 22, curr: 14, life: 11, after: 9, admin: 13, grade: 10 },
  forms: [
    { id: 'f1', cat: 'after', title: '방과후학교 수요조사서(반별 인원)', ext: 'hwpx', pages: 1, table: '반 · 담임 · 희망 인원 · 희망 프로그램', updated: '2026-09-02', fill: true },
    { id: 'f2', cat: 'grade', title: '학예회 반별 참가 인원 취합표', ext: 'hwpx', pages: 1, table: '반 · 담임 · 참가 · 불참 · 비고', updated: '2026-10-05', fill: true },
    { id: 'f3', cat: 'curr', title: '학년말 교육과정 운영 평가서', ext: 'hwp', pages: 3, table: '영역 · 운영 내용 · 성과 · 개선점', updated: '2026-07-11', fill: true },
    { id: 'f4', cat: 'gyomu', title: '현장체험학습 운영 계획서', ext: 'hwp', pages: 4, table: '일시 · 장소 · 인원 · 인솔 교사', updated: '2026-04-20', fill: false },
    { id: 'f5', cat: 'admin', title: '학습준비물 신청서(학급별)', ext: 'hwpx', pages: 1, table: '물품명 · 수량 · 단가 · 금액', updated: '2026-08-28', fill: true },
    { id: 'f6', cat: 'life', title: '학교폭력 예방교육 실시 결과', ext: 'hwp', pages: 1, table: '반 · 실시일 · 시간 · 대상 인원', updated: '2026-06-03', fill: true },
    { id: 'f7', cat: 'gyomu', title: '교직원 연수 이수 현황', ext: 'hwpx', pages: 2, table: '성명 · 연수명 · 이수번호', updated: '2026-03-04', fill: true },
    { id: 'f8', cat: 'grade', title: '학년 협의록', ext: 'hwp', pages: 1, table: '일시 · 장소 · 참석 · 협의 내용', updated: '2026-03-10', fill: false }
  ],

  facts: {
    price: '동학년판 무료', beta: '베타 11월 30일', launch: '정식 12월 14일(월)',
    data: '오라클 클라우드 춘천 리전 — 데이터 국내 보관'
  }
};

// ---------- 계산부: 모든 개수·합계는 행에서 ----------
(function (g) {
  g.collections.forEach(function (c) {
    c.total = c.rows.length;
    c.done = c.rows.filter(function (r) { return r.v[0] !== null; }).length;
    c.sums = c.fields.map(function (f, i) {
      return f.t === 'number' ? c.rows.reduce(function (s, r) { return s + (r.v[i] || 0); }, 0) : null;
    });
  });
  var c1 = g.collections[0];
  console.assert(c1.done === 3 && c1.total === 5, '모으기 c1 집계 불일치');
  console.assert(c1.sums[0] === 72 && c1.sums[1] === 3, 'c1 합계 불일치');
  var n = 0; Object.keys(g.formCount).forEach(function (k) { n += g.formCount[k]; });
  g.formTotal = n;
  console.assert(n === 79, '양식 자료실 79건 불일치');
  console.assert(g.todo.length === 4, '낼 것 개수');
  console.assert(g.members.length === 5, '동학년 인원');
})(window.GT);
