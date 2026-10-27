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

// 시간대(블록): 일정 화면은 교시 대신 이 네 칸으로 본다. 블록수업(80분 = 2교시 묶음)에서 쓰는 실제 이름.
// from/to = TR.slots 인덱스. 전일(시간 미정·종일)은 이 네 칸 위에 따로 둔다.
TR.bands = [
  { k: 'b1', name: '1블록', sub: '아침 · 1·2교시', time: '08:40~10:30', from: 0, to: 2 },
  { k: 'b2', name: '2블록', sub: '중간놀이 · 3·4교시', time: '10:30~12:20', from: 3, to: 5 },
  { k: 'b3', name: '3블록', sub: '점심 · 5·6교시', time: '12:20~14:40', from: 6, to: 8 },
  { k: 'b4', name: '방과후', sub: '14:40 이후', time: '14:40~', from: 9, to: 9 }
];

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

TR.me = { name: '정하은', cls: '4-2', role: '4학년 2반 · 방과후돌봄부' };
TR.vp = { name: '이정민', role: '교감' };

// ---------- 그룹(소속) ----------
// 모든 학교 공통 기본 그룹. 직책은 고정 목록 + 직접 입력. 부서(업무분장)는 그룹과 별개 축.
// 전담은 주 학년 그룹에 '전담'으로 소속. 교장·보건·사서·상담·특수·돌봄은 교무실·기타/지원 어디서나 고를 수 있음.
TR.SUPPORT_ROLES = ['교장', '보건', '사서', '전문상담', '특수', '돌봄전담사'];
TR.groups = [
  { id: 'g1', name: '1학년', kind: 'grade' }, { id: 'g2', name: '2학년', kind: 'grade' }, { id: 'g3', name: '3학년', kind: 'grade' },
  { id: 'g4', name: '4학년', kind: 'grade' }, { id: 'g5', name: '5학년', kind: 'grade' }, { id: 'g6', name: '6학년', kind: 'grade' },
  { id: 'office', name: '교무실' }, { id: 'admin', name: '행정실' }, { id: 'meal', name: '급식소' }, { id: 'support', name: '기타/지원' }
];
TR.groupRoles = function (gid) {
  var g = TR.groups.find(function (x) { return x.id === gid; });
  if (g && g.kind === 'grade') { var r = []; for (var i = 1; i <= TR.school.classes / 6; i++) r.push(i + '반'); return r.concat(['전담']); }
  return { office: ['교감', '교무부장', '교무', '교무실무사', '행정원'].concat(TR.SUPPORT_ROLES), admin: ['실장', '차장', '계장', '주무관'], meal: ['영양교사', '조리사', '조리실무사'], support: TR.SUPPORT_ROLES.slice() }[gid] || [];
};
// 구성원: 본인이 그룹·직책을 고르고 고칠 수 있음. sub = 전담 과목 등 메모
TR.posText = function (m) { var g = TR.groups.find(function (x) { return x.id === m.group; }); return (g && g.kind === 'grade' ? g.name + ' ' : '') + m.pos + (m.sub ? '(' + m.sub + ')' : ''); };
TR.seedMembers = TR.homerooms.map(function (h) { return { name: h[1], group: 'g' + h[0][0], pos: h[0].slice(2) + '반' }; }).concat([
  { name: '남궁현', group: 'office', pos: '교장' }, { name: '이정민', group: 'office', pos: '교감' },
  { name: '한상우', group: 'office', pos: '교무부장' }, { name: '오지현', group: 'office', pos: '교무', sub: '연구부장' },
  { name: '진소희', group: 'office', pos: '보건' }, { name: '윤채린', group: 'office', pos: '교무실무사' },
  { name: '석가영', group: 'g1', pos: '전담', sub: '방과후' }, { name: '이서연', group: 'g3', pos: '전담', sub: '음악' },
  { name: '노태윤', group: 'g4', pos: '전담', sub: '체육' }, { name: '유가람', group: 'g5', pos: '전담', sub: '영어' },
  { name: '차민호', group: 'g5', pos: '전담', sub: '과학' }, { name: '박지훈', group: 'g6', pos: '전담', sub: '도덕' },
  { name: '구본석', group: 'admin', pos: '실장' }, { name: '민지아', group: 'admin', pos: '주무관' }, { name: '탁현수', group: 'admin', pos: '주무관' },
  { name: '도하경', group: 'meal', pos: '영양교사' }, { name: '변은영', group: 'meal', pos: '조리실무사' },
  { name: '하지원', group: 'support', pos: '사서' }, { name: '표다은', group: 'support', pos: '전문상담' },
  { name: '엄재석', group: 'support', pos: '특수' }, { name: '육소라', group: 'support', pos: '돌봄전담사' }
]);

