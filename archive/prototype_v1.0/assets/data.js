// 티로드 프로토타입 샘플 데이터 — 가상의 '티로드초등학교'(24학급). 실존 인물·학교와 무관.
window.TR = window.TR || {};

TR.TODAY = '2026-10-26'; // 월요일
TR.NOW_SLOT = 4;          // 시연 기준 현재 = 3교시

TR.school = { name: '티로드초등학교', classes: 24, region: '인천광역시' };

// 시정표: 교시 축
TR.slots = [
  { k: 'am',  label: '아침', time: '08:40' },
  { k: '1',   label: '1교시', time: '09:00' },
  { k: '2',   label: '2교시', time: '09:50' },
  { k: 'mid', label: '중간놀이', time: '10:30' },
  { k: '3',   label: '3교시', time: '10:50' },
  { k: '4',   label: '4교시', time: '11:40' },
  { k: 'ln',  label: '점심', time: '12:20' },
  { k: '5',   label: '5교시', time: '13:10' },
  { k: '6',   label: '6교시', time: '14:00' },
  { k: 'pm',  label: '방과후', time: '14:40~' }
];
TR.slotIdx = function (k) { return TR.slots.findIndex(function (s) { return s.k === String(k); }); };

TR.rooms = ['과학실1', '과학실2', '과학실', '음악실', '미술실', '컴퓨터실', '영어실', '도서관', '시청각실', '강당', '체육관', '돌봄교실1', '돌봄교실2', '교무실', '교장실', '운동장', '다목적실'];
TR.depts = ['교무기획부', '연구부', '생활인성부', '정보과학부', '체육건강부', '방과후돌봄부', '행정실'];

// 담임 24 + 그 외 14 = 교원 38
TR.homerooms = [
  ['1-1', '강지은'], ['1-2', '윤서준'], ['1-3', '장미경'], ['1-4', '임도현'],
  ['2-1', '최수아'], ['2-2', '한예린'], ['2-3', '오승민'], ['2-4', '서지호'],
  ['3-1', '신유진'], ['3-2', '권태호'], ['3-3', '황보람'], ['3-4', '안지민'],
  ['4-1', '송민재'], ['4-2', '정하은'], ['4-3', '김민준'], ['4-4', '류세아'],
  ['5-1', '전현우'], ['5-2', '홍채원'], ['5-3', '고은비'], ['5-4', '문성훈'],
  ['6-1', '양서윤'], ['6-2', '배준혁'], ['6-3', '조아라'], ['6-4', '백승현']
];
TR.others = [
  ['교장', '남궁현', ''], ['교감', '이정민', ''], ['교무기획부장', '한상우', '교무기획부'], ['연구부장', '오지현', '연구부'],
  ['생활인성부장', '박지훈', '생활인성부'], ['음악전담', '이서연', '연구부'], ['영어전담', '유가람', '정보과학부'],
  ['체육전담', '노태윤', '체육건강부'], ['보건', '진소희', '체육건강부'], ['사서', '하지원', '연구부'],
  ['전문상담', '표다은', '생활인성부'], ['특수', '엄재석', '생활인성부'], ['과학전담', '차민호', '정보과학부'], ['방과후부장', '석가영', '방과후돌봄부']
];
TR.staff = TR.homerooms.map(function (h) { return { name: h[1], role: h[0] + ' 담임', cls: h[0] }; })
  .concat(TR.others.map(function (o) { return { name: o[1], role: o[0], cls: '', dept: o[2] }; }));

TR.me = { name: '정하은', cls: '4-2', role: '4-2 담임 · 방과후돌봄부 계원' };
TR.vp = { name: '이정민', role: '교감' };

// 내 시간표(오늘)
TR.myTimetable = [
  { k: '1', sub: '국어', room: '4-2' },
  { k: '2', sub: '수학', room: '4-2' },
  { k: '3', sub: '과학 · 수업공개', room: '4-2', note: '참관: 교감·4학년부' },
  { k: '4', sub: '사회', room: '4-2' },
  { k: '5', sub: '보강 4-3 (국어)', room: '4-3', boost: true, note: '김민준 선생님 출장' },
  { k: '6', sub: '창체', room: '4-2' }
];

