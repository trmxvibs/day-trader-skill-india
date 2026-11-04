"""trader-scanner tests, NSE India version. Fixtures in tests/fixtures/nse/ (real data,
saved 2026-11-01, see tools/build_nse_fixtures.py):

ind_nifty100list.csv          NSE's Nifty 100 constituent file (100 rows, EQ series)
daily-spark-2026-10-30.json   Yahoo spark, 3 months of daily closes: RELIANCE, HDFCBANK, M&M,
                              BAJAJ-AUTO, TCS, IDEA (IDEA trades near Rs 12)
wall5m-2026-10-29.json.gz     one-day v8 charts for RELIANCE, HDFCBANK, M&M, BAJAJ-AUTO
RELIANCE-2026-10-15-5m.json   a day with a real 11:45/11:50 gap

ORB expectations for 2026-10-29 were worked out by hand from the bars: range 09:15-09:25;
HDFCBANK's 09:30 bar closed 682.50 over its 682.45 high (9:35 AM), RELIANCE's 09:35 bar closed
1174.30 over 1170.50 (9:40 AM), M&M first closed above 2887.50 on the 12:55 bar (1:00 PM),
BAJAJ-AUTO never did.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

sc = load("trader-scanner", "scan")

NDIR = os.path.join(FIX, "nse")
NIFTY = os.path.join(NDIR, "ind_nifty100list.csv")
DAILY = os.path.join(NDIR, "daily-spark-2026-10-30.json")
WALL = os.path.join(NDIR, "wall5m-2026-10-29.json.gz")
REL15 = os.path.join(NDIR, "RELIANCE-2026-10-15-5m.json")
EM_DASH = chr(0x2014)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = sc.main(argv)
    return code, out.getvalue(), err.getvalue()


class InTemp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)


class Universe(InTemp):
    def test_nifty_100_from_the_nse_file(self):
        code, text, err = run(["universe", "--offline", NIFTY])
        self.assertEqual(code, 0, err)
        self.assertIn("Kept 100 Nifty 100 constituents (EQ series)", text)
        self.assertIn("survivorship bias", text)
        with open(os.path.join("trader", "universe.txt"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 100)
        syms = [ln.split("|")[0] for ln in lines]
        for s in ("RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO", "TCS", "IDEA"):
            self.assertIn(s, syms)
        self.assertEqual(syms, sorted(syms))
        self.assertIn("M&M|Mahindra & Mahindra Ltd.", lines)

    def test_series_and_symbol_rules(self):
        rows = [{"Company Name": "A Ltd.", "Symbol": "AAA", "Series": "EQ"},
                {"Company Name": "B Ltd.", "Symbol": "BBB", "Series": "BE"},
                {"Company Name": "C Ltd.", "Symbol": "C C", "Series": "EQ"}]
        kept, counts = sc.build_universe(rows)
        self.assertEqual(kept, [("AAA", "A Ltd.")])
        self.assertEqual((counts["not EQ series"], counts["not a plain NSE symbol"]), (1, 1))

    def test_not_an_index_file(self):
        with open("bad.csv", "w") as fh:
            fh.write("Symbol|Security Name\nAAPL|Apple\n")
        code, _, err = run(["universe", "--offline", "bad.csv"])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE index constituent file", err)
        code, _, err = run(["universe", "--offline", ".", "--index", "nifty50"])
        self.assertIn("no ind_nifty50list.csv", err)

    def test_download_falls_back_to_niftyindices(self):
        calls = []
        with open(NIFTY, encoding="utf-8") as fh:
            body = fh.read()

        def fake(url, timeout=20):
            calls.append(url)
            if "nsearchives" in url:
                raise urllib.error.HTTPError(url, 403, "x", {}, io.BytesIO(b""))
            return body
        old, sc.http_get = sc.http_get, fake
        old_sleep, sc.SLEEP = sc.SLEEP, lambda s: None
        try:
            code, text, err = run(["universe", "--json", "--no-write"])
        finally:
            sc.http_get, sc.SLEEP = old, old_sleep
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, ["https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
                                 "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"])
        self.assertEqual(json.loads(text)["count"], 100)


class Network(unittest.TestCase):
    """The live path (Dhan Data API, one call a symbol): dhan_find_security and
    _dhan_post are swapped for fakes, so no test ever touches the real network or
    needs real credentials. --offline (Universe/Daily/Orb below) is unaffected."""

    def setUp(self):
        self._find, self._post = sc.dhan_find_security, sc._dhan_post
        sc.dhan_find_security = lambda sym, path=None: ("2885", "NSE_EQ", "EQUITY")

    def tearDown(self):
        sc.dhan_find_security, sc._dhan_post = self._find, self._post

    def test_intraday_series_matches_the_fixtures_shape(self):
        with open(REL15) as fh:
            r = json.load(fh)["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        payload = {"timestamp": r["timestamp"], "open": q["open"], "high": q["high"],
                   "low": q["low"], "close": q["close"], "volume": q["volume"]}
        sc._dhan_post = lambda url, body, timeout=30: payload
        s = sc.dhan_intraday_series("RELIANCE", "5m", sc.datetime.date(2026, 10, 15))
        self.assertEqual(len(s["t"]), 72)                 # 75 stamps; 11:45, 11:50, 15:20 null
        self.assertIsNotNone(s["h"][0])                    # real highs/lows, unlike the old spark stage

    def test_daily_series_groups_by_ist_calendar_date(self):
        sc._dhan_post = lambda url, body, timeout=30: {
            "timestamp": [1761782400, 1761868800], "open": [100.0, 101.0], "high": [102.0, 103.0],
            "low": [99.0, 100.0], "close": [101.5, 102.5], "volume": [1000, 2000]}
        s = sc.dhan_daily_series("RELIANCE", sc.datetime.date(2026, 10, 30), sc.datetime.date(2026, 10, 31))
        self.assertEqual(len(s["t"]), 2)
        self.assertEqual(s["meta"], {"regularMarketPrice": 102.5, "regularMarketVolume": 2000})
        dates = [sc.ts_to_ist(t).date().isoformat() for t in s["t"]]
        self.assertEqual(len(set(dates)), 2)               # two distinct IST calendar days, no collision

    def test_http_error_from_dhan_is_one_failed_symbol_not_a_crash(self):
        def fake(sym):
            raise sc.DhanError("Dhan answered HTTP 429: rate limited")
        data, failed = sc.fetch_dhan(["RELIANCE", "M&M"], fake, 0)
        self.assertEqual(data, {})
        self.assertEqual(set(failed), {"RELIANCE", "M&M"})
        self.assertIn("HTTP 429", failed["RELIANCE"])

    def test_missing_credentials_is_a_clear_message(self):
        saved = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        try:
            with self.assertRaises(sc.DhanError) as cm:
                sc.dhan_credentials()
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
    """The automatic tier-3 fallback: no Dhan key, no --offline, so scan.py reads Yahoo's
    free chart endpoint directly and warns about it every time."""

    def setUp(self):
        self._avail = sc.dhan_available
        self._urlopen = sc.urllib.request.urlopen
        self._env = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        sc.dhan_available = lambda: False
        with open(REL15, encoding="utf-8") as fh:
            self._fixture_text = fh.read()

    def tearDown(self):
        sc.dhan_available = self._avail
        sc.urllib.request.urlopen = self._urlopen
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_fetch_yahoo_series_parses_like_the_fixture(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        sc.urllib.request.urlopen = fake_urlopen
        s = sc.fetch_yahoo_series("RELIANCE", "5d", "5m")
        self.assertIn("query2.finance.yahoo.com", calls[0])
        self.assertIn("RELIANCE.NS", calls[0])
        self.assertIsNotNone(s["h"][0])

    def test_retries_the_alternate_host_on_429(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "query2" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        sc.urllib.request.urlopen = fake_urlopen
        s = sc.fetch_yahoo_series("RELIANCE", "5d", "5m")
        self.assertEqual(len(calls), 2)
        self.assertIn("query1", calls[1])
        self.assertIsNotNone(s["h"][0])

    def test_a_real_http_error_does_not_retry_and_raises_cleanly(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        sc.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(sc.ScanError) as cm:
            sc.fetch_yahoo_series("RELIANCE", "5d", "5m")
        self.assertEqual(len(calls), 1)
        self.assertIn("HTTP 404", str(cm.exception))

    def test_a_read_timeout_does_not_crash_and_raises_cleanly(self):
        # a read timeout (TimeoutError/socket.timeout) is an OSError, not a urllib.error.URLError
        def fake_urlopen(req, timeout=None):
            raise TimeoutError("timed out")
        sc.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(sc.ScanError) as cm:
            sc.fetch_yahoo_series("RELIANCE", "5d", "5m")
        self.assertIn("timed out", str(cm.exception))

    def test_orb_falls_back_to_yahoo_and_warns(self):
        def fake_urlopen(req, timeout=None):
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        sc.urllib.request.urlopen = fake_urlopen
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        try:
            os.mkdir("trader")
            code, text, err = run(["orb", "--symbols", "RELIANCE", "--now", "2026-10-15 10:30", "--json"])
            self.assertIn("WARNING", err)
            self.assertIn("Rs 20.50", err)
            self.assertEqual(json.loads(text)["source"], "yahoo")
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)

    def test_daily_falls_back_to_yahoo_and_warns(self):
        def fake_urlopen(req, timeout=None):
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        sc.urllib.request.urlopen = fake_urlopen
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        try:
            os.mkdir("trader")
            code, text, err = run(["daily", "--symbols", "RELIANCE", "--now", "2026-11-02 08:30", "--json",
                                   "--min-price", "0", "--min-value", "0"])
            self.assertIn("WARNING", err)
            self.assertIn("Rs 20.50", err)
            self.assertEqual(json.loads(text)["source"], "yahoo")
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)

    def test_offline_wins_even_with_no_dhan_key(self):
        def fake_urlopen(req, timeout=None):
            raise AssertionError("offline must not touch the network")
        sc.urllib.request.urlopen = fake_urlopen
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        try:
            os.mkdir("trader")
            code, text, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:30", "--json"])
            self.assertEqual(json.loads(text)["source"], "offline")
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)


class Symbols(unittest.TestCase):
    def test_yahoo_symbols_and_paths(self):
        self.assertEqual(sc.to_yahoo("reliance"), "RELIANCE.NS")
        self.assertEqual(sc.to_yahoo("M&M"), "M&M.NS")
        self.assertEqual(sc.to_yahoo("^NSEI"), "^NSEI")
        self.assertEqual(sc.from_yahoo("BAJAJ-AUTO.NS"), "BAJAJ-AUTO")
        self.assertEqual(sc.spark_path(["RELIANCE", "M&M", "^NSEI"], "1d", "5m"),
                         "/v7/finance/spark?symbols=RELIANCE.NS,M%26M.NS,%5ENSEI&range=1d&interval=5m")
        self.assertEqual(sc.chart_path("BAJAJ-AUTO", "5d", "5m"), "/v8/finance/chart/BAJAJ-AUTO.NS?range=5d&interval=5m")

    def test_spark_maps_back_to_nse_symbols(self):
        with open(DAILY, encoding="utf-8") as fh:
            got = sc.parse_spark(fh.read())
        self.assertEqual(sorted(got), ["BAJAJ-AUTO", "HDFCBANK", "IDEA", "M&M", "RELIANCE", "TCS"])


def ref_breakout_20d(closes):
    return closes[-1] > max(closes[-21:-1])


class Daily(InTemp):
    NOW = "2026-11-02 08:30"      # Monday, before the 09:15 open: the last daily close is Friday 2026-10-30

    def test_liquidity_floor_and_setups(self):
        code, text, err = run(["daily", "--offline", DAILY, "--now", self.NOW, "--json"])
        doc = json.loads(text)
        c = doc["counts"]
        self.assertEqual((c["requested"], c["fetched"], c["illiquid"]), (6, 6, 1))   # IDEA: under Rs 20
        self.assertEqual(c["scanned"], 5)
        self.assertEqual(doc["as_of"], "2026-10-30 close")
        self.assertEqual(doc["filters"], {"min_price": 20.0, "min_volume": 0, "min_value": 25e7})
        self.assertNotIn("IDEA", [h["symbol"] for h in doc["hits"]])
        # the breakout tag agrees with an independent reading of the closes
        with open(DAILY, encoding="utf-8") as fh:
            data = sc.parse_spark(fh.read())
        for sym in ("RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO", "TCS"):
            closes = [cl for _, cl in sc.daily_closes(data[sym], sc.parse_now(self.NOW))]
            hit = next((h for h in doc["hits"] if h["symbol"] == sym), None)
            tags = hit["setups"] if hit else []
            self.assertEqual("breakout_20d" in tags, ref_breakout_20d(closes), sym)
        self.assertEqual(code, 0 if doc["hits"] else 1)

    def test_traded_value_floor(self):
        # price x Yahoo's regularMarketVolume: M&M about Rs 528 crore, BAJAJ-AUTO about Rs 394 crore
        code, text, err = run(["daily", "--offline", DAILY, "--now", self.NOW, "--json", "--min-value", "6e9"])
        c = json.loads(text)["counts"]
        self.assertEqual((c["illiquid"], c["scanned"]), (3, 3))       # IDEA, M&M, BAJAJ-AUTO
        code, text, err = run(["daily", "--offline", DAILY, "--now", self.NOW, "--min-price", "0", "--min-value",
                               "0", "--json"])
        self.assertEqual(json.loads(text)["counts"]["scanned"], 6)

    def test_text_output_is_in_rupees_and_ist(self):
        code, text, err = run(["daily", "--offline", DAILY, "--now", self.NOW])
        self.assertIn("IST", text)
        self.assertIn("Rs 20+ price, Rs 25 crore+ traded value", text)
        self.assertNotIn("$", text)
        self.assertNotIn(" ET", text)
        self.assertTrue(os.path.exists(os.path.join("trader", "watchlist.json")))


class Orb(InTemp):
    def test_the_replay_at_1030(self):
        code, text, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:30", "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual(doc["as_of"], "2026-10-29 10:30 IST")
        got = [(h["symbol"], h["at"], h["price"], h["or_high"], h["bar_start"], h["pct_over"]) for h in doc["hits"]]
        self.assertEqual(got, [("HDFCBANK", "9:35 AM", 682.5, 682.45, "09:30", 0.01),
                               ("RELIANCE", "9:40 AM", 1174.3, 1170.5, "09:35", 0.32)])
        self.assertEqual((doc["counts"]["scanned"], doc["counts"]["candidates"] >= 2), (4, True))

    def test_later_in_the_day(self):
        code, text, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 14:30", "--json"])
        hits = {h["symbol"]: h for h in json.loads(text)["hits"]}
        self.assertEqual(hits["M&M"]["at"], "1:00 PM")
        self.assertNotIn("BAJAJ-AUTO", hits)

    def test_a_chart_with_a_missing_bar_is_unusable(self):
        shutil.copy(REL15, "RELIANCE.json")
        code, text, err = run(["orb", "--offline", "RELIANCE.json", "--now", "2026-10-15 12:30", "--json"])
        self.assertEqual(json.loads(text)["counts"]["unusable"], 1)      # the real 11:45 gap
        code, text, err = run(["orb", "--offline", "RELIANCE.json", "--now", "2026-10-15 11:40", "--json"])
        hits = json.loads(text)["hits"]
        self.assertEqual((hits[0]["at"], hits[0]["price"]), ("9:55 AM", 1186.1))   # the same trigger as trader-entry

    def test_the_window_says_why(self):
        for now, words in (("2026-10-29 09:31", "Run orb again after 9:35 AM"),
                           ("2026-10-29 14:45", "after 14:30, your last time for new entries"),
                           ("2026-10-20 10:30", "NSE holiday: Dussehra"),
                           ("2026-11-08 18:30", "Muhurat")):
            code, _, err = run(["orb", "--offline", WALL, "--now", now])
            self.assertEqual(code, 1, now)
            self.assertIn(words, err, now)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 15:40", "--at", "15:20"])
        self.assertIn("after continuous trading ends (3:15 PM)", err)

    def test_traded_value_floor_in_the_orb_scan(self):
        code, text, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:30", "--json", "--min-value", "1e9"])
        doc = json.loads(text)
        self.assertGreater(doc["counts"]["illiquid"], 0)
        self.assertEqual(doc["filters"]["min_value"], 1e9)

    def test_text_output(self):
        code, text, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:30"])
        self.assertIn("9:15 to 9:30 AM", text)
        self.assertIn("HDFCBANK", text)
        self.assertIn("Rs 1,174.30", text)
        self.assertNotIn("$", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-scanner", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-scanner\n"))
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("nifty 100", "nse", "ist", "traded value", "not a list of stocks to buy", "survivorship"):
            self.assertIn(must, low)
        for never in (EM_DASH, "nasdaq", "nyse", "new york", "pro" + "fit"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")


if __name__ == "__main__":
    unittest.main()
