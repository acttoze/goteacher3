// E4 한글 쪽 미리보기 — rhwp(WASM, 고정 사본 ./rhwp-0.8.7, MIT)로 쪽별 SVG를 그리고 표 영역을 겹쳐 보인다.
// - 변환이 '완료'가 되면 회원 전용 hwpx 뷰에서 파일을 받는다(외부 요청 없음).
// - 쪽 SVG는 blob: <img>로 넣는다. 문서에서 나온 SVG를 DOM에 직접 넣지 않아 그 안의 스크립트·링크가 돌지 않는다.
// - 메모리: 화면 가까이 온 쪽만 그리고, 멀어진 쪽은 그림을 버린다(다시 오면 다시 그림). 앞 MAX_PAGES쪽까지만.
// - 표 영역: 첫 쪽을 그린 뒤 쉬는 틈에 쪽 배치를 조금씩 읽어(표 bbox만 남김) 서버 표 요약(hwp.tables)과
//   짝을 맞춘다(tablemap.js). 후보 표는 파란 점선, 나머지는 회색. rhwp가 쪽에 놓지 않은 중첩 후보는 품은 표에 근사 표시.
// - 표 고르기(E5): 미리보기 상자에 data-pick(확정 POST 주소)이 있으면 표 영역을 눌러 확정한다(후보 아닌 회색 표도).
//   추천(1순위)은 굵게, 나머지 후보는 '후보 k'로 순서를 보인다. 확정은 서버가 report["tables"] 안의 표인지 다시 본다.
// - 만들기 화면은 파일을 올린 뒤에야 상자가 생기므로 htmx 교체 때마다 다시 찾는다. rhwp는 처음 그릴 때만 불러온다.
// - 콘솔·화면 어디에도 문서 글자를 남기지 않는다.
import { match, newMap, scanPage } from './tablemap.js';

let root = null;
const FAIL = '미리보기를 못 그렸어요. 파일은 그대로 있어요. 잠시 뒤 다시 열어 보고, 계속되면 운영자에게 알려 주세요.';
const ROLE = { approval: '결재란' };
const MAX_PAGES = 300;   // 이보다 긴 문서는 앞쪽만(표는 대개 앞쪽에 있다)
const NEAR = '1500px 0px';  // 화면 위아래 이만큼 안의 쪽만 그림을 둔다
const SCAN_CHUNK = 8;    // 쉬는 틈마다 읽을 쪽 수

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

const isCand = (p) => p && p.rank !== null && p.rank !== undefined;
const rankName = (p) => (p.rank === 0 ? '추천' : `후보 ${p.rank + 1}`);
const idOf = (p) => `${p.sec}/${p.index}`;

function label(p) {
  return (isCand(p) ? `${rankName(p)} · ` : '') + `표 ${p.no}` + (ROLE[p.role] ? ` · ${ROLE[p.role]}` : '');
}

function layer(list, pw, ph, approx, pick) {
  const box = document.createElement('div');
  box.className = 'pv-layer';
  for (const f of list) {
    const p = f.t.py;
    const inner = approx.get(f.t.key);
    if (!p && !f.t.top && !inner) continue;  // 짝 못 찾은 중첩 표는 그리지 않는다(겹쳐서 어지러움)
    const d = document.createElement('div');
    d.className = isCand(p) ? (p.rank === 0 ? 'cand best' : 'cand') : 'cand no';
    d.dataset.key = f.t.key;
    d.style.zIndex = 1 + (f.t.key.match(/>/g) || []).length;  // 중첩 표가 품은 표 위에서 눌리게
    if (p) { d.dataset.sec = p.sec; d.dataset.index = p.index; d.dataset.id = idOf(p); }
    Object.assign(d.style, { left: `${f.x / pw * 100}%`, top: `${f.y / ph * 100}%`,
                             width: `${f.w / pw * 100}%`, height: `${f.h / ph * 100}%` });
    if (p) d.appendChild(tag(label(p), 'tag'));
    if (pick && p) {  // 고르기: 짝이 맞은 표는 모두 누를 수 있다(회색도)
      d.setAttribute('role', 'button');
      d.tabIndex = 0;
      d.setAttribute('aria-label', `${label(p)}, ${p.rows}줄 ${p.cols}칸. 이 표로 받기`);
      d.setAttribute('aria-pressed', String(pick.id === idOf(p)));
      d.classList.toggle('on', pick.id === idOf(p));
    }
    if (inner) {  // 위치를 모르는 중첩 후보: 이 표 안 어딘가에 있다
      d.classList.add('has-in');
      d.dataset.inner = inner.map(idOf).join(' ');
      if (pick) {  // 품은 표 안의 후보를 따로 누를 수 있게 단추로
        const g = document.createElement('span');
        g.className = 'tag in';
        g.appendChild(document.createTextNode('이 표 안: '));
        for (const t of inner) {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'in-pick' + (pick.id === idOf(t) ? ' on' : '');
          b.dataset.sec = t.sec;
          b.dataset.index = t.index;
          b.dataset.id = idOf(t);
          b.setAttribute('aria-pressed', String(pick.id === idOf(t)));
          b.textContent = label(t);
          g.appendChild(b);
        }
        d.appendChild(g);
      } else d.appendChild(tag(`이 표 안: 표 ${inner.map((t) => t.no).join('·')}`, 'tag in'));
    }
    box.appendChild(d);
  }
  return box;
}

