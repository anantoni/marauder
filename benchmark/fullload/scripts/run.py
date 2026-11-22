# run.py NAME TSV OUTDIR : run hooked harness NAME on (game\tpath) rows with gold and ours maps, one process at a time
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gameargs import args
from fl_config import OODLE, ENV, W
name, tsv, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
from fl_config import named_bin
BIN = named_bin(name); out.mkdir(parents=True, exist_ok=True)
rows = [l.rstrip('\n').split('\t', 1) for l in tsv.open() if l.strip()]
for game in sorted({g for g, _ in rows}):
    eg, paks = args(game)
    if os.environ.get('PAKS'): paks = os.environ['PAKS']
    lst = out / f'{game}.list.txt'
    lst.write_text('\n'.join(dict.fromkeys(p for g, p in rows if g == game)) + '\n')
    for m in ('gold', 'ours'):
        o = out / f'{game}.{m}.jsonl'
        cmd = [str(BIN), '--paks', paks, '--game', eg, '--oodle', OODLE, '--usmap', str(W / 'maps' / f'{game}.{m}.usmap'), '--pkgtimeout', '900', '--diag', '1', 'check', str(lst), str(o)]
        for attempt in range(5):   # resumable: a crash/timeout row is recorded and the run continues
            p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=open(out / f'{game}.{m}.err', 'a'), env=ENV)
            if p.returncode in (0, 3): break
        print(game, m, 'rc', p.returncode, flush=True)
print('DONE', flush=True)
