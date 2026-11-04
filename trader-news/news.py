#!/usr/bin/env python3
"""News monitor for trader-news, NSE India version. Standard library only.

Watches headlines for the NSE stocks you watch or hold and prints only what is
new since the last check, newest first, with how far each stock has moved
today and a flag on anything that touches your symbols.

    python3 -B news.py                                   # watchlist + plan + portfolio symbols
    python3 -B news.py --tickers RELIANCE HDFCBANK M&M
    python3 -B news.py --tickers RELIANCE --all --hours 72
    python3 -B news.py --no-moves                        # skip the price line per symbol

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json.

Sources (--sources, all by default):
  - Google News RSS search, India edition (Yahoo's headline RSS returns nothing
    for NSE symbols). An aggregator: each item names its publisher.
    https://news.google.com/rss/search?q="<SYMBOL>" NSE share&hl=en-IN&gl=IN&ceid=IN:en
  - The move: the last price against the previous close. This pack is open source and
    brings no data source of its own, so it tries, in order: --offline DIR reading
    DIR/chart-<SYMBOL>.json (the legacy Yahoo-shaped file every fixture here uses);
    else your own Dhan Data API key if you have one (two calls a symbol: today's
    intraday bars, the prior daily bar for the previous close; DHAN_CLIENT_ID/
    DHAN_ACCESS_TOKEN, see tools/dhan_block.py); else Yahoo's free chart automatically,
    one call a symbol too, with a warning (see SKILL.md "Data warning"), so a user
    with no broker account never hits a hard failure here either.
  - Market-wide feeds, one request each per check, kept only when an item names
    one of your symbols or companies: ET Markets (stocks), Zerodha Pulse,
    Livemint (markets) and NSE's corporate announcements feed. Company names come
    from the watchlist or trader/universe.txt (trader-scanner writes it).

The original's SEC EDGAR filings are US-only and are not used. Their Indian
counterpart is the NSE announcements feed above (results, board meetings,
outcomes): an announcement for one of your companies is always marked "!".

Times print in IST. State: trader/state/news.json remembers what you have seen
(30 days), so an item shows once. Offline: --offline DIR reads
gnews-<SYMBOL>.xml, feed-<et|pulse|mint|nse>.xml and chart-<SYMBOL>.json
from DIR. The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import datetime
import datetime as _dt
import email.utils
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Yahoo's headline RSS returns no items for NSE symbols (checked 2026-11-01), so headlines come
# from Google News' public RSS search, India edition. An aggregator: leads, not facts.
GNEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
# Market-wide Indian feeds, one request each per check (not per symbol). An item is kept only
# when it names one of your symbols or companies. Checked from India on 2026-11-01: all live.
# Moneycontrol's RSS files still answer but have not been updated since 2024, so they are not
# used; StockEdge and Groww publish no public feed.
MARKET_FEEDS = {
    "et": ("ET Markets", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    "pulse": ("Zerodha Pulse", "https://pulse.zerodha.com/feed.php"),
    "mint": ("Livemint", "https://www.livemint.com/rss/markets"),
    "nse": ("NSE announcement", "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"),
}
DEFAULT_SOURCES = ("google", "et", "pulse", "mint", "nse")
AMBIGUOUS_SYMBOLS = {"BSE", "NSE"}   # also the names of the exchanges: matched only as "NSE: BSE" or "(BSE)"
NAME_SUFFIX = r"\s*(?:\(India\)\s*)?(?:Limited|Ltd\.?)\s*$"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
BROWSER_UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving

# no filings feed here, so "!" marks a headline tagged with one of these
IMPORTANT_TAGS = {"earnings", "halt", "risk", "legal", "regulatory"}
KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bQ[1-4]\b|\bprofit\b|\bEPS\b|\bguidance\b|\bforecast\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|\boutperform\b|\bunderperform\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bSEBI order\b|\bED\b|\bantitrust\b|\bjudge\b|\bcourt\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bbuyout\b|\btakeover\b|\bdeal\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\brepurchase\b|\bstock split\b|\bbonus\b|\brights issue\b|\bQIP\b|\bOFS\b|\bblock deal\b|\boffering\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\brecall\b|\bbankrupt|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\b(?:upper|lower) circuit\b|\bF&O ban\b"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bCCI\b|\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b|\bclinical\b"),
    ("macro", r"\brepo rate\b|\bMPC\b|\bFed\b|\bCPI\b|\binflation\b|\bGDP\b|\bFIIs?\b|\bFPIs?\b|\btariffs?\b|\brupee\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

# ---- BEGIN NSE MARKET BLOCK (source: tools/nse_block.py, copied by tools/sync_nse_block.py; do not edit here) ----
# NSE cash market (equity, intraday) clock, calendar and tick size. Standard library only.
# Every skill is a standalone script, so this block is copied into each one and a test
# keeps the copies identical. All times are IST, a fixed UTC+05:30 with no daylight time.
#
# Sources (read 2026-11-01):
#   Trading holidays 2026, Capital Market: NSE circular 172/2025 (NSE/CMTR/71775, 2025-12-12)
#     https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf
#   Extra holiday 2026-01-15: NSE circular 07/2026 (NSE/CMTR/72260, 2026-01-12)
#     https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf
#   Closing Auction Session from 2026-08-03 (continuous trading ends 15:15 for CAS securities):
#     NSE circular 108/2026 (NSE/CMTR/75479, 2026-07-30)
#     https://nsearchives.nseindia.com/content/circulars/CMTR75479.pdf
#     https://www.nseindia.com/static/products-services/closing-auction-session
#   Tick size by price band, reviewed monthly on the previous month's last close (ETFs excluded):
#     NSE circular 33/2025 (NSE/CMTR/67133, 2025-03-13)
#     https://nsearchives.nseindia.com/content/circulars/CMTR67133.pdf

IST = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)
OPEN = datetime.time(9, 15)              # normal market open (the 09:00-09:15 pre-open auction is not trading)
CLOSE = datetime.time(15, 30)            # normal market close
CAS_START = datetime.date(2026, 8, 3)    # Closing Auction Session go-live
CAS_CLOSE = datetime.time(15, 15)        # continuous trading ends here for CAS securities from CAS_START
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election in Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
# Special sessions are not normal trading days for these tools (and never enter a backtest pool).
NSE_SPECIAL_SESSIONS = {
    "2026-11-08": "Muhurat trading (Sunday, Diwali Laxmi Pujan; timings notified separately)",
}
CALENDAR_YEARS = (2026,)
# (upper price bound, tick): a price above 1,000 and up to 5,000 trades in 0.10 steps, and so on.
TICK_BANDS = ((250.0, 0.01), (1000.0, 0.05), (5000.0, 0.10), (10000.0, 0.50), (20000.0, 1.00), (None, 5.00))


def to_ist(utc):
    """Naive UTC -> naive IST wall-clock."""
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    """Naive IST wall-clock -> naive UTC (always exact: IST has no daylight time)."""
    return local.replace(tzinfo=None) - IST


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    """Unix seconds -> naive UTC, without the machine's time zone."""
    return EPOCH + datetime.timedelta(seconds=int(ts))