// 고르기: 표 하나를 눌러 서버에 확정한다. 화면 표시는 서버가 받아들인 뒤에만 바꾼다.
function picker(el, py) {
  const url = el.dataset.pick;
  if (!url) return null;
  const st = { id: el.dataset.picked || '', busy: false };
  const hint = document.getElementById('pickHint');
  const err = document.getElementById('pickErr');
  const csrf = () => (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
  const byId = new Map(py.map((t) => [idOf(t), t]));
  st.paint = () => {
    document.querySelectorAll('#hwpPick [data-id], #hwpPick .pchip').forEach((x) => {
      const on = (x.dataset.id || `${x.dataset.sec}/${x.dataset.index}`) === st.id;
      x.classList.toggle('on', on);
      if (x.hasAttribute('aria-pressed')) x.setAttribute('aria-pressed', String(on));
    });
    el.dataset.picked = st.id;
    const hidden = document.getElementById('hwpTable');
    if (hidden) hidden.value = st.id;
  };
  st.pick = async (id) => {
    const t = byId.get(id);
    if (!t || st.busy) return;
    st.busy = true;
    if (err) err.textContent = '';
    try {
      const body = new URLSearchParams({ sec: t.sec, index: t.index });
      const res = await fetch(url, { method: 'POST', body, credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'fetch' } });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(j.error || '표를 고르지 못했어요. 잠시 뒤 다시 눌러 주세요.');
      st.id = id;
      st.paint();
      if (hint) {
        hint.textContent = '';
        const b = document.createElement('b');
        b.textContent = `표 ${t.no}`;
        hint.append(b, ' — 이 표로 받아요. 다 모이면 이 표 칸만 채워 같은 파일로 돌려드려요. 다른 표를 누르면 바뀌어요.');
      }
      el.dispatchEvent(new CustomEvent('gt-hwp-picked', { bubbles: true, detail: { sec: t.sec, index: t.index, no: t.no } }));
    } catch (e) {
      if (err) err.textContent = e.message;
    }
    st.busy = false;
  };
  const target = (e) => e.target.closest('[data-id], .pchip[data-sec]');
  const onPick = (e) => {
    const x = target(e);
    if (!x || !x.closest('#hwpPick')) return;
    if (e.type === 'keydown') {
      if (x.tagName === 'BUTTON' || (e.key !== 'Enter' && e.key !== ' ')) return;
      e.preventDefault();
    }
    e.stopPropagation();
    st.pick(x.dataset.id || `${x.dataset.sec}/${x.dataset.index}`);
  };
  const box = document.getElementById('hwpPick');
  box.addEventListener('click', onPick);
  box.addEventListener('keydown', onPick);
  return st;
}

