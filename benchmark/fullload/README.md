# Full-load benchmark: the CUE4Parse harness, patch and scripts

This directory has everything behind the full-load tables of the paper: the hooked CUE4Parse
harness, the patch that adds the load checks, the build and run scripts, and the scripts that
turn the per-package rows into the tables. It contains no game data and no maps. You run it on
games you own, with maps you made yourself (generated map = `ours`, runtime dump = `gold`).

## What the test does

For every package of a game the harness loads every export with CUE4Parse under a map and
records one JSON row. A package counts as fully loaded only if

1. every export deserializes with no exception (object serialization errors are fatal),
2. every export ends exactly at its serial size (the end-of-export check), and
3. no nested read that CUE4Parse would otherwise skip or reposition silently goes wrong: no
   tagged property whose value throws or ends at the wrong byte, and no instanced struct that
   throws, ends at the wrong byte or has no type (the nested-read check).

Each export is hashed three ways (exact JSON, JSON with object keys lowercased, everything
lowercased). Every package is run with the `gold` map and with the `ours` map (and, in the main
run, with no map). The harness refuses encrypted archives (exit code 3).

## Contents

| Path | What |
|---|---|
| `harness/Program.cs`, `harness/UsmapLoadCheck.csproj` | The hooked harness (`list` and `check` commands). The project reads the CUE4Parse checkout from `$CUE4PARSE_DIR`. |
| `patches/cue4parse-fullload.patch` | The load-check patch: hooks in CUE4Parse that report position mismatches, skipped nested reads and touched types. Changes nothing else. Applies on `7353666`, on `c95dd9b5` and on the integration patch below. |
| `patches/marauder-integration-over-c95dd9b5.patch` | The reader fixes of the paper's second table, as one diff over upstream `c95dd9b5` (equal to branch `marauder-integration` at `d16f66eb`, 23 files, including the synthetic-byte tests). |
| `scripts/build.sh`, `build_natives.sh`, `mkbench.sh` | Build the harness for one CUE4Parse commit; build the ACL natives; build several readers side by side. |
| `scripts/driver.sh`, `run_game.py` | Main run: every package, gold, ours and no map, one game at a time under a memory cap. |
| `scripts/diag_driver.sh`, `diag_game.py`, `diag_retry.*`, `diag2.sh` | Diagnostic re-run of every package that did not fully load, with the natives library, exception chains and virtual paths. |
| `scripts/run_par.py`, `run_list2.py`, `run_list.py`, `run.py` | Reruns of a package list with a named build (reader-fix rerun, regression samples). |
| `scripts/compare.py`, `compare_full.py` | Before/after comparison of two runs: regressions, newly full, hash changes. |
| `scripts/analyze.py`, `classify.py`, `parents.py`, `make_report.py` | Merge rows, group the shared failures by cause, resolve Blueprint parent classes, write the tables. |
| `scripts/fl_config.py`, `gameargs.py`, `common.sh` | Shared settings (all from environment variables). |

## Setup

Needs Linux, .NET 10 SDK, g++, git, python3 (3.10+), `rg` (ripgrep, used by the classifier),
systemd user scopes (for memory caps), the Oodle shared library CUE4Parse loads, and the
released generator installed (`pip install -e <repo root>`, `classify.py` uses its usmap reader).

```
export FULLLOAD_WORK=$PWD/fullload-work       # all inputs and outputs live here
export OODLE_LIB=/path/to/liboodle-data-shared.so
export DOTNET_ROOT=$HOME/.dotnet              # default
mkdir -p "$FULLLOAD_WORK/runs"                # the driver logs are redirected into it
```

`marauder generate` also reads Oodle-compressed archives: set `UE_OODLE_LIB` to the same library when you
make the `ours` maps.

### Your games and maps

For every game put these in `$FULLLOAD_WORK/maps/` (the id is any label you choose):

* `<id>.gold.usmap`: the runtime dump (see `docs/reproducing.md`)
* `<id>.ours.usmap`: the map the generator wrote from the same install (`marauder generate ...`)
* `<id>.json` (paths are your own):

```
{"game": "<id>", "split": "development", "engine": "5.1",
 "exe": "/path/to/Game-Win64-Shipping.exe",
 "paks": ["/path/to/Game/Content/Paks/Game-WindowsNoEditor.pak"]}
```

`engine` picks CUE4Parse's `GAME_UE<major>_<minor>`. `split` is `development`, `hold` or `fresh`
(only used to group the tables). `paks` lists container files; their folders are mounted. Add
`"first_paks_dir_only": true` when an install holds a second folder that duplicates the same
pak. Loose cooked installs: leave `paks` empty, the folder three levels above `exe` is used.
List the ids, one per line, in `$FULLLOAD_WORK/games-order.txt`.

The released benchmark list (`../benchmark-list.csv`) gives the exact Steam build and depot
manifests of the measured install of each game.

## Exact steps

CUE4Parse commits used in the paper:

| Use | Commit |
|---|---|
| Main full-load run (121 games, 510,503 packages) | `7353666` (upstream, pinned) + `patches/cue4parse-fullload.patch` |
| Reader-fix rerun base | upstream master `c95dd9b5` |
| Reader-fix rerun reader | `marauder-integration` `d16f66eb` = `c95dd9b5` + `patches/marauder-integration-over-c95dd9b5.patch` |

The integration branch merges these fix branches (one root cause each): `fix/dynamicmesh-serial-end`,
`fix/tagged-container-properties`, `fix/landscape-mesh-collision-cooked-flag`,
`fix/datasmith-scene-strip-flags`, `fix/geometrycollection-union-hierarchy-flag`,
`fix/serialized-enum-underlying-type` (open upstream as pull request 486) and
`feat/groom-asset-reader`. The patch file is their combined diff; the branches themselves are
not published separately yet.

