// N1 협력업체: 뒤로·앞으로 가기(htmx가 목록을 되살린다) 뒤 찾기 칸·선택 칸을 주소의 값에 맞춘다.
document.addEventListener('htmx:historyRestore', function () {
  var q = document.getElementById('q'), cat = document.getElementById('cat'), area = document.getElementById('area');
  if (!q || !cat || !area) return;
  var p = new URLSearchParams(location.search);
  q.value = p.get('q') || '';
  cat.value = p.get('cat') || '';
  area.value = p.get('area') || '';
});
