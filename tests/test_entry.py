"""trader-entry tests, NSE India version. Fixtures in tests/fixtures/nse/:

RELIANCE-2026-10-15-5m.json  RELIANCE's 2026-10-15 five-minute bars, cut from a real Yahoo
                             range=60d answer saved 2026-11-01 (tools/build_nse_fixtures.py).
                             75 Yahoo stamps 09:15 to 15:25 IST. Real quirks kept: the 09:15
                             bar has volume 0, 11:45 and 11:50 are null (a gap on every NSE
                             symbol that day), and 15:15/15:20/15:25 are Closing Auction
                             Session artifacts (outside continuous trading, so not bars here).
DEMO-1m-synthetic.json       SYNTHETIC one-minute bars (not market data): the original demo
                             re-stamped to start at 09:15 IST. A wick above the range high, a
                             close AT the high, a null bar, a live tick.

Expected values were worked out by hand from the fixture's rows (independent of entry.py):
opening range 09:15-09:25: high 1185.00, low 1177.40; average volume of the range bars
that report volume (09:20, 09:25) = 248,183.5; first close above 1185.00 whose bar closes at
or after 09:30 is the 09:50 bar (bar 7) at 1186.10, closing 9:55 AM.
"""

import copy
import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")

NDIR = os.path.join(FIX, "nse")
REL = os.path.join(NDIR, "RELIANCE-2026-10-15-5m.json")
DEMO = os.path.join(NDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1792035900       # 2026-10-15 09:15 IST = 03:45 UTC
DT = datetime.datetime


def ist(text):
    return entry.parse_now(text)


def doc(path=REL):
    with open(path) as fh:
        return json.load(fh)


def chart(d=None, sym="RELIANCE"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def shift_to(d, day):
    """SYNTHETIC: the same session moved to another date (09:15 IST is always 03:45 UTC)."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    new_open = int((DT.combine(day, datetime.time(3, 45)) - DT(1970, 1, 1)).total_seconds())
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def no_breakout(d=None):
    """SYNTHETIC: every bar after the range kept under the 1185.00 high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        if q["close"][i] is None:
            continue
        q["close"][i] = min(q["close"][i], 1184.80)
        q["high"][i] = min(q["high"][i], 1184.90)
        q["open"][i] = min(q["open"][i], 1184.80)
        q["low"][i] = min(q["low"][i], q["close"][i], q["open"][i])
    return d


def live_cut(d, last_slot, tick_ts):
    """SYNTHETIC: the file as Yahoo would serve it live: bars up to `last_slot`, plus a
    live tick at `tick_ts` (not on the 5-minute grid), regularMarketTime = the tick."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    keep = [i for i, t in enumerate(r["timestamp"]) if t <= OPEN_TS + last_slot * 300]
    r["timestamp"] = [r["timestamp"][i] for i in keep] + [tick_ts]
    q = r["indicators"]["quote"][0]
    for k in q:
        q[k] = [q[k][i] for i in keep] + [q[k][keep[-1]]]
    r["meta"]["regularMarketTime"] = tick_ts
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheDay(unittest.TestCase):
    def test_triggers_at_955_on_the_950_bar(self):
        r = at("2026-10-15 09:55")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["symbol"], "RELIANCE")
        self.assertEqual(r["opening_range"]["high"], 1185.0)
        self.assertEqual(r["opening_range"]["low"], 1177.4)
        self.assertEqual(r["opening_range"]["bars"], 3)
        t = r["trigger"]
        self.assertEqual(t["time"], "9:55 AM")
        self.assertEqual(t["bar_start"], "09:50")
        self.assertEqual(t["bar_index"], 7)
        self.assertEqual(t["price"], 1186.1)
        self.assertEqual(t["pct_over"], 0.09)
        self.assertEqual(r["level"], 1185.0)
        self.assertEqual(r["prev_close"], 1174.5999755859375)

    def test_prices_are_exact_rupees_on_the_tick_grid(self):
        # Yahoo sends 1186.0999755859375; the bar keeps the exchange price 1186.10
        raw = doc()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        self.assertIn(1186.0999755859375, raw)
        c = chart()
        for b in c["bars"]:
            for k in "ohlc":
                self.assertLess(abs(b[k] * 100 - round(b[k] * 100)), 1e-6, b)   # whole paise
        self.assertIn(1186.1, [b["c"] for b in c["bars"]])

    def test_the_first_bar_alone_has_no_volume_average(self):
        r = at("2026-10-15 09:21")                       # only the 09:15 bar (volume 0) has closed
        self.assertEqual(r["opening_range"]["bars"], 1)
        self.assertIsNone(r["opening_range"]["avg_volume"])

    def test_at_954_the_950_bar_is_still_forming(self):
        r = at("2026-10-15 09:54")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:45")
        self.assertEqual(r["last"]["close"], 1181.0)
        self.assertEqual(r["forming"]["bar_start"], "09:50")
        self.assertIn("Rs 4.00 under the range high Rs 1185.00", r["reason"])
        self.assertEqual(at("2026-10-15 09:54:59")["status"], "waiting")

    def test_before_the_open_and_while_the_range_forms(self):
        r = at("2026-10-15 09:10")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("opens at 9:15", r["reason"])
        r = at("2026-10-15 09:29")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at("2026-10-15 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        tick = OPEN_TS + 14 * 300 + 285                        # 10:29:45, inside the 10:25 bar
        d = live_cut(doc(), 14, tick)
        c = chart(d)
        self.assertEqual(c["bars"][-1]["t"], tick)
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))
        self.assertEqual(r["now"], "2026-10-15 10:29:45")
        self.assertEqual(r["_bars"][-1]["slot"], 13)            # the 10:20 bar
        self.assertEqual(r["forming"]["bar_start"], "10:25")
        self.assertEqual(r["bars_closed"], 14)
        r = entry.evaluate(c, ist("2026-10-15 10:30"))          # closed by the clock, source behind
        self.assertEqual(r["_bars"][-1]["slot"], 13)
        self.assertIn("the data source's last trade is 10:29:45", r["lag_note"])
        r = entry.evaluate(c, ist("2026-10-15 10:31"))
        self.assertEqual(r["_bars"][-1]["slot"], 14)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_the_real_1145_gap_and_the_auction_stamps(self):
        r = at("2026-10-15 16:00")
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(30, slots)                             # 11:45 came as nulls
        self.assertNotIn(31, slots)                             # 11:50 came as nulls
        self.assertEqual(max(slots), 71)                        # the 15:10 bar: continuous trading ends 15:15
        self.assertEqual(r["bars_closed"], 70)                  # 72 continuous bars, 2 missing
        self.assertEqual(r["closes"], "15:15")

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at("2026-10-15 15:40")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "9:55 AM")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)
        self.assertIn("IST", text)


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        r = at("2026-10-15 10:30", min_volume_x=1)
        self.assertEqual(r["opening_range"]["avg_volume"], 248184)   # the 09:15 bar's 0 is left out
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"]), ("10:10 AM", 10, 1194.7))
        self.assertEqual(t["volume_x"], 1.86)
        r = at("2026-10-15 09:56", min_volume_x=1)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("0.98x the range's volume, under your 1.00x", r["reason"])

    def test_level_setup(self):
        r = at("2026-10-15 10:30", setup="level", level=1190)
        self.assertEqual(r["trigger"]["time"], "10:10 AM")
        self.assertEqual(r["trigger"]["bar_index"], 10)
        self.assertEqual(r["trigger"]["price"], 1194.7)
        # a level under the open: the first eligible close is the 9:25 bar's, at 9:30 (no_entries_before)
        r = at("2026-10-15 10:30", setup="level", level=1170)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:30 AM", 2))
        with self.assertRaises(entry.EntryError):
            at("2026-10-15 10:30", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        self.assertEqual(at("2026-10-15 10:49", d)["status"], "waiting")
        self.assertEqual(at("2026-10-15 14:29", d)["status"], "waiting")
        r = at("2026-10-15 14:30", d)
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 14:30", r["reason"])
        self.assertEqual(at("2026-10-15 15:20", d)["phase"], "after-close")

    def test_config_cutoff(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "09:50"}
        self.assertEqual(at("2026-10-15 09:55", cfg=cfg)["status"], "expired")
        cfg = {"no_entries_before": "10:05", "no_entries_after": "14:30"}
        r = at("2026-10-15 10:30", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "10:05 AM")      # the 10:00 bar, first to close at or after 10:05

    def test_cutoff_never_later_than_continuous_trading(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "15:25"}
        r = at("2026-10-15 15:20", no_breakout(), cfg=cfg)
        self.assertEqual(r["no_entries_after"], "15:15")        # CAS: continuous trading ends 15:15

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at("2026-10-15 10:15", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at("2026-10-15 10:15", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        self.assertEqual(entry.parse_now("2026-10-15 09:55"), DT(2026, 10, 15, 4, 25))
        self.assertEqual(entry.parse_now("2026-10-15T04:25:00Z"), DT(2026, 10, 15, 4, 25))
        self.assertEqual(entry.parse_now("2026-10-15 09:55 IST"), DT(2026, 10, 15, 4, 25))
        self.assertEqual(entry.parse_now("2026-10-15T09:55:00+05:30"), DT(2026, 10, 15, 4, 25))

    def test_or_minutes_30(self):
        r = at("2026-10-15 10:30", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 1185.0)
        self.assertEqual(r["trigger"]["time"], "9:55 AM")


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], "2026-10-19 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10667})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.62))
        r = entry.evaluate(c, ist("2026-10-19 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 9:42 bar closed AT the high

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 9:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 9:51 still forming, 9:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


class Calendar(unittest.TestCase):
    def test_ist_is_fixed_all_year(self):
        # no daylight time: 10:15 IST is 04:45 UTC in January, July and on the US change days
        for day in (datetime.date(2026, 1, 5), datetime.date(2026, 3, 9), datetime.date(2026, 7, 6),
                    datetime.date(2026, 11, 2), datetime.date(2026, 12, 7)):
            self.assertEqual(ist("%s 10:15" % day), DT.combine(day, datetime.time(4, 45)), day)
            local = DT.combine(day, datetime.time(13, 7, 30))
            self.assertEqual(entry.to_ist(entry.ist_to_utc(local)), local)
        d = shift_to(doc(), datetime.date(2026, 11, 2))
        r = entry.evaluate(chart(d), ist("2026-11-02 09:55"))
        self.assertEqual((r["status"], r["trigger"]["time"], r["trigger"]["bar_index"]), ("triggered", "9:55 AM", 7))

    def test_closing_auction_session_shortens_continuous_trading(self):
        self.assertEqual(entry.session(datetime.date(2026, 8, 3)), (datetime.time(9, 15), datetime.time(15, 15)))
        self.assertEqual(entry.session(datetime.date(2026, 7, 31)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(entry.continuous_bars(datetime.date(2026, 10, 15)), 72)
        self.assertEqual(entry.continuous_bars(datetime.date(2026, 7, 31)), 75)
        self.assertEqual(entry.continuous_bars(datetime.date(2026, 10, 15), cas=False), 75)
        d = shift_to(no_breakout(), datetime.date(2026, 7, 30))     # SYNTHETIC: a pre-CAS date
        r = entry.evaluate(chart(d), ist("2026-07-30 15:40"))
        self.assertEqual(r["closes"], "15:30")

    def test_weekend_holiday_special_session_and_empty_data(self):
        r = at("2026-10-17 10:30")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertEqual(r["next_open"], "2026-10-19 09:15")
        r = at("2026-10-20 10:30")
        self.assertIn("Dussehra", r["reason"])
        self.assertEqual(r["next_open"], "2026-10-21 09:15")
        r = at("2026-11-08 18:30")
        self.assertIn("Muhurat", r["reason"])
        self.assertEqual(r["status"], "no-data")
        self.assertIn("Diwali", at("2026-11-10 10:00")["reason"])
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at("2026-10-15 10:15", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        self.assertEqual(at("2026-10-16 10:15")["status"], "no-data")   # Yahoo sent the day before only
        self.assertFalse(at("2027-03-01 10:30")["calendar_known"])

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "RELIANCE")


class Network(unittest.TestCase):
    """The live path (Dhan Data API): dhan_find_security and _dhan_post are swapped for
    fakes, so no test ever touches the real network or needs real credentials."""

    def setUp(self):
        self._find, self._post, self._avail = entry.dhan_find_security, entry._dhan_post, entry.dhan_available
        entry.dhan_find_security = lambda sym, path=None: ("2885", "NSE_EQ", "EQUITY")
        entry.dhan_available = lambda: True   # a Dhan key is set; these tests exercise that path

    def tearDown(self):
        entry.dhan_find_security, entry._dhan_post, entry.dhan_available = self._find, self._post, self._avail

    def dhan_payload(self):
        """The same RELIANCE bars as the REL fixture, reshaped into Dhan's own
        parallel-array response shape (timestamp/open/high/low/close/volume)."""
        r = doc()["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        return {"timestamp": r["timestamp"], "open": q["open"], "high": q["high"],
                "low": q["low"], "close": q["close"], "volume": q["volume"]}

    def test_fetch_builds_the_same_bars_as_the_fixture(self):
        payload = self.dhan_payload()
        calls = []

        def fake(url, body, timeout=30):
            calls.append((url, body))
            return payload
        entry._dhan_post = fake
        c = entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 15), today=datetime.date(2026, 10, 15))
        self.assertEqual(len(c["bars"]), 72)                      # 75 stamps; 11:45, 11:50, 15:20 null
        self.assertEqual(calls[0][0], entry.DHAN_INTRADAY_URL)
        self.assertEqual(calls[0][1]["securityId"], "2885")
        self.assertEqual(calls[0][1]["exchangeSegment"], "NSE_EQ")
        self.assertEqual(calls[0][1]["instrument"], "EQUITY")
        self.assertEqual(calls[0][1]["interval"], 5)
        self.assertEqual(c["meta"]["source"], "dhan")

    def test_http_error_from_dhan_becomes_an_entry_error(self):
        def fake(url, body, timeout=30):
            raise entry.DhanError("Dhan answered HTTP 429: rate limited")
        entry._dhan_post = fake
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 15), today=datetime.date(2026, 10, 15))
        self.assertIn("HTTP 429", str(cm.exception))

    def test_still_busy_is_one_plain_error(self):
        def fake(url, body, timeout=30):
            raise entry.DhanError("Dhan answered HTTP 429: rate limited")
        entry._dhan_post = fake
        code, out, err = run(["check", "RELIANCE", "--now", "2026-10-15 09:55"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_symbol_is_a_clear_error(self):
        def not_found(sym, path=None):
            raise entry.DhanError("%s is not in Dhan's NSE security master (checked x): check the symbol" % sym)
        entry.dhan_find_security = not_found
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ", "5m", datetime.date(2026, 10, 15), today=datetime.date(2026, 10, 15))
        self.assertIn("security master", str(cm.exception))

    def test_missing_credentials_is_a_clear_message(self):
        saved = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        try:
            with self.assertRaises(entry.DhanError) as cm:
                entry.dhan_credentials()
            self.assertIn("no Dhan credentials", str(cm.exception))
            self.assertIn("--offline", str(cm.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_index_symbol_maps_to_dhans_own_trading_symbol(self):
        seen = []
        entry.dhan_find_security = lambda sym, path=None: (seen.append(sym) or ("13", "IDX_I", "INDEX"))
        entry._dhan_post = lambda url, body, timeout=30: self.dhan_payload()
        entry.get_chart("^NSEI", "5m", datetime.date(2026, 10, 15), today=datetime.date(2026, 10, 15))
        self.assertEqual(seen, ["^NSEI"])   # the symbol lookup gets the pack's own ^NSEI spelling


class YahooFallback(unittest.TestCase):
    """Tier 3: no Dhan key, no --offline, so get_chart() falls back to Yahoo automatically
    and prints the warning every time. urlopen is swapped for a fake; no real network."""

    def setUp(self):
        self._avail, self._open = entry.dhan_available, entry.urllib.request.urlopen
        entry.dhan_available = lambda: False   # no Dhan key set: force the fallback

    def tearDown(self):
        entry.dhan_available, entry.urllib.request.urlopen = self._avail, self._open

    def test_falls_back_to_yahoo_and_warns(self):
        with open(REL, "rb") as fh:
            body = fh.read()
        calls = []

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeResp(body)
        entry.urllib.request.urlopen = fake_urlopen
        err = io.StringIO()
        with redirect_stderr(err):
            c = entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 15), today=datetime.date(2026, 10, 15))
        self.assertEqual(len(c["bars"]), 72)
        self.assertEqual(c["meta"]["source"], "yahoo")
        self.assertIn("query2.finance.yahoo.com", calls[0])
        self.assertIn("RELIANCE.NS", calls[0])
        self.assertIn("WARNING", err.getvalue())
        self.assertIn("Dhan", err.getvalue())

    def test_retries_the_alternate_host_on_429(self):
        with open(REL, "rb") as fh:
            body = fh.read()
        calls = []

        class FakeResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "query2" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, None)
            return FakeResp(body)
        entry.urllib.request.urlopen = fake_urlopen
        with redirect_stderr(io.StringIO()):
            c = entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 15), today=datetime.date(2026, 10, 15))
        self.assertEqual(len(calls), 2)
        self.assertIn("query1", calls[1])
        self.assertEqual(c["meta"]["source"], "yahoo")

    def test_a_real_http_error_does_not_retry_and_raises_cleanly(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)
        entry.urllib.request.urlopen = fake_urlopen
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(entry.EntryError) as cm:
                entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 15), today=datetime.date(2026, 10, 15))
        self.assertIn("HTTP 404", str(cm.exception))

    def test_a_read_timeout_does_not_crash_and_raises_cleanly(self):
        # a read timeout (TimeoutError/socket.timeout) is an OSError, not a urllib.error.URLError
        def fake_urlopen(req, timeout=None):
            raise TimeoutError("timed out")
        entry.urllib.request.urlopen = fake_urlopen
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(entry.EntryError) as cm:
                entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 15), today=datetime.date(2026, 10, 15))
        self.assertIn("timed out", str(cm.exception))

    def test_offline_wins_even_with_no_dhan_key(self):
        # --offline is explicit; it must never fall through to Yahoo even when dhan_available() is False.
        d = tempfile.mkdtemp()
        try:
            shutil.copy(REL, os.path.join(d, "RELIANCE.json"))

            def fail_urlopen(req, timeout=None):
                raise AssertionError("must not touch the network when --offline is given")
            entry.urllib.request.urlopen = fail_urlopen
            c = entry.get_chart("RELIANCE", "5m", offline=d)
            self.assertEqual(c["meta"]["source"], "offline")
        finally:
            shutil.rmtree(d)


def live_provider(d, lag_until=None, calls=None):
    """SYNTHETIC timing over real bars: closed bars final, the in-progress bar showing a
    spike to 1185.50 (above the high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 1185.50, max(b["h"], 1185.50)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 1185.5, "h": 1185.5, "l": 1185.5, "c": 1185.5, "v": 0})
        return {"symbol": "RELIANCE", "meta": {}, "bars": bars, "prev_close": 1174.6, "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), "2026-10-15 09:47", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "9:55 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 1186.1)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:47:00", "09:50:02", "09:55:02"])
        text = "\n".join(lines)
        self.assertIn(" 9:45 bar  closed Rs 1181.00  Rs 4.00 under Rs 1185.00", text)
        self.assertIn(" 9:50 bar  closed Rs 1186.10  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist("2026-10-15 09:55:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls),
                                                   "2026-10-15 09:52", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["09:52:00", "09:55:02", "09:55:32"])
        self.assertEqual(fired[0][0]["trigger"]["time"], "9:55 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), "2026-10-15 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn(" 9:25 bar  closed Rs 1179.90  opening range set: high Rs 1185.00, low Rs 1177.40", text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 8)    # 9:15 ... 9:50, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist("2026-10-15 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[:2], ["09:31:00", "09:49:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "9:55 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-15 14:00", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "14:30")
        until = DT(2026, 10, 15, 11, 0)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-15 10:30", every=300,
                                                   until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-17 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-20 10:00")
        self.assertIn("Dussehra", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-10-15 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-10-15 09:55", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 9:55 AM at Rs 1186.10", text)
        self.assertIn("/trader-exit", text)
        self.assertIn("Nothing was placed", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy",
                "date": "2026-10-15", "status": "triggered", "time": "9:55 AM", "bar_start": "09:50",
                "bar_index": 7, "price": 1186.1, "level": 1185.0, "interval": "5m",
                "opening_range": {"high": 1185.0, "low": 1177.4, "minutes": 15},
                "source": "offline", "checked_at": "2026-10-15T04:25:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-10-15 09:54", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-10-15 09:54", "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(REL, "rb") as src, gzip.open(os.path.join("charts", "RELIANCE.NS.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "reliance", "--offline", "charts", "--now", "2026-10-15 09:55"])
        self.assertEqual(code, 0, text)
        code, _, err = run(["check", "HDFCBANK", "--offline", REL])
        self.assertEqual(code, 1)
        self.assertIn("holds RELIANCE, not HDFCBANK", err)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--level", "1190"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "09:50"}, fh)
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-10-15 09:55"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "RELIANCE", "--offline", REL])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "RELIANCE", "--offline", REL, "--now", "2026-10-15 09:31", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn(" 9:50 bar  closed Rs 1186.10  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 7)
        code, text, _ = run(["watch", "RELIANCE", "--offline", REL, "--now", "2026-10-15 09:31", "--until", "09:45",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(REL, os.path.join("charts", "RELIANCE.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "RELIANCE", "setups": ["orb"], "or_high": 1185.0, "or_low": 1177.4},
                {"symbol": "ZZZZ", "setups": ["orb"]}, {"symbol": "DEMO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-15 09:55"])
        self.assertEqual(code, 0, text)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("RELIANCE    triggered  9:55 AM   Rs 1186.10  Rs 1185.00"), rows[1])
        self.assertIn("ZZZZ        error: no offline file", rows[2])
        self.assertIn("DEMO        error: DEMO has 1m bars", rows[3])
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-15 09:54", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "day trading loses most people money", "/trader-exit", "longs only",
                     "nothing was placed", "9:55 am", "rs 1186.10", "bar 7", "ist", "nse"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "new york", "nyse"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")                         # no dollar amounts


if __name__ == "__main__":
    unittest.main()
