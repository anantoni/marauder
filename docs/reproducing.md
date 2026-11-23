# Reproducing the benchmark results

Everything needed to check the published numbers on games you own, without any of our files.

## The benchmark list

[`benchmark/benchmark-list.csv`](../benchmark/benchmark-list.csv) has one row per game:

| Column | Meaning |
|---|---|
| `split` | `development`, `hold-out` or `fresh hold-out` |
| `appid` | Steam app ID |
| `build_id` | exact Steam build ID that was measured |
| `depot_manifests` | Steam depot manifest IDs of that build (space-separated when there are several) |
| `ue` | engine version |
| `status` | `scored`, or why the game was excluded (encrypted archives, engine 4.13 to 4.16) |
| `is_free` | `yes` when the game was free to download, else `unknown` |
| `gold_sha256` | SHA-256 of the runtime reference mapping |
| `research_generated_sha256` | SHA-256 of the map scored in the paper (research configuration of the spelling rule; you cannot regenerate it, see below) |
| `released_generator` | the released generator that made the map in `released_generated_sha256` |
| `released_generated_sha256` | SHA-256 of the map that this release's `marauder generate` writes for the listed build; compare your own output with this one |
| `score_fields_right`, `score_fields_total`, `score_fields_percent` | exact field comparison against the frozen runtime reference |
| `score_fields_casefold_right`, `score_fields_casefold_total`, `score_fields_casefold_percent` | field comparison after folding ASCII letter case |
| `score_records_identical`, `score_records_total`, `score_records_percent` | complete type and enum records that match |
| `score_differences`, `score_case_only_differences` | difference rows, including the case-only subset |

Score columns are populated for scored games and blank for excluded rows. They are the scores of
the released map (`released_generated_sha256`), from `marauder compare` against the frozen gold.
The released hashes and scores come from running the released command line
(`marauder generate <install> -o game.usmap`, commit `7fec886`, whose generator code is identical
to `6fe7f76`) once on each scored game. Game slugs and generated maps remain private.
When a report is requested, `staticCodeLinks` also records `code_sections_read`,
`wrapper_sections_unread`, and `plain_code_rejected` for a wrapped executable. The CLI prints the
plain `.text` check result and those section boundaries while it runs.

## Steps

1. **Get the build.** Steam serves the newest build by default. Use the listed depot manifest IDs
   with [DepotDownloader](https://github.com/SteamRE/DepotDownloader) to get the measured build
   (you need to own the game).
2. **Capture the reference.** With [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS), run
   `DumpUSMAP(false)` (Blueprint-generated types excluded) once the game reaches its main menu, with
   network access blocked. Capture twice and keep the dump only when both have the same schema.
   Its SHA-256 should equal `gold_sha256`. A capture can differ from ours when the game loads a
   different Anim Blueprint copy at start-up; the paper measures how often that happens.
3. **Generate.** `marauder generate <install folder> -o game.usmap`. Its SHA-256 should equal
   `released_generated_sha256`.
4. **Score.** `marauder compare game.usmap reference.usmap --json`; the result should equal the
   score columns.

## Which configuration the hashes describe

The list has two generated hashes per scored game. `research_generated_sha256` is the map scored in
the paper, made with the research configuration of the spelling rule, which votes with runtime
dumps of the most similar development games. Those dumps are not distributed, so nobody outside can
regenerate that map. `released_generated_sha256` is the map this release writes; it is the one to
check your own output against. This release uses a derived table instead
([`src/usmap_generator/data/spelling_table_by_engine_version.json`](../src/usmap_generator/data/spelling_table_by_engine_version.json):
16 engine versions with 140 rows, plus one table per major version pooled over its minor versions
with 103 rows; identifiers and vote counts only). The table is built from this release's own
output on the development games, so its rows describe the generator that ships. Its output can
differ from `research_generated_sha256` in the letter case of a few names. Both configurations:

| Set | Research configuration | This release (public table) |
|---|---:|---:|
| Development (66 games) | 99.923% | 99.923% (leave-one-out) |
| Hold-out (31) | 99.886% | 99.885% |
| Fresh blind hold-out (24) | 99.867% | 99.867% |

When the executable stores no engine build string, the minor version is estimated from the
reflected type names
([`src/usmap_generator/data/engine_version_markers.json`](../src/usmap_generator/data/engine_version_markers.json):
type names added or removed by each engine version, from the development games). On the 109 games
whose executables store the build string, the estimate matches it on 105 (the four misses are
neighbouring versions). When no estimate is possible, the pooled table for the major version is
used; it respells a name only when no game of that major version kept the generated spelling.

## Rebuilding the spelling table

[`tools/build_spelling_table.py`](../tools/build_spelling_table.py) rebuilds the table from your own
captures: one pickle per game with `{"rows": {stem: (generated spelling, reference spelling)}}` and
a JSON file mapping each pickle name to its engine version (`"5.3"`). The table keeps only stems for
which at least one game shows a different spelling. A name is respelled when at least two games of
the same engine version agree on one other spelling, no game kept the generated one, and the new
spelling occurs in the executable. Build the pickles from maps made by the same Marauder version
you ship: rows from another version's output describe its spellings, not this one's.

[`tools/build_version_markers.py`](../tools/build_version_markers.py) rebuilds the version markers
from generated maps of games whose engine version you know.
