// 고티처 공통 스크립트. 상태는 서버가 갖고 갱신은 htmx가 한다. 여기에는 화면 안 작은 동작만 둔다.
(function () {
  // 알림 한 줄(design/assets/shell.js U.toast)
  var timer;
  function toast(text) {
    var el = document.getElementById('toast');
    if (!el) {
      el = document.createElement('div'); el.id = 'toast'; el.className = 'toast';
      el.setAttribute('role', 'status'); el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(timer); timer = setTimeout(function () { el.classList.remove('show'); }, 2800);
  }

  // 대화상자(design .scrim): <button data-open="#id">로 열고, [data-close]·바깥·Esc로 닫는다.
  var opener;
  function closeDlg(d) { d.hidden = true; if (opener && document.contains(opener)) opener.focus(); }
  document.addEventListener('keydown', function (e) {
    var d = document.querySelector('.scrim:not([hidden])');
    if (e.key === 'Escape' && d) closeDlg(d);
  });
  // 저장 뒤 바뀐 내용에 [data-autocopy="알림"]이 있으면 그 단추의 복사를 한 번 한다(prototype: 마감 저장 = 안내 문구 복사).
  // 브라우저가 클립보드를 막으면 단추를 눌러 달라고 알린다.
  document.addEventListener('htmx:afterSwap', function () {
    var t = document.querySelector('[data-toast-now]');  // 저장 결과 한 줄(마감하고 잠그기·다시 열기)
    if (t) { toast(t.dataset.toastNow); t.removeAttribute('data-toast-now'); }
    var b = document.querySelector('[data-autocopy]');
    if (!b) return;
    var msg = b.dataset.autocopy, src = document.querySelector(b.dataset.copy);
    b.removeAttribute('data-autocopy');
    var fail = function () { toast(msg.replace(/안내 문구를 복사했어요\./, '「' + b.textContent + '」를 눌러 쿨메신저에 붙여넣으세요.')); };
    if (src && navigator.clipboard) navigator.clipboard.writeText(src.textContent).then(function () { toast(msg); }, fail);
    else fail();
  });

  document.addEventListener('click', function (e) {
    var o = e.target.closest('[data-open]');
    if (o) {
      var d = document.querySelector(o.dataset.open);
      if (d) { opener = o; d.hidden = false; var f = d.querySelector('input:not([type=hidden]),button'); if (f) f.focus(); }
      return;
    }
    if (e.target.closest('[data-close]') || e.target.classList.contains('scrim')) {
      closeDlg(e.target.closest('.scrim'));
      return;
    }
    // 복사: <button data-copy="#id" data-done="알림"> 또는 <button data-text="글자">. 입력 칸이면 value, 아니면 글자.
    // htmx로 바뀐 내용도 누를 때 읽는다. 원본에 data-done이 있으면 그 알림, 원본 글자가 비면 알림만 띄운다.
    var c = e.target.closest('[data-copy],[data-text]');
    if (c) {
      var src = c.dataset.copy ? document.querySelector(c.dataset.copy) : null;
      if (c.dataset.text == null && !src) return;
      var text = c.dataset.text != null ? c.dataset.text : src.tagName === 'INPUT' ? src.value : src.textContent;
      var msg = (src && src.dataset.done) || c.dataset.done || '복사했어요';
      if (!text.trim()) { toast(msg); return; }
      var done = function () { toast(msg); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () { toast(text); });
      else toast(text);
      return;
    }

    // 고르기 단추 묶음: <div data-pick="이름"> 안의 [data-v]를 누르면
    // 같은 폼의 <input type="hidden" name="이름">에 값을 넣고 aria-pressed를 바꾼다. htmx로 바뀐 폼에도 그대로 동작(위임).
    var b = e.target.closest('[data-pick] [data-v]');
    if (!b) return;
    var group = b.closest('[data-pick]'), form = b.closest('form');
    group.querySelectorAll('[data-v]').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    var input = form && form.querySelector('input[type=hidden][name="' + group.dataset.pick + '"]');
    if (input) input.value = b.dataset.v;
    if (group.getAttribute('aria-invalid') === 'true') {  // 고르면 칸 옆 오류를 지운다
      var err = document.getElementById(group.getAttribute('aria-describedby'));
      if (err) err.remove();
      group.removeAttribute('aria-invalid');
      group.removeAttribute('aria-describedby');
    }
    if (b.dataset.toast) toast(b.dataset.toast);
  });
})();
