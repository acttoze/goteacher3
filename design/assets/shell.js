// 고티처 셸 — 사이드바·상단바·알림 묶음·공간 전환과 화면들이 같이 쓰는 도우미.
// 화면은 <body data-page="today"> 처럼 자기 메뉴를 알리고, GTShell.mount({ crumb, quick })를 부른다.
(function () {
  var G = window.GT, P = window.PEOPLE;

  // ---------- 도우미 ----------
  var U = window.GTU = {
    $: function (s, r) { return (r || document).querySelector(s); },
    $$: function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); },
    esc: function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); },
    // 받침 따라 조사 고르기: josa('교실','으로','로')
    josa: function (w, a, b) { w = String(w); var c = w.charCodeAt(w.length - 1) - 0xac00; if (c < 0 || c > 11171) return b; var t = c % 28; if (a === '으로' && t === 8) return b; return t ? a : b; },
    av: function (name, cls) {
      var p = P.of(name);
      return '<span class="av' + (p.inkText ? ' ink-t' : '') + (cls ? ' ' + cls : '') + '" style="background:' + p.color + '" title="' + U.esc(name) + '">' + U.esc(String(name).charAt(0)) + '</span>';
    },
    ptag: function (name, txt) { var p = P.of(name); return '<span class="ptag' + (p.inkText ? ' ink-t' : '') + '" style="--who:' + p.color + '">' + U.esc(name) + (txt ? ' · ' + U.esc(txt) : '') + '</span>'; },
    toast: function (t, undo) {
      var el = U.$('#toast');
      if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; el.setAttribute('role', 'status'); el.setAttribute('aria-live', 'polite'); document.body.appendChild(el); }
      el.innerHTML = U.esc(t) + (undo ? '<button type="button">되돌리기</button>' : '');
      if (undo) { el.querySelector('button').onclick = function () { undo(); el.classList.remove('show'); el.style.pointerEvents = 'none'; }; el.style.pointerEvents = 'auto'; }
      el.classList.add('show');
      clearTimeout(U._t); U._t = setTimeout(function () { el.classList.remove('show'); el.style.pointerEvents = 'none'; }, undo ? 4200 : 2800);
    },
    copy: function (txt, msg) {
      var ok = function () { U.toast(msg || '복사했어요'); };
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(ok, function () { U.toast(txt); }); else U.toast(txt);
    },
    icon: function (k, size) {
      var s = size || 20, d = ICONS[k] || '';
      return '<svg width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
    },
    mark: function (s) { s = s || 24; return '<svg width="' + s + '" height="' + s + '" viewBox="0 0 26 26" aria-hidden="true"><path d="M4 3l17 8.2-7.4 2.1L10.4 21z" fill="#2251ff"/></svg>'; },
    // 한 줄 일정 해석: "내일 2블록 과학실 안전교육" → 날짜·블록·장소·제목
    parseLine: function (s) {
      var DAY = { '오늘': '10/26(월)', '내일': '10/27(화)', '모레': '10/28(수)', '월요일': '11/2(월)', '화요일': '10/27(화)', '수요일': '10/28(수)', '목요일': '10/29(목)', '금요일': '10/30(금)' };
      var WD = ['일', '월', '화', '수', '목', '금', '토'];
      var out = { date: null, slot: null, slotK: null, place: null, title: s };
      var m = s.match(/(\d{1,2})\/(\d{1,2})/);
      if (m) { var d = new Date(2026, +m[1] - 1, +m[2]); out.date = m[1] + '/' + m[2] + '(' + WD[d.getDay()] + ')'; out.title = out.title.replace(m[0], ''); }
      Object.keys(DAY).forEach(function (k) { if (!out.date && s.indexOf(k) > -1) { out.date = DAY[k]; out.title = out.title.replace(k, ''); } });
      var b = s.match(/([123])블록|방과후/);
      if (b) { var sl = b[1] ? G.slots[+b[1] - 1] : G.slots[3]; out.slotK = sl.k; out.slot = sl.label + ' ' + sl.from + (sl.to ? '~' + sl.to : '~'); out.title = out.title.replace(b[0], ''); }
      var p = s.match(/(\S*(?:실|관|교실|운동장|도서관|정문))(?=\s|$)/);
      if (p) { out.place = p[1]; out.title = out.title.replace(p[1], ''); }
      out.title = out.title.replace(/\s+/g, ' ').trim();
      return out;
    }
  };

  var ICONS = {
    today: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2"/>',
    collect: '<path d="M4 6h16M4 12h16M4 18h10"/>',
    schedule: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
    forms: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    people: '<circle cx="9" cy="9" r="3.5"/><path d="M3 19c.8-3 3.2-4.5 6-4.5s5.2 1.5 6 4.5M16 6.5a3 3 0 0 1 0 5.5M18 14.8c1.5.7 2.5 2 3 4.2"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    chev: '<path d="M9 6l6 6-6 6"/>',
    down: '<path d="M12 4v12M6 11l6 6 6-6M5 20h14"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 13h6M9 17h6"/>',
    upload: '<path d="M12 16V4M6 9l6-6 6 6M5 20h14"/>',
    table: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M9 10v9M15 10v9"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    star: '<path d="M12 4l2.4 5 5.6.6-4.2 3.8 1.2 5.6L12 16.3 7 19l1.2-5.6L4 9.6 9.6 9z"/>',
    send: '<path d="M4 12l16-8-6 16-2.5-6.5z"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2"/>',
    more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    back: '<path d="M15 6l-6 6 6 6"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    qr: '<rect x="4" y="4" width="6" height="6"/><rect x="14" y="4" width="6" height="6"/><rect x="4" y="14" width="6" height="6"/><path d="M14 14h2v2h-2zM18 18h2v2h-2zM14 18h2M18 14h2"/>'
  };

  // ---------- 셸 ----------
  var NAV = [
    { k: 'today', label: '오늘', href: 'today.html' },
    { k: 'collect', label: '모으기', href: 'collect.html', cnt: function () { return G.todo.length; } },
    { k: 'schedule', label: '일정', href: 'schedule.html' },
    { k: 'forms', label: '양식 자료실', href: 'forms.html' }
  ];
  var SUB = [
    { k: 'grade', label: '선생님 ' + G.members.length + '명', href: 'grade.html', icon: 'people' },
    { k: 'settings', label: '설정', href: 'settings.html', icon: 'settings' }
  ];

  function mount(opt) {
    opt = opt || {};
    var page = document.body.dataset.page;
    var side = document.createElement('aside');
    side.className = 'side'; side.setAttribute('aria-label', '고티처 메뉴');
    side.innerHTML =
      '<a class="brand" href="today.html" aria-label="고티처 오늘로">' + U.mark(24) + '<span class="wm">고티처</span></a>' +
      '<nav class="nav" aria-label="주 메뉴">' + NAV.map(function (n) {
        return '<a href="' + n.href + '"' + (n.k === page ? ' aria-current="page"' : '') + '>' + U.icon(n.k) + '<span>' + n.label + '</span>' + (n.cnt ? '<span class="cnt num" data-cnt="' + n.k + '" aria-label="낼 것 ' + n.cnt() + '건">' + n.cnt() + '</span>' : '') + '</a>';
      }).join('') +
      // 폰 하단 바에서만 보이는 동학년 칸
      '<a href="grade.html" class="phone-only"' + (page === 'grade' ? ' aria-current="page"' : '') + '>' + U.icon('people') + '<span>동학년</span></a>' +
      '</nav>' +
      '<p class="group">' + U.esc(G.space.grade + '학년 동학년') + '</p>' +
      '<nav class="nav sub" aria-label="동학년">' + SUB.map(function (n) {
        return '<a href="' + n.href + '"' + (n.k === page ? ' aria-current="page"' : '') + '>' + U.icon(n.icon) + '<span>' + n.label + '</span></a>';
      }).join('') + '</nav>' +
      '<a class="me" href="settings.html#me">' + U.av(G.me.name, 'lg') + '<div><b>' + U.esc(G.me.name) + '</b><span>' + U.esc(G.me.role) + '</span></div></a>';

    var top = document.createElement('header');
    top.className = 'top';
    var left = '';
    if (opt.crumb) {
      left = '<nav class="crumb" aria-label="위치">' + opt.crumb.map(function (c, i) {
        return (i ? U.icon('chev', 16) : '') + (c[1] ? '<a href="' + c[1] + '">' + U.esc(c[0]) + '</a>' : '<b>' + U.esc(c[0]) + '</b>');
      }).join('') + '</nav>';
    }
    if (opt.quick !== false && !opt.crumb) {
      left = '<label class="quick"><span class="sr">한 줄로 일정 추가</span>' + U.icon('plus', 18) +
        '<input id="quick" autocomplete="off" placeholder="한 줄로 일정 추가 (예: 내일 2블록 과학실 안전교육)"></label>';
    }
    var live = (opt.online || ['송민재', '정하은', '김도윤']);
    var here = NAV.concat(SUB).filter(function (n) { return n.k === page; })[0];
    top.innerHTML = '<b class="ptitle">' + U.mark(20) + U.esc(opt.phoneTitle || (here ? here.label : '고티처')) + '</b>' + left +
      '<div class="online"><span class="stack">' + live.map(function (n) { return U.av(n, 'sm'); }).join('') + '</span><span class="lbl">동학년 ' + live.length + '명 접속 중</span></div>' +
      '<button class="bell" type="button" id="bellBtn" aria-haspopup="true" aria-expanded="false">' + U.icon('bell', 18) + '<i class="dot-n" aria-hidden="true"></i><span class="lbl">다음 알림 <b class="num">' + G.nextBundle.at + '</b></span><span class="sr">알림 묶음 열기</span></button>' +
      '<button class="switch" type="button" id="spaceBtn" aria-haspopup="true" aria-expanded="false" aria-label="공간: ' + U.esc(G.space.name) + '"><span class="av sm" style="background:var(--ink)">' + G.space.grade + '</span><span><b>' + U.esc(G.space.name) + '</b><span>' + G.space.kind + ' 공간 · ' + G.members.length + '명</span></span></button>';

    document.body.insertBefore(top, document.body.firstChild);
    document.body.insertBefore(side, document.body.firstChild);

    // 알림 묶음
    var bell = document.createElement('div');
    bell.className = 'pop'; bell.id = 'bellPop'; bell.hidden = true; bell.setAttribute('role', 'dialog'); bell.setAttribute('aria-label', '알림 묶음');
    bell.style.cssText = 'top:58px;right:220px;width:420px';
    bell.innerHTML = G.bundles.map(function (b) {
      return '<div class="ph"><b>' + U.esc(b.label) + ' · <span class="num">' + b.at + '</span></b><span>' + b.items.length + '건</span></div>' +
        b.items.map(function (it) { return '<a class="pi" href="' + it.href + '"><span class="pill ' + (it.kind === '마감' ? 'acc' : '') + '">' + it.kind + '</span><span>' + U.esc(it.text) + '</span></a>'; }).join('');
    }).join('') +
      '<div class="pf">다음 묶음 <b class="num" style="color:var(--ink)">' + G.nextBundle.at + '</b>(' + G.nextBundle.label + ') · 수업 중에는 창을 띄우지 않아요. <a href="settings.html#alerts" class="tlink acc">알림 시간 바꾸기</a></div>';
    document.body.appendChild(bell);

    var sp = document.createElement('div');
    sp.className = 'pop'; sp.id = 'spacePop'; sp.hidden = true; sp.setAttribute('role', 'dialog'); sp.setAttribute('aria-label', '공간');
    sp.style.cssText = 'top:58px;right:16px;width:300px';
    sp.innerHTML = '<div class="ph"><span>지금 공간</span></div>' +
      '<a class="pi" href="grade.html"><span class="av sm" style="background:var(--ink)">' + G.space.grade + '</span><span><b>' + U.esc(G.space.name) + '</b><br><span class="small muted">' + G.space.kind + ' · ' + G.members.length + '명 · 만든 사람 ' + U.esc(G.space.by) + '</span></span></a>' +
      '<button class="pi tlink" type="button" id="spInvite" style="width:100%;text-align:left">' + U.icon('link', 18) + '<span>초대 링크 복사</span></button>' +
      '<div class="pf">학교 전체가 함께 쓰는 학교판은 2027년 2월에 열려요.</div>';
    document.body.appendChild(sp);

    function toggle(btn, pop) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var open = pop.hidden;
        U.$$('.pop').forEach(function (p) { p.hidden = true; });
        U.$$('[aria-expanded]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
        pop.hidden = !open; btn.setAttribute('aria-expanded', String(open));
        if (open && btn.id === 'bellBtn') { var d = btn.querySelector('.dot-n'); if (d) d.remove(); }
      });
    }
    toggle(U.$('#bellBtn'), bell);
    toggle(U.$('#spaceBtn'), sp);
    document.addEventListener('click', function (e) { if (!e.target.closest('.pop')) { U.$$('.pop').forEach(function (p) { p.hidden = true; }); U.$$('.top [aria-expanded]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); }); } });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') U.$$('.pop').forEach(function (p) { p.hidden = true; }); });
    U.$('#spInvite').onclick = function () { U.copy(G.space.invite, '초대 링크를 복사했어요 · ' + G.space.invite); };

    // 한 줄 일정
    var q = U.$('#quick');
    if (q) {
      q.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' || !q.value.trim()) return;
        var r = U.parseLine(q.value.trim());
        U.toast('「' + (r.title || q.value.trim()) + '」 ' + (r.date || '오늘') + ' ' + (r.slot ? r.slot.split(' ')[0] : '시간 미정') + (r.place ? ' · ' + r.place : '') + '에 올렸어요');
        q.value = '';
        if (opt.onQuick) opt.onQuick(r);
      });
      document.addEventListener('keydown', function (e) { if (e.key === 'n' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); q.focus(); } });
    }
  }

  window.GTShell = { mount: mount, setCount: function (k, n) { U.$$('[data-cnt="' + k + '"]').forEach(function (el) { el.textContent = n; el.setAttribute('aria-label', '낼 것 ' + n + '건'); }); } };
})();
