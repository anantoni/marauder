#!/usr/bin/env python3
"""Check every http(s) URL in the tracked documentation with curl; each must answer 200.

usage: python tools/check_links.py [--pending URL ...]
--pending  URLs that are expected to exist only after publication (reported, not failed)
"""
import argparse, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = re.compile(r"https?://[^\s)<>\"'`\]]+")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--pending", nargs="*", default=[])
    a = ap.parse_args()
    files = subprocess.run(["git", "ls-files", "*.md", "*.toml", "*.yml", "src/*.py", "tools/*.py"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split()
    urls = {}
    for f in files:
        for u in URL.findall((ROOT / f).read_text(encoding="utf-8")):
            u = u.rstrip(".,;:")
            if "{" in u or u.endswith("/v1"): continue
            urls.setdefault(u, f)
    bad = 0
    for u in sorted(urls):
        code = subprocess.run(["curl", "-sL", "-o", "/dev/null" if sys.platform != "win32" else "NUL", "-w", "%{http_code}",
                               "--max-time", "30", "-A", "link-check", u], capture_output=True, text=True).stdout.strip()
        pending = any(u.startswith(p) for p in a.pending)
        status = "ok" if code == "200" else ("pending (after publication)" if pending else "FAIL")
        bad += status == "FAIL"
        print(f"{code} {status:28} {u}   ({urls[u]})")
    print(f"links: {len(urls)} checked, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