def ts_of(utc):
    return int((utc.replace(tzinfo=None) - EPOCH).total_seconds())


def trading_day(d):
    """(is_trading_day, reason). Weekends, the NSE holiday list and special sessions."""
    key = d.isoformat()
    if key in NSE_SPECIAL_SESSIONS:
        return False, "special session, not a normal trading day: " + NSE_SPECIAL_SESSIONS[key]
    if d.weekday() >= 5:
        return False, "weekend"
    if key in NSE_HOLIDAYS:
        return False, "NSE holiday: " + NSE_HOLIDAYS[key]
    return True, ""


def continuous_close(d, cas=True):
    """When continuous trading ends on day d: 15:15 for CAS securities from 2026-08-03, else 15:30."""
    return CAS_CLOSE if (cas and d >= CAS_START) else CLOSE


def session(d, cas=True):
    """(open_time, close_time) of continuous trading in IST, or None if not a normal trading day."""
    ok, _ = trading_day(d)
    if not ok:
        return None
    return OPEN, continuous_close(d, cas)


def continuous_bars(d, step_seconds=300, cas=True):
    """Number of bars of continuous trading: 72 five-minute bars with CAS, 75 without."""
    s = session(d, cas)
    if not s:
        return 0
    mins = (s[1].hour * 60 + s[1].minute) - (s[0].hour * 60 + s[0].minute)
    return mins * 60 // step_seconds


