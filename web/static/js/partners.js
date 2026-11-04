// N1 협력업체: 뒤로·앞으로 가기(htmx가 목록을 되살린다) 뒤 찾기 칸·선택 칸을 주소의 값에 맞춘다.
document.addEventListener('htmx:historyRestore', function () {
  var q = document.getElementById('q'), cat = document.getElementById('cat'), area = document.getElementById('area'), sort = document.getElementById('sort');
  if (!q || !cat || !area || !sort) return;
  var p = new URLSearchParams(location.search);
  q.value = p.get('q') || '';
  cat.value = p.get('cat') || '';
  area.value = p.get('area') || '';
  sort.value = p.get('sort') || '';
});

// N2 후기 쓰기: 배지는 5개까지(서버도 검사한다).
document.addEventListener('change', function (e) {
  if (e.target.name !== 'badges' || !e.target.checked) return;
  var msg = document.getElementById('badgeMsg');
  var over = document.querySelectorAll('input[name=badges]:checked').length > 5;
  if (over) e.target.checked = false;
  if (msg) msg.hidden = !over;
});
