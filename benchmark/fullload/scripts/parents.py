"""Parent-class chain for every Blueprint class that fails with "Unknown property" (both maps).
Dumps the package that defines each class (diag binary, gold map) and follows SuperStruct until a /Script/ class.
The classifier uses this to decide "derives from FunctionalTest" from the parent class, not from the path.
usage: parents.py -> runs/parents.json  {game: {class_path: [super, super-of-super, ...]}}"""
import json, os, subprocess, sys, shutil, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); from gameargs import args
from fl_config import OODLE, ENV, W
from fl_config import BIN_DIAG as BIN
R = W / 'runs'
TMP = R / 'parents-work'

def pkg_file(allp, game_path):
    """/Game/A/B -> <Project>/Content/A/B.uasset as listed for this game."""
    tail = '/Content/' + game_path[len('/Game/'):] + '.uasset'
    hits = [p for p in allp if p.lower().endswith(tail.lower())]
    return hits[0] if hits else None

def dump(game, pkgs):
    out = TMP / game; shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    lst = out / 'list.txt'; lst.write_text('\n'.join(pkgs) + '\n')
    eg, d = args(game)
    cmd = ['systemd-run', '--user', '--scope', '-q', '-p', 'MemoryMax=12G', '-p', 'MemorySwapMax=0', 'nice', '-n', '19',
           str(BIN), '--paks', d, '--game', eg, '--oodle', OODLE, '--usmap', str(W / 'maps' / f'{game}.gold.usmap'),
           '--pkgtimeout', '900', '--vpaths', '1', '--dump', str(out / 'dump'), 'check', str(lst), str(out / 'rows.jsonl')]
    subprocess.run(cmd, env=ENV, stdout=subprocess.DEVNULL, stderr=open(out / 'err.log', 'w'))
    supers = {}
    for f in (out / 'dump').glob('*.json') if (out / 'dump').exists() else []:
        dec = json.JSONDecoder(); s = f.read_text(); i = 0
        while i < len(s):
            while i < len(s) and s[i].isspace(): i += 1
            if i >= len(s): break
            o, i = dec.raw_decode(s, i); d = o['data']
            if d.get('Type') == 'BlueprintGeneratedClass' and d.get('SuperStruct'):
                ss = d['SuperStruct']; nm = ss['ObjectName'].split("'")[1].split(':')[0]
                supers[d['Package'] + '|' + d['Name']] = ss['ObjectPath'] + '.' + nm
    return supers

def main():
    C = json.loads((R / 'causes.json').read_text())
    want = collections.defaultdict(set)
    for x in C['groups']:
        c = x['class'] or ''
        if 'Unknown property' in x['pattern'] and c.split('|')[-1].endswith('_C'):
            for g in x['games']: want[g].add(c)
    res = {}
    for g, classes in want.items():
        allp = list(dict.fromkeys(p for p in (R / g / 'all.txt').read_text().split('\n') if p))
        chain = {c: [] for c in classes}; todo = set(classes); known = {}
        for _ in range(6):   # follow Blueprint parents up to 6 levels
            pkgs = sorted({pkg_file(allp, c.split('|')[0]) for c in todo} - {None})
            if not pkgs: break
            known.update(dump(g, pkgs)); nxt = set()
            for c in todo:
                cur = c
                while cur in known:
                    sup = known[cur]; chain[c].append(sup) if sup not in chain[c] else None
                    cur = sup.replace('.', '|', 1) if sup.startswith('/Game/') else None
                    if cur is None: break
                if chain[c] and chain[c][-1].startswith('/Game/'):
                    p, n = chain[c][-1].rsplit('.', 1); nxt.add(p + '|' + n)
            todo = nxt - set(known)
            if not todo: break
        res[g] = chain
    (R / 'parents.json').write_text(json.dumps(res, indent=1))
    return res

if __name__ == '__main__':
    for g, ch in main().items():
        for c, s in sorted(ch.items()): print(g, c.split('|')[-1], '->', ' -> '.join(s) or '(not found)')
