"""trader-alerts tests, NSE India version. Quote fixtures in tests/fixtures/nse/ are real Yahoo
range=1d answers saved 2026-11-01 with curl, unmodified: RELIANCE 1164.90 (previous close
1167.40) and M&M 2840.30 (previous close 2890.00), both as of Fri 2026-10-30."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

alerts = load("trader-alerts", "alerts")

NDIR = os.path.join(FIX, "nse")
EM_DASH = chr(0x2014)
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-30", "setup": "orb",
        "entry": 1170.0, "stop": 1165.0, "target": 1175.0, "flat_by": "15:00"}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


class Clock(unittest.TestCase):
    def status(self, now):
        return alerts.market_status(alerts.parse_now(now))

    def test_open_closed_and_the_auction_session(self):
        st = self.status("2026-10-15 09:56")
        self.assertEqual((st["open"], st["opens"], st["closes"]), (True, "09:15", "15:15"))
        self.assertFalse(self.status("2026-10-15 09:10")["open"])          # the pre-open auction
        st = self.status("2026-10-15 15:20")                               # Closing Auction Session
        self.assertEqual((st["open"], st["reason"]), (False, "after continuous trading"))
        self.assertEqual(self.status("2026-07-31 15:20")["closes"], "15:30")   # before CAS go-live

    def test_holidays_weekends_and_special_sessions(self):
        st = self.status("2026-10-20 10:00")
        self.assertIn("Dussehra", st["reason"])
        self.assertEqual(st["next_open"], "2026-10-21 09:15")
        self.assertEqual(self.status("2026-10-17 10:00")["next_open"], "2026-10-19 09:15")
        self.assertIn("Muhurat", self.status("2026-11-08 18:30")["reason"])
        self.assertFalse(self.status("2027-01-04 10:00")["calendar_known"])

    def test_clock_text(self):
        code, text, _ = run(["clock", "--now", "2026-10-15 09:56"])
        self.assertIn("IST 2026-10-15 09:56 (Thursday): NSE OPEN", text)
        self.assertNotIn("New York", text)


class Quotes(unittest.TestCase):
    def test_offline_quotes_in_rupees(self):
        q, errors = alerts.get_quotes(["RELIANCE", "M&M"], offline=NDIR)
        self.assertEqual(errors, [])
        self.assertEqual((q["RELIANCE"]["price"], q["RELIANCE"]["prev_close"]), (1164.9, 1167.4))
        self.assertEqual((q["M&M"]["price"], q["M&M"]["currency"]), (2840.3, "INR"))
        code, text, _ = run(["quote", "RELIANCE", "M&M", "--offline", NDIR, "--now", "2026-10-30 15:40"])
        self.assertIn("RELIANCE    Rs 1164.90 (-0.21% vs prev close Rs 1167.40)", text)
        self.assertNotIn("$", text)

    def test_yahoo_urls(self):
        self.assertEqual(alerts.yahoo_url("m&m"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=1d")
        self.assertIn("/chart/RELIANCE.NS?", alerts.yahoo_url("RELIANCE"))

    def test_there_is_no_stooq_source(self):
        with self.assertRaises(SystemExit):
            run(["quote", "RELIANCE", "--source", "stooq"])


class Network(unittest.TestCase):
    """The live path (Dhan Data API): dhan_find_security and _dhan_post are swapped for
    fakes, so no test ever touches the real network or needs real credentials.
    --offline is covered by Quotes.test_offline_quotes_in_rupees."""

    def setUp(self):
        self._find, self._post, self._avail = alerts.dhan_find_security, alerts._dhan_post, alerts.dhan_available
        alerts.dhan_find_security = lambda sym, path=None: ("2885", "NSE_EQ", "EQUITY")
        alerts.dhan_available = lambda: True

    def tearDown(self):
        alerts.dhan_find_security, alerts._dhan_post, alerts.dhan_available = self._find, self._post, self._avail

    def intraday_payload(self):
        # RELIANCE today, three 5-min bars: open 1160, high 1168, low 1158, last close 1164.90
        ts = [1793000000, 1793000300, 1793000600]
        return {"timestamp": ts, "open": [1160.0, 1163.0, 1162.0], "high": [1162.0, 1168.0, 1165.0],
                "low": [1158.0, 1161.0, 1160.0], "close": [1161.0, 1167.0, 1164.9],
                "volume": [1000, 1200, 900]}

    def daily_payload(self):
        # one daily bar: previous close 1167.40
        return {"timestamp": [1792900000], "open": [1150.0], "high": [1170.0],
                "low": [1145.0], "close": [1167.4], "volume": [500000]}

    def test_quote_builds_price_and_prev_close_from_dhan(self):
        intraday, daily = self.intraday_payload(), self.daily_payload()

        def fake(url, body, timeout=30):
            return intraday if url == alerts.DHAN_INTRADAY_URL else daily
        alerts._dhan_post = fake
        q = alerts.dhan_quote("RELIANCE")
        self.assertEqual(q["symbol"], "RELIANCE")
        self.assertEqual(q["price"], 1164.9)
        self.assertEqual(q["open"], 1160.0)
        self.assertAlmostEqual(q["high"], 1168.0)
        self.assertAlmostEqual(q["low"], 1158.0)
        self.assertEqual(q["prev_close"], 1167.4)
        self.assertEqual(q["currency"], "INR")
        self.assertEqual(q["source"], "dhan")

    def test_quote_survives_a_failed_prev_close_lookup(self):
        intraday = self.intraday_payload()

        def fake(url, body, timeout=30):
            if url == alerts.DHAN_INTRADAY_URL:
                return intraday
            raise alerts.DhanError("Dhan answered HTTP 429: rate limited")
        alerts._dhan_post = fake
        q = alerts.dhan_quote("RELIANCE")
        self.assertEqual(q["price"], 1164.9)
        self.assertIsNone(q["prev_close"])

    def test_no_bars_yet_today_is_a_clear_error(self):
        def fake(url, body, timeout=30):
            return {"timestamp": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        alerts._dhan_post = fake
        with self.assertRaises(alerts.AlertError) as cm:
            alerts.dhan_quote("RELIANCE")
        self.assertIn("no price for RELIANCE", str(cm.exception))

    def test_http_error_from_dhan_becomes_a_clean_alert_error(self):
        def fake(url, body, timeout=30):
            raise alerts.DhanError("Dhan answered HTTP 500: server error")
        alerts._dhan_post = fake
        with self.assertRaises(alerts.AlertError) as cm:
            alerts.dhan_quote("RELIANCE")
        self.assertIn("HTTP 500", str(cm.exception))

    def test_missing_credentials_is_a_clear_message(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        try:
            for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"):
                os.environ.pop(k, None)
            with self.assertRaises(alerts.AlertError) as cm:
                alerts.dhan_quote("RELIANCE")
            self.assertIn("no Dhan credentials found", str(cm.exception))
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)

    def test_get_quotes_over_the_network_path(self):
        intraday, daily = self.intraday_payload(), self.daily_payload()

        def fake(url, body, timeout=30):
            return intraday if url == alerts.DHAN_INTRADAY_URL else daily
        alerts._dhan_post = fake
        q, errors = alerts.get_quotes(["RELIANCE"], pause=0)
        self.assertEqual(errors, [])
        self.assertEqual(q["RELIANCE"]["price"], 1164.9)
        self.assertEqual(q["RELIANCE"]["source"], "dhan")


class FakeYahooResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class YahooFallback(unittest.TestCase):
    """The automatic tier-3 fallback: no Dhan key, no --offline, so get_quotes() reads
    Yahoo's free chart endpoint directly and warns about it once a call, not once a symbol."""

    def setUp(self):
        self._avail = alerts.dhan_available
        self._urlopen = alerts.urllib.request.urlopen
        self._env = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        alerts.dhan_available = lambda: False
        with open(os.path.join(NDIR, "yahoo-RELIANCE.json"), encoding="utf-8") as fh:
            self._fixture_text = fh.read()

    def tearDown(self):
        alerts.dhan_available = self._avail
        alerts.urllib.request.urlopen = self._urlopen
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_fetch_yahoo_quote_matches_the_fixture(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        alerts.urllib.request.urlopen = fake_urlopen
        q = alerts.fetch_yahoo_quote("RELIANCE")
        self.assertIn("query2.finance.yahoo.com", calls[0])
        self.assertEqual(q["price"], 1164.9)

    def test_retries_the_alternate_host_on_429(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "query2" in req.full_url:
                raise alerts.urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        alerts.urllib.request.urlopen = fake_urlopen
        q = alerts.fetch_yahoo_quote("RELIANCE")
        self.assertEqual(len(calls), 2)
        self.assertIn("query1", calls[1])
        self.assertEqual(q["price"], 1164.9)

    def test_a_real_http_error_does_not_retry_and_raises_cleanly(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise alerts.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        alerts.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(alerts.AlertError) as cm:
            alerts.fetch_yahoo_quote("RELIANCE")
        self.assertEqual(len(calls), 1)
        self.assertIn("HTTP 404", str(cm.exception))

    def test_a_read_timeout_does_not_crash_and_raises_cleanly(self):
        # a read timeout (TimeoutError/socket.timeout) is an OSError, not a urllib.error.URLError
        def fake_urlopen(req, timeout=None):
            raise TimeoutError("timed out")
        alerts.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(alerts.AlertError) as cm:
            alerts.fetch_yahoo_quote("RELIANCE")
        self.assertIn("timed out", str(cm.exception))

    def test_get_quotes_falls_back_to_yahoo_and_warns_once(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeYahooResponse(self._fixture_text.encode("utf-8"))
        alerts.urllib.request.urlopen = fake_urlopen
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            q, errors = alerts.get_quotes(["RELIANCE", "M&M"], pause=0)
        self.assertEqual(errors, [])
        self.assertEqual(q["RELIANCE"]["price"], 1164.9)
        self.assertEqual(err.getvalue().count("WARNING"), 1)
        self.assertIn("Rs 20.50", err.getvalue())

    def test_offline_wins_even_with_no_dhan_key(self):
        def fake_urlopen(req, timeout=None):
            raise AssertionError("offline must not touch the network")
        alerts.urllib.request.urlopen = fake_urlopen
        q, errors = alerts.get_quotes(["RELIANCE"], offline=NDIR)
        self.assertEqual(errors, [])
        self.assertEqual(q["RELIANCE"]["price"], 1164.9)


class Rules(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        with open(os.path.join("trader", "plan.json"), "w") as fh:
            json.dump(PLAN, fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_rules_from_the_plan(self):
        rules = alerts.rules_from_plan(PLAN)
        kinds = sorted((r.get("type"), r.get("price") or r.get("at")) for r in rules)
        self.assertIn(("time", "15:00"), kinds)
        self.assertTrue(any(r.get("price") == 1165.0 for r in rules))
        self.assertTrue(any(r.get("price") == 1175.0 for r in rules))
        self.assertTrue(all(r.get("date") == "2026-10-30" for r in rules))

    def test_the_stop_rings_and_the_flat_by_time_rings(self):
        code, text, err = run(["add", "--from-plan"])
        self.assertEqual(code, 0, err)
        # RELIANCE at 1164.90 is under the 1165.00 stop
        code, text, err = run(["check", "--offline", NDIR, "--now", "2026-10-30 14:00"])
        self.assertEqual(code, 0, err)
        self.assertIn("RELIANCE", text)
        self.assertIn("Rs 1164.90", text)
        self.assertNotIn("$", text)
        code, text, err = run(["check", "--offline", NDIR, "--now", "2026-10-30 15:01"])
        self.assertIn("15:00", text)
        self.assertIn("IST", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-alerts", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("nse", "ist", "15:15", "09:15"):
            self.assertIn(must, low)
        for never in (EM_DASH, "nyse", "new york", "stooq"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")


if __name__ == "__main__":
    unittest.main()