// ---------- 업무분장(부서) — 그룹과 별개 축 ----------
TR.duties = [
  ['교무기획부', '부장 · 학사일정 · 교육과정', '한상우'], ['교무기획부', '학적 · 전입학', '강지은'], ['교무기획부', '교과서', '윤서준'], ['교무기획부', '학교운영위원회', '장미경'],
  ['연구부', '부장 · 교원 연수', '오지현'], ['연구부', '수업공개 · 장학', '송민재'], ['연구부', '학교평가', '임도현'], ['연구부', '독서 · 도서관', '하지원'],
  ['생활인성부', '부장 · 학교폭력', '박지훈'], ['생활인성부', '학생자치', '양서윤'], ['생활인성부', '인성 · 안전교육', '신유진'], ['생활인성부', '상담', '표다은'], ['생활인성부', '특수교육', '엄재석'],
  ['정보과학부', '부장 · 정보보안 · 기기', '차민호'], ['정보과학부', '과학 · 발명', '전현우'], ['정보과학부', '영재교육', '홍채원'], ['정보과학부', '영어교육', '유가람'],
  ['체육건강부', '부장 · 체육행사', '노태윤'], ['체육건강부', '보건 · 성교육', '진소희'], ['체육건강부', '생존수영', '최수아'], ['체육건강부', '급식', '도하경'],
  ['방과후돌봄부', '부장 · 돌봄교실', '석가영'], ['방과후돌봄부', '방과후학교', '정하은'], ['방과후돌봄부', '늘봄학교', '류세아'], ['방과후돌봄부', '돌봄 운영', '육소라'],
  ['행정실', '예산 · 회계', '구본석'], ['행정실', '급여 · 복무 서류', '민지아'], ['행정실', '시설 · 안전', '탁현수']
];

// 내 시간표(오늘)
TR.myTimetable = [
  { k: '1', sub: '국어', room: '4-2' },
  { k: '2', sub: '수학', room: '4-2' },
  { k: '3', sub: '과학 · 수업공개', room: '4-2', note: '참관: 교감·4학년부' },
  { k: '4', sub: '사회', room: '4-2' },
  { k: '5', sub: '체육', room: '체육관', note: '노태윤 선생님(전담)' },
  { k: '6', sub: '창체', room: '4-2' }
];

