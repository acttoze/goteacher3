// 복무 붙여넣기(J1). 표 읽기는 서버가 한다. 한글·엑셀 표는 HTML을 미리보기에 한 번 실어 보내고, 원문은 읽은 뒤 칸에서 지운다(사유가 화면에 남지 않게).
(function () {
  var ta = document.getElementById('attPaste'), html = document.getElementById('attHtml');
  if (!ta) return;
  ta.addEventListener('paste', function (e) {
    if (!e.clipboardData) return;
    var h = e.clipboardData.getData('text/html');
    e.preventDefault();
    html.value = /<table[\s>]/i.test(h) ? h : '';
    ta.value = html.value ? '' : e.clipboardData.getData('text/plain');
    htmx.trigger(ta, 'gt-pasted');
  });
  document.body.addEventListener('htmx:responseError', function (e) {
    if (e.detail.elt !== ta || (e.detail.xhr.status !== 400 && e.detail.xhr.status !== 413)) return;
    e.stopPropagation();  // 공통 안내(gt.js) 대신 이 칸 아래에
    var p = document.createElement('p');
    p.className = 'field-err'; p.setAttribute('role', 'alert');
    p.textContent = '표가 너무 커요. 500줄 안으로 나눠 붙여넣어 주세요.';
    document.getElementById('attOut').replaceChildren(p);
  });
  document.body.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail.elt !== ta) return;
    html.value = '';
    ta.value = '';  // 어떤 길로 들어왔든 읽은 뒤에는 칸에 남기지 않는다
  });
})();
