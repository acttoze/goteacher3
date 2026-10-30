// rhwp 표 조각 ↔ 서버 표 요약(hwp.tables) 짝 맞추기. DOM을 쓰지 않는다(app/hwpfiles/test_tablemap.py가 node로 검증).
//
// rhwp getPageControlLayout 의 표 stableIndex
//   바깥 표:      [구역, 문단, 컨트롤]                            → 문서 순서 = (문단, 컨트롤)
//   글상자 안 표: [구역, 문단, 도형 컨트롤, 0, 글상자 문단, 컨트롤]  → 파이썬도 바깥 표로 친다(칸 안이 아님)
//   중첩 표:      [구역, 칸 안 문단, 컨트롤, 부모 칸 번호, …]       → 부모 표 번호는 없다
// 그래서 부모는 같은 쪽에서 중첩 표 중심을 품은 칸이 가장 작은 표로 찾는다(넓이가 같으면 칸 번호 path[3]이 맞는 것).
// 파이썬 hwp.tables 순서는 XML 문서 순서(부모 → 그 칸들 안의 표 → 다음 표)이므로,
// 바깥 표를 (문단, 컨트롤)로, 자식은 (부모 칸 번호, 칸 안 문단, 컨트롤)로 늘어놓은 전위 순서와 맞춘다.
// 짝은 구역마다 앞에서부터 (행·열 수, 중첩 여부)가 같은 것을 몇 개 앞까지만 찾는다.
// 칸 수는 쓰지 않는다: rhwp는 쪽에 실제로 놓인 칸만 돌려줘 원본 칸 수보다 적을 수 있다.
// rhwp가 쪽에 놓지 않은 중첩 표(감춘 표·rhwp 한계)는 위치가 없다. 그런 중첩 후보는 짝이 맞은 가장 가까운
// 조상 표에 근사로 붙인다(map.approx: rhwp 표 key → 파이썬 표 목록, hwp.tables 의 parent 사용).

export function newMap() {
  return { tables: new Map(), approx: new Map() };
}

// 쪽 하나의 배치(JSON 문자열)를 읽어 그 쪽 표 조각 목록을 돌려준다. 칸 좌표는 부모 찾기에만 쓰고 버린다.
// 중첩 표의 stableIndex는 부모가 달라도 같을 수 있어(예: 서로 다른 표 칸 안의 첫 표가 모두 0/0/0/0/0/0/0/0/0)
// key는 "부모 key>자기 경로"로 만든다. 쪽을 넘긴 표는 다음 쪽에서도 같은 부모를 찾아 같은 key가 된다.
export function scanPage(map, layoutJson) {
  const ctrls = (JSON.parse(layoutJson).controls || []).filter((c) => c.type === 'table');
  const pathOf = (c) => c.stableIndex || [c.secIdx, c.paraIdx, c.controlIdx];
  const isTop = (c) => pathOf(c).length === 3 || pathOf(c).length === 6;
  const parentOf = (c) => {
    const path = pathOf(c);
    const cx = c.x + c.w / 2, cy = c.y + c.h / 2;
    const inside = (k) => cx >= k.x && cx <= k.x + k.w && cy >= k.y && cy <= k.y + k.h;
    // 중심을 품은 칸이 가장 작은 표가 부모(이중 중첩이면 가운데 표). 넓이가 같으면 칸 번호가 path[3]인 쪽.
    // 칸 번호만 믿지 않는다: 쪽을 넘긴 표는 rhwp가 칸 번호를 그 쪽 기준으로 다시 매기고,
    // 이중 중첩 표의 path[3]은 바깥 표의 칸 번호다.
    let best = null, area = Infinity, exact = false;
    for (const p of ctrls) {
      if (p === c) continue;
      for (const k of p.cells || []) {
        const a = k.w * k.h;
        if (!inside(k) || a < c.w * c.h * 0.9) continue;  // 자기보다 작은 칸은 부모가 아니다
        const ex = k.cellIdx === path[3];
        if (a < area - 0.01 || (Math.abs(a - area) <= 0.01 && ex && !exact)) { area = a; best = p; exact = ex; }
      }
    }
    return best;
  };
  const keys = new Map();
  const keyOf = (c, depth = 0) => {
    if (keys.has(c)) return keys.get(c);
    let key, parent = null;
    if (isTop(c)) key = pathOf(c).join('/');
    else {
      const p = depth < 16 ? parentOf(c) : null;
      parent = p ? keyOf(p, depth + 1) : null;
      key = `${parent || '?'}>${pathOf(c).join('/')}`;
    }
    keys.set(c, key);
    if (!map.tables.has(key)) {
      const path = pathOf(c);
      map.tables.set(key, { key, path, sec: path[0], top: isTop(c), rows: c.rowCount, cols: c.colCount, parent, py: null });
    }
    return key;
  };
  return ctrls.map((c) => ({ t: map.tables.get(keyOf(c)), x: c.x, y: c.y, w: c.w, h: c.h }));
}

function cmpChild(a, b) {
  const A = a.path, B = b.path;
  if (A[3] !== B[3]) return A[3] - B[3];
  if (A[1] !== B[1]) return A[1] - B[1];
  if (A[2] !== B[2]) return A[2] - B[2];
  for (let i = 4; i < Math.min(A.length, B.length); i++) if (A[i] !== B[i]) return A[i] - B[i];
  return A.length - B.length;
}

// 모든 쪽을 읽은 뒤 한 번 부른다. py = hwp.tables 요약(문서 순서). 짝 맞은 rhwp 표는 t.py 에 파이썬 표가 붙는다.
export function match(map, py, look = 4) {
  py.forEach((t, i) => { t.no = i + 1; });
  const all = [...map.tables.values()];
  const kids = new Map();
  for (const t of all) if (!t.top && t.parent) {
    if (!kids.has(t.parent)) kids.set(t.parent, []);
    kids.get(t.parent).push(t);
  }
  for (const list of kids.values()) list.sort(cmpChild);
  const walk = (t, out) => { out.push(t); for (const k of kids.get(t.key) || []) walk(k, out); return out; };
  for (const sec of new Set(py.map((t) => t.sec))) {
    const R = [];
    const k4 = (t) => [t.path[1], t.path[2], t.path[4] ?? -1, t.path[5] ?? -1];
    all.filter((t) => t.sec === sec && t.top)
      .sort((a, b) => { const A = k4(a), B = k4(b); return A[0] - B[0] || A[1] - B[1] || A[2] - B[2] || A[3] - B[3]; })
      .forEach((t) => walk(t, R));
    const P = py.filter((t) => t.sec === sec).sort((a, b) => a.index - b.index);
    let j = 0;
    for (const p of P) {
      for (let k = j; k < Math.min(R.length, j + look); k++) {
        const r = R[k];
        if (r.rows === p.rows && r.cols === p.cols && !r.top === !!p.nested) { r.py = p; j = k + 1; break; }
      }
    }
  }
  const mappedTo = new Map();
  for (const r of all) if (r.py) mappedTo.set(r.py, r);
  const byIdx = new Map(py.map((t) => [`${t.sec}/${t.index}`, t]));
  map.approx.clear();
  for (const t of py) {
    if (!t.nested || t.rank === null || t.rank === undefined || mappedTo.has(t)) continue;
    let p = t;
    for (let hop = 0; p && !mappedTo.has(p) && hop < 32; hop++) p = p.parent === null || p.parent === undefined ? null : byIdx.get(`${t.sec}/${p.parent}`);
    if (!p) continue;
    const key = mappedTo.get(p).key;
    if (!map.approx.has(key)) map.approx.set(key, []);
    map.approx.get(key).push(t);
  }
}
