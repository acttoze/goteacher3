// F2 한글로 받기 미리보기 — 합본 화면(collect/detail.html)의 #hwpPaper 에 채운 결과(없으면 올린 양식) 쪽을 그리고,
// 원본과 합본의 쪽 수를 rhwp(WASM, ./rhwp-0.8.7)로 세어 비교한다. 쪽 수는 이 화면에서만 쓰고 서버에 보내지 않는다.
// - 합본 쪽이 늘었고 더 줄일 수 있으면 #hwpShrink(글자 줄이기)를 보인다. 누르면 htmx 가 다음 크기로 다시 채우기를 맡긴다.
// - 쪽 SVG는 blob: <img>로 넣는다(문서에서 나온 SVG를 DOM에 직접 넣지 않음, E4와 같은 CSP).
// - 쪽은 상자 안에서 화면 가까이 온 것만 그린다. 앞 MAX_PAGES쪽까지만.
// - 콘솔·화면 어디에도 문서 글자를 남기지 않는다(오류는 예외 종류만 data-error 에).
const MAX_PAGES = 60;
const FAIL = '미리보기를 못 그렸어요. 파일은 그대로 받을 수 있어요.';
const counts = new Map();   // 파일 주소 → 쪽 수(같은 결과를 htmx가 다시 그려도 다시 세지 않게)
let rhwp = null;
let shown = '';             // 지금 상자에 그린 파일 주소
let live = null;            // 지금 상자의 { doc, io } — 다른 파일로 바뀌면 풀어 준다

let ctx2d = null, lastFont = '';
globalThis.measureTextWidth = globalThis.measureTextWidth || ((font, text) => {
  if (!ctx2d) ctx2d = document.createElement('canvas').getContext('2d');
  if (font !== lastFont) { ctx2d.font = font; lastFont = font; }
  return ctx2d.measureText(text).width;
});

async function lib() {
  if (!rhwp) {
    rhwp = (async () => {
      if (typeof WebAssembly !== 'object') throw new Error('NoWasm');
      const m = await import('./rhwp-0.8.7/rhwp.js');
      await m.default();
      return m;
    })();
  }
  return rhwp;
}

async function open(url) {
  const { HwpDocument } = await lib();
  const res = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP${res.status}`);
  const doc = new HwpDocument(new Uint8Array(await res.arrayBuffer()));
  const n = doc.pageCount();
  if (!n) throw new Error('NoPage');
  counts.set(url, n);
  return doc;
}

async function pages(url) {
  if (counts.has(url)) return counts.get(url);
  const doc = await open(url);
  const n = counts.get(url);
  doc.free();
  return n;
}

function msg(box, text, err) {
  box.textContent = '';
  const p = document.createElement('p');
  p.className = 'pv-msg' + (err ? ' err' : '');
  p.textContent = text;
  box.appendChild(p);
}

// 상자에 쪽들을 그린다(화면 가까이 온 쪽만). 그 사이 다른 파일로 바뀌면 그만둔다.
async function draw(box, url, label) {
  if (shown === url) return;
  shown = url;
  msg(box, '미리보기를 그리는 중이에요…');
  const doc = await open(url);
  if (shown !== url) { doc.free(); return; }
  if (live) { live.io.disconnect(); live.doc.free(); }
  const total = counts.get(url), n = Math.min(total, MAX_PAGES);
  box.textContent = '';
  box.dataset.src = url;
  const slots = [];
  for (let i = 0; i < n; i++) {
    const info = JSON.parse(doc.getPageInfo(i));
    const el = document.createElement('div');
    el.className = 'pv-pg';
    el.style.aspectRatio = `${info.width} / ${info.height}`;
    el.setAttribute('role', 'img');
    el.setAttribute('aria-label', `${label} ${i + 1}쪽`);
    el.dataset.i = i;
    box.appendChild(el);
    slots.push({ el, img: null, busy: false });
  }
  const paint = async (s, i) => {
    if (s.img || s.busy || shown !== url) return;
    s.busy = true;
    try {
      const blob = URL.createObjectURL(new Blob([doc.renderPageSvg(i)], { type: 'image/svg+xml' }));
      const img = new Image();
      img.alt = '';
      img.src = blob;
      try { await img.decode(); } finally { URL.revokeObjectURL(blob); }
      s.img = img;
      s.el.appendChild(img);
    } catch (e) {
      s.el.classList.add('fail');
    }
    s.busy = false;
    box.dataset.drawn = String(slots.filter((x) => x.img).length);
  };
  const io = new IntersectionObserver((ents) => {
    for (const e of ents) if (e.isIntersecting) paint(slots[+e.target.dataset.i], +e.target.dataset.i);
  }, { root: box, rootMargin: '600px 0px' });
  slots.forEach((s) => io.observe(s.el));
  live = { doc, io };
  box.dataset.pages = total;
  box.dataset.state = 'ready';
}

function setText(el, parts, more) {
  el.textContent = '';
  for (const p of parts) {
    if (typeof p === 'string') el.append(p);
    else { const b = document.createElement('b'); b.textContent = p.b; el.append(b); }
  }
  el.classList.toggle('more', !!more);
}

async function check() {
  const li = document.getElementById('hwpResult');
  const box = document.getElementById('hwpPaper');
  if (!li || !box) return;
  const head = document.getElementById('hwpR');
  const out = li.dataset.out, orig = li.dataset.orig;
  try {
    if (!out) {   // 아직 결과가 없거나 만드는 중: 올린 양식을 보인다
      if (li.dataset.status === 'pending' || li.dataset.status === 'filling') {
        if (head) head.textContent = '새로 만드는 중이에요';
        if (shown) return;   // 앞 결과 미리보기는 끝날 때까지 그대로 둔다
      } else if (head) head.textContent = '올린 양식 · 채우기 전';
      await draw(box, orig, '올린 양식');
      return;
    }
    await draw(box, out, '합본');
    const b = counts.get(out), a = await pages(orig);
    if (!li.isConnected) return;
    li.dataset.origPages = a;
    li.dataset.outPages = b;
    if (head) head.textContent = `합본 미리보기 ${b}쪽`;
    const line = document.getElementById('hwpPages');
    const shrink = +(li.dataset.shrink || 0), max = +(li.dataset.max || 0);
    const form = document.getElementById('hwpShrink');
    if (line) {
      if (b > a) {
        const tail = shrink < max ? ' 칸에 글이 길어 줄이 늘었어요. 원래 쪽에 맞추려면 글자 줄이기를 눌러 보세요.'
          : ' 글자를 더 줄이면 읽기 어려워요. 한글에서 칸 너비나 쪽 여백을 조정해 주세요.';
        setText(line, ['원본 ', { b: `${a}쪽` }, ' → 합본 ', { b: `${b}쪽` }, ` (${b - a}쪽 늘었어요).${tail}`], true);
      } else setText(line, ['원본 ', { b: `${a}쪽` }, ' → 합본 ', { b: `${b}쪽` }, b < a ? '' : ' · 쪽 수가 같아요.']);
    }
    if (form) form.hidden = !(b > a && shrink < max);
  } catch (e) {
    box.dataset.state = 'error';
    box.dataset.error = (e && e.name) || 'Error';
    shown = '';
    msg(box, FAIL, true);
    const line = document.getElementById('hwpPages');
    if (line && out) line.textContent = '쪽 수를 세지 못했어요. 한글에서 열어 확인해 주세요.';
  }
}

check();
document.body.addEventListener('htmx:afterSettle', check);
