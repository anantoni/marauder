# Contributing

Thank you for helping. Bug reports, fixes and support for more engine builds are welcome.

- **Bug reports:** open an issue with the command you ran, the full output (run with `-v` and
  `--report report.json`), the engine version and the game's store and build ID if you can share it.
  Never attach game files, mappings made from them or keys.
- **Pull requests:** keep them small and focused, add a test, and run `python -m pytest` and
  `ruff check .` before you push.
- **Tests use synthetic data only.** Fixtures are built by the tests (see `tests/synthetic.py`).
  Do not add game files, runtime dumps, generated mappings of real games or keys to the repository.
- **Scope:** Marauder reads files; it never runs, injects into or modifies a game, and it never
  finds, extracts or breaks keys. Changes that would do any of these will not be merged.

By contributing you agree that your contribution is licensed under the MIT license of this project.
