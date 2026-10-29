// 고티처 시안 A — 메인과 오늘이 같이 쓰는 문서 블록 스크립트.
(function () {
  var G = window.GT;
  // 반별 참가 인원(시안용 샘플). 낸 반만 숫자가 있다.
  var COUNTS = { '4-1': [24, 1], '4-2': [25, 0], '4-3': [23, 2] };
  var PEOPLE = { '송민재': 'p1', '정하은': 'p2', '김도윤': 'p3', '이수빈': 'p4', '박서준': 'p5' };

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function rows() {
    return G.sent[0].rows.map(function (r) {
      var p = r.who.split(' '), cls = p[0], name = p[1], c = r.done ? COUNTS[cls] : null;
      return { cls: cls, name: name, done: r.done, yes: c ? c[0] : null, no: c ? c[1] : null, pc: PEOPLE[name] || 'p1' };
    });
  }
  function sums(rs) {
    var d = rs.filter(function (r) { return r.done; });
    return { done: d.length, total: rs.length, yes: d.reduce(function (a, r) { return a + r.yes; }, 0), no: d.reduce(function (a, r) { return a + r.no; }, 0), left: rs.filter(function (r) { return !r.done; }) };
  }
  function av(r) { return '<span class="av ' + r.pc + '" aria-hidden="true">' + esc(r.name[0]) + '</span>'; }

  function table(rs, s) {
    return '<table><thead><tr><th>반</th><th class="hide-s">담임</th><th class="r">참가(명)</th><th class="r">불참(명)</th><th class="r">상태</th></tr></thead><tbody>' +
      rs.map(function (r) {
        return '<tr class="' + (r.done ? '' : 'is-wait') + '"><td><div class="who">' + av(r) + '<span>' + r.cls + '</span></div></td><td class="hide-s">' + esc(r.name) + '</td>' +
          '<td class="r num">' + (r.done ? r.yes : '—') + '</td><td class="r num">' + (r.done ? r.no : '—') + '</td>' +
          '<td class="r"><span class="pill ' + (r.done ? 'done' : 'wait') + '">' + (r.done ? '제출' : '기다림') + '</span></td></tr>';
      }).join('') +
      '<tr><td><b>합계</b></td><td class="hide-s"></td><td class="r num"><b>' + s.yes + '</b></td><td class="r num"><b>' + s.no + '</b></td><td></td></tr></tbody></table>';
  }
  function board(rs) {
    function col(title, list, pill) {
      return '<div class="col"><h4>' + title + ' <span class="pill ' + pill + '">' + list.length + '</span></h4>' +
        list.map(function (r) { return '<div class="card">' + av(r) + '<span>' + r.cls + ' ' + esc(r.name) + '</span><small class="num">' + (r.done ? '참가 ' + r.yes : '아직') + '</small></div>'; }).join('') + '</div>';
    }
    return '<div class="board">' + col('제출', rs.filter(function (r) { return r.done; }), 'done') + col('기다림', rs.filter(function (r) { return !r.done; }), 'wait') + '</div>';
  }
  function calendar(rs, s) {
    var days = [['10/26', '월'], ['10/27', '화'], ['10/28', '수'], ['10/29', '목'], ['10/30', '금']];
    var done = rs.filter(function (r) { return r.done; }).map(function (r) { return r.cls; }).join(' · ');
    return '<div class="cal">' + days.map(function (d, i) {
      var inner = '';
      if (i === 0) inner = '<span class="ev ok">' + done + ' 제출</span><span class="ev">링크 보냄</span>';
      if (i === 1) inner = '<span class="ev due">마감 12:00</span>' + (s.left.length ? '<span class="ev">' + s.left.map(function (r) { return r.cls; }).join(' · ') + ' 기다림</span>' : '');
      return '<div class="d' + (i === 0 ? ' today' : '') + '"><b class="num">' + d[0] + ' ' + d[1] + '</b>' + inner + '</div>';
    }).join('') + '</div>';
  }

  // 모으기 데이터베이스 블록을 el 안에 그린다. opts.tabs=false면 탭을 밖에서 제어.
  function db(el, view, opts) {
    opts = opts || {};
    var rs = rows(), s = sums(rs), sent = G.sent[0];
    var views = [['table', '표'], ['board', '보드'], ['calendar', '캘린더']];
    var body = view === 'board' ? board(rs) : view === 'calendar' ? calendar(rs, s) : table(rs, s);
    el.innerHTML =
      '<div class="db-h"><b>' + esc(sent.title) + '</b><span>' + esc(sent.form) + ' · 마감 ' + esc(sent.due) + '</span></div>' +
      (opts.tabs === false ? '' : '<div class="db-tabs" role="tablist" aria-label="보기">' + views.map(function (v) {
        return '<button type="button" role="tab" data-view="' + v[0] + '" aria-selected="' + (v[0] === view) + '">' + v[1] + '</button>';
      }).join('') + '</div>') +
      '<div class="db-body">' + body + '</div>' +
      '<div class="db-f"><div class="meter"><span class="bar" aria-hidden="true"><i style="width:' + Math.round(s.done / s.total * 100) + '%"></i></span><span class="num"><b>' + s.done + '</b> / ' + s.total + '반 제출</span></div>' +
      '<button type="button" class="btn sm" data-nudge>' + s.left.map(function (r) { return r.cls; }).join('·') + ' 독촉 문구 복사</button></div>';
    el.dataset.view = view;
    el.querySelectorAll('[data-view]').forEach(function (b) { b.onclick = function () { db(el, b.dataset.view, opts); if (opts.onView) opts.onView(b.dataset.view); }; });
    var n = el.querySelector('[data-nudge]');
    if (n) n.onclick = function () {
      var txt = '[고티처] ' + sent.title + ' 마감이 ' + sent.due + '이에요. ' + s.left.map(function (r) { return r.cls + ' ' + r.name; }).join(', ') + ' 선생님, 링크에서 반 칸만 채워 주세요.';
      if (navigator.clipboard) navigator.clipboard.writeText(txt).catch(function () {});
      toast('독촉 문구를 복사했어요');
    };
  }

  var tEl;
  function toast(msg) {
    if (!tEl) { tEl = document.createElement('div'); tEl.className = 'toast'; tEl.setAttribute('role', 'status'); document.body.appendChild(tEl); }
    tEl.textContent = msg; tEl.classList.add('on');
    clearTimeout(tEl._t); tEl._t = setTimeout(function () { tEl.classList.remove('on'); }, 2200);
  }

  window.DocA = { db: db, toast: toast, esc: esc, rows: rows, sums: sums, PEOPLE: PEOPLE };
  var r = rows(), s = sums(r);
  console.assert(s.done === G.sent[0].done && s.total === G.sent[0].total, '모으기 집계가 공통 데이터와 다름');
  console.assert(s.yes === 72 && s.no === 3, '합계 불일치');
})();
