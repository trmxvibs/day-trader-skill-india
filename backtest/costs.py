#!/usr/bin/env python3
# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""Round-trip costs of an NSE intraday equity trade at Dhan, from costs.json. Standard library only.

    python3 -B costs.py --qty 102 --buy 1186.10 --sell 1191.00

Charges are computed from the rates in costs.json (each with its source and the date it was
checked) and rounded to the paisa per component, the way a contract note shows them.
Slippage is separate: an assumed 0.03% of price per side, applied to the fill prices by the
backtest, not part of the charges."""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path=None):
    with open(path or os.path.join(HERE, "costs.json"), encoding="utf-8") as fh:
        return json.load(fh)


def r2(x):
    return round(x + 1e-9, 2)


def charges(qty, buy, sell, cfg=None):
    """A long round trip: qty shares bought at buy and sold at sell. Returns rupees per component."""
    cfg = cfg or load()
    tb, ts = qty * buy, qty * sell
    brk = lambda v: min(cfg["brokerage_per_order_max_inr"], v * cfg["brokerage_per_order_pct"])
    out = {
        "brokerage": r2(brk(tb) + brk(ts)),
        "stt": r2(ts * cfg["stt_sell"]),
        "exchange_txn": r2((tb + ts) * cfg["exchange_txn"]),
        "sebi_fee": r2((tb + ts) * cfg["sebi_fee"]),
        "ipft": r2((tb + ts) * cfg["ipft"]),
        "stamp": r2(tb * cfg["stamp_buy"]),
    }
    out["gst"] = r2(cfg["gst"] * sum(out[k] for k in cfg["gst_on"]))
    out["total"] = r2(sum(out.values()))
    return out


def slipped(price, side, cfg=None):
    """A fill price after slippage: a buy fills higher, a sell lower."""
    cfg = cfg or load()
    s = cfg["slippage_per_side"]
    return price * (1 + s) if side == "buy" else price * (1 - s)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--qty", type=int, required=True)
    ap.add_argument("--buy", type=float, required=True)
    ap.add_argument("--sell", type=float, required=True)
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    cfg = load(a.config)
    c = charges(a.qty, a.buy, a.sell, cfg)
    for k, v in c.items():
        print("%-13s Rs %10.2f" % (k, v))
    print("rates checked %s at %s; slippage %.2f%% a side is extra" % (
        cfg["date_checked"], cfg["broker"], cfg["slippage_per_side"] * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