// 일정. s/e = 슬롯 키, type: event(행사) work(업무·교육) meet(회의) duty(복무)
TR.seedEvents = [
  { id: 'e1', date: '2026-10-26', s: 'am', e: 'am', time: '08:40', title: '교직원 아침 협의', place: '교무실', target: '전 교직원', type: 'meet', by: '한상우' },
  { id: 'e2', date: '2026-10-26', s: '1', e: '2', title: '1학년 학교폭력예방교육', place: '시청각실', target: '1-1~1-4', type: 'work', by: '박지훈' },
  { id: 'e3', date: '2026-10-26', s: '1', e: '4', title: '5학년 현장체험학습', place: '국립과천과학관', target: '5학년', type: 'event', by: '전현우', note: '인솔 담임 4명 · 버스 08:50 출발' },
  { id: 'e4', date: '2026-10-26', s: '3', e: '3', title: '4-2 수업공개', place: '4-2 교실', target: '참관: 교감·4학년부', type: 'event', by: '정하은', mine: true },
  { id: 'e5', date: '2026-10-26', s: '3', e: '4', title: '6학년 진로체험', place: '과학실1', target: '6-1·6-2', type: 'work', by: '양서윤' },
  { id: 'e6', date: '2026-10-26', s: 'mid', e: 'mid', title: '학생자치회의', place: '도서관', target: '회장단', type: 'meet', by: '표다은' },
  { id: 'e7', date: '2026-10-26', s: '5', e: '6', title: '3학년 수영교육 출발', place: '버스(정문)', target: '3학년', type: 'event', by: '신유진', changed: true, change: '출발 <del>13:00</del> → <ins>13:10 (5교시 시작)</ins>' },
  { id: 'e8', date: '2026-10-26', s: 'pm', e: 'pm', time: '15:00', title: '학교폭력전담기구 회의', place: '교장실', target: '생활부·교감', type: 'meet', by: '박지훈' },
  { id: 'e9', date: '2026-10-26', s: 'pm', e: 'pm', time: '15:30', title: '학년부장 협의회', place: '교무실', target: '학년부장 6명', type: 'meet', by: '한상우' },
  { id: 'e10', date: '2026-10-26', s: 'pm', e: 'pm', time: '16:00', title: '소방 합동점검', place: '전 교실', target: '행정실', type: 'work', by: '행정실' },

  { id: 'e11', date: '2026-10-27', s: '1', e: '2', title: '2학년 생존수영 이론', place: '시청각실', target: '2학년', type: 'work', by: '최수아' },
  { id: 'e12', date: '2026-10-27', s: '3', e: '4', title: '4학년 과학실 안전교육', place: '과학실1', target: '4학년', type: 'work', by: '송민재' },
  { id: 'e13', date: '2026-10-27', s: 'pm', e: 'pm', time: '12:00', title: '방과후 수요조사 마감', place: '', target: '담임', type: 'work', by: '정하은' },
  { id: 'e14', date: '2026-10-27', s: 'pm', e: 'pm', time: '15:00', title: '전문적학습공동체(4학년)', place: '4-1 교실', target: '4학년부', type: 'meet', by: '송민재' },
  { id: 'e15', date: '2026-10-28', s: '1', e: '4', title: '전교 체육대회', place: '운동장', target: '전교', type: 'event', by: '노태윤', note: '우천 시 10/30 순연' },
  { id: 'e16', date: '2026-10-28', s: 'pm', e: 'pm', time: '15:00', title: '교육과정 협의회', place: '교무실', target: '전 교원', type: 'meet', by: '오지현' },
  { id: 'e17', date: '2026-10-29', s: '6', e: '6', title: '학예회 리허설', place: '체육관', target: '1~3학년', type: 'event', by: '한상우', changed: true, change: '장소 <del>강당</del> → <ins>체육관</ins>, <del>5교시</del> → <ins>6교시</ins>' },
  { id: 'e18', date: '2026-10-29', s: '3', e: '4', title: '5학년 성교육', place: '시청각실', target: '5학년', type: 'work', by: '진소희' },
  { id: 'e19', date: '2026-10-30', s: '1', e: '6', title: '현장체험학습(3·4학년)', place: '인천어린이과학관', target: '3·4학년', type: 'event', by: '송민재' },
  { id: 'e21', date: '2026-10-29', s: '4', e: '4', title: '6학년 미디어리터러시 영상교육', place: '시청각실', target: '6학년', type: 'work', by: '배준혁' },
  { id: 'e20', date: '2026-10-30', s: 'pm', e: 'pm', time: '17:00', title: '교원 연수이수 실적 마감', place: '', target: '전 교원', type: 'work', by: '오지현' },

  { id: 'd1', date: '2026-10-26', s: '1', e: '6', title: '김민준 출장', place: '교육지원청', target: '연수', type: 'duty', duty: '출장', who: '김민준', time: '09:00–17:00', boost: '4-3 1~6교시', need: ['1', '2', '3', '4', '6'] },
  { id: 'd2', date: '2026-10-26', s: '6', e: 'pm', title: '이서연 조퇴', place: '', target: '병원', type: 'duty', duty: '조퇴', who: '이서연', time: '14:00~', boost: '' },
  { id: 'd3', date: '2026-10-26', s: '3', e: 'ln', title: '박지훈 외출', place: '', target: '개인', type: 'duty', duty: '외출', who: '박지훈', time: '11:00–12:30', boost: '' },
  { id: 'd4', date: '2026-10-26', s: '1', e: '6', title: '최수아 연가', place: '', target: '', type: 'duty', duty: '연가', who: '최수아', time: '종일', boost: '2-1 1~6교시', need: ['3', '4', '5', '6'], changed: true, change: '연가 신규 등록 · 2-1 보강 필요' }
];

