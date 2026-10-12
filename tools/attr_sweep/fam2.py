import json, collections, statistics
L=lambda f: json.load(open('/var/tmp/renau/exp/'+f))
base={r['test']:r for r in L('o_base.json') if r['status']=='ok'}
nb={}
for r in L('n_base.json'):
    if r['status']=='ok': nb.setdefault(r['test'],{})[r['tag']]=r
var=collections.defaultdict(dict)
for k in ('mult','add','shift'):
    for r in L(f'o_{k}.json'):
        if r['status']=='ok': var[r['test']][r['tag']]=r
agg=collections.defaultdict(list)
for t,vs in var.items():
    b=base.get(t)
    if not b or not b['area']: continue
    for tag,r in vs.items():
        if r['area'] and r['cells']!=b['cells']: agg[tag].append((r['area']/b['area'], r['sta']/b['sta'] if r['sta'] and b['sta'] else 1.0))
print("ops-targeted attribute  n  geo_area geo_delay  wins(<.97) losses(>1.03)")
for tag in sorted(agg):
    a=[x[0] for x in agg[tag]]; d=[x[1] for x in agg[tag]]
    print(f"{tag:14s} {len(a):3d}   {statistics.geometric_mean(a):.3f}    {statistics.geometric_mean(d):.3f}     {sum(x<.97 for x in a):3d}      {sum(x>1.03 for x in a):3d}")
rows=[]
for t,vs in var.items():
    b=base.get(t)
    if not b or not b['area']: continue
    c=[(b['area'],'base',b)]
    if t in nb and 'color' in nb[t] and nb[t]['color']['area']: c.append((nb[t]['color']['area'],'color',nb[t]['color']))
    for tag,r in vs.items():
        if r['area']: c.append((r['area'],tag,r))
    m=min(c,key=lambda x:x[0]); rows.append((m[0]/b['area'],t,m[1],b,m[2]))
rows.sort(key=lambda x:x[0])
print("\nbest-of: n=%d geomean area %.3f; improved>3%%: %d; worse: 0 (best-of includes base)"%(len(rows),statistics.geometric_mean([r[0] for r in rows]),sum(r[0]<.97 for r in rows)))
for ar,t,tag,b,m in rows[:20]: print(f"  x{ar:.2f} {t:32s} {tag:12s} cells {b['cells']:.0f}->{m['cells']:.0f} sta {b['sta']:.0f}->{m['sta']:.0f}")
print("winner tags:",collections.Counter(r[2] for r in rows if r[0]<.97).most_common())
