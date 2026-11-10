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
    out.dataset.v = v;
  }
  // S1 예산 품목: 줄 금액 합계와 남은 금액(예산이 있으면). 넘으면 '예산 초과'로 바꾼다. 저장 값은 서버가 다시 계산한다.
  function budget() {
    var box = document.querySelector('[data-budget]');
    if (!box) return;
    var sum = 0;
    document.querySelectorAll('#lines [data-calc]').forEach(function (x) { sum += +x.dataset.v || 0; });
    box.querySelector('[data-total]').textContent = sum.toLocaleString('ko-KR');
    var left = box.querySelector('[data-left-box]');
    if (!left || !box.dataset.budget) return;
    var rest = +box.dataset.budget - sum;
    left.className = rest < 0 ? 'over' : '';
    left.innerHTML = '';
    left.append(rest < 0 ? '예산 초과 ' : '남은 금액 ');
    var b = document.createElement('b');
    b.textContent = Math.abs(rest).toLocaleString('ko-KR');
    left.append(b, '원');
  }
  function all() { document.querySelectorAll('[data-line]').forEach(calc); budget(); }

  document.addEventListener('input', function (e) {
    var el = e.target;
    if (el.matches('[data-num]')) {
      var x = el.value.replace(/[^0-9.,]/g, '');
      if (x !== el.value) el.value = x;
      el.classList.remove('bad');
      var line = el.closest('[data-line]');
      if (line) { calc(line); budget(); }
    }
  });
  // S1 상품 주소 붙이기: 폼 전체가 아니라 그 줄 칸만 보낸다(htmx는 둘러싼 폼 값을 모두 싣는다).
  document.addEventListener('htmx:configRequest', function (e) {
    var line = e.detail.elt.matches('[data-shop]') && e.detail.elt.closest('[data-line]');
    if (!line) return;
    var p = e.detail.parameters, csrf = p.csrfmiddlewaretoken;
    Object.keys(p).forEach(function (k) { delete p[k]; });
    if (csrf) p.csrfmiddlewaretoken = csrf;
    line.querySelectorAll('input[name]').forEach(function (i) { p[i.name] = i.value; });
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
      if (window.htmx) htmx.process(box.lastElementChild);  // S1 새 줄의 상품 주소 칸(hx-post)도 동작하게
      var first = box.lastElementChild.querySelector('input:not([type=hidden]), select');
      if (first) first.focus();
      return;
    }
    var del = e.target.closest('[data-del-line]');
    if (del) {
      var lines = document.querySelectorAll('#lines [data-line]');
      if (lines.length > 1) del.closest('[data-line]').remove();
      else del.closest('[data-line]').querySelectorAll('input:not([type=hidden]), select').forEach(function (i) { i.value = ''; });
      all();
    }
  });
  // 칸 오류(400)·마감(403)·너무 잦음(429)도 서버가 그린 화면으로 바꾼다(htmx는 4xx를 기본으로 버린다)
  document.addEventListener('htmx:beforeSwap', function (e) {
    var s = e.detail.xhr.status;
    if (s === 400 || s === 403 || s === 429) { e.detail.shouldSwap = true; e.detail.isError = false; }
  });
  // 칸을 고르고 들어오면 첫 입력 칸에 바로 쓴다(design: a.focus()). 이미 낸 값이 있으면 그대로 둔다.
  document.addEventListener('DOMContentLoaded', function () {
    all();
    var first = document.querySelector('#f input:not([type=hidden]), #f select');
    if (first && !first.value) first.focus();
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    all();
    if (e.detail.target.id !== 'main') return;  // S1 한 줄 채우기는 그 자리에서(맨 위로 가지 않는다)
    var ok = document.getElementById('ok');
    if (ok) ok.focus();
    window.scrollTo(0, 0);
  });
})();
