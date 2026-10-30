// 제출 화면(받는 사람) 작은 동작. 값 검사·저장은 서버가 한다. htmx로 바뀐 폼에도 그대로 동작(위임).
(function () {
  // 자동 계산 칸: 한 줄의 숫자 칸을 곱한다(prototype itemsForm: 금액 = 수량 × 단가)
  function calc(line) {
    var out = line.querySelector('[data-calc]');
    if (!out) return;
    var nums = line.querySelectorAll('[data-num]');
    if (nums.length < 2) return;
    var v = 1;
    nums.forEach(function (n) { v *= parseFloat(n.value.replace(/,/g, '')) || 0; });
    out.textContent = v.toLocaleString('ko-KR');
  }
  function all() { document.querySelectorAll('[data-line]').forEach(calc); }

  document.addEventListener('input', function (e) {
    var el = e.target;
    if (el.matches('[data-num]')) {
      var x = el.value.replace(/[^0-9.,]/g, '');
      if (x !== el.value) el.value = x;
      el.classList.remove('bad');
      var line = el.closest('[data-line]');
      if (line) calc(line);
    }
  });
  // 연수 이수 '해당 없음': 이수번호 칸을 비우고 막는다(prototype matrixForm)
  document.addEventListener('change', function (e) {
    var cb = e.target.closest('[data-na]');
    if (!cb) return;
    var t = document.getElementById(cb.dataset.na);
    if (!t) return;
    t.disabled = cb.checked;
    if (cb.checked) t.value = '';
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-add-line]')) {
      var t = document.getElementById('lineT'), box = document.getElementById('lines');
      if (!t || !box) return;
      box.appendChild(t.content.cloneNode(true));
      var first = box.lastElementChild.querySelector('input:not([type=hidden]), select');
      if (first) first.focus();
      return;
    }
    var del = e.target.closest('[data-del-line]');
    if (del) {
      var lines = document.querySelectorAll('#lines [data-line]');
      if (lines.length > 1) del.closest('[data-line]').remove();
      else del.closest('[data-line]').querySelectorAll('input:not([type=hidden]), select').forEach(function (i) { i.value = ''; });
    }
  });
  // 칸 오류(400)·마감(403)도 서버가 그린 화면으로 바꾼다(htmx는 4xx를 기본으로 버린다)
  document.addEventListener('htmx:beforeSwap', function (e) {
    var s = e.detail.xhr.status;
    if (s === 400 || s === 403) { e.detail.shouldSwap = true; e.detail.isError = false; }
  });
  // 칸을 고르고 들어오면 첫 입력 칸에 바로 쓴다(design: a.focus()). 이미 낸 값이 있으면 그대로 둔다.
  document.addEventListener('DOMContentLoaded', function () {
    all();
    var first = document.querySelector('#f input:not([type=hidden]), #f select');
    if (first && !first.value) first.focus();
  });
  document.addEventListener('htmx:afterSwap', function () {
    all();
    var ok = document.getElementById('ok');
    if (ok) ok.focus();
    window.scrollTo(0, 0);
  });
})();
