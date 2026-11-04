#!/usr/bin/env python3
"""Opening-range-breakout backtest on NSE 5-minute bars, India version. Standard library only
(matplotlib, if installed, draws the PNG charts; without it the charts are skipped).

    python3 -B backtest.py fetch --data ../../data           # Nifty 100 list + 60 days of 5-min bars, cached
    python3 -B backtest.py run   --data ../../data --out ../../results
    python3 -B backtest.py run   --data ../../data --out ../../results --no-tm   # skip the time machine check

PAPER RESEARCH ONLY. Nothing here places an order or talks to a broker. Not financial advice.

DATA WARNING (Phase 4, 2026-11-01): Yahoo's NSE 5-minute bars proved unreliable for this test.
Against Dhan's chart the 09:15 bar's high was short by up to Rs 20.50 (one of three trades checked
flipped from a loss to a win), and on 31-37% of symbol-days the 5-minute bars miss the day's high or
low. Yahoo is therefore removed as a backtest source: `fetch` is disabled, and `run` only reads the
old Yahoo cache with --legacy-yahoo, stamping every output UNRELIABLE. A broker or exchange feed
(Dhan's Data API) is the intended replacement; it is not built yet.

The baseline rule is the one the skills use (trader-entry, trader-exit, trader-timemachine):
  - opening range = the first 15 minutes (09:15-09:30 IST, three 5-minute bars)
  - long only: the first 5-minute bar that CLOSES above the range high, with that close
    between 09:30 and 14:30 IST; one trade per stock per day
  - entry = that bar's close (the skills' plan), filled 0.03% worse (slippage)
  - stop = the range midpoint, rounded down to the NSE tick; target = 1R, rounded down;
    a stop closer than max(0.05% of price, one tick) is refused, as trader-exit refuses it
  - bar by bar after the entry: a bar that opens through the stop exits at its open (a gap
    is worse than the stop), a bar that touches the stop exits at the stop, a bar that
    touches the target exits at the target; stop first when one bar touches both;
    otherwise out at the close of the 14:55 bar (flat by 15:00 IST)
  - every exit fill is 0.03% worse (slippage); Dhan / NSE / SEBI / stamp / GST charges from
    costs.json on every round trip
  - size = whole shares for a Rs 500 loss at the stop, capped at Rs 2,00,000 an order

Two views of the same signals:
  - every signal: each trade on its own (does the rule have an edge per trade?)
  - portfolio: one Rs 2,00,000 paper account under the example config (10% cash buffer,
    no margin, max 3 trades a day, Rs 1,500 daily loss), signals taken in time order
    while cash allows

Splits: the trading days are split by date, the first 60% in-sample (IS), the rest
out-of-sample (OOS). Variants are compared on IS; OOS is reported for every one of them
so a variant that only won IS by luck shows it. Confidence intervals are a day-clustered
bootstrap (seed 19): whole days are resampled, because trades on one day move together.
"""

