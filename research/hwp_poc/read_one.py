"""한 파일을 여러 방법으로 읽어 표 통계를 JSON 한 줄로 출력 (별도 프로세스에서 실행)."""
import sys, json, zipfile, zlib, struct, io, re, time
p = sys.argv[1]; mode = sys.argv[2]
out = {"file": p.split('/')[-1]}
HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'

def grid_stats_xml(xml_bytes_list):
    from lxml import etree
    n = nested = merged_tbl = 0; sizes = []
    for xb in xml_bytes_list:
        root = etree.fromstring(xb, etree.XMLParser(resolve_entities=False, huge_tree=True))
        for t in root.iter(HP + 'tbl'):
            n += 1
            if any(a.tag == HP + 'tc' for a in t.iterancestors()): nested += 1
            m = 0
            for sp in t.iter(HP + 'cellSpan'):
                if int(sp.get('rowSpan', 1)) > 1 or int(sp.get('colSpan', 1)) > 1: m += 1
            if m: merged_tbl += 1
            sizes.append((int(t.get('rowCnt', 0)), int(t.get('colCnt', 0))))
    return dict(tables=n, nested=nested, merged_tables=merged_tbl, big=sum(1 for r, c in sizes if r >= 3 and c >= 2))

def sections_from_zip(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    names = sorted([x for x in z.namelist() if re.match(r'Contents/section\d+\.xml$', x)], key=lambda s: int(re.findall(r'\d+', s)[0]))
    return [z.read(x) for x in names]

data = open(p, 'rb').read()
if mode == 'hwpx':
    t = time.time()
    try:
        out['own'] = grid_stats_xml(sections_from_zip(data)); out['own_ok'] = True
    except Exception as e:
        out['own_ok'] = False; out['own_err'] = type(e).__name__ + ':' + str(e)[:80]
    out['own_ms'] = int((time.time() - t) * 1000)
    try:
        from hwpx.table_patch import table_summary
        s = table_summary(data); out['phx_ok'] = True; out['phx_tables'] = len(s)
    except Exception as e:
        out['phx_ok'] = False; out['phx_err'] = type(e).__name__ + ':' + str(e)[:80]
elif mode == 'hwp':
    # (a) olefile + 레코드 직접 파싱: HWPTAG_TABLE(77) 개수
    try:
        import olefile
        o = olefile.OleFileIO(io.BytesIO(data))
        fh = o.openstream('FileHeader').read()
        flags = struct.unpack('<I', fh[36:40])[0]
        ver = struct.unpack('<I', fh[32:36])[0]
        out['ver'] = f"{ver>>24}.{(ver>>16)&255}.{(ver>>8)&255}.{ver&255}"
        out['password'] = bool(flags & 2); out['distribution'] = bool(flags & 4)
        secs = sorted([s for s in o.listdir() if s[0] == 'BodyText'], key=lambda s: int(s[1][7:]))
        cnt = 0
        for s in secs:
            raw = o.openstream(s).read()
            if flags & 1: raw = zlib.decompress(raw, -15)
            i = 0
            while i + 4 <= len(raw):
                h = struct.unpack('<I', raw[i:i+4])[0]; i += 4
                tag = h & 0x3FF; size = h >> 20
                if size == 0xFFF: size = struct.unpack('<I', raw[i:i+4])[0]; i += 4
                if tag == 77: cnt += 1
                i += size
        out['raw_ok'] = True; out['raw_tables'] = cnt
    except Exception as e:
        out['raw_ok'] = False; out['raw_err'] = type(e).__name__ + ':' + str(e)[:80]
    # (b) python-hwpx: HWP5 -> HWPX 변환 후 표 통계
    t = time.time()
    try:
        from hwpx.hwp5.package import convert
        c = convert(data)
        secs = [v for k, v in sorted(c.files.items()) if re.match(r'Contents/section\d+\.xml$', k)]
        out['phx'] = grid_stats_xml(secs); out['phx_ok'] = True
        out['phx_unconverted'] = sorted(set(map(str, getattr(c.report, 'unconverted', []) or [])))[:5]
        out['phx_dropped'] = sorted(set(map(str, getattr(c.report, 'dropped', []) or [])))[:5]
    except Exception as e:
        out['phx_ok'] = False; out['phx_err'] = type(e).__name__ + ':' + str(e)[:100]
    out['phx_ms'] = int((time.time() - t) * 1000)
print(json.dumps(out, ensure_ascii=False))
