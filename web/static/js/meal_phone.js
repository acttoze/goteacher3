// 폰으로 식판 사진 올리기(M3). 찍으면 미리 보여 주고, 큰 사진은 올리기 전에 긴 변 2400px로 줄인다(업로드 상한 8MB). 실패하면 원본을 보낸다.
// 폼은 보통 POST다. 줄인 파일은 입력칸에 도로 넣으므로 스크립트가 없어도 같은 주소로 올라간다. 사진 검사는 서버가 한다.
(function () {
  var LONG = 2400, MAX = 8 * 1024 * 1024, QUALITY = 0.88;
  var form = document.getElementById('phForm'), file = document.getElementById('phFile'), prev = document.getElementById('phPrev');
  var shot = document.getElementById('phShot'), label = document.getElementById('phLabel'), hint = document.getElementById('phHint');
  var send = document.getElementById('phSend'), msg = document.getElementById('phMsg');
  var BUTTON = send.textContent, url = null, run = 0;
  // 사진을 캔버스에 그리면 EXIF가 사라진다. 그리면서 회전을 적용하지 않는 옛 브라우저는 줄이지 않고 원본을 보내 서버가 돌리게 한다
  var ROTATES = 'imageOrientation' in document.documentElement.style;

  function setBusy(on, text) { send.disabled = on; send.textContent = text || BUTTON; }

  function empty() {
    prev.hidden = true; prev.removeAttribute('src');
    shot.classList.remove('has-pic');
    hint.textContent = '배식 직전 오늘 식판을 찍어 주세요';
    setBusy(true);
  }

  function shrink(img, mine, done) {
    var k = Math.min(1, LONG / Math.max(img.naturalWidth, img.naturalHeight));
    var cv = document.createElement('canvas'), g = cv.getContext('2d');
    cv.width = Math.round(img.naturalWidth * k); cv.height = Math.round(img.naturalHeight * k);
    g.fillStyle = '#fff'; g.fillRect(0, 0, cv.width, cv.height);  // PNG 투명 칸이 JPEG에서 검게 되지 않게
    g.drawImage(img, 0, 0, cv.width, cv.height);
    cv.toBlob(function (blob) {
      if (mine !== run) return;  // 그사이 다시 골랐다: 앞 사진으로 덮어쓰지 않는다
      try {
        if (blob) {
          var box = new DataTransfer();
          box.items.add(new File([blob], 'photo.jpg', { type: 'image/jpeg' }));
          file.files = box.files;
        }
      } catch (e) { /* 파일을 바꿔 넣지 못하는 브라우저: 원본을 보낸다 */ }
      done();
    }, 'image/jpeg', QUALITY);
  }

  file.addEventListener('change', function () {
    var f = file.files[0], mine = ++run;
    msg.hidden = true;
    if (url) { URL.revokeObjectURL(url); url = null; }
    if (!f) { empty(); return; }
    setBusy(true, '사진 준비 중…');
    function ready() {
      if (mine !== run) return;
      var tooBig = file.files[0] && file.files[0].size > MAX;  // 줄이지 못한 큰 원본: 보내도 서버가 거부하므로 여기서 알린다
      if (tooBig) { msg.textContent = '사진이 너무 커요(8MB까지). 폰 카메라 화질을 낮춰 다시 찍어 주세요.'; msg.hidden = false; }
      setBusy(tooBig);
    }
    url = URL.createObjectURL(f);
    prev.onload = function () {
      prev.onload = null;
      shot.classList.add('has-pic');
      prev.hidden = false;
      hint.textContent = '다시 찍으려면 사진을 누르세요';
      var big = Math.max(prev.naturalWidth, prev.naturalHeight) > LONG || f.size > MAX;
      var odd = !/^image\/(jpeg|png)$/.test(f.type);  // 사파리는 HEIC를 그릴 수 있다: 그렸으면 JPEG로 바꿔 보낸다
      if (!ROTATES || !(big || odd)) { ready(); return; }
      try { shrink(prev, mine, ready); } catch (e) { ready(); }
    };
    prev.onerror = function () {  // 이 브라우저가 못 그리는 형식: 원본을 보내고 받을지는 서버가 정한다(HEIC면 안내가 돌아온다)
      prev.onerror = null;
      shot.classList.remove('has-pic');
      prev.hidden = true;
      label.textContent = '사진을 골랐어요';
      hint.textContent = '미리 보기는 못 보여 드려요. 올리기를 눌러 주세요';
      ready();
    };
    prev.src = url;
  });

  form.addEventListener('submit', function (e) {
    if (send.disabled) { e.preventDefault(); return; }
    setTimeout(function () { setBusy(true, '올리는 중…'); }, 0);
  });
  // 뒤로 가기로 돌아왔을 때 '올리는 중'에 멈춰 있지 않게
  window.addEventListener('pageshow', function (e) { if (e.persisted) setBusy(!file.files.length); });

  empty();
})();
