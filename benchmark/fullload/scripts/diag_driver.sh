#!/bin/bash
# After driver.sh: diagnostic pass per game (only not-fully-loaded packages), same scope limits. Up to 3 retries for crashes.
. "$(dirname "$0")/common.sh"; cd "$W"
echo "diag driver pid $$ start $(date -Is)"
for pass in 1 2 3; do
  for game in $(cat "$W/games-order.txt"); do
    [ -f runs/$game/diag-gold.done ] && [ -f runs/$game/diag-ours.done ] && continue
    [ -f runs/$game/run.json ] || continue
    capped timeout -k 60 3600 python3 "$HERE/diag_game.py" $game
  done
done
echo "diag driver done $(date -Is)"
