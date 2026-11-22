# compare_full.py OUTDIR : integration full-load rows vs the original full run (runs/merged, pinned CUE4Parse)
import json, glob, os, sys, collections
from fl_config import RUNS
out = sys.argv[1]; new = collections.defaultdict(dict)
for f in glob.glob(f'{out}/*.jsonl'):
    b = os.path.basename(f).split('.'); g, m = b[0], b[2]
    for l in open(f):
        try: x = json.loads(l)
        except Exception: continue
        new[(g, m)][x['p']] = x
games = sorted({g for g, _ in new})
tot = collections.Counter(); bad = []
for g in games:
    old = {json.loads(l)['p']: json.loads(l) for l in open(f'{RUNS}/merged/{g}.jsonl')}
    for m in ('gold', 'ours'):
        c = collections.Counter()
        for p, y in new[(g, m)].items():
            x = old.get(p, {}).get(m, {})
            if x.get('full') and y.get('full'): c['full_both'] += 1; c['same_h' if x.get('h') == y.get('h') else 'diff_h'] += 1; c['same_hl' if x.get('hl') == y.get('hl') else 'diff_hl'] += 1
            elif x.get('full'): c['REGRESSION'] += 1; bad.append((g, m, p, y.get('st'), y.get('mm'), y.get('soft'), (y.get('err') or '')[:120]))
            elif y.get('full'): c['fixed'] += 1
            else: c['still_not_full'] += 1
        tot.update({f'{m}:{k}': v for k, v in c.items()})
        print(g, m, dict(c))
print('TOTAL', dict(sorted(tot.items())))
for z in bad[:40]: print('REG', z)
