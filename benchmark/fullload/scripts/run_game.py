"""Run the full-load check for one game: every package, three maps (gold, ours = v11 option (v), none).
Runs inside one memory-capped scope started by driver.sh. Resumable: harness skips packages already written.
usage: run_game.py <game> <wall_seconds>"""
import json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from gameargs import args
from fl_config import OODLE, ENV, W
from fl_config import BIN
game, wall = sys.argv[1], int(sys.argv[2]); deadline = time.time() + wall
eg, d = args(game); base = [str(BIN), '--paks', d, '--game', eg, '--oodle', OODLE]
g = W / 'runs' / game; g.mkdir(parents=True, exist_ok=True)
log = open(g / 'run.log', 'a')
def say(*a): print(time.strftime('%H:%M:%S'), *a, file=log, flush=True)
if not (g / 'all.txt').exists():
    rc = subprocess.run(base + ['list', str(g / 'all.tmp')], stdout=log, stderr=log, env=ENV).returncode
    if rc != 0:
        say('list failed rc', rc); (g / 'run.json').write_text(json.dumps({'game': game, 'list_rc': rc, 'complete': False})); sys.exit(rc)
    (g / 'all.tmp').rename(g / 'all.txt')
allp = [x for x in (g / 'all.txt').read_text().split('\n') if x]
n = len(allp)
S = {'gold': 3, 'ours': 3, 'none': 2} if n >= 600 else {'gold': 1, 'ours': 1, 'none': 1}
jobs = []
for m, s in S.items():
    for k in range(s):
        lst = g / f'list.{m}.{k}.txt'
        if not lst.exists(): lst.write_text('\n'.join(allp[k::s]) + '\n')
        jobs.append({'m': m, 'k': k, 'list': lst, 'out': g / f'{m}.{k}.jsonl', 'want': len(allp[k::s]), 'restarts': 0, 'stuck': 0, 'proc': None, 'done': False})
def rows(f): return sum(1 for _ in open(f)) if f.exists() else 0
def start(j):
    cmd = base + ([] if j['m'] == 'none' else ['--usmap', str(W / 'maps' / f"{game}.{j['m']}.usmap")]) + ['--pkgtimeout', '900', '--diag', '1', 'check', str(j['list']), str(j['out'])]
    j['before'] = rows(j['out'])
    j['proc'] = subprocess.Popen(cmd, stdout=open(g / f"{j['m']}.{j['k']}.log", 'a'), stderr=subprocess.STDOUT, env=ENV)
    say('start', j['m'], j['k'], 'pid', j['proc'].pid, 'rows', j['before'], '/', j['want'])
say('game', game, eg, 'packages', n, 'shards', S, 'wall', wall)
MAXP = 8 if n < 10000 else 6   # concurrent harness processes (memory)
for j in jobs:
    if rows(j['out']) >= j['want']: j['done'] = True
def running(): return sum(1 for j in jobs if j['proc'] is not None and j['proc'].poll() is None)
def fill():
    for j in jobs:
        if running() >= MAXP: break
        if not j['done'] and j['proc'] is None and not j.get('waiting_restart'): start(j)
fill()
status = 'complete'
while not all(j['done'] for j in jobs):
    time.sleep(5)
    if time.time() > deadline:
        for j in jobs:
            if j['proc'] and j['proc'].poll() is None:
                say('wall limit: kill pid', j['proc'].pid); j['proc'].kill(); j['proc'].wait()
        status = 'wall_limit'; break
    for j in jobs:
        if j['done'] or j['proc'] is None or j['proc'].poll() is None: continue
        if j['proc'] is None: continue
        rc = j['proc'].returncode; have = rows(j['out']); j['proc'] = None
        if rc == 3: say('ENCRYPTED, stop game'); status = 'encrypted'; break
        if have >= j['want']: j['done'] = True; say('done', j['m'], j['k'], 'rc', rc); continue
        j['stuck'] = j['stuck'] + 1 if have <= j['before'] else 0
        j['restarts'] += 1
        if j['stuck'] >= 3 or j['restarts'] > 500:
            say('give up', j['m'], j['k'], 'rc', rc, 'rows', have); j['done'] = True; status = 'gave_up'; continue
        say('restart', j['m'], j['k'], 'rc', rc, 'rows', have); start(j)
    fill()
    if status == 'encrypted':
        for j in jobs:
            if j['proc'] and j['proc'].poll() is None: j['proc'].kill(); j['proc'].wait()
        break
res = {'game': game, 'egame': eg, 'packages': n, 'status': status, 'complete': status == 'complete',
       'rows': {f"{j['m']}.{j['k']}": rows(j['out']) for j in jobs}, 'restarts': sum(j['restarts'] for j in jobs)}
(g / 'run.json').write_text(json.dumps(res)); say('end', json.dumps(res))
