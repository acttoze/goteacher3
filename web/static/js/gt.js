// 고티처 공통 스크립트. 상태는 서버가 갖고 갱신은 htmx가 한다. 여기에는 화면 안 작은 동작만 둔다.
(function () {
  // 알림 한 줄(design/assets/shell.js U.toast)
  var timer;
  function toast(text, ms) {
    var el = document.getElementById('toast');
    if (!el) {
      el = document.createElement('div'); el.id = 'toast'; el.className = 'toast';
      el.setAttribute('role', 'status'); el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(timer); timer = setTimeout(function () { el.classList.remove('show'); }, ms || 2800);
  }

  // 대화상자(design .scrim): <button data-open="#id">로 열고, [data-close]·바깥·Esc로 닫는다.
  var opener;
  function closeDlg(d) {
    d.querySelectorAll('[data-link-out]').forEach(function (i) { i.value = ''; });  // 링크 원문을 화면에 남기지 않는다
    d.hidden = true; if (opener && document.contains(opener)) opener.focus();
  }
  document.addEventListener('keydown', function (e) {
    var d = document.querySelector('.scrim:not([hidden])');
    if (e.key === 'Tab' && d) {  // 대화상자 안에서만 돈다
      var all = [].filter.call(d.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select,textarea'), function (x) { return x.offsetParent !== null || x.classList.contains('sr'); });
      var first = all[0], last = all[all.length - 1];
      if (!d.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      return;
    }
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
  // 폴링하는 카드 안 대화상자(예: 명단의 내보내기 확인)가 열려 있으면 그 폴링은 건너뛴다(열린 창이 바뀌지 않게).
  // 빈도 제한(429) 안내는 화면에 그대로 넣는다(htmx 기본은 4xx를 버림). [data-err-swap] 안은 입력 오류(400)도 넣는다.
  // 앞단(Traefik)의 429는 앱 화면이 아닌 글자라 넣지 않는다. 아래 htmx:responseError가 안내한다.
  document.addEventListener('htmx:beforeSwap', function (e) {
    var st = e.detail.xhr.status;
    var page = /text\/html/.test(e.detail.xhr.getResponseHeader('Content-Type') || '');
    if ((st === 429 && page) || (st === 400 && e.detail.elt.closest('[data-err-swap]'))) { e.detail.shouldSwap = true; e.detail.isError = false; }
  });
  // 앱까지 오지 못한 거절: 앞단 본문 상한(413)·동시 업로드 상한(429). 자기 안내가 있는 화면은 stopPropagation으로 여기 오지 않는다.
  document.addEventListener('htmx:responseError', function (e) {
    var st = e.detail.xhr.status;
    if (st === 413) toast('파일이 너무 커요. 크기를 줄여 다시 올려 주세요.');
    else if (st === 429) toast('지금 올리는 사람이 많아요. 잠시 뒤 다시 올려 주세요.');
  });
  // 보내기 전 크기 검사: <input type="file" data-max-mb="n">의 파일 합이 n MB를 넘으면 보내지 않는다. 일반 폼은 앞단 거절이 브라우저 기본 화면으로 뜨기 때문이다.
  // 캡처 단계에서 막아 htmx 폼도 보내지 않는다.
  document.addEventListener('submit', function (e) {
    var over = [].find.call(e.target.querySelectorAll('input[type=file][data-max-mb]'), function (i) {
      return [].reduce.call(i.files, function (n, f) { return n + f.size; }, 0) > i.dataset.maxMb * 1048576;
    });
    if (!over) return;
    e.preventDefault();
    e.stopPropagation();
    toast('파일이 너무 커요. ' + over.dataset.maxMb + 'MB까지 올릴 수 있어요. 줄여서 다시 올려 주세요.');
  }, true);
  // 일반 폼(data-upload-form)을 fetch로 보낸다. 앞단의 413·429는 앱 화면이 아닌 글자라 일반 POST면 화면 전체가 그 글자로 바뀌고 쓴 내용·고른 파일이 사라진다.
  // 413·429 글자·연결 실패는 폼을 그대로 두고 안내, 앱이 준 HTML(폼 오류, 리디렉트한 화면)은 화면을 교체. 스크립트가 없으면 일반 POST 그대로.
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches('[data-upload-form]') || e.defaultPrevented) return;
    e.preventDefault();
    if (form.dataset.sending) return;
    form.dataset.sending = '1';
    var multi = form.enctype === 'multipart/form-data', fd = new FormData(form), by = e.submitter;
    if (by && by.name) fd.append(by.name, by.value);
    var buttons = [].filter.call(form.elements, function (b) { return b.type === 'submit'; });  // form= 로 밖에 둔 단추까지
    buttons.forEach(function (b) { b.disabled = true; });
    function stay(text) {
      delete form.dataset.sending;
      buttons.forEach(function (b) { b.disabled = false; });
      toast(text, 6000);
    }
    fetch(form.getAttribute('action') || location.href, { method: 'POST', credentials: 'same-origin', body: multi ? fd : new URLSearchParams(fd) })
      .then(function (r) { return r.text().then(function (h) { return { r: r, h: h }; }); })
      .then(function (x) {
        var r = x.r, page = /text\/html/.test(r.headers.get('Content-Type') || '');
        if (r.status === 413) return stay(multi ? '파일이 너무 커요. 크기를 줄여 다시 올려 주세요.' : '내용이 너무 커요. 줄여서 다시 보내 주세요.');
        if (r.status === 429 && !page) return stay('지금 올리는 사람이 많아요. 잠시 뒤 다시 올려 주세요.');
        if (!page) return stay('처리하지 못했어요. 잠시 뒤 다시 해 주세요.');
        if (r.redirected) history.replaceState(null, '', r.url);  // 다시 받지 않고 이 응답을 그린다(한 번 띄울 저장 결과가 이 응답에 들어 있다)
        document.open(); document.write(x.h); document.close();
        window.scrollTo(0, 0);
      })
      .catch(function () { stay('인터넷 연결을 확인하고 다시 해 주세요.'); });
  });
  // 파일 칸: <input type="file" data-names="#id">에서 고른 파일 이름을 #id에 적는다. 안 골랐으면 처음 글자로 돌린다.
  document.addEventListener('change', function (e) {
    var i = e.target.closest('input[type=file][data-names]');
    if (!i) return;
    var out = document.querySelector(i.dataset.names);
    if (!out.dataset.none) out.dataset.none = out.textContent;
    out.textContent = Array.prototype.map.call(i.files, function (f) { return f.name; }).join(', ') || out.dataset.none;
  });
  // 확인 글자: <input data-must="글자">에 그대로 써야 같은 폼의 제출 단추가 켜진다(동학년 나가기). 서버도 다시 확인한다.
  document.addEventListener('input', function (e) {
    var i = e.target.closest('[data-must]');
    if (!i) return;
    var b = i.form && i.form.querySelector('[type=submit]');
    if (b) b.disabled = i.value.trim() !== i.dataset.must;
  });
  document.addEventListener('htmx:beforeRequest', function (e) {
    var el = e.detail.elt;
    if (/^every /.test(el.getAttribute('hx-trigger') || '') && el.querySelector('.scrim:not([hidden])')) e.preventDefault();
  });
  function toastNow() {  // 저장 결과 한 줄(마감하고 잠그기·다시 열기). 화면을 옮긴 뒤 처음 그릴 때도 띄운다.
    var t = document.querySelector('[data-toast-now]');
    if (t) { toast(t.dataset.toastNow); t.removeAttribute('data-toast-now'); }
  }
  toastNow();
  // 서버가 대화상자를 연 채 그렸으면(입력 오류, /board/new) 오류 칸이나 첫 칸에 포커스.
  var openDlg = document.querySelector('.scrim:not([hidden])');
  if (openDlg) { var firstIn = openDlg.querySelector('[aria-invalid=true],input:not([type=hidden]),button'); if (firstIn) firstIn.focus(); }
  document.addEventListener('htmx:afterSwap', function () {
    toastNow();
    var b = document.querySelector('[data-autocopy]');
    if (!b) return;
    var msg = b.dataset.autocopy, src = document.querySelector(b.dataset.copy);
    b.removeAttribute('data-autocopy');
    var fail = function () { toast(msg.replace(/안내 문구를 복사했어요\./, '「' + b.textContent + '」를 눌러 쿨메신저에 붙여넣으세요.')); };
    if (src && navigator.clipboard) navigator.clipboard.writeText(src.textContent).then(function () { toast(msg); }, fail);
    else fail();
  });

  // 이 PC에서 알림 받기(G2). 🔔 목록이 열리거나 설정 > 알림을 열면 이 브라우저 구독 상태로 단추를 맞춘다. 권한 요청은 단추를 누를 때만.
  // 상자: .pushbox[data-vapid·data-on·data-off·data-mine] 안의 [data-push-btn]·[data-push-msg]. 한 화면에 둘(🔔·설정)일 수 있다.
  var SW = '/static/sw.js';
  function pushOk() { return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window; }
  function csrf() { var i = document.querySelector('input[name=csrfmiddlewaretoken]'); return i ? i.value : ''; }
  function vapidKey(s) {
    var raw = atob((s + '='.repeat((4 - s.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: JSON.stringify(body),
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); });
  }
  function mySub() {
    return navigator.serviceWorker.getRegistration(SW).then(function (reg) { return reg ? reg.pushManager.getSubscription() : null; });
  }
  function activated(reg) {  // 서비스 워커 범위(/static/) 밖 화면이라 serviceWorker.ready 대신 직접 기다린다
    var w = reg.installing || reg.waiting;
    if (reg.active || !w) return Promise.resolve(reg);
    return new Promise(function (ok) { w.addEventListener('statechange', function () { if (w.state === 'activated') ok(reg); }); });
  }
  function pushSync() { document.querySelectorAll('.pushbox').forEach(syncBox); }
  function syncBox(box) {
    var btn = box.querySelector('[data-push-btn]'), msg = box.querySelector('[data-push-msg]');
    if (!pushOk()) { btn.hidden = true; msg.textContent = '이 브라우저는 알림을 받을 수 없어요.'; return; }
    if (Notification.permission === 'denied') { btn.disabled = true; msg.textContent = '브라우저 설정에서 고티처 알림이 막혀 있어요.'; return; }
    mySub().then(function (sub) {
      if (!sub || Notification.permission !== 'granted') return false;
      // 이 브라우저 구독이 지금 회원 것인지 서버에 묻는다. 앞사람 것이면 '꺼짐'으로 보이고, 켜면 지금 회원으로 옮긴다.
      return fetch(box.dataset.mine, { method: 'POST', credentials: 'same-origin', body: JSON.stringify({ endpoint: sub.endpoint }),
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() } })
        .then(function (r) { return r.ok ? r.json() : { mine: false }; }).then(function (d) { return !!d.mine; });
    }).catch(function () { return false; }).then(function (on) {
      btn.dataset.state = on ? 'on' : 'off';
      btn.textContent = on ? '이 PC에서 알림 끄기' : '이 PC에서 알림 받기';
      msg.textContent = on ? '이 PC에서 받고 있어요.' : '';
      btn.disabled = false;
    });
  }
  // 로그아웃: 이 PC 브라우저 구독을 서버에서 지우고 브라우저에서도 끊은 뒤 나간다(공용 PC에 앞사람 알림이 뜨지 않게).
  // 서버도 로그아웃 때 이 PC 기기 토큰에 묶인 구독을 지운다. 어떤 경우에도 2초 안에는 로그아웃한다.
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (f.id !== 'logoutForm' || !pushOk()) return;
    e.preventDefault();
    var done = false, go = function () { if (!done) { done = true; f.submit(); } }, t = setTimeout(go, 2000);
    mySub().then(function (sub) {
      if (!sub) return;
      return post(f.dataset.pushOff, { endpoint: sub.endpoint }).catch(function () {}).then(function () { return sub.unsubscribe(); });
    }).catch(function () {}).then(function () { clearTimeout(t); go(); });
  });
  function pushToggle(btn) {
    var box = btn.closest('.pushbox'), msg = box.querySelector('[data-push-msg]'), on = btn.dataset.state !== 'on';
    btn.disabled = true;
    var job = on
      ? Notification.requestPermission().then(function (p) {
          if (p !== 'granted') throw new Error('perm');
          return navigator.serviceWorker.register(SW).then(activated);
        }).then(function (reg) {
          return reg.pushManager.getSubscription().then(function (s) {
            return s || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKey(box.dataset.vapid) });
          });
        }).then(function (sub) { return post(box.dataset.on, sub.toJSON()); })
      : mySub().then(function (sub) {
          if (sub) return post(box.dataset.off, { endpoint: sub.endpoint }).then(function () { return sub.unsubscribe(); });
        });
    job.then(function () { toast(on ? '이 PC에서 묶음 알림을 받아요.' : '이 PC에서 알림을 껐어요.'); pushSync(); },
      function (err) {
        pushSync();
        msg.textContent = err.message === 'perm' ? '알림을 허용해야 받을 수 있어요.' : '바꾸지 못했어요. 잠시 뒤 다시 눌러 주세요.';
      });
  }
  document.addEventListener('htmx:afterSwap', function (e) { if (e.target.id === 'bellPop') pushSync(); });
  if (document.querySelector('.pushbox')) pushSync();  // 설정 > 알림(defer라 문서는 이미 읽혔다)

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
    var pb = e.target.closest('[data-push-btn]');
    if (pb) { pushToggle(pb); return; }
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

    // 비밀 링크 복사: <button data-link-copy="POST 주소" data-dlg="#대화상자" data-done="알림">. 누를 때만 서버에서 받는다(화면에 원문을 두지 않고, 서버가 누가 받았는지 남긴다).
    // 응답을 기다리면 사용자 동작이 끊기는 브라우저가 있어 누른 순간 ClipboardItem을 만들고 값은 Promise로 채운다.
    // 클립보드가 안 되면 원문을 알림으로 띄우지 않고, 대화상자 안 읽기 전용 칸에 선택해 두어 직접 복사하게 한다(닫으면 지운다).
    var lc = e.target.closest('[data-link-copy]');
    if (lc) {
      var link = fetch(lc.dataset.linkCopy, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); });
      var showLink = function () {
        link.then(function (t) {
          var dlg = document.querySelector(lc.dataset.dlg), out = dlg.querySelector('[data-link-out]');
          out.value = t; opener = lc; dlg.hidden = false; out.focus(); out.select();
        }, function () { toast('링크를 받지 못했어요. 화면을 새로 고친 뒤 다시 눌러 주세요.'); });
      };
      if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
        navigator.clipboard.write([new ClipboardItem({ 'text/plain': link.then(function (t) { return new Blob([t], { type: 'text/plain' }); }) })])
          .then(function () { toast(lc.dataset.done); }, showLink);
      } else showLink();
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
    var clear = group.dataset.clear && document.querySelector(group.dataset.clear);  // 목록에서 고르면 직접 쓴 칸을 비운다
    if (clear) clear.value = '';
    if (group.getAttribute('aria-invalid') === 'true') {  // 고르면 칸 옆 오류를 지운다
      var err = document.getElementById(group.getAttribute('aria-describedby'));
      if (err) err.remove();
      group.removeAttribute('aria-invalid');
      group.removeAttribute('aria-describedby');
    }
    if (b.dataset.toast) toast(b.dataset.toast);
  });
})();
