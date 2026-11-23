"""Full-load table with the reader fixes (the paper's all-games table), per split.
usage: fullfix_table.py MERGED      (MERGED = the OUT folder of compare_all.py)
'Impossible' packages are the ones the classifier puts in 'skip: impossible' (class code in neither the shipped
executable nor the runtime dump, or a Blueprint deriving from the functional-testing base class): each package that
fails with both maps in the pinned run is matched to its root-cause group in runs/causes.json the same way
classify.py builds the groups, and the group's class comes from make_report.category. Run analyze.py, classify.py,
parents.py and classify.py again on the pinned run first. Loadable = packages - impossible."""
import json, glob, os, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fl_config import RUNS
from classify import rows as shard_rows, detail
import make_report as MR

merged = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else sys.exit(__doc__)
cat = {}
for x in MR.C['groups']:
    cat[(x['side'], x['pattern'], (x['class'] or '').split('|')[-1] or None, x['frame'])] = MR.category(x)[0]
def impossible(g):
    """Paths of game g that fail with both maps in the pinned run and fall in a 'skip: impossible' group."""
    main = {m: shard_rows(g, m, False) for m in ('gold', 'ours')}; dia = {m: shard_rows(g, m, True) for m in ('gold', 'ours')}
    out = set()
    for p, G in main['gold'].items():
        O = dia['ours'].get(p, main['ours'].get(p)); G = dia['gold'].get(p, G)
        if O is None or G.get('full') or O.get('full'): continue
        pat, cls, fr, _ = detail(G)
        if cat.get(('both_fail', pat, (cls or '').split('|')[-1] or None, fr)) == 'skip: impossible': out.add(p)
    return out
new = collections.defaultdict(dict)
for f in glob.glob(f'{merged}/*.jsonl'):
    b = os.path.basename(f).split('.'); g, m = b[0], b[2]
    for l in open(f):
        try: x = json.loads(l)
        except Exception: continue
        new[(g, m)][x['p']] = x
split = {g: v['split'] for g, v in MR.S['games'].items()}
C = collections.defaultdict(collections.Counter); ngames = collections.Counter()
for g in sorted({g for g, _ in new}):
    s = split[g]; ngames[s] += 1; imp = impossible(g); seen = set(); c = C[s]
    for l in open(RUNS / 'merged' / f'{g}.jsonl'):
        x = json.loads(l)
        if x['p'] in seen: continue
        seen.add(x['p']); c['packages'] += 1
        c['full_pinned'] += x['gold']['full'] and x['ours']['full']
        c['impossible'] += x['p'] in imp
        a, b = new[(g, 'gold')].get(x['p']), new[(g, 'ours')].get(x['p'])
        if a is None or b is None: c['not_rerun'] += 1; continue
        c['full_fixed'] += a['full'] and b['full']
T = collections.Counter()
for s in list(C): T.update(C[s])
C['all'] = T; ngames['all'] = sum(ngames.values())
print('split games packages impossible loadable full_pinned full_fixed pinned% fixed% fixed%_all_packages not_rerun')
for s in [k for k in C if k != 'all'] + ['all']:
    c = C[s]; L = c['packages'] - c['impossible']
    print(s, ngames[s], c['packages'], c['impossible'], L, c['full_pinned'], c['full_fixed'],
          '%.3f' % (100 * c['full_pinned'] / L), '%.3f' % (100 * c['full_fixed'] / L), '%.3f' % (100 * c['full_fixed'] / c['packages']), c['not_rerun'])