// 일정. s/e = 슬롯 키, type: event(행사) work(업무·교육) meet(회의) duty(복무)
TR.seedEvents = [
  { id: 'e1', date: '2026-10-26', s: 'am', e: 'am', time: '08:40', title: '교직원 아침 협의', place: '교무실', target: '전 교직원', type: 'meet', by: '한상우' },
  { id: 'e2', date: '2026-10-26', s: '1', e: '2', title: '1학년 학교폭력예방교육', place: '시청각실', target: '1-1~1-4', type: 'work', by: '박지훈' },
  { id: 'e3', date: '2026-10-26', s: '1', e: '4', title: '5학년 현장체험학습 - 국립과천과학관 (08:50 버스 출발, 인솔 담임 4명)', place: '국립과천과학관', target: '5학년', type: 'event', by: '전현우' },
  { id: 'e4', date: '2026-10-26', s: '3', e: '3', title: '4-2 수업공개', place: '4-2 교실', target: '참관: 교감·4학년부', type: 'event', by: '정하은', mine: true },
  { id: 'e5', date: '2026-10-26', s: '3', e: '4', title: '6학년 진로체험', place: '과학실1', target: '6-1·6-2', type: 'work', by: '양서윤' },
  { id: 'e6', date: '2026-10-26', s: 'mid', e: 'mid', title: '학생자치회의', place: '도서관', target: '회장단', type: 'meet', by: '표다은' },
  { id: 'e7', date: '2026-10-26', s: '5', e: '6', title: '3학년 수영교육 출발 13:10 정문 버스 (수영복·수모 지참)', place: '버스(정문)', target: '3학년', type: 'event', by: '신유진', changed: true, change: '출발 <del>13:00</del> → <ins>13:10 (5교시 시작)</ins>' },
  { id: 'e8', date: '2026-10-26', s: 'pm', e: 'pm', time: '15:00', title: '학교폭력전담기구 회의', place: '교장실', target: '생활부·교감', type: 'meet', by: '박지훈' },
  { id: 'e9', date: '2026-10-26', s: 'pm', e: 'pm', time: '15:30', title: '학년부장 협의회', place: '교무실', target: '학년부장 6명', type: 'meet', by: '한상우' },
  { id: 'e10', date: '2026-10-26', s: 'pm', e: 'pm', time: '16:00', title: '소방 합동점검', place: '전 교실', target: '행정실', type: 'work', by: '탁현수' },

  { id: 'e11', date: '2026-10-27', s: '1', e: '2', title: '2학년 생존수영 이론', place: '시청각실', target: '2학년', type: 'work', by: '최수아' },
  { id: 'e12', date: '2026-10-27', s: '3', e: '4', title: '4학년 과학실 안전교육 3~4교시 과학실1 (실험복 지참)', place: '과학실1', target: '4학년', type: 'work', by: '송민재' },
  { id: 'e13', date: '2026-10-27', s: 'pm', e: 'pm', time: '12:00', title: '방과후 수요조사 마감 12시까지 (모으기 링크로 반별 제출)', place: '', target: '담임', type: 'work', by: '정하은' },
  { id: 'e14', date: '2026-10-27', s: 'pm', e: 'pm', time: '15:00', title: '전문적학습공동체(4학년)', place: '4-1 교실', target: '4학년부', type: 'meet', by: '송민재' },
  { id: 'e15', date: '2026-10-28', s: '1', e: '4', title: '전교 체육대회 1~4교시 운동장 (우천 시 10/30 순연)', place: '운동장', target: '전교', type: 'event', by: '노태윤' },
  { id: 'e16', date: '2026-10-28', s: 'pm', e: 'pm', time: '15:00', title: '교육과정 협의회', place: '교무실', target: '전 교원', type: 'meet', by: '오지현' },
  { id: 'e17', date: '2026-10-29', s: '6', e: '6', title: '학예회 리허설 1~3학년 6교시 체육관 (학년별 15분)', place: '체육관', target: '1~3학년', type: 'event', by: '한상우', changed: true, change: '장소 <del>강당</del> → <ins>체육관</ins>, <del>5교시</del> → <ins>6교시</ins>' },
  { id: 'e18', date: '2026-10-29', s: '3', e: '4', title: '5학년 성교육', place: '시청각실', target: '5학년', type: 'work', by: '진소희' },
  { id: 'e19', date: '2026-10-30', s: '1', e: '6', title: '현장체험학습(3·4학년)', place: '인천어린이과학관', target: '3·4학년', type: 'event', by: '송민재' },
  { id: 'e21', date: '2026-10-29', s: '4', e: '4', title: '6학년 미디어리터러시 영상교육', place: '시청각실', target: '6학년', type: 'work', by: '배준혁' },
  { id: 'e20', date: '2026-10-30', s: 'pm', e: 'pm', time: '17:00', title: '교원 연수이수 실적 마감', place: '', target: '전 교원', type: 'work', by: '오지현' },

  // 날짜만 입력한 일정(시간 미정) → 「전일」 칸. 필요한 사람만 끌어서 시간대를 정한다.
  { id: 'e22', date: '2026-10-26', title: '급식실 위생점검 (교육지원청 방문)', place: '급식소', target: '급식소', type: 'work', by: '도하경' },
  { id: 'e23', date: '2026-10-26', title: '4학년 학예회 소품 준비물 확인', place: '', target: '4학년', type: 'work', by: '정하은', mine: true },
  { id: 'e24', date: '2026-10-26', title: '4학년 학년협의 (학예회 순서 정하기)', place: '4-1 교실', target: '4학년부', type: 'meet', by: '송민재' },
  { id: 'e25', date: '2026-10-27', title: '정보보안 점검의 날 (PC 보안 점검표 제출)', place: '', target: '전 교직원', type: 'work', by: '차민호' },
  { id: 'e26', date: '2026-10-28', title: '학부모 상담 가능 시간 입력 마감', place: '', target: '담임', type: 'work', by: '한상우' },
  { id: 'e27', date: '2026-10-29', title: '2학년 학예회 의상 대여 수령', place: '', target: '2학년', type: 'event', by: '한예린' },

  { id: 'd1', date: '2026-10-26', s: '1', e: '6', title: '김민준 출장', place: '교육지원청', target: '연수', type: 'duty', duty: '출장', who: '김민준', time: '09:00–17:00' },
  { id: 'd2', date: '2026-10-26', s: '6', e: 'pm', title: '이서연 조퇴', place: '', target: '병원', type: 'duty', duty: '조퇴', who: '이서연', time: '14:00~', },
  { id: 'd3', date: '2026-10-26', s: '3', e: 'ln', title: '박지훈 외출', place: '', target: '개인', type: 'duty', duty: '외출', who: '박지훈', time: '11:00–12:30' },
  { id: 'd4', date: '2026-10-26', s: '1', e: '6', title: '최수아 연가', place: '', target: '', type: 'duty', duty: '연가', who: '최수아', time: '종일', changed: true, change: '<ins>연가 신규 등록</ins>' }
];

