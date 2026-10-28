import csv, os, sys, re, shutil, hashlib
A='/Users/o0o0o/moon/marketing/goteacher-forms-archive'
rows=list(csv.DictReader(open(A+'/files.tsv',encoding='utf-8'),delimiter='\t'))
kw=re.compile(r'명단|명부|현황|희망|수요조사|이수|조사서|집계|신청서|제출|기초자료|임원|참가자|실적')
seen=set(); out=[]
for r in rows:
    src=r['bf_source']; f=r['bf_file']
    ext=f.rsplit('.',1)[-1].lower()
    if ext not in('hwp','hwpx'): continue
    if not kw.search(src): continue
    p=None
    for d in('goteacher','free_school'):
        q=f'{A}/raw/{d}/{f}'
        if os.path.exists(q): p=q;break
    if not p: continue
    h=hashlib.md5(open(p,'rb').read()).hexdigest()
    if h in seen: continue
    seen.add(h); out.append((ext,src,p,os.path.getsize(p)))
print(len(out), sum(1 for o in out if o[0]=='hwpx'))
for o in out: print(o[0],o[3],o[1],o[2],sep="\t")
