// 사용법: node rhwp_check.mjs <파일...>  — rhwp(독립 구현)로 열어 쪽수·표 수 출력(JSON 줄)
import { readFileSync } from 'node:fs';
import path from 'node:path';
const here = path.dirname(new URL(import.meta.url).pathname);
const { existsSync } = await import('node:fs');
const modDir = process.env.RHWP_DIR || [path.join(here, 'node_modules/@rhwp/core'), path.join(here, '../npm/node_modules/@rhwp/core')].find(existsSync);
const { initSync, HwpDocument } = await import(path.join(modDir, 'rhwp.js'));
globalThis.measureTextWidth = (font, text) => { const m = /(\d+(?:\.\d+)?)px/.exec(font); const px = m ? +m[1] : 13; let w = 0; for (const ch of text) w += (ch.charCodeAt(0) > 0x2e80 ? 1 : 0.55) * px; return w; };
initSync({ module: readFileSync(path.join(modDir, 'rhwp_bg.wasm')) });
for (const f of process.argv.slice(2)) {
  try {
    const d = new HwpDocument(new Uint8Array(readFileSync(f)));
    const pages = d.pageCount();
    let svgOk = true; try { d.renderPageSvg(0); } catch (e) { svgOk = false; }
    console.log(JSON.stringify({ f, ok: true, pages, svgOk }));
  } catch (e) { console.log(JSON.stringify({ f, ok: false, err: String(e).slice(0, 120) })); }
}
