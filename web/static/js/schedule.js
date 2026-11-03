// 일정 화면(design/schedule.html): 한 줄로 올리고 나면 칸을 비우고 다시 쓸 수 있게 둔다. 판은 서버가 그려 htmx가 바꾼다.
document.addEventListener('htmx:afterRequest', function (e) {
  var f = e.detail.elt;
  if (!f || f.id !== 'addForm' || !e.detail.successful || e.detail.xhr.getResponseHeader('HX-Retarget')) return;
  f.querySelector('input[name=line]').value = '';
  f.querySelector('input[name=line]').focus();
});
