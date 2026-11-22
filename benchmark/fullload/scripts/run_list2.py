# run_list2.py NAME OUTDIR WORKERS CHUNK : like run_list.py but each harness process runs in its own 12G scope, in chunks; a chunk that dies (OOM) is retried one package at a time, a package that dies alone is recorded as oom.
import os, subprocess, sys, json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gameargs import args
from fl_config import OODLE, ENV, W
name, out, workers, ch = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
from fl_config import named_bin
BIN = str(named_bin(name))
def once(g, m, pk, tag):
    eg, paks = args(g); lst = out / f'{g}.{tag}.list.tmp'; lst.write_text('\n'.join(pk) + '\n'); o = out / f'{g}.{tag}.{m}.jsonl'
    cmd = ['systemd-run', '--user', '--scope', '-q', '-p', 'MemoryMax=' + os.environ.get('MEMMAX', '12G'), 'nice', '-n19', 'taskset', '-c', os.environ.get('CORES', '0-7'), BIN, '--paks', paks, '--game', eg, '--oodle', OODLE, '--usmap', str(W / 'maps' / f'{g}.{m}.usmap'), '--pkgtimeout', '900', '--diag', '1', 'check', str(lst), str(o)]
    p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=open(out / f'{g}.{tag}.{m}.err', 'a'), env=ENV)
    return p.returncode, o
def done(o): return {json.loads(l)['p'] for l in open(o)} if o.exists() else set()
def job(j):
    g, m, pk, tag = j; rc, o = once(g, m, pk, tag)
    if rc in (0, 3): print(g, tag, m, 'ok', flush=True); return
    d = done(o); rest = [p for p in pk if p not in d]
    for i, p in enumerate(rest):
        rc, o2 = once(g, m, [p], f'{tag}s{i}')
        if rc not in (0, 3): print('FAIL', g, m, p, rc, flush=True)
    print(g, tag, m, 'retried', len(rest), flush=True)
jobs = []
for l in sorted(out.glob('*.0.list.txt')):
    g = l.name.split('.')[0]; pk = [x for x in l.read_text().split('\n') if x]
    for i in range(0, len(pk), ch):
        for m in ('gold', 'ours'): jobs.append((g, m, pk[i:i+ch], f'c{i//ch}'))
with ThreadPoolExecutor(workers) as ex: list(ex.map(job, jobs))
print('DONE', len(jobs), flush=True)