import argparse
import csv
import datetime
import gzip
import importlib.util
import json
import math
import os
import random
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(folder, name):
    spec = importlib.util.spec_from_file_location("bt_" + name, os.path.join(ROOT, folder, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tm = _load("trader-timemachine", "timemachine")     # bars, sessions, ticks, the Monte Carlo
scan = _load("trader-scanner", "scan")              # the Nifty 100 list parser
costs = _load("backtest", "costs")                  # Dhan / NSE charges

OPEN_MIN = tm.OPEN_MIN
SEED = 19
BOOT = 5000
IS_SHARE = 0.60
CONFIG = {"budget": 200000.0, "cash_buffer_pct": 10.0, "max_position_pct": 75.0, "max_order_inr": 200000.0,
          "min_order_inr": 500.0, "max_loss_per_trade_inr": 500.0, "max_daily_loss_inr": 1500.0,
          "max_trades_per_day": 3}
BASE = {"name": "baseline", "or_minutes": 15, "entry_from": "09:30", "entry_until": "14:30", "flat_by": "15:00",
        "stop_rule": "or_mid", "target_r": 1.0, "fill": "close", "side": "buy", "min_value_cr": 25.0}
VARIANTS = [
    dict(BASE),
    dict(BASE, name="or30", or_minutes=30, entry_from="09:45"),
    dict(BASE, name="target2R", target_r=2.0),
    dict(BASE, name="stop_or_low", stop_rule="or_low"),
    dict(BASE, name="next_open_fill", fill="next_open"),
    dict(BASE, name="start_0945", entry_from="09:45"),
    dict(BASE, name="until_1130", entry_until="11:30"),
    dict(BASE, name="short_side", side="sell"),
]
MIN_RISK_PCT = 0.05


def hm(text):
    return int(text[:2]) * 60 + int(text[3:])


def clock(mins):
    return "%02d:%02d" % (mins // 60, mins % 60)


# ---------------------------------------------------------------- data

def fetch(a):
    """The Nifty 100 constituent file and one 60-day 5-minute chart per symbol, into --data."""
    os.makedirs(os.path.join(a.data, "yahoo5m"), exist_ok=True)
    idx = os.path.join(a.data, "ind_nifty100list.csv")
    if not os.path.exists(idx) or a.refresh:
        text = None
        for base in scan.INDEX_URLS:
            try:
                text = scan.http_get(base + "ind_nifty100list.csv", timeout=60)
                scan.parse_index_csv(text)
                print("index list from %s" % (base + "ind_nifty100list.csv"))
                break
            except Exception as e:  # try the next mirror
                print("index list: %s failed (%s)" % (base, e))
                text = None
        if text is None:
            print("could not download the Nifty 100 list", file=sys.stderr)
            return 1
        with open(idx, "w", encoding="utf-8") as fh:
            fh.write(text)
    syms = universe(a.data)[0]
    got, failed = 0, []
    stamp = datetime.date.today().isoformat()
    for s in syms + ["^NSEI"]:
        path = os.path.join(a.data, "yahoo5m", "%s-60d5m.json.gz" % s)
        if os.path.exists(path) and not a.refresh:
            continue
        try:
            text = tm.fetch_chart(s)
            tm.chart_result(text, s)
        except Exception as e:
            failed.append("%s: %s" % (s, e))
            continue
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
        got += 1
        time.sleep(a.pause)
    with open(os.path.join(a.data, "yahoo5m", "FETCHED.txt"), "a", encoding="utf-8") as fh:
        fh.write("%s fetched %d, failed %d\n" % (stamp, got, len(failed)))
    print("fetched %d charts, %d failed" % (got, len(failed)))
    for f in failed:
        print("  " + f)
    return 0 if not failed else 1


def universe(data):
    with open(os.path.join(data, "ind_nifty100list.csv"), encoding="utf-8-sig") as fh:
        rows = scan.parse_index_csv(fh.read())
    kept, counts = scan.build_universe(rows)
    return [s for s, _ in kept], counts


def dropped_days(data, syms):
    """Trading days left out for more than half the symbols (holes in the data, not one stock)."""
    cnt = {}
    for s in syms:
        path = os.path.join(data, "yahoo5m", "%s-60d5m.json.gz" % s)
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            res = tm.chart_result(fh.read(), s)
        bars = tm.parse_bars(res, datetime.datetime(2100, 1, 1))[1]
        for d, S in tm.split_sessions(bars).items():
            if len(S) < tm.continuous_bars(datetime.date.fromisoformat(d), tm.INTERVAL):
                cnt[d] = cnt.get(d, 0) + 1
    return sorted(d for d, n in cnt.items() if n > len(syms) / 2)


def load_sessions(data, sym):
    """{date: [72 bars]} full continuous sessions only, plus the count left out."""
    path = os.path.join(data, "yahoo5m", "%s-60d5m.json.gz" % sym)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        text = fh.read()
    res = tm.chart_result(text, sym)
    now = datetime.datetime(2100, 1, 1)
    meta, bars, dropped = tm.parse_bars(res, now)
    days = tm.split_sessions(bars)
    full, short = {}, 0
    for d in sorted(days):
        n = tm.continuous_bars(datetime.date.fromisoformat(d), tm.INTERVAL)
        s = days[d]
        if all(i in s for i in range(n)):
            full[d] = [s[i] for i in range(n)]
        else:
            short += 1
    return full, short


# ---------------------------------------------------------------- one trade

def plan_for(D, rule):
    """(trigger bar index, plan dict) or (None, reason)."""
    long = rule["side"] == "buy"
    n = rule["or_minutes"] // 5
    oh = max(b["h"] for b in D[:n])
    ol = min(b["l"] for b in D[:n])
    last = (hm(rule["flat_by"]) - OPEN_MIN) // 5 - 1
    a, b = hm(rule["entry_from"]), hm(rule["entry_until"])
    k = None
    for i in range(n, last):
        close_min = OPEN_MIN + 5 * (i + 1)
        if a <= close_min <= b and (D[i]["c"] > oh if long else D[i]["c"] < ol):
            k = i
            break
    if k is None:
        return None, "no trigger"
    if rule["fill"] == "next_open" and k + 1 > last:
        return None, "no bar left"
    e = round(D[k]["c"], 2)
    tk = tm.tick_for(e)
    if rule["stop_rule"] == "or_mid":
        s = tm.away_from_entry((oh + ol) / 2, tk, long)
    else:
        s = tm.away_from_entry(ol if long else oh, tk, long)
    risk = round((e - s) if long else (s - e), 2)
    if risk < max(e * MIN_RISK_PCT / 100, tk) - 1e-9:
        return None, "stop too tight"
    t = tm.toward_entry(e + rule["target_r"] * risk if long else e - rule["target_r"] * risk, tk, long)
    return k, {"entry": e, "stop": s, "target": t, "risk": risk, "or_high": oh, "or_low": ol, "tick": tk,
               "last": last, "trigger_close": clock(OPEN_MIN + 5 * (k + 1))}


def size(plan, cfg):
    e, risk = plan["entry"], plan["risk"]
    cash = cfg["budget"] * (1 - cfg["cash_buffer_pct"] / 100)
    q = min(int(cfg["max_loss_per_trade_inr"] / risk + 1e-9),
            int(cfg["max_order_inr"] / e + 1e-9),
            int(cfg["budget"] * cfg["max_position_pct"] / 100 / e + 1e-9),
            int(cash / e + 1e-9))
    if q < 1 or q * e < cfg["min_order_inr"]:
        return 0
    return q


def walk(D, k, plan, rule, slip):
    """Run the plan bar by bar. Returns (exit price before slippage, outcome, exit bar, entry price)."""
    long = rule["side"] == "buy"
    s, t, last = plan["stop"], plan["target"], plan["last"]
    if rule["fill"] == "next_open":
        start = k + 1
        e_px = D[start]["o"]
    else:
        start = k + 1
        e_px = plan["entry"]
    # in next_open mode the entry bar itself can hit the stop or target after the open
    first = start if rule["fill"] == "next_open" else start
    for j in range(first, last + 1):
        b = D[j]
        if long:
            if b["o"] <= s:
                return b["o"], "gap stop", j, e_px
            if b["l"] <= s:
                return s, "stop", j, e_px
            if b["h"] >= t:
                return t, "target", j, e_px
        else:
            if b["o"] >= s:
                return b["o"], "gap stop", j, e_px
            if b["h"] >= s:
                return s, "stop", j, e_px
            if b["l"] <= t:
                return t, "target", j, e_px
    return D[last]["c"], "flat 15:00", last, e_px


def trade(sym, d, D, rule, cfg, ccfg):
    k, plan = plan_for(D, rule)
    if k is None:
        return None, plan
    q = size(plan, cfg)
    if q == 0:
        return None, "size zero"
    long = rule["side"] == "buy"
    slip = ccfg["slippage_per_side"]
    x_px, outcome, j, e_px = walk(D, k, plan, rule, slip)
    e_fill = e_px * (1 + slip) if long else e_px * (1 - slip)
    x_fill = x_px * (1 - slip) if long else x_px * (1 + slip)
    if long:
        c = costs.charges(q, e_fill, x_fill, ccfg)
        gross = q * (x_fill - e_fill)
    else:  # a short sells first and buys back: STT on the sell, stamp on the buy
        c = costs.charges(q, x_fill, e_fill, ccfg)
        gross = q * (e_fill - x_fill)
    risk_rs = q * plan["risk"]
    move = (x_px - e_px) if long else (e_px - x_px)
    net = gross - c["total"]
    return {
        "symbol": sym, "date": d, "variant": rule["name"], "side": rule["side"],
        "trigger_close": plan["trigger_close"], "exit_time": clock(OPEN_MIN + 5 * (j + 1)),
        "entry_bar": k, "exit_bar": j,
        "or_high": plan["or_high"], "or_low": plan["or_low"],
        "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"], "risk": plan["risk"],
        "qty": q, "entry_fill": round(e_fill, 4), "exit_price": round(x_px, 2), "exit_fill": round(x_fill, 4),
        "outcome": outcome, "R_rule": round(move / plan["risk"], 4),
        "gross_inr": round(gross, 2), "costs_inr": c["total"], "net_inr": round(net, 2),
        "R_net": round(net / risk_rs, 4), "notional_inr": round(q * e_fill, 2), "risk_inr": round(risk_rs, 2),
    }, "trade"


# ---------------------------------------------------------------- stats

def boot_ci(trades, key, days, seed=SEED, n=BOOT):
    """95% day-clustered bootstrap CI of the mean of key, plus the share of resamples <= 0."""
    by = {}
    for t in trades:
        by.setdefault(t["date"], []).append(t[key])
    ds = [d for d in days if d in by]
    if len(ds) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        vals = []
        for _ in ds:
            vals.extend(by[rng.choice(ds)])
        means.append(sum(vals) / len(vals))
    means.sort()
    return {"lo": round(means[int(0.025 * n)], 4), "hi": round(means[int(0.975 * n) - 1], 4),
            "p_le_0": round(sum(1 for m in means if m <= 0) / n, 4)}


def max_drawdown(values):
    peak = cum = dd = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 2)


def stats(trades, days):
    if not trades:
        return {"n": 0}
    net = [t["net_inr"] for t in trades]
    rn = [t["R_net"] for t in trades]
    rr = [t["R_rule"] for t in trades]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    oc = {}
    for t in trades:
        oc[t["outcome"]] = oc.get(t["outcome"], 0) + 1
    ordered = sorted(trades, key=lambda t: (t["date"], t["trigger_close"], t["symbol"]))
    return {
        "n": len(trades), "days_with_trades": len({t["date"] for t in trades}),
        "win_rate_net": round(len(wins) / len(trades), 4),
        "avg_R_rule": round(statistics.mean(rr), 4), "avg_R_net": round(statistics.mean(rn), 4),
        "median_R_net": round(statistics.median(rn), 4),
        "sd_R_net": round(statistics.pstdev(rn), 4),
        "sum_net_inr": round(sum(net), 2), "sum_gross_inr": round(sum(t["gross_inr"] for t in trades), 2),
        "sum_costs_inr": round(sum(t["costs_inr"] for t in trades), 2),
        "avg_costs_inr": round(statistics.mean(t["costs_inr"] for t in trades), 2),
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
        "max_drawdown_inr": max_drawdown(t["net_inr"] for t in ordered),
        "outcomes": oc,
        "ci_R_net": boot_ci(trades, "R_net", days), "ci_R_rule": boot_ci(trades, "R_rule", days),
    }


def portfolio(trades, cfg):
    """Take signals in time order under the example account's rules. Returns the trades taken."""
    taken = []
    by = {}
    for t in trades:
        by.setdefault(t["date"], []).append(t)
    for d in sorted(by):
        cash = cfg["budget"] * (1 - cfg["cash_buffer_pct"] / 100)
        open_ = []          # (exit_bar, notional)
        risk_used = 0.0
        n = 0
        for t in sorted(by[d], key=lambda x: (x["entry_bar"], x["symbol"])):
            # positions that exited at or before this entry bar free their cash
            still = []
            for xb, notional in open_:
                if xb <= t["entry_bar"]:
                    cash += notional
                else:
                    still.append((xb, notional))
            open_ = still
            if n >= cfg["max_trades_per_day"]:
                break
            if risk_used + t["risk_inr"] > cfg["max_daily_loss_inr"] + 1e-9:
                continue
            if t["notional_inr"] > cash + 1e-9:
                continue
            cash -= t["notional_inr"]
            open_.append((t["exit_bar"], t["notional_inr"]))
            risk_used += t["risk_inr"]
            n += 1
            taken.append(t)
    return taken


# ---------------------------------------------------------------- time machine check

def tm_check(sym, sessions, rows, max_per_symbol=None, replays=1000):
    """For each trade, the time machine's target-first odds from the sessions before its day."""
    tm.BAND_PATHS = 1          # the fan-chart bands are not needed here
    tm.SAMPLE_PATHS = 0
    dates = sorted(sessions)
    out = []
    for t in rows[:max_per_symbol] if max_per_symbol else rows:
        before = [(d, sessions[d]) for d in dates if d < t["date"]]
        if len(before) < tm.MIN_SESSIONS:
            continue
        pool = tm.build_pool(before)
        mc = tm.simulate(pool, t["entry"], t["stop"], t["target"], t["entry_bar"],
                         (hm(BASE["flat_by"]) - OPEN_MIN) // 5 - 1, replays=replays, seed=SEED)
        out.append({"symbol": sym, "date": t["date"], "p_target": mc["p_target"], "p_stop": mc["p_stop"],
                    "p_neither": mc["p_neither"], "mc_avg_R": mc["avg_R"], "outcome": t["outcome"],
                    "R_rule": t["R_rule"], "R_net": t["R_net"]})
    return out


def calibration(rows):
    bins = [(0, 0.40), (0.40, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 1.01)]
    out = []
    for lo, hi in bins:
        sel = [r for r in rows if lo <= r["p_target"] < hi]
        if not sel:
            continue
        out.append({"p_target_bin": "%.2f-%.2f" % (lo, min(hi, 1.0)), "n": len(sel),
                    "mean_predicted": round(statistics.mean(r["p_target"] for r in sel), 4),
                    "realized_target": round(sum(1 for r in sel if r["outcome"] == "target") / len(sel), 4),
                    "mean_R_net": round(statistics.mean(r["R_net"] for r in sel), 4)})
    return out


# ---------------------------------------------------------------- outputs

FIELDS = ["variant", "symbol", "date", "side", "trigger_close", "exit_time", "or_high", "or_low", "entry", "stop",
          "target", "risk", "qty", "entry_fill", "exit_price", "exit_fill", "outcome", "R_rule", "gross_inr",
          "costs_inr", "net_inr", "R_net", "notional_inr", "risk_inr", "entry_bar", "exit_bar"]


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def charts(out, base_all, base_pf, tmrows, days):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return ["matplotlib is not installed: PNG charts skipped"]
    made = []
    # 1. equity curves (net rupees), every signal and the portfolio
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for rows, label in ((base_all, "every signal (sum of all trades)"), (base_pf, "portfolio (Rs 2 lakh account rules)")):
        by = {}
        for t in rows:
            by[t["date"]] = by.get(t["date"], 0.0) + t["net_inr"]
        cum, ys = 0.0, []
        for d in days:
            cum += by.get(d, 0.0)
            ys.append(cum)
        ax.plot(range(len(days)), ys, label=label)
    split = int(len(days) * IS_SHARE)
    ax.axvline(split - 0.5, color="grey", ls="--", lw=1)
    ax.text(split, 0.04, " out-of-sample", color="grey", fontsize=8, transform=ax.get_xaxis_transform())
    ax.text(split - 1, 0.04, "in-sample ", color="grey", fontsize=8, ha="right", transform=ax.get_xaxis_transform())
    ax.axhline(0, color="black", lw=0.6)
    ax.set_xticks(range(0, len(days), max(1, len(days) // 8)))
    ax.set_xticklabels([days[i][5:] for i in range(0, len(days), max(1, len(days) // 8))])
    ax.set_ylabel("cumulative net P&L, Rs (after costs and slippage)")
    ax.set_title("Baseline ORB, Nifty 100, %s to %s (Yahoo data: UNRELIABLE)" % (days[0], days[-1]))
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    p = os.path.join(out, "equity.png")
    fig.savefig(p, dpi=110)
    plt.close(fig)
    made.append(p)
    # 2. distribution of net R per trade
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist([t["R_net"] for t in base_all], bins=40)
    ax.axvline(0, color="black", lw=0.8)
    ax.set_xlabel("net R per trade (1R = the planned risk)")
    ax.set_ylabel("trades")
    ax.set_title("Baseline: net R per trade, every signal (n=%d)" % len(base_all))
    fig.tight_layout()
    p = os.path.join(out, "r_distribution.png")
    fig.savefig(p, dpi=110)
    plt.close(fig)
    made.append(p)
    # 3. time machine calibration
    cal = calibration(tmrows)
    if cal:
        fig, ax = plt.subplots(figsize=(5.5, 5))
        xs = [c["mean_predicted"] for c in cal]
        ys = [c["realized_target"] for c in cal]
        ax.plot([0.3, 0.7], [0.3, 0.7], color="grey", ls="--", lw=1, label="perfect calibration")
        ax.scatter(xs, ys, s=[max(15, c["n"] / 3) for c in cal])
        for c in cal:
            ax.annotate("n=%d" % c["n"], (c["mean_predicted"], c["realized_target"]), fontsize=7,
                        xytext=(4, 4), textcoords="offset points")
        ax.set_xlabel("time machine: predicted target-first share")
        ax.set_ylabel("backtest: real target-first share")
        ax.set_title("Time machine vs what happened")
        ax.legend(fontsize=8)
        fig.tight_layout()
        p = os.path.join(out, "timemachine_calibration.png")
        fig.savefig(p, dpi=110)
        plt.close(fig)
        made.append(p)
    return made


def fmt_ci(ci):
    return "n/a" if not ci else "%+.3f to %+.3f" % (ci["lo"], ci["hi"])


def summary_md(doc):
    b = doc["baseline"]
    L = []
    L.append("# ORB backtest, NSE Nifty 100 (paper research)")
    L.append("")
    L.append("> **UNRELIABLE DATA (Yahoo).** A direct check against Dhan's own chart found Yahoo's NSE 5-minute "
             "bars miss highs and lows (the 09:15 bar's high was short by up to Rs 20.50 against Dhan's chart; "
             "31-37% of days miss the day's high or low). The cost result (about -0.28 R a trade from charges "
             "and slippage) holds; the before-cost numbers below are only a rough view.")
    L.append("")
    L.append("Generated %s from Yahoo 5-minute bars, %s to %s (%d trading days; IS %s to %s, OOS %s to %s). "
             "Not financial advice. Past results, especially over 60 days, say little about the future."
             % (doc["generated"], doc["days"][0], doc["days"][-1], len(doc["days"]), doc["is_days"][0],
                doc["is_days"][-1], doc["oos_days"][0], doc["oos_days"][-1]))
    L.append("")
    L.append("Universe: %d Nifty 100 symbols with data; %d passed the traded-value floor (median Rs %.0f crore a day)."
             % (doc["universe"]["with_data"], doc["universe"]["liquid"], BASE["min_value_cr"]))
    L.append("Costs: Dhan intraday rates checked %s (costs.json), slippage %.2f%% a side. Size: Rs 500 at the stop."
             % (doc["costs_checked"], doc["slippage_pct"]))
    L.append("")
    L.append("## Baseline: every signal")
    L.append("")
    L.append("| Sample | Trades | Win rate (net) | Avg R before costs | Avg R net | 95% CI avg R net | Net Rs | Costs Rs |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for key, label in (("all", "All"), ("is", "IS"), ("oos", "OOS")):
        s = b["every_signal"][key]
        if not s.get("n"):
            L.append("| %s | 0 | | | | | | |" % label)
            continue
        L.append("| %s | %d | %.1f%% | %+.3f | %+.3f | %s | %s | %s |" % (
            label, s["n"], 100 * s["win_rate_net"], s["avg_R_rule"], s["avg_R_net"], fmt_ci(s["ci_R_net"]),
            "{:,.0f}".format(s["sum_net_inr"]), "{:,.0f}".format(s["sum_costs_inr"])))
    s = b["every_signal"]["all"]
    L.append("")
    L.append("Outcomes: %s. Average costs a trade Rs %.2f. Max drawdown (all signals in time order) Rs %s."
             % (", ".join("%s %d" % kv for kv in sorted(s["outcomes"].items())), s["avg_costs_inr"],
                "{:,.0f}".format(s["max_drawdown_inr"])))
    L.append("")
    L.append("## Baseline: one Rs 2 lakh paper account (max 3 trades a day, no margin)")
    L.append("")
    p = b["portfolio"]["all"]
    if p.get("n"):
        L.append("%d trades on %d days, win rate %.1f%%, avg R net %+.3f (95%% CI %s), net Rs %s, max drawdown Rs %s."
                 % (p["n"], p["days_with_trades"], 100 * p["win_rate_net"], p["avg_R_net"], fmt_ci(p["ci_R_net"]),
                    "{:,.0f}".format(p["sum_net_inr"]), "{:,.0f}".format(p["max_drawdown_inr"])))
        po = b["portfolio"]["oos"]
        if po.get("n"):
            L.append("OOS only: %d trades, avg R net %+.3f, net Rs %s." % (po["n"], po["avg_R_net"],
                                                                         "{:,.0f}".format(po["sum_net_inr"])))
    L.append("")
    L.append("## Variants (every signal)")
    L.append("")
    L.append("| Variant | IS trades | IS avg R net | OOS trades | OOS avg R net | OOS 95% CI | All net Rs |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for v in doc["variants"]:
        i, o, a = v["is"], v["oos"], v["all"]
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            v["name"], i.get("n", 0), "%+.3f" % i["avg_R_net"] if i.get("n") else "",
            o.get("n", 0), "%+.3f" % o["avg_R_net"] if o.get("n") else "", fmt_ci(o.get("ci_R_net")),
            "{:,.0f}".format(a["sum_net_inr"]) if a.get("n") else ""))
    L.append("")
    L.append("Best on IS: **%s**. %s" % (doc["best_is"]["name"], doc["best_is"]["note"]))
    L.append("")
    L.append("Eight variants were tried on the same data. Picking the best of eight by chance alone is likely, "
             "so a variant that is better only in-sample is not evidence.")
    L.append("")
    L.append("## Time machine vs what happened")
    L.append("")
    c = doc["timemachine"]
    if c["n"]:
        L.append("%d baseline trades had at least %d earlier full sessions. For each, the time machine ran %d replays "
                 "(seed %d) from the sessions before that day." % (c["n"], tm.MIN_SESSIONS, c["replays"], SEED))
        L.append("")
        L.append("Mean predicted target-first %.1f%%, real target-first %.1f%%. Mean predicted avg R %+.3f, real "
                 "avg R (rule, before costs) %+.3f."
                 % (100 * c["mean_p_target"], 100 * c["real_target"], c["mean_mc_R"], c["real_R_rule"]))
        L.append("")
        L.append("| Predicted target-first | Trades | Mean predicted | Real target-first | Avg R net |")
        L.append("| --- | --- | --- | --- | --- |")
        for r in c["calibration"]:
            L.append("| %s | %d | %.1f%% | %.1f%% | %+.3f |" % (r["p_target_bin"], r["n"], 100 * r["mean_predicted"],
                                                            100 * r["realized_target"], r["mean_R_net"]))
    else:
        L.append("Skipped.")
    L.append("")
    L.append("## Honest reading")
    L.append("")
    for line in doc["reading"]:
        L.append("- " + line)
    L.append("")
    L.append("## Proposed scheduled job (not built)")
    L.append("")
    L.append("If the paper research continues, one weekday job after the close could keep the sample growing, "
             "because Yahoo only keeps 60 days of 5-minute bars:")
    L.append("")
    L.append("- 16:30 IST Monday to Friday, skip NSE holidays: `backtest.py fetch --refresh` into a dated folder "
             "(so older bars are kept), then `backtest.py run`, then append the day's paper trades to a log.")
    L.append("- No orders, no broker login, no alerts that say buy or sell. Nothing is set up until you ask.")
    L.append("")
    L.append("## Files")
    L.append("")
    for f in doc["files"]:
        L.append("- `%s`" % os.path.basename(f))
    L.append("")
    return "\n".join(L)


def reading(doc):
    b = doc["baseline"]["every_signal"]
    out = []
    a, o = b["all"], b["oos"]
    if a.get("n"):
        ci = a["ci_R_net"]
        if ci and ci["lo"] > 0:
            out.append("After costs the baseline's average R is above zero and the 95%% CI (%s) excludes zero over this "
                       "window. That is one 60-day window, not proof of an edge." % fmt_ci(ci))
        elif ci and ci["hi"] < 0:
            out.append("After costs the baseline loses: average R %+.3f a trade, 95%% CI %s, entirely below zero."
                       % (a["avg_R_net"], fmt_ci(ci)))
        else:
            out.append("After costs the baseline's average R is %+.3f a trade with a 95%% CI of %s, which includes zero: "
                       "no edge can be claimed from this data." % (a["avg_R_net"], fmt_ci(ci)))
        out.append("Costs and slippage take %+.3f R a trade on average (avg R %+.3f before, %+.3f after)."
                   % (a["avg_R_net"] - a["avg_R_rule"], a["avg_R_rule"], a["avg_R_net"]))
    if o.get("n") and a.get("n"):
        out.append("Out-of-sample (last 40%% of days): %d trades, average R net %+.3f." % (o["n"], o["avg_R_net"]))
    cr = doc.get("cost_ratio")
    if cr:
        out.append("Why costs hurt so much: the opening-range-midpoint stop is close, so a Rs 500 risk buys an average "
                   "position of Rs %s. Charges (avg Rs %.0f) plus 0.06%% round-trip slippage (avg Rs %.0f) are %.0f%% "
                   "of the risk on every trade, win or lose. A wider stop (stop_or_low) shrinks that share, which is "
                   "most of why it loses less." % ("{:,.0f}".format(cr["avg_notional"]), cr["avg_charges"],
                                                   cr["avg_slippage"], 100 * cr["share_of_risk"]))
    t = doc.get("timemachine") or {}
    if t.get("n"):
        out.append("The time machine's target-first odds were only weakly related to what happened (Brier %.3f vs "
                   "%.3f for one constant guess), and trades it rated above 55%% did no better after costs. Use it as "
                   "a coin-flip detector, not as a filter." % (t["brier"], t["brier_constant"]))
    dd = doc.get("dropped_days")
    if dd:
        out.append("Left out because the 5-minute data has holes on that day for most symbols: %s. "
                   "Phase 4 checks this against Dhan's charts." % ", ".join(dd))
    out.append("Only ~60 days of 5-minute data are free from Yahoo, so every number here has wide error bars. "
               "Yahoo NSE data is unofficial; Phase 4 checks a sample of trades against Dhan's charts.")
    out.append("Simplifications: fills at bar prices plus a fixed 0.03% slippage, no partial fills, no circuit limits, "
               "no news filter, a target that gaps over fills at the target (not better), one trade per stock per day.")
    return out


def run(a):
    os.makedirs(a.out, exist_ok=True)
    ccfg = costs.load()
    syms, ucounts = universe(a.data)
    sessions, short_days, missing = {}, {}, []
    for s in syms:
        try:
            sessions[s], short_days[s] = load_sessions(a.data, s)
        except (OSError, tm.TMError) as e:
            missing.append(s)
    if a.symbols:
        sessions = {s: v for s, v in sessions.items() if s in a.symbols}
    all_days = sorted({d for v in sessions.values() for d in v})
    # liquidity: median daily traded value (close x volume summed over the day's bars)
    liquid, value_cr = [], {}
    for s, days in sessions.items():
        vals = [sum(b["c"] * (b["v"] or 0) for b in D) for D in days.values()]
        value_cr[s] = round(statistics.median(vals) / 1e7, 1) if vals else 0
        if value_cr[s] >= BASE["min_value_cr"]:
            liquid.append(s)
    liquid.sort()
    split = int(len(all_days) * IS_SHARE)
    is_days, oos_days = all_days[:split], all_days[split:]
    is_set = set(is_days)
    rows_by_variant, skips = {}, {}
    for rule in VARIANTS:
        rows, sk = [], {}
        for s in liquid:
            for d, D in sorted(sessions[s].items()):
                t, why = trade(s, d, D, rule, CONFIG, ccfg)
                if t is None:
                    sk[why] = sk.get(why, 0) + 1
                else:
                    rows.append(t)
        rows_by_variant[rule["name"]] = rows
        skips[rule["name"]] = sk

    def three(rows):
        return {"all": stats(rows, all_days), "is": stats([r for r in rows if r["date"] in is_set], is_days),
                "oos": stats([r for r in rows if r["date"] not in is_set], oos_days)}

    base = rows_by_variant["baseline"]
    pf = portfolio(base, CONFIG)
    doc = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "days": all_days,
           "data_source": "yahoo (UNRELIABLE: 5-minute bars miss highs and lows)",
           "is_days": is_days, "oos_days": oos_days, "config": CONFIG, "baseline_rule": BASE,
           "costs_checked": ccfg["date_checked"], "slippage_pct": ccfg["slippage_per_side"] * 100,
           "universe": {"index_rows": ucounts.get("rows"), "symbols": len(syms), "with_data": len(sessions),
                        "missing": missing, "liquid": len(liquid),
                        "illiquid": sorted(s for s in sessions if s not in liquid),
                        "median_value_cr": value_cr, "short_sessions_dropped": short_days},
           "baseline": {"every_signal": three(base), "portfolio": three(pf), "skips": skips["baseline"]},
           "variants": []}
    for rule in VARIANTS:
        v = three(rows_by_variant[rule["name"]])
        v.update(name=rule["name"], rule=rule, skips=skips[rule["name"]])
        doc["variants"].append(v)
    cands = [v for v in doc["variants"] if v["is"].get("n")]
    best = max(cands, key=lambda v: v["is"]["avg_R_net"])
    note = ("Its OOS average R net is %+.3f on %d trades, versus the baseline's %+.3f."
            % (best["oos"].get("avg_R_net", float("nan")), best["oos"].get("n", 0),
               doc["baseline"]["every_signal"]["oos"].get("avg_R_net", float("nan"))))
    doc["best_is"] = {"name": best["name"], "note": note}
    if base:
        slip_rs = [t["qty"] * (t["entry_fill"] - t["entry"] + t["exit_price"] - t["exit_fill"]) for t in base]
        doc["cost_ratio"] = {
            "avg_notional": round(statistics.mean(t["notional_inr"] for t in base), 2),
            "avg_charges": round(statistics.mean(t["costs_inr"] for t in base), 2),
            "avg_slippage": round(statistics.mean(slip_rs), 2),
            "share_of_risk": round(statistics.mean((t["costs_inr"] + sr) / t["risk_inr"]
                                                   for t, sr in zip(base, slip_rs)), 4)}
    doc["dropped_days"] = dropped_days(a.data, liquid)
    # time machine check on the baseline trades
    tmrows = []
    if not a.no_tm:
        by = {}
        for t in base:
            by.setdefault(t["symbol"], []).append(t)
        for s in sorted(by):
            tmrows += tm_check(s, sessions[s], by[s], replays=a.tm_replays)
    doc["timemachine"] = {"n": len(tmrows), "replays": a.tm_replays, "calibration": calibration(tmrows)}
    if tmrows:
        doc["timemachine"].update(
            mean_p_target=round(statistics.mean(r["p_target"] for r in tmrows), 4),
            real_target=round(sum(1 for r in tmrows if r["outcome"] == "target") / len(tmrows), 4),
            mean_mc_R=round(statistics.mean(r["mc_avg_R"] for r in tmrows), 4),
            real_R_rule=round(statistics.mean(r["R_rule"] for r in tmrows), 4),
            brier=round(statistics.mean((r["p_target"] - (r["outcome"] == "target")) ** 2 for r in tmrows), 4),
            brier_constant=round(statistics.mean(
                (sum(1 for x in tmrows if x["outcome"] == "target") / len(tmrows) - (r["outcome"] == "target")) ** 2
                for r in tmrows), 4))
    files = []
    p = os.path.join(a.out, "trades.csv")
    write_csv(p, base, FIELDS)
    files.append(p)
    p = os.path.join(a.out, "trades_portfolio.csv")
    write_csv(p, pf, FIELDS)
    files.append(p)
    p = os.path.join(a.out, "trades_variants.csv")
    write_csv(p, [r for v in VARIANTS[1:] for r in rows_by_variant[v["name"]]], FIELDS)
    files.append(p)
    if tmrows:
        p = os.path.join(a.out, "timemachine_check.csv")
        write_csv(p, tmrows, list(tmrows[0]))
        files.append(p)
    notes = charts(a.out, base, pf, tmrows, all_days)
    files += [n for n in notes if n.endswith(".png")]
    doc["chart_notes"] = [n for n in notes if not n.endswith(".png")]
    files += [os.path.join(a.out, "summary.json"), os.path.join(a.out, "summary.md")]
    doc["files"] = files
    doc["reading"] = reading(doc)
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    with open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(summary_md(doc))
    s = doc["baseline"]["every_signal"]["all"]
    print("UNRELIABLE (Yahoo data) baseline: %d trades, avg R before costs %+.3f, after %+.3f (95%% CI %s), net Rs %s"
          % (s.get("n", 0), s.get("avg_R_rule", 0), s.get("avg_R_net", 0), fmt_ci(s.get("ci_R_net")),
             "{:,.0f}".format(s.get("sum_net_inr", 0))))
    print("wrote %s" % ", ".join(os.path.basename(f) for f in files))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--data", default=os.path.join(ROOT, "..", "data"))
    f.add_argument("--refresh", action="store_true", help="download again even if cached")
    f.add_argument("--pause", type=float, default=0.6)
    r = sub.add_parser("run")
    r.add_argument("--data", default=os.path.join(ROOT, "..", "data"))
    r.add_argument("--out", default=os.path.join(ROOT, "..", "results"))
    r.add_argument("--symbols", nargs="*", help="only these symbols")
    r.add_argument("--no-tm", action="store_true", help="skip the time machine comparison")
    r.add_argument("--tm-replays", type=int, default=1000)
    r.add_argument("--legacy-yahoo", action="store_true",
                   help="run on the old Yahoo cache; every output is marked UNRELIABLE")
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        print("backtest: Yahoo was removed as a data source (its NSE 5-minute bars miss highs and lows). "
              "A Dhan Data API fetch is not built yet.", file=sys.stderr)
        return 2
    if not a.legacy_yahoo:
        print("backtest: no reliable 5-minute data source is set up yet. To re-run the Phase 3 test on the old "
              "Yahoo cache, marked UNRELIABLE, pass --legacy-yahoo.", file=sys.stderr)
        return 2
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
