// 고티처 시안 B — 하루 캘린더 부품. 할 일 카드를 블록 칸으로 끌어다 놓는다(클릭으로 골라 칸을 눌러도 된다).
// GTCal.mount({ cal, list, lanes, laneOf, nowAt, todos, onChange })
(function () {
  var NOW = '11:05'; // 시안 기준 지금 시각(한 곳에서만 정한다)
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function min(t) { var p = t.split(':'); return +p[0] * 60 + +p[1]; }
  function dueMin(t) { var m = /(\d{1,2}):(\d{2})/.exec(t.due || ''); return t.due && t.due.indexOf('오늘') === 0 && m ? +m[1] * 60 + +m[2] : null; }

  var toastEl;
  function toast(msg) {
    if (!toastEl) { toastEl = document.createElement('div'); toastEl.className = 'cal-toast'; toastEl.setAttribute('role', 'status'); document.body.appendChild(toastEl); }
    toastEl.textContent = msg; toastEl.classList.add('show');
    clearTimeout(toast.t); toast.t = setTimeout(function () { toastEl.classList.remove('show'); }, 2400);
  }

  function mount(o) {
    var G = window.GT, cal = o.cal, list = o.list;
    var rows = [{ k: 'all', label: '전일', sub: '시간 미정' }].concat(G.slots.map(function (s) { return { k: s.k, label: s.label, sub: s.from + (s.to ? '–' + s.to : '~'), from: s.from, to: s.to || '17:00' }; }));
    var todos = (o.todos || G.todo).map(function (t) { return { t: t, at: null }; });
    var picked = null, lastDrop = null;

    function items(rowK) {
      var out = [];
      G.timetable.forEach(function (x) { if (rowK === x.k) out.push({ kind: 'class', lane: o.laneOf('class'), title: x.sub, sub: x.room + (x.note ? ' · ' + x.note : '') }); });
      G.events.forEach(function (e) {
        var k = e.s || 'all';
        if (k !== rowK) return;
        if (e.mine && e.s) return; // 내 수업 칸이 이미 보여 준다
        var sub = e.changed ? '<s>' + esc(e.changed.from) + '</s> → <mark>' + esc(e.changed.to) + '</mark>' + (e.place && e.changed.what !== '장소' ? ' · ' + esc(e.place) : '') : esc([e.place, e.who].filter(Boolean).join(' · '));
        out.push({ kind: e.mine ? 'class' : 'school', lane: o.laneOf(e.mine ? 'class' : 'school'), title: e.title, subHtml: sub });
      });
      todos.forEach(function (d) { if (d.at === rowK) out.push({ kind: 'todo', lane: o.laneOf('todo'), todo: d }); });
      return out;
    }

    function evHtml(it) {
      if (it.kind === 'todo') {
        var d = it.todo, dm = dueMin(d.t), row = rows.filter(function (r) { return r.k === d.at; })[0];
        var late = dm !== null && row && row.from && min(row.from) >= dm;
        return '<div class="ev todo' + (lastDrop === d ? ' pop' : '') + '"><b>' + esc(d.t.title) + '</b><span>' + esc(d.t.kind) + ' · 마감 ' + esc(d.t.due) +
          ' <button class="undo" data-undo="' + d.t.id + '">되돌리기</button></span>' + (late ? '<i class="warn" style="font-style:normal">마감이 지나요</i>' : '') + '</div>';
      }
      return '<div class="ev ' + it.kind + '"><b>' + esc(it.title) + '</b><span>' + (it.subHtml != null ? it.subHtml : esc(it.sub)) + '</span></div>';
    }

    function render() {
      var n = o.lanes.length;
      cal.style.setProperty('--lanes', n);
      var h = '<div class="cal-head"><span></span>' + o.lanes.map(function (l) { return '<span>' + esc(l) + '</span>'; }).join('') + '</div>';
      rows.forEach(function (r) {
        var its = items(r.k), cells = [];
        for (var i = 0; i < n; i++) cells.push([]);
        its.forEach(function (it) { cells[it.lane].push(evHtml(it)); });
        var todoLane = o.laneOf('todo');
        cells[todoLane].push('<button class="slot-btn" data-slot="' + r.k + '">' + r.label + '에 놓기</button>');
        var isNow = r.k === G.nowSlot, urg = todos.filter(function (d) { return dueMin(d.t) !== null; })[0], udm = urg ? dueMin(urg.t) : null;
        var hasDue = udm !== null && r.from && udm > min(r.from) && udm < min(r.to);
        h += '<div class="cal-row' + (r.k === 'all' ? ' all' : '') + (isNow ? ' now' : '') + '" data-row="' + r.k + '"><div class="lab"><b>' + r.label + '</b><span>' + r.sub + '</span>' +
          (isNow ? '<em class="tag now">지금 ' + NOW + '</em>' : '') + (hasDue && o.dueLine !== false ? '<em class="tag due">' + esc(urg.t.due.replace('오늘 ', '')) + ' 마감</em>' : '') + '</div>' +
          cells.map(function (c) { return '<div class="cal-cell">' + c.join('') + '</div>'; }).join('');
        if (isNow && r.from) h += '<div class="nowline" style="top:calc(' + ((min(NOW) - min(r.from)) / (min(r.to) - min(r.from)) * 100).toFixed(1) + '%)"></div>';
        var urgent = todos.filter(function (d) { return dueMin(d.t) !== null; })[0];
        if (urgent && r.from && o.dueLine !== false) { var dm = dueMin(urgent.t); if (dm > min(r.from) && dm < min(r.to)) h += '<div class="dueline" style="top:' + ((dm - min(r.from)) / (min(r.to) - min(r.from)) * 100).toFixed(1) + '%"></div>'; }
        h += '</div>';
      });
      cal.innerHTML = h;
      cal.classList.toggle('picking', !!picked);

      list.innerHTML = todos.map(function (d) {
        var t = d.t, row = rows.filter(function (r) { return r.k === d.at; })[0];
        return '<div class="todo' + (d.at ? ' placed' : '') + (picked === d ? ' picked' : '') + '" draggable="' + (d.at ? 'false' : 'true') + '" data-id="' + t.id + '">' +
          '<div class="tt">' + esc(t.title) + '</div><div class="due' + (t.urgent ? ' urgent' : '') + '">' + esc(t.due) + '</div>' +
          '<div class="fr">' + esc(t.from) + (t.form ? ' · ' + esc(t.form) : '') + '</div>' +
          '<button class="pick" data-pick="' + t.id + '" aria-pressed="' + (picked === d) + '">' + (d.at ? row.label + ' · 되돌리기' : picked === d ? '칸을 고르세요' : '시간 정하기') + '</button></div>';
      }).join('');
      bind();
      lastDrop = null;
      if (o.onChange) o.onChange(todos);
    }

    function byId(id) { return todos.filter(function (d) { return d.t.id === id; })[0]; }
    function place(d, rowK) {
      var row = rows.filter(function (r) { return r.k === rowK; })[0];
      d.at = rowK; picked = null; lastDrop = d; render();
      var dm = dueMin(d.t), late = dm !== null && row.from && min(row.from) >= dm;
      toast('「' + d.t.title + '」 ' + row.label + '에 넣었어요' + (late ? ' — 마감 ' + d.t.due.replace('오늘 ', '') + '이 지나요' : ''));
    }

    function bind() {
      list.querySelectorAll('.todo[draggable="true"]').forEach(function (el) {
        el.addEventListener('dragstart', function (e) { e.dataTransfer.setData('text/plain', el.dataset.id); e.dataTransfer.effectAllowed = 'move'; el.classList.add('dragging'); });
        el.addEventListener('dragend', function () { el.classList.remove('dragging'); });
      });
      list.querySelectorAll('[data-pick]').forEach(function (b) {
        b.onclick = function () {
          var d = byId(b.dataset.pick);
          if (d.at) { d.at = null; picked = null; render(); toast('「' + d.t.title + '」을 목록으로 되돌렸어요'); return; }
          picked = picked === d ? null : d; render();
          if (picked) { var first = cal.querySelector('.slot-btn'); if (first) first.focus(); }
        };
      });
      cal.querySelectorAll('.cal-row').forEach(function (r) {
        r.addEventListener('dragover', function (e) { e.preventDefault(); r.classList.add('over'); });
        r.addEventListener('dragleave', function () { r.classList.remove('over'); });
        r.addEventListener('drop', function (e) { e.preventDefault(); r.classList.remove('over'); var d = byId(e.dataTransfer.getData('text/plain')); if (d && !d.at) place(d, r.dataset.row); });
      });
      cal.querySelectorAll('[data-slot]').forEach(function (b) { b.onclick = function () { if (picked) place(picked, b.dataset.slot); else toast('할 일을 끌어 오거나, 할 일의 「시간 정하기」를 먼저 눌러 주세요'); }; });
      cal.querySelectorAll('[data-undo]').forEach(function (b) { b.onclick = function () { var d = byId(b.dataset.undo); d.at = null; render(); toast('「' + d.t.title + '」을 목록으로 되돌렸어요'); }; });
    }
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && picked) { picked = null; render(); } });
    render();
    return { todos: todos, render: render };
  }
  window.GTCal = { mount: mount, toast: toast, NOW: NOW, esc: esc };
})();
