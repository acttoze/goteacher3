// 새 취합 — 표 붙여넣기(C2). 표 읽기·분류는 서버(collect/paste.py)가 하고, 여기서는 클립보드만 넘긴다.
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
  // 일반 본문 상한을 넘은 붙여넣기: 칸이 말없이 비어 보이지 않게 안내한다.
  document.body.addEventListener('htmx:responseError', function (e) {
    if (e.detail.elt !== ta || (e.detail.xhr.status !== 400 && e.detail.xhr.status !== 413)) return;
    e.stopPropagation();  // 공통 안내(gt.js) 대신 이 칸 아래에
    var p = document.createElement('p');
    p.className = 'msg-inline'; p.setAttribute('role', 'alert');
    p.textContent = '표가 너무 커요. 500줄 안으로 나눠 붙여넣어 주세요.';
    document.getElementById('pasteOut').replaceChildren(p);
  });
  // 만들기 제출도 재 본다: 넘으면 보내지 않고 미리보기와 같은 안내를 띄운다(413은 gt.js의 data-upload-form 처리).
  var newForm = document.getElementById('newForm');
  newForm.addEventListener('submit', function (e) {
    if (new URLSearchParams(new FormData(newForm)).toString().length < +newForm.dataset.maxBody) return;
    e.preventDefault();
    var p = document.createElement('p');
    p.className = 'msg-inline'; p.setAttribute('role', 'alert');
    p.textContent = '표가 너무 커요. 500줄 안으로 나눠 붙여넣어 주세요.';
    document.getElementById('pasteOut').replaceChildren(p);
    p.scrollIntoView({ block: 'center' });
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
    var tooBig = '파일이 너무 커요. ' + file.dataset.maxMb + 'MB 이하 한글 파일만 올릴 수 있어요.';
    if (f.size > file.dataset.maxMb * 1048576) { say(tooBig); file.value = ''; return; }
    busy = true;
    say('');
    msg.innerHTML = '<p class="hwp-status">올리는 중이에요…</p>';
    var fd = new FormData();
    fd.append('file', f);
    fetch(drop.dataset.upload, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() } })
      .then(function (r) { return r.text().then(function (h) { return { status: r.status, h: h, page: /text\/html/.test(r.headers.get('Content-Type') || '') }; }); })
      .then(function (r) {
        if (r.status === 413) { say(tooBig); return; }  // 앞단이나 앱의 본문 상한
        if (r.status === 429 && !r.page) { say('지금 올리는 사람이 많아요. 잠시 뒤 다시 올려 주세요.'); return; }  // 앞단 동시 건수 상한(글자 응답)
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
  // E6 칸 확인: 표를 확정하면(preview.js 'gt-hwp-picked') 그 표의 칸 확인 부분 화면을 받아 온다.
  var fields = document.getElementById('hwpFields');
  document.addEventListener('gt-hwp-picked', function () {
    if (!fields || !key.value) return;
    var url = fields.dataset.url + encodeURIComponent(key.value);
    // 학교 공간: 고른 받는 사람으로 표 줄을 짝짓도록 폼 값을 함께 보낸다(칸 값은 hwp_sent가 없으니 자동 추정 그대로)
    if (document.getElementById('audBox')) htmx.ajax('POST', url, { source: '#newForm', target: '#hwpFields', swap: 'innerHTML' });
    else htmx.ajax('GET', url, { target: '#hwpFields', swap: 'innerHTML' });
  });
  box.addEventListener('click', function (e) {
    if (!e.target.closest('[data-hwp-reset]')) return;
    box.textContent = '';
    if (fields) fields.textContent = '';
    key.value = '';
    say('');
    drop.hidden = false;
    file.focus();
  });
})();

// H4 학교 공간 받는 사람: 고른 방식의 칸만 보이기, 그룹 묶음 단추, 사람 찾기. 요약 줄은 서버가 그린다(htmx, _aud_school.html).
(function () {
  var box = document.getElementById('audBox');
  if (!box) return;
  function panes() {
    var on = box.querySelector('input[name=aud]:checked');
    box.querySelectorAll('[data-aud]').forEach(function (p) { p.hidden = !on || p.dataset.aud !== on.value; });
  }
  function changed() { box.dispatchEvent(new Event('change', { bubbles: true })); }
  box.addEventListener('change', function (e) {
    if (e.target.name === 'aud') panes();
    var check = document.getElementById('hwpCheck');  // 한글 양식 '받는 줄'도 새 범위로
    if (check && e.target.id !== 'psq') htmx.trigger(check, 'change');
  });
  box.addEventListener('click', function (e) {
    var g = e.target.closest('[data-gall]');
    if (g) {
      box.querySelectorAll('input[name=aud_groups]').forEach(function (c) { c.checked = g.dataset.gall === 'grade' && /^g\d$/.test(c.value); });
      changed();
    } else if (e.target.closest('[data-pclear]')) {
      box.querySelectorAll('input[name=aud_people]').forEach(function (c) { c.checked = false; });
      changed();
    }
  });
  document.getElementById('psq').addEventListener('input', function () {
    var q = this.value.trim();
    box.querySelectorAll('.pck').forEach(function (l) { l.hidden = !!q && l.textContent.indexOf(q) < 0; });
    box.querySelectorAll('.pg').forEach(function (g) { g.hidden = !g.querySelector('.pck:not([hidden])'); });
  });
  document.getElementById('psq').addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
  panes();
})();