def tick_for(ref_price, etf=False):
    """NSE tick size for a security whose reference price (the previous month's last close)
    is ref_price. ETFs are outside the price-band table: 0.01."""
    if etf:
        return 0.01
    p = float(ref_price)
    if p < TICK_BANDS[0][0]:          # "below 250": 250.00 itself is in the 0.05 band
        return TICK_BANDS[0][1]
    for upper, tick in TICK_BANDS[1:]:
        if upper is None or p <= upper:   # "above 1,000 to 5,000": the upper bound is inclusive
            return tick
    return TICK_BANDS[-1][1]


def round_tick(price, tick, mode="nearest"):
    """Round a price onto the tick grid. mode: "down", "up" or "nearest" (half up).
    It works in paise: a price within a quarter paisa of a whole paisa is that paisa
    (float noise from a chart feed, 1161.0999755 -> 116110 paise), while a real half
    paisa from a midpoint (1159.55 + ...)/2 stays. So noise cannot push a price one
    tick the wrong way."""
    p = float(price) * 100.0
    whole = round(p)
    if abs(p - whole) < 0.25:
        p = float(whole)
    tp = int(round(tick * 100))
    units = p / tp
    near = round(units)
    if abs(units - near) < 1e-9:
        units = near
    if mode == "down":
        n = math.floor(units)
    elif mode == "up":
        n = math.ceil(units)
    else:
        n = math.floor(units + 0.5)
    return round(n * tp / 100.0, 2)


def away_from_entry(stop, tick, long=True):
    """A stop rounds to the tick AWAY from the entry: down for a long, up for a short."""
    return round_tick(stop, tick, "down" if long else "up")


def toward_entry(target, tick, long=True):
    """A target rounds to the tick TOWARD the entry, so it never promises more than asked."""
    return round_tick(target, tick, "down" if long else "up")


def rs(x):
    """Rs 1,234.50 for people."""
    return "Rs %s" % format(float(x), ",.2f")
# ---- END NSE MARKET BLOCK ----


# ---- BEGIN DHAN DATA BLOCK (source: tools/dhan_block.py, copied by tools/sync_dhan_block.py; do not edit here) ----
# Open-source live data client: Dhan Data API (needs your own account + key) or
# --offline bars you made yourself. See tools/dhan_block.py for the full explanation.

import csv as _dhan_csv
import json as _dhan_json
import os as _dhan_os
import time as _dhan_time
import urllib.error as _dhan_urlerror
import urllib.request as _dhan_urlreq

DHAN_UA = "day-trader-skill-india/0.2 (+https://github.com/trmxvibs/day-trader-skill-india)"
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DHAN_HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"
DHAN_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
DHAN_SCRIP_MASTER_MAX_AGE = 24 * 3600
DHAN_INTRADAY_INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "25m": 25, "60m": 60}
# Index symbols this pack knows (Yahoo-style ^SYMBOL) -> Dhan's own trading symbol for the
# same index (security master, SEM_SEGMENT "I"). Security IDs cross-checked against this
# project's earlier Dhan chart work: NIFTY 50 = 13, INDIA VIX = 21.
DHAN_INDEX_SYMBOLS = {
    "^NSEI": "NIFTY",
    "^NSEBANK": "NIFTY BANK",
    "^INDIAVIX": "INDIA VIX",
    "^CNX100": "NIFTY 100",
    "^CNXIT": "NIFTY IT",
}
# Printed (to stderr) every time a skill falls back to the free, automatic, tier-3 Yahoo
# path (no Dhan key, no --offline): never silent, so no one mistakes free data for the
# Dhan-quality bars. See the module docstring above.
YAHOO_FALLBACK_WARNING = (
    "WARNING: no Dhan key and no --offline file, so this is reading free Yahoo data. "
    "A direct check against Dhan's own chart found Yahoo's NSE bars can be wrong (the "
    "09:15 bar's high short by up to Rs 20.50; the day's high or low missing on 31-37% "
    "of days). This pack takes no responsibility for "
    "any loss from using it; the stock market carries real risk. For money decisions, "
    "set up a Dhan Data API key (DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN) or bring --offline "
    "data read off your own broker's chart."
)


class DhanError(Exception):
    pass


def dhan_broker_config_path():
    return _dhan_os.environ.get("TRADER_BROKER_CONFIG") or _dhan_os.path.join("trader", "broker.json")


