"""Write RESULTS-fullload.md tables from runs/summary.json + runs/causes.json (run analyze.py and classify.py first).
The plain summary at the top is written by hand from these numbers; this script writes everything below it to
runs/report-tables.md."""
import json, re, subprocess, functools, collections
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parent))
from fl_config import W
R = W / 'runs'; CP = W / 'CUE4Parse' / 'CUE4Parse'
S = json.loads((R / 'summary.json').read_text()); C = json.loads((R / 'causes.json').read_text())
P = json.loads((R / 'parents.json').read_text()) if (R / 'parents.json').exists() else {}   # parents.py: Blueprint super chains

def derives_from_functional_test(x):
    """True when the failing Blueprint class's parent chain reaches /Script/FunctionalTesting.FunctionalTest (review 22 Nov: was a path rule)."""
    return any('/Script/FunctionalTesting.FunctionalTest' in P.get(g, {}).get(x['class'] or '', []) for g in x['games'])
THIRD_PARTY = {'SubstanceCore', 'HoudiniEngineRuntime', 'PaperZD', 'Flow', 'AkAudio'}   # AkAudio = Wwise plugin (review 22 Nov: was (b))
ENGINE_HINT = {'Engine', 'GeometryFramework', 'HairStrandsCore', 'Landscape', 'Niagara', 'GeometryCollectionEngine', 'ControlRig',
               'DatasmithContent', 'OpenColorIO', 'FieldSystemEngine', 'InterchangePipelines', 'WorldPartitionHLODUtilities'}

@functools.lru_cache(None)
def find_type(name):
    out = subprocess.run(['rg', '-l', rf'(class|struct) {re.escape(name)}\b', str(CP)], capture_output=True, text=True).stdout.split()
    return out[0].replace(str(W / 'CUE4Parse') + '/', '') if out else None

def fix_location(x):
    if x['pattern'].startswith('soft:tag'):
        return 'CUE4Parse/UE4/Assets/Objects/FPropertyTag.cs (tagged FPropertyTag ctor: value read vs tag Size) and the container readers UScriptArray/UScriptSet/UScriptMap.cs'
    if x['frame']:
        parts = x['frame'].split('.')
        meth = parts[-1] if parts[-1] else parts[-2]
        typ = [p for p in parts if p and p[0].isupper()][-2] if len([p for p in parts if p and p[0].isupper()]) >= 2 else None
        f = find_type(typ) if typ else None
        if f: return f'{f} ({typ}.{meth})'
    if x.get('where'): return x['where'].replace('CUE4Parse/CUE4Parse/', 'CUE4Parse/')
    cls = (x['class'] or '').split('|')[-1]
    return f'no reader for {cls}: add one (falls back to UObject.Deserialize)' if cls else '-'

def module(x):
    c = x['class'] or ''
    if '|' not in c: return ''
    m = c.split('|')[0]
    return m[len('/Script/'):] if m.startswith('/Script/') else m

def category(x):
    pat, cls, side = x['pattern'], (x['class'] or ''), x['side']
    mod = module(x); subj_gold = x['subj_in_gold']; subj_exe = x['subj_in_exe']; vers = sorted(x['engines'])
    if side == 'ours_fails_gold_full': return '(a) our usmap', 'ours fails where gold fully loads'
    if pat in ('crash', 'timeout'): return '(b) CUE4Parse reader', f'process {pat} on this package after retries (memory or runaway read)'
    if 'DllNotFound' in pat: return 'harness', 'ACL natives missing (should not remain after re-run)'
    if 'OutOfMemory' in pat or 'Overflow' in pat and 'Groom' in cls: return '(b) CUE4Parse reader', 'runaway size read in bulk data'
    if 'Missing prop mappings' in pat:
        if subj_gold == 0 and subj_exe == 0: return 'skip: impossible', 'class code is not in the shipped executable (editor or test module); no map can describe it'
        if subj_gold == 0: return 'extra: static opportunity', 'class name is in the executable but not in the runtime map'
        return '(b) CUE4Parse reader', 'struct present in map but lookup failed'
    if 'Unknown property' in pat and cls.split('|')[-1].endswith('_C') and derives_from_functional_test(x):
        return 'skip: impossible', 'Blueprint derives from FunctionalTest (FunctionalTesting module not shipped); parent layout unknown to any map'
    if 'not a struct' in pat: return '(b) CUE4Parse reader', 'import of the class did not resolve (loose cooked files)'
    if pat.startswith('soft:tag'): return '(b) CUE4Parse reader', 'tagged container/struct property read short or failed (CUE4Parse skips it silently)'
    if mod in THIRD_PARTY or mod.split('/')[0] in THIRD_PARTY: return '(c) game/plugin custom serialization', f'third-party plugin {mod}'
    if mod and not mod.startswith('/Game') and mod not in ENGINE_HINT and len(x['games']) == 1:
        return '(c) game/plugin custom serialization', f'game or plugin module {mod}'
    if cls.split('|')[-1].startswith(('PABP_', 'ABP_')) and 'mismatch' in pat:
        return '(c) game/plugin custom serialization', 'PaperZD animation Blueprint (third-party plugin class with native data)'
    if vers == ['5.7']: return '(d) engine version support', 'only seen on UE 5.7 (newest engine; CUE4Parse support incomplete)'
    if len(vers) == 1: return '(b) CUE4Parse reader', f'only seen on UE {vers[0]} (may be version support, d)'
    return '(b) CUE4Parse reader', 'engine class read incorrectly across versions'

