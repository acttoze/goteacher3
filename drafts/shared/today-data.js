// 고티처 시안 공통 샘플 데이터 — 세 시안이 같은 하루를 보여 주도록 한 곳에 둔다.
// 모두 가상의 학교·인물이다. 실제 서비스에서는 NEIS·모으기 데이터로 바뀐다.
window.GT = {
  product: '고티처',
  school: '새솔초등학교',
  me: { name: '정하은', role: '4학년 2반 담임', grade: '4학년', initial: '정' },
  date: { iso: '2026-10-26', label: '10월 26일 월요일', week: '2학기 9주차' },
  nowSlot: 'b2', // 지금 = 2블록
  nextBundle: { at: '12:20', label: '2블록 끝' }, // 묶음 알림 08:20·12:20·방과후

  slots: [
    { k: 'b1', label: '1블록', from: '08:40', to: '10:30' },
    { k: 'b2', label: '2블록', from: '10:30', to: '12:20' },
    { k: 'b3', label: '3블록', from: '12:20', to: '14:40' },
    { k: 'pm', label: '방과후', from: '14:40', to: '' }
  ],

  // NEIS에서 자동으로 들어오는 것
  meal: ['잡곡밥', '쇠고기미역국', '돈육장조림', '시금치나물', '깍두기', '감귤'],
  mealKcal: 642,
  neis: '독서주간 1일차 (10/26~10/30)',

  // 내 시간표(오늘)
  timetable: [
    { k: 'b1', sub: '국어 · 수학', room: '4-2' },
    { k: 'b2', sub: '과학 수업공개 · 사회', room: '4-2', note: '참관: 교감 · 4학년부' },
    { k: 'b3', sub: '체육 · 창체', room: '체육관 → 4-2', note: '체육은 노태윤 선생님(전담)' }
  ],

  // 동학년(4학년) 오늘 일정. s/e 없으면 전일(시간 미정)
  events: [
    { id: 'e1', s: 'b1', e: 'b1', title: '독서주간 아침 10분 독서', place: '각 교실', who: '전교' },
    { id: 'e2', s: 'b2', e: 'b2', title: '4-2 수업공개', place: '4-2 교실', who: '참관: 교감 · 4학년부', mine: true },
    { id: 'e3', s: 'b3', e: 'b3', title: '3학년 수영교육 출발 13:10 정문', place: '정문 버스', who: '3학년', changed: { what: '출발', from: '13:00', to: '13:10' } },
    { id: 'e4', s: 'pm', e: 'pm', title: '학년부장 협의회 15:30', place: '교무실', who: '학년부장' },
    { id: 'e5', title: '4학년 학년협의 — 학예회 순서 정하기', place: '4-1 교실', who: '4학년부', changed: { what: '장소', from: '4-3 교실', to: '4-1 교실' } },
    { id: 'e6', title: '학예회 소품 준비물 확인', place: '', who: '4학년', mine: true }
  ],

  // 어제 퇴근 이후 바뀐 것(나 · 우리 학년 대상만)
  changes: [
    { title: '4학년 학년협의', what: '장소', from: '4-3 교실', to: '4-1 교실', by: '송민재' },
    { title: '3학년 수영교육', what: '출발', from: '13:00', to: '13:10', by: '신유진' },
    { title: '학예회 리허설(10/29)', what: '장소 · 시간', from: '강당 · 2블록', to: '체육관 · 3블록', by: '한상우' }
  ],

  // 나에게 온 것 — 내가 낼 것
  todo: [
    { id: 't1', title: '방과후 수요조사 (반별 인원)', from: '석가영 · 방과후돌봄부', due: '오늘 14:00', urgent: true, kind: '제출', form: '한글 양식' },
    { id: 't3', title: '학예회 의상 대여 수량', from: '송민재 · 4학년 대표', due: '10/28(수)', kind: '제출', form: '한글 양식' },
    { id: 't2', title: '2학기 교육과정 평가 설문', from: '오지현 · 교무부', due: '10/30(금)', kind: '제출', form: '웹 표' },
    { id: 't4', title: '개인정보 보호 연수 이수 확인', from: '차민호 · 정보부', due: '11/6(금)', kind: '확인', form: '' }
  ],

  // 내가 보낸 것 — 진행 중인 모으기. done/total은 행에서 세지 말고 여기 값만 쓴다
  sent: [
    {
      id: 's1', title: '학예회 반별 참가 인원', form: '2026_학예회_참가인원.hwpx', due: '10/27(화) 12:00',
      rows: [
        { who: '4-1 송민재', done: true }, { who: '4-2 정하은', done: true }, { who: '4-3 김도윤', done: true },
        { who: '4-4 이수빈', done: false }, { who: '4-5 박서준', done: false }
      ]
    }
  ],

  // 시안에 적어도 되는 사실(R1 동학년판, meeting/27·29 기준)
  facts: {
    price: '동학년판 무료',
    beta: '베타 11월 30일',
    launch: '정식 12월 14일(월)',
    login: '구글 로그인 한 번',
    join: '초대받은 선생님은 가입 없이 이름만 고르고 입장',
    submit: '받는 사람은 링크 하나로 제출 — 로그인 없음, 대신 내기 허용',
    hwp: 'hwp·hwpx 원본 양식을 올리면 그 양식 그대로 채워서 돌려받기(올린 형식 + hwpx)',
    paste: '한글·엑셀 표를 복사해 붙여넣으면 칸이 된다',
    student: '학생 정보는 받지 않는다 — 교직원 정보만',
    forms: '양식 자료실 79건, 로그인 없이 보기·내려받기',
    alerts: '알림은 하루 3번 묶어서(08:20 · 12:20 · 방과후), 수업 중엔 띄우지 않음',
    data: '오라클 클라우드 춘천 리전 — 데이터 국내 보관',
    neis: 'NEIS 학사일정 · 급식 자동'
  }
};
(function (g) {
  var s = g.sent[0];
  s.total = s.rows.length;
  s.done = s.rows.filter(function (r) { return r.done; }).length;
  console.assert(s.done === 3 && s.total === 5, '모으기 집계 불일치');
  console.assert(g.todo.length === 4, '낼 것 개수');
})(window.GT);
