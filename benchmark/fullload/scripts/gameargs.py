import json, sys
from pathlib import Path
from fl_config import MAPS, OODLE
def args(game):
    if not OODLE: raise SystemExit('set OODLE_LIB to the path of liboodle-data-shared.so')
    i = json.loads((MAPS / f'{game}.json').read_text())
    a, b = i['engine'].split('.')
    eg = f'GAME_UE{a}_{b}'
    dirs = []
    for p in i['paks']:
        d = p.rsplit('/', 1)[0]
        if d not in dirs: dirs.append(d)
    if i.get('first_paks_dir_only'): dirs = dirs[:1]   # optional: the install has a second folder that duplicates the same pak
    if not dirs: dirs = [str(Path(i['exe']).parents[3])]   # loose cooked files
    return eg, ':'.join(dirs)
if __name__ == '__main__':
    eg, d = args(sys.argv[1]); print(eg); print(d)
