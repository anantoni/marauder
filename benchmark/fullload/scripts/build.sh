#!/bin/bash
# build.sh [CUE4PARSE_REF] : clone CUE4Parse into $FULLLOAD_WORK/CUE4Parse, check out REF (default: the pinned commit 7353666),
# apply the load-check patch, build the hooked harness into $W/bin and $W/bin-diag (+ ACL natives), see README.md.
# For another reader commit (reader-fix rerun) use mkbench.sh instead, which builds side by side under a name.
set -e; . "$(dirname "$0")/common.sh"
REF=${1:-7353666}
mkdir -p "$W"
[ -d "$W/CUE4Parse/.git" ] || git clone https://github.com/FabianFG/CUE4Parse.git "$W/CUE4Parse"
cd "$W/CUE4Parse"
git reset -q --hard; git checkout -q --detach "$REF"
git submodule update --init --recursive
git apply "$HERE/../patches/cue4parse-fullload.patch"
echo "CUE4Parse at $(git rev-parse --short=8 HEAD) + patch"
mkdir -p "$W/harness"; cp "$HERE/../harness/Program.cs" "$HERE/../harness/UsmapLoadCheck.csproj" "$W/harness/"
cd "$W/harness"
CUE4PARSE_DIR="$W/CUE4Parse" "$DOTNET_ROOT/dotnet" build -c Release -o "$W/bin" -v q -nologo
rm -rf "$W/bin-diag"; cp -r "$W/bin" "$W/bin-diag"
"$HERE/build_natives.sh"