1. **Build the pinned reader and harness** (clones upstream CUE4Parse into `$FULLLOAD_WORK/CUE4Parse`):

   ```
   benchmark/fullload/scripts/build.sh 7353666
   ```

   This applies the patch, builds `bin/` (main run) and `bin-diag/` (same harness plus
   `CUE4Parse-Natives.so`, built from the ACL submodule the commit pins, for animation
   decompression). The main run uses `bin/`; the diagnostic pass uses `bin-diag/`.

2. **Main run**, every package, gold, ours and no map. Memory cap 12 GB per game, pinned to 8
   cores (`MEMMAX` and `CORES` override), per-package watchdog 900 s, restarts after a crash or
   an out-of-memory kill:

   ```
   setsid nohup benchmark/fullload/scripts/driver.sh > $FULLLOAD_WORK/runs/driver.log 2>&1 < /dev/null &
   ```

   Output: `runs/<id>/{gold,ours,none}.<k>.jsonl` (one row per package). Resumable; rerun to continue.

3. **Diagnostic pass** (only packages that did not fully load with a map; this is what removed
   the missing-natives harness fault that hit both maps equally):

   ```
   benchmark/fullload/scripts/diag_driver.sh
   ```

   Big games that do not finish: `diag_retry.sh par|seq <id>...`. Packages whose diagnostic rows
   disagree or crashed: write them to `runs/<id>/diag2.txt` and run `diag2.sh <id>`.

4. **Tables.** In this order:

   ```
   python3 benchmark/fullload/scripts/analyze.py      # runs/merged/<id>.jsonl, runs/summary.json
   python3 benchmark/fullload/scripts/classify.py     # runs/causes.json
   python3 benchmark/fullload/scripts/parents.py      # runs/parents.json (Blueprint parents of failing classes)
   python3 benchmark/fullload/scripts/classify.py && python3 benchmark/fullload/scripts/make_report.py
   ```

   `analyze.py` dedupes the package list per game, applies the diagnostic rows and writes the
   headline numbers; `make_report.py` writes `runs/report-tables.md`: the headline table per
   split (packages, full with gold, full with ours, full with no map, recall, gold-only,
   ours-only, data-equal and exact-equal over packages both maps fully load), the Animation
   Blueprint table, the shared failures by owner, the root-cause table and the per-game table.
   Run `classify.py` once before `parents.py` (it names the failing Blueprint classes) and again
   after, so the FunctionalTest rule uses the parent chain.

5. **Reader-fix rerun** (second table). Build the integration reader next to the pinned one:

   ```
   INTEGRATION=1 benchmark/fullload/scripts/mkbench.sh integ c95dd9b5
   ```

   Put the games to rerun in a file (`games.txt`, one id per line) and rerun every package
   that did not crash or run out of memory in step 2, with both maps:

   ```
   systemd-run --user --scope -p MemoryMax=12G python3 benchmark/fullload/scripts/run_par.py integ games.txt integ-out 4
   python3 benchmark/fullload/scripts/compare_full.py integ-out     # rows vs the main run
   ```

   `compare_full.py` prints per game and map: packages full in both runs, how many have equal
   hashes, regressions (must be 0), packages that became full. `run_list2.py NAME OUT WORKERS CHUNK`
   reruns lists with one memory scope per process and one-package retry after an out-of-memory kill.
   `compare.py BASEDIR FIXDIR` does the same comparison between two arbitrary row directories.
   To test one build against another, name them with `mkbench.sh NAME REF` (set `INTEGRATION=1` for the fix patch).

## Memory caps

Every run is wrapped in `systemd-run --user --scope -p MemoryMax=12G -p MemorySwapMax=0` (set
`MEMMAX`), low priority, pinned cores (`CORES`). A harness process killed for memory is restarted;
its package is recorded as `crash` and re-checked in the diagnostic pass. At most 6 to 8
harness processes run at once per game. A full run of all games is a long job;
start with a few small games (`games-order.txt`).

## Expected outputs

Row fields (`runs/<id>/<map>.<k>.jsonl`): `p` package path, `st` (`ok`, `partial`, `failed`,
`crash`, `timeout`), `full` (status ok and no nested-read event), `ex`/`okx` exports and loaded
exports, `soft` skipped nested reads by kind, `mm` end-of-export mismatches, `err`, `h`, `hn`,
`hl` (exact, keys-lowercased, all-lowercased hashes), `abp` (Animation Blueprint class present).

On the paper's corpus the tables come out as: 510,503 packages, 509,297 fully loaded with both
maps, 0 loaded with one map and not the other, 145,440 with no map, data equal after lowercasing
object keys on 509,122 of 509,297 (99.966%), byte-identical on 505,573 (99.269%); with the
reader fixes, 0 regressions on the 242,858 packages of the 40 rerun games that loaded before.
Your numbers will differ with your games and builds; the checks that must hold are recall 100%
(no package full with gold and not with ours) and 0 regressions in step 5. Hashes of the
runs depend on the CUE4Parse commit: output changes made upstream after `7353666` change some
hashes without losing data (see the paper).

## Notes

* The classifier's owner labels (reader, impossible, plugin, newest engine) are rules written
  for the paper's corpus (module names, plugin lists in `make_report.py`); check them against your
  own failures before quoting them.
* Maps and rows hold names from your own installs. Do not publish them; publish counts and hashes.
