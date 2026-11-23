"""Diagnostic re-run (bin-diag: class module, exception chain, stack frames) of every package that did not
fully load with gold or ours in the main run. usage: diag_game.py <game>"""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from gameargs import args
from fl_config import OODLE, ENV, W
from fl_config import BIN_DIAG as BIN
game = sys.argv[1]; g = W / 'runs' / game; eg, d = args(game)
bad = set()
for m in ('gold', 'ours'):
    for f in g.glob(f'{m}.*.jsonl'):
        for line in open(f):
            r = json.loads(line)
            if not r.get('full'): bad.add(r['p'])
(g / 'diag.txt').write_text('\n'.join(sorted(bad)) + '\n')
procs = []
for m in ('gold', 'ours'):
    if not bad or (g / f'diag-{m}.done').exists(): continue
    cmd = [str(BIN), '--paks', d, '--game', eg, '--oodle', OODLE, '--usmap', str(W / 'maps' / f'{game}.{m}.usmap'),
           '--pkgtimeout', '900', '--diag', '1', '--vpaths', '1', 'check', str(g / 'diag.txt'), str(g / f'diag-{m}.jsonl')]
    procs.append((m, subprocess.Popen(cmd, stdout=open(g / f'diag-{m}.log', 'a'), stderr=subprocess.STDOUT, env=ENV)))
for m, p in procs:
    p.wait()
    n = sum(1 for _ in open(g / f'diag-{m}.jsonl')) if (g / f'diag-{m}.jsonl').exists() else 0
    if n >= len(bad): (g / f'diag-{m}.done').write_text('')
print(game, len(bad), [(m, p.returncode) for m, p in procs], flush=True)
