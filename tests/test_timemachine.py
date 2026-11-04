"""trader-timemachine tests, NSE India version.

Fixture: tests/fixtures/nse/RELIANCE-60d5m.json.gz, a real Yahoo range=60d interval=5m answer
for RELIANCE.NS saved 2026-11-01 (sessions 2026-08-05 to 2026-10-30, all after the Closing
Auction Session go-live, so 72 continuous bars a day). The plan is the 2026-10-15 trade from
test_entry.py / test_exit.py, read at 09:56 IST.

The original pack proved bit-for-bit equality with the reel's own sim.py. Here the proof is an
independent reference written from the timemachine.py docstring alone (ref_sessions, ref_sim,
ref_history below): the code must give exactly the same numbers.
"""

import datetime
import gzip
import io
import json
import math
import os
import random
import shutil
import statistics
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

REL = os.path.join(FIX, "nse", "RELIANCE-60d5m.json.gz")
with gzip.open(REL, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-10-15 09:56"
NOW_UTC = tm.parse_now(NOW)

# the plan as trader-exit writes it for 2026-10-15
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-15", "setup": "orb",
        "entry": 1186.1, "entry_time": "9:55 AM", "entry_bar": 7, "interval": "5m",
        "opening_range": {"high": 1185.0, "low": 1177.4, "minutes": 15},
        "stop": 1181.2, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1191.0, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 4.9, "reward_risk": 1.0, "flat_by": "15:00", "tick": 0.1, "currency": "INR"}


# ---------------------------------------------------------------- the independent reference

