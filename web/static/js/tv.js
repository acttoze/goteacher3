// 급식 TV(M2). 5분마다 /data를 받아 화면을 만들고 자동으로 돌린다. 받기에 실패하면 마지막 화면을 그대로 둔다.
// 학생이 보는 화면이라 교직원용 안내·오류 문구를 넣지 않는다. 오래된 크롬·웨일을 위해 ES5만 쓴다(빌드 도구 없음).
(function () {
  var POLL = 5 * 60 * 1000, RETRY = 60 * 1000, FIRST_RETRY = 15 * 1000;
  var stage = document.getElementById('stage'), pagesEl = document.getElementById('pages'), footEl = document.getElementById('foot');
  var BASE = stage.getAttribute('data-base');
  var last = null, view = null, lastRaw = '', stale = false, gone = 0, pollTimer = null, seenDay = '';
  var SLIDES = [], cur = 0, timer = null, paused = false, startedAt = 0, left = 0;

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function localDay() { var d = new Date(); return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()); }
  function chunks(list, size) { var out = []; for (var i = 0; i < list.length; i += size) out.push(list.slice(i, i + size)); return out; }
  function head(title, rest) { return '<div class="pg-h"><h1>' + esc(title) + '</h1>' + (rest || '') + '</div>'; }
  function nth(i, n) { return n > 1 ? '<span class="nth">' + (i + 1) + ' / ' + n + '</span>' : ''; }
  function names(list) { return list.map(function (x) { return x.name; }); }

  // ---------- 화면 조각 ----------
  // 내용을 잘라 내지 않는다(알레르기는 안전 정보): 칸에 안 들어갈 양이면 장을 나눈다.
  // 글자 폭을 글씨 크기 그대로(1em) 어림한다. 한글은 이보다 조금 좁아 넘치는 쪽으로 틀리지 않는다.
  var BODY = 765; // 장 본문 높이
  function linesOf(names, perLine) { // 메뉴 이름들을 ' · '로 이을 때 몇 줄인가
    var lines = 1, room = perLine;
    names.forEach(function (n, i) {
      var len = n.length + (i < names.length - 1 ? 2 : 0);
      if (len > room && room < perLine) { lines += 1; room = perLine; }
      while (len > perLine) { lines += 1; len -= perLine; }
      room -= len;
    });
    return lines;
  }
  function cardPages(items, textOf, tiers) { // 큰 칸이 필요한 만큼만 칸을 키우고, 한 장에 들어가는 만큼씩 나눈다
    var pages = [], i = 0;
    function fits(item, t) { return linesOf(textOf(item), t.perLine) <= t.lines; }
    while (i < items.length) {
      var tier = tiers.filter(function (t) { return fits(items[i], t); })[0] || tiers[tiers.length - 1], list = [items[i]];
      i += 1;
      while (i < items.length && list.length < tier.n && fits(items[i], tier)) { list.push(items[i]); i += 1; }
      pages.push({ cls: tier.cls, list: list });
    }
    return pages;
  }
  function menuSlides(meal) {
    var n = meal.dishes.length, base = n <= 5 ? 76 : n <= 6 ? 70 : n <= 7 ? 62 : n <= 8 ? 56 : 44, ROOM = 1280, MAX = 749, pages = [[]], used = 0;
    meal.dishes.forEach(function (d) {
      var selfW = d.self ? 110 : 0, numsW = d.allergy.length ? d.allergy.length * 66 + 16 : 0, len = Math.max(d.name.length, 1);
      var stack = numsW && len * 40 > ROOM - selfW - numsW; // 번호가 많아 이름이 쪼개질 자리면 번호를 아래 줄로
      var room = ROOM - selfW - (stack ? 0 : numsW), fs = Math.max(40, Math.min(base, Math.floor(room / len)));
      var h = Math.max(Math.ceil(len * fs / room) * fs * 1.15, numsW && !stack ? 60 : 0) + (stack ? 64 : 0) + 12;
      if (used + h > MAX) { pages.push([]); used = 0; }
      used += h;
      pages[pages.length - 1].push('<li' + (stack ? ' class="stack"' : '') + '><b style="font-size:' + fs + 'px">' + esc(d.name) + '</b>' +
        (d.self ? '<em class="self">자율</em>' : '') +
        (numsW ? '<span class="nums">' + d.allergy.map(function (a) { return '<span>' + a + '</span>'; }).join('') + '</span>' : '') + '</li>');
    });
    return pages.map(function (rows, i) {
      return head('오늘의 메뉴', '<p>' + esc(view.date_label) + ' 점심</p>' + nth(i, pages.length)) + '<div class="pg-b menu"><ul class="dishes">' +
        rows.join('') + '</ul><div class="facts">' +
        (meal.kcal ? '<div class="stat"><div class="l">칼로리</div><div class="v">' + meal.kcal + '<small>kcal</small></div></div>' : '') +
        '<div class="stat neg"><div class="l">알레르기 식품</div><div class="v">' + meal.allergens.length + '<small>가지</small></div></div></div></div>';
    });
  }
  function allergySlides(meal) {
    if (!meal.allergens.length) return [head('알레르기 정보') + '<div class="pg-b calm">오늘 식단표에 표시된 알레르기 식품이 없어요</div>'];
    var pages = cardPages(meal.allergens, function (x) { return x.dishes; }, [
      { cls: 'c3', n: 6, perLine: 11, lines: 4 }, { cls: 'c2', n: 4, perLine: 18, lines: 4 }, { cls: 'c1', n: 1, perLine: 39, lines: 11 }]);
    return pages.map(function (page, i) {
      return head('알레르기 정보', '<p>해당 학생은 배식 전에 꼭 말해 주세요</p>' + nth(i, pages.length)) + '<div class="pg-b cards alg ' + page.cls + '">' + page.list.map(function (x) {
        return '<div><div class="nm"><i>' + x.no + '</i>' + esc(x.name) + '</div><div class="in">' + esc(x.dishes.join(' · ')) + '</div></div>';
      }).join('') + '</div>';
    });
  }
  function calSlide(meal) {
    var r = 270, c = 2 * Math.PI * r, BASE_KCAL = 585, pct = Math.min(meal.kcal / BASE_KCAL, 1);
    return head('칼로리 · 영양', meal.kcal ? '<p>초등학생 한 끼 기준 ' + BASE_KCAL + ' kcal</p>' : '') + '<div class="pg-b cal">' +
      (meal.kcal ? '<div class="ring"><svg viewBox="0 0 600 600" aria-hidden="true"><circle class="ring-t" cx="300" cy="300" r="' + r + '" fill="none" stroke-width="44"/><circle class="fill" cx="300" cy="300" r="' + r + '" fill="none" stroke-width="44" stroke-linecap="round" stroke-dasharray="' + (c * pct).toFixed(1) + ' ' + c.toFixed(1) + '"/></svg><div class="c"><b>' + meal.kcal + '</b><span>kcal</span></div></div>' : '') +
      '<div class="nt">' + meal.nutr.map(function (x) {
        return '<div><div class="n">' + esc(x.name) + '</div><div class="v">' + esc(x.value) + '<small>' + esc(x.unit) + '</small></div></div>';
      }).join('') + '</div></div>';
  }
  function originSlides(meal) {
    var pages = chunks(meal.origin, 14); // 장당 14개
    return pages.map(function (list, i) {
      return head('원산지 정보', '<p>국내산이 아닌 재료는 <b class="org-im">주황색</b></p>' + nth(i, pages.length)) + '<div class="pg-b org">' + list.map(function (x) {
        return '<div><span class="p">' + esc(x[0]) + '</span><span class="o' + (/국내산/.test(x[1]) ? '' : ' im') + '">' + esc(x[1]) + '</span></div>';
      }).join('') + '</div>';
    });
  }
  function hygieneSlide(hy) {
    return head(hy.title, '<span class="tag">' + esc(hy.tag) + '</span>') + '<div class="pg-b wash">' + hy.items.map(function (w, i) {
      return '<div><div class="hd"><i>' + (i + 1) + '</i>' + esc(w[0]) + '</div><span>' + esc(w[1]) + '</span></div>';
    }).join('') + '</div>';
  }
  function eatSlides(meal, slogan) {
    var pages = cardPages(meal.groups, function (g) { return g.dishes; }, [
      { cls: 'c3', n: 6, perLine: 11, lines: 4 }, { cls: 'c2', n: 4, perLine: 18, lines: 4 }, { cls: 'c1', n: 1, perLine: 39, lines: 9 }]);
    return pages.map(function (page, i) {
      return head('골고루 먹어요', '<p>오늘 식판에는 어떤 식품이 있을까요?</p>' + nth(i, pages.length)) + '<div class="pg-b cards grp ' + page.cls + '">' + page.list.map(function (g) {
        return '<div' + (g.dishes.length ? ' class="has"' : '') + '><div class="gn">' + esc(g.name) + '</div><div class="gt">' + esc(g.dishes.length ? g.dishes.join(' · ') : '오늘은 없어요') + '</div></div>';
      }).join('') + '</div><div class="slogan">' + esc(slogan) + '</div>';
    });
  }
  function bigMenuSlides(meal) { // 사진이 없을 때 그 칸: 오늘 메뉴를 크게. 한 장에 안 들어가면 글씨를 줄이고, 40px로도 안 되면 나눈다
    var ROOM = 824, MAX = meal.kcal ? 655 : 733, names = meal.dishes.map(function (d) { return d.name; });
    function rowH(a, b, fs) { return Math.max(Math.ceil(a.length * fs / ROOM), b ? Math.ceil(b.length * fs / ROOM) : 1) * fs * 1.12 + 4; }
    function split(fs) {
      var pages = [[]], used = 0;
      for (var i = 0; i < names.length; i += 2) {
        var h = rowH(names[i], names[i + 1], fs);
        if (used + h > MAX) { pages.push([]); used = 0; }
        used += h;
        pages[pages.length - 1].push(names[i]);
        if (names[i + 1]) pages[pages.length - 1].push(names[i + 1]);
      }
      return pages;
    }
    var fs = 60, pages = split(60);
    if (pages.length > 1) { fs = 48; pages = split(48); }
    if (pages.length > 1) { fs = 40; pages = split(40); }
    return pages.map(function (list, i) {
      return head('오늘의 식사', '<p>' + esc(view.date_label) + ' 점심</p>' + nth(i, pages.length)) + '<div class="pg-b bigmenu" style="font-size:' + fs + 'px">' + list.map(function (n) {
        return '<div>' + esc(n) + '</div>';
      }).join('') + '</div>' + (meal.kcal ? '<div class="kc">' + meal.kcal + ' kcal</div>' : '');
    });
  }
  function photoSlides(src, meal) { // 사진 옆에 메뉴가 안 들어가면 사진만 크게 보이고 메뉴는 다음 장으로
    var img = '<div class="fr"><img src="' + esc(src) + '" alt="오늘 점심 식판 사진"></div>', top = head('오늘의 식사', '<p>' + esc(view.date_label) + ' 점심</p>');
    if (!meal) return [top + '<div class="pg-b ph alone">' + img + '</div>'];
    function height(perLine, lineH, gap) {
      var h = meal.kcal ? 78 : 0;
      meal.dishes.forEach(function (d) { h += Math.ceil(Math.max(d.name.length, 1) / perLine) * lineH + gap; });
      return h;
    }
    var roomy = height(10, 55.2, 22) <= BODY, tight = height(13, 46, 14) <= BODY;
    if (!roomy && !tight) return [top + '<div class="pg-b ph alone">' + img + '</div>'].concat(bigMenuSlides(meal));
    return [top + '<div class="pg-b ph">' + img + '<div class="ph-list' + (roomy ? '' : ' tight') + '"><ul>' + meal.dishes.map(function (d) {
      return '<li>' + esc(d.name) + '</li>';
    }).join('') + '</ul>' + (meal.kcal ? '<div class="kc">' + meal.kcal + ' kcal</div>' : '') + '</div></div>'];
  }
  function dayFit(day) { // 세 칸 중 한 칸에 들어가나: 48px 9줄, 40px 11줄
    function lines(perLine) { var n = 0; (day.meal ? day.meal.dishes : []).forEach(function (m) { n += Math.ceil(Math.max(m.name.length, 1) / perLine); }); return n; }
    return lines(10) <= 9 ? 'roomy' : lines(12) <= 11 ? 'tight' : 'solo';
  }
  function chunks3(day) { // 혼자 쓰는 장(세 단, 13자, 단마다 8줄)에도 안 들어가면 장을 더 쓴다
    var out = [{ ymd: day.ymd, label: day.label, meal: { kcal: day.meal.kcal, dishes: [] } }], used = 0;
    day.meal.dishes.forEach(function (m) {
      var n = Math.ceil(Math.max(m.name.length, 1) / 13);
      if (used + n > 24) { out.push({ ymd: day.ymd, label: day.label, meal: { kcal: day.meal.kcal, dishes: [] } }); used = 0; }
      used += n;
      out[out.length - 1].meal.dishes.push(m);
    });
    return out;
  }
  function weekSlides(d) { // 세 날씩. 한 칸에 안 들어가는 날은 혼자 한 장을 쓴다
    var parts = [], cur3 = [];
    d.days.forEach(function (x) {
      if (dayFit(x) === 'solo') {
        if (cur3.length) { parts.push(cur3); cur3 = []; }
        chunks3(x).forEach(function (one) { parts.push([one]); });
        return;
      }
      cur3.push(x);
      if (cur3.length === 3) { parts.push(cur3); cur3 = []; }
    });
    if (cur3.length) parts.push(cur3);
    return parts.map(function (part, i) {
      return head(d.week_title, nth(i, parts.length)) + '<div class="pg-b wk">' + part.map(function (x) {
        var on = x.ymd === d.date, fit = part.length === 1 && dayFit(x) !== 'roomy' ? 'solo' : dayFit(x);
        return '<section class="' + (on ? 'today' : '') + (fit === 'roomy' ? '' : ' ' + fit) + '"><h2>' + esc(x.label) + (on ? ' · 오늘' : '') + '</h2>' +
          (x.meal ? '<ul>' + names(x.meal.dishes).map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>' + (x.meal.kcal ? '<div class="kcal">' + x.meal.kcal + ' kcal</div>' : '') : '<div class="no">급식 없음</div>') + '</section>';
      }).join('') + '</div>';
    });
  }
  function noMealSlide(d) {
    return '<div class="pg-b none"><b>오늘은 급식이 없어요</b>' + (d.next ? '<span>다음 급식은 ' + esc(d.next.label) + '이에요</span>' : '') + '</div>';
  }
  function quietSlide(d) {
    return '<div class="pg-b none"><b>' + esc(d.school) + '</b><span>' + esc(d.date_label) + '</span></div>';
  }

  // ---------- 켜진 화면과 순서대로 장 만들기 ----------
  function todayMeal(d) {
    for (var i = 0; i < d.days.length; i++) if (d.days[i].ymd === d.date) return d.days[i].meal;
    return null;
  }
  function build(d) {
    if (d.quiet) return [{ html: quietSlide(d), sec: 3600, k: 'none' }];
    var meal = todayMeal(d), out = [], noMealShown = false;
    function add(htmls, sec, k) { htmls.forEach(function (h) { out.push({ html: h, sec: sec, k: k }); }); }
    function noMeal(sec) { // 메뉴가 필요한 화면 자리 중 첫 칸에 한 번만
      if (noMealShown || !d.known || d.photo) return;
      noMealShown = true;
      add([noMealSlide(d)], sec, 'none');
    }
    d.screens.forEach(function (s) {
      var sec = s.sec;
      var k = s.key;
      if (k === 'hygiene') add([hygieneSlide(d.hygiene)], sec, 'hy-' + (d.hygiene.key || 'wash'));
      else if (k === 'photo') {
        if (d.photo) add(photoSlides(d.photo, meal), sec, k);
        else if (meal) add(bigMenuSlides(meal), sec, k);
        else noMeal(sec);
      } else if (k === 'week') {
        if (d.days.some(function (x) { return x.meal; })) add(weekSlides(d), sec, k);
      } else if (!meal) noMeal(sec);
      else if (k === 'menu') add(menuSlides(meal), sec, k);
      else if (k === 'allergy') add(allergySlides(meal), sec, k);
      else if (k === 'cal') { if (meal.kcal || meal.nutr.length) add([calSlide(meal)], sec, k); }
      else if (k === 'origin') { if (meal.origin.length) add(originSlides(meal), sec, k); }
      else if (k === 'eat') add(eatSlides(meal, d.slogan), sec, k);
    });
    return out.length ? out : [{ html: quietSlide(d), sec: 3600, k: 'none' }];
  }

  // ---------- 그리기·돌리기 ----------
  // 테마 1은 지금 화면 그대로. 2~5는 같은 본문(.pgc)을 줄여 놓고 남는 자리에 그림(.art)과 시계 띠를 둔다(tv-themes.css).
  // ?theme=N은 미리보기용: 이 화면에서만 바뀌고 저장되지 않는다.
  var FORCED = /[?&]theme=([1-5])\b/.exec(location.search);
  function themeOf(d) { var t = FORCED ? +FORCED[1] : +d.theme; return t >= 1 && t <= 5 ? t : 1; }
  function render(d) {
    view = d;
    var th = d.quiet ? 1 : themeOf(d);
    stage.className = 'stage' + (d.quiet ? ' quiet' : '') + (th > 1 ? ' th th' + th : '') + (d.photo ? ' has-today' : '');
    if (d.photo) stage.style.setProperty('--today', 'url("' + d.photo + '")'); else stage.style.removeProperty('--today');
    $('sch').textContent = d.school;
    $('date').textContent = d.date_label;
    SLIDES = build(d);
    pagesEl.innerHTML = SLIDES.map(function (s) { return '<section class="pg" data-k="' + s.k + '"><div class="art" aria-hidden="true"></div><div class="pgc">' + s.html + '</div></section>'; }).join('');
    footEl.innerHTML = SLIDES.length > 1 ? SLIDES.map(function (s, i) { return '<div class="seg' + (i ? '' : ' first') + '"><i></i></div>'; }).join('') : '';
    var imgs = pagesEl.getElementsByTagName('img');
    for (var i = 0; i < imgs.length; i++) imgs[i].onerror = photoFailed;
    show(Math.min(cur, SLIDES.length - 1));
  }
  function photoFailed() { // 사진을 못 받으면 빈 틀 대신 오늘 메뉴
    var meal = todayMeal(view), pg = this.parentNode;
    while (pg && pg.className !== 'pgc') pg = pg.parentNode;
    if (meal && pg) pg.innerHTML = bigMenuSlides(meal)[0];
  }
  function show(i) {
    if (!SLIDES.length) return;
    cur = (i + SLIDES.length) % SLIDES.length;
    var pgs = pagesEl.children, sts = footEl.children;
    for (var j = 0; j < pgs.length; j++) {
      pgs[j].className = 'pg' + (j === cur ? ' on' : '');
      if (sts[j]) sts[j].className = 'seg' + (j ? '' : ' first') + (j < cur ? ' done' : '');
    }
    if (sts[cur]) {
      void sts[cur].offsetWidth; // 진행 막대를 처음부터 다시
      sts[cur].className += ' on' + (paused ? ' paused' : '');
      sts[cur].firstChild.style.animationDuration = SLIDES[cur].sec + 's';
    }
    left = SLIDES[cur].sec * 1000;
    schedule();
  }
  function schedule() {
    clearTimeout(timer);
    if (paused || SLIDES.length < 2) return;
    startedAt = Date.now();
    timer = setTimeout(function () { show(cur + 1); }, left);
  }
  function togglePause() {
    paused = !paused;
    if (paused) { clearTimeout(timer); left -= Date.now() - startedAt; } else schedule();
    var st = footEl.children[cur];
    if (st) { if (paused) st.className += ' paused'; else st.className = st.className.replace(' paused', ''); }
  }

  // ---------- 받기: 실패하면 아무것도 바꾸지 않는다 ----------
  function merged(d) { // 서버가 메뉴를 못 읽었으면(known=false) 받아 둔 메뉴로 오늘을 찾는다
    if (d.quiet || d.known || !last || !last.days) return d;
    var keep = last.days.some(function (x) { return x.ymd === d.date; });
    if (keep) { d.days = last.days; d.week_title = last.week_title; d.next = last.next; d.known = true; }
    return d;
  }
  function again(ms) { clearTimeout(pollTimer); pollTimer = setTimeout(load, ms); }
  function failed() { // 같은 날이면 마지막 화면 그대로. 날짜가 지났으면 어제 메뉴를 오늘로 보이지 않게 학교 이름과 오늘 날짜만
    var day = localDay();
    if (last && last.date !== day && view.date !== day) {
      var t = new Date();
      stale = true;
      render({ quiet: true, school: last.school, date: day, date_label: (t.getMonth() + 1) + '월 ' + t.getDate() + '일 ' + '일월화수목금토'.charAt(t.getDay()) + '요일' });
    }
    again(last ? RETRY : FIRST_RETRY);
  }
  function load() {
    var x = new XMLHttpRequest();
    x.open('GET', BASE + '/data');
    x.timeout = 30000;
    x.onload = function () {
      if (x.status === 404) { // 두 번 연속이면 주소가 새로 만들어진 것: 옛 화면을 계속 띄우지 않고 비운다. 잠깐의 404는 그냥 지나간다
        gone += 1;
        if (gone >= 2) { last = null; view = null; lastRaw = ''; clearTimeout(timer); SLIDES = []; pagesEl.innerHTML = ''; footEl.innerHTML = ''; stage.className = 'stage blank'; }
        return gone >= 2 ? again(RETRY) : failed();
      }
      if (x.status !== 200) return failed();
      gone = 0;
      var d;
      try { d = JSON.parse(x.responseText); } catch (e) { return failed(); }
      if (x.responseText !== lastRaw || stale) {
        lastRaw = x.responseText;
        stale = false;
        last = merged(d);
        render(last);
      }
      again(POLL);
    };
    x.onerror = x.ontimeout = failed;
    x.send();
  }

  function tick() {
    var d = new Date();
    $('clock').textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
    if (localDay() !== seenDay) { // 날짜가 바뀌면 바로 다시 받는다
      if (seenDay) load();
      seenDay = localDay();
    }
  }
  function fit() { stage.style.transform = 'translate(-50%,-50%) scale(' + Math.min(innerWidth / 1920, innerHeight / 1080) + ')'; }
  function fullscreen() {
    var el = document.documentElement;
    if (document.fullscreenElement) document.exitFullscreen();
    else if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  }

  addEventListener('resize', fit);
  addEventListener('keydown', function (e) {
    var k = e.keyCode;
    if (k === 39) show(cur + 1);
    else if (k === 37) show(cur - 1);
    else if (k === 32) { e.preventDefault(); togglePause(); }
    else if (k === 70) fullscreen();
  });
  addEventListener('dblclick', fullscreen);
  var idleT;
  function wake() { document.body.className = 'tv-body'; clearTimeout(idleT); idleT = setTimeout(function () { document.body.className = 'tv-body idle'; }, 3000); }
  addEventListener('mousemove', wake);
  wake(); fit(); tick(); setInterval(tick, 10000);
  load();
})();
