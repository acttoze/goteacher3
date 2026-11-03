// 공지 봄 기록(I2): 화면을 보이는 상태로 2초 두거나 끝까지 내리면 한 번 보낸다. 링크 미리보기 봇은 스크립트를 돌리지 않아 기록되지 않는다.
(function () {
  var form = document.getElementById('seenForm');
  if (!form) return;
  var sent = false, timer = null;
  function send() {
    if (sent) return;
    sent = true;
    clearTimeout(timer);
    fetch(form.action, { method: 'POST', credentials: 'same-origin', body: new FormData(form) })
      .then(function (r) { if (!r.ok) sent = false; }, function () { sent = false; });
  }
  function arm() {  // 뒤 탭으로 열어 둔 시간은 세지 않는다
    clearTimeout(timer);
    if (document.visibilityState === 'visible') timer = setTimeout(send, 2000);
  }
  function atEnd() {
    var d = document.documentElement;
    if (window.scrollY > 0 && window.scrollY + window.innerHeight >= d.scrollHeight - 2) send();
  }
  document.addEventListener('visibilitychange', arm);
  window.addEventListener('scroll', atEnd, { passive: true });
  arm();
})();
