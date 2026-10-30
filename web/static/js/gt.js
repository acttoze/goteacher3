// 고티처 공통 스크립트. 상태는 서버가 갖고 갱신은 htmx가 한다. 여기에는 화면 안 작은 동작만 둔다.
(function () {
  // 고르기 단추 묶음: <div class="picks" data-pick="이름"> 안의 .pick[data-v]를 누르면
  // 같은 폼의 <input type="hidden" name="이름">에 값을 넣고 aria-pressed를 바꾼다. htmx로 바뀐 폼에도 그대로 동작(위임).
  document.addEventListener('click', function (e) {
    var b = e.target.closest('.picks[data-pick] .pick[data-v]');
    if (!b) return;
    var group = b.closest('.picks'), form = b.closest('form');
    group.querySelectorAll('.pick').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    var input = form && form.querySelector('input[type=hidden][name="' + group.dataset.pick + '"]');
    if (input) input.value = b.dataset.v;
    if (group.getAttribute('aria-invalid') === 'true') {  // 고르면 칸 옆 오류를 지운다
      var err = document.getElementById(group.getAttribute('aria-describedby'));
      if (err) err.remove();
      group.removeAttribute('aria-invalid');
      group.removeAttribute('aria-describedby');
    }
  });
})();
