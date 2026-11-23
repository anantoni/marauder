"""All-games comparison of a reader-fix reload against the pinned full run (the paper's all-121 numbers).
usage: compare_all.py OUT ROWDIR [ROWDIR ...]
Each ROWDIR holds harness rows named <game>.<chunk>.<map>.jsonl (map = gold or ours), as written by run_par.py,
run_list.py or run_list2.py. Rows are merged in the order given: a later ROWDIR replaces a package's row from an
earlier one (for example: the 40-game rerun, then the reload of the other games, then a rerun of single packages);
inside one ROWDIR, chunk files come first and single-package retry files (chunk name with an 's') after.
Writes OUT/<game>.0.<map>.jsonl (the merged rows), OUT.cmp.json and OUT.pergame.json, and prints:
regressions (must be 0), newly full per map, load result and data equality between the two maps, packages whose
exact hash changed against the pinned run, and the packages still not full grouped by side and owner class
(the classes of classify.py / make_report.py; run both on the pinned run first)."""
import json, glob, os, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fl_config import RUNS
from classify import detail
import make_report as MR

out = sys.argv[1].rstrip('/'); dirs = sys.argv[2:]
if not dirs: sys.exit(__doc__)
os.makedirs(out, exist_ok=True)
rows = collections.defaultdict(dict)
def order(f): return ('s' in os.path.basename(f).split('.')[1], f)
for d in dirs:
    for f in sorted(glob.glob(f'{d}/*.jsonl'), key=order):
        b = os.path.basename(f).split('.')
        if len(b) < 4 or b[2] not in ('gold', 'ours'): continue
        g, m = b[0], b[2]
        for l in open(f):
            try: x = json.loads(l)
            except Exception: continue
            rows[(g, m)][x['p']] = x
for (g, m), d in rows.items():
    with open(f'{out}/{g}.0.{m}.jsonl', 'w') as o:
        for x in d.values(): o.write(json.dumps(x) + '\n')

catmap = {}
for x in MR.C['groups']:
    cat, why = MR.category(x)
    catmap.setdefault((x['pattern'], (x['class'] or '').split('|')[-1] or None), (cat, why))
def category(r):
    pat, cls, _, _ = detail(r); k = (pat, (cls or '').split('|')[-1] or None)
    for kk in (k, (pat, None)):
        if kk in catmap: return catmap[kk][0] + ' | ' + catmap[kk][1][:70]
    return 'unclassified | ' + pat[:90] + ' | ' + str(cls)

games = sorted({g for g, _ in rows})
tot = collections.Counter(); reg = []; still = collections.Counter(); stillex = {}; hd = []; missing = []; per = {}
for g in games:
    old = {}
    for l in open(RUNS / 'merged' / f'{g}.jsonl'):
        x = json.loads(l); old.setdefault(x['p'], x)
    c = collections.Counter()
    for p, o in old.items():
        n = {m: rows[(g, m)].get(p) for m in ('gold', 'ours')}
        if n['gold'] is None or n['ours'] is None: missing.append((g, p)); c['missing'] += 1; continue
        c['pkgs'] += 1
        for m in ('gold', 'ours'):
            x, y = o.get(m) or {}, n[m]
            if x.get('full') and y.get('full'):
                c[m + ':full_both'] += 1; c[m + (':same_h' if x.get('h') == y.get('h') else ':diff_h')] += 1
                c[m + (':same_hn' if x.get('hn') == y.get('hn') else ':diff_hn')] += 1
                if x.get('h') != y.get('h'): hd.append((g, m, p))
            elif x.get('full'): c[m + ':REGRESSION'] += 1; reg.append((g, m, p, y.get('st'), y.get('mm'), y.get('soft'), (y.get('err') or '')[:140]))
            elif y.get('full'): c[m + ':newly_full'] += 1
            else: c[m + ':still_not_full'] += 1
            c[m + ':full_now'] += bool(y.get('full'))
        G, O = n['gold'], n['ours']
        if G.get('full') and O.get('full'):
            c['both_full'] += 1; c['eq_hn'] += G['hn'] == O['hn']; c['eq_h'] += G['h'] == O['h']; c['eq_hl'] += G['hl'] == O['hl']
        else:
            side = 'gold_only' if G.get('full') else 'ours_only' if O.get('full') else 'both_fail'
            c[side] += 1
            bad = O if side == 'gold_only' else G
            cat = category(bad); still[(side, cat)] += 1; stillex.setdefault((side, cat), (g, p, bad.get('st'), (bad.get('err') or '')[:100]))
    per[g] = c; tot.update(c)
json.dump({'tot': tot, 'reg': reg, 'missing': missing[:50], 'still': [[list(k), v, stillex[k]] for k, v in still.most_common()], 'hash_diff': hd},
          open(out + '.cmp.json', 'w'), indent=1)
json.dump({g: dict(c) for g, c in per.items()}, open(out + '.pergame.json', 'w'), indent=1)
print('games', len(games), 'missing', len(missing))
for k, v in sorted(tot.items()): print(k, v)
print('REGRESSIONS', len(reg))
for z in reg[:40]: print('REG', z)
print('STILL NOT FULL (by side and class)')
for (s, cat), v in still.most_common(): print(v, s, cat, stillex[(s, cat)])
