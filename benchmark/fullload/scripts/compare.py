# compare.py BASEDIR FIXDIR : per map, previously-failing -> full? and full-before -> identical h/hn/hl?
import json, sys, glob, os
from collections import Counter
b, f = sys.argv[1], sys.argv[2]
def load(d, m):
    r = {}
    for fn in glob.glob(f'{d}/*.{m}.jsonl'):
        g = os.path.basename(fn).split('.')[0]
        for l in open(fn):
            try: x = json.loads(l)
            except Exception: continue
            r[(g, x['p'])] = x
    return r
for m in ('gold', 'ours'):
    B, F = load(b, m), load(f, m); c = Counter(); bad = []
    for k, x in B.items():
        y = F.get(k)
        if y is None: c['missing'] += 1; continue
        if x.get('full'):
            same = all(x.get(h) == y.get(h) for h in ('h', 'hn', 'hl')) and y.get('full')
            c['full_before_same' if same else 'REGRESSION'] += 1
            if not same: bad.append((k, x.get('h'), y.get('h'), y.get('st'), y.get('err')))
        else:
            c['fail_before_now_full' if y.get('full') else 'fail_before_still_fail'] += 1
            if not y.get('full'): bad.append((k, 'still', y.get('st'), y.get('soft'), y.get('mm'), (y.get('err') or '')[:150]))
    print(m, dict(c))
    for z in bad[:15]: print('  ', z)
