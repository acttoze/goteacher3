// 일정 화면(Q2): 칸 빈 곳이나 빈 줄을 누르면 그 자리에 카드가 열린다. Enter·칸 밖 누르기로 저장, Esc·빈 내용은 취소.
// 판은 서버가 그려 htmx가 통째로 바꾸므로 모두 문서에 위임한다.
(function () {
  var open = null;    // 열린 카드(form)
  var saving = false;
  var next = null;    // 저장하는 동안 누른 다른 칸 → 판이 바뀐 뒤 연다
  var back = null;    // Enter로 저장한 칸 → 판이 바뀐 뒤 그 칸 단추로 포커스
  var inside = false; // 카드 안을 누르는 중
  var enterLater = false; // 한글 조합 중 누른 Enter

  function keyOf(cell) { return cell ? { date: cell.dataset.date, slot: cell.dataset.slot } : null; }
  function find(k) { return document.querySelector('[data-cell][data-date="' + k.date + '"][data-slot="' + k.slot + '"]'); }
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
})();
