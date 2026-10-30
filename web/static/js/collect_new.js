// 새로 모으기 — 표 붙여넣기(C2). 표 읽기·분류는 서버(collect/paste.py)가 하고, 여기서는 클립보드만 넘긴다.
// 한글에서 표를 복사하면 글자(text/plain)에는 칸 구분이 없고 표는 HTML에만 있다(research/05: 649줄×1칸).
// 그래서 HTML에 표가 있으면 그 HTML을 미리보기 요청에 한 번 실어 보내고, 서버가 돌려준 TSV로 입력 칸을 채운다.
(function () {
  var ta = document.getElementById('paste'), html = document.getElementById('pasteHtml');
  if (!ta || !html) return;
  var SAMPLE = '반\t담임\t참가(명)\t불참(명)\t비고\n4-1\t\t\t\t\n4-2\t\t\t\t\n4-3\t\t\t\t\n4-4\t\t\t\t\n4-5\t\t\t\t\n합계\t\t\t\t';

  ta.addEventListener('paste', function (e) {
    var h = e.clipboardData && e.clipboardData.getData('text/html');
    if (!h || !/<table[\s>]/i.test(h)) return;  // 표가 없으면 글자(엑셀·시트의 탭 글자) 그대로
    e.preventDefault();
    html.value = h;
    htmx.trigger(ta, 'gt-pasted');
  });
  document.body.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail.elt === ta) html.value = '';  // HTML은 한 번만 보낸다
  });
  document.body.addEventListener('htmx:afterSwap', function () {
    var out = document.getElementById('tsvOut');
    if (out) { ta.value = out.value; out.remove(); }
  });
  document.getElementById('sample').addEventListener('click', function () {
    ta.value = SAMPLE;
    var t = document.getElementById('title');
    if (!t.value.trim()) { t.value = '학예회 반별 참가 인원'; t.dispatchEvent(new Event('input')); }
    htmx.trigger(ta, 'gt-pasted');
  });
  // 쪽지 미리보기의 제목
  document.getElementById('title').addEventListener('input', function () {
    var v = this.value.trim() || '(제목)';
    document.querySelectorAll('[data-title]').forEach(function (x) { x.textContent = v; });
  });
})();

// C3: 가져오는 방식 탭(표 붙여넣기·직접 입력·자주 쓰는 양식). 고른 방식은 숨은 칸 src로 서버에 간다.
(function () {
  var src = document.getElementById('src');
  if (!src) return;
  function show(v) {
    src.value = v;
    document.querySelectorAll('[data-src]').forEach(function (b) { if (!b.disabled) b.setAttribute('aria-pressed', String(b.dataset.src === v)); });
    document.querySelectorAll('[data-pane]').forEach(function (p) { p.hidden = p.dataset.pane.split(' ').indexOf(v) < 0; });
  }
  document.querySelectorAll('[data-src]').forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.src); }); });
  var add = document.getElementById('addCol');
  if (add) add.addEventListener('click', function () {
    var next = document.querySelector('tr[data-col][hidden]');
    if (!next) { document.getElementById('addColMsg').textContent = '직접 입력은 칸 6개까지예요. 더 많으면 표를 붙여넣어 주세요.'; return; }
    next.hidden = false;
    next.querySelector('input').focus();
  });
})();
