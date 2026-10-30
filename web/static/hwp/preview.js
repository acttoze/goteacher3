// E4 한글 쪽 미리보기 — rhwp(WASM, 고정 사본 ./rhwp-0.8.7, MIT)로 쪽별 SVG를 그리고 표 영역을 겹쳐 보인다.
// - 변환이 '완료'가 되면 회원 전용 hwpx 뷰에서 파일을 받는다(외부 요청 없음).
// - 쪽 SVG는 blob: <img>로 넣는다. 문서에서 나온 SVG를 DOM에 직접 넣지 않아 그 안의 스크립트·링크가 돌지 않는다.
// - 메모리: 화면 가까이 온 쪽만 그리고, 멀어진 쪽은 그림을 버린다(다시 오면 다시 그림). 앞 MAX_PAGES쪽까지만.
// - 표 영역: 첫 쪽을 그린 뒤 쉬는 틈에 쪽 배치를 조금씩 읽어(표 bbox만 남김) 서버 표 요약(hwp.tables)과
//   짝을 맞춘다(tablemap.js). 후보 표는 파란 점선, 나머지는 회색. rhwp가 쪽에 놓지 않은 중첩 후보는 품은 표에 근사 표시.
// - 표 고르기(클릭 확정)는 E5. 콘솔·화면 어디에도 문서 글자를 남기지 않는다.
import init, { HwpDocument } from './rhwp-0.8.7/rhwp.js';
import { match, newMap, scanPage } from './tablemap.js';

const root = document.getElementById('hwpPreview');
const FAIL = '미리보기를 못 그렸어요. 파일은 그대로 있어요. 잠시 뒤 다시 열어 보고, 계속되면 운영자에게 알려 주세요.';
const ROLE = { approval: '결재란' };
const MAX_PAGES = 300;   // 이보다 긴 문서는 앞쪽만(표는 대개 앞쪽에 있다)
const NEAR = '1500px 0px';  // 화면 위아래 이만큼 안의 쪽만 그림을 둔다
const SCAN_CHUNK = 8;    // 쉬는 틈마다 읽을 쪽 수
let started = false;

function say(text, err) {
  const m = document.getElementById('hwpMsg');
  m.textContent = text;
  m.classList.toggle('err', !!err);
}

// 글자 폭은 브라우저 canvas로 잰다(rhwp가 줄바꿈 계산에 부른다).
let ctx2d = null, lastFont = '';
globalThis.measureTextWidth = (font, text) => {
  if (!ctx2d) ctx2d = document.createElement('canvas').getContext('2d');
  if (font !== lastFont) { ctx2d.font = font; lastFont = font; }
  return ctx2d.measureText(text).width;
};

function tag(text, cls) {
  const t = document.createElement('span');
  t.className = cls;
  t.textContent = text;
  return t;
}

function layer(list, pw, ph, approx) {
  const box = document.createElement('div');
  box.className = 'pv-layer';
  for (const f of list) {
    const p = f.t.py;
    const inner = approx.get(f.t.key);
    if (!p && !f.t.top && !inner) continue;  // 짝 못 찾은 중첩 표는 그리지 않는다(겹쳐서 어지러움)
    const d = document.createElement('div');
    const cand = p && p.rank !== null && p.rank !== undefined;
    d.className = cand ? 'cand' : 'cand no';
    d.dataset.key = f.t.key;
    if (p) { d.dataset.sec = p.sec; d.dataset.index = p.index; }
    Object.assign(d.style, { left: `${f.x / pw * 100}%`, top: `${f.y / ph * 100}%`,
                             width: `${f.w / pw * 100}%`, height: `${f.h / ph * 100}%` });
    if (p) d.appendChild(tag(`표 ${p.no}` + (ROLE[p.role] ? ` · ${ROLE[p.role]}` : ''), 'tag'));
    if (inner) {  // 위치를 모르는 중첩 후보: 이 표 안 어딘가에 있다
      d.classList.add('has-in');
      d.dataset.inner = inner.map((t) => `${t.sec}/${t.index}`).join(' ');
      d.appendChild(tag(`이 표 안: 표 ${inner.map((t) => t.no).join('·')}`, 'tag in'));
    }
    box.appendChild(d);
  }
  return box;
}

