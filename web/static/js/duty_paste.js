// 업무분장 붙여넣기(H3). 표 읽기는 서버가 한다. 한글 표는 HTML에만 칸이 있어 그 HTML을 미리보기 요청에 한 번 실어 보내고, 돌려받은 TSV로 칸을 채운다(collect_new.js와 같은 길).
(function () {
  var ta = document.getElementById('dutyPaste'), html = document.getElementById('dutyHtml');
  if (!ta) return;
  ta.addEventListener('paste', function (e) {
    var h = e.clipboardData && e.clipboardData.getData('text/html');
    if (!h || !/<table[\s>]/i.test(h)) return;  // 표가 없으면 글자(엑셀·시트의 탭 글자) 그대로
    e.preventDefault();
    html.value = h;
    htmx.trigger(ta, 'gt-pasted');
  });
  // 서버 업로드 한도를 넘으면 Django가 400을 준다: 칸이 말없이 비어 보이지 않게 안내한다.
  document.body.addEventListener('htmx:responseError', function (e) {
    if (e.detail.elt !== ta || (e.detail.xhr.status !== 400 && e.detail.xhr.status !== 413)) return;
    e.stopPropagation();  // 공통 안내(gt.js) 대신 이 칸 아래에
    var p = document.createElement('p');
    p.className = 'field-err'; p.setAttribute('role', 'alert'); p.style.marginTop = '12px';
    p.textContent = '표가 너무 커요. 500줄 안으로 나눠 붙여넣어 주세요.';
    document.getElementById('dutyOut').replaceChildren(p);
  });
  document.body.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail.elt === ta) html.value = '';  // HTML은 한 번만 보낸다
  });
  document.body.addEventListener('htmx:afterSwap', function () {
    var out = document.getElementById('tsvOut');
    if (out) { ta.value = out.value; out.remove(); }
  });
})();
