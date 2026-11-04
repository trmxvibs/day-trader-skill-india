#!/usr/bin/env python3
# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""Copy tools/dhan_block.py into every skill script that carries the Dhan Data block.

Each skill is a standalone script (a skill folder can be installed alone), so the open-source
live data client (Dhan Data API, or --offline) is copied, not imported, the same way
tools/nse_block.py is copied by tools/sync_nse_block.py. This keeps the copies identical:

    python -B tools/sync_dhan_block.py          # rewrite the block in every script
    python -B tools/sync_dhan_block.py --check  # exit 1 if any copy differs (the tests run this)
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "tools", "dhan_block.py")
BEGIN = "# ---- BEGIN DHAN DATA BLOCK"
END = "# ---- END DHAN DATA BLOCK ----"
SCRIPTS = (
    "trader-entry/entry.py",
    "trader-exit/exit.py",
    "trader-scanner/scan.py",
    "trader-timemachine/timemachine.py",
    "trader-alerts/alerts.py",
    "trader-news/news.py",
)


def block():
    with open(SOURCE, encoding="utf-8") as fh:
        text = fh.read()
    i, j = text.index(BEGIN), text.index(END) + len(END)
    return text[i:j]


def splice(text, new):
    """Replace the block in text; ValueError if the markers are missing or doubled."""
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError("needs exactly one BEGIN and one END marker")
    i, j = text.index(BEGIN), text.index(END) + len(END)
    return text[:i] + new + text[j:]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    new = block()
    bad = []
    for rel in SCRIPTS:
        path = os.path.join(ROOT, *rel.split("/"))
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        try:
            out = splice(text, new)
        except ValueError as e:
            print("%s: %s" % (rel, e), file=sys.stderr)
            bad.append(rel)
            continue
        if out != text:
            if a.check:
                bad.append(rel)
                print("%s: Dhan block differs from tools/dhan_block.py" % rel, file=sys.stderr)
            else:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(out)
                print("updated %s" % rel)
    if bad:
        return 1
    if a.check:
        print("Dhan block identical in %d scripts." % len(SCRIPTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
