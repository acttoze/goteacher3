// 티로드 프로토타입 — 화면 라우팅과 렌더링
(function () {
  var S = function () { return TR.state; };
  var esc = TR.esc, $ = TR.$, $$ = TR.$$;
  var view = $('#view');
  var role = 'teacher';

  // ---------- 라우터 ----------
  // 교내(학교 이름) · 교외(티로드) 두 공간. 외부인이 들어오는 기능은 전부 #out/ 아래에만 둔다
  var lastIn = '#today';
  function route() {
    var h = (location.hash || '#today').slice(1).split('/');
    if (h[0] === 'board' && h[1] === 'forms') { location.replace('#out/forms' + (h[2] ? '/' + h[2] : '')); return; }
    if (role === 'vendor' && h[0] !== 'out') { location.replace('#out/it'); return; }
    var zone = h[0] === 'out' ? 'out' : 'in';
    if (zone === 'in') lastIn = location.hash || '#today';
    setZone(zone, h[1] || 'forms');
    var nav = h[0] === 'notice' ? 'board' : zone === 'out' ? 'out/' + (h[1] || 'forms') : h[0];
    $$('.nav a').forEach(function (a) { a.classList.toggle('on', a.dataset.r === nav); });
    var fn = { today: today, cal: cal, collect: h[1] === 'new' ? collectNew : h[1] ? collectDetail : collectList, notice: h[1] ? noticeDetail : function () { board('notice'); }, board: board, school: school, start: start, out: outZone }[h[0]] || today;
    view.innerHTML = '';
    fn(h[1], h[2]);
    window.scrollTo(0, 0);
    updateDots();
  }
  window.addEventListener('hashchange', route);
  TR.onSync(function () { var h = location.hash; if (/collect\/|notice\/|board|today|school|out\//.test(h || '#today')) softRender(); });
  function softRender() { var y = window.scrollY; var h = (location.hash || '#today').slice(1).split('/'); var fn = { today: today, collect: h[1] && h[1] !== 'new' ? collectDetail : null, notice: h[1] ? noticeDetail : null, board: board, school: school, out: outZone }[h[0]]; if (!fn) return; view.innerHTML = ''; fn(h[1], h[2], true); window.scrollTo(0, y); updateDots(); }

  function updateDots() {
    var n = S().notices.some(function (x) { return x.must && !S().acked[x.id]; });
    $('.nav a[data-r="board"] .dot').hidden = !n;
    var c = S().collections.some(function (c) { return !c.mine && c.rows.some(function (r) { return mineRow(r) && !r.done; }); });
    $('.nav a[data-r="collect"] .dot').hidden = !c;
  }
  function mineRow(r) { return TR.rowMine(r); }

  // ---------- 공통 조각 ----------
  // 카드: 흰 바탕 + 유형 색 점. 면 색은 '나와 관련'에만, 최근 변경은 아이콘. (인쇄는 흑백 기호)
  function evCard(ev, opts) {
    opts = opts || {};
    var meta = [ev.end ? TR.dateText(ev) : '', ev.s || !opts.noTime ? TR.slotText(ev) : '', ev.place, ev.target].filter(Boolean).join(' · ');
    return '<div class="ev ' + ev.type + (TR.isRel(ev) ? ' rel' : '') + (ev.changed ? ' changed' : '') + '" data-ev="' + ev.id + '" title="' + esc(ev.title) + '">' +
      '<div class="t"><i class="dt" aria-hidden="true"></i><span class="psym">' + TR.typeSym[ev.type] + '</span><span class="sr">' + TR.typeLabel[ev.type] + '</span><span>' + esc(ev.title) + '</span>' + (ev.changed ? TR.chgIcon : '') + attMark(ev) + '</div>' +
      (opts.compact ? (opts.time || opts.range ? '<div class="m">' + esc([opts.range ? TR.dateText(ev) : '', opts.time ? TR.slotText(ev) : ''].filter(Boolean).join(' · ')) + '</div>' : '') : '<div class="m">' + esc(meta) + (ev.note ? ' · ' + esc(ev.note) : '') + '</div>') +
      (ev.type === 'duty' ? '' : '<div class="by">' + esc(TR.byline(ev.by || TR.me.name)) + '</div>') + '</div>';
  }
  function attMark(ev) { return (ev.body ? '<span class="att" title="내용 있음">≡</span>' : '') + (ev.files ? '<span class="att" title="첨부 ' + ev.files.length + '개">📎' + ev.files.length + '</span>' : ''); }
  function legendHtml(extra) {
    return '<span class="legend">' + ['event', 'work', 'meet'].map(function (t) { return '<span><i class="dt ' + t + '"></i>' + TR.typeLabel[t] + '</span>'; }).join('') +
      '<span><i class="relsw"></i>나와 관련</span><span class="lg-chg">' + TR.chgIcon + ' 최근 변경</span>' + (extra || '') + '</span>';
  }
  function money(n) { return (+n || 0).toLocaleString('ko-KR'); }
  function progress(c) {
    if (c.kind === 'matrix') { // 칸 단위: 연수 × 사람
      var t = 0, d = 0; c.rows.forEach(function (r) { r.v.forEach(function (x) { t++; if (x) d++; }); });
      return { d: d, t: t, p: Math.round(d / t * 100), unit: '칸', people: c.rows.filter(function (r) { return r.done; }).length };
    }
    var dn = c.rows.filter(function (r) { return r.done; }).length;
    var a = c.aud || {};
    return { d: dn, t: c.rows.length, p: Math.round(dn / c.rows.length * 100), unit: c.kind === 'items' || a.hrOnly ? '반' : a.mode === 'pergroup' ? '그룹' : '명' };
  }
  var dragEnd = 0;
  function bindEvClicks(root) {
    $$('[data-ev]', root).forEach(function (el) {
      el.addEventListener('click', function () { if (Date.now() - dragEnd < 400) return; openEvent(el.dataset.ev); });
    });
  }
  // 날짜·기간·블록 바꾸기. w = { date, end, a, b } 중 바꿀 것만. a === null 이면 전일(시간 미정)로.
  // 이미 정해진 날짜·시간을 바꾸면 '바뀐 것'으로 알리고, 시간 미정이던 일정에 처음 블록을 정하는 건 알리지 않는다.
  function setWhen(ev, w) {
    var bD = TR.dateText(ev), bT = ev.s ? TR.slotText(ev) : null;
    if (w.date) { ev.date = w.date; if (w.end && w.end > w.date) ev.end = w.end; else delete ev.end; }
    if (w.a === null) { delete ev.s; delete ev.e; delete ev.time; }
    else if (w.a !== undefined) { var a = Math.min(w.a, w.b), b = Math.max(w.a, w.b); if (ev.s !== TR.slots[a].k || ev.e !== TR.slots[b].k) delete ev.time; ev.s = TR.slots[a].k; ev.e = TR.slots[b].k; }
    var aD = TR.dateText(ev), aT = ev.s ? TR.slotText(ev) : '시간 미정(전일)';
    if (bD === aD && (bT || '시간 미정(전일)') === aT) return;
    var parts = [];
    if (bD !== aD) parts.push((/~/.test(bD + aD) ? '기간' : '날짜') + ' <del>' + esc(bD) + '</del> → <ins>' + esc(aD) + '</ins>');
    if (bT && bT !== aT) parts.push('시간 <del>' + esc(bT) + '</del> → <ins>' + esc(aT) + '</ins>');
    if (parts.length) { ev.changed = true; ev.change = parts.join(', ') + ' · ' + esc(TR.me.name); }
    TR.save();
    TR.toast('「' + ev.title + '」 ' + (bD !== aD ? aD + ' ' : '') + aT + (parts.length ? ' · 관련 선생님 「바뀐 것」에 알려요' : ''));
  }
  // 칸에 바로 쓰기(오늘·주간·월간 공통): 칸의 [＋ 추가]를 누르면 그 자리에 2줄 입력창이 열린다.
  // Enter = 저장, Shift+Enter = 줄바꿈, Esc = 취소, 바깥을 누르면 저장. 우하단 [파일첨부]·[내용입력]은 필요한 사람만.
  function cellInput(o) {
    var box = document.createElement('div'), files = [], fin = false;
    box.className = 'newbox no-print' + (o.cls ? ' ' + o.cls : '');
    box.innerHTML = '<textarea class="nb-t" rows="2" placeholder="' + esc(o.ph) + '"></textarea>' +
      '<textarea class="nb-b" rows="4" placeholder="내용 — 준비물·참고 사항 등 (Ctrl+Enter 저장)" hidden></textarea><div class="nb-f"></div>' +
      '<div class="nb-bar"><span class="nb-k">Enter 저장 · Esc 취소</span><label class="nb-btn">파일첨부<input type="file" multiple hidden></label><button type="button" class="nb-btn" data-body>내용입력</button></div>';
    o.place(box);
    var t = $('.nb-t', box), bd = $('.nb-b', box);
    t.focus();
    function chips() {
      $('.nb-f', box).innerHTML = files.map(function (f, i) { return '<span class="fchip">📎 ' + esc(f.name) + ' <small>' + f.size + '</small><button type="button" data-rm="' + i + '" aria-label="빼기">×</button></span>'; }).join('');
      $$('[data-rm]', box).forEach(function (b) { b.onclick = function () { files.splice(+b.dataset.rm, 1); chips(); t.focus(); }; });
    }
    function end(save) {
      if (fin) return; fin = true; document.removeEventListener('pointerdown', outside, true);
      var title = t.value.replace(/\s+/g, ' ').trim(), body = bd.value.trim();
      if (!title && body) title = body.split('\n')[0].slice(0, 40);
      if (save && title) o.save({ text: title, body: body, files: files }); else o.cancel();
    }
    function outside(e) { if (!box.contains(e.target)) end(true); }
    document.addEventListener('pointerdown', outside, true);
    t.onkeydown = function (e) {
      if (e.isComposing) return; // 한글 조합 중 Enter는 글자 확정
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); end(true); }
      if (e.key === 'Escape') { e.stopPropagation(); end(false); }
    };
    bd.onkeydown = function (e) {
      if (e.isComposing) return;
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); end(true); }
      if (e.key === 'Escape') { e.stopPropagation(); end(false); }
    };
    $('[data-body]', box).onclick = function () { bd.hidden = !bd.hidden; this.classList.toggle('on', !bd.hidden); (bd.hidden ? t : bd).focus(); };
    $('input[type=file]', box).onchange = function () {
      [].forEach.call(this.files, function (f) { files.push({ name: f.name, size: f.size < 1048576 ? Math.max(1, Math.round(f.size / 1024)) + 'KB' : (f.size / 1048576).toFixed(1) + 'MB' }); });
      this.value = ''; chips(); t.focus();
    };
  }
  // 입력한 문장으로 일정 만들기. 문장에 날짜·블록이 있으면 그것이 이기고, 없으면 누른 칸의 날짜·블록.
  function addEvent(r, date, slot) {
    var p = TR.parse(r.text), ev = { id: 'u' + Date.now().toString(36), date: p.date || date, end: p.end, title: p.title || r.text, place: p.place || '', target: p.target || '', type: p.type === 'duty' ? 'work' : p.type, by: TR.me.name, mine: true };
    if (!ev.end) delete ev.end;
    if (p.s) { ev.s = p.s; ev.e = p.e || p.s; if (p.time) ev.time = p.time; } else if (slot !== null) { ev.s = ev.e = TR.slots[slot].k; }
    if (r.body) ev.body = r.body;
    if (r.files.length) ev.files = r.files;
    S().events.push(ev); TR.save();
    return ev;
  }
  function openEvent(id) {
    var ev = S().events.find(function (x) { return x.id === id; }); if (!ev) return;
    var conf = TR.conflicts(ev), edit = TR.canEdit(ev);
    var a = ev.s ? TR.slotIdx(ev.s) : 0, b = ev.s ? TR.slotIdx(ev.e || ev.s) : a;
    var opt = function (sel) { return TR.slots.map(function (s, i) { return '<option value="' + i + '"' + (i === sel ? ' selected' : '') + '>' + s.label + '</option>'; }).join(''); };
    var dIn = function (id, v) { return '<input type="date" id="' + id + '" class="btn sm" value="' + v + '">'; };
    var dateDd = edit ? '<div class="row" style="flex-wrap:wrap">' + dIn('mD', ev.date) + '~' + dIn('mDE', TR.lastDay(ev)) + '<span class="small muted">하루짜리는 두 날짜를 같게</span></div>' : esc(TR.dateText(ev));
    var timeDd = edit ? '<div class="row" style="flex-wrap:wrap"><label class="toggle"><input type="checkbox" id="mAll"' + (ev.s ? '' : ' checked') + '> 시간 미정(전일)</label><span id="mRange" class="row"' + (ev.s ? '' : ' hidden') + '><select id="mS" class="btn sm">' + opt(a) + '</select>~<select id="mE" class="btn sm">' + opt(b) + '</select></span></div><div class="small muted" style="margin-top:7px">「오늘」·「일정」에서 카드를 끌어 옮기거나, 끝을 끌어 늘리고 줄여도 됩니다.</div>'
      : esc(TR.slotText(ev)) + (ev.s ? '' : ' <span class="small muted">· 작성자·같은 그룹 선생님이 정할 수 있어요</span>');
    modal('<div class="card-h"><h2>' + esc(ev.title) + '</h2><span class="badge">' + TR.typeLabel[ev.type] + '</span></div><div class="card-b">' +
      '<dl class="kv"><dt>날짜</dt><dd>' + dateDd + '</dd><dt>시간</dt><dd>' + timeDd + '</dd>' +
      (ev.place ? '<dt>장소</dt><dd>' + esc(ev.place) + '</dd>' : '') + (ev.target ? '<dt>대상</dt><dd>' + esc(ev.target) + '</dd>' : '') +
      '<dt>등록</dt><dd>' + esc(TR.byline(ev.by || ev.who || TR.me.name)) + '</dd>' + (ev.note ? '<dt>메모</dt><dd>' + esc(ev.note) + '</dd>' : '') +
      (edit ? '<dt>내용</dt><dd><textarea id="mBody" class="mbody" rows="3" placeholder="준비물·참고 사항 등">' + esc(ev.body || '') + '</textarea></dd>' : ev.body ? '<dt>내용</dt><dd style="white-space:pre-wrap">' + esc(ev.body) + '</dd>' : '') +
      (ev.files ? '<dt>첨부</dt><dd>' + ev.files.map(function (f) { return '<span class="fchip">📎 ' + esc(f.name) + ' <small>' + esc(f.size) + '</small></span>'; }).join(' ') + '</dd>' : '') + '</dl>' +
      (ev.changed ? '<div class="diff" style="margin-top:14px"><b>바뀐 부분</b><div>' + ev.change + '</div></div>' : '') +
      (conf.length ? '<div class="warn">장소가 겹쳐요: ' + conf.map(function (c) { return esc(c.title) + '(' + esc(TR.slotText(c)) + ')'; }).join(', ') + '</div>' : '') +
      '<div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn sm" data-copy>메신저용 문구 복사</button><button class="btn sm" data-close>닫기</button>' + (edit ? '<button class="btn sm pri" id="mSave">저장</button>' : '') + '</div></div>');
    $('[data-copy]', $('#modal')).onclick = function () { TR.copy('[티로드] ' + TR.dateText(ev) + ' ' + TR.slotText(ev) + ' ' + ev.title + (ev.place ? ' · ' + ev.place : '') + '\nhttps://t.road/s/' + ev.id); };
    if (edit) {
      $('#mAll').onchange = function () { $('#mRange').hidden = this.checked; };
      $('#mSave').onclick = function () {
        var body = $('#mBody').value.trim(); if (body) ev.body = body; else delete ev.body; TR.save();
        var d = $('#mD').value || ev.date, de = $('#mDE').value || d; if (de < d) { var t = d; d = de; de = t; }
        setWhen(ev, $('#mAll').checked ? { date: d, end: de, a: null } : { date: d, end: de, a: +$('#mS').value, b: +$('#mE').value }); closeModal(); route();
      };
    }
  }
  function modal(html) {
    var bg = $('#modal'); bg.innerHTML = '<div class="card modal" role="dialog" aria-modal="true">' + html + '</div>'; bg.classList.add('show');
    $$('[data-close]', bg).forEach(function (b) { b.onclick = closeModal; });
    bg.onclick = function (e) { if (e.target === bg) closeModal(); };
  }
  function closeModal() { $('#modal').classList.remove('show'); $('#modal').innerHTML = ''; }
  TR.closeModal = closeModal;
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeModal(); hideQ(); } });

  // 학교 업무는 PC에서만 한다 → 받는 화면도 PC 브라우저 창으로 보여 준다
  function pcWin(src, url, h, id) {
    return '<div class="pcwin" style="height:' + (h || 713) + 'px"><div class="pcbar"><i></i><i></i><i></i><span>' + esc(url) + '</span></div><iframe' + (id ? ' id="' + id + '"' : '') + ' src="' + src + '" title="받는 사람 PC 화면"></iframe></div>';
  }
  function recvModal(src, title) {
    modal('<div class="card-h"><h2>' + esc(title) + '</h2><button class="btn sm ghost" data-close aria-label="닫기">✕</button></div><div class="card-b" style="background:var(--surface-2)">' + pcWin(src, 't.road/c/k7Q2', 690) + '</div><div class="card-b small muted">쿨메신저 쪽지의 링크를 누르면 PC 브라우저에서 열리는 화면입니다. 로그인하지 않아도 됩니다.</div>');
    $('.modal').style.width = '828px';
  }

  // ---------- 나에게 온 것 / 학교 전체 ----------
  var inboxScope = 'me';
  function myInbox() {
    var st = S(), inbox = [];
    st.collections.forEach(function (c) {
      if (c.mine) {
        var p = progress(c);
        inbox.push({ k: 'send', id: c.id, title: c.title, meta: '내가 보낸 요청 · ' + (c.due ? '마감 ' + c.due : '마감 미정 — 눌러서 정하기'), p: p });
        var myRow = c.rows.find(mineRow);
        if (myRow && !myRow.done) inbox.push({ k: 'col', id: c.id, unit: myRow.unit, title: c.title + ' — ' + (myRow.gid ? myRow.unit + ' 칸' : /^\d-\d$/.test(myRow.unit) ? '우리 반 칸' : '내 칸'), meta: TR.dueText(c) + ' · 내가 만든 요청', urgent: false });
      } else {
        var r = c.rows.find(mineRow);
        if (r) inbox.push({ k: 'col', id: c.id, unit: r.unit, title: c.title + (r.gid ? ' — ' + r.unit + ' 칸' : ''), meta: c.dept + ' ' + c.owner + ' · ' + (c.due ? '마감 ' + c.due : '마감 미정'), why: TR.whyMine(c, r) + (r.gid && r.done && r.by ? ' · ' + r.by + ' 선생님이 냄' : ''), urgent: c.urgent, done: r.done });
      }
    });
    st.notices.forEach(function (n) { if (n.must) inbox.push({ k: 'ack', id: n.id, title: n.title + (n.ver > 1 ? ' v' + n.ver : ''), meta: '확인 필수 · ' + n.dept, done: !!st.acked[n.id] }); });
    inbox.sort(function (a, b) { return (a.done ? 1 : 0) - (b.done ? 1 : 0) || (b.urgent ? 1 : 0) - (a.urgent ? 1 : 0); });
    return inbox;
  }
  function schoolWideHtml() {
    var st = S();
    var cols = st.collections.slice().sort(function (a, b) { return (a.dueISO || 'z') < (b.dueISO || 'z') ? -1 : (a.dueISO || 'z') > (b.dueISO || 'z') ? 1 : 0; }).map(function (c) {
      var p = progress(c), r = c.rows.find(mineRow);
      var mineTag = c.mine ? '<span class="badge pri">내가 보냄</span>' : r ? (r.done ? '<span class="badge ok">내 칸 제출</span>' : '<span class="badge red">내 칸 비었음</span>') : '';
      return '<a class="it" href="#collect/' + c.id + '" style="text-decoration:none;color:inherit"><span class="badge' + (c.urgent ? ' red' : '') + '" style="margin-top:1px">마감</span><div class="grow"><div class="ti">' + esc(c.title) + ' ' + mineTag + '</div><div class="meta">' + esc(c.dept) + ' ' + esc(c.owner) + ' · ' + esc(TR.dueText(c)) + ' · 대상 ' + esc(c.aud ? TR.audText(c.aud) : c.target) + '</div><div class="row" style="margin-top:7px"><div class="bar grow' + (c.urgent ? ' red' : '') + '"><i style="width:' + p.p + '%"></i></div><span class="small">' + p.d + '/' + p.t + '</span></div></div></a>';
    }).join('');
    var nts = st.notices.filter(function (n) { return n.must; }).map(function (n) {
      return '<a class="it" href="#notice/' + n.id + '" style="text-decoration:none;color:inherit"><span class="badge pri" style="margin-top:1px">확인</span><div class="grow"><div class="ti">' + esc(n.title) + (n.ver > 1 ? ' <span class="badge acc">v' + n.ver + '</span>' : '') + (st.acked[n.id] ? ' <span class="badge ok">나 확인함</span>' : '') + '</div><div class="meta">' + esc(n.dept) + ' ' + esc(n.by) + ' · 확인 ' + n.seen + '/' + n.total + '</div><div class="bar" style="margin-top:7px"><i style="width:' + Math.round(n.seen / n.total * 100) + '%"></i></div></div></a>';
    }).join('');
    return cols + nts;
  }

  // ---------- 오늘 ----------
  // 학교 오늘 = [전일] + 블록 4칸. 날짜만 넣은 일정은 전일에 쌓이고, 필요한 사람이 끌어서 블록을 정한다.
  var SH = 120; // 블록 한 칸 높이(px) — style.css의 .tlx --sh와 맞춤
  function dayBoard(evs) {
    var all = evs.filter(TR.isAllDay), timed = evs.filter(function (e) { return !TR.isAllDay(e); });
    var lanes = [];
    timed.sort(function (a, b) { return TR.slotIdx(a.s) - TR.slotIdx(b.s) || (TR.slotIdx(b.e) - TR.slotIdx(a.e)); }).forEach(function (ev) {
      var a = TR.slotIdx(ev.s), b = TR.slotIdx(ev.e || ev.s);
      for (var i = 0; i < lanes.length; i++) if (lanes[i] < a) { lanes[i] = b; ev._lane = i; return; }
      lanes.push(b); ev._lane = lanes.length - 1;
    });
    var L = Math.max(lanes.length, 2);
    var h = '<div class="allday" id="allday"><div class="ad-l"><b>전일</b><span>시간 미정 · 종일</span></div><div class="ad-b">' +
      all.map(function (ev) { return '<div class="adw' + (TR.canEdit(ev) ? ' edit' : '') + '" data-w="' + ev.id + '">' + evCard(ev, { noTime: true }) + '</div>'; }).join('') +
      '<button class="ad-add no-print" id="adAdd">＋ 추가</button></div></div>';
    h += '<div class="tlx" id="tlx" style="--lanes:' + L + ';height:' + TR.slots.length * SH + 'px">';
    TR.slots.forEach(function (s, i) {
      h += '<div class="srow' + (i === TR.NOW_SLOT ? ' now' : '') + '" style="top:' + i * SH + 'px" data-slot="' + i + '" title="빈 곳을 두 번 누르면 이 블록에 카드를 만들어요"><span class="sl"><b>' + s.label + '</b>' + s.time + (i === TR.NOW_SLOT ? '<i class="nowtag">지금</i>' : '') + '</span><button class="cell-add sa no-print" data-slot="' + i + '" title="' + s.label + '에 일정 추가">＋ 추가</button></div>';
    });
    timed.forEach(function (ev) {
      var a = TR.slotIdx(ev.s), b = TR.slotIdx(ev.e || ev.s), ed = TR.canEdit(ev);
      h += '<div class="evw' + (ed ? ' edit' : '') + '" data-w="' + ev.id + '" style="--top:' + a + ';--span:' + (b - a + 1) + ';--lane:' + ev._lane + '">' +
        (ed ? '<i class="hdl t" data-h="t" title="끌어서 시작 블록 바꾸기"></i><i class="hdl b" data-h="b" title="끌어서 끝 블록 바꾸기"></i>' : '') + evCard(ev, { compact: b === a, time: false }) + '</div>';
    });
    return h + '<div class="ph" id="ph" hidden></div></div>';
  }
  function bindDayBoard(root, rerender) {
    var tlx = $('#tlx', root), ad = $('#allday', root), ph = $('#ph', root); if (!tlx) return;
    var N = TR.slots.length;
    var tip = $('#dragtip') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'dragtip', className: 'tip' }));
    function slotAt(y) { var r = tlx.getBoundingClientRect(); return Math.max(0, Math.min(N - 1, Math.floor((y - r.top) / SH))); }
    function inside(el, x, y) { var r = el.getBoundingClientRect(); return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom; }
    function label(a, b) { return TR.slotText({ s: TR.slots[a].k, e: TR.slots[b].k }); }
    function showPh(a, b) { ph.hidden = false; ph.style.top = (a * SH + 2) + 'px'; ph.style.height = ((b - a + 1) * SH - 4) + 'px'; }
    $$('.edit[data-w]', root).forEach(function (w) {
      w.addEventListener('pointerdown', function (e) {
        if (e.button !== 0) return;
        var ev = S().events.find(function (x) { return x.id === w.dataset.w; });
        var mode = e.target.dataset.h || 'move', timed = !TR.isAllDay(ev);
        var a0 = timed ? TR.slotIdx(ev.s) : null, b0 = timed ? TR.slotIdx(ev.e || ev.s) : null, a = a0, b = b0;
        var grab = timed ? slotAt(e.clientY) - a0 : 0, rect = w.getBoundingClientRect(), ox = e.clientX - rect.left, oy = e.clientY - rect.top;
        var x0 = e.clientX, y0 = e.clientY, moved = false, ghost = null, toAll = false;
        e.preventDefault();
        function move(m) {
          if (!moved && Math.abs(m.clientX - x0) + Math.abs(m.clientY - y0) < 5) return;
          if (!moved) {
            moved = true; w.classList.add('dragging'); document.body.classList.add('is-dragging');
            if (mode === 'move') { ghost = w.cloneNode(true); ghost.className = 'dragghost'; ghost.style.width = rect.width + 'px'; ghost.style.height = rect.height + 'px'; document.body.appendChild(ghost); }
          }
          if (mode === 'move') {
            ghost.style.left = (m.clientX - ox) + 'px'; ghost.style.top = (m.clientY - oy) + 'px';
            toAll = inside(ad, m.clientX, m.clientY); ad.classList.toggle('over', toAll);
            if (!toAll && inside(tlx, m.clientX, m.clientY)) { var span = timed ? b0 - a0 : 0; a = Math.max(0, Math.min(N - 1 - span, slotAt(m.clientY) - grab)); b = a + span; showPh(a, b); }
            else { ph.hidden = true; a = a0; b = b0; }
          } else if (mode === 't') { a = Math.min(slotAt(m.clientY), b0); w.style.setProperty('--top', a); w.style.setProperty('--span', b0 - a + 1); }
          else { b = Math.max(slotAt(m.clientY), a0); w.style.setProperty('--span', b - a0 + 1); }
          tip.textContent = toAll ? '전일(시간 미정)으로' : a === null ? '블록 칸에 놓으세요' : label(a, b);
          tip.style.left = (m.clientX + 14) + 'px'; tip.style.top = (m.clientY + 16) + 'px'; tip.classList.add('show');
        }
        function up() {
          document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', up);
          if (ghost) ghost.remove(); tip.classList.remove('show'); document.body.classList.remove('is-dragging');
          if (!moved) return; // 클릭 → 카드 열기(bindEvClicks)
          dragEnd = Date.now();
          if (toAll) { if (timed) setWhen(ev, { a: null }); }
          else if (a !== null && (a !== a0 || b !== b0)) setWhen(ev, { a: a, b: b });
          rerender();
        }
        document.addEventListener('pointermove', move); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', up);
      });
    });
    // 카드 만들기: 칸마다 있는 [＋ 추가](전일 맨 끝 · 블록 이름 아래) 또는 블록 줄 빈 곳 더블클릭.
    // [＋ 추가]는 카드 영역 밖(왼쪽 이름 칸)에 있어서 카드가 꽉 차도 가려지지 않는다. 쓴 문장에 블록이 있으면 그 블록으로 들어간다.
    function newCard(slot) {
      cellInput({
        ph: slot === null ? '제목 쓰고 Enter — 날짜는 오늘, 시간은 나중에' : TR.slots[slot].label + ' — 제목 쓰고 Enter',
        cls: slot === null ? '' : 'abs',
        place: function (box) { if (slot === null) $('#adAdd', root).replaceWith(box); else { box.style.top = (slot * SH + 6) + 'px'; tlx.appendChild(box); } },
        cancel: rerender,
        save: function (r) {
          var ev = addEvent(r, TR.TODAY, slot);
          TR.toast(ev.date !== TR.TODAY ? '「' + ev.title + '」 ' + TR.fmt(ev.date) + '에 만들었어요 (일정 화면에서 보여요)' : ev.s ? '「' + ev.title + '」 ' + TR.slotText(ev) + ' — 아래 끝을 끌면 길게 늘어나요' : '「' + ev.title + '」 전일에 만들었어요 — 끌어서 블록에 놓으면 시간이 정해져요');
          rerender();
        }
      });
    }
    $('#adAdd', root).onclick = function () { newCard(null); };
    $$('.srow .cell-add', tlx).forEach(function (b) { b.onclick = function () { newCard(+b.dataset.slot); }; });
    $$('.srow', tlx).forEach(function (r) { r.addEventListener('dblclick', function (e) { if (!e.target.closest('.cell-add')) newCard(+r.dataset.slot); }); });
  }

  function today() {
    var st = S(), todayEv = st.events.filter(function (e) { return TR.onDay(e, TR.TODAY); });
    var school = todayEv.filter(function (e) { return e.type !== 'duty'; });
    var duty = todayEv.filter(function (e) { return e.type === 'duty'; });
    var n2 = st.notices.find(function (n) { return n.id === 'n2'; });
    var changes = st.events.filter(function (e) { return e.changed && TR.lastDay(e) >= TR.TODAY; });
    var untimed = school.filter(function (e) { return !e.s; }).length;

    var mine = TR.myTimetable.map(function (t) {
      var i = TR.slotIdx(t.k);
      return '<tr class="' + (i === TR.NOW_SLOT ? 'now' : '') + '"><td class="p"><b>' + TR.slots[i].label + '</b>' + TR.slots[i].from + '</td><td><div class="sub">' + esc(t.sub) + '</div><div class="small muted">' + esc(t.room) + (t.note ? ' · ' + esc(t.note) : '') + '</div></td></tr>';
    }).join('');

    var dutyHtml = duty.map(function (d) {
      return '<div class="duty"><span class="k">' + d.duty + '</span><div class="grow"><div><b>' + esc(d.who) + '</b> <span class="muted small">' + esc(TR.groupOf(d.who)) + ' · ' + esc(d.time) + (d.target ? ' · ' + esc(d.target) : '') + '</span>' + (d.changed ? ' ' + TR.chgIcon : '') + '</div></div></div>';
    }).join('');

    var inbox = myInbox();
    var open = inbox.filter(function (i) { return !i.done && i.k !== 'send'; }).length;
    var inboxHtml = inboxScope === 'all' ? schoolWideHtml() : inbox.map(function (it) {
      if (it.k === 'send') return '<a class="it" href="#collect/' + it.id + '" style="text-decoration:none;color:inherit"><span class="badge pri" style="margin-top:1px">보냄</span><div class="grow"><div class="ti">' + esc(it.title) + '</div><div class="meta">' + esc(it.meta) + '</div><div class="row" style="margin-top:7px"><div class="bar grow"><i style="width:' + it.p.p + '%"></i></div><span class="small"><b>' + it.p.d + '</b>/' + it.p.t + '</span></div><div class="meta"><span class="need">' + (it.p.unit === '칸' ? '빈 칸 ' : '미제출 ') + (it.p.t - it.p.d) + it.p.unit + '</span> · 누르면 합본·독촉</div></div></a>';
      return '<div class="it' + (it.done ? ' done' : '') + '"><button class="box" aria-label="완료" data-act="' + it.k + '" data-id="' + it.id + '" data-unit="' + esc(it.unit || '') + '"></button><div class="grow"><div class="ti">' + esc(it.title) + '</div><div class="meta">' + (it.urgent && !it.done ? '<span class="badge red">오늘 14:00</span>' : '') + esc(it.meta) + '</div>' + (it.why ? '<div class="why">왜 나에게: ' + esc(it.why) + '</div>' : '') + '</div>' +
        (it.done ? '<span class="badge ok">완료</span>' : '<button class="btn sm' + (it.urgent ? ' pri' : '') + '" data-act="' + it.k + '" data-id="' + it.id + '" data-unit="' + esc(it.unit || '') + '">' + (it.k === 'ack' ? '보기' : '제출') + '</button>') + '</div>';
    }).join('');

    var chHtml = changes.map(function (e) {
      return '<li>' + TR.chgIcon + '<span class="t">' + TR.dateText(e) + ' ' + esc(e.title) + '</span><span>' + e.change + '</span><a href="javascript:void 0" data-ev="' + e.id + '" class="small">자세히</a></li>';
    }).join('') +
      '<li>' + TR.chgIcon + '<span class="t">공지 「' + esc(n2.title) + '」 v2</span><span>장소 <del>강당</del> → <ins>체육관</ins> · <del>2블록</del> → <ins>3블록</ins></span><a href="#notice/n2" class="small">공지 보기</a></li>';

    view.innerHTML =
      '<div class="page-h"><div><h1>10월 26일 월요일</h1><p>2학기 9주차 · ' + TR.school.name + ' · ' + esc(TR.me.role) + '</p></div>' +
      '<div class="row no-print"><a class="btn" href="#cal">이번 주 보기</a><button class="btn" id="printDay">오늘 일과 A4 인쇄</button></div></div>' +
      '<div class="strip"><span class="pill">🍚 <b>급식</b> 잡곡밥 · 쇠고기미역국 · 돈육장조림 · 감귤</span><span class="pill">🌤 미세먼지 보통</span><span class="pill">🔔 다음 묶음 알림 <b>12:20</b> (2블록 끝) · 수업 중엔 PC 알림도 띄우지 않아요</span><span class="pill faint">학사일정·급식은 NEIS에서 자동</span></div>' +
      '<section class="changes" aria-label="오늘 바뀐 것"><h2>' + TR.chgIcon + '오늘 바뀐 것 ' + (changes.length + 1) + '건 <span class="small muted" style="font-weight:400">어제 퇴근 이후</span></h2><ul>' + chHtml + '</ul></section>' +
      '<div class="grid3">' +
      '<section class="card col-tt"><div class="card-h"><h2>내 시간표</h2><span class="small muted">4-2 · 학기 초 1번 입력</span></div><div class="card-b" style="padding-top:5px"><table class="tt">' + mine + '</table></div></section>' +
      '<section class="card col-tl"><div class="card-h"><h2>학교 오늘</h2>' + legendHtml() + '</div><div class="card-b">' +
      '<p class="board-hint">날짜만 넣은 일정은 <b>전일</b>에 모입니다' + (untimed ? ' (지금 ' + untimed + '건)' : '') + '. 내 일정과 우리 학년 일정은 <b>끌어서 블록에 놓고</b>, 카드 <b>위·아래 끝</b>을 끌어 길이를 정하세요. 새 일정은 칸마다 있는 <b>＋ 추가</b>를 눌러 그 자리에서 바로 쓰세요(빈 곳을 두 번 눌러도 됩니다).</p>' +
      dayBoard(school) +
      '<h3 style="font-size:16px;margin:18px 0 9px">복무 <span class="small muted" style="font-weight:400">' + duty.length + '명 · 나이스 결재와 별개로 공유만 · 보강은 담당자가 기존대로</span></h3><div class="duty-list">' + dutyHtml + '</div></div></section>' +
      '<section class="card col-r"><div class="card-h"><div class="seg"><button data-scope="me" class="' + (inboxScope === 'me' ? 'on' : '') + '">나에게 온 것' + (open ? ' ' + open : '') + '</button><button data-scope="all" class="' + (inboxScope === 'all' ? 'on' : '') + '">학교 전체</button></div>' + (inboxScope === 'me' ? '<span class="badge ' + (open ? 'red' : 'ok') + '">' + (open ? '남은 일 ' + open : '모두 끝') + '</span>' : '<span class="small muted">마감순</span>') + '</div><div class="card-b"><div class="inbox">' + inboxHtml + '</div>' +
      '<p class="small faint" style="margin:14px 0 0">' + (inboxScope === 'me' ? '모으기를 만들 때 고른 대상(그룹·개인·그룹별)에 내가 들어 있는 칸과 확인 필수 공지만 모입니다. 「왜 나에게」가 그 규칙이에요. 제출하면 자동으로 지워져요.' : '학교에서 진행 중인 취합과 확인 필수 공지 전체. 내 것이 아니어도 "다들 이걸 하고 있구나" 리마인드용입니다.') + '</p></div></section>' +
      '</div>';

    bindEvClicks(view);
    bindDayBoard(view, today);
    $$('[data-act]', view).forEach(function (b) {
      b.addEventListener('click', function () {
        var k = b.dataset.act, id = b.dataset.id;
        if (k === 'col') recvModal('recv.html?c=' + id + '&u=' + encodeURIComponent(b.dataset.unit) + '&me=1', '제출하기');
        if (k === 'go') location.hash = '#collect/' + id;
        if (k === 'ack') location.hash = '#notice/' + id;
      });
    });
    $$('[data-scope]', view).forEach(function (b) { b.onclick = function () { inboxScope = b.dataset.scope; today(); }; });
    $('#printDay').onclick = function () { location.hash = '#cal'; setTimeout(function () { window.print(); }, 300); };
  }

  // ---------- 일정 (주간/월간 + 인쇄) ----------
  // 주간 = [전일] + 블록 4칸(1블록·2블록·3블록·방과후). 기간 일정은 여러 날에 걸친 긴 카드. 화면은 색, 인쇄는 흑백 기호.
  var calMode = 'week', calMonth = '2026-10', paper = 'A4', WEEK0 = '2026-10-26';
  function weekCard(e, days) {
    var a = Math.max(0, TR.dayDiff(days[0], e.date)), b = Math.min(4, TR.dayDiff(days[0], TR.lastDay(e)));
    var cl = e.date < days[0], cr = TR.lastDay(e) > days[4], ed = TR.canEdit(e);
    return '<div class="wkw' + (ed ? ' edit' : '') + (cl ? ' cl' : '') + (cr ? ' cr' : '') + '" data-w="' + e.id + '" style="grid-column:' + (a + 1) + ' / span ' + (b - a + 1) + '">' +
      (ed && !cl ? '<i class="hdl l" data-h="l" title="끌어서 시작 날짜 바꾸기"></i>' : '') + (ed && !cr ? '<i class="hdl r" data-h="r" title="끌어서 마지막 날짜 바꾸기"></i>' : '') +
      evCard(e, { compact: true, time: !TR.isAllDay(e), range: !!e.end }) + '</div>';
  }
  // 주간 끌기: 카드 몸통 = 다른 날·다른 블록(전일 포함)으로 옮기기, 좌우 끝 = 기간 늘리기·줄이기
  function bindWeek(root, days) {
    var wk = $('#wk2', root); if (!wk) return;
    var tip = $('#dragtip') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'dragtip', className: 'tip' }));
    var areas = $$('.wa[data-row]', wk);
    function areaAt(x, y) { return areas.find(function (a) { var r = a.getBoundingClientRect(); return y >= r.top && y <= r.bottom && x >= r.left && x <= r.right; }); }
    function dayAt(area, x) { var r = area.getBoundingClientRect(); return Math.max(0, Math.min(4, Math.floor((x - r.left) / r.width * 5))); }
    function when(st, en, row) { return TR.fmt(TR.addDays(days[0], st)) + (en > st ? '~' + TR.fmt(TR.addDays(days[0], en)) : '') + ' · ' + (row === 'all' ? '전일' : row); }
    $$('.wkw.edit', wk).forEach(function (w) {
      w.addEventListener('pointerdown', function (e) {
        if (e.button !== 0) return;
        var ev = S().events.find(function (x) { return x.id === w.dataset.w; });
        var mode = e.target.dataset.h || 'move', area0 = w.parentNode;
        var s0 = TR.dayDiff(days[0], ev.date), e0 = TR.dayDiff(days[0], TR.lastDay(ev)), len = e0 - s0;
        var span = ev.s && !TR.isAllDay(ev) ? TR.slotIdx(ev.e || ev.s) - TR.slotIdx(ev.s) : 0;
        var grab = dayAt(area0, e.clientX) - s0, st = s0, en = e0, row = area0.dataset.row, target = area0;
        var rect = w.getBoundingClientRect(), ox = e.clientX - rect.left, oy = e.clientY - rect.top, x0 = e.clientX, y0 = e.clientY, moved = false, ghost = null;
        e.preventDefault();
        function rowLabel(r) { if (r === 'all') return 'all'; var a = +r, b = Math.min(TR.slots.length - 1, a + span); return TR.slotText({ s: TR.slots[a].k, e: TR.slots[b].k }); }
        function showPh(area, a, b) {
          $$('.wph', wk).forEach(function (p) { p.hidden = true; });
          var ph = $('.wph', area), lo = Math.max(0, a), hi = Math.min(4, b); if (hi < lo) return;
          ph.hidden = false; ph.style.left = (lo * 20) + '%'; ph.style.width = ((hi - lo + 1) * 20) + '%';
        }
        function move(m) {
          if (!moved && Math.abs(m.clientX - x0) + Math.abs(m.clientY - y0) < 5) return;
          if (!moved) {
            moved = true; w.classList.add('dragging'); document.body.classList.add('is-dragging');
            if (mode === 'move') { ghost = w.cloneNode(true); ghost.className = 'dragghost'; ghost.style.width = rect.width + 'px'; ghost.style.height = rect.height + 'px'; document.body.appendChild(ghost); }
          }
          if (mode === 'move') {
            ghost.style.left = (m.clientX - ox) + 'px'; ghost.style.top = (m.clientY - oy) + 'px';
            var ar = areaAt(m.clientX, m.clientY);
            if (ar) { target = ar; row = ar.dataset.row; st = dayAt(ar, m.clientX) - grab; en = st + len; showPh(ar, st, en); }
          } else {
            var d = dayAt(area0, m.clientX);
            if (mode === 'l') st = Math.min(d, e0); else en = Math.max(d, s0);
            var lo = Math.max(0, st), hi = Math.min(4, en); w.style.gridColumn = (lo + 1) + ' / span ' + (hi - lo + 1);
          }
          var rl = rowLabel(row);
          tip.textContent = when(st, en, rl);
          tip.style.left = (m.clientX + 14) + 'px'; tip.style.top = (m.clientY + 16) + 'px'; tip.classList.add('show');
        }
        function up() {
          document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', up);
          if (ghost) ghost.remove(); tip.classList.remove('show'); document.body.classList.remove('is-dragging');
          if (!moved) return; // 클릭 → 카드 열기(bindEvClicks)
          dragEnd = Date.now();
          var w2 = { date: TR.addDays(days[0], st), end: TR.addDays(days[0], en) };
          if (mode === 'move' && row !== area0.dataset.row) { if (row === 'all') w2.a = null; else { w2.a = +row; w2.b = Math.min(TR.slots.length - 1, +row + span); } }
          setWhen(ev, w2);
          cal();
        }
        document.addEventListener('pointermove', move); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', up);
      });
    });
  }
  // 일정의 칸마다 [＋ 추가] — 주간은 (날짜 × 전일/블록), 월간은 날짜. 칸이 꽉 차도 버튼은 늘 그 칸 맨 아래에 있다.
  function bindCellAdd(root) {
    $$('.cell-add[data-d]', root).forEach(function (b) {
      b.onclick = function () {
        var date = b.dataset.d, slot = b.dataset.row === 'all' ? null : +b.dataset.row, mo = b.classList.contains('mo-add'), wrap = b.parentNode;
        cellInput({
          ph: TR.fmt(date) + (slot === null ? '' : ' ' + TR.slots[slot].label) + ' — 제목 쓰고 Enter',
          cls: mo ? 'mo-in' + (b.dataset.rt ? ' rt' : '') : 'wk-in',
          place: function (box) {
            if (mo) { $('.mo', root).classList.add('editing'); wrap.appendChild(box); b.hidden = true; return; }
            var c = +b.style.gridColumn.split('/')[0]; box.style.gridColumn = Math.min(c, 4) + ' / span 2'; wrap.classList.add('open'); wrap.appendChild(box);
          },
          cancel: cal,
          save: function (r) { var ev = addEvent(r, date, slot); TR.toast('「' + ev.title + '」 ' + TR.dateText(ev) + ' ' + (ev.s ? TR.slotText(ev) : '전일') + '에 넣었어요'); cal(); }
        });
      };
    });
  }
  function cal() {
    var st = S();
    var head = '<div class="page-h no-print"><div><h1>일정</h1><p>주간은 전일 + 블록 4칸, 월간은 월중행사표. 인쇄는 흑백 기호로 나갑니다.</p></div>' +
      '<div class="row"><div class="seg" role="tablist"><button data-m="week" class="' + (calMode === 'week' ? 'on' : '') + '">주간</button><button data-m="month" class="' + (calMode === 'month' ? 'on' : '') + '">월간</button></div>' +
      (calMode === 'month' ? '<button class="btn sm" data-mv="-1" aria-label="이전 달">◀ 이전 달</button><button class="btn sm" data-mv="1" aria-label="다음 달">다음 달 ▶</button>' : '<button class="btn sm" id="wpaste">주간계획 표 붙여넣기</button>') +
      '<select id="psize" class="btn sm" aria-label="용지"><option value="A4"' + (paper === 'A4' ? ' selected' : '') + '>A4 가로</option><option value="A3"' + (paper === 'A3' ? ' selected' : '') + '>A3 가로</option></select>' +
      '<button class="btn pri" id="print">' + (calMode === 'month' ? '월중행사표 인쇄' : '주간계획 인쇄') + '</button></div></div>';
    var y = +calMonth.slice(0, 4), m = +calMonth.slice(5);
    var title = calMode === 'month' ? y + '년 ' + m + '월 월중행사' : '10월 5주 주간교육계획 (10/26~10/30)';
    var ph = '<div class="print-head"><div><div class="small">' + TR.school.name + '</div><h1>' + title + '</h1><div class="small">출력 2026-10-26 · 티로드</div></div><table><tr><td>담당</td><td>부장</td><td>교감</td><td>교장</td></tr><tr><td></td><td></td><td></td><td></td></tr></table></div>';
    var legend = '<div class="cal-legend">' + legendHtml('<span class="no-print faint">· 카드 아래 작성자(그룹)</span>') + '<span class="print-only">◆ 행사 ■ 업무·교육 ▲ 회의 ○ 복무 · ↻ 최근 변경</span></div>';
    var body = '';
    if (calMode === 'week') {
      var days = [0, 1, 2, 3, 4].map(function (i) { return TR.addDays(WEEK0, i); }), td = days.indexOf(TR.TODAY);
      var inWeek = function (e) { return e.date <= days[4] && TR.lastDay(e) >= days[0]; };
      var byStart = function (p, q) { return (p.date < q.date ? -1 : p.date > q.date ? 1 : 0) || TR.dayDiff(q.date, TR.lastDay(q)) - TR.dayDiff(p.date, TR.lastDay(p)); };
      body = '<div class="wk2" id="wk2"><div class="wl hd"></div><div class="wa hd">' + days.map(function (d) { return '<div class="' + (d === TR.TODAY ? 'today' : '') + '">' + TR.fmt(d) + '</div>'; }).join('') + '</div>';
      [{ label: '전일', sub: '시간 미정 · 종일', row: 'all' }].concat(TR.slots.map(function (x, i) { return { label: x.label, sub: x.time, row: i }; })).forEach(function (rw) {
        var all = rw.row === 'all';
        var evs = st.events.filter(function (e) { return e.type !== 'duty' && inWeek(e) && (all ? TR.isAllDay(e) : !TR.isAllDay(e) && TR.slotIdx(e.s) === rw.row); }).sort(byStart);
        var duties = all ? days.map(function (d, i) { var ds = st.events.filter(function (e) { return e.type === 'duty' && TR.onDay(e, d); }); return ds.length ? '<div class="dutyline" style="grid-column:' + (i + 1) + '"><b>복무</b> ' + ds.map(function (x) { return esc(x.who) + ' ' + x.duty; }).join(' · ') + '</div>' : ''; }).join('') : '';
        body += '<div class="wl' + (all ? ' all' : '') + '"><b>' + rw.label + '</b>' + rw.sub + '</div><div class="wa' + (all ? ' all' : '') + (td >= 0 ? ' has-td' : '') + '" data-row="' + rw.row + '" style="--td:' + td + '">' + evs.map(function (e) { return weekCard(e, days); }).join('') + duties +
          '<div class="wadd no-print">' + days.map(function (d, i) { return '<button class="cell-add" style="grid-column:' + (i + 1) + '" data-d="' + d + '" data-row="' + rw.row + '" title="' + TR.fmt(d) + ' ' + rw.label + '에 일정 추가">＋ 추가</button>'; }).join('') + '</div><div class="wph" hidden></div></div>';
      });
      body += '</div>';
    } else {
      var first = new Date(y, m - 1, 1), start = new Date(first); start.setDate(1 - first.getDay());
      body = '<div class="mo">' + TR.dow.map(function (d) { return '<div class="dh">' + d + '</div>'; }).join('');
      for (var i = 0; i < 42; i++) {
        var dd = new Date(start); dd.setDate(start.getDate() + i); var iso = TR.iso(dd);
        if (i >= 35 && dd.getMonth() !== m - 1) break;
        // 기간 일정은 날마다 다시 적되, 이어지는 날(주말 제외)에는 '↳'로 표시
        var wkend = dd.getDay() === 0 || dd.getDay() === 6;
        var items = st.events.filter(function (e) { return TR.onDay(e, iso) && e.type !== 'duty' && (e.date === iso || !wkend); }).map(function (e) { return { t: e.title, ty: e.type, ch: e.changed, id: e.id, ev: e, g: TR.groupOf(e.by), by: TR.byline(e.by), rel: TR.isRel(e), cont: e.date !== iso }; })
          .concat(TR.monthExtra.filter(function (x) { return x[0] === iso || (x[4] && x[0] < iso && iso <= x[4] && !wkend); }).map(function (x) { return { t: x[1], ty: x[2], g: x[3], by: x[3], rel: x[3] === TR.groupOf(TR.me.name), cont: x[0] !== iso }; }));
        var hol = TR.holidays[iso];
        var cls = [dd.getMonth() !== m - 1 ? 'out' : '', hol ? 'hol' : '', dd.getDay() === 0 ? 'sun' : '', dd.getDay() === 6 ? 'sat' : '', iso === TR.TODAY ? 'today' : ''].join(' ');
        var shown = items.slice(0, 4);
        body += '<div class="mc ' + cls + '"><div class="d"><span>' + dd.getDate() + '</span>' + (hol ? '<span class="small">' + hol + '</span>' : '') + '</div>' +
          shown.map(function (it) { return '<div class="e ' + it.ty + (it.rel ? ' rel' : '') + '"' + (it.id ? ' data-ev="' + it.id + '" style="cursor:pointer"' : '') + ' title="' + esc(it.by) + '"><i class="dt"></i><span class="psym">' + TR.typeSym[it.ty] + '</span><span>' + (it.cont ? '<span class="faint">↳</span> ' : '') + (it.g && !it.cont ? '<span class="g">[' + esc(it.g) + ']</span> ' : '') + esc(it.t) + '</span>' + (it.ch ? TR.chgIcon : '') + (it.ev ? attMark(it.ev) : '') + '</div>'; }).join('') +
          (items.length > 4 ? '<div class="e more">+' + (items.length - 4) + '건 별지</div>' : '') +
          '<button class="cell-add mo-add no-print" data-d="' + iso + '" data-row="all"' + (dd.getDay() >= 5 ? ' data-rt="1"' : '') + ' title="' + TR.fmt(iso) + '에 일정 추가">＋ 추가</button></div>';
      }
      body += '</div>';
    }
    view.innerHTML = '<div class="page">' + head + ph + '<div class="no-print" style="margin-bottom:9px"><div class="proto-note">' + (calMode === 'week'
      ? '시간 단위는 <b>1블록 · 2블록 · 3블록 · 방과후</b> 네 칸뿐입니다. 날짜만 넣은 일정은 맨 위 <b>전일</b>에 들어가고, 여러 날짜의 일정은 <b>긴 카드</b>로 이어집니다. 내 일정·우리 학년 일정은 카드를 <b>끌어서 다른 날·다른 블록</b>으로 옮기고, <b>좌우 끝을 끌어 기간</b>을 늘리거나 줄이세요. 새 일정은 칸마다 아래에 있는 <b>＋ 추가</b>를 눌러 그 칸에서 바로 쓰세요.'
      : '제목은 자르지 않고 줄바꿈합니다. 한 줄 입력(상단)으로 쓴 문장은 날짜 표현만 빼고 <b>그대로 제목</b>이 됩니다. 굵은 글씨는 나와 관련된 일정입니다. 날마다 아래의 <b>＋ 추가</b>로 그 날짜에 바로 쓸 수 있어요.') + '</div></div>' + legend + body + '</div>';
    view.firstChild.style.padding = '0';
    $$('[data-m]', view).forEach(function (b) { b.onclick = function () { calMode = b.dataset.m; cal(); }; });
    $$('[data-mv]', view).forEach(function (b) { b.onclick = function () { var d = new Date(y, m - 1 + (+b.dataset.mv), 1); calMonth = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); view.innerHTML = ''; cal(); }; });
    $('#psize').onchange = function () { paper = this.value; };
    $('#print').onclick = function () {
      var pg = $('#pgsize') || document.head.appendChild(Object.assign(document.createElement('style'), { id: 'pgsize' }));
      pg.textContent = '@media print{@page{size:' + paper + ' landscape}}';
      window.print();
    };
    var wp = $('#wpaste'); if (wp) wp.onclick = weekPaste;
    bindEvClicks(view);
    bindCellAdd(view);
    if (calMode === 'week') bindWeek(view, [0, 1, 2, 3, 4].map(function (i) { return TR.addDays(WEEK0, i); }));
  }

  // 주간교육계획 표 붙여넣기: 교무부장이 이미 만드는 표를 그대로 넣으면 한 주 일정이 한꺼번에 들어간다.
  // 시간 칸이 비어 있으면 전일로. 담당 칸의 이름이 명부에 있으면 그 선생님 이름으로 등록.
  var WEEK_SAMPLE = '날짜\t시간\t내용\t장소\t담당\n10/27(화)\t3블록\t3학년 생활안전 체험교실 (실내화 지참)\t강당\t신유진\n10/28(수)\t\t1학년 학부모 독서교실 신청서 회수\t\t강지은\n10/29(목)\t15:00\t학교운영위원회 정기회의\t교장실\t한상우\n10/30(금)\t1블록\t전교 소방 대피훈련 (사이렌 후 운동장 집결)\t운동장\t탁현수\n\t\t6학년 수학여행 사전 안전교육\t시청각실\t양서윤';
  function parseWeek(text) {
    var last = null, out = [];
    text.split(/\r?\n/).forEach(function (line, i) {
      var c = line.split('\t').map(function (x) { return x.trim(); });
      if (!line.trim() || (i === 0 && /내용|날짜|일시/.test(line))) return;
      if (c.length < 3) c = [c[0] || '', '', c.slice(1).join(' ')];
      var d = TR.parse(' ' + c[0].replace(/\([월화수목금토일]\)/, ' ') + ' ');
      if (d.date) last = d.date;
      var p = TR.parse(c[1] || '');
      var who = S().members.some(function (m) { return m.name === c[4]; }) ? c[4] : TR.me.name;
      var cp = TR.parse(c[2] || '');
      var ev = { id: 'w' + Date.now().toString(36) + i, date: last || TR.TODAY, end: d.end, title: c[2], place: c[3] || cp.place || '', target: cp.target || '', type: cp.type === 'duty' ? 'work' : cp.type, by: who };
      if (p.s) { ev.s = p.s; ev.e = p.e || p.s; if (p.time) ev.time = p.time; }
      if (ev.title) out.push(ev);
    });
    return out;
  }
  function weekPaste() {
    modal('<div class="card-h"><h2>주간계획 표 붙여넣기</h2><button class="btn sm ghost" data-close aria-label="닫기">✕</button></div><div class="card-b" style="display:grid;gap:12px">' +
      '<p class="muted" style="margin:0">교무부장님이 매주 만드는 주간교육계획(한글·엑셀) 표를 복사해 붙여넣으세요. <b>날짜 · 시간 · 내용 · 장소 · 담당</b> 순서면 됩니다. 시간 칸이 비면 <b>전일</b>로 들어가고, 블록이나 시각(15:00)이 적혀 있으면 그 <b>블록</b>으로 들어갑니다. 날짜가 빈 줄은 위 날짜를 따라가고, <b>10/28~30</b>처럼 쓰면 기간 일정이 됩니다.</p>' +
      '<div class="row" style="justify-content:flex-end"><button class="btn sm" id="wsample">예시 표 넣기</button></div><textarea class="paste" id="wtext" placeholder="날짜	시간	내용	장소	담당"></textarea><div id="wprev"></div>' +
      '<div class="row" style="justify-content:flex-end"><button class="btn" data-close>취소</button><button class="btn pri" id="wadd" disabled>넣기</button></div></div>');
    $('.modal').style.width = '782px';
    var list = [];
    function prev() {
      list = parseWeek($('#wtext').value);
      $('#wprev').innerHTML = list.length ? '<table class="tbl"><tr><th>날짜</th><th>시간</th><th>제목(원문 그대로)</th><th>장소</th><th>등록</th></tr>' + list.map(function (e) { return '<tr><td>' + TR.dateText(e) + '</td><td>' + (e.s ? esc(TR.slotText(e)) : '<span class="badge">전일</span>') + '</td><td style="white-space:normal">' + esc(e.title) + '</td><td>' + esc(e.place) + '</td><td>' + esc(e.by) + '</td></tr>'; }).join('') + '</table>' : '';
      $('#wadd').disabled = !list.length; $('#wadd').textContent = list.length ? list.length + '건 넣기' : '넣기';
    }
    $('#wtext').addEventListener('input', prev);
    $('#wsample').onclick = function () { $('#wtext').value = WEEK_SAMPLE; prev(); };
    $('#wadd').onclick = function () { list.forEach(function (e) { if (!e.end) delete e.end; S().events.push(e); }); TR.save(); closeModal(); TR.toast('주간계획 ' + list.length + '건을 넣었어요. 시간 없는 ' + list.filter(function (e) { return !e.s; }).length + '건은 전일에 있어요.'); cal(); };
  }

  // ---------- 모으기 ----------
  function collectList() {
    var st = S();
    var rows = st.collections.map(function (c) {
      var p = progress(c), r = c.rows.find(mineRow);
      var mineState = c.mine ? '<span class="badge pri">내가 보냄</span>' : r ? (r.done ? '<span class="badge ok">제출함</span>' : '<span class="badge red">내 칸 비었음</span>') : '';
      return '<a class="crow" href="#collect/' + c.id + '"><div><div class="row"><b>' + esc(c.title) + '</b>' + mineState + '</div><div class="small muted">' + esc(c.dept) + ' ' + esc(c.owner) + ' · ' + esc(c.target) + '</div></div>' +
        '<div><div class="bar' + (c.urgent ? ' red' : '') + '"><i style="width:' + p.p + '%"></i></div><div class="small muted" style="margin-top:5px"><b style="color:var(--text)">' + p.d + '</b>/' + p.t + ' 제출 · ' + p.p + '%</div></div>' +
        '<div class="small' + (c.urgent ? ' need' : ' muted') + '">' + (c.due ? '마감 ' + esc(c.due) : '<span class="badge">마감 미정</span>') + '</div><div class="small muted">미제출 ' + (p.t - p.d) + p.unit + '</div></a>';
    }).join('');
    view.innerHTML = '<div class="page-h"><div><h1>모으기</h1><p>양식 표를 붙여넣으면 끝. 받는 사람은 자기 칸만 채우고, 합본은 실시간으로 만들어집니다. 마감은 만든 뒤 각 요청 화면에서 정합니다.</p></div><a class="btn pri" href="#collect/new">＋ 새로 모으기</a></div>' +
      '<div class="card">' + rows + '</div>' +
      '<div class="grid2" style="margin-top:18px"><div class="proto-note">취합 흐름: hwp에서 <b>취합할 표 부분만</b> 꺼내 칸 단위 데이터로 만들고 → 선생님별 "내 칸" 링크로 걷고 → 다 모이면 다시 hwp로 내보냅니다. MVP는 표 <b>붙여넣기</b>, v1은 <b>hwpx 파일을 올려 같은 파일에 채워 돌려주기</b>입니다.</div><div class="proto-note">합본 파일 꼬리말에 "티로드로 모았습니다"가 들어갑니다. 받는 사람이 다음 취합을 직접 만들어 보게 하는 장치입니다.</div></div>';
  }

  var SAMPLE_TABLE = '학년반\t담임\t희망 인원\t희망 프로그램\t비고\n1-1\t강지은\t\t\t\n1-2\t윤서준\t\t\t\n1-3\t장미경\t\t\t\n2-1\t최수아\t\t\t\n…';
  var TYPE_LABEL = { text: '글자', number: '숫자', select: '선택지', date: '날짜', url: '링크' };
  var newSrc = 'sheet';
  // 자주 쓰는 양식: 누르면 바로 만들어진다. 마감은 만든 뒤 관리(상세) 화면에서 정한다.
  function makePreset(key) {
    var c = Object.assign(TR.preset(key), { id: 'c' + Date.now().toString(36), owner: TR.me.name, mine: true, due: '', dueISO: '' });
    S().collections.unshift(c); TR.save();
    TR.copy('[티로드] ' + c.title + '\n\n아래 링크에서 ' + (c.kind === 'matrix' ? '내 줄' : '우리 반') + '만 채워 주세요.\nhttps://t.road/c/' + c.id.slice(-4) + '\n\n— ' + TR.me.name, '「' + c.title + '」을 만들고 쪽지 문구를 복사했어요. 마감은 이 화면 위에서 정하세요.');
    location.hash = '#collect/' + c.id;
  }
  function collectNew() {
    var cols = [{ n: '', t: 'text' }], multi = false;
    view.innerHTML = '<div class="page-h"><div><h1>새로 모으기</h1><p>받을 항목만 정하면 됩니다. 받는 사람 칸(학년반·이름)은 대상에 맞춰 자동으로 채워져요.</p></div><a class="btn" href="#collect">취소</a></div>' +
      '<div class="grid2"><section class="card"><div class="card-b">' +
      '<div class="step"><i>1</i>양식 가져오기</div>' +
      '<div class="seg" style="margin-bottom:14px">' + [['sheet', '직접 입력'], ['preset', '자주 쓰는 양식'], ['paste', '표 붙여넣기'], ['file', 'hwpx 올리기 · v1']].map(function (s) { return '<button data-src="' + s[0] + '" class="' + (newSrc === s[0] ? 'on' : '') + '">' + s[1] + '</button>'; }).join('') + '</div>' +
      '<div id="srcSheet" data-pane="sheet"><div id="sheet"></div><label class="toggle" style="margin-top:12px"><input type="checkbox" id="multi"> 받는 사람이 줄을 여러 개 추가 (예: 물품 신청 — 한 반이 여러 물품)</label></div>' +
      '<div data-pane="preset"><div class="presets">' +
      '<button class="preset" data-preset="supplies"><b>학습준비물 신청</b><span>반마다 물품을 여러 줄로 · <b>금액 = 수량 × 단가</b> 자동 · 학급 예산 10만 원에서 잔액 자동 · 학년별 합계 자동</span><span class="small muted">물품명 · 수량 · 단가 · 금액 · 구입 링크 / 담임 24명 + 도움반</span><span class="go">누르면 바로 만들어요 →</span></button>' +
      '<button class="preset" data-preset="training"><b>교직원 연수 이수 현황</b><span>법정의무연수 ' + TR.TRAININGS.length + '개 × 전 교직원 · 칸마다 <b>이수번호</b> · 연수별 이수율 · 안 한 사람만 골라 독촉</span><span class="small muted">연수명 · 기간 · 대상 · 담당 / 전 교직원 ' + S().members.length + '명 (교원·행정실·급식소·지원)</span><span class="go">누르면 바로 만들어요 →</span></button>' +
      '</div><p class="small muted" style="margin:12px 0 0">학교에서 구글 시트로 가장 많이 돌리는 두 가지입니다. 연수 목록과 예산은 만든 뒤 고칠 수 있어요.</p></div>' +
      '<div id="srcFile" data-pane="file"><label class="drop"><input type="file" id="hwpFile" accept=".hwpx,.hwp" hidden><b>hwpx 파일을 끌어다 놓거나 눌러서 고르세요</b><span class="small muted">파일 안의 표를 찾아 취합할 표를 고르게 합니다. 본문·결재란은 그대로 두고, 다 모이면 <b>같은 파일의 그 표 칸만</b> 채워 돌려드립니다.</span></label><div id="fileRes"></div></div>' +
      '<div id="srcPaste" data-pane="paste"><div class="row" style="justify-content:flex-end;margin-bottom:7px"><button class="btn sm" id="sample">예시 표 넣기</button></div>' +
      '<textarea class="paste" id="paste" placeholder="한글에서 표를 드래그해 복사(Ctrl+C) → 여기에 붙여넣기(Ctrl+V)\n\n예) 학년반 | 담임 | 희망 인원 | 희망 프로그램 | 비고"></textarea><div id="fields" style="margin-top:14px"></div></div>' +
      '<div id="rest"><div class="step" style="margin-top:21px"><i>2</i>누구에게 받나요</div><div id="aud"></div>' +
      '<div class="step" style="margin-top:21px"><i>3</i>받는 방식</div>' +
      '<div style="display:grid;gap:7px"><label><input type="radio" name="acc" value="in" checked> <b>티로드 안에서</b> — 쪽지 링크를 누르면 바로 내 칸. 처음 한 번 이 PC를 등록하면 그다음부터 로그인 화면이 없어요.</label><label><input type="radio" name="acc" value="ext"> <b>+ 외부 링크도 허용</b> — 명부에 없는 사람(방과후 강사·기간제 첫날·업체)용. 칸 하나 제출만 되고, 마감되면 링크가 닫힙니다.</label></div>' +
      '<div class="row" style="margin-top:21px"><input id="title" placeholder="제목 (예: 2학기 방과후 수요조사)" style="flex:1;height:44px;border:1px solid var(--line-strong);border-radius:9px;padding:0 12px;background:var(--surface)"><button class="btn pri" id="create">만들고 링크 받기</button></div><p class="small muted" style="margin:9px 0 0">마감은 여기서 받지 않아요. 만든 뒤 요청 화면(관리)에서 정하면 받는 사람에게 마감과 리마인드가 갑니다.</p></div>' +
      '</div></section>' +
      '<section class="card"><div class="card-h"><h2>받는 사람에게 갈 쪽지</h2><span class="small muted">쿨메신저에 그대로 붙여넣기</span></div><div class="card-b"><div class="msg" id="msg"></div><p class="small muted">링크를 누른 선생님은 PC 브라우저에서 <b>자기 반 칸만</b> 봅니다. 티로드에 들어와 있으면 우상단 🔔에도 뜹니다.</p><div id="pii"></div></div></section></div>';
    var paste = $('#paste'), fieldsEl = $('#fields'), parsed = null;

    // 2. 누구에게 받나요 — 그룹 선택(사람마다 한 줄) / 개인 선택(전 직원 중) / 그룹별(그룹마다 한 줄)
    var GRADES = TR.groups.filter(function (g) { return g.kind === 'grade'; }).map(function (g) { return g.id; });
    var aud = { mode: 'groups', gids: GRADES.slice(), hrOnly: true, names: [], pgids: GRADES.slice() };
    function spec() { return aud.mode === 'pergroup' ? { mode: 'pergroup', gids: aud.pgids.slice() } : aud.mode === 'people' ? { mode: 'people', names: aud.names.slice() } : { mode: 'groups', gids: aud.gids.slice(), hrOnly: aud.hrOnly }; }
    function audChange() { renderAud(); renderSheet(); syncSheet(); }
    function gchips(sel, key) {
      var ms = S().members;
      return '<div class="gck-row">' + TR.groups.map(function (g) { var n = ms.filter(function (m) { return m.group === g.id; }).length; return '<label class="gck"><input type="checkbox" data-' + key + '="' + g.id + '"' + (sel.indexOf(g.id) >= 0 ? ' checked' : '') + '>' + esc(g.name) + '<span>' + n + '</span></label>'; }).join('') + '</div>';
    }
    function renderAud() {
      var ms = S().members, sp = spec(), n = TR.audRows(sp).length;
      var opt = function (v, t, d) { return '<label class="amode' + (aud.mode === v ? ' on' : '') + '"><input type="radio" name="am" value="' + v + '"' + (aud.mode === v ? ' checked' : '') + '><b>' + t + '</b><span class="small muted">' + d + '</span></label>'; };
      var people = '<div class="apane"><input class="psearch" id="psq" placeholder="이름·직책으로 찾기 (예: 부장, 전담, 김)"><div class="plist">' + TR.groups.map(function (g) {
        var gm = ms.filter(function (m) { return m.group === g.id; }); if (!gm.length) return '';
        return '<div class="pg"><b>' + esc(g.name) + '</b><div class="pg-l">' + gm.map(function (m) { return '<label class="pck" data-q="' + esc(m.name + ' ' + TR.posText(m) + ' ' + (m.sub || '')) + '"><input type="checkbox" data-p="' + esc(m.name) + '"' + (aud.names.indexOf(m.name) >= 0 ? ' checked' : '') + '>' + esc(m.name) + '<span>' + esc(TR.posText(m).replace(g.name + ' ', '')) + '</span></label>'; }).join('') + '</div></div>';
      }).join('') + '</div>' + (aud.names.length ? '<div class="small" style="margin-top:9px">고른 사람 ' + aud.names.length + '명: <b>' + aud.names.map(esc).join(', ') + '</b> <button class="btn sm ghost" data-pclear>모두 해제</button></div>' : '') + '</div>';
      $('#aud').innerHTML = '<div class="audbox">' +
        opt('groups', '그룹 선택', '고른 그룹의 사람마다 한 줄') +
        (aud.mode === 'groups' ? '<div class="apane">' + gchips(aud.gids, 'g') + '<div class="row" style="flex-wrap:wrap;margin-top:9px"><button class="btn sm" data-gall="all">전 교직원</button><button class="btn sm" data-gall="grade">1~6학년만</button><button class="btn sm" data-gall="none">모두 해제</button><label class="toggle" style="margin-left:auto"><input type="checkbox" id="hrOnly"' + (aud.hrOnly ? ' checked' : '') + '> 담임만 · 반별 1칸</label></div></div>' : '') +
        opt('people', '개인 선택', '전 직원 중에서 골라서') + (aud.mode === 'people' ? people : '') +
        opt('pergroup', '그룹별', '그룹마다 한 줄 — 그룹의 의견을 물을 때') +
        (aud.mode === 'pergroup' ? '<div class="apane">' + gchips(aud.pgids, 'pg') + '<p class="small muted" style="margin:9px 0 0">그룹 칸은 그 그룹 구성원 <b>누구나</b> 쓸 수 있고, 한 명이 내면 그 그룹은 제출 완료입니다. 다 내기 전까지는 구성원 모두의 「나에게 온 것」에 뜹니다.</p></div>' : '') +
        '<div class="audsum' + (n ? '' : ' empty') + '">' + (n ? '받는 곳: <b>' + esc(TR.audText(sp)) + '</b> — 받는 선생님 「오늘」의 「나에게 온 것」에 바로 뜹니다.' : '받을 사람이나 그룹을 골라 주세요.') + '</div></div>';
      $$('input[name=am]', view).forEach(function (r) { r.onchange = function () { aud.mode = r.value; audChange(); }; });
      $$('input[data-g]', view).forEach(function (cb) { cb.onchange = function () { aud.gids = $$('input[data-g]', view).filter(function (x) { return x.checked; }).map(function (x) { return x.dataset.g; }); audChange(); }; });
      $$('input[data-pg]', view).forEach(function (cb) { cb.onchange = function () { aud.pgids = $$('input[data-pg]', view).filter(function (x) { return x.checked; }).map(function (x) { return x.dataset.pg; }); audChange(); }; });
      $$('[data-gall]', view).forEach(function (b) { b.onclick = function () { var k = b.dataset.gall; aud.gids = k === 'all' ? TR.groups.map(function (g) { return g.id; }) : k === 'grade' ? GRADES.slice() : []; if (k === 'all') aud.hrOnly = false; audChange(); }; });
      var ho = $('#hrOnly'); if (ho) ho.onchange = function () { aud.hrOnly = ho.checked; audChange(); };
      $$('input[data-p]', view).forEach(function (cb) { cb.onchange = function () { var nm = cb.dataset.p, i = aud.names.indexOf(nm), q = $('#psq').value; if (cb.checked && i < 0) aud.names.push(nm); if (!cb.checked && i >= 0) aud.names.splice(i, 1); audChange(); $('#psq').value = q; filterPeople(q); }; });
      var pc = $('[data-pclear]', view); if (pc) pc.onclick = function () { aud.names = []; audChange(); };
      var psq = $('#psq'); if (psq) psq.oninput = function () { filterPeople(psq.value); };
    }
    function filterPeople(q) {
      q = (q || '').trim();
      $$('.pck', view).forEach(function (l) { l.hidden = !!q && l.dataset.q.indexOf(q) < 0; });
      $$('.pg', view).forEach(function (g) { g.hidden = !$$('.pck', g).some(function (l) { return !l.hidden; }); });
    }

    // 시트형 직접 입력: 1행 = 항목 이름, 2행 = 형식. 앞 두 열(학년반·담임 / 직위·성명 / 그룹)은 대상에서 자동.
    function lockCols() { var sp = spec(); return sp.mode === 'pergroup' ? ['그룹', '작성'] : sp.hrOnly ? ['학년반', '담임'] : ['직위', '성명']; }
    function lockRows() { var sp = spec(); return TR.audRows(sp).map(function (r) { return sp.mode === 'pergroup' ? [r.unit, '그룹 누구나'] : sp.hrOnly ? [r.unit, r.who] : [r.pos, r.who]; }); }
    function renderSheet() {
      var L = String.fromCharCode, lk = lockCols(), units = lockRows();
      var h = '<div class="sheet-wrap"><table class="sheet"><tr class="colh"><th class="rn"></th><th>A</th><th>B</th>' + cols.map(function (_, i) { return '<th>' + L(67 + i) + '</th>'; }).join('') + '<th class="addc" rowspan="' + (multi ? 7 : 6) + '"><button class="btn sm" id="addCol" title="항목 추가">＋ 열</button></th></tr>' +
        '<tr class="hdr"><th class="rn">항목</th>' + lk.map(function (x) { return '<td class="lock"><b>' + x + '</b> <span class="small faint">자동</span></td>'; }).join('') +
        cols.map(function (c, i) { return '<td><input data-ci="' + i + '" value="' + esc(c.n) + '" placeholder="' + (i ? '항목 이름' : '예: 희망 인원') + '" aria-label="' + L(67 + i) + '열 항목 이름"></td>'; }).join('') + '</tr>' +
        '<tr class="typ"><th class="rn">형식</th><td class="lock faint">—</td><td class="lock faint">—</td>' +
        cols.map(function (c, i) { return '<td><div class="row" style="gap:4px;padding:0 4px"><select data-ti="' + i + '" aria-label="형식">' + Object.keys(TYPE_LABEL).map(function (k) { return '<option value="' + k + '"' + (k === c.t ? ' selected' : '') + '>' + TYPE_LABEL[k] + '</option>'; }).join('') + '</select>' + (cols.length > 1 ? '<button class="xbtn" data-del="' + i + '" title="이 열 지우기" aria-label="열 지우기">✕</button>' : '') + '</div>' + (c.t === 'select' ? '<input class="opts" data-oi="' + i + '" value="' + esc((c.o || []).join(', ')) + '" placeholder="보기: 예, 아니오">' : '') + '</td>'; }).join('') + '</tr>';
      units.slice(0, 3).forEach(function (u, r) {
        h += '<tr><th class="rn">' + (r + 1) + '</th><td class="lock">' + esc(u[0]) + '</td><td class="lock">' + esc(u[1]) + '</td>' + cols.map(function (c, i) { return '<td class="cellp" data-pc="' + i + '">' + (c.n ? TYPE_LABEL[c.t] + ' 입력' : '') + '</td>'; }).join('') + '</tr>';
        if (multi && r === 0) h += '<tr><th class="rn"></th><td class="lock"></td><td class="lock"></td><td class="cellp" colspan="' + cols.length + '">＋ 줄 추가 (받는 사람이 필요한 만큼)</td></tr>';
      });
      h += '<tr><th class="rn">⋮</th><td class="lock faint" colspan="2">… ' + units.length + '줄 자동</td><td class="cellp" colspan="' + cols.length + '"></td></tr></table></div>';
      $('#sheet').innerHTML = h;
      $$('[data-ci]', view).forEach(function (inp) {
        inp.oninput = function () { var i = +inp.dataset.ci; cols[i].n = inp.value; $$('[data-pc="' + i + '"]', view).forEach(function (td) { td.textContent = inp.value ? TYPE_LABEL[cols[i].t] + ' 입력' : ''; }); syncSheet(); };
        inp.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); cols.push({ n: '', t: 'text' }); renderSheet(); $('[data-ci="' + (cols.length - 1) + '"]').focus(); } };
      });
      $$('[data-ti]', view).forEach(function (s) { s.onchange = function () { cols[+s.dataset.ti].t = s.value; renderSheet(); }; });
      $$('[data-oi]', view).forEach(function (s) { s.oninput = function () { cols[+s.dataset.oi].o = s.value.split(/\s*,\s*/).filter(Boolean); }; });
      $$('[data-del]', view).forEach(function (b) { b.onclick = function () { cols.splice(+b.dataset.del, 1); renderSheet(); syncSheet(); }; });
      $('#addCol').onclick = function () { cols.push({ n: '', t: 'text' }); renderSheet(); $('[data-ci="' + (cols.length - 1) + '"]').focus(); };
    }
    function syncSheet() {
      var named = cols.filter(function (c) { return c.n.trim(); });
      if (newSrc === 'sheet') { parsed = named.length ? { head: lockCols().concat(named.map(function (c) { return c.n; })), input: named.map(function (c) { return c.n; }), rows: lockRows().length } : null; if (!$('#title').value && named.length) $('#title').value = named[0].n + ' 조사'; }
      renderMsg();
    }
    $('#multi').onchange = function () { multi = this.checked; renderSheet(); };
    $$('input[name=acc]', view).forEach(function (r) { r.onchange = renderMsg; });

    function analyze() {
      var lines = paste.value.split(/\r?\n/).filter(function (l) { return l.trim() && l.trim() !== '…'; });
      if (!lines.length) { fieldsEl.innerHTML = ''; parsed = null; renderMsg(); return; }
      var sep = lines[0].indexOf('\t') >= 0 ? '\t' : lines[0].indexOf('|') >= 0 ? '|' : /\s{2,}/;
      var head = lines[0].split(sep).map(function (s) { return s.trim(); }).filter(Boolean);
      var unitCols = head.filter(function (h) { return /학년|반|이름|성명|담임|교사|부서/.test(h); });
      var inputCols = head.filter(function (h) { return unitCols.indexOf(h) < 0; });
      parsed = { head: head, unit: unitCols, input: inputCols, rows: lines.length - 1 };
      fieldsEl.innerHTML = '<div class="small muted" style="margin-bottom:7px">머리글 ' + head.length + '개를 찾았어요. <b>회색</b>은 받는 사람 칸(미리 채움), <b>나머지</b>는 입력 칸입니다.</div><div class="chips">' +
        unitCols.map(function (h) { return '<span class="field-chip" style="background:var(--surface-2)">' + esc(h) + '<span class="small faint">미리 채움</span></span>'; }).join('') +
        inputCols.map(function (h) { var t = /인원|수|시간|금액/.test(h) ? 'number' : /프로그램|여부|희망/.test(h) ? 'select' : /일$|날짜/.test(h) ? 'date' : 'text'; return '<span class="field-chip">' + esc(h) + '<select aria-label="입력 형식"><option' + (t === 'text' ? ' selected' : '') + '>글자</option><option' + (t === 'number' ? ' selected' : '') + '>숫자</option><option' + (t === 'select' ? ' selected' : '') + '>선택지</option><option' + (t === 'date' ? ' selected' : '') + '>날짜</option></select></span>'; }).join('') + '</div>';
      var pii = head.filter(function (h) { return /학생\s?(이름|성명)|학생명|연락처|전화|주민|주소/.test(h); });
      $('#pii').innerHTML = pii.length ? '<div class="warn">학생 개인정보로 보이는 열이 있어요: ' + pii.map(esc).join(', ') + '. 티로드는 학생 개인정보를 저장하지 않습니다. 인원 수로 바꾸거나 열을 빼 주세요.</div>' : '';
      if (!$('#title').value) $('#title').value = /방과후|프로그램/.test(paste.value) ? '2학기 방과후 수요조사(추가)' : '새 취합';
      renderMsg();
    }
    function renderMsg() {
      var t = $('#title').value || '(제목)', sp = spec(), ext = ($('input[name=acc]:checked') || {}).value === 'ext';
      $('#msg').textContent = '[티로드] ' + t + '\n\n아래 링크에서 ' + (sp.mode === 'pergroup' ? '우리 그룹 칸' : sp.hrOnly ? '우리 반 칸' : '내 칸') + '만 채워 주세요. (30초)\nhttps://t.road/c/' + (parsed ? 'k7Q2' : '····') +
        (ext ? '\n\n명부에 없는 분(강사 등)은 이 링크로 제출해 주세요. 마감 후 닫힙니다.\nhttps://t.road/x/' + (parsed ? 'P4m9' : '····') : '') + '\n\n— ' + TR.me.name + ' (방과후돌봄부)';
    }
    function showSrc(src) {
      newSrc = src;
      $$('[data-src]', view).forEach(function (x) { x.classList.toggle('on', x.dataset.src === src); });
      $$('[data-pane]', view).forEach(function (p) { p.hidden = p.dataset.pane !== src && !(src === 'file' && p.dataset.pane === 'paste' && parsed); });
      $('#rest').hidden = src === 'preset';
      if (src === 'sheet') syncSheet(); else if (src === 'paste') analyze(); else renderMsg();
    }
    paste.addEventListener('input', analyze);
    paste.addEventListener('paste', function () { setTimeout(analyze, 0); });
    $('#title').addEventListener('input', renderMsg);
    $('#sample').onclick = function () { paste.value = SAMPLE_TABLE.replace('…', TR.homerooms.slice(4).map(function (h) { return h[0] + '\t' + h[1] + '\t\t\t'; }).join('\n')).replace(/\n2-1\t최수아\t\t\t$/m, ''); analyze(); };
    $$('[data-src]', view).forEach(function (b) { b.onclick = function () { showSrc(b.dataset.src); }; });
    $$('[data-preset]', view).forEach(function (b) { b.onclick = function () { makePreset(b.dataset.preset); }; });
    $('#hwpFile').onchange = function () {
      var f = this.files[0]; if (!f) return;
      if (/\.hwp$/i.test(f.name)) { $('#fileRes').innerHTML = '<div class="warn">구형 .hwp 파일이에요. 한글에서 <b>다른 이름으로 저장 → hwpx</b>로 바꿔 올려 주세요. (또는 표만 복사해 붙여넣기 탭 사용)</div>'; return; }
      $('#fileRes').innerHTML = '<div class="proto-note" style="margin-top:12px">「' + esc(f.name) + '」에서 표 2개를 찾았어요. 취합할 표를 고르세요. <span class="small">(프로토타입: 실제 파일은 읽지 않고 예시 표로 대신합니다)</span><div class="row" style="margin-top:9px"><button class="btn sm" data-tbl="1">표 1 · 결재란(2×4)</button><button class="btn sm pri" data-tbl="2">표 2 · 학년반/담임/희망 인원… (25행)</button></div></div>';
      $$('[data-tbl]', view).forEach(function (b) { b.onclick = function () { if (b.dataset.tbl === '1') { TR.toast('결재란은 취합 표가 아니에요. 표 2를 고르세요.'); return; } showSrc('paste'); $('#sample').click(); TR.toast('표 2를 가져왔어요. 내보낼 때 같은 hwpx 파일의 이 표에 채워 드립니다.'); }; });
    };
    $('#create').onclick = function () {
      if (!parsed || !parsed.input.length) { TR.toast(newSrc === 'sheet' ? '받을 항목 이름을 하나 이상 적어 주세요 (C열부터).' : '먼저 양식 표를 붙여넣어 주세요. "예시 표 넣기"를 눌러도 돼요.'); return; }
      var sp = spec(), units = TR.audRows(sp);
      if (!units.length) { TR.toast('받을 사람이나 그룹을 골라 주세요.'); return; }
      var fields = newSrc === 'sheet'
        ? cols.filter(function (c) { return c.n.trim(); }).map(function (c) { return { n: c.n.trim(), t: c.t, o: c.t === 'select' ? (c.o && c.o.length ? c.o : ['예', '아니오']) : undefined }; })
        : $$('.field-chip select', fieldsEl).map(function (sel, i) { var t = { '숫자': 'number', '선택지': 'select', '날짜': 'date' }[sel.value] || 'text'; return { n: parsed.input[i], t: t, o: t === 'select' ? TR.programs : undefined }; });
      var isItems = newSrc === 'sheet' && multi;
      if (isItems && fields.filter(function (f) { return f.t === 'number'; }).length >= 2) fields.push({ n: '금액', t: 'calc' });
      var id = 'c' + Date.now().toString(36);
      S().collections.unshift({ id: id, kind: isItems ? 'items' : undefined, title: $('#title').value, owner: TR.me.name, dept: '방과후돌봄부', mine: true, due: '', dueISO: '', ext: $('input[name=acc]:checked').value === 'ext',
        aud: sp, target: TR.audText(sp) + (isItems ? ' · 여러 줄' : ''), fields: fields,
        rows: units.map(function (u) { var r = Object.assign({ done: false, at: '' }, u); if (isItems) r.items = []; else r.v = fields.map(function () { return ''; }); return r; }) });
      TR.save(); TR.copy($('#msg').textContent, '만들었어요. 쪽지 문구를 복사했습니다. 마감은 이 화면 위에서 정하세요.');
      location.hash = '#collect/' + id;
    };
    renderAud(); renderSheet(); showSrc(newSrc);
  }

  // 합본을 표 한 장으로 (엑셀·한글 내보내기 공용)
  function itemAmount(c, it) { var nums = c.fields.map(function (f, i) { return f.t === 'number' ? +it[i] || 0 : null; }).filter(function (x) { return x !== null; }); return nums.length >= 2 ? nums.reduce(function (a, b) { return a * b; }, 1) : 0; }
  function itemVals(c, it) { return c.fields.map(function (f, i) { return f.t === 'calc' ? itemAmount(c, it) : it[i]; }); }
  function unitSpent(c, r) { return (r.items || []).reduce(function (a, it) { return a + itemAmount(c, it); }, 0); }
  function sheetOf(c) {
    if (c.kind === 'items') {
      var rows = []; c.rows.forEach(function (r) { r.items.forEach(function (it) { rows.push([r.unit, r.who].concat(itemVals(c, it))); }); });
      return { head: ['학년반', '담임'].concat(c.fields.map(function (f) { return f.n; })), rows: rows };
    }
    if (c.kind === 'matrix') return { head: ['직위', '성명'].concat(c.cols.map(function (x) { return x.n; })), rows: c.rows.map(function (r) { return [r.pos, r.who].concat(r.v); }) };
    return { head: colHead(c), rows: c.rows.map(function (r) { return [colA(c, r), colB(c, r)].concat(r.v); }) };
  }
  // 받는 줄의 앞 두 칸: 담임 반별 = 학년반·담임 / 사람별 = 직위·성명 / 그룹별 = 그룹·쓴 사람
  function audMode(c) { var a = c.aud || {}; return a.mode === 'pergroup' ? 'pg' : a.hrOnly || (!a.mode && /담임/.test(c.target)) ? 'hr' : 'p'; }
  function colHead(c) { return { pg: ['그룹', '쓴 사람'], hr: ['학년반', '담임'], p: ['직위', '성명'] }[audMode(c)]; }
  function colA(c, r) { return audMode(c) === 'p' ? r.pos || r.unit : r.unit; }
  function colB(c, r) { return audMode(c) === 'pg' ? (r.by || '') : r.who; }
  function rowLabel(c, r) { var m = c.kind === 'matrix' ? 'p' : audMode(c); return m === 'hr' || c.kind === 'items' ? r.unit + ' ' + r.who : m === 'pg' ? r.unit : r.who; }

  var phoneUnit = {};
  function collectDetail(id, _x, soft) {
    var c = S().collections.find(function (x) { return x.id === id; }); if (!c) { location.hash = '#collect'; return; }
    var p = progress(c), miss = c.rows.filter(function (r) { return !r.done; });
    if (!phoneUnit[id] || !c.rows.some(function (r) { return r.unit === phoneUnit[id]; })) phoneUnit[id] = ((c.rows.find(mineRow) && c.mine ? c.rows.find(mineRow) : miss[0]) || c.rows[0]).unit;
    var lastAt = TR._lastSubmit && TR._lastSubmit.c === id ? TR._lastSubmit.u : null;
    var kpis = '', table = '', extra = '';
    var stamp = function (r) { return r.done ? '<span class="badge ok">' + esc(r.at || '제출') + '</span>' : '<span class="badge red">미제출</span>'; };

    if (c.kind === 'items') {
      var total = c.rows.reduce(function (a, r) { return a + unitSpent(c, r); }, 0), budget = c.budget * c.rows.length;
      var overN = c.rows.filter(function (r) { return unitSpent(c, r) > c.budget; }).length;
      kpis = '<div class="card kpi"><div class="n">' + p.d + '<span class="muted" style="font-size:21px">/' + p.t + '</span></div><div class="l">제출한 반 · ' + p.p + '%</div><div class="bar" style="margin-top:9px"><i style="width:' + p.p + '%"></i></div></div>' +
        '<div class="card kpi"><div class="n">' + money(total) + '<span class="muted" style="font-size:17px">원</span></div><div class="l">신청 합계 / 예산 ' + money(budget) + '원</div></div>' +
        '<div class="card kpi"><div class="n" style="color:' + (overN ? 'var(--danger)' : 'var(--ok)') + '">' + overN + '</div><div class="l">예산 넘은 반 · 학년 안에서 조정 가능</div></div>';
      var grades = {}; c.rows.forEach(function (r) { var g = /^\d/.test(r.unit) ? r.unit[0] + '학년' : r.unit; grades[g] = grades[g] || { n: 0, s: 0 }; grades[g].n++; grades[g].s += unitSpent(c, r); });
      extra = '<section class="card" style="margin-bottom:18px"><div class="card-h"><h2>학년별 예산 현황</h2><span class="small muted">학급 ' + money(c.budget) + '원 × 반 수 · 자동 계산</span></div><div class="scroll" style="max-height:none"><table class="tbl"><tr><th>학년</th><th>반</th><th>예산</th><th>사용액</th><th>잔액</th></tr>' +
        Object.keys(grades).map(function (g) { var x = grades[g], b = x.n * c.budget, left = b - x.s; return '<tr><td><b>' + esc(g) + '</b></td><td>' + x.n + '</td><td>' + money(b) + '</td><td>' + money(x.s) + '</td><td class="' + (left < 0 ? 'over' : '') + '">' + money(left) + '</td></tr>'; }).join('') + '</table></div></section>';
      var fh = c.fields.map(function (f) { return '<th>' + esc(f.n) + '</th>'; }).join('');
      table = '<tr><th>학년반</th><th>담임</th>' + fh + '<th>제출</th></tr>' + c.rows.map(function (r) {
        var spent = unitSpent(c, r), left = c.budget - spent, its = r.items.length ? r.items : [null], cls = (r.done ? '' : 'pending') + (lastAt === r.unit ? ' flash' : '');
        if (!r.items.length) return '<tr class="' + cls + '"><td><b>' + esc(r.unit) + '</b></td><td>' + esc(r.who) + '</td><td colspan="' + c.fields.length + '" class="faint">아직 신청한 물품이 없어요</td><td>' + stamp(r) + '</td></tr>';
        return its.map(function (it, k) {
          return '<tr class="' + cls + '">' + (k === 0 ? '<td rowspan="' + (its.length + 1) + '" style="vertical-align:top"><b>' + esc(r.unit) + '</b></td><td rowspan="' + (its.length + 1) + '" style="vertical-align:top">' + esc(r.who) + '</td>' : '') +
            (it ? itemVals(c, it).map(function (v, i) { var f = c.fields[i]; return '<td' + (f.t === 'number' || f.t === 'calc' ? ' class="num"' : '') + '>' + (f.t === 'url' && v ? '<a href="javascript:void 0" title="' + esc(v) + '">링크</a>' : f.t === 'calc' || /단가|금액|가격|원/.test(f.n) ? money(v) : esc(v)) + '</td>'; }).join('') : '<td colspan="' + c.fields.length + '" class="faint">아직 신청한 물품이 없어요</td>') +
            (k === 0 ? '<td rowspan="' + (its.length + 1) + '" style="vertical-align:top">' + stamp(r) + '</td>' : '') + '</tr>';
        }).join('') + '<tr class="sub"><td colspan="' + c.fields.length + '">소계 <b>' + money(spent) + '원</b> · 잔액 <b class="' + (left < 0 ? 'over' : '') + '">' + money(left) + '원</b>' + (left < 0 ? ' · 예산 초과' : '') + '</td></tr>';
      }).join('');
    } else if (c.kind === 'matrix') {
      var colStat = c.cols.map(function (_, j) { var ok = 0, need = 0; c.rows.forEach(function (r) { if (r.v[j] === '해당 없음') return; need++; if (r.v[j]) ok++; }); return { ok: ok, need: need, p: need ? Math.round(ok / need * 100) : 100 }; });
      var low = colStat.map(function (s, j) { return [s.p, j]; }).sort(function (a, b) { return a[0] - b[0]; })[0];
      kpis = '<div class="card kpi"><div class="n">' + p.p + '<span class="muted" style="font-size:21px">%</span></div><div class="l">칸 이수율 · ' + p.d + '/' + p.t + '칸</div><div class="bar" style="margin-top:9px"><i style="width:' + p.p + '%"></i></div></div>' +
        '<div class="card kpi"><div class="n">' + p.people + '<span class="muted" style="font-size:21px">/' + c.rows.length + '</span></div><div class="l">모든 연수 마친 사람</div></div>' +
        '<div class="card kpi"><div class="l">가장 덜 된 연수</div><div style="margin-top:5px;font-size:16px"><b>' + esc(c.cols[low[1]].n) + '</b> ' + low[0] + '%<br><span class="small muted">' + esc(c.cols[low[1]].due) + ' · 담당 ' + esc(c.cols[low[1]].by) + '</span></div></div>';
      extra = '<section class="card" style="margin-bottom:18px"><div class="card-h"><h2>연수 목록</h2><span class="small muted">' + c.cols.length + '개 · 연수별 이수율</span></div><div class="scroll" style="max-height:none"><table class="tbl"><tr><th>순</th><th>연수명</th><th>기간</th><th>대상</th><th>담당</th><th style="width:207px">이수</th></tr>' +
        c.cols.map(function (x, j) { var s = colStat[j]; return '<tr><td>' + (j + 1) + '</td><td><b>' + esc(x.n) + '</b></td><td>' + esc(x.due) + '</td><td class="small">' + esc(x.target) + '</td><td>' + esc(x.by) + '</td><td><div class="row"><div class="bar grow' + (s.p < 60 ? ' red' : '') + '"><i style="width:' + s.p + '%"></i></div><span class="small">' + s.ok + '/' + s.need + '</span></div></td></tr>'; }).join('') + '</table></div></section>';
      table = '<tr><th>직위</th><th>성명</th>' + c.cols.map(function (x, j) { return '<th class="v" title="' + esc(x.n) + '">' + (j + 1) + '. ' + esc(x.n) + '</th>'; }).join('') + '<th>상태</th></tr>' +
        c.rows.map(function (r) {
          var n = r.v.filter(Boolean).length;
          return '<tr class="' + (r.who === TR.me.name ? 'me' : '') + (lastAt === r.unit ? ' flash' : '') + '"><td class="small">' + esc(r.pos) + '</td><td><b>' + esc(r.who) + '</b></td>' + r.v.map(function (v) { return v === '해당 없음' ? '<td class="mxc na" title="해당 없음">－</td>' : v ? '<td class="mxc ok" title="' + esc(v) + '">✓</td>' : '<td class="mxc no" title="미이수">·</td>'; }).join('') +
            '<td>' + (r.done ? '<span class="badge ok">모두 이수</span>' : '<span class="small muted">' + n + '/' + r.v.length + '</span>') + '</td></tr>';
        }).join('') + '<tr class="sub"><td colspan="2">이수율</td>' + colStat.map(function (s) { return '<td class="mxc' + (s.p < 60 ? ' over' : '') + '">' + s.p + '%</td>'; }).join('') + '<td></td></tr>';
    } else {
      var sums = c.fields.map(function (f, i) { if (f.t !== 'number') return ''; var s = c.rows.reduce(function (a, r) { return a + (+r.v[i] || 0); }, 0); return esc(f.n) + ' 합계 <b>' + s + '</b>'; }).filter(Boolean).join(' · ');
      var sel = c.fields.find(function (f) { return f.t === 'select'; }), selIdx = c.fields.indexOf(sel), dist = '';
      if (sel) { var cnt = {}; c.rows.forEach(function (r) { if (r.v[selIdx]) cnt[r.v[selIdx]] = (cnt[r.v[selIdx]] || 0) + 1; }); dist = Object.keys(cnt).map(function (k) { return esc(k) + ' ' + cnt[k]; }).join(' · '); }
      kpis = '<div class="card kpi"><div class="n">' + p.d + '<span class="muted" style="font-size:21px">/' + p.t + '</span></div><div class="l">제출 · ' + p.p + '%</div><div class="bar" style="margin-top:9px"><i style="width:' + p.p + '%"></i></div></div>' +
        '<div class="card kpi"><div class="n" style="color:var(--danger)">' + miss.length + '</div><div class="l">미제출 · ' + (c.due ? '08:20 자동 리마인드 예정' : '마감을 정하면 리마인드') + '</div></div>' +
        '<div class="card kpi"><div class="l">실시간 합본</div><div style="margin-top:5px;font-size:15px">' + (sums || '—') + (dist ? '<br>' + dist : '') + '</div></div>';
      var ch = colHead(c);
      table = '<tr><th>' + ch[0] + '</th><th>' + ch[1] + '</th>' + c.fields.map(function (f) { return '<th>' + esc(f.n) + '</th>'; }).join('') + '<th>제출</th></tr>' + c.rows.map(function (r) {
        return '<tr class="' + (r.done ? '' : 'pending') + (lastAt === r.unit ? ' flash' : '') + '"><td><b>' + esc(colA(c, r)) + '</b></td><td>' + (audMode(c) === 'pg' && !r.by ? '<span class="faint">그룹 누구나</span>' : esc(colB(c, r))) + '</td>' + r.v.map(function (v) { return '<td>' + (v ? esc(v) : '<span class="faint">—</span>') + '</td>'; }).join('') + '<td>' + stamp(r) + '</td></tr>';
      }).join('');
    }

    var missLabel = c.kind === 'matrix' ? '덜 마친 사람' : c.kind === 'items' ? '미제출 반' : '미제출';
    var missUnit = { '반': '개 반', '그룹': '곳', '명': '명', '칸': '명' }[p.unit];
    var missShow = miss.slice(0, 18);
    // 마감은 만들 때 받지 않고 여기(관리)서 정한다
    var dueISO = (c.dueISO || '').split('T');
    var dueBox = c.mine ? '<section class="card duebox' + (c.due ? '' : ' need') + '"><div class="card-b"><div class="row" style="flex-wrap:wrap;gap:12px"><b style="font-size:17px">' + (c.due ? '마감 ' + esc(c.due) : '마감을 아직 정하지 않았어요') + '</b><span class="small muted grow">' + (c.due ? '바꾸면 받는 선생님 「나에게 온 것」·🔔의 마감도 함께 바뀝니다.' : '만들 때는 마감을 받지 않아요. 정하면 받는 선생님께 마감과 리마인드가 갑니다.') + '</span></div>' +
      '<div class="row" style="flex-wrap:wrap;margin-top:12px"><input type="date" id="dd" class="btn sm" value="' + (dueISO[0] || TR.addDays(TR.TODAY, 4)) + '"><input type="time" id="dt" class="btn sm" value="' + (dueISO[1] || '17:00') + '"><label class="toggle"><input type="checkbox" id="drm" checked> D-1 · 당일 08:20 자동 리마인드</label><button class="btn sm pri" id="dsave" style="margin-left:auto">' + (c.due ? '마감 바꾸기' : '마감 정하기') + '</button></div></div></section>' : '';
    view.innerHTML = '<div class="page-h"><div><div class="small"><a href="#collect">모으기</a> /</div><h1>' + esc(c.title) + '</h1><p>' + esc(c.dept) + ' ' + esc(c.owner) + ' · ' + esc(c.target) + ' · ' + esc(c.due ? '마감 ' + c.due : '마감 미정') + (c.ext ? ' · <span class="badge acc">외부 링크 허용</span>' : '') + '</p></div>' +
      '<div class="row"><button class="btn" id="csv">엑셀로 내보내기</button><button class="btn" id="cptbl" title="한글 문서의 표 자리에 붙여넣으세요">합본 표 복사 → 한글에 붙여넣기</button>' + (c.kind ? '' : '<button class="btn" id="hwpx">원본 hwpx에 채워 받기 <span class="badge">v1</span></button>') + (c.mine ? '<button class="btn" id="lock">마감하고 잠그기</button>' : '') + '</div></div>' +
      dueBox + (c.guide ? '<div class="guide"><b>안내</b><ol>' + c.guide.map(function (g) { return '<li>' + esc(g) + '</li>'; }).join('') + '</ol></div>' : '') +
      '<div class="split"><div>' +
      '<div class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))">' + kpis + '</div>' +
      (miss.length ? '<section class="card" style="margin-bottom:18px"><div class="card-h"><h2>' + missLabel + ' ' + miss.length + missUnit + '</h2><button class="btn sm pri" id="remindAll">한 번에 독촉 문구 복사</button></div><div class="card-b"><div class="miss">' + missShow.map(function (r) { return '<button data-r="' + esc(r.unit) + '">' + esc(rowLabel(c, r)) + '</button>'; }).join('') + (miss.length > missShow.length ? '<span class="small muted" style="align-self:center">외 ' + (miss.length - missShow.length) + missUnit + '</span>' : '') + '</div><p class="small muted" style="margin:9px 0 0">' + (audMode(c) === 'pg' ? '그룹을 누르면 그 그룹 선생님들께 보낼 쪽지 문구가 복사됩니다' : '이름을 누르면 그 선생님께 보낼 쪽지 문구가 복사됩니다') + (c.kind === 'matrix' ? ' (안 한 연수만 적어서)' : '') + '. 일일이 명단과 대조하지 않아도 돼요.</p></div></section>' : '<div class="proto-note" style="margin-bottom:18px">모두 제출했습니다. 합본을 내려받으세요.</div>') +
      extra +
      '<section class="card"><div class="card-h"><h2>합본</h2><span class="small muted">제출되는 즉시 반영</span></div><div class="scroll"><table class="tbl' + (c.kind === 'matrix' ? ' mx' : '') + '">' + table + '</table></div></section>' +
      '</div><aside class="no-print"><div class="row" style="margin-bottom:9px"><b class="grow">받는 사람 화면 (PC)</b><select id="pu" style="border:1px solid var(--line);border-radius:9px;padding:5px 7px;background:var(--surface);max-width:230px">' + c.rows.map(function (r) { return '<option value="' + esc(r.unit) + '"' + (r.unit === phoneUnit[id] ? ' selected' : '') + '>' + esc(c.kind === 'matrix' ? r.who + ' · ' + r.pos : rowLabel(c, r)) + '</option>'; }).join('') + '</select></div>' +
      pcWin('recv.html?c=' + id + '&u=' + encodeURIComponent(phoneUnit[id]), 't.road/c/k7Q2?u=' + phoneUnit[id], 736, 'phone') + '<p class="small muted">쪽지 링크를 누른 선생님의 PC 브라우저 화면입니다. 여기서 제출하면 왼쪽 합본에 바로 들어옵니다. 직접 눌러 보세요.</p></aside></div>';
    TR._lastSubmit = null;
    $$('.miss button', view).forEach(function (b) { b.onclick = function () { var r = c.rows.find(function (x) { return x.unit === b.dataset.r; }); var what = c.kind === 'matrix' ? '아직 이수번호가 없는 연수: ' + c.cols.filter(function (_, j) { return !r.v[j]; }).map(function (x) { return x.n; }).join(', ') : (audMode(c) === 'pg' ? r.unit + ' 그룹' : audMode(c) === 'hr' ? r.unit : '선생님') + ' 칸이 아직 비어 있어요.'; TR.copy((audMode(c) === 'pg' ? r.unit + ' 선생님들' : r.who + ' 선생님') + ', 「' + c.title + '」 ' + what + ' ' + TR.dueText(c) + '\nhttps://t.road/c/k7Q2?u=' + encodeURIComponent(r.unit), rowLabel(c, r) + ' 쪽 문구를 복사했어요.'); }; });
    var ra = $('#remindAll'); if (ra) ra.onclick = function () { TR.copy('[티로드] 「' + c.title + '」 아직 다 못 채운 ' + (audMode(c) === 'pg' ? '그룹' : '분') + ': ' + miss.map(function (r) { return rowLabel(c, r); }).join(', ') + '\n' + TR.dueText(c) + ' · 링크에서 ' + (c.kind === 'matrix' ? '내 줄' : audMode(c) === 'pg' ? '우리 그룹 칸' : audMode(c) === 'hr' ? '우리 반 칸' : '내 칸') + '만 채워 주세요.\nhttps://t.road/c/k7Q2', missLabel + ' ' + miss.length + ' 독촉 문구를 복사했어요.'); };
    $('#pu').onchange = function () { phoneUnit[id] = this.value; $('#phone').src = 'recv.html?c=' + id + '&u=' + encodeURIComponent(this.value); };
    $('#csv').onclick = function () {
      var sh = sheetOf(c), lines = [sh.head].concat(sh.rows);
      var csv = '﻿' + lines.map(function (l) { return l.map(function (x) { return '"' + String(x == null ? '' : x).replace(/"/g, '""') + '"'; }).join(','); }).join('\r\n') + '\r\n"티로드로 모았습니다 · t.road"';
      var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); a.download = c.title + '_합본.csv'; a.click();
      TR.toast('합본을 내려받았어요 (엑셀에서 열림).');
    };
    var hx = $('#hwpx'); if (hx) hx.onclick = function () { TR.toast('v1 기능: hwpx 파일로 시작한 취합이면, 그 파일의 같은 표에 값을 채워 hwpx로 돌려드립니다(본문·결재란 유지).'); };
    $('#cptbl').onclick = function () {
      var sh = sheetOf(c), all = [sh.head].concat(sh.rows);
      var tsv = all.map(function (l) { return l.join('\t'); }).join('\n');
      var html = '<table border="1">' + all.map(function (l, i) { return '<tr>' + l.map(function (x) { return (i ? '<td>' : '<th>') + esc(x == null ? '' : x) + (i ? '</td>' : '</th>'); }).join('') + '</tr>'; }).join('') + '</table>';
      var msg = '합본 표를 복사했어요. 한글 문서에서 Ctrl+V 하면 표로 들어갑니다.';
      if (navigator.clipboard && window.ClipboardItem && window.isSecureContext) navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([html], { type: 'text/html' }), 'text/plain': new Blob([tsv], { type: 'text/plain' }) })]).then(function () { TR.toast(msg); }, function () { TR.copy(tsv, msg); });
      else TR.copy(tsv, msg);
    };
    var lk = $('#lock'); if (lk) lk.onclick = function () { TR.toast('마감했어요. 늦은 제출은 "지각" 표시와 함께 받습니다.'); };
    var ds = $('#dsave'); if (ds) ds.onclick = function () {
      var d = $('#dd').value, t = $('#dt').value || '17:00'; if (!d) { TR.toast('마감 날짜를 골라 주세요.'); return; }
      var first = !c.due; c.due = (d === TR.TODAY ? '오늘 ' : TR.fmt(d) + ' ') + t; c.dueISO = d + 'T' + t; c.urgent = d === TR.TODAY; c.remind = $('#drm').checked; TR.save();
      TR.copy('[티로드] 「' + c.title + '」 마감 ' + (first ? '안내' : '변경') + ': ' + c.due + '\n아직 안 채운 분은 링크에서 ' + (c.kind === 'matrix' ? '내 줄' : audMode(c) === 'pg' ? '우리 그룹 칸' : audMode(c) === 'hr' ? '우리 반 칸' : '내 칸') + '만 채워 주세요.\nhttps://t.road/c/k7Q2', '마감을 ' + c.due + '로 ' + (first ? '정했어요' : '바꿨어요') + '. 받는 선생님 화면에 바로 반영되고, 안내 문구를 복사했어요.');
      collectDetail(id);
    };
  }

  // iframe 제출 신호 → 강조 표시용
  window.addEventListener('message', function (e) { if (e.data && e.data.troad === 'submitted') { TR._lastSubmit = { c: e.data.c, u: e.data.u }; TR.state = TR.load(); softRender(); TR.toast(e.data.u + ' 제출이 합본에 들어왔어요.'); } if (e.data && e.data.troad === 'acked') { TR.state = TR.load(); softRender(); } });

  // ---------- 게시판: 공지 · 수리요청 · 양식 자료실 ----------
  var formCat = '전체', formSort = 'new', formQ = '', formPage = 1;
  var REPAIR_OWNER = { '시설': '행정실 탁현수 주무관', '정보': TR.vendor.name + ' (외부 업체)' };
  var STATUS_BADGE = { '접수': 'red', '방문 예정': 'acc', '처리 중': 'acc', '완료': 'ok' };
  function board(tab, sub) {
    tab = tab || 'notice';
    var st = S();
    var tabs = [['notice', '공지', st.notices.filter(function (n) { return n.must && !st.acked[n.id]; }).length], ['repair', '시설 수리', st.repairs.filter(function (r) { return r.cat === '시설' && r.status !== '완료'; }).length]];
    var act = tab === 'notice' ? '<button class="btn pri" id="nn">＋ 공지 쓰기</button>' : tab === 'repair' ? '<button class="btn pri" id="nr">＋ 시설 수리 요청</button>' : '';
    var head = '<div class="page-h"><div><h1>게시판</h1><p>' + { notice: '고칠 때마다 판(v1, v2…)이 올라가고, 바뀐 부분만 다시 보여 줍니다.', repair: '교실·화장실·전기·냉난방 같은 시설 고장은 행정실이 받습니다. 학교 안 직원만 봅니다.' }[tab] + '</p></div>' + act + '</div>' +
      '<nav class="btabs">' + tabs.map(function (t) { return '<a href="#board/' + t[0] + '" class="' + (t[0] === tab ? 'on' : '') + '">' + t[1] + (t[2] ? '<span class="cnt">' + t[2] + '</span>' : '') + '</a>'; }).join('') + '</nav>';
    view.innerHTML = head + '<div id="bbody"></div>';
    var bb = $('#bbody');
    if (tab === 'notice') {
      bb.innerHTML = '<div class="card">' + st.notices.map(function (n) {
        return '<a class="crow" href="#notice/' + n.id + '" style="grid-template-columns:minmax(0,1fr) 230px 184px"><div><div class="row"><b>' + esc(n.title) + '</b>' + (n.ver > 1 ? '<span class="badge acc">v' + n.ver + ' 바뀜</span>' : '') + (n.must ? '<span class="badge pri">확인 필수</span>' : '') + (n.must && !st.acked[n.id] ? '<span class="badge red">내 확인 전</span>' : '') + '</div><div class="small muted">' + esc(n.dept) + ' ' + esc(n.by) + ' · ' + esc(n.updated) + '</div></div>' +
          '<div><div class="bar"><i style="width:' + Math.round(n.seen / n.total * 100) + '%"></i></div><div class="small muted" style="margin-top:5px">확인 ' + n.seen + '/' + n.total + '</div></div><div class="small muted">' + (n.real ? '실질 변경 · 재확인 ' + n.seenV2 + '명' : '—') + '</div></a>';
      }).join('') + '</div>';
      $('#nn').onclick = function () { TR.toast('프로토타입: 공지 쓰기는 일정 한 줄 입력과 같은 방식(칩)으로 만듭니다.'); };
    }
    if (tab === 'repair') repairTab(bb, '시설');
  }

  // 수리요청은 받는 사람으로 나눈다: 시설 = 행정실(교내 게시판), 정보 = 외부 업체(교외 · 티로드)
  function repairTab(bb, cat) {
    var st = S(), list = st.repairs.filter(function (r) { return r.cat === cat; });
    var v = TR.vendor, open = list.filter(function (r) { return r.status !== '완료'; }).length;
    var rows = '<div class="card">' + (list.map(function (r) {
      return '<a class="crow rrow" href="javascript:void 0" data-rp="' + r.id + '"><div><div class="row"><b>' + esc(r.title) + '</b>' + (r.urgent && r.status !== '완료' ? '<span class="badge red">급함</span>' : '') + '</div><div class="small muted">' + esc(r.place) + ' · ' + esc(TR.byline(r.by)) + ' · ' + esc(r.at) + '</div>' + (r.reply ? '<div class="small" style="margin-top:3px">↳ ' + esc(r.reply) + '</div>' : '') + '</div>' +
        '<div class="small muted">' + esc(REPAIR_OWNER[r.cat]) + '</div><div><span class="badge ' + STATUS_BADGE[r.status] + '">' + esc(r.status) + '</span></div></a>';
    }).join('') || '<div class="card-b muted">요청이 없어요.</div>') + '</div>';
    var side = cat === '시설'
      ? '<section class="card"><div class="card-h"><h2>담당</h2></div><div class="card-b" style="display:grid;gap:14px">' +
        '<div><div class="row"><span class="badge dut">시설</span><b>행정실 · 탁현수 주무관</b></div><p class="small muted" style="margin:5px 0 0">학교 교직원 계정 그대로. 외부 사람은 이 게시판을 볼 수 없습니다.</p></div>' +
        '<div class="pubwarn"><span>🌐</span><div><b>PC·전자칠판·네트워크 고장은?</b><br>외부 유지보수 업체가 받기 때문에 학교 밖 공간(티로드)의 <b>정보 수리요청</b>에 있습니다.<div style="margin-top:8px"><a class="btn sm" href="#out/it">정보 수리요청으로 →</a></div></div></div></div></section>'
      : '<section class="card"><div class="card-h"><h2>이 게시판을 보는 사람</h2></div><div class="card-b" style="display:grid;gap:14px">' +
        '<div class="row" style="flex-wrap:wrap"><span class="aud lock">🔒 ' + esc(TR.school.name) + ' 교직원</span><span class="aud pub">🛠 ' + esc(v.name) + ' (계약 업체 1곳)</span></div>' +
        '<div class="vcard"><div class="row"><b>' + esc(v.name) + '</b><span class="badge">외부 업체 계정</span></div>' +
        '<dl class="kv small" style="margin-top:9px;grid-template-columns:96px 1fr;font-size:15px"><dt>볼 수 있는 것</dt><dd><b>정보 수리요청만</b>. 교내 공간(일정·공지·명부·모으기)은 잠겨 있음</dd><dt>로그인</dt><dd>담당자 휴대폰 ' + esc(v.phone) + ' 문자 인증 · 비밀번호 없음</dd><dt>사용 기간</dt><dd>~' + esc(v.until) + ' (계약 끝나면 자동 만료)</dd><dt>요청자 표시</dt><dd>이름 대신 <b>위치</b>만 (예: 4-2 교실)</dd><dt>최근 접속</dt><dd>' + esc(v.lastLogin) + '</dd><dt>초대한 사람</dt><dd>정보부장 ' + esc(v.invitedBy) + '</dd></dl>' +
        '<div class="row" style="margin-top:12px;flex-wrap:wrap"><button class="btn sm pri" id="vprev">업체 화면 미리보기</button><button class="btn sm" id="vswap">담당자 바꾸기</button><button class="btn sm" id="vlog">접속 기록</button></div></div>' +
        '<p class="small muted" style="margin:0">업체 계정은 정보부장이 <b>휴대폰 번호 하나로 초대</b>합니다. 담당자가 바뀌면 번호만 바꾸면 이전 사람은 바로 막힙니다. 이 업체는 티로드를 쓰는 다른 학교 ' + (v.schools - 1) + '곳도 맡지만, 학교끼리 서로의 요청은 보이지 않습니다.</p></div></section>';
    bb.innerHTML = '<div class="split"><div><div class="small muted" style="margin-bottom:9px">진행 중 ' + open + '건 · 전체 ' + list.length + '건</div>' + rows + '</div><aside>' + side + '</aside></div>';
    $$('[data-rp]', bb).forEach(function (a) { a.onclick = function () { repairView(a.dataset.rp); }; });
    $('#nr').onclick = function () { repairNew(cat); };
    if (cat === '정보') {
      $('#vprev').onclick = vendorPreview;
      $('#vswap').onclick = function () { TR.toast('새 담당자 휴대폰 번호를 넣으면 초대 문자가 가고, 이전 담당자 접속은 즉시 끊깁니다.'); };
      $('#vlog').onclick = function () { TR.toast('최근 30일 접속 12회 · 모두 정보 수리요청 (프로토타입)'); };
    }
  }
  function repairNew(cat) {
    var info = cat === '정보';
    modal('<div class="card-h"><h2>' + (info ? '정보 수리 요청' : '시설 수리 요청') + '</h2><button class="btn sm ghost" data-close aria-label="닫기">✕</button></div><div class="card-b" style="display:grid;gap:14px">' +
      '<label class="fld">위치<input id="rpl" value="' + esc(TR.me.cls) + ' 교실"></label>' +
      '<label class="fld">무엇이 안 되나요<input id="rti" placeholder="' + (info ? '예: 전자칠판 소리가 안 나요' : '예: 교실 뒷문이 잘 안 닫혀요') + '"></label>' +
      '<label class="fld">메모 <span class="small muted">언제 오면 좋은지 등 (선택)</span><input id="rno" placeholder="예: 2블록 수업 중 말고"></label>' +
      '<label class="drop" style="min-height:92px"><b>사진 끌어다 놓기 (선택)</b><span class="small muted">사진 한 장이 설명 열 줄보다 빠릅니다</span></label>' +
      '<label class="toggle"><input type="checkbox" id="rur"> 급함 — 오늘 수업에 지장</label>' +
      (info ? '<div class="pubwarn"><span>🛠</span><div><b>이 요청은 외부 업체(' + esc(TR.vendor.name) + ')가 봅니다.</b><br>업체에는 선생님 이름 대신 위치만 보이고, 담당자에게 문자 알림이 갑니다. 학생 이름·개인정보는 적지 마세요.</div></div>'
        : '<p class="small muted" style="margin:0">🔒 행정실 시설 담당(탁현수 주무관) 🔔에 바로 뜹니다. 학교 밖 사람은 볼 수 없어요.</p>') +
      '<div class="row" style="justify-content:flex-end"><button class="btn" data-close>취소</button><button class="btn pri" id="rsave">요청하기</button></div></div>');
    $('#rsave').onclick = function () {
      var t = $('#rti').value.trim(); if (!t) { TR.toast('무엇이 안 되는지 한 줄만 적어 주세요.'); return; }
      S().repairs.unshift({ id: 'r' + Date.now().toString(36), cat: cat, title: t, place: $('#rpl').value.trim(), by: TR.me.name, at: '10/26(월) ' + new Date().toTimeString().slice(0, 5), status: '접수', urgent: $('#rur').checked, note: $('#rno').value.trim() });
      TR.save(); closeModal(); route(); TR.toast(info ? TR.vendor.name + '에 접수했어요.' : '행정실에 접수했어요.');
    };
  }
  function repairView(id) {
    var r = S().repairs.find(function (x) { return x.id === id; });
    modal('<div class="card-h"><h2>' + esc(r.title) + '</h2><span class="badge ' + STATUS_BADGE[r.status] + '">' + esc(r.status) + '</span></div><div class="card-b"><dl class="kv"><dt>분류</dt><dd>' + r.cat + '</dd><dt>위치</dt><dd>' + esc(r.place) + '</dd><dt>요청</dt><dd>' + esc(TR.byline(r.by)) + ' · ' + esc(r.at) + '</dd><dt>담당</dt><dd>' + esc(REPAIR_OWNER[r.cat]) + '</dd>' + (r.note ? '<dt>메모</dt><dd>' + esc(r.note) + '</dd>' : '') + (r.reply ? '<dt>답변</dt><dd>' + esc(r.reply) + '</dd>' : '') + '</dl>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn sm pri" data-close>닫기</button></div></div>');
  }
  // 외부 업체가 보는 화면: 정보 수리요청만, 요청자 이름 없이 위치만, 담당 학교 여러 곳
  function vendorBody() {
    var v = TR.vendor, list = S().repairs.filter(function (r) { return r.cat === '정보'; });
    return '<div class="vend"><div class="vend-h"><b>' + esc(v.name) + '</b><span class="small muted">정보 유지보수 · 담당 학교 ' + v.schools + '곳</span><select class="btn sm" style="margin-left:auto"><option>' + TR.school.name + ' (' + list.filter(function (r) { return r.status !== '완료'; }).length + ')</option><option>티로드중학교 (2)</option><option>새길초등학교 (0)</option><option>… 외 4곳</option></select></div>' +
      list.map(function (r) {
        return '<div class="vrow"><div class="grow"><div class="row"><b>' + esc(r.title) + '</b>' + (r.urgent && r.status !== '완료' ? '<span class="badge red">급함</span>' : '') + '</div><div class="small muted">' + esc(r.place) + ' · ' + esc(r.at) + (r.note ? ' · ' + esc(r.note) : '') + '</div></div>' +
          '<div class="seg">' + ['접수', '방문 예정', '처리 중', '완료'].map(function (s) { return '<button data-vs="' + r.id + '" data-s="' + s + '" class="' + (r.status === s ? 'on' : '') + '">' + s + '</button>'; }).join('') + '</div></div>';
      }).join('') + '<p class="small muted" style="margin:12px 0 0">업체 담당자에게는 이 화면만 열립니다. 요청한 선생님 이름·학교 일정·명부는 보이지 않아요.</p></div>';
  }
  function vendorBind(root, redraw) {
    $$('[data-vs]', root).forEach(function (b) { b.onclick = function () { var r = S().repairs.find(function (x) { return x.id === b.dataset.vs; }); r.status = b.dataset.s; TR.save(); redraw(); TR.toast('업체가 「' + r.title + '」을 ' + r.status + '(으)로 바꿨어요 → 요청한 선생님 🔔'); }; });
  }
  function vendorPreview() {
    var v = TR.vendor;
    modal('<div class="card-h"><h2>업체 화면 미리보기</h2><button class="btn sm ghost" data-close aria-label="닫기">✕</button></div><div class="card-b" style="background:var(--surface-2)"><div class="pcwin"><div class="pcbar"><i></i><i></i><i></i><span>troad.kr · ' + esc(v.name) + ' (휴대폰 인증으로 로그인)</span></div><div id="vbody">' + vendorBody() + '</div></div></div>');
    $('.modal').style.width = '874px';
    function redraw() { $('#vbody').innerHTML = vendorBody(); vendorBind($('#vbody'), redraw); }
    vendorBind($('#vbody'), redraw);
    $('#modal').addEventListener('click', function once(e) { if (!$('#modal').classList.contains('show')) { $('#modal').removeEventListener('click', once); if (/out\/it/.test(location.hash)) route(); } });
  }

  // ---------- 교외(티로드) 공간: 양식 자료실 · 협력업체 · 정보 수리요청 ----------
  // 우리 학교 일정·공지·모으기·명부는 교외 본문에 싣지 않는다
  var ZONE_AUD = {
    forms: ['<span class="aud pub">🌐 누구나 · 로그인 없이</span>'],
    vendors: ['<span class="aud pub">🌐 누구나 보기</span>', '<span class="aud">✍ 별점·후기는 인증 교사만</span>'],
    it: ['<span class="aud lock">🔒 ' + TR.school.name + ' 교직원</span>', '<span class="aud pub">🛠 ' + TR.vendor.name + '</span>']
  };
  function setZone(zone, sub) {
    document.body.dataset.zone = zone;
    document.body.dataset.role = role;
    $('#zin').href = lastIn;
    document.title = (zone === 'in' ? TR.school.name.replace(/등학교$/, '') + ' — 교내' : '티로드 — 교외');
    var txt = role === 'vendor'
      ? '<b>' + esc(TR.vendor.name) + ' 업체 계정</b> — 정보 수리요청과 공개 자료만 열립니다. 학교 안(교내) 공간은 잠겨 있어요.'
      : zone === 'in'
        ? '<b>' + esc(TR.school.name) + ' 교내</b> — 우리 학교 교직원만 보는 공간입니다. 외부 업체·다른 학교·로그인 안 한 사람은 들어올 수 없어요.'
        : '<b>티로드 교외</b> — 학교 밖 공간입니다. 다른 학교 선생님과 업체도 들어옵니다. 우리 학교 일정·공지·모으기·명부는 여기 나오지 않아요.';
    var aud = zone === 'in' ? ['<span class="aud lock">🔒 ' + esc(TR.school.name) + ' 교직원 ' + S().members.length + '명만</span>'] : ZONE_AUD[sub] || [];
    $('#zstrip').innerHTML = '<div class="zstrip-in"><span class="zi" aria-hidden="true">' + (zone === 'in' ? '🔒' : '🌐') + '</span><span>' + txt + '</span><span class="who-sees"><span class="small muted" style="align-self:center">이 화면을 보는 사람</span>' + aud.join('') + '</span></div>';
  }
  function outZone(sub, id) {
    sub = sub || 'forms';
    var meta = {
      forms: ['양식 자료실', '학교 업무 양식을 한글(hwpx) 파일로 받습니다. 로그인 없이 누구나 볼 수 있어요.', ''],
      vendors: ['협력업체', '에듀테크부터 졸업앨범·수학여행·전세버스·체험·용역·인쇄까지, 학교가 함께 일하는 업체를 선생님들이 별점으로 알려 줍니다.', role === 'vendor' ? '' : '<button class="btn pri" id="vsug">＋ 업체 추천하기</button>'],
      it: [role === 'vendor' ? '정보 수리요청 · 업체 화면' : '정보 수리요청', role === 'vendor' ? '맡은 학교의 PC·전자칠판·네트워크 수리 요청입니다. 상태를 누르면 요청한 선생님께 알림이 갑니다.' : 'PC·전자칠판·네트워크 고장은 외부 유지보수 업체가 받습니다. 그래서 학교 밖 공간에 있어요.', role === 'vendor' ? '' : '<button class="btn pri" id="nr">＋ 정보 수리 요청</button>']
    }[sub];
    if (!meta) { location.replace('#out/forms'); return; }
    view.innerHTML = '<div class="page-h"><div><h1>' + meta[0] + '</h1><p>' + meta[1] + '</p></div>' + meta[2] + '</div><div id="bbody"></div>';
    var bb = $('#bbody');
    if (sub === 'forms') { if (id) formView(bb, id); else formList(bb); }
    if (sub === 'vendors') { if (id) vendorView(bb, id); else vendorList(bb); var sg = $('#vsug'); if (sg) sg.onclick = function () { TR.toast('업체 이름·분류·연락처를 받아 운영자가 확인한 뒤 올립니다. 업체가 스스로 등록·광고할 수는 없어요.'); }; }
    if (sub === 'it') {
      if (role === 'vendor') { var draw = function () { bb.innerHTML = '<div class="card"><div class="card-b">' + vendorBody() + '</div></div>'; vendorBind(bb, draw); }; draw(); }
      else repairTab(bb, '정보');
    }
  }

  // 협력업체 — 보기는 누구나, 별점·후기는 인증 교사만. 후기 작성자는 '지역 · 학교급'까지만 보인다
  var vCat = '전체', vSort = 'score', vArea = '전체';
  var V_AREAS = ['전체', '서울', '경기', '인천', '강원', '충청', '전라', '경상', '제주'];
  function vAvg(v, k) { if (!v.rv.length) return 0; return v.rv.reduce(function (s, r) { return s + (k ? r.a[k] : r.r); }, 0) / v.rv.length; }
  function stars(x) { var n = Math.round(x); return '<span class="stars" aria-label="별점 ' + x.toFixed(1) + '">' + '★★★★★'.slice(0, n) + '<span class="off">' + '★★★★★'.slice(n) + '</span></span>'; }
  function vInArea(v) {
    if (vArea === '전체' || v.area === '전국') return true;
    if (v.area.indexOf('수도권') >= 0 && /서울|경기|인천/.test(vArea)) return true;
    return v.area.indexOf(vArea) >= 0 || (vArea === '충청' && /충남|충북|대전|세종/.test(v.area)) || (vArea === '전라' && /전남|전북|광주/.test(v.area)) || (vArea === '경상' && /경남|경북|부산|대구|울산/.test(v.area));
  }
  function vendorList(bb) {
    var all = TR.seedVendors;
    var list = all.filter(function (v) { return (vCat === '전체' || v.cat === vCat) && vInArea(v); })
      .sort(function (a, b) { return vSort === 'cnt' ? b.rv.length - a.rv.length : vSort === 'used' ? b.used - a.used : vAvg(b) - vAvg(a); });
    bb.innerHTML = '<div class="chips" style="margin-bottom:12px">' + ['전체'].concat(TR.vendorCats).map(function (c) { return '<button data-vc="' + c + '" class="' + (vCat === c ? 'on' : '') + '">' + c + '</button>'; }).join('') + '</div>' +
      '<div class="row" style="margin-bottom:14px;flex-wrap:wrap"><label class="small muted" for="varea">지역</label><select id="varea" class="btn sm">' + V_AREAS.map(function (a) { return '<option' + (a === vArea ? ' selected' : '') + '>' + a + '</option>'; }).join('') + '</select>' +
      '<div class="seg">' + [['score', '별점순'], ['cnt', '후기 많은 순'], ['used', '이용 학교 많은 순']].map(function (s) { return '<button data-vs2="' + s[0] + '" class="' + (vSort === s[0] ? 'on' : '') + '">' + s[1] + '</button>'; }).join('') + '</div><span class="small muted" style="margin-left:auto">업체 ' + list.length + '곳</span></div>' +
      '<div class="vgrid">' + (list.map(function (v) {
        var a = vAvg(v);
        return '<a class="vcard2" href="#out/vendors/' + v.id + '"><div class="row"><span class="badge">' + esc(v.cat) + '</span><span class="small muted">' + esc(v.area) + '</span></div><h3>' + esc(v.name) + '</h3>' +
          '<div class="vscore">' + stars(a) + '<b>' + a.toFixed(1) + '</b><span class="small muted">후기 ' + v.rv.length + ' · 티로드 학교 ' + v.used + '곳 이용</span></div>' +
          '<p class="small muted" style="margin:8px 0 0">' + esc(v.d) + '</p>' + (v.tags.length ? '<div class="row" style="margin-top:9px;flex-wrap:wrap">' + v.tags.map(function (t) { return '<span class="badge pri">' + esc(t) + '</span>'; }).join('') + '</div>' : '') + '</a>';
      }).join('') || '<div class="card"><div class="card-b muted">조건에 맞는 업체가 없어요.</div></div>') + '</div>' +
      '<p class="small muted" style="margin:16px 0 0">업체 목록은 운영자가 확인해 올리고, 업체가 돈을 내고 순위를 올리거나 후기를 지울 수 없습니다. 후기를 쓴 선생님의 이름·학교는 업체에도 다른 선생님에게도 보이지 않아요.</p>';
    $$('[data-vc]', bb).forEach(function (b) { b.onclick = function () { vCat = b.dataset.vc; vendorList(bb); }; });
    $$('[data-vs2]', bb).forEach(function (b) { b.onclick = function () { vSort = b.dataset.vs2; vendorList(bb); }; });
    $('#varea', bb).onchange = function () { vArea = this.value; vendorList(bb); };
  }
  function vendorView(bb, id) {
    var v = TR.seedVendors.find(function (x) { return x.id === id; }); if (!v) { location.replace('#out/vendors'); return; }
    var a = vAvg(v), canWrite = role !== 'vendor';
    bb.innerHTML = '<div class="small" style="margin-bottom:9px"><a href="#out/vendors">협력업체</a> / ' + esc(v.cat) + '</div><div class="split"><article class="card"><div class="card-h" style="display:block"><div class="row"><span class="badge">' + esc(v.cat) + '</span><span class="small muted">' + esc(v.area) + ' · 티로드 학교 ' + v.used + '곳 이용</span></div><h2 style="font-size:24px;margin-top:7px">' + esc(v.name) + '</h2><p class="muted" style="margin:4px 0 0">' + esc(v.d) + '</p></div>' +
      '<div class="card-b" style="border-bottom:1px solid var(--line)"><div class="row" style="justify-content:space-between"><h3 style="margin:0;font-size:18px">선생님 후기 ' + v.rv.length + '</h3>' + (canWrite ? '<button class="btn pri" id="rvnew">별점·후기 쓰기</button>' : '<span class="small muted">후기는 인증 교사만 쓸 수 있어요</span>') + '</div></div>' +
      v.rv.map(function (r) { return '<div class="review"><div class="row">' + stars(r.r) + '<b>' + r.r + '.0</b><span class="small muted">인증 교사 · ' + esc(r.by) + ' · ' + esc(r.at) + '</span></div><p style="margin:7px 0 6px">' + esc(r.t) + '</p><div class="small muted">' + TR.vendorAxes.map(function (x) { return x[1] + ' ' + r.a[x[0]]; }).join(' · ') + '</div></div>'; }).join('') + '</article>' +
      '<aside><section class="card"><div class="card-h"><h2>별점</h2></div><div class="card-b"><div class="vscore" style="margin-bottom:14px">' + stars(a) + '<b style="font-size:30px">' + a.toFixed(1) + '</b><span class="small muted">/ 5 · 후기 ' + v.rv.length + '</span></div>' +
      '<div class="axes">' + TR.vendorAxes.map(function (x) { var m = vAvg(v, x[0]); return '<span>' + x[1] + '</span><div class="bar"><i style="width:' + (m / 5 * 100) + '%"></i></div><b>' + m.toFixed(1) + '</b>'; }).join('') + '</div>' +
      '<p class="small muted" style="margin:14px 0 0"><b>서류 처리</b> = 견적서·사업자등록증·청렴서약서·보험·강사 경력 조회서 같은 학교 서류를 제때 챙겨 주는지.</p>' +
      (v.tags.length ? '<div class="row" style="margin-top:12px;flex-wrap:wrap">' + v.tags.map(function (t) { return '<span class="badge pri">' + esc(t) + '</span>'; }).join('') + '</div>' : '') + '</div></section></aside></div>';
    var b = $('#rvnew'); if (b) b.onclick = function () { reviewNew(v); };
  }
  function reviewNew(v) {
    var pick = { r: 5, p: 4, s: 4, q: 4, d: 4 };
    var seg = function (k) { return '<div class="seg" data-pk="' + k + '">' + [1, 2, 3, 4, 5].map(function (n) { return '<button data-n="' + n + '" class="' + (pick[k] === n ? 'on' : '') + '">' + n + '</button>'; }).join('') + '</div>'; };
    var area = TR.school.region.replace(/광역시|특별시|특별자치시|특별자치도|도$/, '');
    modal('<div class="card-h"><h2>' + esc(v.name) + ' 후기</h2><button class="btn sm ghost" data-close aria-label="닫기">✕</button></div><div class="card-b" style="display:grid;gap:12px">' +
      '<div class="rpick"><b>전체 별점</b>' + seg('r') + TR.vendorAxes.map(function (x) { return '<span>' + x[1] + '</span>' + seg(x[0]); }).join('') + '</div>' +
      '<label class="fld">후기<textarea id="rvt" rows="4" placeholder="다른 학교 선생님이 업체를 고를 때 도움이 될 이야기 (예: 서류는 제때 왔는지, 아이들 안전은 어땠는지)"></textarea></label>' +
      '<div class="pubwarn"><span>🌐</span><div><b>이 후기는 학교 밖에 공개됩니다.</b> 다른 학교 선생님과 업체도 봅니다.<br>이름·학교는 가리고 <b>인증 교사 · ' + esc(area) + ' · 초등</b>으로만 표시돼요. 학생·교직원 이름은 적지 마세요.</div></div>' +
      '<div class="row" style="justify-content:flex-end"><button class="btn" data-close>취소</button><button class="btn pri" id="rvsave">공개로 올리기</button></div></div>');
    $$('[data-pk]').forEach(function (g) { $$('button', g).forEach(function (b) { b.onclick = function (e) { e.preventDefault(); pick[g.dataset.pk] = +b.dataset.n; $$('button', g).forEach(function (x) { x.classList.toggle('on', x === b); }); }; }); });
    $('#rvsave').onclick = function () {
      var t = $('#rvt').value.trim(); if (t.length < 10) { TR.toast('후기를 한두 문장(10자 이상) 적어 주세요.'); return; }
      v.rv.unshift({ by: area + ' · 초등', at: '10/26', r: pick.r, a: { p: pick.p, s: pick.s, q: pick.q, d: pick.d }, t: t });
      closeModal(); route(); TR.toast('후기를 올렸어요. 이름·학교는 가려서 보입니다.');
    };
  }

  // 양식 자료실 — 고티처 양식 자료실과 같은 구성: 분류(학교·교육청), 제목·작성자·조회·날짜, 조회·날짜 정렬, 페이지당 20건.
  // 글은 운영자만 올리고, 보기·다운로드는 로그인 없이 누구나(검색 유입).
  function formList(bb) {
    var all = TR.seedForms, q = formQ.trim();
    var list = all.filter(function (f) { return (formCat === '전체' || f.ca === formCat) && (!q || (f.t + f.d).indexOf(q) >= 0); });
    list = list.slice().sort(function (a, b) { return formSort === 'hit' ? b.hit - a.hit : formSort === 'dl' ? b.dl - a.dl : (b.at < a.at ? -1 : b.at > a.at ? 1 : b.id < a.id ? -1 : 1); });
    var pages = Math.max(1, Math.ceil(list.length / 20)); formPage = Math.min(formPage, pages);
    var cnt = function (k) { return all.filter(function (f) { return k === '전체' || f.ca === k; }).length; };
    bb.innerHTML = '<div class="row" style="margin-bottom:12px;flex-wrap:wrap"><div class="seg">' + ['전체', '학교', '교육청'].map(function (k) { return '<button data-fc="' + k + '" class="' + (formCat === k ? 'on' : '') + '">' + k + ' ' + cnt(k) + '</button>'; }).join('') + '</div>' +
      '<input id="fq" value="' + esc(formQ) + '" placeholder="양식 이름으로 찾기 (예: 출장, 체험학습)" style="flex:1;min-width:276px;height:39px;border:1px solid var(--line-strong);border-radius:9px;padding:0 12px;background:var(--surface)">' +
      '<div class="seg">' + [['new', '최신순'], ['hit', '조회순'], ['dl', '다운로드순']].map(function (s) { return '<button data-fs="' + s[0] + '" class="' + (formSort === s[0] ? 'on' : '') + '">' + s[1] + '</button>'; }).join('') + '</div></div>' +
      '<div class="small muted" style="margin-bottom:9px">전체 ' + list.length + '건 / ' + pages + '페이지</div>' +
      '<div class="card">' + (list.slice((formPage - 1) * 20, formPage * 20).map(function (f) {
        return '<a class="crow frow" href="#out/forms/' + f.id + '"><span class="badge ' + (f.ca === '학교' ? 'pri' : 'acc') + '">' + f.ca + '</span><div style="min-width:0"><b>' + esc(f.t) + '</b><div class="small muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + esc(f.d) + '</div></div><span class="small muted">운영자 · ' + f.at.slice(5).replace('-', '/') + '</span><span class="small muted">조회 ' + money(f.hit) + '</span><span class="small"><span class="badge">hwpx</span> ' + money(f.dl) + '</span></a>';
      }).join('') || '<div class="card-b muted">찾는 양식이 없어요.</div>') + '</div>' +
      (pages > 1 ? '<div class="row" style="justify-content:center;margin-top:14px">' + Array.from({ length: pages }, function (_, i) { return '<button class="btn sm' + (i + 1 === formPage ? ' pri' : '') + '" data-fp="' + (i + 1) + '">' + (i + 1) + '</button>'; }).join('') + '</div>' : '') +
      '<p class="small muted" style="margin:14px 0 0">고티처 양식 자료실과 같은 79건 · 같은 구성(분류 · 설명 · 이렇게 쓰세요 · hwpx 첨부). 양식은 운영자가 올리고, 선생님은 받아 씁니다. 티로드에서는 표가 있는 양식을 <b>바로 모으기로</b> 돌릴 수 있습니다.</p>';
    $$('[data-fc]', bb).forEach(function (b) { b.onclick = function () { formCat = b.dataset.fc; formPage = 1; formList(bb); }; });
    $$('[data-fs]', bb).forEach(function (b) { b.onclick = function () { formSort = b.dataset.fs; formList(bb); }; });
    $$('[data-fp]', bb).forEach(function (b) { b.onclick = function () { formPage = +b.dataset.fp; formList(bb); window.scrollTo(0, 0); }; });
    var fq = $('#fq', bb); fq.oninput = function () { formQ = fq.value; formPage = 1; var pos = fq.selectionStart; formList(bb); var n = $('#fq', bb); n.focus(); n.setSelectionRange(pos, pos); };
  }
  function formView(bb, id) {
    var all = TR.seedForms, i = all.findIndex(function (f) { return f.id === id; }), f = all[i]; if (!f) { location.hash = '#out/forms'; return; }
    f.hit++;
    var prev = all[i - 1], next = all[i + 1];
    bb.innerHTML = '<div class="small" style="margin-bottom:9px"><a href="#out/forms">양식 자료실</a> /</div><div class="split"><article class="card"><div class="card-h" style="display:block"><div class="row"><span class="badge ' + (f.ca === '학교' ? 'pri' : 'acc') + '">' + f.ca + '</span><span class="small muted">운영자 · ' + f.at + ' · 조회 ' + money(f.hit) + '</span></div><h2 style="font-size:22px;margin-top:7px">' + esc(f.t) + '</h2></div>' +
      '<div class="card-b"><p style="margin-top:0">' + esc(f.d) + '</p><h3 style="font-size:17px;margin:18px 0 7px">이렇게 쓰세요</h3><ul class="tips">' + f.tips.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>' +
      '<p class="small muted">한글(hwpx) 파일입니다. 예시로 채운 내용은 그대로 쓰거나 지우고 우리 학교 내용으로 바꿔 쓰세요.</p>' +
      '<div class="attach"><span class="badge">hwpx</span><div class="grow"><b>' + esc(f.file) + '</b><div class="small muted">' + f.kb + 'KB · 다운로드 ' + money(f.dl) + '회 · ' + f.at + '</div></div><button class="btn pri" id="fdl">내려받기</button></div></div>' +
      '<div class="card-b row" style="border-top:1px solid var(--line);justify-content:space-between">' + (prev ? '<a href="#out/forms/' + prev.id + '" class="small">← ' + esc(prev.t) + '</a>' : '<span></span>') + (next ? '<a href="#out/forms/' + next.id + '" class="small">' + esc(next.t) + ' →</a>' : '') + '</div></article>' +
      '<aside><section class="card"><div class="card-h"><h2>티로드에서 바로</h2></div><div class="card-b" style="display:grid;gap:9px"><a class="btn" href="#collect/new">이 양식의 표로 모으기</a><button class="btn" id="fshare">링크 복사 (쪽지로 보내기)</button><p class="small muted" style="margin:0">표가 있는 양식이면 hwpx를 올려 <b>표만 칸 단위로 걷고</b>, 다 모이면 같은 파일에 채워 돌려받습니다(v1).</p></div></section></aside></div>';
    $('#fdl').onclick = function () { f.dl++; TR.toast('프로토타입: 「' + f.file + '」 내려받기'); formView(bb, id); };
    $('#fshare').onclick = function () { TR.copy('[티로드 양식] ' + f.t + '\nhttps://t.road/forms/' + f.id); };
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
    view.innerHTML = '<div class="page-h"><div><div class="small"><a href="#board/notice">게시판 · 공지</a> /</div><h1>' + esc(n.title) + ' ' + (n.ver > 1 ? '<span class="badge acc" style="vertical-align:middle">v' + n.ver + '</span>' : '') + '</h1><p>' + esc(n.dept) + ' ' + esc(n.by) + ' · 최종 수정 ' + esc(n.updated) + '</p></div>' +
      '<div class="row"><button class="btn" id="cp">쪽지용 링크 복사</button>' + (n.must ? (st.acked[n.id] ? '<span class="badge ok" style="height:39px;padding:0 14px">확인함</span>' : '<button class="btn pri" id="ack">확인했어요</button>') : '') + '</div></div>' +
      '<div class="split"><div>' +
      (prev ? '<section class="diff" style="margin-bottom:18px"><div class="row" style="margin-bottom:9px">' + TR.chgIcon + '<b>v' + prev.v + ' → v' + cur.v + '에서 바뀐 부분</b><span class="small muted">' + esc(cur.at) + '</span></div><dl class="kv">' + Object.keys(cur.fields).filter(function (k) { return prev.fields[k] !== cur.fields[k]; }).map(function (k) { return '<dt>' + esc(k) + '</dt><dd><del>' + esc(prev.fields[k]) + '</del> → <ins>' + esc(cur.fields[k]) + '</ins></dd>'; }).join('') + '</dl><p class="small" style="margin:9px 0 0">v1을 이미 본 28명에게는 <b>이 카드만</b> 「오늘 바뀐 것」에 다시 뜹니다. 전체를 다시 읽게 하지 않아요.</p></section>' : '') +
      '<section class="card"><div class="card-h"><h2>내용</h2><div class="seg">' + n.versions.map(function (v) { return '<button data-v="' + v.v + '" class="' + (v.v === cur.v ? 'on' : '') + '">v' + v.v + '</button>'; }).join('') + '</div></div><div class="card-b"><dl class="kv" id="kv">' + diffRows + '</dl><p style="margin:16px 0 0">' + esc(n.body) + '</p></div></section>' +
      '<section class="card" style="margin-top:18px"><div class="card-h"><h2>보낸 사람 설정</h2></div><div class="card-b" style="display:grid;gap:12px">' +
      '<label class="toggle"><input type="checkbox" ' + (n.real ? 'checked' : '') + ' id="real"> 실질 변경(일시·장소·대상·준비물) — 켜면 이전에 본 사람에게 바뀐 부분만 다시 알림</label>' +
      '<label class="toggle"><input type="checkbox" ' + (n.must ? 'checked' : '') + (canMust ? '' : ' disabled') + '> 확인 필수 — <span class="muted">' + (canMust ? '교감 권한으로 켤 수 있어요' : '부장·교감만 켤 수 있어요 (아무나 켜면 다시 "확인했습니다" 쪽지 30통이 됩니다)') + '</span></label>' +
      '<p class="small muted" style="margin:0">기본값은 <b>열어 보면 확인한 것</b>으로 칩니다(2초 이상 보거나 끝까지 스크롤, 링크 미리보기 봇은 제외).</p></div></section>' +
      '</div><aside><section class="card"><div class="card-h"><h2>확인 현황</h2><b>' + n.seen + '/' + n.total + '</b></div><div class="card-b"><div class="bar"><i style="width:' + pct + '%"></i></div>' +
      (n.ver > 1 ? '<p class="small" style="margin:12px 0 5px">v' + n.ver + ' 바뀐 부분 확인 <b>' + n.seenV2 + '/' + n.seen + '</b></p><div class="bar"><i style="width:' + Math.round(n.seenV2 / n.seen * 100) + '%;background:var(--accent)"></i></div>' : '') +
      (id === 'n2' ? '<p class="small muted" style="margin:14px 0 7px">아직 안 본 선생님</p><div class="miss">' + TR.unseenN2.map(function (x) { return '<button>' + x + '</button>'; }).join('') + '</div><button class="btn sm" style="margin-top:12px" id="rem">안 본 분께 보낼 문구 복사</button>' : '') +
      '</div></section><div style="margin-top:18px"><div class="small muted" style="margin-bottom:7px">받는 사람 화면 (PC 브라우저)</div>' + pcWin('recv.html?n=' + id, 't.road/n/' + id, 644) + '</div></aside></div>';
    $$('[data-v]', view).forEach(function (b) { b.onclick = function () { $$('[data-v]', view).forEach(function (x) { x.classList.toggle('on', x === b); }); var v = n.versions[+b.dataset.v - 1]; $('#kv').innerHTML = Object.keys(v.fields).map(function (k) { return '<dt>' + esc(k) + '</dt><dd>' + esc(v.fields[k]) + '</dd>'; }).join(''); }; });
    var ack = $('#ack'); if (ack) ack.onclick = function () { st.acked[n.id] = true; n.seen = Math.min(n.total, n.seen + 1); TR.save(); TR.toast('확인했어요. 보낸 분 화면에 바로 반영됩니다.'); noticeDetail(id); updateDots(); };
    $('#cp').onclick = function () { TR.copy('[티로드] ' + n.title + (n.ver > 1 ? ' (v' + n.ver + ' 수정: 장소·시간 변경)' : '') + '\nhttps://t.road/n/' + n.id); };
    var rem = $('#rem'); if (rem) rem.onclick = function () { TR.copy('[티로드] 「' + n.title + '」 v2에서 장소(체육관)·시간(3블록)이 바뀌었어요. 확인 부탁드립니다.\nhttps://t.road/n/' + n.id, '미확인 ' + TR.unseenN2.length + '명께 보낼 문구를 복사했어요.'); };
  }

  // ---------- 학교 (전체 교사 공용 현황판) ----------
  // 교감 전용 화면이 아니다. 모든 선생님이 같은 화면으로 학년분장·업무분장·오늘 복무를 본다.
  var schoolTab = 'grade';
  function school() {
    var st = S(), duty = st.events.filter(function (e) { return TR.onDay(e, TR.TODAY) && e.type === 'duty'; });
    var members = st.members || TR.seedMembers;
    var dutyHtml = duty.map(function (d) {
      return '<div class="duty"><span class="k">' + d.duty + '</span><div class="grow"><b>' + esc(d.who) + '</b> <span class="small muted">' + esc(TR.groupOf(d.who)) + '</span>' + (d.changed ? ' ' + TR.chgIcon : '') + '<div class="small muted">' + esc(d.time) + (d.target ? ' · ' + esc(d.target) : '') + (d.place ? ' · ' + esc(d.place) : '') + '</div></div></div>';
    }).join('');
    var body = '';
    if (schoolTab === 'grade') {
      body = '<div class="groups">' + TR.groups.map(function (g) {
        var roles = TR.groupRoles(g.id);
        var ms = members.filter(function (m) { return m.group === g.id; }).sort(function (a, b) { var ord = function (p) { var i = roles.indexOf(p); return p === '교장' ? -1 : i < 0 ? 99 : i; }; return ord(a.pos) - ord(b.pos); });
        return '<section class="card gcard"><div class="card-h"><h2>' + esc(g.name) + '</h2><span class="small muted">' + ms.length + '명</span></div><ul>' + (ms.length ? ms.map(function (m) {
          return '<li class="' + (m.name === TR.me.name ? 'me' : '') + '"><span class="pos">' + esc(m.pos) + '</span><b>' + esc(m.name) + '</b>' + (m.sub ? '<span class="small muted">' + esc(m.sub) + '</span>' : '') + (m.name === TR.me.name ? '<button class="btn sm ghost" data-edit-me>고치기</button>' : '') + '</li>';
        }).join('') : '<li class="faint">아직 아무도 없어요</li>') + '</ul></section>';
      }).join('') + '</div>' +
        '<p class="small muted" style="margin:14px 0 0">그룹·직책은 선생님이 <b>직접</b> 고르고 고칩니다(승인 없음). 전담은 주 학년 그룹에 속하고, 교장·보건·사서·상담·특수·돌봄은 교무실이나 기타/지원 중 학교 사정에 맞게 고릅니다. 고칠 때마다 이력이 남습니다.</p>';
    } else {
      // 학년분장과 같은 UI: 부서마다 카드 하나, 줄마다 [업무] 담당자 · 그룹
      var depts = []; TR.duties.forEach(function (d) { if (depts.indexOf(d[0]) < 0) depts.push(d[0]); });
      body = '<div class="groups">' + depts.map(function (dp) {
        var rows = TR.duties.filter(function (d) { return d[0] === dp; });
        var people = rows.map(function (d) { return d[2]; }).filter(function (v, i, a) { return a.indexOf(v) === i; });
        return '<section class="card gcard dept"><div class="card-h"><h2>' + esc(dp) + '</h2><span class="small muted">' + people.length + '명 · 업무 ' + rows.length + '</span></div><ul>' + rows.map(function (d) {
          var m = members.find(function (x) { return x.name === d[2]; });
          return '<li class="' + (d[2] === TR.me.name ? 'me' : '') + '"><span class="pos">' + esc(d[1]) + '</span><b>' + esc(d[2]) + '</b><span class="small muted">' + (m ? esc(TR.groupName(m.group)) : '') + '</span></li>';
        }).join('') + '</ul></section>';
      }).join('') + '</div>' +
        '<p class="small muted" style="margin:14px 0 0">부서(업무분장)는 그룹(근무 위치)과 <b>별개 축</b>입니다. 같은 선생님이 \'4학년\' 그룹이면서 \'방과후돌봄부\' 소속일 수 있습니다. 취합 대상도 부서·업무로 고를 수 있어요. 업무분장은 해마다 보관되어 내년 2월에 그대로 불러옵니다.</p>';
    }
    view.innerHTML = '<div class="page-h"><div><h1>학교</h1><p>모든 선생님이 같은 화면을 봅니다 · 누가 어느 학년·부서에서 무슨 일을 맡았는지, 오늘 누가 자리에 없는지.</p></div><div class="row"><button class="btn" data-edit-me>내 그룹·직책 고치기</button><a class="btn" href="#start">새 학년 세팅</a></div></div>' +
      '<section class="card" style="margin-bottom:18px"><div class="card-h"><h2>급식소 현황판 (TV)</h2><span class="small muted">로그인 없음 · 급식소 TV 브라우저에 이 주소만 띄워 두세요</span></div><div class="card-b row" style="justify-content:space-between;flex-wrap:wrap;gap:12px"><div><b style="font-size:18px">t.road/tv/' + TR.meal.code + '</b><div class="small muted">오늘의 메뉴 · 알레르기 · 칼로리 · 원산지 · 손 씻기 · 골고루 · 오늘의 식사 사진 · 이번 주 메뉴, 8개 화면이 7초씩 넘어갑니다. 식단은 NEIS에서 자동, 사진만 영양 선생님이 휴대폰으로 올립니다.</div></div><a class="btn pri" href="tv.html?s=' + TR.meal.code + '" target="_blank" rel="noopener">현황판 열기 ↗</a></div></section>' +
      '<section class="card" style="margin-bottom:18px"><div class="card-h"><h2>오늘 복무 현황</h2><span class="small muted">' + duty.length + '명 · 나이스 결재와 별개로 공유만 · 보강 배정은 담당자가 기존 방식대로</span></div><div class="card-b"><div class="duty-row">' + (dutyHtml || '<span class="muted">오늘 부재자가 없어요.</span>') + '</div></div></section>' +
      '<div class="row" style="margin-bottom:14px"><div class="seg"><button data-tab="grade" class="' + (schoolTab === 'grade' ? 'on' : '') + '">학년분장 · 그룹 현황</button><button data-tab="duty" class="' + (schoolTab === 'duty' ? 'on' : '') + '">업무분장 · 부서</button></div><span class="small muted">' + (schoolTab === 'grade' ? '그룹 ' + TR.groups.length + '개 · 구성원 ' + members.length + '명' : '부서 ' + TR.duties.map(function (d) { return d[0]; }).filter(function (v, i, a) { return a.indexOf(v) === i; }).length + '개 · 업무 ' + TR.duties.length + '개') + '</span></div>' + body;
    $$('[data-tab]', view).forEach(function (b) { b.onclick = function () { schoolTab = b.dataset.tab; school(); }; });
    $$('[data-edit-me]', view).forEach(function (b) { b.onclick = editMe; });
  }
  function editMe() {
    var st = S(), m = st.members.find(function (x) { return x.name === TR.me.name; });
    function posOptions(gid, cur) { var r = TR.groupRoles(gid); var custom = cur && r.indexOf(cur) < 0; return r.map(function (p) { return '<option' + (p === cur ? ' selected' : '') + '>' + esc(p) + '</option>'; }).join('') + '<option value="__custom"' + (custom ? ' selected' : '') + '>직접 입력…</option>'; }
    modal('<div class="card-h"><h2>내 그룹·직책</h2><button class="btn sm ghost" data-close aria-label="닫기">✕</button></div><div class="card-b" style="display:grid;gap:14px">' +
      '<label class="fld">그룹<select id="eg">' + TR.groups.map(function (g) { return '<option value="' + g.id + '"' + (g.id === m.group ? ' selected' : '') + '>' + esc(g.name) + '</option>'; }).join('') + '</select></label>' +
      '<label class="fld">직책<select id="ep">' + posOptions(m.group, m.pos) + '</select></label>' +
      '<label class="fld" id="ecw" ' + (TR.groupRoles(m.group).indexOf(m.pos) >= 0 ? 'hidden' : '') + '>직책 직접 입력<input id="ec" value="' + esc(TR.groupRoles(m.group).indexOf(m.pos) >= 0 ? '' : m.pos) + '" placeholder="예: 늘봄실무사"></label>' +
      '<label class="fld">메모 <span class="small muted">전담 과목 등 (선택)</span><input id="es" value="' + esc(m.sub || '') + '" placeholder="예: 영어"></label>' +
      '<p class="small muted" style="margin:0">전담 선생님은 <b>주로 들어가는 학년</b> 그룹을 고르고 직책을 \'전담\'으로 두세요.</p>' +
      '<div class="row" style="justify-content:flex-end"><button class="btn" data-close>취소</button><button class="btn pri" id="esave">저장</button></div></div>');
    var eg = $('#eg'), ep = $('#ep');
    eg.onchange = function () { ep.innerHTML = posOptions(eg.value, ''); $('#ecw').hidden = true; };
    ep.onchange = function () { $('#ecw').hidden = ep.value !== '__custom'; if (ep.value === '__custom') $('#ec').focus(); };
    $('#esave').onclick = function () {
      var pos = ep.value === '__custom' ? $('#ec').value.trim() : ep.value; if (!pos) { TR.toast('직책을 입력해 주세요.'); return; }
      m.group = eg.value; m.pos = pos; m.sub = $('#es').value.trim() || undefined;
      TR.save(); closeModal(); TR.toast('바꿨어요: ' + TR.groupName(m.group) + ' · ' + pos + '. 모든 선생님 화면에 바로 반영됩니다.'); route();
    };
  }

  // ---------- 시작하기 ----------
  var wizStep = 1;
  var DUTY_SAMPLE = '부서\t업무\t담당\n교무기획부\t학사일정·교육과정\t한상우\n교무기획부\t학적·전입학\t강지은\n연구부\t연수·수업공개\t오지현\n생활인성부\t학교폭력·생활지도\t박지훈\n방과후돌봄부\t방과후학교\t정하은\n방과후돌봄부\t돌봄교실\t석가영\n정보과학부\t정보보안·기기\t차민호\n체육건강부\t보건·체육행사\t노태윤\n행정실\t시설·예산\t구본석';
  var TIME_SAMPLE = '1블록\t08:40\t10:30\n2블록\t10:30\t12:20\n3블록\t12:20\t14:40\n방과후\t14:40\t';
  function start() {
    var steps = ['그룹·직책 확인', '업무분장 붙여넣기 (선택)', '블록 시각 정하기', '선생님 초대'];
    var body = '';
    if (wizStep === 1) body = '<h2 style="font-size:20px">기본 그룹이 준비돼 있어요</h2><p class="muted">모든 학교에 공통인 그룹과 직책입니다. 선생님들은 처음 들어올 때 여기서 <b>자기 그룹과 직책만 고르면</b> 됩니다. 학교에 맞게 그룹을 더하거나(예: 병설유치원, 특수학급) 직책을 직접 입력할 수 있어요.</p>' +
      '<div class="groups compact">' + TR.groups.map(function (g) { return '<div class="card gcard"><div class="card-h"><h2>' + esc(g.name) + '</h2></div><div class="card-b chips">' + TR.groupRoles(g.id).map(function (r) { return '<span class="badge">' + esc(r) + '</span>'; }).join('') + '<span class="badge" style="border:1px dashed var(--line-strong);background:none">+ 직접 입력</span></div></div>'; }).join('') + '</div>' +
      '<p class="small muted">교장·보건·사서·상담·특수·돌봄 직책은 교무실과 기타/지원 양쪽에 있습니다. 전담은 주 학년 그룹의 \'전담\'으로 들어갑니다.</p>';
    if (wizStep === 2) body = '<h2 style="font-size:20px">업무분장표를 붙여넣으세요 (선택)</h2><p class="muted">2월에 만드는 그 표 그대로. <b>부서와 담당 업무</b>가 만들어지고, 취합 대상을 "부서·업무"로 고를 수 있게 됩니다. 부서는 그룹과 별개라서, 비워 두고 나중에 넣어도 됩니다.</p><textarea class="paste" id="wp">' + DUTY_SAMPLE + '</textarea><div id="wr" style="margin-top:14px"></div>';
    if (wizStep === 3) body = '<h2 style="font-size:20px">블록 시각을 정하세요</h2><p class="muted">티로드의 시간 단위는 <b>1블록 · 2블록 · 3블록 · 방과후</b> 네 칸뿐입니다. 학교 시정에 맞춰 시작·끝 시각만 고치면 「오늘」·「일정」의 블록 칸과 <b>묶음 알림 시각</b>이 여기서 정해집니다. 수업 중에는 PC 알림도 띄우지 않습니다(교실 TV·전자칠판에 화면이 송출되기 때문).</p><textarea class="paste" id="wp">' + TIME_SAMPLE + '</textarea><div id="wr" style="margin-top:14px"></div>';
    if (wizStep === 4) body = '<h2 style="font-size:20px">선생님 초대</h2><p class="muted">학교 코드를 말로 전할 필요가 없습니다. 링크를 쪽지로 보내거나 교무실에 QR을 붙여 두세요. 처음 들어올 때 <b>이름 → 그룹 → 직책</b>만 고르면 됩니다.</p><div class="row" style="gap:23px;align-items:flex-start;flex-wrap:wrap"><canvas class="qr" id="qr" width="29" height="29" aria-label="초대 QR 예시"></canvas><div class="grow" style="min-width:276px"><div class="msg">[티로드] 우리 학교 티로드가 열렸어요.\n오늘 일과·바뀐 것·내 마감을 한 화면에서 봅니다.\n가입 없이 이름과 그룹(학년·교무실…)만 고르면 끝 → https://t.road/j/TRD-7Q2K</div><div class="row" style="margin-top:12px"><button class="btn pri" id="cpj">쪽지 문구 복사</button><button class="btn" onclick="window.print()">QR 인쇄</button></div><p class="small muted">학교 도메인 메일 인증은 선택입니다 (초등은 업무용 메일 보유율이 낮음).</p></div></div>';
    view.innerHTML = '<div class="page-h"><div><h1>새 학년 세팅</h1><p>2월 셋째 주, 10분이면 끝나는 4단계. 고티처처럼 엑셀 매칭·임시 비밀번호가 필요 없습니다.</p></div><a class="btn" href="#school">학교 화면으로</a></div>' +
      '<div class="wiz"><nav class="wiz-steps card" style="padding:9px">' + steps.map(function (s, i) { return '<button class="' + (wizStep === i + 1 ? 'on' : '') + '" data-s="' + (i + 1) + '"><i>' + (i + 1) + '</i>' + s + '</button>'; }).join('') + '</nav>' +
      '<section class="card"><div class="card-b">' + body + '<div class="row" style="justify-content:flex-end;margin-top:18px">' + (wizStep > 1 ? '<button class="btn" data-s="' + (wizStep - 1) + '">이전</button>' : '') + (wizStep < 4 ? '<button class="btn pri" data-s="' + (wizStep + 1) + '">다음</button>' : '<a class="btn pri" href="#today">완료 — 오늘 화면 보기</a>') + '</div></div></section></div>';
    $$('[data-s]', view).forEach(function (b) { b.onclick = function () { wizStep = +b.dataset.s; start(); }; });
    var wp = $('#wp');
    function an() {
      var lines = wp.value.split(/\r?\n/).filter(function (l) { return l.trim(); }).map(function (l) { return l.split(/\t|\s{2,}|\|/).map(function (x) { return x.trim(); }); });
      if (wizStep === 2) {
        var rows = lines.slice(1), depts = {}; rows.forEach(function (r) { depts[r[0]] = (depts[r[0]] || 0) + 1; });
        var people = rows.map(function (r) { return r[2]; }).filter(function (x) { return x && x[0] !== '('; });
        $('#wr').innerHTML = '<div class="proto-note">부서 <b>' + Object.keys(depts).length + '개</b>, 업무 <b>' + rows.length + '개</b>, 담당 <b>' + people.length + '명</b>을 찾았어요. ' + Object.keys(depts).map(function (d) { return '<span class="badge pri">' + esc(d) + ' ' + depts[d] + '</span>'; }).join(' ') + '<br>담당자 이름은 선생님이 들어와 이름을 고르는 순간 연결됩니다. 그룹(학년·교무실)은 각자 고릅니다.</div>';
      } else {
        var cls = lines.filter(function (r) { return /블록|방과\s?후/.test(r[0]); });
        $('#wr').innerHTML = '<div class="proto-note">블록 <b>' + cls.length + '칸</b>을 찾았어요. 묶음 알림: <b>08:20</b> · <b>12:20</b>(2블록 끝) · <b>14:50</b>(방과후). 수업 시간에는 우상단 🔔에만 쌓이고 PC 알림 창은 뜨지 않습니다.</div>';
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
      chip('title', '제목(쓴 그대로)', p.title || '(제목)', 'k-type', !p.title) +
      chip('date', '날짜', TR.dateText(p) + (TR.parse(qi.value).date ? '' : ' (오늘)'), 'k-date') +
      chip('s', '시간', p.s ? TR.slotText({ s: p.s, e: p.e, time: p.time }) : '없음 → 전일', 'k-period', !p.s) +
      chip('target', '대상', p.target || '대상 없음', 'k-target', !p.target) +
      chip('place', '장소', p.place || '장소 없음', 'k-place', !p.place) +
      (p.who ? chip('who', '참석', p.who.join(', '), 'k-who') : '') +
      chip('type', '유형', (p.duty ? '복무·' + p.duty : TR.typeLabel[p.type]), 'k-type') + '</div>' +
      (conf.length ? '<div class="warn">⚠ ' + esc(p.place) + '이(가) 겹쳐요: ' + conf.map(function (c) { return esc(c.title) + ' (' + esc(TR.slotText(c)) + ', ' + esc(TR.byline(c.by)) + ')'; }).join(', ') + '</div>' : '') +
      '<div class="hint"><span>제목은 쓴 문장 그대로(날짜 표현만 빠짐) · 칩은 검색·충돌 경고용, 눌러서 고칠 수 있어요 · 블록/장소/대상은 학교 사전(특별실 ' + TR.rooms.length + '곳·교직원 ' + TR.staff.length + '명)으로 알아봅니다</span><span><kbd>Enter</kbd> 저장 <kbd>Esc</kbd> 닫기</span></div>';
    qpop.classList.add('show');
    $$('.chip', qpop).forEach(function (c) {
      c.onclick = function () {
        var k = c.dataset.k; if (k === 'type') { var order = ['event', 'work', 'meet', 'duty']; qOver.type = order[(order.indexOf(p.type) + 1) % 4]; renderQ(); return; }
        var cur = k === 'date' ? p.date : k === 's' ? (p.s ? TR.slotText({ s: p.s, e: p.e }) : '') : k === 'who' ? (p.who || []).join(',') : (p[k] || '');
        c.outerHTML = '<input class="chip" data-edit="' + k + '" value="' + esc(cur) + '" style="width:172px" aria-label="' + k + ' 수정">';
        var inp = $('[data-edit]', qpop); inp.focus(); inp.select();
        var commit = function () { var v = inp.value.trim(); if (k === 's') { var t = TR.parse(' 오늘 ' + v + ' '); qOver.s = t.s; qOver.e = t.e; qOver.time = t.time; } else if (k === 'who') qOver.who = v ? v.split(/[,\s]+/) : undefined; else qOver[k] = v || undefined; renderQ(); qi.focus(); };
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
      var ev = { id: 'u' + Date.now().toString(36), date: p.date, end: p.end, title: p.title || '새 일정', place: p.place || '', target: p.target || '', type: p.type, by: TR.me.name, mine: true, changed: true, change: '<ins>새로 등록</ins> · ' + TR.me.name };
      if (!ev.end) delete ev.end;
      if (p.s) { ev.s = p.s; ev.e = p.e || p.s; } if (p.time) ev.time = p.time; // 블록·시각을 안 쓰면 전일(시간 미정)
      if (p.duty) { ev.duty = p.duty; ev.who = (p.who && p.who[0]) || TR.me.name; ev.time = p.time || TR.slotText(ev); ev.title = ev.who + ' ' + p.duty; }
      S().events.push(ev); TR.save();
      qi.value = ''; qOver = {}; hideQ();
      TR.toast(TR.dateText(ev) + ' ' + (ev.s ? TR.slotText(ev) : '전일') + ' 「' + ev.title + '」 등록' + (ev.s ? ' — 학교 일과와 바뀐 것에 반영' : ' — 시간은 필요할 때 「오늘」에서 끌어서 정하세요'));
      if (TR.onDay(ev, TR.TODAY)) { location.hash = '#today'; route(); } else { calMode = 'week'; if (location.hash === '#cal') cal(); else location.hash = '#cal'; }
    }
  });
  document.addEventListener('click', function (e) { if (!e.target.closest('.qi')) hideQ(); });
  document.addEventListener('keydown', function (e) {
    if (e.target.matches('input,textarea,select')) return;
    if (e.key === '/' || e.key === 'n' || e.key === 'N') { e.preventDefault(); qi.focus(); }
    if (e.key === 't' || e.key === 'T') location.hash = '#today';
  });

  $('#who').addEventListener('change', function () {
    var was = role; role = this.value;
    $('.avatar').textContent = { vp: '이', vendor: '누' }[role] || '정';
    TR.toast(role === 'vendor' ? '유지보수 업체(' + TR.vendor.name + ') 계정으로 바꿨어요. 교내는 잠기고 정보 수리요청만 열립니다.' : role === 'vp' ? '교감(이정민) 권한으로 바꿨어요. 화면은 모든 선생님과 같고, 공지 \'확인 필수\'만 켤 수 있어요.' : '담임(정하은) 화면으로 바꿨어요.');
    if (role === 'vendor') { location.hash = '#out/it'; route(); return; }
    if (was === 'vendor') { location.hash = '#today'; return; }
    route();
  });
  $('#zin').addEventListener('click', function (e) { if (role === 'vendor') { e.preventDefault(); TR.toast('🔒 교내는 ' + TR.school.name + ' 교직원만 들어갈 수 있어요. 업체 계정에는 열리지 않습니다.'); } });
  $('#zin .zt b').textContent = TR.school.name.replace(/등학교$/, '');
  $('#reset').addEventListener('click', function () { TR.reset(); route(); TR.toast('시연 데이터를 처음 상태로 되돌렸어요.'); });

  // ---------- 우상단 알림(🔔) — PC 전용 사용 환경 ----------
  // 교사는 늘 PC 앞에 있다. 앱 안 벨 + 브라우저 웹 알림(윈도우 알림센터)으로 시작, 전용 PC 에이전트는 차후.
  function renderBell() {
    var items = myInbox().filter(function (i) { return !i.done && i.k !== 'send'; });
    var ch = S().events.filter(function (e) { return e.changed && TR.lastDay(e) >= TR.TODAY; });
    $('#bell .cnt').textContent = items.length + ch.length; $('#bell .cnt').hidden = !(items.length + ch.length);
    $('#bellpop').innerHTML = '<div class="bp-h"><b>알림</b><span class="small muted">다음 묶음 12:20 · 수업 중엔 PC 알림 창을 띄우지 않아요</span></div>' +
      items.map(function (it) { return '<a class="bp-i" href="' + (it.k === 'ack' ? '#notice/' + it.id : '#collect/' + it.id) + '"><span class="badge ' + (it.urgent ? 'red' : 'pri') + '">' + (it.k === 'ack' ? '확인' : '제출') + '</span><span class="grow">' + esc(it.title) + '<span class="small muted">' + esc(it.meta) + '</span></span></a>'; }).join('') +
      ch.map(function (e) { return '<a class="bp-i" href="#cal">' + TR.chgIcon + '<span class="grow">' + TR.dateText(e) + ' ' + esc(e.title) + '<span class="small muted">바뀜 · ' + esc(TR.byline(e.by || e.who)) + '</span></span></a>'; }).join('') +
      '<div class="bp-f"><button class="btn sm" id="pcnoti">PC 알림 켜기</button><span class="small muted">브라우저가 켜져 있으면 윈도우 알림센터에 뜹니다. 전용 PC 에이전트(노션 PC처럼)는 차후 개발.</span></div>';
    $('#pcnoti').onclick = function (e) { e.stopPropagation(); TR.toast('프로토타입: 실제 서비스에서는 브라우저 알림 권한을 받아 쉬는 시간 묶음으로 윈도우 알림을 보냅니다.'); };
  }
  $('#bell').addEventListener('click', function (e) { e.stopPropagation(); renderBell(); $('#bellpop').classList.toggle('show'); });
  document.addEventListener('click', function (e) { if (!e.target.closest('#bellpop')) $('#bellpop').classList.remove('show'); });
  window.addEventListener('hashchange', function () { $('#bellpop').classList.remove('show'); renderBell(); });
  TR.onSync(renderBell);

  route();
  renderBell();
})();
