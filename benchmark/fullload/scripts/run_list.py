# run_list.py NAME OUTDIR WORKERS : run the existing OUTDIR/<game>.0.list.txt lists with gold and ours (hooked harness NAME)
import subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gameargs import args
from fl_config import OODLE, ENV, W
name, out, workers = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
from fl_config import named_bin
BIN = str(named_bin(name))
jobs = [(l.name.split('.')[0], m, l) for l in out.glob('*.0.list.txt') for m in ('gold', 'ours')]
def run(j):
    g, m, lst = j; eg, paks = args(g); o = out / f'{g}.0.{m}.jsonl'
    cmd = [BIN, '--paks', paks, '--game', eg, '--oodle', OODLE, '--usmap', str(W / 'maps' / f'{g}.{m}.usmap'), '--pkgtimeout', '900', '--diag', '1', 'check', str(lst), str(o)]
    for _ in range(20):
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=open(out / f'{g}.0.{m}.err', 'a'), env=ENV)
        if p.returncode in (0, 3): break
    print(g, m, 'rc', p.returncode, flush=True)
with ThreadPoolExecutor(workers) as ex: list(ex.map(run, sorted(jobs)))
print('DONE', len(jobs), flush=True)
