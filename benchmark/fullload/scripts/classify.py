"""Root-cause classes for every package that does not fully load with gold or with ours.
Uses the diagnostic re-run (diag-<map>.jsonl) when present, else the main-run rows.
usage: classify.py -> runs/causes.json"""
import json, re, collections, subprocess, functools
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
from fl_config import W
R = W / 'runs'; CP = W / 'CUE4Parse' / 'CUE4Parse'
from usmap_generator.engine import usmap as U   # the released generator's usmap reader (pip install -e <repo root>)

def rows(g, m, diag):
    d = {}
    for f in ([R / g / f'diag-{m}.jsonl', R / g / f'diag2-{m}.jsonl'] if diag else sorted((R / g).glob(f'{m}.*.jsonl'))):
        if not f.exists(): continue
        for line in open(f):
            try: r = json.loads(line); d[r['p']] = r
            except Exception: pass
    return d

def norm(msg):
    msg = msg.split('\n')[0]
    msg = re.sub(r'Path \'[^\']*\'', "Path '<p>'", msg)
    msg = re.sub(r'(for type|of type|named|type) [\w/.:<>]+', r'\1 <X>', msg)
    msg = re.sub(r'0x[0-9A-Fa-f]+|\d+', 'N', msg)
    return msg[:140]

def frame(fx):
    for f in fx.get('frames') or []:
        if f.startswith('CUE4Parse.'): return re.sub(r'\(.*', '', f)
    return None

def detail(r):
    """(pattern, export class, CUE4Parse frame, subject) for one failing row."""
    if r['st'] in ('crash', 'timeout'): return (r['st'], None, None, None)
    if r['st'] == 'ok' and r.get('soft'):
        k = next(iter(r['soft'])); kind, _, subj = k.partition(':')
        return ('soft:' + kind, None, None, subj)
    fx = (r.get('fx') or [{}])[0]
    chain = fx.get('chain') or []
    last = chain[-1] if chain else (r.get('err') or '')
    m = re.search(r'(?:for type|of type) ([\w/.]+)', last) or re.search(r'^\w+: ([\w]+): (?:Unknown property|Failed to serialize)', last)
    subj = m.group(1) if m else None
    if not chain and fx.get('mm'): return ('export end position mismatch', fx.get('type'), None, None)
    if not chain and r.get('mm'): return ('export end position mismatch', None, None, None)
    return (norm(last), fx.get('type'), frame(fx), subj)

@functools.lru_cache(None)
def exe_bytes(game):
    i = json.loads((W / 'maps' / f'{game}.json').read_text()); return Path(i['exe']).read_bytes()
def in_exe(game, name):
    b = exe_bytes(game); return name.encode() in b or name.encode('utf-16-le') in b
@functools.lru_cache(None)
def mapnames(game, m):
    e, s = U.read(W / 'maps' / f'{game}.{m}.usmap'); return {k.lower() for k in s}

@functools.lru_cache(None)
def where(cls):
    """CUE4Parse file that reads this export class, if any."""
    if not cls: return None
    c = cls.split('|')[-1]
    for pat in (rf'class U{c}\b', rf'class {c}\b'):
        out = subprocess.run(['rg', '-l', pat, str(CP)], capture_output=True, text=True).stdout.split()
        if out: return out[0].replace(str(W) + '/', '')
    return None

def main():
    games = [l for l in (W / 'games-order.txt').read_text().split() if (R / l / 'run.json').exists()]
    groups = {}; side_tot = collections.Counter(); fixed = collections.Counter(); fixed_ex = {}
    def harness_cause(r):
        e = (r.get('err') or '') + ' ' + ' '.join((r.get('exc') or {}).keys())
        if r.get('st') in ('crash', 'timeout'): return 'harness: process killed in main run (memory cap or stop), clean on re-run'
        if 'DllNotFound' in e or 'CUE4Parse-Natives' in e: return 'harness: ACL natives library missing in main run'
        if 'not a struct' in e or 'import' in e.lower(): return 'harness: virtual paths not loaded (plugin imports)'
        return 'harness: other, clean on re-run (' + (next(iter(r.get('exc') or {'?': 1}))) + ')'
    for g in games:
        info = json.loads((W / 'maps' / f'{g}.json').read_text())
        main_ = {m: rows(g, m, False) for m in ('gold', 'ours')}
        dia = {m: rows(g, m, True) for m in ('gold', 'ours')}
        for p, G in main_['gold'].items():
            G0, O0 = G, main_['ours'].get(p)
            G = dia['gold'].get(p, G); O = dia['ours'].get(p, O0)
            for m, a, b in (('gold', G0, G), ('ours', O0, O)):
                if a is not None and b is not None and not a.get('full') and b.get('full'):
                    k = (m, harness_cause(a)); fixed[k] += 1; fixed_ex.setdefault(k, f'{g} {p}')
            if O is None or (G.get('full') and O.get('full')): continue
            side = 'ours_fails_gold_full' if G.get('full') else 'gold_fails_ours_full' if O.get('full') else 'both_fail'
            side_tot[side] += 1
            bad = dia['ours'].get(p) or O if side == 'ours_fails_gold_full' else dia['gold'].get(p) or G
            pat, cls, fr, subj = detail(bad)
            same = None
            if side == 'both_fail':
                bo = dia['ours'].get(p) or O
                same = detail(bo)[0] == pat
            key = (side, pat, (cls or '').split('|')[-1] or None, fr)
            x = groups.setdefault(key, {'side': side, 'pattern': pat, 'class': cls, 'frame': fr, 'n': 0, 'games': collections.Counter(),
                                       'engines': collections.Counter(), 'subjects': collections.Counter(), 'examples': [], 'same_both': 0,
                                       'subj_in_gold': 0, 'subj_in_ours': 0, 'subj_in_exe': 0, 'subj_checked': 0})
            x['n'] += 1; x['games'][g] += 1; x['engines'][info['engine']] += 1
            if same: x['same_both'] += 1
            if subj:
                x['subjects'][subj] += 1
                if x['subj_checked'] < 50 and x['subjects'][subj] == 1:
                    x['subj_checked'] += 1
                    x['subj_in_gold'] += subj.lower() in mapnames(g, 'gold'); x['subj_in_ours'] += subj.lower() in mapnames(g, 'ours')
                    x['subj_in_exe'] += in_exe(g, subj)
            if len(x['examples']) < 3 and (not x['examples'] or x['examples'][-1]['game'] != g):
                x['examples'].append({'game': g, 'p': p, 'err': (bad.get('err') or '')[:200], 'soft': bad.get('soft'), 'mm': bad.get('mm'), 'cls': cls})
    out = []
    for x in groups.values():
        x['where'] = where(x['class']); x['games'] = dict(x['games'].most_common()); x['engines'] = dict(x['engines'])
        x['subjects'] = dict(x['subjects'].most_common(10)); out.append(x)
    out.sort(key=lambda x: -x['n'])
    fx_list = [{'map': m, 'cause': c, 'n': n, 'example': fixed_ex[(m, c)]} for (m, c), n in fixed.most_common()]
    (R / 'causes.json').write_text(json.dumps({'side_totals': side_tot, 'harness_fixed': fx_list, 'groups': out}, indent=1, default=str))
    return side_tot, out

if __name__ == '__main__':
    st, out = main(); print(dict(st))
    for x in out[:40]:
        print(x['n'], x['side'], x['pattern'][:70], x['class'], x['frame'], list(x['engines']), list(x['subjects'])[:3], x['where'], x['subj_in_gold'], x['subj_in_exe'], '/', x['subj_checked'])
