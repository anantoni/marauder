"""Finish the diag pass for one game with restarts: kill-artefact crash rows are dropped and retried once more;
a package that crashes on two attempts keeps its crash row. usage: diag_retry.py <game> <map>"""
import json, subprocess, sys, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from gameargs import args
from fl_config import OODLE, ENV, W
from fl_config import BIN_DIAG as BIN
game, m = sys.argv[1:3]; g = W / 'runs' / game; eg, d = args(game)
out = g / f'diag-{m}.jsonl'; att = g / f'diag-{m}.attempts.json'
want = [x for x in (g / 'diag.txt').read_text().split('\n') if x]
A = json.loads(att.read_text()) if att.exists() else {}
if out.exists() and not (g / f'diag-{m}.jsonl.orig').exists(): shutil.copy(out, g / f'diag-{m}.jsonl.orig')
for it in range(400):
    rows = [json.loads(l) for l in open(out)] if out.exists() else []
    keep = []
    for r in rows:
        if r['st'] == 'crash' and A.get(r['p'], 0) < 2: A[r['p']] = A.get(r['p'], 0) + 1; continue   # retry
        keep.append(r)
    with open(out, 'w') as f:
        for r in keep: f.write(json.dumps(r) + '\n')
    att.write_text(json.dumps(A))
    if len({r['p'] for r in keep}) >= len(want): break
    cur = Path(str(out) + '.cur')
    if cur.exists():
        c = cur.read_text().strip()
        if c and A.get(c, 0) < 2: A[c] = A.get(c, 0) + 1; cur.write_text(''); att.write_text(json.dumps(A))
    cmd = [str(BIN), '--paks', d, '--game', eg, '--oodle', OODLE, '--usmap', str(W / 'maps' / f'{game}.{m}.usmap'),
           '--pkgtimeout', '1800', '--diag', '1', '--vpaths', '1', 'check', str(g / 'diag.txt'), str(out)]
    rc = subprocess.run(cmd, stdout=open(g / f'diag-{m}.log', 'a'), stderr=subprocess.STDOUT, env=ENV).returncode
    print(game, m, 'iter', it, 'rc', rc, flush=True)
(g / f'diag-{m}.done').write_text('')
print(game, m, 'done', flush=True)
