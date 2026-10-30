// node run_tablemap.mjs <tablemap.js> <고정 입력.json>  — test_tablemap.py가 부른다.
// 고정 입력: { pages: [쪽마다 getPageControlLayout 의 표 컨트롤], py: hwp.tables 요약 }. 결과를 JSON 한 줄로.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const [, , lib, fixture] = process.argv;
const { newMap, scanPage, match } = await import(pathToFileURL(lib).href);
const fx = JSON.parse(readFileSync(fixture, 'utf8'));
const map = newMap();
const pages = fx.pages.map((controls) => scanPage(map, JSON.stringify({ controls })));
const py = fx.py.map((t) => ({ ...t }));
match(map, py);
const idx = (p) => (p ? p.index : null);
console.log(JSON.stringify({
  tables: [...map.tables.values()].map((t) => ({ key: t.key, top: t.top, parent: t.parent, py: idx(t.py) })),
  pages: pages.map((fr) => fr.map((f) => ({ key: f.t.key, py: idx(f.t.py) }))),
  approx: Object.fromEntries([...map.approx].map(([k, v]) => [k, v.map(idx)])),
}));
