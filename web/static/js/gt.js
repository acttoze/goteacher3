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

  document.addEventListener('click', function (e) {
    // 복사: <button data-copy="#id" data-done="알림">. 입력 칸이면 value, 아니면 글자. htmx로 바뀐 내용도 누를 때 읽는다.
    var c = e.target.closest('[data-copy]');
    if (c) {
      var src = document.querySelector(c.dataset.copy);
      if (!src) return;
      var text = 'value' in src && src.tagName === 'INPUT' ? src.value : src.textContent;
      var done = function () { toast(c.dataset.done || '복사했어요'); };
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