// 월간용 추가 일정
TR.monthExtra = [
  ['2026-10-01', '국군의날 계기교육', 'work'], ['2026-10-02', '2학기 학부모 공개수업', 'event'], ['2026-10-05', '학교운영위원회', 'meet'],
  ['2026-10-07', '독서의 달 행사', 'event'], ['2026-10-08', '교직원 연수(생성형AI)', 'work'], ['2026-10-12', '학년별 협의회', 'meet'],
  ['2026-10-13', '1학년 숲체험', 'event'], ['2026-10-14', '정보보안 점검', 'work'], ['2026-10-15', '2학년 현장체험', 'event'],
  ['2026-10-16', '학생자치 선거', 'event'], ['2026-10-19', '방과후 2분기 시작', 'work'], ['2026-10-20', '교직원 체육행사', 'event'],
  ['2026-10-21', '학교평가 설문 마감', 'work'], ['2026-10-22', '6학년 수학여행 설명회', 'meet'], ['2026-10-23', '안전점검의 날', 'work'],
  ['2026-11-02', '학부모 상담주간 시작', 'event'], ['2026-11-04', '교육과정 평가회', 'meet'], ['2026-11-06', '학예회', 'event'],
  ['2026-11-10', '6학년 수학여행', 'event'], ['2026-11-11', '6학년 수학여행', 'event'], ['2026-11-13', '학부모 상담주간 종료', 'event'],
  ['2026-11-16', '2학기 수행평가 점검', 'work'], ['2026-11-18', '학교운영위원회', 'meet'], ['2026-11-19', '수능일 · 등교시각 조정', 'work'],
  ['2026-11-25', '교원능력개발평가', 'work'], ['2026-11-27', '정보공시 자료 제출', 'work']
];
TR.holidays = { '2026-10-03': '개천절', '2026-10-09': '한글날', '2026-10-05': '대체공휴일' };