async function start() {
  const me = root;
  if (me.dataset.state) return;
  me.dataset.state = 'loading';
  const py = JSON.parse(document.getElementById('hwpTables').textContent || '[]');
  const pick = picker(me, py);  // 후보 단추는 미리보기를 못 그려도 누를 수 있게 먼저
  me.classList.toggle('picking', !!pick);
  try {
    if (typeof WebAssembly !== 'object') throw new Error('NoWasm');
    const t0 = performance.now();
    const { default: init, HwpDocument } = await import('./rhwp-0.8.7/rhwp.js');
    const [wasm, res] = await Promise.all([init(), fetch(me.dataset.src, { credentials: 'same-origin', cache: 'no-store' })]);
    if (!res.ok) throw new Error(`HTTP${res.status}`);
    if (!me.isConnected) return;  // 그사이 '다른 파일'을 눌렀다
    const doc = new HwpDocument(new Uint8Array(await res.arrayBuffer()));
    const total = doc.pageCount();
    if (!total) throw new Error('NoPage');
    const n = Math.min(total, MAX_PAGES);
    const map = newMap();
    let mapped = false;

    const holder = document.getElementById('hwpPages');
    const slots = [];
    for (let i = 0; i < n; i++) {
      const info = JSON.parse(doc.getPageInfo(i));
      const el = document.createElement('div');
      el.className = 'pv-page';
      el.style.aspectRatio = `${info.width} / ${info.height}`;
      el.setAttribute('role', pick ? 'group' : 'img');  // 고르기 화면은 안에 누를 표가 있다
      el.setAttribute('aria-label', `${i + 1}쪽`);
      el.dataset.i = i;
      el.appendChild(tag(`${i + 1} / ${total}쪽`, 'pv-no'));
      holder.appendChild(el);
      slots.push({ el, w: info.width, h: info.height, frags: null, img: null, lay: null, busy: null, near: false, ms: null });
    }

    const addLayer = (s) => {
      if (!mapped || !s.img || s.lay) return;
      s.lay = layer(s.frags, s.w, s.h, map.approx, pick);
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
          me.dataset.pageFails = String(+(me.dataset.pageFails || 0) + 1);
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
    me.dataset.state = 'drawing';
    me.dataset.pages = total;
    me.dataset.shownPages = n;

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
    Object.assign(me.dataset, { state: 'ready', tables: all.length, top: all.filter((t) => t.top).length,
      mapped: exact.size, cands: cands.length, candsShown: shown, candsApprox: cands.filter((t) => near.has(t)).length,
      loadMs: Math.round(performance.now() - t0) });
    document.getElementById('hwpSummary').textContent = `${total}쪽` + (total > n ? ` · 앞 ${n}쪽만 보여요` : '') +
      ` · 표 ${all.length}개` + (cands.length ? ` · 고를 수 있는 표 ${shown}개` : '');
    // 미리보기에 자리를 못 찾은 후보(rhwp가 쪽에 놓지 않은 표)도 고를 수 있게 후보 단추 줄에 덧붙인다
    const chips = document.querySelector('#hwpPick .pick-chips');
    if (pick && chips) {
      for (const t of cands) {
        if (exact.has(t) || near.has(t) || chips.querySelector(`[data-sec="${t.sec}"][data-index="${t.index}"]`)) continue;
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'pchip';
        b.dataset.sec = t.sec;
        b.dataset.index = t.index;
        b.setAttribute('aria-pressed', 'false');
        b.append(`${rankName(t)} · 표 ${t.no}`, tag('미리보기에 안 보이는 표', ''));
        chips.appendChild(b);
      }
      pick.paint();
    }
    const hint = document.getElementById('pickHint');
    if (!pick) hint.hidden = !shown;
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
    me.dataset.state = 'error';
    me.dataset.error = (e && e.name) || 'Error';  // 예외 종류만(문서 내용 없음)
    if (me.isConnected) say(FAIL, true);
  }
}

function check() {
  root = document.getElementById('hwpPreview');
  if (!root) return;
  const st = document.querySelector('.hwp-status');
  const s = st && st.dataset.status;
  if (s === 'done') start();
  else if (s === 'failed') say('미리보기를 그릴 파일이 없어요.', false);
}

check();
document.body.addEventListener('htmx:afterSettle', check);
document.addEventListener('gt-hwp-mount', check);
