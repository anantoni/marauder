#!/bin/bash
# Main run. One game at a time, each in its own memory-capped scope. Three passes: later passes resume unfinished games.
# OOMPolicy=continue: an OOM kill takes one harness process (restarted, its package marked crash and re-checked in the diagnostic pass), not the game.
# Games come from $FULLLOAD_WORK/games-order.txt (one game id per line, each with maps/<id>.json, <id>.gold.usmap, <id>.ours.usmap).
# Start detached:  setsid nohup scripts/driver.sh > $FULLLOAD_WORK/runs/driver.log 2>&1 < /dev/null &
. "$(dirname "$0")/common.sh"; cd "$W"; mkdir -p runs
echo "driver pid $$ start $(date -Is)"
for pass in 1 2 3; do
  for game in $(cat "$W/games-order.txt"); do
    if [ -f runs/$game/run.json ] && grep -q '"complete": true' runs/$game/run.json; then continue; fi
    n=$( (wc -l < runs/$game/all.txt) 2>/dev/null || echo 3000)
    wall=$(( 1800 + n * 15 / 100 )); [ $wall -gt 21600 ] && wall=21600
    echo "$(date -Is) pass $pass game $game packages $n wall $wall"
    CAPEXTRA="-p OOMPolicy=continue" capped timeout -k 60 $((wall + 300)) python3 "$HERE/run_game.py" $game $wall
    echo "$(date -Is) end $game rc $? $(cat runs/$game/run.json 2>/dev/null)"
  done
done
echo "driver done $(date -Is)"
