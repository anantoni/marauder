#!/bin/bash
# mkbench.sh NAME REF : worktree of the CUE4Parse clone at REF (commit or branch) + the load-check patch, hooked harness built to
# $FULLLOAD_WORK/harness-builds/NAME/bin (used by run_par.py / run_list.py / run_list2.py / run.py as their NAME argument).
set -e; . "$(dirname "$0")/common.sh"
N=$1; C=$(git -C "$W/CUE4Parse" rev-parse "$2"); B="$W/bench-src/$N"; H="$W/harness-builds/$N"
cd "$W/CUE4Parse"
if [ -d "$B" ]; then git -C "$B" reset -q --hard; git -C "$B" checkout -q --detach "$C"; else git worktree add -q --detach "$B" "$C"; fi
git -C "$B" submodule update --init --recursive
# REF may be c95dd9b5 + INTEGRATION=1 to apply the released reader-fix patch first (marauder-integration d16f66eb equivalent)
[ -n "$INTEGRATION" ] && git -C "$B" apply "$HERE/../patches/marauder-integration-over-c95dd9b5.patch"
git -C "$B" apply "$HERE/../patches/cue4parse-fullload.patch"
mkdir -p "$H"; cp "$HERE/../harness/Program.cs" "$HERE/../harness/UsmapLoadCheck.csproj" "$H/"
cd "$H" && CUE4PARSE_DIR="$B" nice -n19 taskset -c $CORES "$DOTNET_ROOT/dotnet" build -c Release -o bin -v q -nologo 2>&1 | grep -E ' error |Build succeeded|Error' | grep -v warning | tail -3
cp "$W/bin-diag/CUE4Parse-Natives.so" "$H/bin/" 2>/dev/null || true
echo "bench $N at $(git -C "$B" rev-parse --short=8 HEAD)"