def dhan_credentials():
    """(client_id, access_token) from the environment, else trader/broker.json."""
    cid = _dhan_os.environ.get("DHAN_CLIENT_ID")
    tok = _dhan_os.environ.get("DHAN_ACCESS_TOKEN")
    if not (cid and tok):
        path = dhan_broker_config_path()
        if _dhan_os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = _dhan_json.load(fh)
            except ValueError as e:
                raise DhanError("%s is not valid JSON: %s" % (path, e))
            cid = cid or doc.get("dhan_client_id")
            tok = tok or doc.get("dhan_access_token")
    if not (cid and tok):
        raise DhanError(
            "no Dhan credentials found. Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN (environment "
            "variables) or put {\"dhan_client_id\": \"...\", \"dhan_access_token\": \"...\"} in "
            "%s. Generate a token at web.dhan.co under My Profile > DhanHQ Trading APIs (needs a "
            "Dhan trading account and the Data API add-on). No Dhan account? use --offline with "
            "bars you read off your own broker's chart." % path)
    return cid, tok


def dhan_available():
    """True if a Dhan key can be found (environment or trader/broker.json), without
    raising. Lets a skill quietly prefer Dhan over the automatic Yahoo fallback when a
    key is set, and fall through to Yahoo (with YAHOO_FALLBACK_WARNING) when it is not."""
    try:
        dhan_credentials()
        return True
    except DhanError:
        return False


def _dhan_read_url(url, timeout=30):
    req = _dhan_urlreq.Request(url, headers={"User-Agent": DHAN_UA})
    with _dhan_urlreq.urlopen(req, timeout=timeout) as r:
        return r.read()


def dhan_scrip_master_path():
    return _dhan_os.environ.get("TRADER_DHAN_SCRIP_MASTER") or _dhan_os.path.join("trader", "dhan_scrip_master.csv")


def dhan_ensure_scrip_master(path=None, max_age=DHAN_SCRIP_MASTER_MAX_AGE, fetch=_dhan_read_url):
    """Download Dhan's public security-master CSV if missing or older than max_age.
    No login needed; it is Dhan's own published file (same one their SDK downloads)."""
    path = path or dhan_scrip_master_path()
    stale = (not _dhan_os.path.exists(path)) or (_dhan_time.time() - _dhan_os.path.getmtime(path)) > max_age
    if stale:
        try:
            data = fetch(DHAN_SCRIP_MASTER_URL)
        except (_dhan_urlerror.URLError, _dhan_urlerror.HTTPError, OSError) as e:
            if _dhan_os.path.exists(path):
                return path   # network hiccup: keep using what is already cached
            raise DhanError("could not download Dhan's security master (%s): %s" % (DHAN_SCRIP_MASTER_URL, e))
        d = _dhan_os.path.dirname(path)
        if d:
            _dhan_os.makedirs(d, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    return path


def dhan_find_security(sym, path=None):
    """NSE symbol (as this pack writes it: RELIANCE, M&M, ^NSEI, ^INDIAVIX, ...)
    -> (security_id, exchange_segment, instrument), from Dhan's security master."""
    s = sym.strip().upper()
    is_index = s.startswith("^")
    want = DHAN_INDEX_SYMBOLS.get(s, s) if is_index else s
    path = dhan_ensure_scrip_master(path)
    try:
        fh = open(path, encoding="utf-8", errors="replace", newline="")
    except OSError as e:
        raise DhanError("could not read %s: %s" % (path, e))
    with fh:
        for row in _dhan_csv.DictReader(fh):
            if row.get("SEM_EXM_EXCH_ID") != "NSE":
                continue
            trading_symbol = (row.get("SEM_TRADING_SYMBOL") or "").strip().upper()
            if trading_symbol != want:
                continue
            if is_index and row.get("SEM_SEGMENT") == "I":
                return row["SEM_SMST_SECURITY_ID"], "IDX_I", "INDEX"
            if (not is_index) and row.get("SEM_SEGMENT") == "E" and row.get("SEM_SERIES") == "EQ":
                return row["SEM_SMST_SECURITY_ID"], "NSE_EQ", "EQUITY"
    raise DhanError("%s is not in Dhan's NSE security master (checked %s): check the symbol" % (sym, path))


def _dhan_post(url, body, timeout=30):
    _, tok = dhan_credentials()
    data = _dhan_json.dumps(body).encode("utf-8")
    req = _dhan_urlreq.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "access-token": tok})
    try:
        with _dhan_urlreq.urlopen(req, timeout=timeout) as r:
            return _dhan_json.loads(r.read().decode("utf-8", "replace"))
    except _dhan_urlerror.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        e.close()
        raise DhanError("Dhan answered HTTP %d: %s" % (e.code, detail[:300] or "no detail"))
    except _dhan_urlerror.URLError as e:
        raise DhanError("could not reach Dhan (%s): check your internet connection" % e.reason)
    except ValueError as e:
        raise DhanError("Dhan sent something that was not JSON: %s" % e)


