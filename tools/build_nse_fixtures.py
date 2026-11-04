#!/usr/bin/env python3
# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""Build the NSE test fixtures from saved, real Yahoo chart JSON. Standard library only.

    python -B tools/build_nse_fixtures.py ..\\data\\raw

The raw files are Yahoo v8 chart answers saved by
`curl ".../v8/finance/chart/RELIANCE.NS?range=60d&interval=5m"` on 2026-11-01 (a Sunday,
so the last session is Fri 2026-10-30). They are not committed (data\\ is a cache);
the fixtures cut from them are. Nothing here invents prices: a fixture is a slice of
a real answer, with meta trimmed the way a range=1d answer looks.

Writes tests/fixtures/nse/:
  RELIANCE-2026-10-15-5m.json   one full session (75 Yahoo stamps, 09:15 to 15:25 IST);
                                it has a real 11:45 and 11:50 gap (null bars on every
                                NSE symbol that day) and the Closing Auction Session
                                artifacts (15:15 tiny print or null, 15:20 null, 15:25 flat)
  RELIANCE-60d5m.json.gz        the whole 60-day answer (time machine, backtest)
  MM-2026-10-29-5m.json         M&M (symbol with "&"), one session
  NSEI-2026-10-30-5m.json       the Nifty 50 index (^NSEI), one session, with Yahoo's
                                stray 15:30 stamp
  DEMO-1m-synthetic.json        SYNTHETIC one-minute bars (not market data), the
                                original's demo re-stamped to start at 09:15 IST
  ind_nifty100list.csv          NSE's Nifty 100 constituent file, as downloaded 2026-11-01
  daily-spark-2026-10-30.json   Yahoo spark, 3 months of daily closes for six NSE symbols
  wall5m-2026-10-29.json.gz     {SYMBOL: one-day v8 chart} for RELIANCE, HDFCBANK, M&M,
                                BAJAJ-AUTO on 2026-10-29 (the scanner's orb replay)
"""

import copy
import datetime
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "fixtures", "nse")
IST = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)


def load(raw, name):
    with open(os.path.join(raw, name), encoding="utf-8") as fh:
        return json.load(fh)


def one_day(doc, day):
    """The same Yahoo answer cut to one IST date (meta kept, ranges trimmed)."""
    d = copy.deepcopy(doc)
    r = d["chart"]["result"][0]
    keep = [i for i, t in enumerate(r["timestamp"]) if (EPOCH + datetime.timedelta(seconds=t) + IST).date() == day]
    if not keep:
        raise SystemExit("no bars on %s" % day)
    r["timestamp"] = [r["timestamp"][i] for i in keep]
    q = r["indicators"]["quote"][0]
    for k in list(q):
        q[k] = [q[k][i] for i in keep]
    m = r["meta"]
    m["regularMarketTime"] = r["timestamp"][-1] + 300
    m["range"] = "1d"
    m.pop("tradingPeriods", None)
    m.pop("validRanges", None)
    # previousClose: the close of the last bar before this day in the same answer
    return d


def prev_close(doc, day):
    r = doc["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    last = None
    for i, t in enumerate(r["timestamp"]):
        if (EPOCH + datetime.timedelta(seconds=t) + IST).date() < day and q["close"][i] is not None:
            last = q["close"][i]
    return last


def write_json(name, d):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(d, fh, separators=(",", ":"))
        fh.write("\n")
    return path


def write_gz(name, d):
    path = os.path.join(OUT, name)
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:   # mtime 0: same bytes every build
            fh.write(json.dumps(d, separators=(",", ":")).encode("utf-8"))
    return path


def restamp_demo(src):
    """The original demo: 1-minute bars from 09:30 New York (13:30 UTC) on 2026-10-19.
    Moved to start at 09:15 IST (03:45 UTC) the same date. Synthetic, as before."""
    d = copy.deepcopy(src)
    r = d["chart"]["result"][0]
    old_open = int((datetime.datetime(2026, 10, 19, 13, 30) - EPOCH).total_seconds())
    new_open = int((datetime.datetime(2026, 10, 19, 3, 45) - EPOCH).total_seconds())
    delta = new_open - old_open
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    r["meta"]["timezone"] = "IST"
    r["meta"]["exchangeTimezoneName"] = "Asia/Kolkata"
    r["meta"]["gmtoffset"] = 19800
    return d


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    raw = argv[0] if argv else os.path.join(ROOT, "..", "data", "raw")
    os.makedirs(OUT, exist_ok=True)
    rel = load(raw, "RELIANCE.NS-60d5m.json")
    day = datetime.date(2026, 10, 15)
    d = one_day(rel, day)
    d["chart"]["result"][0]["meta"]["previousClose"] = prev_close(rel, day)
    print(write_json("RELIANCE-2026-10-15-5m.json", d))
    print(write_gz("RELIANCE-60d5m.json.gz", rel))
    mm = load(raw, "M&M.NS-60d5m.json")
    print(write_json("MM-2026-10-29-5m.json", one_day(mm, datetime.date(2026, 10, 29))))
    ix = load(raw, "^NSEI-60d5m.json")
    print(write_json("NSEI-2026-10-30-5m.json", one_day(ix, datetime.date(2026, 10, 30))))
    with open(os.path.join(raw, "ind_nifty100list.csv"), "rb") as src, \
            open(os.path.join(OUT, "ind_nifty100list.csv"), "wb") as dst:
        dst.write(src.read())
    print(write_json("daily-spark-2026-10-30.json", load(raw, "daily-spark-3mo.json")))
    wall = {}
    for sym, name in (("RELIANCE", "RELIANCE.NS"), ("HDFCBANK", "HDFCBANK.NS"), ("M&M", "M&M.NS"),
                      ("BAJAJ-AUTO", "BAJAJ-AUTO.NS")):
        wall[sym] = one_day(load(raw, name + "-60d5m.json"), datetime.date(2026, 10, 29))
    print(write_gz("wall5m-2026-10-29.json.gz", wall))
    demo_src = os.path.join(ROOT, "tests", "fixtures", "trader-entry", "DEMO-1m-synthetic.json")
    if os.path.exists(demo_src):
        with open(demo_src, encoding="utf-8") as fh:
            print(write_json("DEMO-1m-synthetic.json", restamp_demo(json.load(fh))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
