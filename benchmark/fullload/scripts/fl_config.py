"""Paths and settings shared by the full-load scripts. Everything comes from the environment; nothing is hard-coded.

FULLLOAD_WORK  working directory (default ./fullload-work): maps/, runs/, harness/, CUE4Parse/ live here
OODLE_LIB      path to the Oodle shared library (liboodle-data-shared.so) that CUE4Parse loads
DOTNET_ROOT    .NET SDK/runtime root (default ~/.dotnet)
"""
import os
from pathlib import Path
W = Path(os.environ.get('FULLLOAD_WORK', './fullload-work')).resolve()
OODLE = os.environ.get('OODLE_LIB', '')
DOTNET_ROOT = os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))
ENV = dict(os.environ, DOTNET_ROOT=DOTNET_ROOT)
MAPS = W / 'maps'
RUNS = W / 'runs'
CUE4PARSE = W / 'CUE4Parse'
BIN = W / 'bin' / 'UsmapLoadCheck'            # harness without ACL natives (main run)
BIN_DIAG = W / 'bin-diag' / 'UsmapLoadCheck'  # same harness plus CUE4Parse-Natives.so (diagnostic re-run)
def named_bin(name): return W / 'harness-builds' / name / 'bin' / 'UsmapLoadCheck'
