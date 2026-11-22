# sourced by the shell scripts: reads the same environment as fl_config.py
: "${FULLLOAD_WORK:=$PWD/fullload-work}"; export FULLLOAD_WORK
: "${DOTNET_ROOT:=$HOME/.dotnet}"; export DOTNET_ROOT
: "${CORES:=0-7}"          # cores the runs are pinned to
: "${MEMMAX:=12G}"         # hard memory cap of every run scope
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="$FULLLOAD_WORK"
# run a command under a memory cap, low priority, pinned cores (extra systemd properties as $CAPEXTRA)
capped() { systemd-run --user --scope -q -p MemoryMax=$MEMMAX -p MemorySwapMax=0 $CAPEXTRA nice -n19 taskset -c $CORES "$@"; }
