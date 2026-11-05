// 급식 식판 사진(M1): 파일 고르기·끌어 놓기·Ctrl+V 모두 그 칸의 폼으로 보낸다. 사진 검사와 날짜 경고는 서버가 한다.
(function () {
  function say(text) {
    var m = document.getElementById('mealMsg');
    if (m) { m.textContent = text; m.hidden = false; }
  }
  function send(form, file) {
    var box = new DataTransfer();
    box.items.add(file);
    form.querySelector('input[type=file]').files = box.files;
    form.requestSubmit();
  }
  function dayOf(target) {
    var d = target.closest && target.closest('.mday');
    return d && d.querySelector('.mup') ? d : null;
  }
  document.addEventListener('change', function (e) {
    if (e.target.matches('.mup input[type=file]') && e.target.files.length) e.target.form.requestSubmit();
  });
  document.addEventListener('dragover', function (e) {
    e.preventDefault();  // 칸 밖에 놓아도 브라우저가 파일을 열지 않게(이 스크립트는 급식 입력·TV 화면에만 있다)
    var d = dayOf(e.target);
    if (d) d.classList.add('over');
    else e.dataTransfer.dropEffect = 'none';
  });
  document.addEventListener('dragleave', function (e) {
    var d = dayOf(e.target);
    if (d && !d.contains(e.relatedTarget)) d.classList.remove('over');
  });
  document.addEventListener('drop', function (e) {
    e.preventDefault();
    var d = dayOf(e.target);
    if (!d) return;
    d.classList.remove('over');
    var f = e.dataTransfer.files[0];
    if (f) send(d.querySelector('.mup'), f);
  });
  document.addEventListener('paste', function (e) {
    var f = [].slice.call((e.clipboardData && e.clipboardData.files) || []).find(function (x) { return /^image\//.test(x.type); });
    if (!f) return;
    var form = document.querySelector('.mday.today .mup');
    if (!form) { if (document.querySelector('.mup')) say('오늘은 칸이 없어요. 올릴 날짜 칸에 끌어다 놓아 주세요.'); return; }
    e.preventDefault();
    send(form, f);
  });
  // TV 바로보기: 이 클릭에서 같은 출처 iframe을 전체 화면으로. 끝나면 iframe을 지운다. 못 쓰는 브라우저는 링크대로 새 탭(htmx 교체에도 동작하게 위임).
  document.addEventListener('click', function (e) {
    var a = e.target.closest('[data-tv-open]');
    if (!a || !document.fullscreenEnabled) return;
    e.preventDefault();
    var f = document.createElement('iframe');
    f.src = a.getAttribute('href');
    f.title = '급식 TV 화면';
    f.setAttribute('allow', 'fullscreen');
    f.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;border:0;background:#000;z-index:70';
    document.body.appendChild(f);
    function ended() {
      if (document.fullscreenElement) return;
      document.removeEventListener('fullscreenchange', ended);
      f.remove();
      if (document.contains(a)) a.focus();
    }
    document.addEventListener('fullscreenchange', ended);
    f.requestFullscreen().catch(function () {
      document.removeEventListener('fullscreenchange', ended);
      f.remove();
      window.open(a.href, '_blank', 'noopener');
    });
  });
  // 폴링([data-poll]) 실패는 알리지 않는다: 사용자가 누른 일이 아니고, 다음 폴링이 다시 본다
  document.body.addEventListener('htmx:responseError', function (e) {
    if (!e.detail.elt.closest('#mealMain')) return;
    e.stopPropagation();  // 공통 안내(gt.js) 대신 이 화면의 안내 칸에
    var st = e.detail.xhr.status;
    if (!e.detail.elt.hasAttribute('data-poll')) say(st === 413 ? '사진이 너무 커요. 크기를 줄여 다시 올려 주세요.' : st === 429 ? '지금 올리는 사람이 많아요. 잠시 뒤 다시 올려 주세요.' : '처리하지 못했어요. 잠시 뒤 다시 해 주세요.');
  });
  document.body.addEventListener('htmx:sendError', function (e) {
    if (e.detail.elt.closest('#mealMain') && !e.detail.elt.hasAttribute('data-poll')) say('인터넷 연결을 확인하고 다시 해 주세요.');
  });
})();