def _dhan_bars_from_arrays(doc):
    ts = doc.get("timestamp") or []
    o = doc.get("open") or []
    h = doc.get("high") or []
    lo = doc.get("low") or []
    c = doc.get("close") or []
    v = doc.get("volume") or []
    bars = []
    for i, t in enumerate(ts):
        if t is None or i >= len(c) or c[i] is None:
            continue
        cc = round(float(c[i]), 2)
        bars.append({
            "t": int(t),
            "o": round(float(o[i]), 2) if i < len(o) and o[i] is not None else cc,
            "h": round(float(h[i]), 2) if i < len(h) and h[i] is not None else cc,
            "l": round(float(lo[i]), 2) if i < len(lo) and lo[i] is not None else cc,
            "c": cc,
            "v": int(v[i]) if i < len(v) and v[i] is not None else 0,
        })
    return bars


def dhan_get_intraday(sym, interval, day, extra_days_back=0, scrip_master_path_=None):
    """1m/5m/... bars for `sym` covering `day` (an IST date) from Dhan's v2 intraday
    endpoint. extra_days_back widens the window to also fetch earlier days in the same
    call (Dhan allows up to 90 days per request). Returns the pack's common chart dict:
    {symbol, meta, bars, prev_close, data_time}. prev_close is left None (not part of
    this endpoint's reply); a caller that needs it can read it from the previous day's
    close with dhan_get_daily."""
    if interval not in DHAN_INTRADAY_INTERVALS:
        raise DhanError("Dhan intraday here only takes %s (got %r)" % (sorted(DHAN_INTRADAY_INTERVALS), interval))
    import datetime as _dt
    security_id, seg, instr = dhan_find_security(sym, scrip_master_path_)
    start_day = day - _dt.timedelta(days=extra_days_back)
    body = {
        "securityId": security_id, "exchangeSegment": seg, "instrument": instr,
        "interval": DHAN_INTRADAY_INTERVALS[interval],
        "fromDate": "%s 00:00:00" % start_day.isoformat(),
        "toDate": "%s 23:59:59" % day.isoformat(),
    }
    doc = _dhan_post(DHAN_INTRADAY_URL, body)
    bars = _dhan_bars_from_arrays(doc)
    return {
        "symbol": sym.strip().upper(),
        "meta": {"symbol": sym.strip().upper(), "dataGranularity": interval, "source": "dhan"},
        "bars": bars,
        "prev_close": None,
        "data_time": bars[-1]["t"] if bars else None,
    }


