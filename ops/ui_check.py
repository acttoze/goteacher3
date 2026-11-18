"""새 UI를 넣으면 돌리는 여백 검사 — 색 박스·테두리 박스 안에서 글자·단추가 가장자리에 붙었는지, 박스 아래 요소가 맞닿았는지.

실행(저장소 루트, 플레이라이트가 깔린 파이썬):
    python3 ops/ui_check.py            # 문제가 있으면 목록을 찍고 종료 코드 1
    python3 ops/ui_check.py --shots    # 문제 박스마다 잘라 찍은 그림도 남긴다(임시 폴더 경로를 알려 줌)

도움말 캡처(ops/help/capture.py)의 데모 서버·데모 학교를 그대로 쓴다(개발 DB는 건드리지 않음).
로그인한 사람별로 왼쪽 메뉴·화면 안 링크를 따라 공간 안 화면을 훑고, 숨은 탭 칸([data-pane])도 하나씩 펼쳐 잰다.
기준(1536×750): 박스 안쪽 여백 < 6px, 박스와 바로 아래 형제 요소 사이 < 4px 이면 문제.
2026-11-18 새 취합 '자주 쓰는 양식'의 물품 주문 조사 강조 카드가 .list > li의 좌우 padding 0을 물려받아 생긴 오류 뒤 만들었다.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "help"))
import capture  # noqa: E402

MIN_PAD, MIN_GAP, MAX_PAGES = 6, 4, 40
SHOTS = "--shots" in sys.argv

JS = r"""([minPad, minGap]) => {
  const bad = [], seen = new Set();
  const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor;
    if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c; } return 'rgb(255, 255, 255)'; };
  const SKIP = 'html,body,input,select,textarea,button,option,img,svg,video,canvas,iframe,hr,i,td,th,tr,table,thead,tbody';
  const isBox = el => {
    if (el.matches(SKIP) || el.closest('button,a.btn,.btn,.pill,.tabs,.seg,.toggle,canvas,svg,[data-ui-check-skip]')) return false;
    const s = getComputedStyle(el);
    const bg = s.backgroundColor, ownBg = bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent' && el.parentElement && bg !== bgOf(el.parentElement);
    const borders = ['Top', 'Right', 'Bottom', 'Left'].filter(k => parseFloat(s['border' + k + 'Width']) > 0 && s['border' + k + 'Style'] !== 'none').length;
    return ownBg || borders >= 3;
  };
  const name = el => { let n = el.tagName.toLowerCase(); if (el.id) n += '#' + el.id; if (el.classList.length) n += '.' + [...el.classList].join('.');
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40); return n + (t ? ` 「${t}」` : ''); };
  const floats = (n, el) => { for (let e = n; e && e !== el; e = e.parentElement) if (/absolute|fixed/.test(getComputedStyle(e).position)) return true; return false; };
  const contentRects = el => {
    const out = [], w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) { if (!n.textContent.trim() || !vis(n.parentElement) || floats(n.parentElement, el)) continue;  // 떠 있는 뱃지·커서는 제외
      const rg = document.createRange(); rg.selectNodeContents(n); out.push(...[...rg.getClientRects()].filter(r => r.width > 2 && r.height > 2)); }
    const painted = c => { const s = getComputedStyle(c); return !/rgba\(0, 0, 0, 0\)|transparent/.test(s.backgroundColor) || parseFloat(s.borderTopWidth) > 0; };
    el.querySelectorAll('input,select,textarea,img,svg,button,.btn').forEach(c => vis(c) && !floats(c, el)
      && (!c.matches('button,.btn') || painted(c)) && out.push(c.getBoundingClientRect()));  // 배경 없는 글자 단추는 글자로만 잰다
    return out.filter(r => r.width > 2 && r.height > 2);
  };
  for (const el of document.querySelectorAll('body *')) {
    if (!vis(el) || !isBox(el)) continue;
    const r = el.getBoundingClientRect(), rs = contentRects(el);
    if (!rs.length || r.width < 60 || r.height < 32) continue;  // 뱃지·알약·파일 아이콘은 제외
    const L = Math.min(...rs.map(x => x.left)) - r.left, R = r.right - Math.max(...rs.map(x => x.right)),
          T = Math.min(...rs.map(x => x.top)) - r.top, B = r.bottom - Math.max(...rs.map(x => x.bottom));
    const sides = [['왼쪽', L], ['오른쪽', R], ['위', T], ['아래', B]].filter(([, v]) => v < minPad && v > -2);
    if (sides.length) bad.push({kind: '안쪽 여백', where: name(el), detail: sides.map(([k, v]) => `${k} ${v.toFixed(0)}px`).join(', '), sel: el});
    let nx = el.nextElementSibling; while (nx && !vis(nx)) nx = nx.nextElementSibling;
    const ps = getComputedStyle(el.parentElement), stacked = !/flex|grid/.test(ps.display) || ps.flexDirection.startsWith('column');
    const ns = nx && stacked && !isBox(nx) ? contentRects(nx) : [];
    if (ns.length) {  // 박스 바로 아래 형제의 '글자·단추'까지 거리(형제 테두리 말고)
      const g = Math.min(...ns.map(x => x.top)) - r.bottom;
      if (g >= -1 && g < minGap) bad.push({kind: '아래와 맞닿음', where: name(el), detail: `→ ${name(nx)} 간격 ${g.toFixed(0)}px`, sel: el});
    }
  }
  return bad.filter(b => { const k = b.kind + b.where; if (seen.has(k)) return false; seen.add(k); return true; })
            .map((b, i) => { b.sel.setAttribute('data-ui-bad', i); return {kind: b.kind, where: b.where, detail: b.detail, i}; });
}"""

found = []
shot_dir = tempfile.mkdtemp(prefix="gt_uicheck_") if SHOTS else None


def check(st, label):
    st.page.wait_for_timeout(300)
    for b in st.page.evaluate(JS, [MIN_PAD, MIN_GAP]):
        found.append((label, b))
        if SHOTS:
            el = st.page.locator(f"[data-ui-bad='{b['i']}']").first
            try:
                el.screenshot(path=os.path.join(shot_dir, f"{len(found):03d}.png"))
            except Exception:
                pass


def crawl(st, who):
    st.login(who)
    base_space = f"/s/{st.ctx['space']}/"
    todo, done = [st.page.url.split(st.base)[1]], set()
    while todo and len(done) < MAX_PAGES:
        path = todo.pop(0)
        if path in done or any(x in path for x in ("logout", "/export", "download", ".xlsx", ".hwp", "/delete")):
            continue
        done.add(path)
        st.go(path)
        check(st, f"{who} {path}")
        panes = st.page.evaluate("[...new Set([...document.querySelectorAll('[data-pane]')].flatMap(e => e.dataset.pane.split(' ')))]")
        for p in panes:  # 탭 칸을 하나씩 펼쳐서
            st.page.evaluate("p => document.querySelectorAll('[data-pane]').forEach(e => e.hidden = !e.dataset.pane.split(' ').includes(p))", p)
            check(st, f"{who} {path} [{p}]")
        for href in st.page.evaluate("[...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))"):
            href = href.split("#")[0]
            if href.startswith(base_space) and href not in done and href not in todo:
                todo.append(href)


def scene_admin(st):
    crawl(st, "admin")


def scene_teacher(st):
    crawl(st, "41")


def scene_public(st):
    for path in ("/", "/start"):
        st.go(path)
        check(st, path)


if __name__ == "__main__":
    capture.main(scenes=[scene_public, scene_admin, scene_teacher])
    for label, b in found:
        print(f"[{b['kind']}] {label} :: {b['where']} — {b['detail']}")
    print(f"\n문제 {len(found)}곳" + (f" · 그림 {shot_dir}" if SHOTS else ""))
    sys.exit(1 if found else 0)
