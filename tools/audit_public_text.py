#!/usr/bin/env python3
"""Release audit: every tracked file must be free of private names, paths and addresses.

usage: python tools/audit_public_text.py [--terms-file FILE] [--names-csv FILE --names-column COL]

--terms-file  extra words to forbid, one per line (kept outside the repository)
--names-csv   a CSV kept outside the repository whose column COL holds further forbidden names
              (hyphenated slugs are also checked with spaces)
Exit code 0 when there are no findings, 1 otherwise. Checks `git ls-files` (tracked files only).
"""
import argparse, csv, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN = [   # generic checks; project-specific words come from --terms-file (kept outside the repository)
    r"datamin\w*", r"(?<![\w.])/data/", r"(?<![\w.])/home/", r"(?<![\w.])/hdd/", r"[\w.+-]+@gmail\.com",
]
# "decrypt" is allowed only where the user-supplied AES key path is implemented or documented.
DECRYPT_OK_FILES = {"src/usmap_generator/engine/aes.py", "src/usmap_generator/engine/archives.py",
                    "src/usmap_generator/engine/iostore_probe.py", "src/usmap_generator/engine/key_provider.py",
                    "tests/test_encrypted_fixture.py", "tests/synthetic.py", "tools/audit_public_text.py"}


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms-file"); ap.add_argument("--names-csv"); ap.add_argument("--names-column", default="slug")
    a = ap.parse_args()
    pats = [(p, re.compile(p, re.I)) for p in FORBIDDEN]
    extra = set()
    if a.terms_file:
        extra |= {w.strip() for w in Path(a.terms_file).read_text(encoding="utf-8").splitlines() if w.strip()}
    if a.names_csv:
        with open(a.names_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                v = (row.get(a.names_column) or "").strip()
                if v: extra |= {v, v.replace("-", " ")}
    for w in sorted(extra):
        pats.append((f"name:{w}", re.compile(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", re.I)))
    decrypt = re.compile(r"decrypt", re.I)
    findings = 0
    for rel in tracked():
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue                                  # binary files: none are tracked except data JSON
        for n, line in enumerate(text.splitlines(), 1):
            for label, rx in pats:
                if rel == "tools/audit_public_text.py" and not label.startswith("name:") and "FORBIDDEN" not in label:
                    if line.lstrip().startswith(("r\"", "FORBIDDEN")): continue        # the pattern list itself
                if rx.search(line):
                    print(f"{rel}:{n}: [{label}] {line.strip()[:160]}"); findings += 1
            if decrypt.search(line) and rel not in DECRYPT_OK_FILES:
                lower = line.lower()
                if not (rel.endswith(".md") and "key" in lower):
                    print(f"{rel}:{n}: [decrypt] {line.strip()[:160]}"); findings += 1
    print(f"audit: {findings} finding(s) in {len(tracked())} tracked files")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
