// 공지 고치기(I2): 일시·장소·대상·준비물이 처음 값과 달라지면 '실질 변경'을 켠다. 선생님이 직접 건드린 뒤에는 그대로 둔다.
// 확인 필수 공지는 항목·본문을 바꾸면 끌 수 없다(서버도 강제한다).
(function () {
  var box = document.getElementById('realChk');
  if (!box) return;
  var items = Array.prototype.slice.call(document.querySelectorAll('[data-orig]'));
  var body = document.querySelector('[data-orig-body]'), must = document.querySelector('input[name="must"]');
  var touched = false;
  function sync() {
    var itemChanged = items.some(function (x) { return x.value.trim() !== x.dataset.orig; });
    var bodyChanged = body.value.replace(/\r\n/g, '\n').trim() !== body.dataset.origBody;
    var force = must.checked && (itemChanged || bodyChanged);
    box.disabled = force;
    if (force) box.checked = true;
    else if (!touched) box.checked = itemChanged;
  }
  box.addEventListener('change', function () { touched = true; });
  must.addEventListener('change', sync);
  body.addEventListener('input', sync);
  items.forEach(function (el) { el.addEventListener('input', sync); });
})();
