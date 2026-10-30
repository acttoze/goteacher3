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
    else if (e.key === 'Escape' && closePops()) document.getElementById('bellBtn').focus();
  });
  // 🔔 알림 목록(design .pop): 단추로 열고 닫는다. 목록은 htmx가 열 때마다 서버에서 받는다(hx-get).
  function closePops() {
    var b = document.getElementById('bellBtn'), p = document.getElementById('bellPop');
    if (!b || !p || p.hidden) return false;
    p.hidden = true; b.setAttribute('aria-expanded', 'false');
    return true;
  }
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

  // 한 줄 일정(오늘): 올리면 칸을 비우고, Esc·바깥을 누르면 미리보기를 닫고, n으로 칸에 들어간다(design shell.js).
  function hideQ() { var q = document.getElementById('qpop'); if (q) q.hidden = true; }
  document.addEventListener('htmx:afterRequest', function (e) {
    var f = e.detail.elt;
    if (f && f.id === 'quickForm' && e.detail.successful && !e.detail.xhr.getResponseHeader('HX-Retarget')) { f.reset(); f.querySelector('input[name=line]').focus(); }
  });
  document.addEventListener('keydown', function (e) {
    var q = document.getElementById('quick');
    if (!q) return;
    if (e.target === q && e.key === 'Escape') hideQ();
    else if (e.key === 'n' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !e.ctrlKey && !e.metaKey && !e.altKey && !document.querySelector('.scrim:not([hidden])')) { e.preventDefault(); q.focus(); }
  });

  document.addEventListener('click', function (e) {
    if (!e.target.closest('.quick')) hideQ();
    if (e.target.closest('#bellBtn')) {
      var p = document.getElementById('bellPop'), open = p.hidden;
      p.hidden = !open; e.target.closest('#bellBtn').setAttribute('aria-expanded', String(open));
      return;
    }
    if (!e.target.closest('.pop')) closePops();
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
    // 합본 표 복사(D4): 서버가 만든 HTML 표와 TSV를 클립보드에 함께 넣는다(한글은 HTML 표를, 메모장·엑셀은 TSV를 읽는다).
    // 누른 순간 ClipboardItem을 만들고 값은 Promise로 채운다(사용자 동작 안에서 써야 하는 브라우저 대비).
    var ct = e.target.closest('[data-copy-table]');
    if (ct) {
      var ok = function () { toast('합본 표를 복사했어요. 한글 문서에서 Ctrl+V 하면 표로 들어가요.'); };
      var bad = function () { toast('복사하지 못했어요. 잠시 뒤 다시 눌러 주세요.'); };
      var got = fetch(ct.dataset.copyTable, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
      var blob = function (k, t) { return got.then(function (d) { return new Blob([d[k]], { type: t }); }); };
      var plain = function () { return got.then(function (d) { return navigator.clipboard.writeText(d.tsv); }); };
      if (!navigator.clipboard) { bad(); return; }
      var w = window.ClipboardItem && navigator.clipboard.write
        ? navigator.clipboard.write([new ClipboardItem({ 'text/html': blob('html', 'text/html'), 'text/plain': blob('tsv', 'text/plain') })]).catch(plain)
        : plain();
      w.then(ok, bad);
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