async function start() {
  if (started) return;
  started = true;
  root.dataset.state = 'loading';
  try {
    if (typeof WebAssembly !== 'object') throw new Error('NoWasm');
    const t0 = performance.now();
    const [wasm, res] = await Promise.all([init(), fetch(root.dataset.src, { credentials: 'same-origin', cache: 'no-store' })]);
    if (!res.ok) throw new Error(`HTTP${res.status}`);
    const doc = new HwpDocument(new Uint8Array(await res.arrayBuffer()));
    const total = doc.pageCount();
    if (!total) throw new Error('NoPage');
    const n = Math.min(total, MAX_PAGES);
    const py = JSON.parse(document.getElementById('hwpTables').textContent || '[]');
    const map = newMap();
    let mapped = false;

    const holder = document.getElementById('hwpPages');
    const slots = [];
    for (let i = 0; i < n; i++) {
      const info = JSON.parse(doc.getPageInfo(i));
      const el = document.createElement('div');
      el.className = 'pv-page';
      el.style.aspectRatio = `${info.width} / ${info.height}`;
      el.setAttribute('role', 'img');
      el.setAttribute('aria-label', `${i + 1}쪽`);
      el.dataset.i = i;
      el.appendChild(tag(`${i + 1} / ${total}쪽`, 'pv-no'));
      holder.appendChild(el);
      slots.push({ el, w: info.width, h: info.height, frags: null, img: null, lay: null, busy: null, near: false, ms: null });
    }

    const addLayer = (s) => {
      if (!mapped || !s.img || s.lay) return;
      s.lay = layer(s.frags, s.w, s.h, map.approx);
      s.el.appendChild(s.lay);
    };
    const release = (s) => {
      if (s.img) { s.img.removeAttribute('src'); s.img.remove(); s.img = null; }
      if (s.lay) { s.lay.remove(); s.lay = null; }
    };
    const draw = (i) => {
      const s = slots[i];
      if (s.img) return Promise.resolve();
      if (s.busy) return s.busy;
      s.busy = (async () => {
        const a = performance.now();
        try {
          const svg = doc.renderPageSvg(i);
          const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
          const img = new Image();
          img.alt = '';
          img.src = url;
          try { await img.decode(); } finally { URL.revokeObjectURL(url); }
          s.img = img;
          s.el.classList.remove('fail');
          s.el.prepend(img);
          addLayer(s);
        } catch (e) {
          s.el.classList.add('fail');
          root.dataset.pageFails = String(+(root.dataset.pageFails || 0) + 1);
        }
        s.ms = performance.now() - a;
        s.busy = null;
        if (!s.near) release(s);  // 그리는 사이 화면에서 멀어졌으면 바로 버린다
      })();
      return s.busy;
    };

    const io = new IntersectionObserver((ents) => {
      for (const e of ents) {
        const s = slots[+e.target.dataset.i];
        s.near = e.isIntersecting;
        if (s.near) draw(+e.target.dataset.i);
        else if (!s.busy) release(s);
      }
    }, { rootMargin: NEAR });
    slots.forEach((s) => io.observe(s.el));

    if (total > n) document.getElementById('hwpLimit').hidden = false;
    document.getElementById('hwpSummary').textContent = `${total}쪽` + (total > n ? ` · 앞 ${n}쪽만 보여요` : '');
    say('');
    root.dataset.state = 'drawing';
    root.dataset.pages = total;
    root.dataset.shownPages = n;

    // 표 짝 맞추기: 쉬는 틈에 쪽 배치를 조금씩 읽는다(그리기를 막지 않게). 표 bbox만 남기고 칸 좌표는 버린다.
    for (let i = 0; i < n; i += SCAN_CHUNK) {
      await new Promise((r) => setTimeout(r, 0));
      for (let k = i; k < Math.min(n, i + SCAN_CHUNK); k++) slots[k].frags = scanPage(map, doc.getPageControlLayout(k));
    }
    match(map, py);
    mapped = true;
    slots.forEach(addLayer);

    const all = [...map.tables.values()];
    const cands = py.filter((t) => t.rank !== null && t.rank !== undefined);
    const exact = new Set(all.filter((t) => t.py).map((t) => t.py));
    const near = new Set([...map.approx.values()].flat());
    const shown = cands.filter((t) => exact.has(t) || near.has(t)).length;
    Object.assign(root.dataset, { state: 'ready', tables: all.length, top: all.filter((t) => t.top).length,
      mapped: exact.size, cands: cands.length, candsShown: shown, candsApprox: cands.filter((t) => near.has(t)).length,
      loadMs: Math.round(performance.now() - t0) });
    document.getElementById('hwpSummary').textContent = `${total}쪽` + (total > n ? ` · 앞 ${n}쪽만 보여요` : '') +
      ` · 표 ${all.length}개` + (cands.length ? ` · 고를 수 있는 표 ${shown}개` : '');
    document.getElementById('pickHint').hidden = !shown;
    // 측정·점검용: 모든 쪽을 차례로 그려 쪽마다 걸린 시간(ms)을 돌려준다(화면 밖 쪽은 재고 나서 버림).
    window.gtHwpPreview = {
      renderAll: async () => {
        for (let i = 0; i < n; i++) { await draw(i); if (!slots[i].near) release(slots[i]); }
        return slots.map((s) => s.ms);
      },
      live: () => slots.filter((s) => s.img).length,
      wasmMB: () => Math.round(wasm.memory.buffer.byteLength / 1048576),
    };
  } catch (e) {
    root.dataset.state = 'error';
    root.dataset.error = (e && e.name) || 'Error';  // 예외 종류만(문서 내용 없음)
    say(FAIL, true);
  }
}

function check() {
  const st = document.querySelector('.hwp-status');
  const s = st && st.dataset.status;
  if (s === 'done') start();
  else if (s === 'failed') say('미리보기를 그릴 파일이 없어요.', false);
}

if (root) {
  check();
  document.body.addEventListener('htmx:afterSettle', check);
}
