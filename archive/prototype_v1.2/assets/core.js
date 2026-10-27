// 공통: 상태 저장(localStorage, 탭/iframe 간 동기화) · 한 줄 입력 파서 · 유틸
(function () {
  var KEY = 'troad.proto.v3';

  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function seed() {
    return { events: clone(TR.seedEvents), collections: clone(TR.seedCollections), notices: clone(TR.seedNotices), members: clone(TR.seedMembers), repairs: clone(TR.seedRepairs), done: {}, acked: {}, log: [] };
  }
  TR.load = function () {
    try { var s = localStorage.getItem(KEY); if (s) return JSON.parse(s); } catch (e) {}
    return seed();
  };
  TR.state = TR.load();
  TR.save = function () {
    try { localStorage.setItem(KEY, JSON.stringify(TR.state)); } catch (e) {}
  };
  TR.reset = function () {
    try { localStorage.removeItem(KEY); localStorage.removeItem('troad.me'); } catch (e) {}
    TR.state = seed();
  };
  // 그룹 조회: 이름 → 구성원 / 그룹명 ("4학년", "교무실")
  TR.member = function (name) { return (TR.state.members || TR.seedMembers).find(function (m) { return m.name === name; }); };
  TR.groupName = function (gid) { var g = TR.groups.find(function (x) { return x.id === gid; }); return g ? g.name : ''; };
  TR.groupOf = function (name) { var m = TR.member(name); return m ? TR.groupName(m.group) : ''; };
  TR.byline = function (name) { var g = TR.groupOf(name); return name + (g ? ' · ' + g : ''); };

  TR.onSync = function (fn) {
    window.addEventListener('storage', function (ev) { if (ev.key === KEY) { TR.state = TR.load(); fn(); } });
  };

  TR.esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  TR.$ = function (sel, root) { return (root || document).querySelector(sel); };
  TR.$$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  TR.toast = function (msg) {
    var t = document.getElementById('toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = msg; t.classList.add('show');
    clearTimeout(TR._tt); TR._tt = setTimeout(function () { t.classList.remove('show'); }, 2600);
  };
  TR.copy = function (text, msg) {
    var done = function () { TR.toast(msg || '복사했어요. 메신저에 붙여넣으세요.'); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback); else fallback();
    function fallback() { var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); } catch (e) {} ta.remove(); done(); }
  };

  // ---------- 날짜 ----------
  var DOW = ['일', '월', '화', '수', '목', '금', '토'];
  TR.dow = DOW;
  TR.d = function (iso) { var p = iso.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); };
  TR.iso = function (d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
  TR.addDays = function (iso, n) { var d = TR.d(iso); d.setDate(d.getDate() + n); return TR.iso(d); };
  TR.fmt = function (iso) { var d = TR.d(iso); return (d.getMonth() + 1) + '/' + d.getDate() + '(' + DOW[d.getDay()] + ')'; };

  // ---------- 한 줄 입력 파서 ----------
  // "내일 3~4교시 4학년 과학실 안전교육 @김민준" → {date, s, e, target, place, who, type, title}
  TR.parse = function (raw) {
    // 교사들은 제목에 내용을 길게 넣어 달력에서 바로 읽는 걸 선호 → 제목은 원문 그대로 두고,
    // 칩(교시·장소·대상)은 검색·충돌 경고용으로만 뽑는다. 날짜 표현과 @참석자만 제목에서 뺀다.
    var text = ' ' + raw + ' ', keep = ' ' + raw + ' ', out = { raw: raw, tokens: [] };
    function take(re, fn, isDate) { var m = text.match(re); if (m) { fn(m); text = text.replace(m[0], ' '); if (isDate) keep = keep.replace(m[0], ' '); } }
    var today = TR.TODAY;

    take(/\s(오늘|금일)\s/, function () { out.date = today; }, 1);
    take(/\s(내일|명일)\s/, function () { out.date = TR.addDays(today, 1); }, 1);
    take(/\s모레\s/, function () { out.date = TR.addDays(today, 2); }, 1);
    take(/\s(다음\s?주|담주)\s?([월화수목금])(요일)?\s/, function (m) {
      var target = '일월화수목금토'.indexOf(m[2]); var d = TR.d(today); var add = (7 - d.getDay()) + target; out.date = TR.addDays(today, add);
    }, 1);
    if (!out.date) take(/\s(이번\s?주\s?)?([월화수목금])요일\s/, function (m) {
      var target = '일월화수목금토'.indexOf(m[2]); var d = TR.d(today); var add = (target - d.getDay() + 7) % 7; out.date = TR.addDays(today, add);
    }, 1);
    if (!out.date) take(/\s(\d{1,2})\s?[\/.월]\s?(\d{1,2})일?\.?\s/, function (m) { out.date = '2026-' + String(+m[1]).padStart(2, '0') + '-' + String(+m[2]).padStart(2, '0'); }, 1);

    take(/\s(\d)\s?[~\-–]\s?(\d)\s?교시\s/, function (m) { out.s = m[1]; out.e = m[2]; });
    if (!out.s) take(/\s(\d)\s?교시\s/, function (m) { out.s = out.e = m[1]; });
    if (!out.s) take(/\s(중간놀이|점심|아침|방과\s?후)\s/, function (m) { var k = { '중간놀이': 'mid', '점심': 'ln', '아침': 'am' }[m[1]] || 'pm'; out.s = out.e = k; });
    take(/\s(오전|오후)?\s?(\d{1,2})(?::(\d{2})|시(?:\s?(\d{1,2})분)?|시\s?반)\s/, function (m) {
      var h = +m[2]; if (m[1] === '오후' && h < 12) h += 12; if (!m[1] && h < 8) h += 12;
      var mm = m[3] || m[4] || (/반/.test(m[0]) ? '30' : '00');
      out.time = String(h).padStart(2, '0') + ':' + String(mm).padStart(2, '0');
      if (!out.s) out.s = out.e = h >= 15 || (h === 14 && +mm >= 40) ? 'pm' : h < 9 ? 'am' : null;
      if (out.s === null) { delete out.s; delete out.e; }
    });

    take(/\s(\d)\s?-\s?(\d)\s?(반)?\s/, function (m) { out.target = m[1] + '-' + m[2]; });
    if (!out.target) take(/\s(\d)\s?학년\s?(\d)\s?반\s/, function (m) { out.target = m[1] + '-' + m[2]; });
    if (!out.target) take(/\s(\d(?:\s?[·,]\s?\d)*)\s?학년(부)?\s/, function (m) { out.target = m[1].replace(/\s/g, '') + '학년' + (m[2] || ''); });
    if (!out.target) take(/\s(전교생?|전\s?교직원|교직원|학년부장|담임|전\s?교원)\s/, function (m) { out.target = m[1].replace(/\s/g, ''); });

    var rooms = TR.rooms.slice().sort(function (a, b) { return b.length - a.length; });
    for (var i = 0; i < rooms.length; i++) { var idx = text.indexOf(rooms[i]); if (idx >= 0) { out.place = rooms[i]; text = text.replace(rooms[i], ' '); break; } }
    if (!out.place) take(/\s(\d-\d)\s?교실\s/, function (m) { out.place = m[1] + ' 교실'; });

    var who = []; text = text.replace(/@([가-힣]{2,4})/g, function (_, n) { who.push(n); return ' '; }); if (who.length) out.who = who;

    var DUTY = ['출장', '조퇴', '외출', '연가', '병가', '공가'];
    for (var j = 0; j < DUTY.length; j++) if (raw.indexOf(DUTY[j]) >= 0) { out.type = 'duty'; out.duty = DUTY[j]; break; }
    if (!out.type) out.type = /회의|협의|간담|위원회/.test(raw) ? 'meet' : /마감|제출|조사|취합/.test(raw) ? 'work' : /교육|연수|수업|점검/.test(raw) ? 'work' : 'event';
    if (out.type === 'duty' && !out.who) { var nm = raw.match(/([가-힣]{2,4})\s?(선생님)?\s?(출장|조퇴|외출|연가|병가|공가)/); if (nm && TR.staff.some(function (s) { return s.name === nm[1]; })) { out.who = [nm[1]]; text = text.replace(nm[1], ' '); } }

    out.title = keep.replace(/@[가-힣]{2,4}/g, ' ').replace(/\s+/g, ' ').trim();
    if (out.type === 'duty') out.title = text.replace(/\s+/g, ' ').replace(/선생님/g, '').trim() || (out.who ? out.who[0] + ' ' : '') + out.duty;
    if (out.type === 'duty' && out.who && out.title.indexOf(out.who[0]) < 0) out.title = out.who[0] + ' ' + out.title;
    return out;
  };

  TR.typeLabel = { event: '행사', work: '업무·교육', meet: '회의', duty: '복무' };
  TR.typeSym = { event: '◆', work: '■', meet: '▲', duty: '○' };

  TR.slotText = function (ev) {
    if (!ev.s) return ev.time || '시간 미정';
    if (TR.isAllDay(ev)) return '종일';
    if (ev.time && (ev.s === 'pm' || ev.s === 'am')) return ev.time;
    var a = TR.slots[TR.slotIdx(ev.s)], b = TR.slots[TR.slotIdx(ev.e || ev.s)];
    if (!a) return ev.time || '';
    if (a === b) return a.label + (ev.time ? ' ' + ev.time : '');
    return /교시/.test(a.label) && /교시/.test(b.label) ? a.label.replace('교시', '') + '~' + b.label : a.label + '~' + b.label;
  };

  // 시간 정밀도: 날짜만(전일) → 시간대(블록) → 교시. 날짜만 넣어도 되고, 필요한 사람이 끌어서 정한다.
  TR.isAllDay = function (ev) { return !ev.s || (TR.slotIdx(ev.s) <= 1 && TR.slotIdx(ev.e || ev.s) >= 8); };
  TR.bandIdx = function (ev) { var i = TR.slotIdx(ev.s); return TR.bands.findIndex(function (b) { return i >= b.from && i <= b.to; }); };
  // 나와 관련: 내가 쓴 것, 대상이 내 반·내 학년·담임·전 교직원
  TR.isRel = function (ev) {
    if (ev.mine || ev.by === TR.me.name || ev.who === TR.me.name) return true;
    var g = TR.groupOf(TR.me.name), t = (ev.target || '') + ' ' + ev.title;
    return t.indexOf(TR.me.cls) >= 0 || t.indexOf(g) >= 0 || t.indexOf(g.replace('학년', '')) >= 0 && /·\d학년|\d·\d/.test(t) || /전\s?교직원|전\s?교원|^담임$|전교/.test(ev.target || '');
  };
  // 시간을 옮길 수 있는 사람: 쓴 사람 + 쓴 사람과 같은 그룹
  TR.canEdit = function (ev) { return ev.type !== 'duty' && (ev.by === TR.me.name || TR.groupOf(ev.by) === TR.groupOf(TR.me.name)); };
  TR.chgIcon = '<svg class="chgi" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-label="최근 변경" role="img"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>';

  // 장소 충돌
  TR.conflicts = function (ev) {
    if (!ev.place || !ev.date || !ev.s) return [];
    var a = TR.slotIdx(ev.s), b = TR.slotIdx(ev.e || ev.s);
    return TR.state.events.filter(function (x) {
      if (x.id === ev.id || x.date !== ev.date || x.place !== ev.place) return false;
      var xa = TR.slotIdx(x.s), xb = TR.slotIdx(x.e || x.s);
      return xa <= b && a <= xb;
    });
  };
})();
