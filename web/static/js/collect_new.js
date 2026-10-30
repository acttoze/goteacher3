// 새로 모으기 — 표 붙여넣기(C2). 표 읽기·분류는 서버(collect/paste.py)가 하고, 여기서는 클립보드만 넘긴다.
// 한글에서 표를 복사하면 글자(text/plain)에는 칸 구분이 없고 표는 HTML에만 있다(research/05: 649줄×1칸).
// 그래서 HTML에 표가 있으면 그 HTML을 미리보기 요청에 한 번 실어 보내고, 서버가 돌려준 TSV로 입력 칸을 채운다.
(function () {
  var ta = document.getElementById('paste'), html = document.getElementById('pasteHtml'), how = document.getElementById('piiHow');
  if (!ta || !html) return;
  var SAMPLE = '반\t담임\t참가(명)\t불참(명)\t비고\n4-1\t\t\t\t\n4-2\t\t\t\t\n4-3\t\t\t\t\n4-4\t\t\t\t\n4-5\t\t\t\t\n합계\t\t\t\t';

  ta.addEventListener('paste', function (e) {
    var h = e.clipboardData && e.clipboardData.getData('text/html');
    if (!h || !/<table[\s>]/i.test(h)) return;  // 표가 없으면 글자(엑셀·시트의 탭 글자) 그대로
    e.preventDefault();
    html.value = h;
    how.value = '';
    htmx.trigger(ta, 'gt-pasted');
  });
  // C7 학생 정보 열: 새로 붙이거나 고치면 고른 정리(인원 수로·빼기)를 지운다(design resolved)
  ta.addEventListener('input', function () { how.value = ''; });
  document.getElementById('pasteOut').addEventListener('click', function (e) {
    var b = e.target.closest('[data-pii]');
    if (!b) return;
    how.value = b.dataset.pii;
    htmx.trigger(ta, 'gt-pasted');
  });
  document.body.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail.elt === ta) html.value = '';  // HTML은 한 번만 보낸다
  });
  document.body.addEventListener('htmx:afterSwap', function () {
    var out = document.getElementById('tsvOut');
    if (out) { ta.value = out.value; out.remove(); }
  });
  document.getElementById('sample').addEventListener('click', function () {
    ta.value = SAMPLE;
    how.value = '';
    var t = document.getElementById('title');
    if (!t.value.trim()) { t.value = '학예회 반별 참가 인원'; t.dispatchEvent(new Event('input')); }
    htmx.trigger(ta, 'gt-pasted');
  });
  // 쪽지 미리보기의 제목
  document.getElementById('title').addEventListener('input', function () {
    var v = this.value.trim() || '(제목)';
    document.querySelectorAll('[data-title]').forEach(function (x) { x.textContent = v; });
  });
})();

// C3: 가져오는 방식 탭(표 붙여넣기·직접 입력·자주 쓰는 양식). 고른 방식은 숨은 칸 src로 서버에 간다.
(function () {
  var src = document.getElementById('src');
  if (!src) return;
  function show(v) {
    src.value = v;
    document.querySelectorAll('button[data-src]').forEach(function (b) { if (!b.disabled) b.setAttribute('aria-pressed', String(b.dataset.src === v)); });
    document.querySelectorAll('[data-pane]').forEach(function (p) { p.hidden = p.dataset.pane.split(' ').indexOf(v) < 0; });
  }
  document.querySelectorAll('button[data-src]').forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.src); }); });
  var add = document.getElementById('addCol');
  if (add) add.addEventListener('click', function () {
    var next = document.querySelector('tr[data-col][hidden]');
    if (!next) { document.getElementById('addColMsg').textContent = '직접 입력은 칸 6개까지예요. 더 많으면 표를 붙여넣어 주세요.'; return; }
    next.hidden = false;
    next.querySelector('input').focus();
  });
})();

// E5 한글 양식: 파일을 올리면 고르기 부분 화면(hwpfiles/_pick.html)이 #hwpBox에 들어온다.
// 읽는 동안은 그 화면이 스스로 2초마다 바뀌고, 다 읽으면 web/static/hwp/preview.js가 쪽을 그리고 표 고르기를 붙인다.
(function () {
  var drop = document.getElementById('hwpDrop'), file = document.getElementById('hwpFile');
  if (!drop || !file) return;
  var box = document.getElementById('hwpBox'), msg = document.getElementById('hwpUpMsg'), key = document.getElementById('hwpKey');
  var busy = false;
  function csrf() { var x = document.querySelector('[name=csrfmiddlewaretoken]'); return x ? x.value : ''; }
  function say(text) {
    msg.textContent = '';
    if (!text) return;
    var p = document.createElement('p');
    p.className = 'err';
    p.setAttribute('role', 'alert');
    p.textContent = text;
    msg.appendChild(p);
  }
  function send(f) {
    if (!f || busy) return;
    busy = true;
    say('');
    msg.innerHTML = '<p class="hwp-status">올리는 중이에요…</p>';
    var fd = new FormData();
    fd.append('file', f);
    fetch(drop.dataset.upload, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } })
      .then(function (r) { return r.text().then(function (h) { return { status: r.status, h: h }; }); })
      .then(function (r) {
        if (r.status === 400 || r.status === 429) { say(new DOMParser().parseFromString(r.h, 'text/html').body.textContent.trim()); return; }  // 거부 문구(hwpfiles/_reject.html)는 글자로만
        if (r.status !== 200) { say('올리지 못했어요. 화면을 새로 고친 뒤 다시 올려 주세요.'); return; }
        msg.textContent = '';
        box.innerHTML = r.h;
        drop.hidden = true;
        var p = document.getElementById('hwpPick');
        key.value = p ? p.dataset.key : '';
        htmx.process(box);
        document.dispatchEvent(new Event('gt-hwp-mount'));
        var t = document.getElementById('title');
        if (!t.value.trim()) { t.value = f.name.replace(/\.(hwpx?)$/i, '').slice(0, 200); t.dispatchEvent(new Event('input')); }
      })
      .catch(function () { say('올리지 못했어요. 인터넷 연결을 확인하고 다시 올려 주세요.'); })
      .then(function () { busy = false; file.value = ''; });
  }
  file.addEventListener('change', function () { send(file.files[0]); });
  drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', function () { drop.classList.remove('over'); });
  drop.addEventListener('drop', function (e) {
    e.preventDefault();
    drop.classList.remove('over');
    send(e.dataTransfer && e.dataTransfer.files[0]);
  });
  box.addEventListener('click', function (e) {
    if (!e.target.closest('[data-hwp-reset]')) return;
    box.textContent = '';
    key.value = '';
    say('');
    drop.hidden = false;
    file.focus();
  });
})();
