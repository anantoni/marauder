#!/bin/bash
# CUE4Parse-Natives (ACL animation decompression) for the diagnostic binary. The ACL submodule is pinned by the CUE4Parse commit.
# Needs g++. Output: $FULLLOAD_WORK/bin-diag/CUE4Parse-Natives.so
set -e; . "$(dirname "$0")/common.sh"
cd "$W/CUE4Parse" && git submodule update --init --recursive CUE4Parse-Natives/ACL/external/acl
mkdir -p "$W/bin-diag"
cd CUE4Parse-Natives && g++ -std=c++11 -O2 -fPIC -shared -DWITH_ACL -IACL/external/acl/includes -IACL/external/acl/external/rtm/includes -Icommon \
  ACL/ACL.cpp Features.cpp -o "$W/bin-diag/CUE4Parse-Natives.so"
