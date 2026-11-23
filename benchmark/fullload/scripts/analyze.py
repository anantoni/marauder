"""Merge per-shard results and write per-package jsonl + summary json.
usage: analyze.py  -> runs/merged/<game>.jsonl, runs/summary.json"""
import json, re, collections
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
from fl_config import W
R = W / 'runs'; M = R / 'merged'; M.mkdir(exist_ok=True)
MAPS = ('gold', 'ours', 'none')

def load(g, m):
    d = {}
    for f in sorted((R / g).glob(f'{m}.*.jsonl')):
        for line in open(f):
            try: r = json.loads(line)
            except Exception: continue
            d[r['p']] = r
    return d

def cause(r):
    """Cause class for a package that does not fully load with one map."""
    if r is None: return 'missing'
    st = r['st']
    if st in ('crash', 'timeout'): return st
    err = r.get('err') or ''
    if st == 'ok' and r.get('soft'):
        k = next(iter(r['soft'])).split(':')[0]
        return {'instrem': 'nested instanced struct read short/long', 'insterr': 'nested instanced struct exception',
                'instnotype': 'nested instanced struct type missing', 'tagrem': 'tagged property size mismatch',
                'tagerr': 'tagged property exception'}.get(k, 'soft:' + k)
    if 'Missing prop mappings' in err: return 'missing struct in map'
    if 'Unknown property with value' in err: return 'property index beyond map (struct too short)'
    if 'mapping file is missing' in err: return 'no map'
    if r.get('mm') and not r.get('exc'): return 'export end position mismatch'
    if 'Failed to serialize property' in err or 'FPropertyTagType' in err: return 'property read failed'
    exc = next(iter(r.get('exc', {'?': 1})))
    return f'exception {exc}'

def touched_struct(r):
    m = re.search(r'Missing prop mappings for type (\S+)', r.get('err') or '') or re.search(r'^(\S+): (Unknown property|Failed to serialize)', r.get('err') or '')
    return m.group(1) if m else None

def summarize(rows):
    c = collections.Counter()
    for x in rows:
        G, O, N = x['gold'], x['ours'], x['none']
        fg, fo, fn = bool(G and G.get('full')), bool(O and O.get('full')), bool(N and N.get('full'))
        c['n'] += 1; c['unv'] += bool((G or {}).get('unv') or (O or {}).get('unv'))
        c['full_gold'] += fg; c['full_ours'] += fo; c['full_none'] += fn
        c['ok_gold'] += bool(G and G['st'] == 'ok'); c['ok_ours'] += bool(O and O['st'] == 'ok'); c['ok_none'] += bool(N and N['st'] == 'ok')
        c['ours_only'] += fo and not fg; c['gold_only'] += fg and not fo
        if fg and fo:
            c['both'] += 1; c['eq_hn'] += G['hn'] == O['hn']; c['eq_h'] += G['h'] == O['h']; c['eq_hl'] += G['hl'] == O['hl']
        for m, r in (('gold', G), ('ours', O), ('none', N)):
            if r and r['st'] in ('crash', 'timeout'): c[f'{r["st"]}_{m}'] += 1
    return dict(c)

def main():
    games = [l for l in (W / 'games-order.txt').read_text().split() if (R / l / 'all.txt').exists()]
    out = {'games': {}, 'examples': {}}
    for g in games:
        info = json.loads((W / 'maps' / f'{g}.json').read_text())
        # dict.fromkeys: a game with two pak folders lists some paths twice (one install listed 534 Engine/ paths twice); count each package once
        allp = list(dict.fromkeys(x for x in (R / g / 'all.txt').read_text().split('\n') if x))
        D = {m: load(g, m) for m in MAPS}
        # packages not fully loaded in the main run were re-run with the diag binary (adds ACL natives); its row is final
        for m in ('gold', 'ours'):
            for f in (R / g / f'diag-{m}.jsonl', R / g / f'diag2-{m}.jsonl'):   # diag2 = single-process re-check of disagreements
              if f.exists():
                for line in open(f):
                    try: r = json.loads(line); r['rerun'] = True; D[m][r['p']] = r
                    except Exception: pass
        rows = []
        with open(M / f'{g}.jsonl', 'w') as f:
            for p in allp:
                x = {'p': p, **{m: D[m].get(p) for m in MAPS}}
                x['abp'] = any((x[m] or {}).get('abp') for m in MAPS)
                rows.append(x)
                rec = {'p': p, 'abp': x['abp']}
                for m in MAPS:
                    r = x[m]
                    rec[m] = None if r is None else {k: r.get(k) for k in ('st', 'full', 'ex', 'okx', 'unv', 'hn', 'h', 'hl', 'err', 'mm', 'soft', 'exc', 'ms') if r.get(k) is not None}
                f.write(json.dumps(rec) + '\n')
        n_all = len(rows); rows = [x for x in rows if x['gold'] and x['ours'] and x['none']]
        run = json.loads((R / g / 'run.json').read_text()) if (R / g / 'run.json').exists() else {}
        s = summarize(rows); s['abp'] = summarize([x for x in rows if x['abp']])
        s.update(split=info['split'], engine=info['engine'], status=run.get('status', 'running'),
                 total=n_all, unmeasured=n_all - len(rows))
        ex = []
        for x in rows:
            G, O = x['gold'], x['ours']
            fg, fo = bool(G and G.get('full')), bool(O and O.get('full'))
            if fg != fo:
                bad = O if fg else G
                ex.append({'game': g, 'dir': 'gold_only' if fg else 'ours_only', 'p': x['p'], 'abp': x['abp'], 'cause': cause(bad),
                           'struct': touched_struct(bad or {}), 'err': (bad or {}).get('err'), 'mm': (bad or {}).get('mm'), 'soft': (bad or {}).get('soft'),
                           'other_st': (G if not fg else O or {}).get('st') if False else None})
            elif fg and fo and G['hn'] != O['hn']:
                ex.append({'game': g, 'dir': 'data_diff', 'p': x['p'], 'abp': x['abp'], 'eq_hl': G['hl'] == O['hl']})
        out['games'][g] = s; out['examples'][g] = ex
    (R / 'summary.json').write_text(json.dumps(out, indent=1))
    return out

if __name__ == '__main__':
    o = main()
    tot = collections.Counter()
    for g, s in o['games'].items():
        for k, v in s.items():
            if isinstance(v, int): tot[k] += v
    print(len(o['games']), 'games', dict(tot))
