// 일정 화면(Q2): 칸 빈 곳이나 빈 줄을 누르면 그 자리에 카드가 열린다. Enter·칸 밖 누르기로 저장, Esc·빈 내용은 취소.
// 판은 서버가 그려 htmx가 통째로 바꾸므로 모두 문서에 위임한다.
(function () {
  var open = null;    // 열린 카드(form)
  var saving = false;
  var next = null;    // 저장하는 동안 누른 다른 칸 → 판이 바뀐 뒤 연다
  var back = null;    // Enter로 저장한 칸 → 판이 바뀐 뒤 그 칸 단추로 포커스
  var inside = false; // 카드 안을 누르는 중
  var enterLater = false; // 한글 조합 중 누른 Enter

  function keyOf(cell) { return cell ? { date: cell.dataset.date, slot: cell.dataset.slot, scope: cell.dataset.scope } : null; }
  function find(k) { return document.querySelector('[data-cell][data-date="' + k.date + '"][data-slot="' + k.slot + '"][data-scope="' + k.scope + '"]'); }
  // 누른 곳이 칸의 빈 곳이나 빈 줄이면 그 칸. 일정 카드·링크·단추·입력은 아니다.
  function blankCell(t) {
    var cell = t.closest('[data-cell]');
    if (!cell) return null;
    if (t.closest('.cell-add')) return cell;
    return t.closest('.ev, a, button, input, select, textarea, label, .cell-card') ? null : cell;
  }
  function show(cell) {
    var week = document.getElementById('week');
    var form = document.getElementById('cellTpl').content.firstElementChild.cloneNode(true);
    form.elements.mode.value = week.dataset.mode;
    form.elements.w.value = week.dataset.w;
    form.elements.date.value = cell.dataset.date;
    form.elements.slot.value = cell.dataset.slot;
    if (form.elements.scope) {  // 동학년은 칸이 없다(언제나 모두)
      form.elements.scope.value = cell.dataset.scope;
      form.elements.line.placeholder = form.elements.line.getAttribute('data-ph-' + cell.dataset.scope);
    }
    cell.classList.add('adding');
    cell.insertBefore(form, cell.querySelector('.cell-add'));
    htmx.process(form);
    open = form;
    next = null;
    form.elements.line.focus();
  }
  function close(refocus) {
    var form = open, cell = form.closest('[data-cell]');
    open = null;
    form.remove();
    cell.classList.remove('adding');
    if (refocus) cell.querySelector('.cell-add').focus({ preventScroll: true });
  }
  function commit(byKey) {
    if (!open || saving) return;
    if (!open.elements.line.value.trim()) { close(byKey); return; }
    saving = true;
    if (byKey) back = keyOf(open.closest('[data-cell]'));
    open.requestSubmit();
  }
  function stayOpen() {  // 저장 실패·입력 오류: 쓴 내용을 두고 다시 고치게
    saving = false; next = null; back = null;
    open.elements.line.focus();
  }

  document.addEventListener('mousedown', function (e) {
    inside = false;
    if (!open) return;
    if (open.contains(e.target)) {
      inside = true;
      if (!e.target.closest('input, select')) e.preventDefault();  // 카드 여백을 눌러도 입력칸 포커스를 놓치지 않게
      return;
    }
    next = keyOf(blankCell(e.target));
  });
  document.addEventListener('mouseup', function () { inside = false; });
  document.addEventListener('click', function (e) {
    if (open && open.contains(e.target)) return;
    commit(false);  // focusout을 못 받은 경우(포커스가 이미 카드 밖). 받았으면 아무 일도 없다
    var cell = blankCell(e.target);
    if (!cell) return;
    if (saving) next = keyOf(cell);
    else show(cell);
  });
  document.addEventListener('focusout', function (e) {
    if (!open || saving || inside || !open.contains(e.target) || open.contains(e.relatedTarget)) return;
    if (document.hasFocus()) commit(false);  // 다른 창으로 간 것은 칸 밖 누르기가 아니다
  });
  document.addEventListener('keydown', function (e) {
    if (!open || !open.contains(e.target)) return;
    var composing = e.isComposing || e.keyCode === 229;
    if (e.key === 'Enter') {
      e.preventDefault();
      if (composing) enterLater = true;
      else commit(true);
    } else if (e.key === 'Escape' && !composing) {
      close(true);
    }
  });
  document.addEventListener('compositionend', function () {
    if (!enterLater) return;
    enterLater = false;
    setTimeout(function () { commit(true); });  // 조합이 끝난 글자까지 값에 들어간 뒤
  });

  document.addEventListener('htmx:afterSwap', function () {
    applyFilters();
    if (open && document.contains(open)) {  // 오류 문구만 바뀌었다
      if (saving) stayOpen();
      return;
    }
    open = null; saving = false;  // 판이 바뀌었다
    var k = next || back, reopen = !!next;
    next = back = null;
    var cell = k && find(k);
    if (!cell) return;
    if (reopen) show(cell);
    else cell.querySelector('.cell-add').focus({ preventScroll: true });
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    if (saving && open && !e.detail.successful) stayOpen();
  });

  // 채널·그룹 필터: 꺼진 값의 줄·카드를 숨긴다. 상태는 이 브라우저에만(공간별), 못 읽거나 못 써도 동작한다.
  var FILTER_KEY = 'gt_sched_off:' + location.pathname;
  function readOff() {
    try {
      var v = JSON.parse(localStorage.getItem(FILTER_KEY));
      return v && typeof v === 'object' ? v : {};
    } catch (e) { return {}; }
  }
  function filterInputs() { return document.querySelectorAll('.flt input[data-flt]'); }
  function applyFilters() {
    var off = { ch: {}, grp: {} };
    filterInputs().forEach(function (i) { if (!i.checked) off[i.dataset.flt][i.value] = true; });
    document.querySelectorAll('#week [data-ch], #week [data-group]').forEach(function (el) {
      el.classList.toggle('f-off', !!(off.ch[el.dataset.ch] || off.grp[el.dataset.group]));
    });
    document.querySelectorAll('.flt').forEach(function (box) {
      var btn = box.querySelector('.flt-btn'), n = box.querySelectorAll('input:not(:checked)').length;
      btn.classList.toggle('on', n > 0);
      btn.setAttribute('aria-label', btn.title + (n ? ' (' + n + '개 꺼짐)' : ''));
    });
  }
  function closeMenus() {
    document.querySelectorAll('.flt-pop:not([hidden])').forEach(function (pop) {
      pop.hidden = true;
      pop.previousElementSibling.setAttribute('aria-expanded', 'false');
    });
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.flt-btn');
    if (!btn && e.target.closest('.flt-pop')) return;
    var pop = btn && btn.nextElementSibling, wasClosed = pop && pop.hidden;
    closeMenus();
    if (wasClosed) { pop.hidden = false; btn.setAttribute('aria-expanded', 'true'); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var pop = document.querySelector('.flt-pop:not([hidden])');
    if (!pop) return;
    closeMenus();
    pop.previousElementSibling.focus();
  });
  document.addEventListener('change', function (e) {
    if (!e.target.matches('.flt input[data-flt]')) return;
    var kind = e.target.dataset.flt, state = readOff();  // 이 화면에 없는 종류(주간의 그룹)는 저장된 값을 둔다
    state[kind] = [].filter.call(filterInputs(), function (i) { return i.dataset.flt === kind && !i.checked; })
      .map(function (i) { return i.value; });
    try { localStorage.setItem(FILTER_KEY, JSON.stringify(state)); } catch (err) { /* 저장만 못 한다 */ }
    applyFilters();
  });
  var saved = readOff();
  filterInputs().forEach(function (i) {
    var list = saved[i.dataset.flt];
    i.checked = !(Array.isArray(list) && list.indexOf(i.value) >= 0);
  });
  applyFilters();
})();