def pct(a, b): return f'{100 * a / b:.3f}%' if b else '-'

def tot(games, key=None):
    t = collections.Counter()
    for g in games:
        s = S['games'][g] if key is None else S['games'][g][key]
        for k, v in s.items():
            if isinstance(v, int): t[k] += v
    return t

def headline(t):
    return (f"| {t['n']:,} | {t['full_gold']:,} | {t['full_ours']:,} | {t['full_none']:,} | "
            f"{pct(t['full_gold'] - t['gold_only'], t['full_gold'])} | {t['gold_only']} | {t['ours_only']} | "
            f"{pct(t['eq_hn'], t['both'])} | {pct(t['eq_h'], t['both'])} |")

def main():
    games = list(S['games']); out = []
    splits = {'dev': [], 'hold': [], 'fresh': []}
    for g in games: splits[S['games'][g]['split']].append(g)
    out.append('## Headline table\n')
    out.append('Recall = packages that fully load with gold and also fully load with ours, over packages that fully load with gold. Data equal = same hash of the full JSON output with object keys lowercased (values untouched). Exact = same hash with keys as written.\n')
    out.append('| Split | Games | Packages | Full: gold | Full: ours | Full: no map | Recall vs gold | Gold full, ours not | Ours full, gold not | Data equal (both full) | Exact keys equal |')
    out.append('|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|')
    for sp, gs in list(splits.items()) + [('all', games)]:
        out.append(f'| {sp} | {len(gs)} ' + headline(tot(gs)))
    out.append('\n### Anim Blueprint packages (separate column)\n')
    out.append('Packages holding an AnimBlueprintGeneratedClass (detected from the loaded export or an Anim generated-struct lookup).\n')
    out.append('| Split | Packages | Full: gold | Full: ours | Full: no map | Recall vs gold | Gold full, ours not | Ours full, gold not | Data equal | Exact keys equal |')
    out.append('|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|')
    for sp, gs in list(splits.items()) + [('all', games)]:
        out.append(f'| {sp} ' + headline(tot(gs, 'abp')))
    # causes
    groups = C['groups']; cat_tot = collections.Counter(); cat_side = collections.Counter()
    for x in groups:
        x['cat'], x['why'] = category(x); cat_tot[x['cat']] += x['n']; cat_side[(x['cat'], x['side'])] += x['n']
    out.append('\n## Packages not fully loaded with gold or ours, by owner\n')
    out.append(f"Sides: {dict(C['side_totals'])}.\n")
    out.append('| Owner | Packages | of which gold fails | of which ours fails, gold full |')
    out.append('|---|--:|--:|--:|')
    for k, n in cat_tot.most_common():
        gf = sum(v for (c, s), v in cat_side.items() if c == k and s != 'ours_fails_gold_full')
        of = cat_side.get((k, 'ours_fails_gold_full'), 0)
        out.append(f'| {k} | {n:,} | {gf:,} | {of} |')
    if C.get('harness_fixed'):
        out.append('\nFixed by the diag re-run (same for both maps; counted as fully loaded in the tables above):\n')
        out.append('| Map | Harness cause | Packages | Example |'); out.append('|---|---|--:|---|')
        for h in C['harness_fixed']:
            out.append(f"| {h['map']} | {h['cause']} | {h['n']:,} | `{h['example']}` |")
    # merge groups into root causes (owner, why, class, pattern)
    roots = {}
    for x in groups:
        cls = (x['class'] or '').split('|')[-1]
        if cls.endswith('_C'): cls = '<Blueprint class>'
        if x['pattern'].startswith('soft:'): cls = 'tagged ' + '/'.join(sorted(x['subjects']))[:60]
        k = (x['cat'], x['why'], cls, re.sub(r'\b\w+_C\b', '<BP>', x['pattern'])[:90])
        r = roots.setdefault(k, {'n': 0, 'games': set(), 'engines': set(), 'ex': [], 'fix': fix_location(x), 'side': collections.Counter(), 'mod': '' if cls == '<Blueprint class>' else module(x)})
        r['n'] += x['n']; r['games'] |= set(x['games']); r['engines'] |= set(x['engines']); r['side'][x['side']] += x['n']
        for e in x['examples']:
            if len(r['ex']) < 2: r['ex'].append(f"{e['game']}: `{e['p']}`")
    out.append('\n## Root causes (one row per distinct cause)\n')
    out.append('| # | Owner | Cause | Export class (module) | Error pattern | Packages | Games | UE versions | Where to fix in CUE4Parse | Examples |')
    out.append('|--:|---|---|---|---|--:|--:|---|---|---|')
    rl = sorted(roots.items(), key=lambda kv: -kv[1]['n'])
    for i, ((cat, why, cls, pat), r) in enumerate(rl, 1):
        out.append(f"| {i} | {cat} | {why} | {cls} ({r['mod']}) | {pat[:90].replace('|', '/')} | {r['n']:,} | {len(r['games'])} | {', '.join(sorted(r['engines']))} | {r['fix'] if cat.startswith('(b)') or cat.startswith('(d)') else '-'} | {'; '.join(r['ex'])} |")
    (R / 'roots.json').write_text(json.dumps([{'owner': c, 'cause': w, 'class': cl, 'pattern': p, 'n': r['n'], 'games': sorted(r['games']),
                                                'engines': sorted(r['engines']), 'fix': r['fix'], 'examples': r['ex']} for (c, w, cl, p), r in rl], indent=1))
    # upstream drafts
    out.append('\n## Upstream issue drafts for CUE4Parse (no game names, no game files)\n')
    n = 0
    for (cat, why, cls, pat), r in rl:
        if not (cat.startswith('(b)') or cat.startswith('(d)')) or cls in ('', '<Blueprint class>') and not pat.startswith('soft'): continue
        n += 1
        vers = ', '.join(sorted(r['engines']))
        out.append(f"**Draft {n}: {cls or 'tagged property'} - {pat[:80]}**  ")
        out.append(f"Seen in {r['n']} cooked packages across {len(r['games'])} titles, UE {vers}. Packages are unversioned (zen/IoStore or pak) unless UE 4.x tagged; "
                   f"a correct runtime usmap is supplied, so property layout is not the cause. Symptom: {why}; error `{pat[:120]}`. "
                   f"Likely fix location: `{r['fix']}`. Repro pattern: load any cooked `{cls}` export from a UE {vers.split(', ')[0]} title with Globals.FatalObjectSerializationErrors = true "
                   f"and check that the archive ends at the export's serial size.\n")
    # per game
    out.append('\n## Per game\n')
    out.append('| Game | Split | UE | Packages | Unversioned | Full: gold | Full: ours | Full: no map | Recall | Gold full, ours not | Ours full, gold not | Data equal | ABP pkgs | ABP recall |')
    out.append('|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|')
    for g in sorted(games, key=lambda g: (S['games'][g]['split'], g)):
        s = S['games'][g]; a = s['abp']
        out.append(f"| {g} | {s['split']} | {s['engine']} | {s['n']:,} | {s['unv']:,} | {s['full_gold']:,} | {s['full_ours']:,} | {s['full_none']:,} | "
                   f"{pct(s['full_gold'] - s['gold_only'], s['full_gold'])} | {s['gold_only']} | {s['ours_only']} | {pct(s['eq_hn'], s['both'])} | "
                   f"{a.get('n', 0)} | {pct(a.get('full_gold', 0) - a.get('gold_only', 0), a.get('full_gold', 0))} |")
    (R / 'report-tables.md').write_text('\n'.join(out) + '\n')
    print('\n'.join(out[:30]))

if __name__ == '__main__':
    main()
