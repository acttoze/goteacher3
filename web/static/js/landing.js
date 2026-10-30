// 고티처 소개 페이지(G5) — design/landing.html 스크립트 이식. 화면 안 시연(가상 인물·하루)만 하고 서버와 주고받지 않는다.
// 샘플 데이터는 design/assets/data.js·people.js에서 이 페이지가 쓰는 것만 옮겼다.
window.GT = {
  date: { iso: '2026-10-26', label: '10월 26일 월요일', short: '10/26(월)', week: '2학기 9주차' },
  nowSlot: 'b2',
  slots: [
    { k: 'b1', label: '1블록', from: '08:40', to: '10:30' },
    { k: 'b2', label: '2블록', from: '10:30', to: '12:20' },
    { k: 'b3', label: '3블록', from: '12:20', to: '14:40' },
    { k: 'pm', label: '방과후', from: '14:40', to: '' }
  ],
  timetable: [
    { k: 'b1', sub: '국어 · 수학', room: '4-2' },
    { k: 'b2', sub: '과학 수업공개 · 사회', room: '4-2', note: '참관: 교감 · 4학년부' },
    { k: 'b3', sub: '체육 · 창체', room: '체육관 → 4-2', note: '체육은 노태윤 선생님(전담)' }
  ],
  events: [
    { id: 'e1', s: 'b1', title: '독서주간 아침 10분 독서', place: '각 교실', who: '전교' },
    { id: 'e2', s: 'b2', title: '4-2 수업공개', place: '4-2 교실', who: '참관: 교감 · 4학년부', mine: true },
    { id: 'e3', s: 'b3', title: '3학년 수영교육 출발 13:10 정문', place: '정문 버스', who: '3학년', changed: { what: '출발', from: '13:00', to: '13:10' } },
    { id: 'e4', s: 'pm', title: '학년부장 협의회 15:30', place: '교무실', who: '학년부장' },
    { id: 'e5', title: '4학년 학년협의 — 학예회 순서 정하기', place: '4-1 교실', who: '4학년부', changed: { what: '장소', from: '4-3 교실', to: '4-1 교실' } },
    { id: 'e6', title: '학예회 소품 준비물 확인', place: '', who: '4학년', mine: true }
  ],
  changes: [
    { title: '4학년 학년협의', what: '장소', from: '4-3 교실', to: '4-1 교실', by: '송민재', when: '어제 17:42' },
    { title: '3학년 수영교육', what: '출발', from: '13:00', to: '13:10', by: '김도윤', when: '어제 18:05' },
    { title: '학예회 리허설(10/29)', what: '장소 · 시간', from: '강당 · 2블록', to: '체육관 · 3블록', by: '송민재', when: '어제 19:10' }
  ],
  todo: [
    { id: 't1', title: '방과후 수요조사 (반별 인원)', from: '석가영 · 방과후돌봄부', due: '오늘 14:00', dueIso: '2026-10-26', urgent: true, kind: '제출', form: '한글 양식', fields: 3 },
    { id: 't3', title: '학예회 의상 대여 수량', from: '송민재 · 4학년 대표', due: '10/28(수)', dueIso: '2026-10-28', kind: '제출', form: '한글 양식', fields: 2 },
    { id: 't2', title: '2학기 교육과정 평가 설문', from: '오지현 · 교무부', due: '10/30(금)', dueIso: '2026-10-30', kind: '제출', form: '웹 표', fields: 6 },
    { id: 't4', title: '개인정보 보호 연수 이수 확인', from: '차민호 · 정보부', due: '11/6(금)', dueIso: '2026-11-06', kind: '확인', form: '', fields: 1 }
  ]
};
window.PEOPLE = (function () {
  var map = {
    '송민재': { color: '#c2410c' },               // 주황
    '정하은': { color: '#0b7d7f' },               // 청록
    '김도윤': { color: '#d6246e' },               // 분홍
    '이수빈': { color: '#37b24d', inkText: true }, // 초록
    '박서준': { color: '#f2a300', inkText: true }  // 호박
  };
  return {
    grade: ['송민재', '정하은', '김도윤', '이수빈', '박서준'],
    of: function (name) { return map[name] || { color: '#6b7280' }; }
  };
})();

