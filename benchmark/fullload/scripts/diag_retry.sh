#!/bin/bash
# diag_retry.sh par|seq GAME... : finish the diagnostic pass for big games, one memory-capped scope per game.
# par = gold and ours in parallel, seq = one at a time.
. "$(dirname "$0")/common.sh"; cd "$W"; echo "diag retry pid $$ start $(date -Is)"
mode=$1; shift
for game in "$@"; do
  if [ "$mode" = seq ]; then inner="python3 $HERE/diag_retry.py $game gold; python3 $HERE/diag_retry.py $game ours"
  else inner="python3 $HERE/diag_retry.py $game gold & python3 $HERE/diag_retry.py $game ours & wait"; fi
  CAPEXTRA="-p OOMPolicy=continue" capped bash -c "$inner"
done
echo "diag retry done $(date -Is)"
