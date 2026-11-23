# Marauder

> I solemnly swear I am up to no good.

Marauder builds a `.usmap` mapping file for an Unreal Engine game from the files you already have installed. It does not run the game, inject anything into it or change any file.

It reads the game's own files in place: the shipping executable (as data), the cooked archives (`.pak`, `.utoc`/`.ucas`, or loose files) and the shipped configuration files. On success it prints `Mischief managed.`

Made by [Sourcemined](https://sourcemined.com). Questions and feedback: anantoni@sourcemined.com

- [What a .usmap is and who this is for](#what-a-usmap-is-and-who-this-is-for)
- [Install](#install)
- [Quick start](#quick-start)
- [Commands and options](#commands-and-options)
- [Per-asset mapping](#per-asset-mapping)
- [Encrypted games](#encrypted-games)
- [Supported games and limitations](#supported-games-and-limitations)
- [Accuracy](#accuracy)
- [Reproducing the results](#reproducing-the-results)
- [Troubleshooting and FAQ](#troubleshooting-and-faq)
- [Credits](#credits)
- [License](#license)

## What a .usmap is and who this is for

Recent Unreal Engine games save their assets with *unversioned* property serialization. A cooked object lists its property values without their names or types. To read it, a tool needs the game's class and struct layouts: property names, order, array sizes, full type trees and enum entries. A `.usmap` file holds exactly that. [CUE4Parse](https://github.com/FabianFG/CUE4Parse), [FModel](https://github.com/4sval/FModel) and [UAssetAPI](https://github.com/atenfyr/UAssetAPI) read it.

Mappings usually come from a dumper that runs inside the game, or from a memory image of the running game. Marauder builds the mapping from the installed files instead. That helps when:

- **you mod or inspect a game** and the game cannot easily be started with a dumper: anti-cheat, no working dumper for its engine version yet, or a fresh update;
- **you build tools** and want to regenerate mappings automatically after every game update, on a server, without a GPU or a Windows session;
- **you want a second opinion** on a runtime dump.

## Install

Marauder needs **Python 3.10, 3.11, 3.12 or 3.13** (64-bit). It runs on **Windows** and **Linux**. Get Python from [python.org](https://www.python.org/downloads/) (Windows: tick "Add python.exe to PATH" in the installer).

### With pipx (recommended)

[pipx](https://github.com/pypa/pipx) installs the `marauder` command in its own environment.

Windows (PowerShell):

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
# open a new terminal, then:
pipx install marauder
```

Linux:

```bash
python3 -m pip install --user pipx     # or: sudo apt install pipx
python3 -m pipx ensurepath
pipx install marauder
```

### With pip

```bash
python -m pip install marauder
```

### From source

```bash
git clone https://github.com/anantoni/marauder.git
cd marauder
python -m pip install -e ".[dev]"
python -m pytest            # optional: run the tests
```

Check the install with `marauder --version`. If your shell cannot find `marauder`, use `python -m usmap_generator` instead; it takes the same arguments.

## Quick start

Point Marauder at the game's install folder (the folder that holds the game's project folder and `Engine/`):

```powershell
marauder generate "C:\Program Files (x86)\Steam\steamapps\common\MyGame"
```

```bash
marauder generate ~/.steam/steam/steamapps/common/MyGame
```

It finds `MyGame/Binaries/Win64/MyGame-Win64-Shipping.exe` and `MyGame/Content/Paks/`, shows its progress in six steps, and writes `MyGame.usmap` into the current folder:

```
Game executable: ...\MyGame\Binaries\Win64\MyGame-Win64-Shipping.exe
[1/6] Reading archives in ...\MyGame\Content\Paks
      1 .pak, 2 .utoc; 0 encrypted
[2/6] Reading type tables from MyGame-Win64-Shipping.exe
      9830 classes/structs, 1881 enums; engine 5.6
[3/6] Restoring name spellings from configuration and the asset registry
[4/6] Applying spelling votes for this engine version
      1 names respelled from the 5.6 table
[5/6] Reading Anim Blueprint structs from the cooked packages
      15 struct copies in 12 packages; 0 added to the global map (identical copies only)
[6/6] Final checks
Wrote MyGame.usmap (9830 classes/structs, 1881 enums, engine 5.6)
Done in 310 s.
Mischief managed.
```

Then load `MyGame.usmap` in your tool (see [Using the file in FModel and CUE4Parse](#using-the-file-in-fmodel-and-cue4parse)).

Time and memory depend on the size of the game. On a busy 16-core Linux server, a UE 4.25 game took under 2 minutes and 0.5 GB of RAM, and a UE 5.6 game 5 minutes and 0.9 GB.

## Commands and options

```
marauder generate GAME [options]     build a .usmap from an installed game folder
marauder compare GENERATED REFERENCE score a .usmap against a reference .usmap
marauder inspect FILE [NAME ...]     summary of a .usmap, or the listed classes, structs and enums
marauder --version
```

### `marauder generate`

| Option | What it does |
|---|---|
| `GAME` | The game's install folder, or its `*-Win64-Shipping.exe`. |
| `-o, --output FILE` | Output file. Default: `<Project>.usmap` in the current folder. |
| `--exe EXE` | The executable to read, when the folder holds several shipping executables. |
| `--paks DIR` | Folder with the archives, when they are not in `<Project>/Content/Paks`. |
| `--per-asset` | Also write the per-asset mapping: `<output>.perasset.json` and `<output>.perasset.bin` (UE5). See [Per-asset mapping](#per-asset-mapping). |
| `--aes-key KEY` | Your AES-256 key for encrypted archives: 64 hex digits (`0x` optional) or base64. `GUID:KEY` gives the key for one key GUID. Can be repeated. |
| `--aes-keys-file FILE` | A file with several keys. See [Encrypted games](#encrypted-games). |
| `--engine-version X.Y` | The engine version, when the executable does not store it (for example `5.3`); otherwise it is estimated from the type names. Used for the spelling votes and the UE5 steps. |
| `--data-only` | Read only the data sections of the executable. Skips the static code links that help name a few types; slightly less accurate. |
| `--report FILE` | Write a JSON report: counts per step, the engine version and how it was found, and every archive or entry that was not read, with the reason. |
| `--strict` | Exit with code 4 if any archive or entry could not be read. |
| `-q, --quiet` | Print only warnings, errors and the final line. |
| `-v, --verbose` | Also print internal diagnostics of the type-table reader. |

Examples:

```bash
# executable given directly, output in another folder, with a report
marauder generate "D:\Games\MyGame\MyGame\Binaries\Win64\MyGame-Win64-Shipping.exe" -o out\MyGame.usmap --report out\report.json

# UE5 game, also write the per-asset mapping
marauder generate ~/Games/MyGame --per-asset

# encrypted game, with your own key
marauder generate ~/Games/MyGame --aes-key 0x1A2B...9F0E

# fail (exit code 4) if anything was not read, for automated pipelines
marauder generate ~/Games/MyGame -q --strict
```

Exit codes: `0` success, `1` error (message says what to do), `2` wrong command-line usage, `4` `--strict` and something was not read, `130` interrupted.

Marauder never skips anything silently. Every archive, entry or package it could not read is listed at the end with the reason (first 15 on screen, all of them with `--report`).

### `marauder compare`

Scores a generated mapping against a reference mapping, with the metric used in the paper (see [Accuracy](#accuracy)):

```bash
marauder compare MyGame.usmap reference.usmap
marauder compare MyGame.usmap reference.usmap --json
```

```
fields right:           77,329 of 77,364 (99.955%)
ignoring letter case:   77,361 of 77,361 (100.000%)
records identical:      4,795 of 4,824 (99.399%)
maps identical:         no (35 differences, 35 case only)
```

### `marauder inspect`

```bash
marauder inspect MyGame.usmap                  # counts
marauder inspect MyGame.usmap PlayerState      # one class, with its properties
```

## Per-asset mapping

A `.usmap` holds **one record per name**. Most of the time that is enough. UE5 Animation Blueprints are the exception: every Anim Blueprint compiles its *own* `AnimBlueprintGeneratedConstantData` and `AnimBlueprintGeneratedMutableData` struct, so a game has dozens or hundreds of different structs with the same two names. A runtime dump keeps whichever copy happened to be loaded last; a global map cannot hold them all.

So, horcrux-style, each Anim Blueprint keeps its piece of the schema in its own package, and `--per-asset` collects every piece: every Anim Blueprint struct copy, keyed by the package that exports it, read from the cooked packages.

```bash
marauder generate ~/Games/MyGame --per-asset
# writes MyGame.usmap, MyGame.perasset.json and MyGame.perasset.bin
```

Both files hold the same content (a test checks this):

- **`.perasset.json`**: readable, easy to use from any language.
- **`.perasset.bin`**: compact binary; each struct record is encoded exactly like a `.usmap` (version 4) struct record, so existing `.usmap` reader code can be reused.

Lookup rule for a reader that resolves structs by name: while reading package `P`, look up `perasset[P][name]` first, then the global `.usmap`. A child Anim Blueprint's struct has its parent's struct (same name) as its super; `superPackage` says which package that parent struct is in.

The format is specified in [docs/per-asset-format.md](docs/per-asset-format.md).

**Do you need it?** For CUE4Parse and FModel, usually not. In our tests CUE4Parse read every Anim Blueprint package (129 of 129, in 9 UE5 games) the same with or without these records, because it reads the struct straight from the package. The global `.usmap` must contain the `AnimBlueprintGeneratedStruct` record for this to work, and Marauder always writes it for UE5 games. The per-asset file is for readers that look structs up by name only; with it, a forced by-name reading matched the normal reading on 129 of 129 packages.

## Encrypted games

Some games encrypt their archives with an AES-256 key. Without a key, Marauder reads everything that is not encrypted, then lists each encrypted archive as not read.

If you have the key for **your own copy** of the game, pass it:

```bash
marauder generate ~/Games/MyGame --aes-key 0x0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF
```

Games with several keys mark each archive with a key GUID. Put the keys in a file and pass `--aes-keys-file keys.json`:

```json
{
  "default": "0x0123...CDEF",
  "8F3A2C1B0D4E5F6A7B8C9D0E1F2A3B4C": "0xFEDC...3210"
}
```

A plain text file works too: one `GUID KEY` pair per line, or a line with only a key for the default key; lines starting with `#` are ignored.

- Marauder **does not find, extract or break keys**, and ships none. Where the key comes from is up to you.
- The key is used in memory only. It is never written to the output, the report or the screen.
- A wrong key is reported (`the supplied AES key does not open this archive`), never silently ignored. If your key opens none of the encrypted archives, Marauder stops with an error.
- The key path is tested on synthetic archives that the tests encrypt themselves with a random key. The accuracy numbers below are for unencrypted games only.

## Supported games and limitations

- **Engine versions:** UE 4.17 to 5.7 by design; tested on UE 4.19 to 5.7 (no UE 4.17 or 4.18 game was in the benchmark). UE 4.13 to 4.16 are out of scope.
- **Tested platform:** Windows x64 shipping builds of games (`*-Win64-Shipping.exe`). Linux, Mac and console builds of games are untested. (Marauder itself runs on Windows and Linux.)
- **Protected executables** (packers, anti-tamper wrappers that hide the executable's data) are out of scope. Marauder does not unpack or bypass them; it stops with a clear message when it finds no type tables.
- **Custom engine builds** (studios that changed the engine's reflection code) can give weaker results. In our tests, one large modded game on a custom engine build failed.
- **Blueprint-defined types** (Blueprint classes, user-defined structs and enums) are not in the mapping, the same as a runtime dump made with Blueprint types excluded. Readers get those from the packages themselves.
- **Anim Blueprint structs:** a record goes into the global map only when every copy in the start-up set is identical; otherwise it is left out and the per-asset mapping has all copies (see above).
- **Letter case:** some property names can differ from a runtime dump in letter case only. CUE4Parse looks names up without regard to case, so this did not change any loaded package in our tests.
- **Oodle:** many games compress their archives with Oodle. See [Troubleshooting](#troubleshooting-and-faq).

## Accuracy

Measured against runtime dumps (UE4SS, Blueprint types excluded) of the games in the [benchmark list](benchmark/benchmark-list.csv). A *field* is one header value of a type (super, property count) or one value of a property (name, full type tree, index, array size), or one enum entry; both missing and extra records count as wrong. Full definitions are in the paper.

Field accuracy of this release's configuration (public spelling table):

| Set | Games | Fields right |
|---|---:|---:|
| Development (UE 4.19 to 5.7) | 66 | 99.923% |
| Hold-out | 31 | 99.885% |
| Fresh blind hold-out (frozen before scoring) | 24 | 99.867% |

- By engine version (development set, research configuration): UE 4.19 to 4.27 99.990% (99.999% ignoring letter case); UE 5.0 to 5.7 99.89% to 99.90%.
- 94% of the remaining errors on the fresh set are the Anim Blueprint structs described above, which the global map leaves out on purpose. Without that class, accuracy is 99.989% to 99.992% on all three sets.
- In a real-use test with CUE4Parse over 188,493 packages from 102 games, the generated mappings loaded every package that the runtime dumps loaded on all supported engine versions; the only visible difference was the letter case of some property names.

Source: A. Antoniadis, *Recovering Unreal Engine type mappings from game files alone*, Sourcemined, 2026 (link to be added at release). The paper also reports results per rule, per engine version, the research configuration of the spelling rule (99.923%, 99.886% and 99.867% on the three sets), and every look at the hold-out data.

## Reproducing the results

[`benchmark/benchmark-list.csv`](benchmark/benchmark-list.csv) lists every benchmark game: split, Steam app ID, exact Steam build ID, depot manifest IDs, engine version, status (scored, or excluded and why), the SHA-256 of the runtime reference mapping, the SHA-256 of the map scored in the paper and the SHA-256 of the map this release writes. No game files, reference mappings or generated mappings are distributed.

To check a game you own:

1. Download the listed build, for example with [DepotDownloader](https://github.com/SteamRE/DepotDownloader) and the listed depot manifest IDs (Steam serves the newest build by default).
2. Capture a reference with [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS): `DumpUSMAP(false)` (Blueprint types excluded), at the main menu, offline. Keep it only when two captures give the same schema. Compare its SHA-256 with `gold_sha256`.
3. Run `marauder generate` on the install folder; its SHA-256 should equal `released_generated_sha256`. Then run `marauder compare` against your reference; the result should equal the score columns.

The `research_generated_sha256` column is the hash of the map scored in the paper, made with the research configuration of the spelling rule, which uses runtime dumps that are not distributed, so it cannot be regenerated outside. This release uses the public spelling table, so its output can differ from that hash in the letter case of a few names; `released_generated_sha256` and the score columns describe the released output. [docs/reproducing.md](docs/reproducing.md) has the details, including how to rebuild the spelling table from your own captures with [`tools/build_spelling_table.py`](tools/build_spelling_table.py).

## Troubleshooting and FAQ

**FModel or CUE4Parse says mappings are missing, or properties fail to read.** Generate a `.usmap` for the exact game version you have, and load it (below). After every game update, generate it again: class layouts change with updates.

<a id="using-the-file-in-fmodel-and-cue4parse"></a>**Using the file in FModel and CUE4Parse.** FModel: *Settings*, then *Local Mapping File*: enable it and select your `.usmap`. CUE4Parse:

```csharp
provider.MappingsContainer = new FileUsmapTypeMappingsProvider(@"C:\path\MyGame.usmap");
```

**"no Unreal Engine shipping executable found".** Pass the install folder (the one with `Engine/` and the project folder), or pass the executable with `--exe`. Launchers in the top folder are not the game executable; the real one is in `<Project>/Binaries/Win64/`.

**"no Unreal Engine type records found" or "no reflection tables could be read".** The executable is protected or packed, not an Unreal Engine game, or not a Windows x64 build. These are out of scope.

**Oodle.** Many games compress archives with Oodle, which is not open source. On Windows, Marauder uses the `oo2core_*_win64.dll` that the game ships, when it finds one. Otherwise set `UE_OODLE_LIB` to the Oodle runtime library for your system (Windows: `oo2core_9_win64.dll`; Linux: a native `liboo2corelinux64.so`) before running Marauder. Without it, Oodle-compressed entries are listed as not read and the map is less complete.

**"engine unknown" / "no spelling table".** Some executables do not store the engine version. Marauder then estimates it from the type names ("estimated from the type names" in the output, `engineVersionBasis: "type names"` in the report). Pass `--engine-version X.Y` if you know it.

**The output differs from my runtime dump in letter case.** Expected for a few names (see Limitations). The `compare` command shows how many differences are case only.

**It is slow or uses a lot of memory.** Large games take longer. Close other programs; very large games need several GB of free RAM.

**Something else.** Run again with `--report report.json` and `-v`, and open an issue with the command and the output. Set `MARAUDER_DEBUG=1` to get the full error trace.

## Credits

Marauder builds on the work of the Unreal modding community. Thank you to the authors of:

- [CUE4Parse](https://github.com/FabianFG/CUE4Parse) and [FModel](https://github.com/4sval/FModel): readers of cooked Unreal assets and the main users of `.usmap` files;
- [UnrealMappingsDumper](https://github.com/TheNaeem/UnrealMappingsDumper): the `.usmap` format;
- [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS): its mapping dumper produced our reference mappings;
- [Dumper-7](https://github.com/Encryqed/Dumper-7), [UEDumper](https://github.com/Spuckwaffel/UEDumper) and [jmap](https://github.com/trumank/jmap): runtime dumpers whose public work and issue trackers informed this project.

Developed by Anastasios Antoniadis at [Sourcemined](https://sourcemined.com). Contact: anantoni@sourcemined.com

## Paper

Antoniadis, A. *Marauder: Recovering Unreal Engine type mappings from game files alone.* Zenodo, 2026. [doi:10.5281/zenodo.24461105](https://doi.org/10.5281/zenodo.24461105)

## License

[MIT](LICENSE). Marauder contains no game data and no keys.
