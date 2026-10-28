// 급식소 현황판 공용 모듈 — NEIS 급식식단정보 연동 + 영양사 입력값.
// 학교 식별자 = '교육청코드-학교코드'. 기본은 김해봉황초등학교(경상남도교육청 S10 / 9091016).
//
// [NEIS에서 자동] 메뉴(DDISH_NM) · 알레르기 번호(메뉴 뒤 괄호) · 칼로리(CAL_INFO) · 영양 9종(NTR_INFO)
//                 · 원산지(ORPLC_INFO, 학교 공통 표시 목록) · 급식 인원(MLSV_FGR) · 학교 이름(schoolInfo)
// [날짜로 자동 순환] 위생 교육 주제 · 영양 교육 표어 (같은 날엔 모든 TV가 같은 내용)
// [영양사 매일 입력] 오늘의 식사 사진 — 당일 찍은 사진을 올리는 것이 규정
// [영양사 선택 입력] 화면 순서 · 화면별 노출 시간 · 화면 켜고 끄기 · 위생 주제 고정
// [표준값 내장] 영양 기준 = 학교급식법 시행규칙 별표3(2021.1.29 개정) 초등 1~6학년 남녀 평균
//
// NEIS 오픈API는 CORS를 허용해 브라우저에서 바로 부른다. 인증키 없이는 한 번에 5건까지라
// 한 주(월~금) 점심만 부른다. 키를 넣으면(설정 또는 ?key=) 제한이 풀린다.
// 프로토타입은 영양사 입력값을 이 브라우저(localStorage)에 저장한다 — 실제 서비스는 티로드 서버에 저장.
window.TR = window.TR || {};

