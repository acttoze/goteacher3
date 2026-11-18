// W7 명단으로 한 번에 등록. 붙여넣은 표를 칸 격자로 읽고, 전화·생년월일·주민번호·메일로 보이는 열은 이 PC에서 지운 뒤
// 남은 칸만 TSV로 서버에 보낸다(원문은 보내지 않는다). 소속·반 해석은 서버가 한다. 쪽지 화면에서는 인쇄 전 떠나기를 막는다.
(function () {
  var HEAD = /(전화|연락처|휴대|핸드폰|폰번호|tel|phone|mobile|생년|생일|출생|주민|등록번호|메일|e-?mail|주소)/i;
  var VALUE = [
    /^0\d{1,2}[-.)\s]?\d{3,4}[-.\s]?\d{4}$/,  // 전화
    /^\d{6}[-\s]?[1-8][\d*]{6}$/,  // 주민등록번호(뒷자리를 가린 것 포함)
    /\S+@\S+\.\S+/,  // 메일
    /^(19|20)?\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?\.?$/,  // 생년월일
    /^(19|20)\d{6}$/,  // 19800304
    /^\d{6}$/  // 800304
  ];

  function sensitive(v) {
    v = String(v || '').trim();
    return !!v && VALUE.some(function (re) { return re.test(v); });
  }

  function fromText(text) {
    return String(text || '').split(/\r\n|\r|\n/).map(function (line) {
      return line.split('\t').map(function (v) { return v.trim(); });
    }).filter(function (r) { return r.some(function (v) { return v; }); });
  }

  // 한글·엑셀 표: 아래로 합친 칸(rowspan)은 같은 글자로 채우고(소속 칸), 옆으로 합친 칸은 첫 칸에만 둔다.
  function fromHtml(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html'), best = null, size = -1;
    [].forEach.call(doc.querySelectorAll('table'), function (t) {
      var n = t.querySelectorAll('td,th').length;
      if (n > size) { best = t; size = n; }
    });
    if (!best) return null;
    var grid = [], rows = best.rows;
    [].forEach.call(rows, function (tr, r) {
      grid[r] = grid[r] || [];
      var c = 0;
      [].forEach.call(tr.cells, function (td) {
        while (grid[r][c] !== undefined) c++;
        var text = td.textContent.replace(/\s+/g, ' ').trim();
        var rs = Math.min(+td.getAttribute('rowspan') || 1, rows.length - r), cs = Math.min(+td.getAttribute('colspan') || 1, 60);
        for (var dr = 0; dr < rs; dr++) {
          var row = grid[r + dr] = grid[r + dr] || [];
          for (var dc = 0; dc < cs; dc++) row[c + dc] = dc ? '' : text;
        }
        c += cs;
      });
    });
    return grid.map(function (r) {
      var out = [];
      for (var i = 0; i < r.length; i++) out.push(r[i] || '');
      return out;
    }).filter(function (r) { return r.some(function (v) { return v; }); });
  }

  // 민감 열: 위 다섯 줄 안의 머리글이 그렇게 보이거나, 값 절반 넘게 그렇게 보이면 열째로 지운다. 남은 칸의 그런 값은 칸만 비운다.
  function clean(grid) {
    var width = grid.reduce(function (w, r) { return Math.max(w, r.length); }, 0), drop = {}, dropped = [];
    for (var c = 0; c < width; c++) {
      var head = '';
      grid.slice(0, 5).forEach(function (r) {
        var v = String(r[c] || '').replace(/\s+/g, '');
        if (!head && v.length <= 12 && HEAD.test(v)) head = r[c];
      });
      var vals = grid.map(function (r) { return r[c]; }).filter(function (v) { return v && String(v).trim(); });
      var hits = vals.filter(sensitive).length;
      if (head || (hits && hits * 2 > vals.length - 1)) {  // 머리글 한 칸을 빼고 절반이 넘으면 그런 열
        drop[c] = true;
        dropped.push(head || (c + 1) + '번째 칸');
      }
    }
    return {
      dropped: dropped,
      rows: grid.map(function (r) {
        return r.filter(function (v, i) { return !drop[i]; }).map(function (v) { return sensitive(v) ? '' : v; });
      })
    };
  }

  function toTsv(rows) {
    return rows.map(function (r) { return r.map(function (v) { return String(v).replace(/[\t\r\n]+/g, ' '); }).join('\t'); }).join('\n');
  }

  function init() {
    var ta = document.getElementById('bulkPaste'), note = document.getElementById('bulkDropped');
    function tell(dropped) {
      if (!note) return;
      note.hidden = !dropped.length;
      note.textContent = dropped.length ? '전화번호·생년월일·주민번호·메일로 보이는 칸 ' + dropped.length + '개(' + dropped.join(', ') + ')는 이 PC에서 지우고 보냈어요.' : '';
    }
    if (ta) {
      ta.addEventListener('paste', function (e) {
        var cd = e.clipboardData;
        if (!cd) return;
        var h = cd.getData('text/html'), grid = h && /<table[\s>]/i.test(h) ? fromHtml(h) : null;
        e.preventDefault();
        var out = clean(grid || fromText(cd.getData('text/plain')));
        ta.value = toTsv(out.rows);
        tell(out.dropped);
        htmx.trigger(ta, 'gt-pasted');
      });
      // 직접 쓰거나 고친 글자도 보내기 직전에 한 번 더 지운다
      document.body.addEventListener('htmx:configRequest', function (e) {
        if (e.detail.elt !== ta) return;
        var out = clean(fromText(ta.value));
        e.detail.parameters.paste = toTsv(out.rows);
        if (out.dropped.length) tell(out.dropped);
      });
    }
    document.addEventListener('submit', function (e) {  // 두 번 누르면 첫 응답(코드)을 브라우저가 버린다
      if (e.target.id !== 'bulkForm') return;
      var b = e.target.querySelector('button[type=submit]');
      setTimeout(function () { b.disabled = true; b.textContent = '만드는 중이에요…'; }, 0);
    });
    var printBtn = document.getElementById('slipPrint');
    if (printBtn) {
      var printed = false;
      printBtn.addEventListener('click', function () { window.print(); });
      window.addEventListener('afterprint', function () { printed = true; });
      window.addEventListener('pageshow', function (e) {  // 뒤로 가기 캐시로 다시 열리면 코드를 지우고 학교 화면으로
        if (!e.persisted) return;
        printed = true;
        document.querySelectorAll('.sheet').forEach(function (el) { el.remove(); });
        location.replace(document.getElementById('bulkDone').dataset.back);
      });
      window.addEventListener('beforeunload', function (e) {
        if (printed) return;
        e.preventDefault();
        e.returnValue = '';
      });
    }
  }

  var api = { clean: clean, fromText: fromText, fromHtml: fromHtml, toTsv: toTsv };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else init();
})();
