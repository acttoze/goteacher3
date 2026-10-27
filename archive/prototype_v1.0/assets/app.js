// 티로드 프로토타입 — 화면 라우팅과 렌더링
(function () {
  var S = function () { return TR.state; };
  var esc = TR.esc, $ = TR.$, $$ = TR.$$;
  var view = $('#view');
  var role = 'teacher';

  // ---------- 라우터 ----------
  function route() {
    var h = (location.hash || '#today').slice(1).split('/');
    $$('.nav a').forEach(function (a) { a.classList.toggle('on', a.dataset.r === h[0]); });
    var fn = { today: today, cal: cal, collect: h[1] === 'new' ? collectNew : h[1] ? collectDetail : collectList, notice: h[1] ? noticeDetail : noticeList, school: school, start: start }[h[0]] || today;
    view.innerHTML = '';
    fn(h[1], h[2]);
    window.scrollTo(0, 0);
    updateDots();
  }
  window.addEventListener('hashchange', route);
  TR.onSync(function () { var h = location.hash; if (/collect\/|notice\/|today|school/.test(h || '#today')) softRender(); });
  function softRender() { var y = window.scrollY; var h = (location.hash || '#today').slice(1).split('/'); var fn = { today: today, collect: h[1] && h[1] !== 'new' ? collectDetail : null, notice: h[1] ? noticeDetail : null, school: school }[h[0]]; if (!fn) return; view.innerHTML = ''; fn(h[1], h[2], true); window.scrollTo(0, y); updateDots(); }

  function updateDots() {
    var n = S().notices.some(function (x) { return x.must && !S().acked[x.id]; });
    $('.nav a[data-r="notice"] .dot').hidden = !n;
    var c = S().collections.some(function (c) { return !c.mine && c.rows.some(function (r) { return mineRow(r) && !r.done; }); });
    $('.nav a[data-r="collect"] .dot').hidden = !c;
  }
  function mineRow(r) { return r.who === TR.me.name; }

  // ---------- 공통 조각 ----------
  function evCard(ev, opts) {
    opts = opts || {};
    var meta = [TR.slotText(ev), ev.place, ev.target].filter(Boolean).join(' · ');
    return '<div class="ev ' + ev.type + (ev.mine ? ' mine' : '') + '" data-ev="' + ev.id + '" title="' + esc(ev.title) + '">' +
      '<div class="t">' + (ev.changed ? '<span class="chg" title="오늘 바뀜"></span>' : '') + '<span class="sr">' + TR.typeLabel[ev.type] + '</span>' + esc(ev.title) + '</div>' +
      (opts.compact ? '' : '<div class="m">' + esc(meta) + (ev.note ? ' · ' + esc(ev.note) : '') + '</div>') + '</div>';
  }
  function progress(c) {
    var d = c.rows.filter(function (r) { return r.done; }).length;
    return { d: d, t: c.rows.length, p: Math.round(d / c.rows.length * 100) };
  }
  function bindEvClicks(root) {
    $$('[data-ev]', root).forEach(function (el) {
      el.addEventListener('click', function () { openEvent(el.dataset.ev); });
    });
  }
  function openEvent(id) {
    var ev = S().events.find(function (x) { return x.id === id; }); if (!ev) return;
    var conf = TR.conflicts(ev);
    modal('<div class="card-h"><h2>' + esc(ev.title) + '</h2><span class="badge">' + TR.typeLabel[ev.type] + '</span></div><div class="card-b">' +
      '<dl class="kv"><dt>날짜</dt><dd>' + TR.fmt(ev.date) + '</dd><dt>시간</dt><dd>' + esc(TR.slotText(ev)) + (ev.time && ev.s !== 'pm' && ev.s !== 'am' ? ' ' + esc(ev.time) : '') + '</dd>' +
      (ev.place ? '<dt>장소</dt><dd>' + esc(ev.place) + '</dd>' : '') + (ev.target ? '<dt>대상</dt><dd>' + esc(ev.target) + '</dd>' : '') +
      (ev.boost ? '<dt>보강</dt><dd class="need">' + esc(ev.boost) + '</dd>' : '') +
      '<dt>등록</dt><dd>' + esc(ev.by || ev.who || TR.me.name) + '</dd>' + (ev.note ? '<dt>메모</dt><dd>' + esc(ev.note) + '</dd>' : '') + '</dl>' +
      (ev.changed ? '<div class="diff" style="margin-top:12px"><b>바뀐 부분</b><div>' + ev.change + '</div></div>' : '') +
      (conf.length ? '<div class="warn">장소가 겹쳐요: ' + conf.map(function (c) { return esc(c.title) + '(' + esc(TR.slotText(c)) + ')'; }).join(', ') + '</div>' : '') +
      '<div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn sm" data-copy>메신저용 문구 복사</button><button class="btn sm pri" data-close>닫기</button></div></div>');
    $('[data-copy]', $('#modal')).onclick = function () { TR.copy('[티로드] ' + TR.fmt(ev.date) + ' ' + TR.slotText(ev) + ' ' + ev.title + (ev.place ? ' · ' + ev.place : '') + '\nhttps://t.road/s/' + ev.id); };
  }
  function modal(html) {
    var bg = $('#modal'); bg.innerHTML = '<div class="card modal" role="dialog" aria-modal="true">' + html + '</div>'; bg.classList.add('show');
    $$('[data-close]', bg).forEach(function (b) { b.onclick = closeModal; });
    bg.onclick = function (e) { if (e.target === bg) closeModal(); };
  }
  function closeModal() { $('#modal').classList.remove('show'); $('#modal').innerHTML = ''; }
  TR.closeModal = closeModal;
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeModal(); hideQ(); } });

  function phoneModal(src, title) {
    modal('<div class="card-h"><h2>' + esc(title) + '</h2><button class="btn sm ghost" data-close aria-label="닫기">✕</button></div><div class="card-b" style="display:flex;justify-content:center;background:var(--surface-2)"><div class="phone" style="height:660px"><iframe src="' + src + '" title="모바일 화면"></iframe></div></div><div class="card-b small muted">받는 선생님이 쪽지 링크를 눌렀을 때 보는 화면입니다. 로그인하지 않아도 됩니다.</div>');
    $('.modal').style.width = '440px';
  }

  // ---------- 오늘 ----------
  function today() {
    var st = S(), todayEv = st.events.filter(function (e) { return e.date === TR.TODAY; });
    var school = todayEv.filter(function (e) { return e.type !== 'duty'; });
    var duty = todayEv.filter(function (e) { return e.type === 'duty'; });
    var n2 = st.notices.find(function (n) { return n.id === 'n2'; });
    var changes = st.events.filter(function (e) { return e.changed && e.date >= TR.TODAY; });

    // 레인 배치
    var lanes = [];
    school.sort(function (a, b) { return TR.slotIdx(a.s) - TR.slotIdx(b.s) || (TR.slotIdx(b.e) - TR.slotIdx(a.e)); }).forEach(function (ev) {
      var a = TR.slotIdx(ev.s), b = TR.slotIdx(ev.e || ev.s);
      for (var i = 0; i < lanes.length; i++) if (lanes[i] < a) { lanes[i] = b; ev._lane = i; return; }
      lanes.push(b); ev._lane = lanes.length - 1;
    });
    var L = Math.max(lanes.length, 2);
    var tl = '<div class="tl" style="--lanes:' + L + '">';
    TR.slots.forEach(function (s, i) {
      tl += '<div class="slot' + (i === TR.NOW_SLOT ? ' now' : '') + '" style="grid-row:' + (i + 1) + '"><b>' + s.label + '</b>' + s.time + '</div><div class="line" style="grid-row:' + (i + 1) + '"></div>';
    });
    school.forEach(function (ev) {
      var a = TR.slotIdx(ev.s), b = TR.slotIdx(ev.e || ev.s);
      tl += '<div style="grid-row:' + (a + 1) + '/' + (b + 2) + ';grid-column:' + (ev._lane + 2) + '">' + evCard(ev) + '</div>';
    });
    tl += '</div>';

    var mine = TR.myTimetable.map(function (t) {
      var i = TR.slotIdx(t.k);
      return '<tr class="' + (t.boost ? 'boost' : '') + (i === TR.NOW_SLOT ? ' now' : '') + '"><td class="p"><b>' + t.k + '</b>' + TR.slots[i].time + '</td><td><div class="sub">' + esc(t.sub) + (t.boost ? ' <span class="badge red">보강</span>' : '') + '</div><div class="small muted">' + esc(t.room) + (t.note ? ' · ' + esc(t.note) : '') + '</div></td></tr>';
    }).join('');

    var dutyHtml = duty.map(function (d) {
      return '<div class="duty"><span class="k">' + d.duty + '</span><div class="grow"><div><b>' + esc(d.who) + '</b> <span class="muted small">' + esc(d.time) + (d.target ? ' · ' + esc(d.target) : '') + '</span>' + (d.changed ? ' <span class="chg"></span>' : '') + '</div>' +
        (d.boost ? '<div class="need">보강 필요 ' + esc(d.boost) + (d.id === 'd1' ? ' — 5교시는 정하은(나)' : '') + '</div>' : '') + '</div></div>';
    }).join('');

    // 할 일함
    var inbox = [];
    st.collections.forEach(function (c) {
      if (c.mine) {
        var p = progress(c);
        inbox.push({ k: 'send', id: c.id, title: c.title, meta: '내가 보낸 요청 · 마감 ' + c.due, p: p });
        var myRow = c.rows.find(mineRow);
        if (myRow && !myRow.done) inbox.push({ k: 'col', id: c.id, unit: myRow.unit, title: c.title + ' — 우리 반 칸', meta: '마감 ' + c.due + ' · 내가 만든 요청', urgent: false });
      } else {
        var r = c.rows.find(mineRow);
        if (r) inbox.push({ k: 'col', id: c.id, unit: r.unit, title: c.title, meta: c.dept + ' ' + c.owner + ' · 마감 ' + c.due, urgent: c.urgent, done: r.done });
      }
    });
    st.notices.forEach(function (n) { if (n.must) inbox.push({ k: 'ack', id: n.id, title: n.title + (n.ver > 1 ? ' v' + n.ver : ''), meta: '확인 요청 · ' + n.dept, done: !!st.acked[n.id] }); });
    inbox.sort(function (a, b) { return (a.done ? 1 : 0) - (b.done ? 1 : 0) || (b.urgent ? 1 : 0) - (a.urgent ? 1 : 0); });
    var open = inbox.filter(function (i) { return !i.done && i.k !== 'send'; }).length;
    var inboxHtml = inbox.map(function (it) {
      if (it.k === 'send') return '<a class="it" href="#collect/' + it.id + '" style="text-decoration:none;color:inherit"><span class="badge pri" style="margin-top:1px">보냄</span><div class="grow"><div class="ti">' + esc(it.title) + '</div><div class="meta">' + esc(it.meta) + '</div><div class="row" style="margin-top:6px"><div class="bar grow"><i style="width:' + it.p.p + '%"></i></div><span class="small"><b>' + it.p.d + '</b>/' + it.p.t + '</span></div><div class="meta"><span class="need">미제출 ' + (it.p.t - it.p.d) + '명</span> · 누르면 합본·독촉</div></div></a>';
      return '<div class="it' + (it.done ? ' done' : '') + '"><button class="box" aria-label="완료" data-act="' + it.k + '" data-id="' + it.id + '" data-unit="' + esc(it.unit || '') + '"></button><div class="grow"><div class="ti">' + esc(it.title) + '</div><div class="meta">' + (it.urgent && !it.done ? '<span class="badge red">오늘 14:00</span>' : '') + esc(it.meta) + '</div></div>' +
        (it.done ? '<span class="badge ok">완료</span>' : '<button class="btn sm' + (it.urgent ? ' pri' : '') + '" data-act="' + it.k + '" data-id="' + it.id + '" data-unit="' + esc(it.unit || '') + '">' + (it.k === 'ack' ? '보기' : '제출') + '</button>') + '</div>';
    }).join('');

    var chHtml = changes.map(function (e) {
      return '<li><span class="chg"></span><span class="t">' + TR.fmt(e.date) + ' ' + esc(e.title) + '</span><span>' + e.change + '</span><a href="javascript:void 0" data-ev="' + e.id + '" class="small">자세히</a></li>';
    }).join('') + '<li><span class="chg"></span><span class="t">5교시 4-3 보강이 나에게 배정됨</span><span>김민준 선생님 출장 · 국어 4단원 학습지(교탁 위)</span></li>' +
      '<li><span class="chg"></span><span class="t">공지 「' + esc(n2.title) + '」 v2</span><span>장소 <del>강당</del> → <ins>체육관</ins> · <del>5교시</del> → <ins>6교시</ins></span><a href="#notice/n2" class="small">공지 보기</a></li>';

    view.innerHTML =
      '<div class="page-h"><div><h1>10월 26일 월요일</h1><p>2학기 9주차 · ' + TR.school.name + ' · ' + esc(TR.me.role) + '</p></div>' +
      '<div class="row no-print"><a class="btn" href="#cal">이번 주 보기</a><button class="btn" id="printDay">오늘 일과 A4 인쇄</button></div></div>' +
      '<div class="strip"><span class="pill">🍚 <b>급식</b> 잡곡밥 · 쇠고기미역국 · 돈육장조림 · 감귤</span><span class="pill">🌤 미세먼지 보통</span><span class="pill">🔔 다음 알림 <b>12:20</b> (점심 묶음) · 수업 중엔 보내지 않아요</span><span class="pill faint">학사일정·급식은 NEIS에서 자동</span></div>' +
      '<section class="changes" aria-label="오늘 바뀐 것"><h2><span class="chg"></span>오늘 바뀐 것 ' + (changes.length + 2) + '건 <span class="small muted" style="font-weight:400">어제 퇴근 이후</span></h2><ul>' + chHtml + '</ul></section>' +
      '<div class="grid3">' +
      '<section class="card"><div class="card-h"><h2>내 시간표</h2><span class="small muted">4-2</span></div><div class="card-b" style="padding-top:4px"><table class="tt">' + mine + '</table></div></section>' +
      '<section class="card"><div class="card-h"><h2>학교 오늘</h2><span class="legend"><span><span class="sym-event">◆</span> 행사</span><span><span class="sym-work">■</span> 업무·교육</span><span><span class="sym-meet">▲</span> 회의</span></span></div><div class="card-b">' + tl +
      '<h3 style="font-size:14px;margin:16px 0 8px">복무 <span class="small muted" style="font-weight:400">' + duty.length + '명 · 나이스 결재와 별개로 공유만</span></h3><div class="duty-list">' + dutyHtml + '</div></div></section>' +
      '<section class="card col-r"><div class="card-h"><h2>나에게 온 것</h2><span class="badge ' + (open ? 'red' : 'ok') + '">' + (open ? '남은 일 ' + open : '모두 끝') + '</span></div><div class="card-b"><div class="inbox">' + inboxHtml + '</div>' +
      '<p class="small faint" style="margin:12px 0 0">쪽지로 온 요청도 링크를 누르는 순간 여기에 쌓입니다. 제출하면 자동으로 지워져요.</p></div></section>' +
      '</div>';

    bindEvClicks(view);
    $$('[data-act]', view).forEach(function (b) {
      b.addEventListener('click', function () {
        var k = b.dataset.act, id = b.dataset.id;
        if (k === 'col') phoneModal('m.html?c=' + id + '&u=' + encodeURIComponent(b.dataset.unit) + '&me=1', '제출하기');
        if (k === 'ack') location.hash = '#notice/' + id;
      });
    });
    $('#printDay').onclick = function () { location.hash = '#cal'; setTimeout(function () { window.print(); }, 300); };
  }

  // ---------- 일정 (주간/월간 + 인쇄) ----------
  var calMode = 'week', calMonth = '2026-10';
  function cal() {
    var st = S();
    var head = '<div class="page-h no-print"><div><h1>일정</h1><p>교시 축 주간 · 월중행사표. 인쇄는 전용 레이아웃(흑백 기호)으로 나갑니다.</p></div>' +
      '<div class="row"><div class="seg" role="tablist"><button data-m="week" class="' + (calMode === 'week' ? 'on' : '') + '">주간</button><button data-m="month" class="' + (calMode === 'month' ? 'on' : '') + '">월간</button></div>' +
      (calMode === 'month' ? '<button class="btn sm" data-mv="-1" aria-label="이전 달">◀ 이전 달</button><button class="btn sm" data-mv="1" aria-label="다음 달">다음 달 ▶</button>' : '') +
      '<button class="btn pri" id="print">' + (calMode === 'month' ? '월중행사표 인쇄 (A4 가로)' : '주간계획 인쇄') + '</button></div></div>';
    var y = +calMonth.slice(0, 4), m = +calMonth.slice(5);
    var title = calMode === 'month' ? y + '년 ' + m + '월 월중행사' : '10월 5주 주간교육계획 (10/26~10/30)';
    var ph = '<div class="print-head"><div><div class="small">' + TR.school.name + '</div><h1>' + title + '</h1><div class="small">출력 2026-10-26 · 티로드</div></div><table><tr><td>담당</td><td>부장</td><td>교감</td><td>교장</td></tr><tr><td></td><td></td><td></td><td></td></tr></table></div>';
    var legend = '<div class="legend" style="margin:8px 0">' + ['event', 'work', 'meet', 'duty'].map(function (t) { return '<span><span class="sym-' + t + '">' + TR.typeSym[t] + '</span> ' + TR.typeLabel[t] + '</span>'; }).join('') + '<span><span class="chg"></span> 최근 변경</span></div>';
    var body = '';
    if (calMode === 'week') {
      var days = [0, 1, 2, 3, 4].map(function (i) { return TR.addDays('2026-10-26', i); });
      body = '<div class="wk"><div class="hd"></div>' + days.map(function (d) { return '<div class="hd' + (d === TR.TODAY ? ' today' : '') + '">' + TR.fmt(d) + '</div>'; }).join('');
      TR.slots.forEach(function (s, si) {
        body += '<div class="sl"><b>' + s.label + '</b>' + s.time + '</div>';
        days.forEach(function (d) {
          var evs = st.events.filter(function (e) { return e.date === d && TR.slotIdx(e.s) === si; });
          body += '<div class="c">' + evs.map(function (e) { return evCard(e, { compact: true }).replace('<div class="t">', '<div class="t"><span class="sym-' + e.type + '">' + TR.typeSym[e.type] + '</span>'); }).join('') + '</div>';
        });
      });
      body += '</div>';
    } else {
      var first = new Date(y, m - 1, 1), start = new Date(first); start.setDate(1 - first.getDay());
      body = '<div class="mo">' + TR.dow.map(function (d) { return '<div class="dh">' + d + '</div>'; }).join('');
      for (var i = 0; i < 42; i++) {
        var dd = new Date(start); dd.setDate(start.getDate() + i); var iso = TR.iso(dd);
        if (i >= 35 && dd.getMonth() !== m - 1) break;
        var items = st.events.filter(function (e) { return e.date === iso && e.type !== 'duty'; }).map(function (e) { return [e.title, e.type, e.changed, e.id]; })
          .concat(TR.monthExtra.filter(function (x) { return x[0] === iso; }).map(function (x) { return [x[1], x[2]]; }));
        var hol = TR.holidays[iso];
        var cls = [dd.getMonth() !== m - 1 ? 'out' : '', hol ? 'hol' : '', dd.getDay() === 0 ? 'sun' : '', dd.getDay() === 6 ? 'sat' : '', iso === TR.TODAY ? 'today' : ''].join(' ');
        var shown = items.slice(0, 4);
        body += '<div class="' + cls + '"><div class="d"><span>' + dd.getDate() + '</span>' + (hol ? '<span class="small">' + hol + '</span>' : '') + '</div>' +
          shown.map(function (it) { return '<div class="e"' + (it[3] ? ' data-ev="' + it[3] + '" style="cursor:pointer"' : '') + '><span class="s sym-' + it[1] + '">' + TR.typeSym[it[1]] + '</span>' + (it[2] ? '<span class="chg" style="width:6px;height:6px"></span>' : '') + '<span>' + esc(it[0]) + '</span></div>'; }).join('') +
          (items.length > 4 ? '<div class="e faint">+' + (items.length - 4) + '건 별지</div>' : '') + '</div>';
      }
      body += '</div>';
    }
    view.innerHTML = '<div class="page">' + head + ph + '<div class="no-print" style="margin-bottom:8px"><div class="proto-note">한 줄 입력(상단)으로 추가한 일정도 여기 바로 들어옵니다. 예: <b>모레 5교시 4학년 강당 학예회 연습</b></div></div>' + legend + body + '</div>';
    view.firstChild.style.padding = '0';
    $$('[data-m]', view).forEach(function (b) { b.onclick = function () { calMode = b.dataset.m; cal(); }; });
    $$('[data-mv]', view).forEach(function (b) { b.onclick = function () { var d = new Date(y, m - 1 + (+b.dataset.mv), 1); calMonth = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); view.innerHTML = ''; cal(); }; });
    $('#print').onclick = function () { window.print(); };
    bindEvClicks(view);
  }

  // ---------- 모으기 ----------
  function collectList() {
    var st = S();
    var rows = st.collections.map(function (c) {
      var p = progress(c), r = c.rows.find(mineRow);
      var mineState = c.mine ? '<span class="badge pri">내가 보냄</span>' : r ? (r.done ? '<span class="badge ok">제출함</span>' : '<span class="badge red">내 칸 비었음</span>') : '';
      return '<a class="crow" href="#collect/' + c.id + '"><div><div class="row"><b>' + esc(c.title) + '</b>' + mineState + '</div><div class="small muted">' + esc(c.dept) + ' ' + esc(c.owner) + ' · ' + esc(c.target) + '</div></div>' +
        '<div><div class="bar' + (c.urgent ? ' red' : '') + '"><i style="width:' + p.p + '%"></i></div><div class="small muted" style="margin-top:4px"><b style="color:var(--text)">' + p.d + '</b>/' + p.t + ' 제출 · ' + p.p + '%</div></div>' +
        '<div class="small' + (c.urgent ? ' need' : ' muted') + '">마감 ' + esc(c.due) + '</div><div class="small muted">미제출 ' + (p.t - p.d) + '명</div></a>';
    }).join('');
    view.innerHTML = '<div class="page-h"><div><h1>모으기</h1><p>양식 표를 붙여넣으면 끝. 받는 사람은 자기 칸만 채우고, 합본은 실시간으로 만들어집니다.</p></div><a class="btn pri" href="#collect/new">＋ 새로 모으기</a></div>' +
      '<div class="card">' + rows + '</div>' +
      '<div class="grid2" style="margin-top:16px"><div class="proto-note">고티처의 고시트는 18건 쓰였고, 그마저 대부분 테스트였습니다. 웹 시트를 새로 만들게 하지 않고 <b>원래 쓰던 hwp·엑셀 표를 그대로 붙여넣는 것</b>에서 시작합니다.</div><div class="proto-note">합본 파일 꼬리말에 "티로드로 모았습니다"가 들어갑니다. 받는 사람이 다음 취합을 직접 만들어 보게 하는 장치입니다.</div></div>';
  }

  var SAMPLE_TABLE = '학년반\t담임\t희망 인원\t희망 프로그램\t비고\n1-1\t강지은\t\t\t\n1-2\t윤서준\t\t\t\n1-3\t장미경\t\t\t\n2-1\t최수아\t\t\t\n…';
  function collectNew() {
    view.innerHTML = '<div class="page-h"><div><h1>새로 모으기</h1><p>hwp·엑셀에서 표를 복사(Ctrl+C)해서 아래에 붙여넣으세요.</p></div><a class="btn" href="#collect">취소</a></div>' +
      '<div class="grid2"><section class="card"><div class="card-b">' +
      '<div class="step"><i>1</i>양식 표 붙여넣기 <button class="btn sm" id="sample" style="margin-left:auto">예시 표 넣기</button></div>' +
      '<textarea class="paste" id="paste" placeholder="여기에 붙여넣기 (Ctrl+V)\n\n예) 학년반 | 담임 | 희망 인원 | 희망 프로그램 | 비고"></textarea>' +
      '<div id="fields" style="margin-top:12px"></div>' +
      '<div class="step" style="margin-top:18px"><i>2</i>누구에게 받나요</div>' +
      '<div style="display:grid;gap:6px"><label><input type="radio" name="tg" value="hr" checked> 담임 24명 · 반별 1칸 (행 = 학년반)</label><label><input type="radio" name="tg" value="all"> 전 교원 38명 · 개인별 1칸</label><label><input type="radio" name="tg" value="g34"> 3·4학년 담임 8명</label></div>' +
      '<div class="step" style="margin-top:18px"><i>3</i>마감</div>' +
      '<div class="row"><input type="date" id="dd" value="2026-10-28" style="height:34px;border:1px solid var(--line-strong);border-radius:8px;padding:0 8px;background:var(--surface)"><input type="time" id="dt" value="12:00" style="height:34px;border:1px solid var(--line-strong);border-radius:8px;padding:0 8px;background:var(--surface)"><label class="toggle"><input type="checkbox" checked> D-1 · 당일 08:20 자동 리마인드</label></div>' +
      '<div class="row" style="margin-top:18px"><input id="title" placeholder="제목 (예: 2학기 방과후 수요조사)" style="flex:1;height:38px;border:1px solid var(--line-strong);border-radius:8px;padding:0 10px;background:var(--surface)"><button class="btn pri" id="create">만들고 링크 받기</button></div>' +
      '</div></section>' +
      '<section class="card"><div class="card-h"><h2>받는 사람에게 갈 쪽지</h2><span class="small muted">쿨메신저에 그대로 붙여넣기</span></div><div class="card-b"><div class="msg" id="msg"></div><p class="small muted">링크를 누른 선생님은 로그인 없이 <b>자기 반 칸만</b> 보입니다. 처음 한 번 이름만 고르면 기기가 기억합니다.</p><div id="pii"></div></div></section></div>';
    var paste = $('#paste'), fieldsEl = $('#fields'), parsed = null;
    function analyze() {
      var lines = paste.value.split(/\r?\n/).filter(function (l) { return l.trim() && l.trim() !== '…'; });
      if (!lines.length) { fieldsEl.innerHTML = ''; parsed = null; renderMsg(); return; }
      var sep = lines[0].indexOf('\t') >= 0 ? '\t' : lines[0].indexOf('|') >= 0 ? '|' : /\s{2,}/;
      var head = lines[0].split(sep).map(function (s) { return s.trim(); }).filter(Boolean);
      var unitCols = head.filter(function (h) { return /학년|반|이름|성명|담임|교사|부서/.test(h); });
      var inputCols = head.filter(function (h) { return unitCols.indexOf(h) < 0; });
      parsed = { head: head, unit: unitCols, input: inputCols, rows: lines.length - 1 };
      fieldsEl.innerHTML = '<div class="small muted" style="margin-bottom:6px">머리글 ' + head.length + '개를 찾았어요. <b>회색</b>은 받는 사람 칸(미리 채움), <b>나머지</b>는 입력 칸입니다.</div><div class="chips">' +
        unitCols.map(function (h) { return '<span class="field-chip" style="background:var(--surface-2)">' + esc(h) + '<span class="small faint">미리 채움</span></span>'; }).join('') +
        inputCols.map(function (h) { var t = /인원|수|시간|금액/.test(h) ? 'number' : /프로그램|여부|희망/.test(h) ? 'select' : /일$|날짜/.test(h) ? 'date' : 'text'; return '<span class="field-chip">' + esc(h) + '<select aria-label="입력 형식"><option' + (t === 'text' ? ' selected' : '') + '>글자</option><option' + (t === 'number' ? ' selected' : '') + '>숫자</option><option' + (t === 'select' ? ' selected' : '') + '>선택지</option><option' + (t === 'date' ? ' selected' : '') + '>날짜</option></select></span>'; }).join('') + '</div>';
      var pii = head.filter(function (h) { return /학생\s?(이름|성명)|학생명|연락처|전화|주민|주소/.test(h); });
      $('#pii').innerHTML = pii.length ? '<div class="warn">학생 개인정보로 보이는 열이 있어요: ' + pii.map(esc).join(', ') + '. 티로드는 학생 개인정보를 저장하지 않습니다. 인원 수로 바꾸거나 열을 빼 주세요.</div>' : '';
      if (!$('#title').value) $('#title').value = /방과후|프로그램/.test(paste.value) ? '2학기 방과후 수요조사(추가)' : '새 취합';
      renderMsg();
    }
    function renderMsg() {
      var t = $('#title').value || '(제목)', d = $('#dd').value, tm = $('#dt').value;
      $('#msg').textContent = '[티로드] ' + t + '\n마감: ' + TR.fmt(d) + ' ' + tm + '\n\n아래 링크에서 우리 반 칸만 채워 주세요. (로그인 없음, 30초)\nhttps://t.road/c/' + (parsed ? 'k7Q2' : '····') + '\n\n— ' + TR.me.name + ' (방과후돌봄부)';
    }
    paste.addEventListener('input', analyze);
    paste.addEventListener('paste', function () { setTimeout(analyze, 0); });
    $('#title').addEventListener('input', renderMsg); $('#dd').addEventListener('change', renderMsg); $('#dt').addEventListener('change', renderMsg);
    $('#sample').onclick = function () { paste.value = SAMPLE_TABLE.replace('…', TR.homerooms.slice(4).map(function (h) { return h[0] + '\t' + h[1] + '\t\t\t'; }).join('\n')).replace(/\n2-1\t최수아\t\t\t$/m, ''); analyze(); };
    renderMsg();
    $('#create').onclick = function () {
      if (!parsed || !parsed.input.length) { TR.toast('먼저 양식 표를 붙여넣어 주세요. "예시 표 넣기"를 눌러도 돼요.'); return; }
      var tg = $('input[name=tg]:checked').value;
      var units = tg === 'all' ? TR.staff.map(function (s) { return [s.role, s.name]; }) : tg === 'g34' ? TR.homerooms.slice(8, 16) : TR.homerooms;
      var fields = $$('.field-chip select', fieldsEl).map(function (sel, i) { var t = { '숫자': 'number', '선택지': 'select', '날짜': 'date' }[sel.value] || 'text'; return { n: parsed.input[i], t: t, o: t === 'select' ? TR.programs : undefined }; });
      var id = 'c' + Date.now().toString(36);
      S().collections.unshift({ id: id, title: $('#title').value, owner: TR.me.name, dept: '방과후돌봄부', mine: true, due: TR.fmt($('#dd').value) + ' ' + $('#dt').value, target: tg === 'all' ? '전 교원 38명 · 개인별' : tg === 'g34' ? '3·4학년 담임 8명' : '담임 24명 · 반별 1칸', fields: fields,
        rows: units.map(function (u) { return { unit: u[0], who: u[1], done: false, at: '', v: fields.map(function () { return ''; }) }; }) });
      TR.save(); TR.copy($('#msg').textContent, '만들었어요. 쪽지 문구를 복사했습니다.');
      location.hash = '#collect/' + id;
    };
  }

  var phoneUnit = {};
  function collectDetail(id, _x, soft) {
    var c = S().collections.find(function (x) { return x.id === id; }); if (!c) { location.hash = '#collect'; return; }
    var p = progress(c), miss = c.rows.filter(function (r) { return !r.done; });
    if (!phoneUnit[id] || !c.rows.some(function (r) { return r.unit === phoneUnit[id]; })) phoneUnit[id] = (miss[0] || c.rows[0]).unit;
    var th = '<tr><th>' + (/담임/.test(c.target) ? '학년반' : '구분') + '</th><th>이름</th>' + c.fields.map(function (f) { return '<th>' + esc(f.n) + '</th>'; }).join('') + '<th>제출</th></tr>';
    var lastAt = TR._lastSubmit && TR._lastSubmit.c === id ? TR._lastSubmit.u : null;
    var tb = c.rows.map(function (r) {
      return '<tr class="' + (r.done ? '' : 'pending') + (lastAt === r.unit ? ' flash' : '') + '"><td><b>' + esc(r.unit) + '</b></td><td>' + esc(r.who) + '</td>' + r.v.map(function (v) { return '<td>' + (v ? esc(v) : '<span class="faint">—</span>') + '</td>'; }).join('') +
        '<td>' + (r.done ? '<span class="badge ok">' + esc(r.at || '제출') + '</span>' : '<span class="badge red">미제출</span>') + '</td></tr>';
    }).join('');
    var sums = c.fields.map(function (f, i) { if (f.t !== 'number') return ''; var s = c.rows.reduce(function (a, r) { return a + (+r.v[i] || 0); }, 0); return esc(f.n) + ' 합계 <b>' + s + '</b>'; }).filter(Boolean).join(' · ');
    var sel = c.fields.find(function (f) { return f.t === 'select'; }), selIdx = c.fields.indexOf(sel), dist = '';
    if (sel) { var cnt = {}; c.rows.forEach(function (r) { if (r.v[selIdx]) cnt[r.v[selIdx]] = (cnt[r.v[selIdx]] || 0) + 1; }); dist = Object.keys(cnt).map(function (k) { return esc(k) + ' ' + cnt[k]; }).join(' · '); }

    view.innerHTML = '<div class="page-h"><div><div class="small"><a href="#collect">모으기</a> /</div><h1>' + esc(c.title) + '</h1><p>' + esc(c.dept) + ' ' + esc(c.owner) + ' · ' + esc(c.target) + ' · 마감 ' + esc(c.due) + '</p></div>' +
      '<div class="row"><button class="btn" id="csv">엑셀로 내보내기</button><button class="btn" id="hwpx">원본 양식(hwpx)으로</button>' + (c.mine ? '<button class="btn" id="lock">마감하고 잠그기</button>' : '') + '</div></div>' +
      '<div class="split"><div>' +
      '<div class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))"><div class="card kpi"><div class="n">' + p.d + '<span class="muted" style="font-size:18px">/' + p.t + '</span></div><div class="l">제출 · ' + p.p + '%</div><div class="bar" style="margin-top:8px"><i style="width:' + p.p + '%"></i></div></div>' +
      '<div class="card kpi"><div class="n" style="color:var(--danger)">' + miss.length + '</div><div class="l">미제출 · 08:20 자동 리마인드 예정</div></div>' +
      '<div class="card kpi"><div class="l">실시간 합본</div><div style="margin-top:4px;font-size:13px">' + (sums || '—') + (dist ? '<br>' + dist : '') + '</div></div></div>' +
      (miss.length ? '<section class="card" style="margin-bottom:16px"><div class="card-h"><h2>미제출 ' + miss.length + '명</h2><button class="btn sm pri" id="remindAll">한 번에 독촉 문구 복사</button></div><div class="card-b"><div class="miss">' + miss.map(function (r) { return '<button data-r="' + esc(r.unit) + '">' + esc(r.unit) + ' ' + esc(r.who) + '</button>'; }).join('') + '</div><p class="small muted" style="margin:8px 0 0">이름을 누르면 그 선생님께 보낼 쪽지 문구가 복사됩니다. 일일이 명단과 대조하지 않아도 돼요.</p></div></section>' : '<div class="proto-note" style="margin-bottom:16px">모두 제출했습니다. 합본을 내려받으세요.</div>') +
      '<section class="card"><div class="card-h"><h2>합본</h2><span class="small muted">제출되는 즉시 반영</span></div><div class="scroll"><table class="tbl">' + th + tb + '</table></div></section>' +
      '</div><aside class="no-print"><div class="row" style="margin-bottom:8px"><b class="grow">받는 사람 화면</b><select id="pu" style="border:1px solid var(--line);border-radius:8px;padding:4px 6px;background:var(--surface)">' + c.rows.map(function (r) { return '<option' + (r.unit === phoneUnit[id] ? ' selected' : '') + '>' + esc(r.unit) + '</option>'; }).join('') + '</select></div>' +
      '<div class="phone"><iframe id="phone" title="받는 사람 모바일 화면" src="m.html?c=' + id + '&u=' + encodeURIComponent(phoneUnit[id]) + '"></iframe></div><p class="small muted" style="max-width:360px">휴대폰 화면에서 제출하면 왼쪽 합본에 바로 들어옵니다. 직접 눌러 보세요.</p></aside></div>';
    TR._lastSubmit = null;
    $$('.miss button', view).forEach(function (b) { b.onclick = function () { var r = c.rows.find(function (x) { return x.unit === b.dataset.r; }); TR.copy(r.who + ' 선생님, 「' + c.title + '」 ' + r.unit + ' 칸이 아직 비어 있어요. 마감 ' + c.due + '\nhttps://t.road/c/k7Q2?u=' + encodeURIComponent(r.unit), r.who + ' 선생님께 보낼 문구를 복사했어요.'); }; });
    var ra = $('#remindAll'); if (ra) ra.onclick = function () { TR.copy('[티로드] 「' + c.title + '」 아직 제출 전인 선생님: ' + miss.map(function (r) { return r.unit + ' ' + r.who; }).join(', ') + '\n마감 ' + c.due + ' · 링크에서 우리 반 칸만 채워 주세요.\nhttps://t.road/c/k7Q2', '미제출 ' + miss.length + '명 독촉 문구를 복사했어요.'); };
    $('#pu').onchange = function () { phoneUnit[id] = this.value; $('#phone').src = 'm.html?c=' + id + '&u=' + encodeURIComponent(this.value); };
    $('#csv').onclick = function () {
      var lines = [['구분', '이름'].concat(c.fields.map(function (f) { return f.n; })).concat(['제출시각'])].concat(c.rows.map(function (r) { return [r.unit, r.who].concat(r.v).concat([r.at]); }));
      var csv = '﻿' + lines.map(function (l) { return l.map(function (x) { return '"' + String(x || '').replace(/"/g, '""') + '"'; }).join(','); }).join('\r\n') + '\r\n"티로드로 모았습니다 · t.road"';
      var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); a.download = c.title + '_합본.csv'; a.click();
      TR.toast('합본을 내려받았어요 (엑셀에서 열림).');
    };
    $('#hwpx').onclick = function () { TR.toast('프로토타입: 원본 hwp 표 양식에 값을 채운 hwpx를 만드는 단계입니다 (본 개발 범위).'); };
    var lk = $('#lock'); if (lk) lk.onclick = function () { TR.toast('마감했어요. 늦은 제출은 "지각" 표시와 함께 받습니다.'); };
  }

  // iframe 제출 신호 → 강조 표시용
  window.addEventListener('message', function (e) { if (e.data && e.data.troad === 'submitted') { TR._lastSubmit = { c: e.data.c, u: e.data.u }; TR.state = TR.load(); softRender(); TR.toast(e.data.u + ' 제출이 합본에 들어왔어요.'); } if (e.data && e.data.troad === 'acked') { TR.state = TR.load(); softRender(); } });

  // ---------- 공지 ----------
  function noticeList() {
    var st = S();
    view.innerHTML = '<div class="page-h"><div><h1>공지</h1><p>고칠 때마다 판(v1, v2…)이 올라가고, 바뀐 부분만 다시 보여 줍니다.</p></div><button class="btn pri" id="nn">＋ 공지 쓰기</button></div><div class="card">' +
      st.notices.map(function (n) {
        return '<a class="crow" href="#notice/' + n.id + '" style="grid-template-columns:minmax(0,1fr) 200px 160px"><div><div class="row"><b>' + esc(n.title) + '</b>' + (n.ver > 1 ? '<span class="badge acc">v' + n.ver + ' 바뀜</span>' : '') + (n.must ? '<span class="badge pri">확인 필수</span>' : '') + (n.must && !st.acked[n.id] ? '<span class="badge red">내 확인 전</span>' : '') + '</div><div class="small muted">' + esc(n.dept) + ' ' + esc(n.by) + ' · ' + esc(n.updated) + '</div></div>' +
          '<div><div class="bar"><i style="width:' + Math.round(n.seen / n.total * 100) + '%"></i></div><div class="small muted" style="margin-top:4px">확인 ' + n.seen + '/' + n.total + '</div></div><div class="small muted">' + (n.real ? '실질 변경 · 재확인 ' + n.seenV2 + '명' : '—') + '</div></a>';
      }).join('') + '</div>';
    $('#nn').onclick = function () { TR.toast('프로토타입: 공지 쓰기는 일정 한 줄 입력과 같은 방식(칩)으로 만듭니다.'); };
  }

  function noticeDetail(id) {
    var st = S(), n = st.notices.find(function (x) { return x.id === id; }); if (!n) { location.hash = '#notice'; return; }
    var cur = n.versions[n.versions.length - 1], prev = n.versions[n.versions.length - 2];
    var diffRows = Object.keys(cur.fields).map(function (k) {
      var a = prev && prev.fields[k], b = cur.fields[k];
      var changed = prev && a !== b;
      return '<dt>' + esc(k) + '</dt><dd>' + (changed ? '<del>' + esc(a) + '</del> → <ins>' + esc(b) + '</ins>' : esc(b)) + '</dd>';
    }).join('');
    var pct = Math.round(n.seen / n.total * 100);
    var canMust = role === 'vp';
    view.innerHTML = '<div class="page-h"><div><div class="small"><a href="#notice">공지</a> /</div><h1>' + esc(n.title) + ' ' + (n.ver > 1 ? '<span class="badge acc" style="vertical-align:middle">v' + n.ver + '</span>' : '') + '</h1><p>' + esc(n.dept) + ' ' + esc(n.by) + ' · 최종 수정 ' + esc(n.updated) + '</p></div>' +
      '<div class="row"><button class="btn" id="cp">쪽지용 링크 복사</button>' + (n.must ? (st.acked[n.id] ? '<span class="badge ok" style="height:34px;padding:0 12px">확인함</span>' : '<button class="btn pri" id="ack">확인했어요</button>') : '') + '</div></div>' +
      '<div class="split"><div>' +
      (prev ? '<section class="diff" style="margin-bottom:16px"><div class="row" style="margin-bottom:8px"><span class="chg"></span><b>v' + prev.v + ' → v' + cur.v + '에서 바뀐 부분</b><span class="small muted">' + esc(cur.at) + '</span></div><dl class="kv">' + Object.keys(cur.fields).filter(function (k) { return prev.fields[k] !== cur.fields[k]; }).map(function (k) { return '<dt>' + esc(k) + '</dt><dd><del>' + esc(prev.fields[k]) + '</del> → <ins>' + esc(cur.fields[k]) + '</ins></dd>'; }).join('') + '</dl><p class="small" style="margin:8px 0 0">v1을 이미 본 28명에게는 <b>이 카드만</b> 「오늘 바뀐 것」에 다시 뜹니다. 전체를 다시 읽게 하지 않아요.</p></section>' : '') +
      '<section class="card"><div class="card-h"><h2>내용</h2><div class="seg">' + n.versions.map(function (v) { return '<button data-v="' + v.v + '" class="' + (v.v === cur.v ? 'on' : '') + '">v' + v.v + '</button>'; }).join('') + '</div></div><div class="card-b"><dl class="kv" id="kv">' + diffRows + '</dl><p style="margin:14px 0 0">' + esc(n.body) + '</p></div></section>' +
      '<section class="card" style="margin-top:16px"><div class="card-h"><h2>보낸 사람 설정</h2></div><div class="card-b" style="display:grid;gap:10px">' +
      '<label class="toggle"><input type="checkbox" ' + (n.real ? 'checked' : '') + ' id="real"> 실질 변경(일시·장소·대상·준비물) — 켜면 이전에 본 사람에게 바뀐 부분만 다시 알림</label>' +
      '<label class="toggle"><input type="checkbox" ' + (n.must ? 'checked' : '') + (canMust ? '' : ' disabled') + '> 확인 필수 — <span class="muted">' + (canMust ? '교감 권한으로 켤 수 있어요' : '부장·교감만 켤 수 있어요 (아무나 켜면 다시 "확인했습니다" 쪽지 30통이 됩니다)') + '</span></label>' +
      '<p class="small muted" style="margin:0">기본값은 <b>열어 보면 확인한 것</b>으로 칩니다(2초 이상 보거나 끝까지 스크롤, 링크 미리보기 봇은 제외).</p></div></section>' +
      '</div><aside><section class="card"><div class="card-h"><h2>확인 현황</h2><b>' + n.seen + '/' + n.total + '</b></div><div class="card-b"><div class="bar"><i style="width:' + pct + '%"></i></div>' +
      (n.ver > 1 ? '<p class="small" style="margin:10px 0 4px">v' + n.ver + ' 바뀐 부분 확인 <b>' + n.seenV2 + '/' + n.seen + '</b></p><div class="bar"><i style="width:' + Math.round(n.seenV2 / n.seen * 100) + '%;background:var(--accent)"></i></div>' : '') +
      (id === 'n2' ? '<p class="small muted" style="margin:12px 0 6px">아직 안 본 선생님</p><div class="miss">' + TR.unseenN2.map(function (x) { return '<button>' + x + '</button>'; }).join('') + '</div><button class="btn sm" style="margin-top:10px" id="rem">안 본 분께 보낼 문구 복사</button>' : '') +
      '</div></section><div style="margin-top:16px"><div class="small muted" style="margin-bottom:6px">받는 사람 휴대폰 화면</div><div class="phone" style="height:640px"><iframe src="m.html?n=' + id + '" title="공지 모바일 화면"></iframe></div></div></aside></div>';
    $$('[data-v]', view).forEach(function (b) { b.onclick = function () { $$('[data-v]', view).forEach(function (x) { x.classList.toggle('on', x === b); }); var v = n.versions[+b.dataset.v - 1]; $('#kv').innerHTML = Object.keys(v.fields).map(function (k) { return '<dt>' + esc(k) + '</dt><dd>' + esc(v.fields[k]) + '</dd>'; }).join(''); }; });
    var ack = $('#ack'); if (ack) ack.onclick = function () { st.acked[n.id] = true; n.seen = Math.min(n.total, n.seen + 1); TR.save(); TR.toast('확인했어요. 보낸 분 화면에 바로 반영됩니다.'); noticeDetail(id); updateDots(); };
    $('#cp').onclick = function () { TR.copy('[티로드] ' + n.title + (n.ver > 1 ? ' (v' + n.ver + ' 수정: 장소·시간 변경)' : '') + '\nhttps://t.road/n/' + n.id); };
    var rem = $('#rem'); if (rem) rem.onclick = function () { TR.copy('[티로드] 「' + n.title + '」 v2에서 장소(체육관)·시간(6교시)이 바뀌었어요. 확인 부탁드립니다.\nhttps://t.road/n/' + n.id, '미확인 ' + TR.unseenN2.length + '명께 보낼 문구를 복사했어요.'); };
  }

  // ---------- 학교(교감 현황판) ----------
  var assigned = { 'd1-5': '정하은' };
  function school() {
    var st = S(), duty = st.events.filter(function (e) { return e.date === TR.TODAY && e.type === 'duty'; });
    var needCells = 0;
    var board = duty.map(function (d) {
      var cells = ['1', '2', '3', '4', '5', '6'].map(function (k) {
        var key = d.id + '-' + k, inSpan = TR.slotIdx(k) >= TR.slotIdx(d.s) && TR.slotIdx(k) <= TR.slotIdx(d.e);
        var need = (d.need || []).indexOf(k) >= 0 || (d.id === 'd1' && k === '5');
        if (!inSpan) return '<div></div>';
        if (assigned[key]) return '<div class="ok" title="' + assigned[key] + '">' + assigned[key].slice(1) + '</div>';
        if (need) { needCells++; return '<div class="need" data-cell="' + key + '" role="button" tabindex="0" title="보강 배정">미배정</div>'; }
        return '<div title="수업 없음 · 전담">전담</div>';
      }).join('');
      return '<div class="row" style="margin-bottom:8px;align-items:center"><div style="width:150px"><span class="badge dut">' + d.duty + '</span> <b>' + esc(d.who) + '</b><div class="small muted">' + esc(d.time) + (d.boost ? ' · ' + esc(d.boost.split(' ')[0]) : '') + '</div></div><div class="sub-grid grow">' + cells + '</div></div>';
    }).join('');
    var urgent = st.collections.slice().sort(function (a, b) { return (a.dueISO || 'z') < (b.dueISO || 'z') ? -1 : 1; });
    var conflicts = [];
    st.events.forEach(function (e) { TR.conflicts(e).forEach(function (x) { if (e.id < x.id) conflicts.push([e, x]); }); });
    var n2 = st.notices.find(function (n) { return n.id === 'n2'; });
    view.innerHTML = '<div class="page-h"><div><h1>학교 현황판</h1><p>교감 화면 · 아침 8시 30분, 오늘 학교가 굴러가는 데 필요한 것만.</p></div><div class="row"><a class="btn" href="#start">새 학년 세팅</a></div></div>' +
      '<div class="kpis"><div class="card kpi"><div class="n">' + duty.length + '명</div><div class="l">오늘 부재 (출장·조퇴·외출·연가)</div></div><div class="card kpi"><div class="n" style="color:var(--danger)">' + needCells + '칸</div><div class="l">보강 미배정</div></div>' +
      '<div class="card kpi"><div class="n">' + progress(urgent[0]).d + '/' + progress(urgent[0]).t + '</div><div class="l">가장 급한 취합 · ' + esc(urgent[0].title) + ' (' + esc(urgent[0].due) + ')</div></div><div class="card kpi"><div class="n">' + Math.round(n2.seen / n2.total * 100) + '%</div><div class="l">확인 필수 공지 확인율 · ' + esc(n2.title) + '</div></div></div>' +
      '<div class="grid2"><section class="card"><div class="card-h"><h2>오늘 복무 · 보강판</h2><span class="small muted">빨간 칸을 눌러 배정</span></div><div class="card-b"><div class="row" style="margin-bottom:6px"><div style="width:150px"></div><div class="sub-grid grow" style="font-weight:600">' + [1, 2, 3, 4, 5, 6].map(function (k) { return '<div style="background:none;height:18px">' + k + '교시</div>'; }).join('') + '</div></div>' + board + '<p class="small muted" style="margin:10px 0 0">복무 결재는 나이스에서. 티로드는 "오늘 누가 비고 누가 들어가는지"를 학교 전체가 같이 보게 합니다. 보강 자동 추천은 v2.</p></div></section>' +
      '<section class="card"><div class="card-h"><h2>마감 임박 취합</h2></div><div class="card-b" style="display:grid;gap:12px">' + urgent.map(function (c) { var p = progress(c); return '<div><div class="row"><b class="grow">' + esc(c.title) + '</b><span class="small' + (c.urgent ? ' need' : ' muted') + '">' + esc(c.due) + '</span></div><div class="row" style="margin-top:4px"><div class="bar grow' + (c.urgent ? ' red' : '') + '"><i style="width:' + p.p + '%"></i></div><span class="small">' + p.d + '/' + p.t + '</span><button class="btn sm" data-rem="' + c.id + '">리마인드</button></div></div>'; }).join('') + '</div></section>' +
      '<section class="card"><div class="card-h"><h2>확인 필수 공지</h2></div><div class="card-b" style="display:grid;gap:12px">' + st.notices.filter(function (n) { return n.must; }).map(function (n) { return '<a href="#notice/' + n.id + '" style="color:inherit;text-decoration:none"><div class="row"><b class="grow">' + esc(n.title) + (n.ver > 1 ? ' <span class="badge acc">v' + n.ver + '</span>' : '') + '</b><span class="small">' + n.seen + '/' + n.total + '</span></div><div class="bar" style="margin-top:4px"><i style="width:' + Math.round(n.seen / n.total * 100) + '%"></i></div>' + (n.ver > 1 ? '<div class="small muted" style="margin-top:4px">바뀐 부분 재확인 ' + n.seenV2 + '/' + n.seen + '</div>' : '') + '</a>'; }).join('') + '</div></section>' +
      '<section class="card"><div class="card-h"><h2>이번 주 특별실·행사 충돌</h2><span class="badge ' + (conflicts.length ? 'red' : 'ok') + '">' + conflicts.length + '건</span></div><div class="card-b">' + (conflicts.length ? conflicts.map(function (p) { return '<div class="warn" style="margin:0 0 8px">' + TR.fmt(p[0].date) + ' <b>' + esc(p[0].place) + '</b> · ' + esc(p[0].title) + '(' + esc(TR.slotText(p[0])) + ') ↔ ' + esc(p[1].title) + '(' + esc(TR.slotText(p[1])) + ')</div>'; }).join('') : '<p class="muted">겹치는 일정이 없어요.</p>') + '<p class="small muted" style="margin:6px 0 0">한 줄 입력으로 일정을 넣을 때도 같은 장소·교시가 겹치면 바로 경고합니다.</p></div></section></div>';
    $$('[data-cell]', view).forEach(function (el) {
      el.onclick = function () {
        var key = el.dataset.cell, k = key.split('-')[1];
        var free = ['노태윤', '유가람', '차민호', '하지원', '석가영'];
        modal('<div class="card-h"><h2>' + k + '교시 보강 배정</h2><button class="btn sm ghost" data-close>✕</button></div><div class="card-b"><p class="small muted" style="margin-top:0">' + k + '교시에 수업이 없는 선생님 (시간표 기준)</p><div style="display:grid;gap:6px">' + free.map(function (f, i) { return '<button class="btn" data-f="' + f + '" style="justify-content:space-between">' + f + '<span class="small muted">이번 주 보강 ' + i + '회</span></button>'; }).join('') + '</div><p class="small muted">배정하면 그 선생님 「오늘」 시간표 맨 위에 보강이 뜨고, 쉬는시간에 1회 알림이 갑니다.</p></div>');
        $$('[data-f]', $('#modal')).forEach(function (b) { b.onclick = function () { assigned[key] = b.dataset.f; closeModal(); TR.toast(b.dataset.f + ' 선생님께 ' + k + '교시 보강을 배정했어요.'); school(); }; });
      };
    });
    $$('[data-rem]', view).forEach(function (b) { b.onclick = function () { var c = st.collections.find(function (x) { return x.id === b.dataset.rem; }); var miss = c.rows.filter(function (r) { return !r.done; }); TR.copy('[티로드] 「' + c.title + '」 미제출: ' + miss.map(function (r) { return r.who; }).join(', ') + ' · 마감 ' + c.due, '미제출 ' + miss.length + '명 리마인드 문구를 복사했어요.'); }; });
  }

  // ---------- 시작하기 ----------
  var wizStep = 1;
  var DUTY_SAMPLE = '부서\t업무\t담당\n교무기획부\t학사일정·교육과정\t한상우\n교무기획부\t학적·전입학\t강지은\n연구부\t연수·수업공개\t오지현\n생활인성부\t학교폭력·생활지도\t박지훈\n방과후돌봄부\t방과후학교\t정하은\n방과후돌봄부\t돌봄교실\t석가영\n정보과학부\t정보보안·기기\t차민호\n체육건강부\t보건·체육행사\t노태윤\n행정실\t시설·예산\t(행정실장)';
  var TIME_SAMPLE = '1교시\t09:00\t09:40\n2교시\t09:50\t10:30\n중간놀이\t10:30\t10:50\n3교시\t10:50\t11:30\n4교시\t11:40\t12:20\n점심\t12:20\t13:10\n5교시\t13:10\t13:50\n6교시\t14:00\t14:40';
  function start() {
    var steps = ['업무분장 붙여넣기', '시정표 붙여넣기', '선생님 초대'];
    var body = '';
    if (wizStep === 1) body = '<h2 style="font-size:17px">업무분장표를 붙여넣으세요</h2><p class="muted">2월에 만드는 그 표 그대로. 부서와 담당 선생님이 자동으로 만들어지고, 취합 대상을 "부서·업무"로 고를 수 있게 됩니다. 가입 승인은 없어요.</p><textarea class="paste" id="wp">' + DUTY_SAMPLE + '</textarea><div id="wr" style="margin-top:12px"></div>';
    if (wizStep === 2) body = '<h2 style="font-size:17px">시정표를 붙여넣으세요</h2><p class="muted">교시 축 화면과 <b>묶음 알림 시각</b>이 여기서 정해집니다. 수업 중에는 알림을 보내지 않습니다.</p><textarea class="paste" id="wp">' + TIME_SAMPLE + '</textarea><div id="wr" style="margin-top:12px"></div>';
    if (wizStep === 3) body = '<h2 style="font-size:17px">선생님 초대</h2><p class="muted">학교 코드를 말로 전할 필요가 없습니다. 링크를 쪽지로 보내거나 교무실에 QR을 붙여 두세요. 처음 들어올 때 명부에서 이름만 고르면 됩니다.</p><div class="row" style="gap:20px;align-items:flex-start;flex-wrap:wrap"><canvas class="qr" id="qr" width="29" height="29" aria-label="초대 QR 예시"></canvas><div class="grow" style="min-width:240px"><div class="msg">[티로드] 우리 학교 티로드가 열렸어요.\n오늘 일과·바뀐 것·내 마감을 한 화면에서 봅니다.\n가입 없이 이름만 고르면 끝 → https://t.road/j/TRD-7Q2K</div><div class="row" style="margin-top:10px"><button class="btn pri" id="cpj">쪽지 문구 복사</button><button class="btn" onclick="window.print()">QR 인쇄</button></div><p class="small muted">학교 도메인 메일 인증은 선택입니다 (초등은 업무용 메일 보유율이 낮음).</p></div></div>';
    view.innerHTML = '<div class="page-h"><div><h1>새 학년 세팅</h1><p>2월 셋째 주, 10분이면 끝나는 3단계. 고티처처럼 엑셀 매칭·임시 비밀번호가 필요 없습니다.</p></div><a class="btn" href="#school">현황판으로</a></div>' +
      '<div class="wiz"><nav class="wiz-steps card" style="padding:8px">' + steps.map(function (s, i) { return '<button class="' + (wizStep === i + 1 ? 'on' : '') + '" data-s="' + (i + 1) + '"><i>' + (i + 1) + '</i>' + s + '</button>'; }).join('') + '</nav>' +
      '<section class="card"><div class="card-b">' + body + '<div class="row" style="justify-content:flex-end;margin-top:16px">' + (wizStep > 1 ? '<button class="btn" data-s="' + (wizStep - 1) + '">이전</button>' : '') + (wizStep < 3 ? '<button class="btn pri" data-s="' + (wizStep + 1) + '">다음</button>' : '<a class="btn pri" href="#today">완료 — 오늘 화면 보기</a>') + '</div></div></section></div>';
    $$('[data-s]', view).forEach(function (b) { b.onclick = function () { wizStep = +b.dataset.s; start(); }; });
    var wp = $('#wp');
    function an() {
      var lines = wp.value.split(/\r?\n/).filter(function (l) { return l.trim(); }).map(function (l) { return l.split(/\t|\s{2,}|\|/).map(function (x) { return x.trim(); }); });
      if (wizStep === 1) {
        var rows = lines.slice(1), depts = {}; rows.forEach(function (r) { depts[r[0]] = (depts[r[0]] || 0) + 1; });
        var people = rows.map(function (r) { return r[2]; }).filter(function (x) { return x && x[0] !== '('; });
        $('#wr').innerHTML = '<div class="proto-note">부서 <b>' + Object.keys(depts).length + '개</b>, 업무 <b>' + rows.length + '개</b>, 선생님 <b>' + people.length + '명</b>을 찾았어요. ' + Object.keys(depts).map(function (d) { return '<span class="badge pri">' + esc(d) + ' ' + depts[d] + '</span>'; }).join(' ') + '<br>명부에 없는 이름은 담임 배정표(다음 해 자동 이월)에서 채워집니다.</div>';
      } else {
        var cls = lines.filter(function (r) { return /교시/.test(r[0]); });
        var breaks = []; for (var i = 0; i < lines.length - 1; i++) if (/교시/.test(lines[i][0])) breaks.push(lines[i][2]);
        $('#wr').innerHTML = '<div class="proto-note">수업 <b>' + cls.length + '교시</b>를 찾았어요. 묶음 알림: <b>08:20</b> · <b>12:20</b>(점심) · <b>14:50</b>(방과후). 쉬는시간(' + breaks.slice(0, 4).join(', ') + ')에는 당일 보강 알림만 갑니다.</div>';
      }
    }
    if (wp) { wp.addEventListener('input', an); an(); }
    var qr = $('#qr'); if (qr) { var g = qr.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, 29, 29); g.fillStyle = '#1B2430'; var seed = 7; for (var y = 0; y < 29; y++) for (var x = 0; x < 29; x++) { seed = (seed * 9301 + 49297) % 233280; if (seed / 233280 > .52) g.fillRect(x, y, 1, 1); } [[0, 0], [22, 0], [0, 22]].forEach(function (p) { g.fillStyle = '#fff'; g.fillRect(p[0], p[1], 7, 7); g.fillStyle = '#1B2430'; g.fillRect(p[0], p[1], 7, 7); g.fillStyle = '#fff'; g.fillRect(p[0] + 1, p[1] + 1, 5, 5); g.fillStyle = '#0F6E6E'; g.fillRect(p[0] + 2, p[1] + 2, 3, 3); }); }
    var cpj = $('#cpj'); if (cpj) cpj.onclick = function () { TR.copy($('.msg').textContent); };
  }

  // ---------- 한 줄 입력 ----------
  var qi = $('#qi'), qpop = $('#qpop'), qOver = {};
  function hideQ() { qpop.classList.remove('show'); }
  function qParsed() { var p = TR.parse(qi.value); Object.keys(qOver).forEach(function (k) { p[k] = qOver[k]; }); if (!p.date) p.date = TR.TODAY; return p; }
  function renderQ() {
    if (!qi.value.trim()) { hideQ(); return; }
    var p = qParsed();
    var chip = function (k, label, val, cls, miss) { return '<span class="chip ' + cls + (miss ? ' missing' : '') + '" data-k="' + k + '" tabindex="0" role="button"><b>' + label + '</b>' + esc(val) + '</span>'; };
    var ev = { id: '_new', date: p.date, s: p.s, e: p.e, place: p.place };
    var conf = TR.conflicts(ev);
    qpop.innerHTML = '<div class="chips">' +
      chip('title', '제목', p.title || '(제목)', 'k-type', !p.title) +
      chip('date', '날짜', TR.fmt(p.date) + (TR.parse(qi.value).date ? '' : ' (오늘)'), 'k-date') +
      chip('s', '시간', p.s ? TR.slotText({ s: p.s, e: p.e, time: p.time }) + (p.time && p.s !== 'pm' && p.s !== 'am' ? ' ' + p.time : '') : (p.time || '교시 없음'), 'k-period', !p.s && !p.time) +
      chip('target', '대상', p.target || '대상 없음', 'k-target', !p.target) +
      chip('place', '장소', p.place || '장소 없음', 'k-place', !p.place) +
      (p.who ? chip('who', '참석', p.who.join(', '), 'k-who') : '') +
      chip('type', '유형', (p.duty ? '복무·' + p.duty : TR.typeLabel[p.type]), 'k-type') + '</div>' +
      (conf.length ? '<div class="warn">⚠ ' + esc(p.place) + '이(가) 겹쳐요: ' + conf.map(function (c) { return esc(c.title) + ' (' + esc(TR.slotText(c)) + ', ' + esc(c.by) + ')'; }).join(', ') + '</div>' : '') +
      '<div class="hint"><span>칩을 눌러 고칠 수 있어요 · 교시/장소/대상은 학교 사전(특별실 ' + TR.rooms.length + '곳·교직원 ' + TR.staff.length + '명)으로 알아봅니다</span><span><kbd>Enter</kbd> 저장 <kbd>Esc</kbd> 닫기</span></div>';
    qpop.classList.add('show');
    $$('.chip', qpop).forEach(function (c) {
      c.onclick = function () {
        var k = c.dataset.k; if (k === 'type') { var order = ['event', 'work', 'meet', 'duty']; qOver.type = order[(order.indexOf(p.type) + 1) % 4]; renderQ(); return; }
        var cur = k === 'date' ? p.date : k === 's' ? (p.s ? p.s + (p.e && p.e !== p.s ? '~' + p.e : '') : '') : k === 'who' ? (p.who || []).join(',') : (p[k] || '');
        c.outerHTML = '<input class="chip" data-edit="' + k + '" value="' + esc(cur) + '" style="width:150px" aria-label="' + k + ' 수정">';
        var inp = $('[data-edit]', qpop); inp.focus(); inp.select();
        var commit = function () { var v = inp.value.trim(); if (k === 's') { var m = v.split('~'); qOver.s = m[0]; qOver.e = m[1] || m[0]; } else if (k === 'who') qOver.who = v ? v.split(/[,\s]+/) : undefined; else qOver[k] = v || undefined; renderQ(); qi.focus(); };
        inp.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); commit(); } };
        inp.onblur = commit;
      };
    });
  }
  qi.addEventListener('input', function () { qOver = {}; renderQ(); });
  qi.addEventListener('focus', renderQ);
  qi.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && qi.value.trim()) {
      var p = qParsed();
      var ev = { id: 'u' + Date.now().toString(36), date: p.date, s: p.s || 'pm', e: p.e || p.s || 'pm', time: p.time, title: p.title || '새 일정', place: p.place || '', target: p.target || '', type: p.type, by: TR.me.name, changed: true, change: '<ins>새로 등록</ins> · ' + TR.me.name };
      if (p.duty) { ev.duty = p.duty; ev.who = (p.who && p.who[0]) || TR.me.name; ev.time = p.time || TR.slotText(ev); ev.title = ev.who + ' ' + p.duty; }
      S().events.push(ev); TR.save();
      qi.value = ''; qOver = {}; hideQ();
      TR.toast(TR.fmt(ev.date) + ' ' + TR.slotText(ev) + ' 「' + ev.title + '」 등록 — 학교 일과와 바뀐 것에 반영');
      if (ev.date === TR.TODAY) { location.hash = '#today'; route(); } else { calMode = 'week'; if (location.hash === '#cal') cal(); else location.hash = '#cal'; }
    }
  });
  document.addEventListener('click', function (e) { if (!e.target.closest('.qi')) hideQ(); });
  document.addEventListener('keydown', function (e) {
    if (e.target.matches('input,textarea,select')) return;
    if (e.key === '/' || e.key === 'n' || e.key === 'N') { e.preventDefault(); qi.focus(); }
    if (e.key === 't' || e.key === 'T') location.hash = '#today';
  });

  $('#who').addEventListener('change', function () {
    role = this.value;
    $('.avatar').textContent = role === 'vp' ? '이' : '정';
    location.hash = role === 'vp' ? '#school' : '#today';
    TR.toast(role === 'vp' ? '교감(이정민) 화면으로 바꿨어요.' : '담임(정하은) 화면으로 바꿨어요.');
  });
  $('#reset').addEventListener('click', function () { TR.reset(); assigned = { 'd1-5': '정하은' }; route(); TR.toast('시연 데이터를 처음 상태로 되돌렸어요.'); });

  route();
})();