(function () {
  var M = TR.meal = {};
  M.DEFAULT = 'S10-9091016';
  M.API = 'https://open.neis.go.kr/hub/';
  M.WD = ['일', '월', '화', '수', '목', '금', '토'];

  // NEIS 알레르기 표기 번호 1~19
  M.allergens = ['난류', '우유', '메밀', '땅콩', '대두', '밀', '고등어', '게', '새우', '돼지고기', '복숭아', '토마토', '아황산류', '호두', '닭고기', '쇠고기', '오징어', '조개류', '잣'];

  // 식품구성자전거 여섯 무리
  M.groups = [
    { id: 'grain', name: '곡류', ex: '밥 · 면 · 떡 · 빵 · 감자', does: '힘을 내요', color: '#C98A00', soft: '#FFF4D6' },
    { id: 'protein', name: '고기 · 생선 · 달걀 · 콩류', ex: '고기 · 생선 · 달걀 · 두부', does: '몸을 만들어요', color: '#C2410C', soft: '#FDE8DC' },
    { id: 'veg', name: '채소류', ex: '나물 · 김치 · 해조류 · 버섯', does: '몸을 튼튼하게', color: '#2F9E44', soft: '#E5F6E8' },
    { id: 'fruit', name: '과일류', ex: '귤 · 사과 · 바나나', does: '비타민 가득', color: '#D6336C', soft: '#FCE6EE' },
    { id: 'milk', name: '우유 · 유제품류', ex: '우유 · 요구르트 · 치즈', does: '뼈와 이를 튼튼하게', color: '#3B5BDB', soft: '#E8EDFD' },
    { id: 'fat', name: '유지 · 당류', ex: '튀김 · 볶음 · 전 · 잡채', does: '조금만 먹어요', color: '#6B7280', soft: '#EEF0F3' }
  ];
  // 메뉴 이름으로 식품군을 어림한다. 영양사가 관리 화면에서 메뉴별로 고칠 수 있다.
  M.FRUITS = ['귤', '감귤', '한라봉', '사과', '배', '바나나', '포도', '청포도', '샤인머스켓', '샤인머스캣', '방울토마토', '토마토', '수박', '참외', '딸기', '키위', '골드키위', '오렌지', '자몽', '파인애플', '복숭아', '천도복숭아', '자두', '망고', '멜론', '감', '단감', '홍시', '블루베리', '체리', '과일', '주스'];
  M.RULES = [
    ['grain', /밥|떡(?!갈비)|면|국수|빵|죽|시리얼|파스타|스파게티|피자|만두|감자|고구마|옥수수|누룽지|또띠아|토스트|버거/],
    ['protein', /고기|돈육|돼지|불고기|갈비|닭|오리|맥적|돈가스|까스|생선|고등어|참치|명태|동태|조기|갈치|오징어|주꾸미|쭈꾸미|낙지|새우|해물|어묵|달걀|계란|메추리알|두부|콩(?!나물)|햄|소시지|미트|추어|장어|연어|삼치|꽁치|코다리|가자미|멸치|북어|순대|치킨/],
    ['veg', /김치|깍두기|겉절이|나물|무침|샐러드|채소|야채|시금치|미역|김자반|김구이|파래|톳|다시마|버섯|연근|우엉|오이|배추|부추|고추|시락|시래기|우거지|무생채|단무지|양파|호박|브로콜리|숙주|잡채|피클|쌈|상추|깻잎|당근|가지/],
    ['milk', /우유|요구르트|요거트|치즈|요플레|라떼/],
    ['fat', /튀김|볶음|전$|까스|잡채|강정|맛탕|버터|마요|도넛|탕수|슈$|케이크|쿠키|아이스|초코|머핀|와플|젤리/]
  ];

  M.hygiene = {
    wash: { name: '손 씻기', title: '손을 깨끗이 씻어요', tag: '흐르는 물에 비누로 30초', items: [
      ['손바닥', '손바닥과 손바닥을 마주 대고 문질러요'], ['손등', '손등과 손바닥을 마주 대고 문질러요'],
      ['손가락 사이', '손바닥을 마주 대고 손깍지를 끼고 문질러요'], ['두 손 모아', '손가락을 마주 잡고 문질러요'],
      ['엄지손가락', '엄지손가락을 다른 손바닥으로 돌려 주며 문질러요'], ['손톱 밑', '손가락을 반대편 손바닥에 놓고 문질러 손톱 밑까지 닦아요']] },
    cough: { name: '기침 예절', title: '기침할 때는 이렇게 해요', tag: '나도 친구도 건강하게', items: [
      ['휴지로 가려요', '기침이 나오면 휴지나 손수건으로 입과 코를 가려요'], ['옷소매로 가려요', '휴지가 없으면 옷소매 위쪽으로 가려요'],
      ['휴지는 쓰레기통에', '쓴 휴지는 바로 쓰레기통에 버려요'], ['손을 씻어요', '기침한 뒤에는 비누로 손을 씻어요'],
      ['마스크를 써요', '기침이 계속 나면 마스크를 써요'], ['얼굴을 피해요', '친구 얼굴 쪽으로 기침하지 않아요']] },
    food: { name: '식중독 예방', title: '식중독을 막아요', tag: '6가지 약속', items: [
      ['손 씻기', '먹기 전, 화장실 다녀온 뒤 비누로 손을 씻어요'], ['익혀 먹기', '고기와 달걀은 속까지 익혀 먹어요'],
      ['끓여 먹기', '물은 끓이거나 정수된 물을 마셔요'], ['깨끗이 씻기', '과일과 채소는 깨끗한 물에 씻어 먹어요'],
      ['나눠 쓰기', '날음식과 익힌 음식은 도구를 나눠 써요'], ['차갑게 보관', '남은 음식은 냉장고에 넣어요']] },
    teeth: { name: '이 닦기', title: '밥 먹고 이를 닦아요', tag: '3 · 3 · 3 약속', items: [
      ['하루 3번', '아침 · 점심 · 저녁 먹고 닦아요'], ['3분 안에', '밥 먹고 3분 안에 닦아요'],
      ['3분 동안', '구석구석 3분 동안 닦아요'], ['위아래로', '칫솔을 위아래로 쓸어내리듯 닦아요'],
      ['안쪽까지', '어금니 안쪽과 혀도 닦아요'], ['칫솔 말리기', '칫솔은 깨끗이 헹궈 말려 두어요']] },
    safe: { name: '급식실 안전', title: '급식실에서는 이렇게 해요', tag: '안전한 점심시간', items: [
      ['차례차례', '줄을 서서 차례를 지켜요'], ['걸어요', '급식실에서는 뛰지 않아요'],
      ['두 손으로', '식판은 두 손으로 꼭 잡아요'], ['뜨거운 국 조심', '국은 천천히 받아 조심히 옮겨요'],
      ['조용조용', '작은 목소리로 이야기해요'], ['깨끗이 정리', '다 먹은 식판은 정해진 곳에 정리해요']] }
  };
  M.SLOGANS = [
    '편식하지 않고 · 꼭꼭 씹어 · 남기지 않아요', '골고루 먹으면 키도 쑥쑥 · 힘도 쑥쑥', '채소 한 입 더 · 건강 한 뼘 더',
    '먹을 만큼 받고 · 받은 만큼 먹어요', '천천히 꼭꼭 씹으면 소화가 잘 돼요', '우유 한 컵으로 뼈를 튼튼하게',
    '음식을 만들어 주신 분들께 감사해요'
  ];
  // 날짜 → 날마다 1씩 늘어나는 수. 주제·표어를 날마다 돌려 영양사가 고를 일을 없앤다.
  M.dayNo = function (ymd) { return Math.round(M.fromYmd(ymd) / 864e5); };
  M.hygieneOf = function (ymd, set) {
    var keys = Object.keys(M.hygiene);
    return set && M.hygiene[set.hygiene] ? set.hygiene : keys[M.dayNo(ymd) % keys.length];
  };
  M.sloganOf = function (ymd, set) { return (set && set.slogan) || M.SLOGANS[M.dayNo(ymd) % M.SLOGANS.length]; };
  // 한 끼 영양 기준(권장섭취량) — 별표3 초등 1~3·4~6학년 남·여 네 줄의 평균. 이름은 NEIS NTR_INFO 표기에 맞춤.
  //   에너지 kcal · 단백질 g · 비타민A ㎍RAE · 티아민 · 리보플라빈 · 비타민C · 칼슘 · 철 (mg)
  //   남 1~3  570 · 11.7 · 150 · 0.24 · 0.30 · 16.7 · 234 · 3.0    남 4~6  670 · 16.7 · 200 · 0.30 · 0.37 · 23.4 · 267 · 3.7
  //   여 1~3  500 · 11.7 · 134 · 0.24 · 0.27 · 16.7 · 234 · 3.0    여 4~6  600 · 15.0 · 184 · 0.30 · 0.34 · 23.4 · 267 · 3.4
  M.BASE = { '에너지': 585, '단백질': 13.8, '비타민A': 167, '티아민': 0.27, '리보플라빈': 0.32, '비타민C': 20.1, '칼슘': 251, '철분': 3.3 };
  M.BASE_NAME = '초등학생 한 끼 기준';

  // 현황판 화면 목록. 순서 · 켜고 끄기 · 노출 시간(초)은 영양사가 바꿀 수 있다.
  M.SCREENS = [
    { key: 'menu', name: '오늘의 메뉴', what: '메뉴 · 자율배식 표시 · 알레르기 번호 · 급식 인원', src: 'auto' },
    { key: 'allergy', name: '알레르기', what: '19종 가운데 그날 들어간 식품과 해당 메뉴', src: 'auto' },
    { key: 'cal', name: '칼로리 · 영양', what: '칼로리 · 영양 9종 · 초등 1~6학년 표준 기준과 비교', src: 'auto' },
    { key: 'origin', name: '원산지', what: '학교 공통 원산지 표시 (학교가 NEIS에 올린 목록 그대로)', src: 'auto' },
    { key: 'hygiene', name: '위생 교육', what: '손 씻기 · 기침 예절 · 식중독 예방 · 이 닦기 · 급식실 안전이 날마다 돌아가며', src: 'rot' },
    { key: 'eat', name: '골고루 먹어요', what: '식품군(메뉴 이름으로 자동 분류) · 표어(날마다 돌아가며)', src: 'rot' },
    { key: 'photo', name: '오늘의 식사', what: '그날 식판 사진 (당일 찍은 사진)', src: 'me', lock: true }, // 규정상 매일 게시 — 끌 수 없음
    { key: 'week', name: '이번 주 메뉴', what: '월~금 메뉴 · 알레르기 번호 · 칼로리', src: 'auto' }
  ];
  M.SEC = 7; M.SEC_MIN = 3; M.SEC_MAX = 60;
  // 저장된 순서에 없는 화면은 뒤에 붙인다(화면이 늘어나도 깨지지 않게)
  M.screens = function (set) {
    var keys = M.SCREENS.map(function (x) { return x.key; }), order = (set.order || []).filter(function (k) { return keys.indexOf(k) >= 0; });
    keys.forEach(function (k) { if (order.indexOf(k) < 0) order.push(k); });
    return order.map(function (k) {
      var sc = M.SCREENS[keys.indexOf(k)], sec = +(set.sec || {})[k] || M.SEC;
      return { key: k, name: sc.name, what: sc.what, src: sc.src, lock: !!sc.lock, on: sc.lock || !(set.off || {})[k], sec: Math.min(M.SEC_MAX, Math.max(M.SEC_MIN, sec)) };
    });
  };

  // ---------- 날짜 ----------
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  M.ymd = function (d) { return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()); };
  M.fromYmd = function (s) { return new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8)); };
  M.label = function (s) { var d = M.fromYmd(s); return { m: d.getMonth() + 1, d: d.getDate(), w: M.WD[d.getDay()] }; };
  // 주말이면 다음 주 월요일
  M.monday = function (d) { var x = new Date(d.getFullYear(), d.getMonth(), d.getDate()), w = x.getDay(); x.setDate(x.getDate() + (w === 0 ? 1 : w === 6 ? 2 : 1 - w)); return x; };
  M.weekDays = function (mon) { var r = []; for (var i = 0; i < 5; i++) { var x = new Date(mon); x.setDate(mon.getDate() + i); r.push(M.ymd(x)); } return r; };

  // ---------- NEIS 응답 풀기 ----------
  function lines(s) { return String(s || '').split(/<br\s*\/?>/i).map(function (x) { return x.replace(/&amp;/g, '&').trim(); }).filter(Boolean); }
  M.parseDish = function (raw) {
    var s = raw, a = [], m = s.match(/\(([\d.\s]+)\)\s*$/);
    if (m) { a = m[1].split('.').map(function (x) { return +x; }).filter(function (x) { return x >= 1 && x <= 19; }); s = s.slice(0, m.index).trim(); }
    return { n: s.replace(/\([^)]*\)/g, '').replace(/\*/g, '').trim(), a: a, self: /\(자율\)/.test(s), raw: raw };
  };
  M.parseRow = function (r) {
    return {
      ymd: r.MLSV_YMD,
      dishes: lines(r.DDISH_NM).map(M.parseDish),
      kcal: Math.round(parseFloat(r.CAL_INFO) || 0),
      count: r.MLSV_FGR ? Math.round(r.MLSV_FGR) : null,
      nutr: lines(r.NTR_INFO).map(function (x) { var m = x.match(/^(.+?)\((.+?)\)\s*:\s*([\d.]+)/); return m && { n: m[1].trim(), u: m[2], v: +m[3] }; }).filter(Boolean),
      origin: lines(r.ORPLC_INFO).map(function (x) { var p = x.split(/\s*:\s*/); return [p[0].replace(/\(종류\)/, '').trim(), (p[1] || '').trim()]; }).filter(function (o) { return o[1] && o[0] !== '비고'; })
    };
  };

  function get(path, params) {
    var u = new URL(M.API + path); u.searchParams.set('Type', 'json');
    Object.keys(params).forEach(function (k) { if (params[k]) u.searchParams.set(k, params[k]); });
    return fetch(u).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function (j) {
      var body = j[path];
      if (!body) { var res = j.RESULT || {}; if (res.CODE === 'INFO-200') return []; throw new Error(res.MESSAGE || 'NEIS 응답 오류'); }
      return body[1].row || [];
    });
  }
  M.split = function (s) { var p = String(s || M.DEFAULT).split('-'); return { atpt: p[0], code: p[1] }; };

  // 한 주(월~금) 점심 + 학교 이름. 실패하면 같은 주 저장본으로 대신한다.
  M.load = function (s, key, mon) {
    var id = M.split(s), days = M.weekDays(mon), ck = 'troad.meal.cache.' + s;
    var meals = get('mealServiceDietInfo', { KEY: key, pSize: 10, ATPT_OFCDC_SC_CODE: id.atpt, SD_SCHUL_CODE: id.code, MMEAL_SC_CODE: '2', MLSV_FROM_YMD: days[0], MLSV_TO_YMD: days[4] });
    var info = get('schoolInfo', { KEY: key, ATPT_OFCDC_SC_CODE: id.atpt, SD_SCHUL_CODE: id.code });
    return Promise.all([meals, info]).then(function (r) {
      var out = { s: s, mon: days[0], days: days, at: Date.now(), name: r[1][0] ? r[1][0].SCHUL_NM : '', meals: {} };
      r[0].forEach(function (row) { out.meals[row.MLSV_YMD] = M.parseRow(row); });
      M.store(ck, out);
      return out;
    }).catch(function (e) {
      var c = M.read(ck);
      if (c && c.mon === days[0]) { c.stale = String(e.message || e); return c; }
      throw e;
    });
  };

  // ---------- 저장 (프로토타입: 이 브라우저) ----------
  M.read = function (k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } };
  M.store = function (k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } };
  M.settings = function (s) { var v = M.read('troad.meal.set.' + s) || {}; v.groups = v.groups || {}; v.sec = v.sec || {}; v.off = v.off || {}; return v; };
  M.saveSettings = function (s, v) { return M.store('troad.meal.set.' + s, v); };
  M.photo = function (s, ymd) { return M.read('troad.meal.photo.' + s + '.' + ymd); };
  M.savePhoto = function (s, ymd, data) {
    M.store('troad.meal.photoat.' + s + '.' + ymd, data ? Date.now() : null);
    return M.store('troad.meal.photo.' + s + '.' + ymd, data);
  };
  M.photoAt = function (s, ymd) { return M.read('troad.meal.photoat.' + s + '.' + ymd); };

  // 식품군: 영양사가 고친 값이 있으면 그것, 없으면 이름으로 어림
  M.autoGroups = function (name) {
    var g = [];
    if (M.FRUITS.some(function (f) { return name === f || (name.length <= f.length + 2 && name.slice(-f.length) === f && f.length > 1); })) g.push('fruit');
    M.RULES.forEach(function (r) { if (r[1].test(name) && g.indexOf(r[0]) < 0) g.push(r[0]); });
    return g;
  };
  M.groupsOf = function (name, set) { return (set && set.groups[name]) || M.autoGroups(name); };
})();
