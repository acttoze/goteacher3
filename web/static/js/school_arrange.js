// Q2 학교 화면 순서·소속 바꾸기(관리자). 끌어서 놓기(기본 HTML5) 또는 ↑·↓·옮기기 단추. 놓는 순간 저장하고, 서버가 본문을 통째로 다시 그린다.
// [data-arr-box]: 순서를 가진 칸(groups·depts = 카드들, members·duties = 카드 안 줄, data-arr-in = 소속·부서). 그 바로 아래 [data-arr-key]가 하나씩.
// 실패: 400은 서버가 안내와 함께 원래 상태를 그리고(gt.js data-err-swap), 그 밖(403·500·연결 끊김)은 본문을 다시 받아 원래 자리로 돌린다.
(function () {
  var dragged = null, origin = null, pending = null, refocus = null;

  function main() { return document.getElementById('schoolMain'); }
  function items(box) { return [].filter.call(box.children, function (x) { return x.hasAttribute('data-arr-key'); }); }
  function keys(box) { return items(box).map(function (x) { return x.dataset.arrKey; }); }
  function boxOf(el) { return el.parentElement; }
  function csrf() { var i = document.querySelector('#moveDlg input[name=csrfmiddlewaretoken]'); return i ? i.value : ''; }

  function save(box, extra) {
    var m = main(), vals = { what: box.dataset.arrBox, group: box.dataset.arrIn || '', keys: JSON.stringify(keys(box)) };
    for (var k in extra || {}) vals[k] = extra[k];
    htmx.ajax('POST', m.dataset.arrUrl, { source: m, target: '#schoolMain', swap: 'outerHTML', values: vals, headers: { 'X-CSRFToken': csrf() } });
  }
  function revert() {
    var m = main();
    if (m && m.dataset.arrBack) htmx.ajax('GET', m.dataset.arrBack, { target: '#schoolMain', swap: 'outerHTML' });
  }
  document.addEventListener('htmx:responseError', function (e) {
    if (e.detail.target && e.detail.target.id === 'schoolMain' && e.detail.xhr.status !== 400) revert();
  });
  document.addEventListener('htmx:sendError', function (e) {
    if (e.detail.target && e.detail.target.id === 'schoolMain') revert();
  });
  // 단추로 옮긴 뒤 같은 단추에 포커스를 돌려준다(키보드로 이어서 옮기게).
  document.addEventListener('htmx:afterSwap', function () {
    if (!refocus) return;
    var el = [].find.call(document.querySelectorAll('[data-arr-key]'), function (x) { return x.dataset.arrKey === refocus.key && boxOf(x).dataset.arrBox === refocus.what; });
    var b = el && el.querySelector('[data-arr-act="' + refocus.act + '"]');
    if (b) b.focus();
    refocus = null;
  });

  // 소속(학년이면 반)·부서를 고르는 창. 끌어 놓은 곳이 학년 카드이면 반을 고른 뒤 저장한다.
  var dlg = function () { return document.getElementById('moveDlg'); };
  function showCls() {
    var d = dlg(), to = d.querySelector('#moveTo').value;
    d.querySelectorAll('[data-arr-cls]').forEach(function (l) { l.hidden = l.dataset.arrCls !== to; });
  }
  function openMove(el, box, before) {
    var d = dlg(), sel = d.querySelector('#moveTo');
    pending = { el: el, box: box, before: before };
    d.querySelector('#moveWho').textContent = el.querySelector('[data-arr-act=move]').dataset.name + (box ? '' : ' — 어디로 옮길까요?');
    sel.disabled = !!box;
    sel.value = box ? box.dataset.arrIn : boxOf(el).dataset.arrIn;
    showCls();
    d.hidden = false;
    (box ? d.querySelector('[data-arr-cls]:not([hidden]) select') || d.querySelector('[data-arr-go]') : sel).focus();
  }
  document.addEventListener('change', function (e) { if (e.target.id === 'moveTo') showCls(); });
  document.addEventListener('click', function (e) {
    var go = e.target.closest('[data-arr-go]');
    if (go && pending) {
      var d = dlg(), to = d.querySelector('#moveTo').value;
      var box = pending.box || document.querySelector('[data-arr-box="' + boxOf(pending.el).dataset.arrBox + '"][data-arr-in="' + CSS.escape(to) + '"]');
      var cls = d.querySelector('[data-arr-cls]:not([hidden]) select');
      d.hidden = true;
      if (box && box !== boxOf(pending.el) || pending.box) {
        box.insertBefore(pending.el, pending.before && pending.before.parentNode === box ? pending.before : null);
        save(box, cls ? { cls: cls.value } : {});
      }
      pending = null;
      return;
    }
    var b = e.target.closest('[data-arr-act]');
    if (!b) return;
    var el = b.closest('[data-arr-key]'), box = boxOf(el), act = b.dataset.arrAct;
    if (act === 'move') { openMove(el, null, null); return; }
    var sib = act === 'up' ? el.previousElementSibling : el.nextElementSibling;
    if (!sib || !sib.hasAttribute('data-arr-key')) return;
    box.insertBefore(el, act === 'up' ? sib : sib.nextElementSibling);
    refocus = { key: el.dataset.arrKey, what: box.dataset.arrBox, act: act };
    save(box);
  });

  // 끌어서 놓기. 끄는 동안 자리를 미리 보여 주고, 놓으면 저장. 칸 밖에 놓거나 Esc면 원래 자리로.
  function targetBox(e) {
    var kind = boxOf(dragged).dataset.arrBox;
    if (kind === 'groups' || kind === 'depts') return e.target.closest('[data-arr-box="' + kind + '"]');
    var card = e.target.closest('[data-arr-card]');
    return card && card.querySelector('[data-arr-box="' + kind + '"]');
  }
  document.addEventListener('dragstart', function (e) {
    var el = e.target.closest && e.target.closest('[data-arr-key][draggable]');
    if (!el || e.target !== el) return;
    dragged = el; origin = { box: boxOf(el), next: el.nextElementSibling, keys: keys(boxOf(el)) };
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', el.dataset.arrKey);
    setTimeout(function () { el.classList.add('dragging'); }, 0);
  });
  document.addEventListener('dragover', function (e) {
    if (!dragged) return;
    var box = targetBox(e);
    if (!box) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    var over = e.target.closest('[data-arr-key]');
    while (over && boxOf(over) !== box) over = boxOf(over).closest('[data-arr-key]');
    if (over === dragged) return;
    if (!over) { if (!items(box).length || boxOf(dragged) !== box) box.appendChild(dragged); return; }
    var r = over.getBoundingClientRect(), x = box.dataset.arrAxis === 'x';
    var after = x ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2;
    box.insertBefore(dragged, after ? over.nextElementSibling : over);
  });
  document.addEventListener('drop', function (e) {
    if (!dragged) return;
    var box = targetBox(e);
    if (!box) return;
    e.preventDefault();
    var el = dragged, from = origin;
    finish();
    if (box === from.box && keys(box).join('\n') === from.keys.join('\n')) return;
    var grade = box.dataset.arrBox === 'members' && box !== from.box && /^g\d$/.test(box.dataset.arrIn);
    if (grade) {  // 반을 고른 뒤에 저장: 그동안은 원래 자리에
      var before = el.nextElementSibling;
      from.box.insertBefore(el, from.next);
      openMove(el, box, before);
      return;
    }
    save(box);
  });
  document.addEventListener('dragend', function () {
    if (!dragged) return;
    origin.box.insertBefore(dragged, origin.next);  // 놓지 않고 끝남
    finish();
  });
  function finish() {
    if (dragged) dragged.classList.remove('dragging');
    dragged = null;
  }
})();
