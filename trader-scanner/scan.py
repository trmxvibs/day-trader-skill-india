#!/usr/bin/env python3
"""Chart Scanner for trader-scanner (NSE India version). Standard library only.

    python -B scan.py universe                        # NSE Nifty 100 list -> trader/universe.txt
    python -B scan.py universe --index nifty50
    python -B scan.py daily                           # before the open: daily setups -> trader/watchlist.json
    python -B scan.py daily --symbols RELIANCE,HDFCBANK,M&M    # or --watchlist FILE, or --limit 50
    python -B scan.py orb                             # after 9:35 AM IST: opening-range breakouts so far
    python -B scan.py orb --at 10:30 --or-minutes 15  # cut the clock at 10:30 AM IST
    python -B scan.py orb --offline wall5m.json.gz --now "2026-10-29 10:30"

universe  Downloads NSE's index constituent file (ind_nifty100list.csv, or
          ind_nifty50list.csv with --index nifty50) from nsearchives.nseindia.com
          (niftyindices.com as the fallback) and keeps every EQ series row with a
          plain NSE symbol (letters, digits, & and -: M&M, BAJAJ-AUTO). It is
          today's list, so a backtest over past months carries survivorship bias.

daily     Pre-market scan of daily CLOSES: --offline if given, else your own
          Dhan Data API key if you have one (one call a symbol, about 130
          days of 1-day bars), else Yahoo's free chart automatically (one
          call a symbol too, with a warning: see SKILL.md "Data warning").
          Liquidity: last price at least --min-price (Rs 20) and traded
          value (price x last session's volume) at least --min-value (Rs 25
          crore).
          Setups, from closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          Today's daily bar is dropped until continuous trading ends.

orb       Opening-range breakouts, 5-minute bars. The range is the first
          --or-minutes (15) of the session from 9:15 IST. One call a symbol
          (--offline, else Dhan, else Yahoo automatically) gets the whole
          session's bars with real highs and lows already, so every symbol
          is checked directly: range high = the highest high of the range
          bars, a hit = the first completed bar to CLOSE above it, at that
          bar's close time. A chart is usable only when every 5-minute bar
          from 9:15 to the cutoff is there. Floors: --min-price Rs 20,
          --min-value 0, --min-volume 0. --max-confirm is a leftover flag
          from the old two-stage Yahoo design and now does nothing (every
          bar already carries real highs and lows, so there is no second
          stage left to limit).

Bars: null closes are dropped, a bar off the 5-minute grid (the live tick) is
dropped, a bar counts only once it is complete, and only continuous trading
counts (09:15 to 15:15 IST for Closing Auction Session stocks from 2026-08-03,
else 15:30). Times are IST. --now pins the clock ("2026-10-29 10:30", IST; ...Z
for UTC).

This pack is open source: no data source is built in. --symbols/--watchlist/
--universe pick WHICH symbols to scan; HOW their bars are fetched tries, in
order: --offline files you made yourself; else your own Dhan Data API key
(DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN; one API call a symbol, since Dhan has no
batch endpoint, so a big universe takes a while and --pause between calls
matters); else Yahoo's free chart endpoint, automatically, one call a symbol
too, with no account and nothing to set up, so a user with no broker never
hits a hard failure here. Yahoo is the weakest of the three for accuracy (see
SKILL.md "Data warning") and a warning is printed every time it is used.
Partial results are reported as partial.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
This pack takes no responsibility for any loss from using it; the stock
market carries real risk. Not financial advice.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill-india/0.1; +https://github.com/Jakeschincariol/day-trader-skill)"
# NSE's own index constituent files (Nifty Indices, an NSE company); the same file is on both hosts
INDEX_FILES = {"nifty100": "ind_nifty100list.csv", "nifty50": "ind_nifty50list.csv"}
INDEX_URLS = ("https://nsearchives.nseindia.com/content/indices/", "https://www.niftyindices.com/IndexConstituent/")
PAUSE = 0.3         # seconds between requests: be polite, and the Dhan Data API has its own rate limit
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MAX_CONFIRM = 200   # stage-2 v8 charts per orb scan, most traded first
MIN_DAILY_BARS = 30
DEFAULT_UNIVERSE = "trader/universe.txt"
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."

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


SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


def ts_to_ist(ts):
    """Unix seconds -> naive IST. Never uses the machine's time zone."""
    return to_ist(utc_of(ts))


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return utc_now()
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ScanError('--now wants a time like "2026-10-29 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, flag="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScanError("%s wants an IST time like 10:15" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def clock_label(t):
    """datetime or time -> '10:15 AM'."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- network

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def to_yahoo(sym):
    """RELIANCE -> RELIANCE.NS, M&M -> M&M.NS; ^NSEI and SYMBOL.NS are kept."""
    s = sym.strip().upper()
    return s if (s.startswith("^") or "." in s) else s + ".NS"


def from_yahoo(ysym):
    s = str(ysym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def spark_path(symbols, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        ",".join(urllib.parse.quote(to_yahoo(s), safe="") for s in symbols), rng, interval)


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(to_yahoo(sym), safe=""), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    """A Yahoo chart result (v8 or one spark response) -> plain lists."""
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc, wanted=None):
    """Spark JSON -> {SYM: series}. Symbols Yahoo does not know are simply missing
    from the answer. `wanted` maps Yahoo symbols back to ours (M&M.NS -> M&M)."""
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise ScanError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or ""
        sym = (wanted or {}).get(ysym.upper(), from_yahoo(ysym))
        out[sym] = series_from_result(resp[0])
    if err and not out:
        raise ScanError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, sym=""):
    """v8 chart JSON -> series. Raises ScanError for Yahoo's 'no data' answers."""
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise ScanError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise ScanError("no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    return series_from_result(res[0])


def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _absorb(doc, name_hint, data, errors):
    """One parsed offline JSON doc -> data / errors."""
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline v8 chart without a symbol: name the file <TICKER>.json")
        sym = sym.upper().replace("-", ".")
        try:
            data[sym] = parse_chart(doc, sym)
        except ScanError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a v8 chart or a bundle" % name_hint)


def load_offline(path):
    """A folder of <T>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            if isinstance(doc, dict) and ("chart" in doc or "spark" in doc):
                _absorb(doc, "" if "spark" in doc else _stem(f), data, errors)
            else:
                _absorb(doc, "", data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


# ---------------------------------------------------------------- universe

TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")


def parse_index_csv(text):
    """An NSE index constituent file (Company Name,Industry,Symbol,Series,ISIN Code) ->
    rows. Raises ScanError if it is not that shape."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows or not {"Symbol", "Series", "Company Name"} <= set(rows[0]):
        raise ScanError("not an NSE index constituent file (want Company Name, Symbol, Series columns)")
    return rows


def build_universe(rows):
    """rows -> (kept [(SYM, name)], counts). EQ series and a plain NSE symbol only."""
    kept, counts = {}, {"rows": len(rows)}
    for r in rows:
        sym = (r.get("Symbol") or "").strip().upper()
        why = "keep"
        if (r.get("Series") or "").strip().upper() != "EQ":
            why = "not EQ series"
        elif not TICKER.match(sym):
            why = "not a plain NSE symbol"
        counts[why] = counts.get(why, 0) + 1
        if why == "keep":
            kept[sym] = " ".join((r.get("Company Name") or "").split())
    return sorted(kept.items()), counts


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes (the same math as the reel's refs/scan.py)."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_closes(series, now_utc):
    """[(date, close)] of COMPLETE daily bars: null closes dropped, today's bar dropped
    until the session closes, anything dated after the clock dropped."""
    now = to_ist(now_utc)
    today = now.date()
    s = session(today)
    today_done = s is None or now.time() >= s[1]
    out = {}
    for ts, c in zip(series["t"], series["c"]):
        if c is None:
            continue
        d = ts_to_ist(ts).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = float(c)  # one bar a day: a late duplicate (the live tick) wins
    return sorted(out.items())


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def scan_daily(data, symbols, now_utc, min_price, min_volume, min_value=0.0):
    """Pure. data = {SYM: series}. Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_closes(s, now_utc)
        meta = s.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None and bars:
            price = bars[-1][1]
        vol = meta.get("regularMarketVolume")
        value = price * vol if (price is not None and vol is not None) else None
        if price is None or price < min_price or (min_volume > 0 and (vol is None or vol < min_volume)) or \
                (min_value > 0 and (value is None or value < min_value)):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 2),
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1),
                         "volume": int(vol) if vol is not None else None})
    hits.sort(key=lambda h: (-(h["price"] * (h["volume"] or 0)), h["symbol"]))  # most rupees traded first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k):
    return datetime.datetime.combine(day, OPEN) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    """5-minute bars of `day` that are complete at `cutoff` (naive IST), on the grid,
    with a close, inside continuous trading (Closing Auction Session stamps are not bars).
    Sorted, one per slot. Prices in whole paise."""
    s = session(day)
    close_t = s[1] if s else CLOSE
    out = {}
    hs, ls = series.get("h"), series.get("l")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        et = ts_to_ist(ts)
        if et.date() != day or et.second or et.minute % BAR_MIN:
            continue  # another day, or the live tick off the grid
        if et.time() < OPEN or et.time() >= close_t:
            continue  # pre-open, the closing auction stamps, after hours
        if et + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue  # still in progress at the cutoff
        v = series.get("v")
        out[et] = {"start": et, "c": round(float(c), 2),
                   "h": None if not hs or hs[i] is None else round(float(hs[i]), 2),
                   "l": None if not ls or ls[i] is None else round(float(ls[i]), 2),
                   "v": (v[i] or 0) if v else 0}
    return [out[k] for k in sorted(out)]


def usable(bars, day, cutoff, n_range):
    """Every slot from 9:15 to the cutoff is there, and at least the range."""
    want = int((cutoff - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    s = session(day)
    if s:
        want = min(want, int((datetime.datetime.combine(day, s[1]) - slot(day, 0)).total_seconds()
                             // (BAR_MIN * 60)))
    if want < n_range or len(bars) != want:
        return False
    return all(b["start"] == slot(day, k) for k, b in enumerate(bars))


def orb_candidate(bars, n_range):
    """Stage 1, closes only: some completed bar after the range closed at or above
    the highest close of the range. Necessary for a breakout (range high >= any range close)."""
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def orb_hit(bars, n_range):
    """Stage 2, real highs and lows: the first completed bar to CLOSE above the range high."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round(b["c"], 2), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    """-> (day, cutoff naive IST) or raise ScanError with the plain message."""
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No session on %s (%s): the market is closed. Run orb on a trading day "
                        "after the opening range, or replay a past day with --offline." % (day_label(day), why))
    s = session(day)
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, s[1])
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s IST)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after continuous trading ends (%s) on %s." % (at.strftime("%H:%M"),
                                                                                     clock_label(close_dt),
                                                                       day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("Continuous trading ended at %s IST on %s. Replay the session with --at 10:15."
                            % (clock_label(close_dt), day_label(day)))
    if cutoff < first_close:
        raise ScanError("It is %s IST: the %d-minute opening range runs 9:15 to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                           clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s IST, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb tomorrow after %s, or replay "
                        "today with --at 10:15." % (clock_label(cutoff), until.strftime("%H:%M"),
                                                      clock_label(first_close)))
    return day, cutoff


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_volume=0, confirm=None,
             max_confirm=None, min_value=0.0):
    """stage1 = {SYM: series} with closes (spark or v8). confirm(sym) -> series with highs/lows
    (None = use the stage-1 series). With confirm and max_confirm, only the max_confirm most
    traded candidates (price x volume so far) are confirmed; the rest are counted as unconfirmed.
    Returns (hits, counts, failed {sym: why})."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        if bars[-1]["c"] < min_price or vol < min_volume or bars[-1]["c"] * vol < min_value:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n_range):
            cands.append((sym, bars, bars[-1]["c"] * vol))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, _ in cands:
        full = bars
        if confirm is not None:
            try:
                s2 = confirm(sym)
            except ScanError as e:
                failed[sym] = str(e)
                continue
            full = session_bars(s2, day, cutoff)
            if not usable(full, day, cutoff, n_range):
                failed[sym] = "the v8 chart has a missing bar"
                continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"]}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- fetching

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def dhan_intraday_series(sym, interval, day, scrip_master_path_=None):
    """dhan_get_intraday's bars -> this module's series shape (t/o/h/l/c/v columns).
    Every bar already carries real highs and lows, so a scan built on this never
    needs trader-scanner's old Yahoo stage-1/stage-2 split (closes first, confirm after)."""
    ch = dhan_get_intraday(sym, interval, day, scrip_master_path_=scrip_master_path_)
    bars = ch["bars"]
    return {"t": [b["t"] for b in bars], "c": [b["c"] for b in bars], "o": [b["o"] for b in bars],
            "h": [b["h"] for b in bars], "l": [b["l"] for b in bars], "v": [b["v"] for b in bars], "meta": {}}


def dhan_daily_series(sym, start, end, scrip_master_path_=None):
    """dhan_get_daily's rows -> this module's series shape. Each row's date becomes an
    epoch stamp at noon IST, well clear of either day's boundary, since daily_closes()
    only reads the IST calendar date back out of it."""
    rows = dhan_get_daily(sym, start, end, scrip_master_path_)
    t, o, h, l, c, v = [], [], [], [], [], []
    for r in rows:
        d = datetime.date.fromisoformat(r["d"])
        t.append(ts_of(ist_to_utc(datetime.datetime.combine(d, datetime.time(12, 0)))))
        o.append(r["o"]); h.append(r["h"]); l.append(r["l"]); c.append(r["c"]); v.append(r["v"])
    meta = {"regularMarketPrice": c[-1], "regularMarketVolume": v[-1]} if c else {}
    return {"t": t, "c": c, "o": o, "h": h, "l": l, "v": v, "meta": meta}


YAHOO_HOSTS = ("query2", "query1")


def fetch_yahoo_series(sym, rng, interval, timeout=20):
    """The free, automatic tier-3 fallback (no Dhan key, no --offline): one symbol's
    Yahoo v8 chart, read with a plain unauthenticated GET (no login, no scrape of a
    protected page), parsed into this module's series shape with parse_chart(). Retries
    once on the alternate host for a 429 or a 5xx. See YAHOO_FALLBACK_WARNING and the
    SKILL.md "Data warning": a direct check found Yahoo's NSE bars can be wrong."""
    last = None
    for host in YAHOO_HOSTS:
        url = "https://%s.finance.yahoo.com%s" % (host, chart_path(sym, rng, interval))
        req = urllib.request.Request(url, headers={"User-Agent": DHAN_UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
            return parse_chart(text, sym)
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
    raise ScanError("could not reach Yahoo's free chart endpoint for %s: %s" % (sym, last))


def fetch_yahoo(symbols, fetch_one, pause, a=None, label="yahoo"):
    """Like fetch_dhan, but for the automatic Yahoo fallback: one GET a symbol, paced the
    same way. The caller prints YAHOO_FALLBACK_WARNING once before the first call, not here,
    so it is said once a run, not once a symbol."""
    data, failed = {}, {}
    for i, sym in enumerate(symbols):
        if i and pause:
            SLEEP(pause)
        try:
            data[sym] = fetch_one(sym)
        except ScanError as e:
            failed[sym] = str(e)
        if a is not None and len(symbols) > 25 and (i + 1) % 50 == 0:
            progress(a, "  %s %d of %d symbols" % (label, i + 1, len(symbols)))
    return data, failed


def fetch_dhan(symbols, fetch_one, pause, a=None, label="dhan"):
    """One Dhan Data API call per symbol (there is no batch endpoint like Yahoo's spark),
    with a pause between calls. -> ({SYM: series}, {SYM: error})."""
    data, failed = {}, {}
    for i, sym in enumerate(symbols):
        if i and pause:
            SLEEP(pause)
        try:
            data[sym] = fetch_one(sym)
        except DhanError as e:
            failed[sym] = str(e)
        if a is not None and len(symbols) > 25 and (i + 1) % 50 == 0:
            progress(a, "  %s %d of %d symbols" % (label, i + 1, len(symbols)))
    return data, failed


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)  # offline: every chart in the file, unless a list was given
    else:
        path = a.universe or DEFAULT_UNIVERSE
        if not os.path.exists(path):
            raise ScanError("no %s yet: run `scan.py universe` first, or pass --symbols" % path)
        syms = symbols_from_file(path)
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln.split("|")[0].strip().upper())
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def money(x):
    return "Rs {:,.2f}".format(x)


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    t0 = time.time()
    fname = INDEX_FILES[a.index]
    source = None
    if a.offline:
        p = a.offline if os.path.isfile(a.offline) else os.path.join(a.offline, fname)
        if not os.path.exists(p):
            raise ScanError("no %s at %s" % (fname, a.offline))
        with open(p, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
        source = p
    else:
        text, last = None, None
        for k, base in enumerate(INDEX_URLS):
            if k and a.pause:
                SLEEP(a.pause)
            try:
                text = http_get(base + fname, timeout=60)
                source = base + fname
                break
            except urllib.error.HTTPError as e:
                last = "HTTP %d from %s" % (e.code, base.split("/")[2])
            except (urllib.error.URLError, OSError) as e:
                last = "could not reach %s: %s" % (base.split("/")[2], getattr(e, "reason", e))
        if text is None:
            raise ScanError("could not download %s (%s)" % (fname, last))
    kept, counts = build_universe(parse_index_csv(text))
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
    dropped = {k: v for k, v in counts.items() if k not in ("keep", "rows")}
    if a.json:
        print(json.dumps({"index": a.index, "count": len(kept), "rows": counts.get("rows", 0), "source": source,
                          "dropped": dropped, "out": None if a.no_write else a.out,
                          "seconds": round(time.time() - t0, 1)}, indent=2))
    else:
        print("NSE index file %s: %s rows (%s)." % (fname, counts.get("rows", 0), source))
        print("Kept %s %s constituents (EQ series)." % (len(kept), "Nifty 100" if a.index == "nifty100" else "Nifty 50"))
        if dropped:
            print("Dropped: " + ", ".join("%s %s" % (v, k) for k, v in sorted(dropped.items())) + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line). Today's list: past backtests on it carry some survivorship "
                  "bias." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    source = "offline"
    if not a.offline:
        today = to_ist(now).date()
        if dhan_available():
            source = "dhan"
            progress(a, "Fetching daily closes for %s symbols (one Dhan Data API call each)..."
                     % "{:,}".format(len(symbols)))
            start = today - datetime.timedelta(days=130)   # comfortably over 30 trading days
            data, failed = fetch_dhan(symbols, lambda s: dhan_daily_series(s, start, today), a.pause, a, "daily")
        else:
            source = "yahoo"
            print(YAHOO_FALLBACK_WARNING, file=sys.stderr)
            progress(a, "Fetching daily closes for %s symbols (free Yahoo data, one request each)..."
                     % "{:,}".format(len(symbols)))
            data, failed = fetch_yahoo(symbols, lambda s: fetch_yahoo_series(s, "1y", "1d"), a.pause, a, "daily")
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_volume, a.min_value)
    nfailed = len(symbols) - counts["fetched"]
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "daily",
           "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"], "failed": nfailed,
           "source": source,
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume, "min_value": a.min_value},
           "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    local = to_ist(now)
    print("Daily scan, %s IST, closes through %s (%s, daily closes only)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing",
        {"offline": "offline file", "dhan": "Dhan Data API", "yahoo": "free Yahoo data"}[source]))
    print("Fetched %s of %s charts%s. %s passed the liquidity filter (Rs %s+ price, Rs %s+ traded value%s)%s; "
          "%s scanned." % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
                           (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
                           "{:,}".format(counts["fetched"] - counts["illiquid"]), _n(a.min_price),
                           crore(a.min_value), (", %s+ shares" % "{:,}".format(a.min_volume)) if a.min_volume else "",
                           (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
                           "{:,}".format(counts["scanned"])))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (price x volume):")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-11s %13s  RSI %5.1f  %s" % (h["symbol"], money(h["price"]), h["rsi"],
                                              ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL.")
    return 0 if hits else 1


def crore(x):
    """Rs 25 crore for people (1 crore = 10,000,000)."""
    c = float(x) / 1e7
    return ("%g crore" % c) if c >= 1 else "{:,.0f}".format(float(x))


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def cmd_orb(a):
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or "14:30", "--until")
    data = failed = None
    explicit_day = None
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [ts_to_ist(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise ScanError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    source = "offline"
    if not a.offline:
        # One call a symbol, already with real highs and lows, so (unlike the old Yahoo
        # spark-then-v8 split) there is no separate confirm stage to run.
        if dhan_available():
            source = "dhan"
            progress(a, "Fetching 5-minute bars for %s symbols (one Dhan Data API call each)..."
                     % "{:,}".format(len(symbols)))
            data, failed = fetch_dhan(symbols, lambda s: dhan_intraday_series(s, "%dm" % BAR_MIN, day), a.pause, a, "orb")
        else:
            source = "yahoo"
            print(YAHOO_FALLBACK_WARNING, file=sys.stderr)
            progress(a, "Fetching 5-minute bars for %s symbols (free Yahoo data, one request each)..."
                     % "{:,}".format(len(symbols)))
            data, failed = fetch_yahoo(symbols, lambda s: fetch_yahoo_series(s, "5d", "5m"), a.pause, a, "orb")
    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_volume, None,
                                     a.max_confirm, a.min_value)
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    as_of = "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M"))
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "orb",
           "as_of": as_of, "universe": len(symbols), "scanned": counts["scanned"], "failed": nfailed,
           "unconfirmed": counts["unconfirmed"], "source": source,
           "or_minutes": a.or_minutes, "interval": "%dm" % BAR_MIN,
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume, "min_value": a.min_value},
           "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    print("Opening-range breakout scan, %s %s IST: %d-minute range (9:15 to %s), 5-minute bars, %s."
          % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(slot(day, a.or_minutes // BAR_MIN)),
             {"offline": "offline file", "dhan": "Dhan Data API", "yahoo": "free Yahoo data"}[source]))
    print("Fetched %s of %s charts%s; %s usable%s; %s candidates, %s confirmed with real highs and lows."
          % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
             (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
             "{:,}".format(counts["scanned"]),
             (" (%s under the price / volume floor)" % "{:,}".format(counts["illiquid"])) if counts["illiquid"] else "",
             "{:,}".format(counts["candidates"]), "{:,}".format(counts["confirmed"])))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded are; --max-confirm 0 "
              "checks all)." % ("{:,}".format(counts["unconfirmed"]), "{:,}".format(a.max_confirm)))
    if hits:
        print("%d of %s charts closed above their opening-range high by %s:"
              % (len(hits), ("the %s checked" % "{:,}".format(counts["confirmed"])) if counts["unconfirmed"]
                 else "{:,}".format(counts["scanned"]), clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %8s  %-11s %13s  range %s to %s  %+.2f%% over" % (
                h["at"], h["symbol"], money(h["price"]), _p(h["or_low"]), _p(h["or_high"]), h["pct_over"]))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL and /trader-entry SYMBOL.")
    return 0 if hits else 1


