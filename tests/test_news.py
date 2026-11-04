"""trader-news tests, NSE India version. gnews-RELIANCE.xml is a real Google News RSS answer
(India edition) for the query "RELIANCE" NSE share, saved 2026-11-01 with curl, unmodified.
feed-et/pulse/mint/nse.xml are the real ET Markets, Zerodha Pulse, Livemint and NSE
announcement feeds, saved from India on 2026-11-01 13:46 IST with curl, unmodified.
The chart file is the real Yahoo RELIANCE.NS answer used by the trader-alerts tests."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

news = load("trader-news", "news")
NDIR = os.path.join(FIX, "nse")
NOW = "2026-11-01T06:00:00+00:00"   # 11:30 IST
EM_DASH = chr(0x2014)
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(NDIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Feeds(unittest.TestCase):
    def test_google_news_rss(self):
        items = news.parse_rss(raw("gnews-RELIANCE.xml"), "RELIANCE")
        self.assertEqual(len(items), 14)
        first = items[0]
        # Google appends " - Publisher" to every title: it is stripped and kept as the source
        self.assertEqual(first["title"], "Reliance Industries Share Price - Live NSE: RELIANCE Stock Price & Chart")
        self.assertEqual(first["source"], "Upstox")
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 31, 0, 11, 11, tzinfo=UTC))
        self.assertTrue(first["link"].startswith("https://news.google.com/rss/articles/"))
        self.assertTrue(all(i["id"].startswith("rss:") for i in items))
        self.assertIn("Reuters", {i["source"] for i in items})

    def test_urls_for_nse_symbols(self):
        self.assertEqual(news.GNEWS % news.news_query("reliance"),
                         "https://news.google.com/rss/search?q=%22RELIANCE%22%20NSE%20share"
                         "&hl=en-IN&gl=IN&ceid=IN:en")
        self.assertEqual(news.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(news.yahoo_symbol("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("^NSEI"), "%5ENSEI")

    def test_the_move_in_rupees(self):
        m = news.parse_chart(raw("yahoo-RELIANCE.json"), "RELIANCE")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (1164.9, 1167.4, -0.21))
        line = news.render_moves([m])[0]
        self.assertIn("Rs 1,164.90 -0.21% vs previous close Rs 1,167.40 at 15:", line)
        self.assertIn("IST", line)


class Network(unittest.TestCase):
    """The live price-move path (Dhan Data API): dhan_find_security and _dhan_post are
    swapped for fakes, so no test ever touches the real network or needs real
    credentials. --offline (chart-RELIANCE.json) is covered by Feeds.test_the_move_in_rupees
    and the Run/MarketFeeds offline tests."""

    def setUp(self):
        self._find, self._post, self._avail = news.dhan_find_security, news._dhan_post, news.dhan_available
        news.dhan_find_security = lambda sym, path=None: ("2885", "NSE_EQ", "EQUITY")
        news.dhan_available = lambda: True

    def tearDown(self):
        news.dhan_find_security, news._dhan_post, news.dhan_available = self._find, self._post, self._avail

    def intraday_payload(self):
        return {"timestamp": [1793000000, 1793000300], "open": [1160.0, 1163.0],
                "high": [1162.0, 1168.0], "low": [1158.0, 1161.0], "close": [1161.0, 1164.9],
                "volume": [1000, 1200]}

    def daily_payload(self):
        return {"timestamp": [1792900000], "open": [1150.0], "high": [1170.0],
                "low": [1145.0], "close": [1167.4], "volume": [500000]}

    def test_move_builds_price_and_pct_from_dhan(self):
        intraday, daily = self.intraday_payload(), self.daily_payload()

        def fake(url, body, timeout=30):
            return intraday if url == news.DHAN_INTRADAY_URL else daily
        news._dhan_post = fake
        m = news.dhan_move("RELIANCE")
        self.assertEqual((m["ticker"], m["price"], m["prev_close"]), ("RELIANCE", 1164.9, 1167.4))
        self.assertEqual(m["pct"], round((1164.9 / 1167.4 - 1) * 100, 2))

    def test_http_error_from_dhan_becomes_a_clean_news_error(self):
        def fake(url, body, timeout=30):
            raise news.DhanError("Dhan answered HTTP 500: server error")
        news._dhan_post = fake
        with self.assertRaises(news.NewsError) as cm:
            news.dhan_move("RELIANCE")
        self.assertIn("HTTP 500", str(cm.exception))

    def test_missing_credentials_is_a_clear_message(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(d)
        try:
            for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"):
                os.environ.pop(k, None)
            with self.assertRaises(news.NewsError) as cm:
                news.dhan_move("RELIANCE")
            self.assertIn("no Dhan credentials found", str(cm.exception))
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)

    def test_collect_moves_over_the_network_path(self):
        intraday, daily = self.intraday_payload(), self.daily_payload()

        def fake(url, body, timeout=30):
            return intraday if url == news.DHAN_INTRADAY_URL else daily
        news._dhan_post = fake
        fetcher = news.Fetcher(offline=None)
        moves, errors = [], []
        moves = news.collect_moves(fetcher, {"RELIANCE": None}, errors, pause=0)
        self.assertEqual(errors, [])
        self.assertEqual(moves[0]["ticker"], "RELIANCE")
        self.assertEqual(moves[0]["price"], 1164.9)


class FakeYahooResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class YahooFallback(unittest.TestCase):
    """The automatic tier-3 fallback: no Dhan key, no --offline, so collect_moves() reads
    Yahoo's free chart endpoint directly and warns about it once a call, not once a symbol."""

    def setUp(self):
        self._avail = news.dhan_available
        self._urlopen = news.urllib.request.urlopen
        self._env = {k: os.environ.pop(k, None) for k in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "TRADER_BROKER_CONFIG")}
        news.dhan_available = lambda: False
        with open(os.path.join(NDIR, "yahoo-RELIANCE.json"), "rb") as fh:
            self._fixture_bytes = fh.read()

    def tearDown(self):
        news.dhan_available = self._avail
        news.urllib.request.urlopen = self._urlopen
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_fetch_yahoo_move_matches_the_fixture(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeYahooResponse(self._fixture_bytes)
        news.urllib.request.urlopen = fake_urlopen
        m = news.fetch_yahoo_move("RELIANCE")
        self.assertIn("query2.finance.yahoo.com", calls[0])
        self.assertEqual(m["price"], 1164.9)

    def test_retries_the_alternate_host_on_429(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "query2" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return FakeYahooResponse(self._fixture_bytes)
        news.urllib.request.urlopen = fake_urlopen
        m = news.fetch_yahoo_move("RELIANCE")
        self.assertEqual(len(calls), 2)
        self.assertIn("query1", calls[1])
        self.assertEqual(m["price"], 1164.9)

    def test_a_real_http_error_does_not_retry_and_raises_cleanly(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        news.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(news.NewsError) as cm:
            news.fetch_yahoo_move("RELIANCE")
        self.assertEqual(len(calls), 1)
        self.assertIn("HTTP 404", str(cm.exception))

    def test_a_read_timeout_does_not_crash_and_raises_cleanly(self):
        # a read timeout (TimeoutError/socket.timeout) is an OSError, not a urllib.error.URLError
        def fake_urlopen(req, timeout=None):
            raise TimeoutError("timed out")
        news.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(news.NewsError) as cm:
            news.fetch_yahoo_move("RELIANCE")
        self.assertIn("timed out", str(cm.exception))

    def test_collect_moves_falls_back_to_yahoo_and_warns_once(self):
        def fake_urlopen(req, timeout=None):
            return FakeYahooResponse(self._fixture_bytes)
        news.urllib.request.urlopen = fake_urlopen
        fetcher = news.Fetcher(offline=None)
        errors = []
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            moves = news.collect_moves(fetcher, {"RELIANCE": None, "M&M": None}, errors, pause=0)
        self.assertEqual(errors, [])
        self.assertEqual({m["ticker"] for m in moves}, {"RELIANCE", "M&M"})
        self.assertEqual(err.getvalue().count("WARNING"), 1)
        self.assertIn("Rs 20.50", err.getvalue())

    def test_offline_wins_even_with_no_dhan_key(self):
        def fake_urlopen(req, timeout=None):
            raise AssertionError("offline must not touch the network")
        news.urllib.request.urlopen = fake_urlopen
        d = tempfile.mkdtemp()
        try:
            shutil.copy(os.path.join(NDIR, "yahoo-RELIANCE.json"), os.path.join(d, "chart-RELIANCE.json"))
            fetcher = news.Fetcher(offline=d)
            errors = []
            moves = news.collect_moves(fetcher, {"RELIANCE": None}, errors, pause=0)
            self.assertEqual(errors, [])
            self.assertEqual(moves[0]["price"], 1164.9)
        finally:
            shutil.rmtree(d)


class Tags(unittest.TestCase):
    def item(self, title):
        return {"id": "rss:x", "ticker": "HDFCBANK", "kind": "headline", "title": title, "summary": "",
                "link": "", "time": None, "source": "Google News"}

    def test_india_keywords_and_mentions(self):
        watch = {"HDFCBANK": None, "RELIANCE": "Reliance Industries"}
        its = news.tag([self.item("SEBI order on bank; NSE: RELIANCE hits upper circuit after Reliance Industries Q2"),
                        self.item("RBI keeps repo rate unchanged")], watch)
        a, b = its
        self.assertEqual(a["touches"], ["HDFCBANK", "RELIANCE"])
        for t in ("earnings", "legal", "halt", "regulatory"):
            self.assertIn(t, a["tags"])
        self.assertTrue(a["important"])
        self.assertIn("macro", b["tags"])


class Run(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        self.feeds = os.path.join(self.d, "feeds")
        os.mkdir(self.feeds)
        shutil.copy(os.path.join(NDIR, "gnews-RELIANCE.xml"), self.feeds)
        shutil.copy(os.path.join(NDIR, "yahoo-RELIANCE.json"), os.path.join(self.feeds, "chart-RELIANCE.json"))
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_offline_check_then_nothing_new(self):
        args = ["--tickers", "RELIANCE", "--offline", self.feeds, "--now", NOW, "--hours", "96"]
        code, text, err = run(args)
        self.assertEqual(code, 0, err)
        self.assertIn("RELIANCE   Rs 1,164.90", text)
        self.assertIn("2026-10-31 05:41 IST", text)
        self.assertIn("Checked 2026-11-01 11:30 IST", text)
        for never in ("SEC", " ET", "New York"):
            self.assertNotIn(never, text)
        self.assertNotIn("$", text.splitlines()[0])   # headlines may quote dollars; our numbers never do
        code, text, err = run(args)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", text)
        with open(os.path.join("trader", "state", "news.json")) as fh:
            self.assertTrue(json.load(fh)["seen"])

    def test_json_and_no_sec_flags(self):
        code, text, _ = run(["--tickers", "RELIANCE", "--offline", self.feeds, "--now", NOW,
                             "--hours", "96", "--json", "--dry-run"])
        doc = json.loads(text)
        self.assertTrue(doc["items"])
        self.assertEqual(doc["moves"][0]["ticker"], "RELIANCE")
        with self.assertRaises(SystemExit):
            run(["--tickers", "RELIANCE", "--no-sec"])
        self.assertFalse(hasattr(news, "parse_sec_atom"))


class MarketFeeds(unittest.TestCase):
    def feed(self, key, label):
        return news.parse_market_feed(raw("feed-%s.xml" % key), label)

    def test_the_four_feeds_parse_with_times(self):
        for key, label, n in (("et", "ET Markets", 50), ("pulse", "Zerodha Pulse", 25), ("mint", "Livemint", 35),
                              ("nse", "NSE announcement", 99)):
            items = self.feed(key, label)
            self.assertEqual(len(items), n, key)
            self.assertTrue(all(i["time"] for i in items), key)
            self.assertTrue(all(i["source"] == label for i in items), key)
        nse = self.feed("nse", "NSE announcement")[0]
        # "01-Nov-2026 13:45:06" is IST
        self.assertEqual(nse["time"], datetime.datetime(2026, 11, 1, 8, 15, 6, tzinfo=UTC))
        self.assertTrue(nse["title"].startswith("Maruti Suzuki India Limited: Maruti Suzuki India Limited has informed"))

    def test_only_items_naming_your_companies_are_kept(self):
        watch = {"RELIANCE": "Reliance Industries Ltd.", "MARUTI": "Maruti Suzuki India Ltd.", "BSE": "BSE Ltd.",
                 "ITC": "ITC Ltd."}
        items = []
        for key, label in (("et", "ET Markets"), ("pulse", "Zerodha Pulse"), ("mint", "Livemint"),
                           ("nse", "NSE announcement")):
            items += self.feed(key, label)
        kept = [i for i in news.dedupe(news.tag(items, watch)) if i["touches"]]
        titles = {i["title"] for i in kept}
        self.assertTrue(any(i["touches"] == ["RELIANCE"] and i["title"].startswith(
            "Reliance Industries Q2 Results: Profit shoots up 41% YoY") for i in kept), titles)
        self.assertTrue(any(i["touches"] == ["ITC"] and "ITC Q2 Results Preview" in i["title"] for i in kept))
        ann = [i for i in kept if i["source"] == "NSE announcement"]
        self.assertEqual([i["touches"] for i in ann], [["MARUTI"]])
        self.assertTrue(ann[0]["important"])
        # "BSE" is also the exchange's name: a story that only mentions the exchange is not about BSE Ltd.
        self.assertFalse(any("BSE" in i["touches"] for i in kept))

    def test_short_names(self):
        self.assertEqual(news.short_names("Maruti Suzuki India Ltd."), ["Maruti Suzuki India", "Maruti Suzuki"])
        self.assertEqual(news.short_names("Tata Motors Passenger Vehicles Limited"),
                         ["Tata Motors Passenger Vehicles"])
        self.assertEqual(news.short_names("ITC Ltd."), [])         # too short: the symbol "ITC" covers it
        self.assertEqual(news.short_names("Bajaj"), [])            # a bare group name never matches

    def test_offline_run_with_every_source(self):
        d = tempfile.mkdtemp()
        try:
            for name in ("gnews-RELIANCE.xml", "feed-et.xml", "feed-pulse.xml", "feed-mint.xml", "feed-nse.xml"):
                shutil.copy(os.path.join(NDIR, name), d)
            shutil.copy(os.path.join(NDIR, "yahoo-RELIANCE.json"), os.path.join(d, "chart-RELIANCE.json"))
            with open(os.path.join(d, "universe.txt"), "w") as fh:     # what trader-scanner writes
                fh.write("RELIANCE|Reliance Industries Ltd.\nMARUTI|Maruti Suzuki India Ltd.\n")
            code, text, err = run(["--tickers", "RELIANCE", "--offline", d, "--now", "2026-11-01T08:30:00+00:00",
                                   "--hours", "72", "--dry-run", "--state", os.path.join(d, "s.json"),
                                   "--universe", os.path.join(d, "universe.txt")])
            self.assertEqual(code, 0, err)
            self.assertIn("ET Markets", text)
            self.assertIn("Livemint", text)
            self.assertNotIn("Maruti", text)          # not one of the symbols asked for
            code, text, err = run(["--tickers", "RELIANCE", "--sources", "moneycontrol"])
            self.assertEqual(code, 2)
            self.assertIn("unknown source", err)
        finally:
            shutil.rmtree(d)

    def test_a_feed_timeout_is_a_clean_error_not_a_crash(self):
        # a slow/unreachable feed (seen for real against nsearchives.nseindia.com) raised a
        # bare TimeoutError from inside urlopen's "with" block: not a urllib.error.URLError,
        # so it was not caught and crashed the whole check instead of being skipped.
        fetcher = news.Fetcher(offline=None)
        orig = news.urllib.request.urlopen

        def fake_urlopen(req, timeout=None):
            raise TimeoutError("timed out")
        news.urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(news.NewsError) as cm:
                fetcher.get("https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml", "feed-nse.xml")
            self.assertIn("timed out", str(cm.exception))
        finally:
            news.urllib.request.urlopen = orig


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-news", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("google news", "ist", "leads, not facts", "nseindia.com"):
            self.assertIn(must, low)
        self.assertIn("8-k filings. they are us-only and are not used here", low)
        for never in (EM_DASH, "new york", "tsla", "--no-sec", "sec_user_agent"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