def dhan_get_daily(sym, start, end, scrip_master_path_=None):
    """Daily OHLCV bars for `sym` from start to end (IST dates, inclusive), from Dhan's
    v2 historical endpoint. Returns [{"d": "YYYY-MM-DD", "o","h","l","c","v"}, ...]."""
    security_id, seg, instr = dhan_find_security(sym, scrip_master_path_)
    body = {
        "securityId": security_id, "exchangeSegment": seg, "instrument": instr,
        "fromDate": start.isoformat(), "toDate": end.isoformat(),
    }
    doc = _dhan_post(DHAN_HISTORICAL_URL, body)
    bars = _dhan_bars_from_arrays(doc)
    import datetime as _dt
    out = []
    for b in bars:
        d = (_dt.datetime(1970, 1, 1) + _dt.timedelta(seconds=b["t"])).date().isoformat()
        out.append({"d": d, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
    return out


# ---- END DHAN DATA BLOCK ----


def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{ticker: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[sym.upper()] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(pl["symbol"].upper(), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[sym.upper()] = p.get("name") or out.get(sym.upper())
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.15):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        headers = {"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))
        except OSError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc))


def news_query(ticker):
    """The Google News search for one NSE symbol: "RELIANCE" NSE share."""
    return urllib.parse.quote('"%s" NSE share' % ticker.upper())


def yahoo_symbol(ticker):
    """RELIANCE -> RELIANCE.NS, url-quoted (M&M -> M%26M.NS); ^NSEI and SYMBOL.NS stay."""
    t = ticker.upper()
    if not t.startswith("^") and "." not in t:
        t += ".NS"
    return urllib.parse.quote(t)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 (Atom) -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            d = None
    if d is None:
        try:   # NSE's announcement feed: "01-Nov-2026 13:45:06", IST
            d = _dt.datetime.strptime(value, "%d-%b-%Y %H:%M:%S").replace(tzinfo=_dt.timezone(IST))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, ticker):
    """Google News RSS -> items (the publisher, when the feed names it, in "source")."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        pub = _text(it, "source") or "Google News"
        if pub != "Google News" and title.endswith(" - " + pub):
            title = title[:-len(" - " + pub)]   # Google appends the publisher to each title
        out.append({"id": "rss:" + guid, "source": pub, "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def dhan_move(ticker, pause=0.0):
    """{"ticker","price","prev_close","pct","time"} built from the Dhan Data API: today's
    intraday bars give the last price, the most recent daily bar before today gives the
    previous close. Two Dhan calls; raises NewsError on any Dhan problem, same as the
    --offline path's parse_chart."""
    if pause:
        time.sleep(pause)
    sym = ticker.strip().upper()
    today = to_ist(utc_now()).date()
    try:
        ch = dhan_get_intraday(sym, "5m", today)
    except DhanError as exc:
        raise NewsError(str(exc))
    bars = ch["bars"]
    if not bars:
        raise NewsError("chart for %s has no price yet today" % ticker)
    try:
        rows = dhan_get_daily(sym, today - _dt.timedelta(days=10), today - _dt.timedelta(days=1))
    except DhanError as exc:
        raise NewsError(str(exc))
    if not rows:
        raise NewsError("chart for %s has no previous close" % ticker)
    price, prev = bars[-1]["c"], rows[-1]["c"]
    when = _dt.datetime.fromtimestamp(bars[-1]["t"], _dt.timezone.utc)
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2), "time": when}


YAHOO_HOSTS = ("query2", "query1")


