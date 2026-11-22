# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## 1.0.0 (unreleased)

Initial public release.

- `marauder generate`: builds a `.usmap` (version 4, uncompressed) from an installed Unreal Engine
  4.17 to 5.7 game: executable reflection tables, cooked archives (`.pak`, IoStore, loose files) and
  configuration files. Six progress steps; every archive or entry not read is listed with its reason.
- `--per-asset`: per-asset mapping of UE5 Anim Blueprint generated structs in JSON and binary
  (same content; format in `docs/per-asset-format.md`).
- `--aes-key` / `--aes-keys-file`: read encrypted archives with the user's own AES-256 key.
- `--engine-refs`: use engine type relations from reference mappings to improve data-only struct naming.
- For a SteamStub-wrapped executable, verify plain `.text` before static links; the wrapper section
  stays unread, and links fall back off if plainness or constructor coverage is insufficient.
- UE5 maps always contain the `AnimBlueprintGeneratedStruct` record.
- Public spelling table keyed by engine version (16 versions, 262 rows; identifiers and counts only).
- `marauder compare`: field and record accuracy against a reference mapping, with the paper's metric.
- `marauder inspect`: print a mapping's classes, structs and enums.
- Benchmark list with Steam app IDs, build IDs, depot manifests and SHA-256 hashes.
