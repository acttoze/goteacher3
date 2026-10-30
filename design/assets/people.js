// 사람마다 고정 색. 동학년 다섯 명은 색상환에서 이웃끼리 90도 이상 떨어지게 골랐다(보라·남색 제외).
// 색은 커서·이름표·아바타에만 쓴다. inkText = 밝은 색이라 글자를 검게.
window.PEOPLE = (function () {
  var map = {
    '송민재': { color: '#c2410c' },               // 주황
    '정하은': { color: '#0b7d7f' },               // 청록
    '김도윤': { color: '#d6246e' },               // 분홍
    '이수빈': { color: '#37b24d', inkText: true }, // 초록
    '박서준': { color: '#f2a300', inkText: true }  // 호박
  };
  return {
    grade: ['송민재', '정하은', '김도윤', '이수빈', '박서준'],
    of: function (name) { return map[name] || { color: '#6b7280' }; }
  };
})();
