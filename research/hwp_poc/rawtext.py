"""HWP5 바이너리에서 문단 글자(PARA_TEXT)와 줄 배치(PARA_LINE_SEG) 레코드를 직접 세어 본다."""
import sys, olefile, zlib, struct
o = olefile.OleFileIO(sys.argv[1]); fh = o.openstream('FileHeader').read(); comp = struct.unpack('<I', fh[36:40])[0] & 1
texts = []; nseg = 0; npara = 0; nlast = 0
for s in sorted([s for s in o.listdir() if s[0] == 'BodyText']):
    raw = o.openstream(s).read(); raw = zlib.decompress(raw, -15) if comp else raw
    i = 0
    while i + 4 <= len(raw):
        h = struct.unpack('<I', raw[i:i+4])[0]; i += 4; tag = h & 0x3FF; size = h >> 20
        if size == 0xFFF: size = struct.unpack('<I', raw[i:i+4])[0]; i += 4
        if tag == 66: npara += 1
        if tag == 67: texts.append(raw[i:i+size].decode('utf-16le', 'replace'))
        if tag == 69: nseg += 1
        i += size
print('paras', npara, 'para_text', len(texts), 'lineseg', nseg)
print([t for t in texts if '값' in t][:5])