def _p(x):
    return "{:,.2f}".format(x)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (trader-scanner): daily setups and opening-range breakouts.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="download NSE's Nifty 100 (or Nifty 50) list -> trader/universe.txt")
    p.add_argument("--index", choices=sorted(INDEX_FILES), default="nifty100")
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--offline", metavar="PATH", help="a saved index CSV, or a folder holding it")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--pause", type=float, default=PAUSE)
    p.add_argument("--json", action="store_true")

    def common(p, min_price, min_volume, min_value):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of tickers")
        p.add_argument("--universe", help="symbol list, one SYMBOL|Name a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price)
        p.add_argument("--min-volume", type=int, default=min_volume)
        p.add_argument("--min-value", type=float, default=min_value,
                       help="traded value floor in rupees, price x volume (default %s)" % crore(min_value))
        p.add_argument("--offline", metavar="PATH", help="fixture folder or file instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-10-29 10:15" IST, or ...Z)')
        p.add_argument("--out", default="trader/watchlist.json")
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (%.1f)" % PAUSE)
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="pre-market setups from daily closes")
    common(p, 20.0, 0, 25e7)
    p = sub.add_parser("orb", help="opening-range breakouts so far today")
    common(p, 20.0, 0, 0.0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="v8 charts to fetch in stage 2, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this IST time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM IST (default: config no_entries_after, 14:30)")
    p.add_argument("--config", default="trader/config.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except ScanError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
