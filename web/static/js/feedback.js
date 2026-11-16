// V6 의견 보내기·예전 고티처로(feedback/_dialogs.html). 열고 닫기·토스트는 gt.js, 보내기는 htmx.
(function () {
  // 보내기 직전에 지금 화면 주소와 창 크기를 넣는다. 서버는 주소 규칙 모양만 남긴다. 화면 내용은 넣지 않는다.
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f.matches('[data-fb]')) return;
    f.elements.path.value = location.pathname;
    f.elements.screen.value = window.innerWidth + 'x' + window.innerHeight;
  }, true);
  document.addEventListener('htmx:afterRequest', function (e) {
    var f = e.detail.elt;
    if (!f.matches || !f.matches('[data-fb]')) return;
    if (f.dataset.site) { leave(f.dataset.site); return; }  // 돌아가기: 저장이 안 돼도(제한·연결 끊김) 예전 고티처로 간다
    if (!e.detail.successful) return;
    f.reset();
    f.closest('.scrim').querySelector('[data-close]').click();
  });
  function leave(site) {
    if (!/^https?:\/\//.test(site)) return;
    window.close();  // 예전 고티처가 새 탭으로 열어 준 탭이면 닫혀서 원래 탭(예전 고티처)이 남는다
    setTimeout(function () { location.href = site; }, 300);  // 닫히지 않는 탭이면 이 탭에서 예전 고티처로
  }
})();
