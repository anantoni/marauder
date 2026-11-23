# run_par.py NAME GAMES.txt OUTDIR [WORKERS] : full-load every package of each game with gold and ours using hooked harness NAME,
# split into chunks run by a small worker pool (all inside the caller's memory-capped scope). Resumable.
# Skips packages that crashed, timed out or ran out of memory in the original full run (runs/merged), recorded in skipped.tsv.
import json, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gameargs import args
from fl_config import OODLE, ENV, W
name, games, out = sys.argv[1], Path(sys.argv[2]).read_text().split(), Path(sys.argv[3])
workers = int(sys.argv[4]) if len(sys.argv) > 4 else 6
from fl_config import named_bin
BIN = str(named_bin(name)); out.mkdir(parents=True, exist_ok=True)
jobs = []; skipped = []
for g in games:
    rows = [json.loads(l) for l in open(W / 'runs/merged' / f'{g}.jsonl')]
    keep = []; budget = 0; chunk = []; parts = []
    for r in rows:
        go = r.get('gold', {})
        if go.get('st') in ('crash', 'timeout') or 'OutOfMemoryException' in (go.get('exc') or {}):
            skipped.append(f"{g}\t{r['p']}\t{go.get('st')}"); continue
        chunk.append(r['p']); budget += go.get('ms') or 0
        if budget > 20 * 60 * 1000 or len(chunk) >= 3000: parts.append(chunk); chunk = []; budget = 0
    if chunk: parts.append(chunk)
    for i, c in enumerate(parts):
        lst = out / f'{g}.{i}.list.txt'; lst.write_text('\n'.join(c) + '\n')
        for m in ('gold', 'ours'): jobs.append((g, i, m, lst))
(out / 'skipped.tsv').write_text('\n'.join(skipped) + '\n')
def run(j):
    g, i, m, lst = j; eg, paks = args(g); o = out / f'{g}.{i}.{m}.jsonl'
    cmd = [BIN, '--paks', paks, '--game', eg, '--oodle', OODLE, '--usmap', str(W / 'maps' / f'{g}.{m}.usmap'), '--pkgtimeout', '900', '--diag', '1', 'check', str(lst), str(o)]
    for _ in range(20):
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=open(out / f'{g}.{i}.{m}.err', 'a'), env=ENV)
        if p.returncode in (0, 3): break
    print(g, i, m, 'rc', p.returncode, flush=True)
with ThreadPoolExecutor(workers) as ex: list(ex.map(run, sorted(jobs)))
print('DONE', len(jobs), 'jobs', len(skipped), 'skipped', flush=True)
