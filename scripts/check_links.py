#!/usr/bin/env python3
"""Check that relative links in Markdown files resolve to existing files.

Covers [text](path) and bare-path references in code spans are skipped.
Usage: python3 scripts/check_links.py
"""
from __future__ import annotations

import os
import re
import sys

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    bad = []
    md_files = []
    for base in (REPO, os.path.join(REPO, "docs")):
        for name in sorted(os.listdir(base)):
            if name.endswith(".md"):
                md_files.append(os.path.join(base, name))
    for path in md_files:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for target in LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if not os.path.exists(resolved):
                bad.append("%s -> %s" % (os.path.relpath(path, REPO), target))
    for entry in bad:
        print("BROKEN:", entry)
    print("%d markdown files checked, %d broken links" % (len(md_files), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
