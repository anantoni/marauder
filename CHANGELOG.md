# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## 1.0.0 (unreleased)

Initial public release.

- `marauder generate`: builds a `.usmap` (version 4, uncompressed) from an installed Unreal Engine
  4.17 to 5.7 game (tested on 4.19 to 5.7): executable reflection tables, cooked archives (`.pak`, IoStore, loose files) and
  configuration files. Six progress steps; every archive or entry not read is listed with its reason.
- `--per-asset`: per-asset mapping of UE5 Anim Blueprint generated structs in JSON and binary
  (same content; format in `docs/per-asset-format.md`).
- `--aes-key` / `--aes-keys-file`: read encrypted archives with the user's own AES-256 key.
- `--engine-refs`: use engine type relations from reference mappings to improve data-only struct naming.
- For a SteamStub-wrapped executable, verify plain `.text` before static links; the wrapper section
  stays unread, and links fall back off if plainness or constructor coverage is insufficient.
- UE5 maps always contain the `AnimBlueprintGeneratedStruct` record.
- Public spelling table keyed by engine version (16 versions, 140 rows, plus pooled tables per major
  version; identifiers and counts only), built from this release's own output on the development games.
- Executables without an engine build string: the minor version is estimated from the reflected type
  names (public marker list), so the spelling votes still apply; otherwise the pooled major table is used.
- The report always lists `code_sections_read`, `wrapper_sections_unread` and `plain_code_rejected`
  (under `codeSections` and `generator.staticCodeLinks`), and step 2 prints which code sections were read.
- `marauder compare`: field and record accuracy against a reference mapping, with the paper's metric.
- `marauder inspect`: print a mapping's classes, structs and enums.
- Benchmark list with Steam app IDs, build IDs, depot manifests and SHA-256 hashes.
- Full-load benchmark (`benchmark/fullload/`): harness, load-check patch, reader-fix patch and scripts; the harness streams each export's JSON into its hashes.