def fetch_yahoo_move(ticker, pause=0.0, timeout=20):
    """{"ticker","price","prev_close","pct","time"} built from Yahoo's own chart endpoint:
    the free, automatic tier-3 fallback (no Dhan key, no --offline), a plain
    unauthenticated GET (no login, no scrape of a protected page), parsed with
    parse_chart() (the same function --offline chart-SYMBOL.json files use). Retries once
    on the alternate host for a 429 or a 5xx. See YAHOO_FALLBACK_WARNING and the SKILL.md
    "Data warning": a direct check found Yahoo's NSE bars can be wrong."""
    if pause:
        time.sleep(pause)
    sym = yahoo_symbol(ticker)
    last = None
    for host in YAHOO_HOSTS:
        req = urllib.request.Request(CHART % (host, sym), headers={"User-Agent": BROWSER_UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return parse_chart(data, ticker)
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code not in (429, 500, 502, 503, 504):
                break
        except urllib.error.URLError as e:
            last = str(e.reason)
            break
        except OSError as e:
            last = str(e)
            break
    raise NewsError("could not reach Yahoo's free chart endpoint for %s: %s" % (ticker, last))


def collect_moves(fetcher, watch, errors=None, pause=0.5):
    """The last price against the previous close for each symbol: --offline
    chart-SYMBOL.json files in the legacy Yahoo-shaped layout every fixture here uses if
    given, else a Dhan Data API call (two calls, counting the previous-close lookup) per
    symbol if a key is set, else Yahoo's free chart automatically (printing
    YAHOO_FALLBACK_WARNING once a call, not once a symbol)."""
    errors = [] if errors is None else errors
    out = []
    use_dhan = (not fetcher.offline) and dhan_available()
    warned = False
    for i, t in enumerate(sorted(watch)):
        try:
            if fetcher.offline:
                data = fetcher.get(CHART % ("query2", yahoo_symbol(t)), "chart-%s.json" % t)
                out.append(parse_chart(data, t))
            elif use_dhan:
                out.append(dhan_move(t, pause=pause if i else 0.0))
            else:
                if not warned:
                    print(YAHOO_FALLBACK_WARNING, file=sys.stderr)
                    warned = True
                out.append(fetch_yahoo_move(t, pause=pause if i else 0.0))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def short_names(name):
    """Reliance Industries Ltd. -> ["Reliance Industries"]; Maruti Suzuki India Ltd. ->
    ["Maruti Suzuki India", "Maruti Suzuki"]. Each at least two words or 6 letters, so a bare
    group name like "Tata" or "Bajaj" never matches every group company."""
    base = re.sub(NAME_SUFFIX, "", (name or "").strip(), flags=re.I).strip(" .,")
    out = []
    for n in (base, re.sub(r"\s+India$", "", base)):
        if n and n not in out and (" " in n or len(n) >= 6):
            out.append(n)
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        alts = [r"\(%s\)" % re.escape(t), r"\b(?:NSE|BSE)\s*:\s*%s\b" % re.escape(t)]
        if len(t) >= 3 and t not in AMBIGUOUS_SYMBOLS:   # the symbol in capitals as a word: "ITC", "M&M"
            alts.append(r"(?<![A-Za-z0-9&])%s(?![A-Za-z0-9&])" % re.escape(t))
        for n in short_names(name):
            # case-sensitive: company names are capitalised ("Eternal" is not "eternal")
            alts.append(r"\b%s\b" % re.escape(n).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def names_from_universe(watch, path="trader/universe.txt"):
    """Fill in missing company names from the scanner's SYMBOL|Name list, when it exists."""
    try:
        with open(path, encoding="utf-8") as fh:
            names = dict(line.strip().split("|", 1) for line in fh if "|" in line)
    except OSError:
        return watch
    return {t: n or names.get(t) for t, n in watch.items()}


def parse_market_feed(data, source):
    """A market-wide RSS feed -> items with no symbol yet (tag() finds which ones you hold)."""
    items = parse_rss(data, None)
    for it in items:
        it["source"] = source
        if source == "NSE announcement":
            it["kind"] = "announcement"
            it["company"] = it["title"]
            it["title"] = "%s: %s" % (it["title"], (it.get("summary") or "").strip())
    return items


def tag(items, watch):
    """Which of your tickers each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        if it.get("kind") == "announcement":   # NSE names the company; match the name only
            hay = it.get("company") or ""
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        it["important"] = bool(set(it["tags"]) & IMPORTANT_TAGS) or (it.get("kind") == "announcement"
                                                                      and bool(it["touches"]))
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, state, errors=None, sources=DEFAULT_SOURCES):
    """Google News per symbol, plus each market-wide feed once."""
    errors = [] if errors is None else errors
    items = []
    if "google" in sources:
        for t in sorted(watch):
            try:
                items += parse_rss(fetcher.get(GNEWS % news_query(t), "gnews-%s.xml" % t), t)
            except NewsError as exc:
                if not (fetcher.offline and "no file" in str(exc)):
                    errors.append("headlines for %s: %s" % (t, exc))
    for key in sources:
        if key not in MARKET_FEEDS:
            continue
        label, url = MARKET_FEEDS[key]
        try:
            items += parse_market_feed(fetcher.get(url, "feed-%s.xml" % key), label)
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("%s feed: %s" % (label, exc))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-10s Rs %s %+.2f%% vs previous close Rs %s%s  %s" % (
            m["ticker"], "{:,.2f}".format(m["price"]), m["pct"], "{:,.2f}".format(m["prev_close"]),
            when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-13s %s" % (mark, when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per ticker")
    ap.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                    help="comma separated: google (per symbol), et, pulse, mint, nse (default: all)")
    ap.add_argument("--universe", default="trader/universe.txt", help="SYMBOL|Name list for company names")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO time, for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        watch = {t.upper(): None for t in args.tickers}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not watch:
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    sources = [x.strip().lower() for x in args.sources.split(",") if x.strip()]
    bad = [x for x in sources if x != "google" and x not in MARKET_FEEDS]
    if bad:
        print("news: unknown source(s) %s; use google, %s" % (", ".join(bad), ", ".join(MARKET_FEEDS)),
              file=sys.stderr)
        return 2
    watch = names_from_universe(watch, args.universe)
    items = collect(fetcher, watch, state, errors=errors, sources=sources)
    # a market-wide item stays only if it names one of your symbols or companies
    items = [i for i in dedupe(tag(items, watch)) if i["ticker"] or i["touches"]]
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