// 모으기(취합)
TR.programs = ['로봇코딩', '바둑', '방송댄스', '생명과학', '축구', '우쿨렐레'];
(function () {
  function seeded(i) { var x = Math.sin(i * 9301 + 49297) * 233280; return x - Math.floor(x); }
  var rows1 = TR.homerooms.map(function (h, i) {
    var done = [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 16, 17, 18, 20, 21, 23].indexOf(i) >= 0; // 17/24
    return { unit: h[0], who: h[1], done: done, at: done ? '10/' + (23 + (i % 3)) + ' ' + (8 + (i % 6)) + ':' + (10 + i) : '',
      v: done ? [String(5 + Math.floor(seeded(i) * 14)), TR.programs[i % 6], i % 5 === 0 ? '돌봄 연계 희망 2명' : ''] : ['', '', ''] };
  });
  var teachers = TR.staff;
  var rows2 = teachers.map(function (t, i) {
    var done = i % 9 !== 4 && i % 9 !== 7 && i % 9 !== 2 && i !== 13 ? (i < 30) : false;
    return { unit: t.role, who: t.name, done: done, at: done ? '10/2' + (i % 5) : '', v: done ? ['디지털 기반 수업혁신', String(15 + (i % 4) * 15), '2026-0' + (7 + i % 3) + '-1' + (i % 9)] : ['', '', ''] };
  });
  // 정확히 21/38 맞춤
  var c = 0; rows2.forEach(function (r, i) { if (r.done) { c++; if (c > 21) { r.done = false; r.at = ''; r.v = ['', '', '']; } } });
  rows2.forEach(function (r) { if (r.who === '정하은') { r.done = false; r.v = ['', '', '']; r.at = ''; } });
  c = rows2.filter(function (r) { return r.done; }).length;
  for (var i = 0; c < 21 && i < rows2.length; i++) { if (!rows2[i].done && rows2[i].who !== '정하은') { rows2[i].done = true; rows2[i].at = '10/24'; rows2[i].v = ['교권보호 직무연수', '15', '2026-08-12']; c++; } }
  var rows3 = TR.homerooms.slice(8, 16).map(function (h, i) {
    var done = h[0] !== '4-2' && h[0] !== '3-3';
    return { unit: h[0], who: h[1], done: done, at: done ? '10/26 0' + (8 + i % 2) + ':' + (12 + i * 5) : '', v: done ? [String(21 + i % 4), String(i % 2), i % 3 === 0 ? '예' : '아니오'] : ['', '', ''] };
  });
  TR.seedCollections = [
    { id: 'c1', title: '2학기 방과후 수요조사', owner: '정하은', dept: '방과후돌봄부', mine: true, due: '10/27(화) 12:00', dueISO: '2026-10-27T12:00',
      target: '담임 24명 · 반별 1칸', fields: [{ n: '희망 인원', t: 'number' }, { n: '1순위 프로그램', t: 'select', o: TR.programs }, { n: '비고', t: 'text' }], rows: rows1 },
    { id: 'c2', title: '교원 연수이수 실적', owner: '오지현', dept: '연구부', due: '10/30(금) 17:00', dueISO: '2026-10-30T17:00',
      target: '전 교원 38명 · 개인별 1칸', fields: [{ n: '연수명', t: 'text' }, { n: '이수시간', t: 'number' }, { n: '이수일', t: 'date' }], rows: rows2 },
    { id: 'c3', title: '11월 현장체험 참가 명단', owner: '한상우', dept: '교무기획부', due: '오늘 14:00', dueISO: '2026-10-26T14:00', urgent: true,
      target: '3·4학년 담임 8명 · 반별 1칸', fields: [{ n: '참가 학생 수', t: 'number' }, { n: '불참 학생 수', t: 'number' }, { n: '인솔 보조 필요', t: 'select', o: ['예', '아니오'] }], rows: rows3 }
  ];
})();

// 공지(버전)
TR.seedNotices = [
  { id: 'n2', title: '학예회 리허설 안내', dept: '교무기획부', by: '한상우', ver: 2, updated: '10/25(일) 19:40', must: true, real: true,
    versions: [
      { v: 1, at: '10/22(목) 16:05', fields: { 일시: '10/29(목) 5교시', 장소: '강당', 대상: '1~3학년 (학년별 15분)', 준비물: '무대 의상, 소품' } },
      { v: 2, at: '10/25(일) 19:40', fields: { 일시: '10/29(목) 6교시', 장소: '체육관', 대상: '1~3학년 (학년별 15분)', 준비물: '무대 의상, 소품 · 실내화' } }
    ],
    body: '학예회(11/6) 리허설을 아래와 같이 진행합니다. 강당 음향 공사로 장소가 체육관으로 바뀌었습니다. 담임 선생님께서는 학년 순서에 맞춰 이동해 주세요. 4~6학년은 11/2(월) 리허설 예정입니다.',
    seen: 28, total: 38, seenV2: 19 },
  { id: 'n1', title: '11월 학부모 상담주간 안내', dept: '교무기획부', by: '한상우', ver: 1, updated: '10/23(금) 14:10', must: true, real: false,
    versions: [{ v: 1, at: '10/23(금) 14:10', fields: { 기간: '11/2(월) ~ 11/13(금)', 방식: '대면·전화 중 학부모 선택', 신청: '하이클래스 신청서 10/30까지', 결과: '상담 기록 11/20까지 나이스 입력' } }],
    body: '학부모 상담주간 운영 계획입니다. 상담 시간은 방과후(14:50~16:30)를 원칙으로 하며, 담임 선생님별 가능 시간을 10/28(수)까지 입력해 주세요.',
    seen: 31, total: 38, seenV2: 31 },
  { id: 'n3', title: '교직원 독감 예방접종 신청', dept: '체육건강부', by: '진소희', ver: 1, updated: '10/21(수) 09:00', must: false, real: false,
    versions: [{ v: 1, at: '10/21(수) 09:00', fields: { 기간: '11/3(화) 14:50~', 장소: '보건실', 대상: '희망 교직원' } }],
    body: '희망자는 보건실 앞 명단에 서명하거나 링크로 신청해 주세요.', seen: 33, total: 52, seenV2: 33 }
];
TR.unseenN2 = ['송민재', '류세아', '안지민', '권태호', '오승민', '서지호', '노태윤', '차민호', '엄재석', '하지원'];