def ref_sessions(text, plan_date, n=72):
    """Full sessions before plan_date: every 09:15 + 5k slot below n, prices in whole paise."""
    r = json.loads(text)["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    days = {}
    for i, t in enumerate(r["timestamp"]):
        o, h, l, c = (q[k][i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c) or t % 300:
            continue
        ist = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=t, hours=5, minutes=30)
        mins = ist.hour * 60 + ist.minute
        slot = (mins - 555) // 5
        if mins < 555 or slot >= n:
            continue
        days.setdefault(ist.date().isoformat(), {})[slot] = {"o": round(o, 2), "h": round(h, 2), "l": round(l, 2),
                                                             "c": round(c, 2)}
    return [(d, [days[d][i] for i in range(n)]) for d in sorted(days)
            if d < plan_date and all(i in days[d] for i in range(n))]


def ref_sim(sessions, entry, stop, target, k, last, replays=10000, seed=19, window=2):
    """Long only. Spec: slot j = clamp(i + randint(-w, w), 1, n-1), one past bar from pool[j],
    stop checked before target, R at the target / -1 / price at the end."""
    n = len(sessions[0][1])
    pool = [[] for _ in range(n)]
    for _, D in sessions:
        for i in range(1, n):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    rng = random.Random(seed)
    res, ends = [], []
    for _ in range(replays):
        p, out = entry, None
        for i in range(k + 1, last + 1):
            j = i if window == 0 else min(n - 1, max(1, i + rng.randint(-window, window)))
            o, h, l, c = rng.choice(pool[j])
            if out is None:
                if p * l <= stop:
                    out = "stop"
                elif p * h >= target:
                    out = "target"
            p = p * c
        res.append(out or "close")
        ends.append(p)
    risk = entry - stop
    rt = round((target - entry) / risk, 4)
    R = [rt if o == "target" else -1.0 if o == "stop" else (e - entry) / risk for o, e in zip(res, ends)]
    srt = sorted(ends)

    def pick(q):
        return srt[min(len(srt) - 1, int(q * len(srt)))]
    return {"p_target": round(res.count("target") / replays, 4), "p_stop": round(res.count("stop") / replays, 4),
            "p_neither": round(res.count("close") / replays, 4), "median_close": round(pick(0.5), 2),
            "p5_close": round(pick(0.05), 2), "p95_close": round(pick(0.95), 2), "avg_R": round(statistics.mean(R), 3)}


def ref_tick(price):
    return (0.01 if price < 250 else 0.05 if price <= 1000 else 0.10 if price <= 5000 else
            0.50 if price <= 10000 else 1.00 if price <= 20000 else 5.00)


def ref_history(sessions, or_bars=3, target_r=1.0, last=68, window=(570, 870)):
    """Orb long, or-mid stop down to the tick, target down to the tick, trigger bar CLOSE time
    inside 09:30..14:30, stop first, else the close of bar `last`."""
    out = []
    for d, D in sessions:
        oh = max(b["h"] for b in D[:or_bars])
        ol = min(b["l"] for b in D[:or_bars])
        kk = next((i for i in range(or_bars, last) if window[0] <= 555 + 5 * (i + 1) <= window[1] and D[i]["c"] > oh),
                  None)
        if kk is None:
            continue
        e = D[kk]["c"]
        tp = round(ref_tick(e) * 100)
        s = math.floor(round((oh + ol) / 2 * 100, 6) / tp) * tp / 100
        risk = round(e - s, 2)
        t = math.floor(round((e + target_r * risk) * 100, 6) / tp) * tp / 100
        oc = None
        for b in D[kk + 1:last + 1]:
            if b["l"] <= s:
                oc = "stop"
                break
            if b["h"] >= t:
                oc = "target"
                break
        R = (t - e) / risk if oc == "target" else -1.0 if oc == "stop" else (D[last]["c"] - e) / risk
        out.append({"date": d, "entry": e, "stop": round(s, 2), "target": round(t, 2), "outcome": oc or "close",
                    "R": round(R, 2)})
    return out


SESSIONS = ref_sessions(TEXT, "2026-10-15")


# ---------------------------------------------------------------- helpers

def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """SYNTHETIC: the fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheDay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:00"))  # the flat-by
        self.assertEqual(m["pool_sessions"], 49)
        self.assertEqual(m["bars_left"], 61)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-08-05", "2026-10-14"))
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["p_green"]), (0.4537, 0.5077, 0.0386, 0.4733))
        self.assertEqual((m["median_close"], m["p5_close"], m["p95_close"]), (1185.45, 1171.71, 1199.31))
        self.assertEqual(m["avg_R"], -0.055)
        self.assertEqual(m["r_at_target"], 1.0)

    def test_matches_the_independent_reference(self):
        self.assertEqual(len(SESSIONS), 49)
        ref = ref_sim(SESSIONS, 1186.1, 1181.2, 1191.0, 7, 68)
        m = self.doc["mc"]
        self.assertEqual({k: m[k] for k in ref}, ref)

    def test_the_history_replay_matches_the_reference(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (49, 22))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (11, 9, 2, 12))
        self.assertEqual(h["avg_R"], 0.086)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0,
                                     "tick": 0.1})
        slim = [{k: t[k] for k in ("date", "entry", "stop", "target", "outcome", "R")} for t in h["trades"]]
        self.assertEqual(slim, ref_history(SESSIONS))
        for t in h["trades"]:                               # every trigger closes inside 9:30 .. 2:30 PM
            mins = tm.parse_clock(t["time"], "t")
            self.assertTrue(570 <= mins <= 870, t)

    def test_the_verdict_says_coin_flip(self):
        self.assertEqual(self.doc["verdict"], "Close to a coin flip: target first 45.4 % vs stop first 50.8 %.")
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), even), (0.472, 0.5))
        self.assertEqual(self.doc["verdict_notes"], [])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [1186.1] * 5)
        self.assertEqual(len(m["bands"]), 62)
        self.assertEqual((m["band_times"][0], m["band_times"][-1]), ("09:55", "15:00"))
        self.assertEqual(m["bands"][-1], [1171.86, 1179.66, 1185.18, 1190.92, 1199.17])
        self.assertTrue(all(p[0] == 1186.1 and len(p) == 62 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("RELIANCE", 1186.1, 1181.2, 1191.0, 7, "9:55 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (4.9, 1.0, "15:00"))
        self.assertEqual(self.doc["warnings"], [])

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("RELIANCE buy, Thursday 2026-10-15: entry 1186.10 at 9:55 AM (bar 7), stop 1181.20, "
                     "target 1191.00", "10,000 replays", "61 five-minute bars", "seed 19", "49 full sessions",
                     "target first   45.4 %", "stop first     50.8 %", "still open at 3:00 PM",
                     "median 1185.45, 90 % of replays between 1171.71 and 1199.31", "Average R: -0.055",
                     "fired 22 times: 11 target, 9 stop, 2 neither. Average R +0.086", "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", "$", "New York"):
            self.assertNotIn(word, text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        self.assertEqual(machine(replays=2000, seed=5)["mc"], machine(replays=2000, seed=5)["mc"])

    def test_another_seed_gives_other_numbers_and_still_matches_the_reference(self):
        m = machine(seed=7, replays=3000)["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"]), (0.4537, 0.5077))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        ref = ref_sim(SESSIONS, 1186.1, 1181.2, 1191.0, 7, 68, replays=3000, seed=7)
        self.assertEqual({k: m[k] for k in ref}, ref)

    def test_the_draws_are_stable_across_pythons(self):
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(59)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        ref = ref_sim(SESSIONS, 1186.1, 1181.2, 1191.0, 7, 68, replays=500, window=0)
        self.assertEqual({k: a[k] for k in ref}, ref)
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "RELIANCE")

    def test_auction_stamps_and_unfinished_bars_are_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        # 15:15/15:20/15:25 IST stamps are Closing Auction Session artifacts: outside continuous trading
        self.assertGreater(dropped["outside_hours"], 0)
        self.assertTrue(all(b["slot"] < 72 for b in bars))
        today = [b for b in bars if b["date"] == "2026-10-15"]
        self.assertEqual((today[-1]["start"], today[-1]["slot"]), ("09:50", 7))   # the 9:55 bar is unfinished
        self.assertEqual(today[-1]["c"], 1186.1)                                   # whole paise, not 1186.0999755

    def test_now_pins_the_clock(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-15 09:54"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:45 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-15 09:55"), replays=200)["mc"]["pool_sessions"], 49)

    def test_ist_has_no_daylight_time(self):
        stamps = [1793331900,   # 2026-10-30 03:45Z = 09:15 IST (US still on EDT)
                  1793591100,   # 2026-11-02 03:45Z = 09:15 IST (US now on EST)
                  1793590200,   # 2026-11-02 03:30Z = 09:00 IST, the pre-open auction
                  1793611800]   # 2026-11-02 09:30Z = 15:00 IST
        res = {"meta": {"symbol": "X"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 4, "high": [1] * 4, "low": [1] * 4, "close": [1] * 4,
                                         "volume": [0] * 4}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-03T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-10-30", "09:15", 0), ("2026-11-02", "09:15", 0), ("2026-11-02", "15:00", 69)])
        self.assertEqual(dropped["outside_hours"], 1)

    def test_before_the_auction_session_a_day_has_75_bars(self):
        self.assertEqual(tm.continuous_bars(datetime.date(2026, 7, 31)), 75)
        self.assertEqual(tm.continuous_bars(datetime.date(2026, 8, 3)), 72)
        res = {"meta": {"symbol": "X"}, "timestamp": [1785469500 + 300 * i for i in range(76)],   # 2026-07-31 09:15 IST
               "indicators": {"quote": [{"open": [1] * 76, "high": [1] * 76, "low": [1] * 76, "close": [1] * 76,
                                         "volume": [0] * 76}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-08-01T00:00Z"))
        self.assertEqual((len(bars), bars[-1]["start"], dropped["outside_hours"]), (75, "15:25", 1))

    def test_a_session_with_a_hole_is_left_out(self):
        def cut(res):  # SYNTHETIC: 2026-09-04 loses its 12:00 bar
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-04" and ist(ts).strftime("%H:%M") ==
                                           "12:00"))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 48)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-04", "bars": 71}])
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 1 short session", "\n".join(tm.report(doc)))

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-14")
        self.assertNotIn("2026-10-15", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None   # SYNTHETIC: 2026-08-05 09:40
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["mc"]["pool_sessions"], 48)

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "RELIANCE.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-17")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-16")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_the_data_must_be_the_plans_symbol(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="HDFCBANK"))
        self.assertIn("data is for RELIANCE", str(cm.exception))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            plan(date="2026-10-20")                             # Dussehra
        self.assertIn("2026-10-20 is not an NSE trading day (NSE holiday: Dussehra)", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-16"), now=NOW_UTC)          # a trading day the data does not reach yet
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:              # 10:00 has not happened yet at 09:56
            machine(plan(entry_time="10:05 AM", entry_bar=None))
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="9:57 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:15 PM", entry_bar=None), "between 9:20 AM and 3:10 PM"),
            (dict(entry_bar=9), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "5-minute bars only"),
            (dict(stop=1187.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "NSE symbol"),
            (dict(symbol="RELIANCE CORP"), "NSE symbol"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 15"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_nse_symbols(self):
        for sym, want in (("M&M", "M&M"), ("BAJAJ-AUTO", "BAJAJ-AUTO"), ("reliance.ns", "RELIANCE"), ("^NSEI", "^NSEI")):
            self.assertEqual(plan(symbol=sym)["symbol"], want)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "9:55 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 7)
        self.assertEqual(plan(entry_time="9:55", entry_bar=None)["entry_bar"], 7)
        self.assertEqual(plan(entry_time="9:55am", entry_bar=None)["entry_bar"], 7)

    def test_until_cuts_the_replay(self):
        doc = machine(until="14:30", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("14:30", 55, "14:30"))
        self.assertIn("still open at 2:30 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="2:30 PM", replays=500)["mc"], m)
        self.assertEqual(machine(until="15:15", replays=50)["mc"]["bars_left"], 64)   # end of continuous trading
        self.assertEqual(machine(plan(flat_by=None), replays=50)["mc"]["until"], "15:15")
        for bad, words in (("9:55 AM", "nothing to replay"), ("15:20", "no later than 3:15 PM"),
                           ("14:57", "5-minute grid"), ("noon", "IST time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=1190.0, stop=1185.0, target=1195.0, stop_rule="pct"), replays=200)
        self.assertIn("outside that bar's range", doc["warnings"][0])

    def test_stops_round_to_the_tick_away_from_the_entry(self):
        self.assertEqual(tm.away_from_entry(1181.25, 0.10, True), 1181.2)
        self.assertEqual(tm.away_from_entry(1181.25, 0.10, False), 1181.3)
        self.assertEqual(tm.away_from_entry(1177.4000244140625, 0.10, True), 1177.4)
        self.assertEqual(tm.away_from_entry(680.175, 0.05, True), 680.15)
        self.assertEqual(tm.toward_entry(1193.45, 0.10, True), 1193.4)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=1177.4, target=1194.8), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=1180.0, target=1198.3), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 0.5143, 2.0))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 49)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=1191.0, target=1181.2, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        self.assertGreater(doc["history"]["triggered"], 0)
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:00"}),
                         "No replay reached the target or the stop by 3:00 PM: the price at 3:00 PM decides every one.")

    def test_a_2r_target_is_judged_against_its_break_even(self):
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_fixture_leans_toward_the_stop(self):
        doc = machine(plan(target=1195.9))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.2055, 0.5807, 0.2138, -0.075))
        ref = ref_sim(SESSIONS, 1186.1, 1181.2, 1195.9, 7, 68)
        self.assertEqual({k: m[k] for k in ref}, ref)
        self.assertEqual(doc["verdict"], "Leans toward the stop: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 26 % (target first 20.5 % vs stop "
                                         "first 58.1 %).")
        h = doc["history"]
        slim = [{k: t[k] for k in ("date", "entry", "stop", "target", "outcome", "R")} for t in h["trades"]]
        self.assertEqual(slim, ref_history(SESSIONS, target_r=2.0))


class Network(unittest.TestCase):
    """The live path (Dhan Data API): dhan_find_security and _dhan_post are swapped for
    fakes, so no test ever touches the real network or needs real credentials. --offline
    (the REL fixture, see Cli below) is a separate, unaffected code path."""

    def setUp(self):
        self._find, self._post, self._avail = tm.dhan_find_security, tm._dhan_post, tm.dhan_available
        tm.dhan_find_security = lambda sym, path=None: ("2885", "NSE_EQ", "EQUITY")
        tm.dhan_available = lambda: True

    def tearDown(self):
        tm.dhan_find_security, tm._dhan_post, tm.dhan_available = self._find, self._post, self._avail

    def dhan_payload(self):
        """The same RELIANCE bars as the REL fixture, reshaped into Dhan's own
        parallel-array response shape (timestamp/open/high/low/close/volume)."""
        r = json.loads(TEXT)["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        return {"timestamp": r["timestamp"], "open": q["open"], "high": q["high"],
                "low": q["low"], "close": q["close"], "volume": q["volume"]}

    def test_fetch_builds_the_same_bars_as_the_fixture(self):
        payload = self.dhan_payload()
        calls = []

        def fake(url, body, timeout=30):
            calls.append((url, body))
            return payload
        tm._dhan_post = fake
        got = json.loads(tm.dhan_chart_text("RELIANCE"))["chart"]["result"][0]
        # dhan_chart_text (via _dhan_bars_from_arrays) drops bars with no close, same as
        # parse_bars would later anyway; compare against the fixture filtered the same way.
        want_r = json.loads(TEXT)["chart"]["result"][0]
        want_q = want_r["indicators"]["quote"][0]
        want_ts, want_c = [], []
        for t, c in zip(want_r["timestamp"], want_q["close"]):
            if t is not None and c is not None:
                want_ts.append(t)
                want_c.append(round(c, 2))
        self.assertEqual(got["timestamp"], want_ts)
        self.assertEqual(got["indicators"]["quote"][0]["close"], want_c)
        self.assertEqual(calls[0][0], tm.DHAN_INTRADAY_URL)
        self.assertEqual(calls[0][1]["securityId"], "2885")
        self.assertEqual(calls[0][1]["interval"], 5)

    def test_http_error_from_dhan_becomes_a_tm_error(self):
        def fake(url, body, timeout=30):
            raise tm.DhanError("Dhan answered HTTP 429: rate limited")
        tm._dhan_post = fake
        with self.assertRaises(tm.TMError) as cm:
            tm.dhan_chart_text("RELIANCE")
        self.assertIn("HTTP 429", str(cm.exception))

    def test_still_busy_is_one_plain_error(self):
        def fake(url, body, timeout=30):
            raise tm.DhanError("Dhan answered HTTP 429: rate limited")
        tm._dhan_post = fake
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, out, err = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 1)
            self.assertIn("HTTP 429", err)
            self.assertNotIn("Traceback", err)
        finally:
            shutil.rmtree(d)

    def test_missing_credentials_is_a_clear_message(self):
        saved = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        try:
            with self.assertRaises(tm.DhanError) as cm:
                tm.dhan_credentials()
            self.assertIn("no Dhan credentials", str(cm.exception))
            self.assertIn("--offline", str(cm.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_run_over_the_network_path(self):
        payload = self.dhan_payload()
        tm._dhan_post = lambda url, body, timeout=30: payload
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
        finally:
            shutil.rmtree(d)


class FakeYahooResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class YahooFallback(unittest.TestCase):
    """The automatic tier-3 fallback: no Dhan key, no --offline, so compute() reads
    Yahoo's free chart endpoint directly and warns about it every time."""

    def setUp(self):
        self._avail = tm.dhan_available
        self._urlopen = tm.urllib.request.urlopen
        self._env = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        tm.dhan_available = lambda: False

    def tearDown(self):
        tm.dhan_available = self._avail
        tm.urllib.request.urlopen = self._urlopen
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_fetch_yahoo_chart_text_matches_the_fixture(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeYahooResponse(TEXT.encode("utf-8"))
        tm.urllib.request.urlopen = fake_urlopen
        text = tm.fetch_yahoo_chart_text("RELIANCE")
        self.assertIn("query2.finance.yahoo.com", calls[0])
        self.assertIn("RELIANCE.NS", calls[0])
        self.assertEqual(text, TEXT)

    def test_retries_the_alternate_host_on_429(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "query2" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return FakeYahooResponse(TEXT.encode("utf-8"))
        tm.urllib.request.urlopen = fake_urlopen
        text = tm.fetch_yahoo_chart_text("RELIANCE")
        self.assertEqual(len(calls), 2)
        self.assertIn("query1", calls[1])
        self.assertEqual(text, TEXT)

    def test_a_real_http_error_does_not_retry_and_raises_cleanly(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        tm.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_yahoo_chart_text("RELIANCE")
        self.assertEqual(len(calls), 1)
        self.assertIn("HTTP 404", str(cm.exception))

    def test_a_read_timeout_does_not_crash_and_raises_cleanly(self):
        # a read timeout (TimeoutError/socket.timeout) is an OSError, not a urllib.error.URLError
        def fake_urlopen(req, timeout=None):
            raise TimeoutError("timed out")
        tm.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_yahoo_chart_text("RELIANCE")
        self.assertIn("timed out", str(cm.exception))

    def test_run_falls_back_to_yahoo_and_warns(self):
        def fake_urlopen(req, timeout=None):
            return FakeYahooResponse(TEXT.encode("utf-8"))
        tm.urllib.request.urlopen = fake_urlopen
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = tm.main(["run", "--plan", p, "--now", NOW, "--replays", "300", "--json"])
            self.assertEqual(code, 0)
            self.assertIn("WARNING", err.getvalue())
            self.assertIn("Rs 20.50", err.getvalue())
            self.assertEqual(json.loads(out.getvalue())["source"], "yahoo")
        finally:
            shutil.rmtree(d)

    def test_offline_wins_even_with_no_dhan_key(self):
        def fake_urlopen(req, timeout=None):
            raise AssertionError("offline must not touch the network")
        tm.urllib.request.urlopen = fake_urlopen
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = tm.main(["run", "--plan", p, "--offline", REL, "--now", NOW, "--replays", "300", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["source"], "offline")
        finally:
            shutil.rmtree(d)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   45.4 %", text)
        self.assertIn("Close to a coin flip: target first 45.4 % vs stop first 50.8 %.", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.4537, -0.055))
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (22, 0.086))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.5077)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:20], "Close to a coin flip")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "reliance", "--date", "2026-10-15", "--entry", "1186.10",
                               "--stop", "1181.20", "--target", "1191.00", "--entry-time", "9:55 AM", "--until", "15:00",
                               "--offline", REL, "--now", NOW, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.4537, 0.4733))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (11, 9, 0.086))
        code, _, err = run(["run", "--symbol", "RELIANCE", "--entry", "1186.10", "--offline", REL])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(REL, self.d)
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", REL, "--until", "9:00"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("2,000 replay paths from the 9:55 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("  1191.00 |") and l.endswith("  target") for l in lines), text)
        self.assertTrue(any(l.startswith("  1181.20 |") and l.endswith("  stop") for l in lines), text)
        self.assertTrue(any(l.endswith("  entry") for l in lines), text)
        self.assertIn("9:55", text)
        self.assertIn("3:00", text)
        self.assertEqual(lines[-1], "Close to a coin flip: target first 45.4 % vs stop first 50.8 %.")
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", REL, "--now", NOW])
        self.assertEqual(again, text)


if __name__ == "__main__":
    unittest.main()