// 월간용 추가 일정
TR.monthExtra = [
  ['2026-10-01', '국군의날 계기교육', 'work', '교무실'], ['2026-10-02', '2학기 학부모 공개수업', 'event', '교무실'], ['2026-10-05', '학교운영위원회', 'meet', '교무실'],
  ['2026-10-07', '독서의 달 행사', 'event', '기타/지원'], ['2026-10-08', '교직원 연수(생성형AI)', 'work', '교무실'], ['2026-10-12', '학년별 협의회', 'meet', '교무실'],
  ['2026-10-13', '1학년 숲체험', 'event', '1학년'], ['2026-10-14', '정보보안 점검', 'work', '5학년'], ['2026-10-15', '2학년 현장체험', 'event', '2학년'],
  ['2026-10-16', '학생자치 선거', 'event', '6학년'], ['2026-10-19', '방과후 2분기 시작', 'work', '1학년'], ['2026-10-20', '교직원 체육행사', 'event', '4학년'],
  ['2026-10-21', '학교평가 설문 마감', 'work', '교무실'], ['2026-10-22', '6학년 수학여행 설명회', 'meet', '6학년'], ['2026-10-23', '안전점검의 날', 'work', '행정실'],
  ['2026-11-02', '학부모 상담주간 시작', 'event', '교무실'], ['2026-11-04', '교육과정 평가회', 'meet', '교무실'], ['2026-11-06', '학예회', 'event', '교무실'],
  ['2026-11-10', '6학년 수학여행', 'event', '6학년'], ['2026-11-11', '6학년 수학여행', 'event', '6학년'], ['2026-11-13', '학부모 상담주간 종료', 'event', '교무실'],
  ['2026-11-16', '2학기 수행평가 점검', 'work', '교무실'], ['2026-11-18', '학교운영위원회', 'meet', '교무실'], ['2026-11-19', '수능일 · 등교시각 조정', 'work', '교무실'],
  ['2026-11-25', '교원능력개발평가', 'work', '교무실'], ['2026-11-27', '정보공시 자료 제출', 'work', '행정실']
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

// ---------- 모으기 · 자주 쓰는 양식(누르면 바로 만들어짐) ----------
// 실제 학교 구글 시트 두 가지(학습준비물 신청, 교직원 연수 이수 현황)의 항목·방식을 옮겼다.
//  supplies(kind 'items'): 반마다 줄을 여러 개 추가. 금액 = 수량 × 단가, 학급 예산에서 잔액 자동, 학년 합계 자동.
//  training(kind 'matrix'): 연수 목록 × 교직원 명단. 칸마다 이수번호. 연수별 이수율 자동.
TR.TRAININGS = [
  { n: '4대폭력 예방교육', due: '10월까지', target: '전 교직원', by: '진소희' },
  { n: '감염병 예방교육', due: '10월까지', target: '전 교직원', by: '진소희' },
  { n: '개인정보보호 교육', due: '3월까지', target: '전 교직원', by: '차민호' },
  { n: '직장 내 장애인식개선교육', due: '~10.30', target: '기간제 교원·교육공무직원', by: '엄재석' },
  { n: '청렴교육', due: '9.30까지', target: '전 교직원', by: '구본석' },
  { n: '공직자 안보교육', due: '9.30까지', target: '교원·지방공무원', by: '구본석' },
  { n: '사회적 장애인식개선교육', due: '~10.30', target: '전 교직원(대면 미참석자)', by: '엄재석' },
  { n: '아동학대 신고의무자 교육', due: '~11.30', target: '전 교직원', by: '표다은' },
  { n: '교직원 안전교육', due: '~11.30', target: '전 교직원 (3년 15시간)', by: '신유진' },
  { n: '긴급복지지원 신고의무 교육', due: '~12.05', target: '교원·일반직·교육공무직', by: '표다은' },
  { n: '교원 다문화교육 연수', due: '~11.30', target: '전 교원 (3년 15시간)', by: '오지현' },
  { n: '통일교육 연수', due: '~11.30', target: '전 교직원 1시간', by: '오지현' },
  { n: '장애인학대 신고의무자 교육', due: '~11.30', target: '전 교직원', by: '엄재석' },
  { n: '직장 내 괴롭힘 예방교육', due: '~10.20', target: '전 교직원', by: '이정민' }
];
TR.preset = function (key) {
  var members = (TR.state && TR.state.members) || TR.seedMembers;
  if (key === 'supplies') return {
    kind: 'items', title: '2학기 학습준비물 신청', dept: '교무기획부', budget: 100000,
    guide: ['수업에 필요한 물품으로 신청해 주세요.', '학급별 10만 원 (학년 예산 안에서 반끼리 조정 가능)', '자산이 되는 디지털 기기는 살 수 없어요.', '되도록 쿠팡·아이스크림몰 상품 링크를 붙여 주세요.', '담당자가 한꺼번에 장바구니에 담아 주문합니다.'],
    target: '담임 24명 + 도움반 · 반별 여러 줄',
    fields: [{ n: '물품명', t: 'text' }, { n: '수량', t: 'number' }, { n: '단가', t: 'number' }, { n: '금액', t: 'calc' }, { n: '구입 링크', t: 'url' }],
    rows: TR.homerooms.concat([['도움반', '엄재석']]).map(function (h) { return { unit: h[0], who: h[1], done: false, at: '', items: [] }; })
  };
  if (key === 'training') return {
    kind: 'matrix', title: '2026 교직원 법정의무연수 이수 현황', dept: '연구부',
    guide: ['연수를 마치면 이수증의 이수번호를 칸에 붙여 넣으세요.', '이미 이수한 연수(3년 주기)는 그때 이수번호를 넣으면 됩니다.', '대상이 아닌 연수는 \'해당 없음\'을 고르세요.'],
    target: '전 교직원 ' + members.length + '명 · 연수 ' + TR.TRAININGS.length + '개',
    cols: TR.TRAININGS.map(function (t) { return Object.assign({}, t); }),
    rows: members.map(function (m) { return { unit: m.name, pos: TR.posText(m), who: m.name, done: false, at: '', v: TR.TRAININGS.map(function () { return ''; }) }; })
  };
};
TR.isAllDone = function (r) { return r.v.every(function (x) { return x; }); };

// 시드 c2: 연수 이수 현황(행렬)을 반쯤 채운 상태로
(function () {
  function seeded(i) { var x = Math.sin(i * 7919 + 104729) * 10000; return x - Math.floor(x); }
  var c = Object.assign(TR.preset('training'), { id: 'c2', owner: '오지현', due: '11/30(월) 17:00', dueISO: '2026-11-30T17:00' });
  c.rows.forEach(function (r, i) {
    r.v = r.v.map(function (_, j) {
      if (r.who === '정하은') return j < 9 && j !== 4 && j !== 7 ? '제 중앙-2026-' + (310000 + i * 97 + j * 13) + '호' : '';
      if (j === 3 && !/실무|조리|주무관|돌봄/.test(r.unit)) return '해당 없음';
      return i % 5 === 1 || seeded(i * 31 + j) < (j < 7 ? .85 : .45) ? '제 중앙-2026-' + (300000 + i * 97 + j * 13) + '호' : '';
    });
    r.done = TR.isAllDone(r); r.at = r.done ? '10/2' + (i % 6) : '';
  });
  TR.seedCollections[1] = c;
})();

// ---------- 게시판 · 수리요청 ----------
// 시설 = 행정실 시설 주무관(내부 직원). 정보 = 외부 유지보수 업체(학교 밖 사람 → 이 게시판만 보는 업체 계정).
TR.vendor = { id: 'v1', name: '누리정보통신', kind: '정보 유지보수 업체', person: '업체 담당자 1명', phone: '010-****-4821', until: '2027-02-28', schools: 7, lastLogin: '10/26(월) 08:12', invitedBy: '차민호' };
TR.seedRepairs = [
  { id: 'r1', cat: '정보', title: '전자칠판 터치가 왼쪽 아래에서 안 먹어요', place: '4-2 교실', by: '정하은', at: '10/26(월) 08:31', status: '접수', urgent: true, note: '수업공개(3교시) 전에 가능하면 부탁드려요.' },
  { id: 'r2', cat: '시설', title: '복도 형광등 깜빡임 (3층 남쪽 끝)', place: '3층 복도', by: '신유진', at: '10/26(월) 08:05', status: '처리 중', note: '' },
  { id: 'r3', cat: '정보', title: '교사용 PC 프린터 연결 끊김 (공유 프린터)', place: '교무실', by: '윤채린', at: '10/23(금) 15:40', status: '처리 중', note: '업체 방문 예정 10/27(화) 오전', reply: '내일 오전 방문해 드라이버 재설치하겠습니다.' },
  { id: 'r4', cat: '시설', title: '과학실1 수도꼭지 누수', place: '과학실1', by: '송민재', at: '10/22(목) 11:20', status: '완료', note: '', reply: '패킹 교체했습니다.' },
  { id: 'r5', cat: '정보', title: '컴퓨터실 12번 PC 부팅 안 됨', place: '컴퓨터실', by: '차민호', at: '10/21(수) 13:15', status: '완료', note: '', reply: 'SSD 교체 완료.' },
  { id: 'r6', cat: '시설', title: '2-3 교실 창문 잠금장치 고장', place: '2-3 교실', by: '오승민', at: '10/20(화) 09:02', status: '완료', note: '', reply: '잠금장치 교체 완료.' }
];
