#!/bin/bash
# diag2.sh GAME : re-check packages whose diagnostic rows disagree or crashed (list in runs/GAME/diag2.txt): one process at a time, the whole memory scope to itself.
. "$(dirname "$0")/common.sh"; cd "$W"; g=$1
EG=$(python3 "$HERE/gameargs.py" $g | head -1); P=$(python3 "$HERE/gameargs.py" $g | tail -1)
for m in gold ours; do
  CAPEXTRA="-p OOMPolicy=continue" capped env DOTNET_ROOT="$DOTNET_ROOT" bin-diag/UsmapLoadCheck --paks "$P" --game $EG --oodle "$OODLE_LIB" \
    --usmap maps/$g.$m.usmap --pkgtimeout 1800 --diag 1 --vpaths 1 check runs/$g/diag2.txt runs/$g/diag2-$m.jsonl
  echo "$g $m rc $?"
done