(function () {
  var G = window.GT, P = window.PEOPLE;
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function toast(t) { var el = $('#toast'); el.textContent = t; el.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(function () { el.classList.remove('show'); }, 2600); }
  function av(name, cls) { var p = P.of(name); return '<span class="av' + (p.inkText ? ' ink-t' : '') + (cls ? ' ' + cls : '') + '" style="background:' + p.color + '" title="' + name + '">' + name.charAt(0) + '</span>'; }

  // 슬롯: 동학년 다섯 명
  $('#slot').innerHTML = P.grade.map(function (n) { var p = P.of(n); return '<i style="background:' + p.color + (p.inkText ? ';color:#0d0d0d' : '') + '"><span>' + n.charAt(0) + '</span></i>'; }).join('');
  $('#howWho').innerHTML = P.grade.map(function (n) { return av(n); }).join('');

  // ---------- 라이브 표: 상태 하나에서 모두 그린다 ----------
  var rows = [
    { cls: '4-1', who: '송민재', a: 24, b: 1 },
    { cls: '4-2', who: '정하은', a: 23, b: 2 },
    { cls: '4-3', who: '김도윤', a: 25, b: 0 },
    { cls: '4-4', who: '이수빈', a: null, b: null },
    { cls: '4-5', who: '박서준', a: null, b: null }
  ];
  var state = rows.map(function (r) { return { cls: r.cls, who: r.who, a: null, b: null, by: null }; });
  var tbody = $('#live tbody');
  tbody.innerHTML = state.map(function (r, i) {
    return '<tr data-i="' + i + '"><td>' + r.cls + '</td><td>' + r.who + '</td>' +
      '<td class="cell" data-k="a"><input inputmode="numeric" aria-label="' + r.cls + ' 참가 인원" placeholder="채우기"></td>' +
      '<td class="cell" data-k="b"><input inputmode="numeric" aria-label="' + r.cls + ' 불참 인원" placeholder="—"></td>' +
      '<td class="r"><span class="st">미제출</span></td></tr>';
  }).join('');
  function paint() {
    var done = 0, sa = 0, sb = 0;
    state.forEach(function (r, i) {
      var tr = tbody.children[i], ok = r.a !== null;
      if (ok) { done++; sa += r.a; sb += (r.b || 0); }
      var st = $('.st', tr); st.className = 'st' + (ok ? ' done' : ''); st.textContent = ok ? (r.by && r.by !== r.who ? '대신 냄' : '제출') : '미제출';
      $$('input', tr).forEach(function (inp) { var k = inp.parentNode.dataset.k; if (document.activeElement !== inp) inp.value = r[k] === null ? '' : r[k]; });
    });
    $('#sumA').textContent = sa; $('#sumB').textContent = sb;
    $('#progBar').style.width = (done / state.length * 100) + '%';
    $('#progN').textContent = done + ' / ' + state.length + '반';
    console.assert(sa === state.reduce(function (s, r) { return s + (r.a || 0); }, 0), '합계 불일치');
    return done;
  }
  function commit(i, k, v, by) { state[i][k] = v; if (k === 'a') state[i].by = by || state[i].by; if (k === 'a' && state[i].b === null) state[i].b = 0; var tr = tbody.children[i]; tr.classList.remove('just'); void tr.offsetWidth; tr.classList.add('just'); paint(); }
  paint();

  // 방문자가 빈 칸을 채운다
  $$('input', tbody).forEach(function (inp) {
    inp.addEventListener('change', function () {
      var i = +inp.closest('tr').dataset.i, k = inp.parentNode.dataset.k, v = inp.value.replace(/[^0-9]/g, '');
      if (v === '') { if (k === 'a') { state[i].a = null; state[i].by = null; } else state[i].b = null; paint(); return; }
      commit(i, k, Math.min(99, +v), '정하은');
      if (k === 'a' && state.every(function (r) { return r.a !== null; })) toast('다섯 반이 다 냈어요. 한글 양식으로 받을 수 있어요.');
    });
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') inp.blur(); });
  });

  // 커서: 송민재·정하은·김도윤이 동시에 채우고, 이수빈은 보는 중
  var sheet = $('#sheet');
  var actors = [
    { name: '송민재', row: 0 }, { name: '정하은', row: 1 }, { name: '김도윤', row: 2 }, { name: '이수빈', row: 3, idle: true }
  ];
  $('#liveStack').innerHTML = actors.map(function (a) { return av(a.name); }).join('') + av('박서준', 'off');
  function cell(i, k) { return $('tr[data-i="' + i + '"] td[data-k="' + k + '"]', tbody); }
  function at(el, dx, dy) { var s = sheet.getBoundingClientRect(), r = el.getBoundingClientRect(); return [r.left - s.left + r.width * dx, r.top - s.top + r.height * dy]; }
  var cursors = actors.map(function (a, n) {
    var p = P.of(a.name), c = document.createElement('div');
    c.className = 'cursor' + (p.inkText ? ' ink-t' : ''); c.style.setProperty('--who', p.color);
    c.innerHTML = '<svg width="20" height="22" viewBox="0 0 20 22" aria-hidden="true"><path d="M2 2l15 7.4-6.6 1.9L7.4 18z" fill="' + p.color + '" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg><span class="nm">' + a.name + (a.idle ? ' · 보는 중' : '') + '</span>';
    sheet.appendChild(c); a.el = c;
    var s = sheet.getBoundingClientRect();
    move(a, [s.width * (0.2 + n * 0.2), -30]);
    return c;
  });
  function move(a, xy) { a.el.style.transform = 'translate(' + Math.round(xy[0]) + 'px,' + Math.round(xy[1]) + 'px)'; }
  function type(a, k, v, done) {
    var td = cell(a.row, k), p = P.of(a.name);
    td.style.setProperty('--who', p.color); td.classList.add('editing');
    var s = String(v), j = 0, inp = $('input', td);
    (function step() {
      j++; inp.value = s.slice(0, j);
      if (j < s.length) setTimeout(step, 160); else setTimeout(function () { td.classList.remove('editing'); commit(a.row, k, v, a.name); done && done(); }, 300);
    })();
  }
  function run() {
    actors.forEach(function (a, n) {
      var t0 = 150 + n * 180;
      if (a.idle) { setTimeout(function () { move(a, at(cell(a.row, 'a'), .2, .6)); }, t0); setTimeout(function () { move(a, at(cell(4, 'b'), .4, .5)); }, t0 + 2600); return; }
      var r = rows[a.row], first = n === 0 ? 'b' : 'a', second = first === 'a' ? 'b' : 'a';
      setTimeout(function () { move(a, at(cell(a.row, first), .3, .5)); }, t0);
      setTimeout(function () { type(a, first, r[first], function () {
        move(a, at(cell(a.row, second), .3, .5));
        setTimeout(function () { type(a, second, r[second], function () { move(a, at(cell(a.row, 'b'), 1.1, .5)); }); }, 800);
      }); }, t0 + 1000);
    });
  }
  function finishNow() { rows.forEach(function (r, i) { if (r.a !== null) { state[i].a = r.a; state[i].b = r.b; state[i].by = r.who; } }); paint(); actors.forEach(function (a, n) { move(a, at(cell(a.row, 'b'), 1.05, 0.9)); }); }

  // 리마인드·내려받기
  $('#nudge').onclick = function () {
    var left = state.filter(function (r) { return r.a === null; });
    if (!left.length) { toast('모두 냈어요. 독촉할 반이 없어요.'); return; }
    var txt = '[고티처] 학예회 반별 참가 인원, ' + left.map(function (r) { return r.cls + ' ' + r.who; }).join(' · ') + ' 선생님 칸이 비어 있어요. 10/27(화) 12:00까지 링크에서 채워 주세요.';
    (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(function () { toast('독촉 문구를 복사했어요: ' + left.map(function (r) { return r.cls; }).join(' · ')); }, function () { toast(txt); });
  };
  $('#dl').onclick = function () { toast('2026_학예회_참가인원.hwpx · 올린 양식 그대로 채워서 내려받아요'); };

  // ---------- 한 줄 일정 해석 ----------
  var DAY = { '오늘': '10/26(월)', '내일': '10/27(화)', '모레': '10/28(수)', '월요일': '11/2(월)', '화요일': '10/27(화)', '수요일': '10/28(수)', '목요일': '10/29(목)', '금요일': '10/30(금)' };
  var WD = ['일', '월', '화', '수', '목', '금', '토'];
  function parse(s) {
    var out = { date: null, slot: null, place: null, title: s };
    var m = s.match(/(\d{1,2})\/(\d{1,2})/);
    if (m) { var d = new Date(2026, +m[1] - 1, +m[2]); out.date = m[1] + '/' + m[2] + '(' + WD[d.getDay()] + ')'; out.title = out.title.replace(m[0], ''); }
    Object.keys(DAY).forEach(function (k) { if (!out.date && s.indexOf(k) > -1) { out.date = DAY[k]; out.title = out.title.replace(k, ''); } });
    var b = s.match(/([123])블록|방과후/);
    if (b) { var sl = b[1] ? G.slots[+b[1] - 1] : G.slots[3]; out.slot = sl.label + ' ' + sl.from + (sl.to ? '~' + sl.to : '~'); out.title = out.title.replace(b[0], ''); }
    var p = s.match(/(\S*(?:실|관|교실|운동장|도서관))(?=\s|$)/);
    if (p) { out.place = p[1]; out.title = out.title.replace(p[1], ''); }
    out.title = out.title.replace(/\s+/g, ' ').trim();
    return out;
  }
  function renderAns() {
    var r = parse($('#q').value);
    function kv(k, v, ph) { return '<div><div class="k">' + k + '</div><div class="v' + (v ? '' : ' miss') + '">' + (v || ph) + '</div></div>'; }
    $('#ans').innerHTML = kv('날짜', r.date, '오늘') + kv('블록', r.slot, '시간 미정') + kv('장소', r.place, '—') + kv('제목', r.title, '—');
    $('#ansRes').textContent = r.slot ? '4학년 오늘 화면 ' + r.slot.split(' ')[0] + ' 칸에 올라가요.' : '시간이 없으면 「전일」 칸에 모였다가, 필요할 때 블록으로 옮겨요.';
  }
  $('#q').addEventListener('input', renderAns);
  $$('.examples button').forEach(function (b) { b.onclick = function () { $('#q').value = b.dataset.q; renderAns(); $('#q').focus(); }; });
  renderAns();

  // ---------- 오늘 미리보기 ----------
  var urgent = G.todo.filter(function (t) { return t.urgent; })[0];
  var now = G.slots.filter(function (s) { return s.k === G.nowSlot; })[0];
  var blocks = G.slots.map(function (s) {
    var tt = G.timetable.filter(function (t) { return t.k === s.k; })[0];
    var ev = G.events.filter(function (e) { return e.s === s.k; })[0];
    return '<div class="blk' + (s.k === G.nowSlot ? ' now' : '') + '"><b>' + s.label + (s.k === G.nowSlot ? ' · 지금' : '') + '</b>' +
      (tt ? '<span class="ev">' + tt.sub + '</span>' : '<span class="ev" style="color:var(--muted)">수업 없음</span>') + (ev ? '<span>' + ev.title + '</span>' : '') + '</div>';
  }).join('');
  var ch = G.changes.slice(0, 2).map(function (c) { return '<p>' + av(c.by) + '<span>' + c.title + ' ' + c.what + ' <del>' + c.from + '</del> → <b>' + c.to + '</b></span></p>'; }).join('');
  $('#tdy').innerHTML = '<div><p class="find">' + urgent.title.replace(/ \(.*\)/, '') + ', ' + urgent.due + ' 마감</p><p class="fsub">' + G.date.label + ' · 지금 ' + now.label + ' ' + now.from + '~' + now.to + '</p></div>' +
    '<div class="blocks">' + blocks + '</div>' +
    '<div class="tdy-row"><div class="tdy-card"><div class="h">내가 낼 것 ' + G.todo.length + '</div>' +
    G.todo.slice(0, 3).map(function (t) { return '<div class="chk" role="checkbox" aria-checked="false" tabindex="0"><span class="box"></span><span class="tx">' + t.title + '</span></div>'; }).join('') +
    '</div><div class="tdy-card feed"><div class="h">어제 이후 바뀐 것</div>' + ch + '</div></div>';
  $$('#tdy .chk').forEach(function (c) {
    function flip() { c.classList.toggle('on'); c.setAttribute('aria-checked', c.classList.contains('on')); }
    c.onclick = flip; c.onkeydown = function (e) { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); flip(); } };
  });

  // ---------- 등장 ----------
  var started = false;
  function startLive() { if (started) return; started = true; if (reduce) finishNow(); else run(); }
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add('in'); io.unobserve(e.target);
        if (e.target.id === 'heroUi') setTimeout(startLive, reduce ? 0 : 500);
      });
    }, { threshold: .12 });
    $$('.rise, .shutter').forEach(function (el) { io.observe(el); });
  } else { $$('.rise, .shutter').forEach(function (el) { el.classList.add('in'); }); startLive(); }
  // 캡처·인쇄용: 스크롤 없이도 항상 첫 화면이 열리게
  setTimeout(function () { $$('.hero .rise, #heroUi').forEach(function (el) { el.classList.add('in'); }); startLive(); }, 60);
})();
