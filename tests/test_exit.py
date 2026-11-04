"""trader-exit tests, NSE India version. Bars for the ATR stop come from
tests/fixtures/nse/RELIANCE-2026-10-15-5m.json (real Yahoo bars, see test_entry.py).

Expected values by hand: entry 1186.10 (the 9:50 bar's close), opening range
1185.00 / 1177.40, midpoint 1181.20 (on the 0.10 grid), risk 4.90, 1R 1191.00,
2R 1195.90. Tick 0.10 because the price is above Rs 1,000 and up to 5,000.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

REL = os.path.join(FIX, "nse", "RELIANCE-2026-10-15-5m.json")
EM_DASH = chr(0x2014)

DAY_PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-15", "setup": "orb",
            "entry": 1186.1, "entry_time": "9:55 AM", "entry_bar": 7, "interval": "5m",
            "opening_range": {"high": 1185.0, "low": 1177.4, "minutes": 15},
            "stop": 1181.2, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
            "target": 1191.0, "target_r": 1.0, "target_label": "1R (one times the risk)",
            "risk_per_share": 4.9, "reward_risk": 1.0, "flat_by": "15:00", "tick": 0.1, "currency": "INR"}
DAY_ENTRY = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": "2026-10-15",
             "status": "triggered", "time": "9:55 AM", "bar_start": "09:50", "bar_index": 7, "price": 1186.1,
             "level": 1185.0, "interval": "5m", "opening_range": {"high": 1185.0, "low": 1177.4, "minutes": 15},
             "source": "yahoo", "checked_at": "2026-10-15T04:25:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="1R", oh=1185.0, ol=1177.4, atr=None, n=None, tick=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n, tick)


class Math(unittest.TestCase):
    def test_the_day_or_mid_1r(self):
        p = c(1186.10, "or-mid", "1R")
        self.assertEqual(p["tick"], Decimal("0.1"))
        self.assertEqual(p["stop"], Decimal("1181.20"))
        self.assertEqual(p["risk"], Decimal("4.90"))
        self.assertEqual(p["target"], Decimal("1191.00"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertNotIn("rounded", p["stop_detail"])           # the midpoint is already on the grid

    def test_2r(self):
        p = c(1186.10, "or-mid", "2R")
        self.assertEqual(p["target"], Decimal("1195.90"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_rupees(self):
        p = c(1186.0999755859375, "or-mid", "1R", 1185.0, 1177.4000244140625)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("1186.10"), Decimal("1181.20"),
                                                                Decimal("1191.00")))

    def test_stops_round_down_to_the_tick_away_from_the_entry(self):
        p = c(1186.10, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1177.40"), Decimal("8.70"), Decimal("1194.80")))
        p = c(1186.10, "price:1181.27")
        self.assertEqual(p["stop"], Decimal("1181.20"))                     # down to the 0.10 tick
        self.assertIn("rounded down to the Rs 0.1 tick", p["stop_detail"])
        p = c(1186.10, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1174.20"), Decimal("11.90"), Decimal("1198.00")))
        p = c(1186.10, "atr:1.5", atr=3.8, n=8)
        self.assertEqual((p["stop"], p["risk"]), (Decimal("1180.40"), Decimal("5.70")))
        self.assertEqual(p["stop_label"], "1.5 x ATR(8) under the entry")

    def test_a_005_tick_stock(self):
        # HDFCBANK-like prices (Rs 250 to 1,000 band): midpoint 680.175 -> 680.15
        p = c(683.30, "or-mid", "1R", 682.95, 677.40)
        self.assertEqual(p["tick"], Decimal("0.05"))
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("680.15"), Decimal("3.15"), Decimal("686.45")))
        p = c(683.30, "or-mid", "1.5R", 682.95, 677.40)
        self.assertEqual(p["target"], Decimal("688.00"))                    # 688.025 -> down to 688.00

    def test_targets_round_toward_the_entry(self):
        p = c(1186.10, "or-mid", "1.5R")
        self.assertEqual(p["target"], Decimal("1193.40"))                   # 1193.45 -> 1193.40
        self.assertEqual(p["reward_risk"], Decimal("1.49"))
        self.assertIn("rounded down to the tick (1.49R)", p["target_label"])
        p = c(1186.10, "or-mid", "price:1192")
        self.assertEqual(p["reward_risk"], Decimal("1.20"))
        self.assertEqual(p["target_label"], "your price (1.2R)")
        self.assertEqual(p["target_r"], Decimal("1.20"))

    def test_tick_override_and_band_edges(self):
        self.assertEqual(c(1186.10, "price:1181.27", tick=0.05)["stop"], Decimal("1181.25"))
        self.assertEqual(c(1000.00, "pct:1", oh=None, ol=None)["tick"], Decimal("0.05"))
        self.assertEqual(c(1000.05, "pct:1", oh=None, ol=None)["tick"], Decimal("0.1"))
        self.assertEqual(c(249.99, "pct:1", oh=None, ol=None)["tick"], Decimal("0.01"))
        self.assertEqual(c(250.00, "pct:1", oh=None, ol=None)["tick"], Decimal("0.05"))

    def test_refusals_say_why(self):
        cases = [("price:1186.10", "1R", "at or above the entry"),
                 ("price:1190", "1R", "at or above the entry"),
                 ("or-mid", "price:1186", "at or below the entry"),
                 ("price:1185.60", "1R", "too tight to survive the spread"),
                 ("pct:0.04", "1R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1186.10, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 1186.10 is 0.593: a Rs 0.60 risk is allowed, Rs 0.50 is not
        self.assertEqual(c(1186.10, "price:1185.50")["risk"], Decimal("0.60"))
        with self.assertRaises(exitp.PlanError):
            c(1180.00, "or-mid")                                    # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(1186.10, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1186.10, "or-mid", oh=1177.4, ol=1185.0)
        self.assertEqual(exitp.parse_stop("price:Rs 1,181.20"), ("price", Decimal("1181.20")))

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, REL)
        day = exitp.datetime.date(2026, 10, 15)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=7)
        self.assertEqual(len(bars), 8)                              # 9:15 ... the 9:50 entry bar
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 1185.0 - 1178.7, places=4)  # the 9:15 bar, no overnight gap
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 8)
        self.assertAlmostEqual(atr, sum(trs) / 8, places=9)
        self.assertAlmostEqual(atr, 3.8, places=4)
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 70)                         # 72 continuous slots, 11:45 and 11:50 missing
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)


class Network(unittest.TestCase):
    """The live path for an atr stop (Dhan Data API): dhan_find_security and _dhan_post
    are swapped for fakes, so no test ever touches the real network or needs real
    credentials. --offline (the REL fixture) is covered by Math.test_atr_on_the_session_bars."""

    def setUp(self):
        self._find, self._post, self._avail = exitp.dhan_find_security, exitp._dhan_post, exitp.dhan_available
        exitp.dhan_find_security = lambda sym, path=None: ("2885", "NSE_EQ", "EQUITY")
        exitp.dhan_available = lambda: True

    def tearDown(self):
        exitp.dhan_find_security, exitp._dhan_post, exitp.dhan_available = self._find, self._post, self._avail

    def dhan_payload(self):
        """The same RELIANCE bars as the REL fixture, reshaped into Dhan's own
        parallel-array response shape (timestamp/open/high/low/close/volume)."""
        with open(REL) as fh:
            r = json.load(fh)["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        return {"timestamp": r["timestamp"], "open": q["open"], "high": q["high"],
                "low": q["low"], "close": q["close"], "volume": q["volume"]}

    def test_fetch_builds_the_same_bars_as_the_fixture(self):
        payload = self.dhan_payload()
        calls = []

        def fake(url, body, timeout=30):
            calls.append((url, body))
            return payload
        exitp._dhan_post = fake
        ch = exitp.load_chart("RELIANCE", "5m", exitp.datetime.date(2026, 10, 15))
        self.assertEqual(len(ch["bars"]), 72)                      # 75 stamps; 11:45, 11:50, 15:20 null
        self.assertEqual(calls[0][0], exitp.DHAN_INTRADAY_URL)
        self.assertEqual(calls[0][1]["securityId"], "2885")
        self.assertEqual(calls[0][1]["interval"], 5)
        bars = exitp.session_bars(ch, exitp.datetime.date(2026, 10, 15), "5m", upto_slot=7)
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 8)
        self.assertAlmostEqual(atr, 3.8, places=4)

    def test_http_error_from_dhan_becomes_a_plan_error(self):
        def fake(url, body, timeout=30):
            raise exitp.DhanError("Dhan answered HTTP 429: rate limited")
        exitp._dhan_post = fake
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.load_chart("RELIANCE", "5m", exitp.datetime.date(2026, 10, 15))
        self.assertIn("HTTP 429", str(cm.exception))

    def test_missing_credentials_is_a_clear_message(self):
        saved = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        try:
            with self.assertRaises(exitp.DhanError) as cm:
                exitp.dhan_credentials()
            self.assertIn("no Dhan credentials", str(cm.exception))
            self.assertIn("--offline", str(cm.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class FakeYahooResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class YahooFallback(unittest.TestCase):
    """The automatic tier-3 fallback for an atr stop: no Dhan key, no --offline, so
    load_chart() reads Yahoo's free chart endpoint and warns about it every time."""

    def setUp(self):
        self._avail = exitp.dhan_available
        self._urlopen = exitp.urllib.request.urlopen
        saved = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        self._env = saved
        exitp.dhan_available = lambda: False
        with open(REL, encoding="utf-8") as fh:
            self._fixture_text = fh.read()

    def tearDown(self):
        exitp.dhan_available = self._avail
        exitp.urllib.request.urlopen = self._urlopen
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_falls_back_to_yahoo_and_warns(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        exitp.urllib.request.urlopen = fake_urlopen
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            ch = exitp.load_chart("RELIANCE", "5m", exitp.datetime.date(2026, 10, 15))
        self.assertIn("query2.finance.yahoo.com", calls[0])
        self.assertIn("RELIANCE.NS", calls[0])
        self.assertIn("WARNING", err.getvalue())
        self.assertIn("Rs 20.50", err.getvalue())
        self.assertEqual(len(ch["bars"]), 72)

    def test_retries_the_alternate_host_on_429(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "query2" in req.full_url:
                raise exitp.urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        exitp.urllib.request.urlopen = fake_urlopen
        with redirect_stderr(io.StringIO()):
            ch = exitp.load_chart("RELIANCE", "5m", exitp.datetime.date(2026, 10, 15))
        self.assertEqual(len(calls), 2)
        self.assertIn("query1", calls[1])
        self.assertEqual(len(ch["bars"]), 72)

    def test_a_real_http_error_does_not_retry_and_raises_cleanly(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise exitp.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        exitp.urllib.request.urlopen = fake_urlopen
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(exitp.PlanError) as cm:
                exitp.load_chart("RELIANCE", "5m", exitp.datetime.date(2026, 10, 15))
        self.assertEqual(len(calls), 1)
        self.assertIn("HTTP 404", str(cm.exception))

    def test_a_read_timeout_does_not_crash_and_raises_cleanly(self):
        # a read timeout (TimeoutError/socket.timeout) is an OSError, not a urllib.error.URLError
        def fake_urlopen(req, timeout=None):
            raise TimeoutError("timed out")
        exitp.urllib.request.urlopen = fake_urlopen
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(exitp.PlanError) as cm:
                exitp.load_chart("RELIANCE", "5m", exitp.datetime.date(2026, 10, 15))
        self.assertIn("timed out", str(cm.exception))

    def test_offline_wins_even_with_no_dhan_key(self):
        def fake_urlopen(req, timeout=None):
            raise AssertionError("offline must not touch the network")
        exitp.urllib.request.urlopen = fake_urlopen
        ch = exitp.load_chart("RELIANCE", "5m", exitp.datetime.date(2026, 10, 15), offline=REL)
        self.assertEqual(len(ch["bars"]), 72)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-15 09:56"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json")) as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, _ = run(entry, ["check", "RELIANCE", "--offline", REL, "--now", "2026-10-15 09:55", "--write"])
        self.assertEqual(code, 0)
        code, text, err = self.plan("--write")                    # defaults: or-mid, 1R, flat by 15:00
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in DAY_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Stop          Rs 1,181.20  opening-range midpoint", text)
        self.assertIn("Target        Rs 1,191.00  1R (one times the risk)", text)
        self.assertIn("Risk a share  Rs 4.90 (0.41% of the entry)", text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:00 IST", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("pro" + "fit", text.lower())
        self.assertNotIn("$", text)
        code, text, _ = self.plan("--target", "2R", "--write")
        self.assertEqual(self.read_plan()["target"], 1195.9)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-15 10:20"])
        self.assertEqual(code, 0)
        self.assertIn("Rs 1,195.90", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-16 10:20"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance", "--entry", "1186.10", "--or-high", "1185.00",
                                  "--or-low", "1177.40", "--time", "9:55", "--entry-bar", "7", "--date", "2026-10-15",
                                  "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in DAY_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), DAY_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", REL, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1180.4, 5.7, 1191.8))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 8))
        self.assertAlmostEqual(p["atr"], 3.8, places=4)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", REL, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_atr_stop_without_offline_or_dhan_falls_back_to_yahoo_with_a_warning(self):
        # no --offline and no Dhan credentials in this test environment: this no longer
        # refuses, it falls back to Yahoo's free chart automatically (a user with no
        # broker account should never hit a hard failure), and warns about it on stderr.
        self.write(os.path.join("trader", "entry.json"), DAY_ENTRY)
        saved = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        real_urlopen = exitp.urllib.request.urlopen
        with open(REL, encoding="utf-8") as fh:
            fixture_text = fh.read()

        def fake_urlopen(req, timeout=None):
            return FakeYahooResponse(fixture_text.encode("utf-8"))
        exitp.urllib.request.urlopen = fake_urlopen
        try:
            code, out, err = self.plan("--stop", "atr:1.5")
            self.assertEqual(code, 0)
            self.assertIn("WARNING", err)
            self.assertIn("Rs 20.50", err)
        finally:
            exitp.urllib.request.urlopen = real_urlopen
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(DAY_ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "1185.10", "--json")    # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1185.1, 1181.2, None))

        self.write(os.path.join("trader", "entry.json"), dict(DAY_ENTRY, setup="level", level=1185.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(DAY_ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1186.10", "--or-high", "1185")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_mis_cutoff_cas_and_late_entries(self):
        self.write(os.path.join("trader", "entry.json"), DAY_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "14:45", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "14:45")
        self.assertIn("under your min_reward_risk 1.5", p["warnings"][0])
        # at or after Dhan's 15:10 MIS auto-square-off: allowed, with a note
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:10"})
        code, text, _ = self.plan("--json")
        self.assertEqual(code, 0)
        self.assertIn("auto-square-off (15:10 IST)", json.loads(text)["warnings"][0])
        os.remove(os.path.join("trader", "config.json"))
        # with the Closing Auction Session continuous trading ends 15:15
        code, _, err = self.plan("--flat-by", "15:15")
        self.assertEqual(code, 1)
        self.assertIn("at or after the end of continuous trading (15:15 IST)", err)
        # before 2026-08-03 it ended 15:30
        self.write(os.path.join("trader", "entry.json"), dict(DAY_ENTRY, date="2026-07-30"))
        code, text, err = run(exitp, ["plan", "--now", "2026-07-30 09:56", "--flat-by", "15:20", "--json"])
        self.assertEqual(code, 0, err)
        self.assertIn("auto-square-off", json.loads(text)["warnings"][0])
        code, _, err = run(exitp, ["plan", "--now", "2026-07-30 09:56", "--flat-by", "15:30"])
        self.assertIn("(15:30 IST)", err)
        self.write(os.path.join("trader", "entry.json"), dict(DAY_ENTRY, time="3:05 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money",
                     "/trader-timemachine", "/trader-budget", "longs only", "rs 1,181.20", "rs 1,191.00", "rs 4.90",
                     "1,195.90", "too tight to survive the spread", "tick", "15:10"):
            self.assertIn(must, low)
        for never in (EM_DASH, "tick " + "by tick", "real " + "time", "pro" + "fit", "new york", "nyse"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")


if __name__ == "__main__":
    unittest.main()
