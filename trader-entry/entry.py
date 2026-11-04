#!/usr/bin/env python3
# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""Entry Timer for trader-entry (NSE India version): watches a setup bar by bar
and says the moment to get in. Standard library only. Long side only. It never
places an order.

    python -B entry.py check RELIANCE                         # opening-range breakout, 5-minute bars
    python -B entry.py check RELIANCE --now "2026-10-29 10:15" --offline RELIANCE.json
    python -B entry.py check HDFCBANK --setup level --level 690.50  # first close above a price
    python -B entry.py check "M&M" --interval 1m --min-volume-x 1.5 --write
    python -B entry.py watch RELIANCE --every 30 --until 14:30   # one line per closed bar
    python -B entry.py watchlist                                # every hit in trader/watchlist.json

Market: NSE cash market, IST (UTC+05:30, no daylight time). Continuous trading
09:15 to 15:30, and 09:15 to 15:15 for Closing Auction Session securities from
2026-08-03. The 09:00-09:15 pre-open auction is not part of the session.

Setups (longs only):
  orb    opening-range breakout. The range is the high and low of the first
         --or-minutes (default 15: the 9:15, 9:20 and 9:25 five-minute bars).
         It triggers on the first COMPLETED bar after the range that CLOSES
         above the range high. A wick above the high is not a trigger.
  level  the first completed bar that closes above --level.
  --min-volume-x X  the trigger bar also needs at least X times the average
         volume of the opening-range bars that report volume (the 09:15 bar
         can show 0 for NSE, since the pre-open auction volume is not in it,
         so that bar is left out of the average).

A trigger's time is the trigger bar's CLOSE time (the 10:10 bar closes at
10:15 AM) and its price is that close, in rupees on the exchange's tick grid.
Only bars on the interval grid count and a bar counts only once it has closed:
now >= its start + the interval.

Statuses: triggered; waiting (not open yet, range still forming, or no close
above the level yet); expired (no trigger by no_entries_after, default 14:30);
no-data (the market is closed today or the data source sent no bars).

trader/config.json (optional): "no_entries_before" (09:30) and
"no_entries_after" (14:30), IST. A trigger counts when the trigger bar's CLOSE
time is inside that window.

watch wakes just after each bar closes, re-polls every --every seconds (default
30) while the data source has not posted the new bar, prints one line per closed
bar and stops on a trigger (exit 0, writes trader/entry.json), on expiry, at the
close or at --until (exit 1). With --now or --offline it runs on a simulated
clock and replays the day without sleeping.

Data, tried in this order, so it never just fails:
  1. --offline PATH, if you pass it: a chart-JSON file you made yourself, from any
     broker's own chart (the standard Yahoo-style chart.result[0] shape every
     fixture here uses: {"chart":{"result":[{"meta":{...},"timestamp":[...],
     "indicators":{"quote":[{"open":[...],"high":[...],"low":[...],"close":[...],
     "volume":[...]}]}}],"error":null}}; see tools/build_nse_fixtures.py).
     PATH/<SYMBOL>.json also works when PATH is a folder. Always wins when given.
  2. The Dhan Data API, if you have set it up: your own Dhan trading account + the
     Data API add-on and an access token (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN, see
     tools/dhan_block.py).
  3. Otherwise, Yahoo's free chart endpoint, automatically, no setup needed - with
     a warning every time (see the "Data warning" in SKILL.md: a direct check
     found Yahoo's NSE bars can be wrong).
  Symbols: the NSE symbol (M&M, BAJAJ-AUTO, RELIANCE); ^NSEI for the Nifty 50.

Exit codes: check 0 = triggered, 1 = anything else or an error. watch 0 on a
trigger, 1 otherwise. watchlist 0 when at least one symbol triggered.
Not financial advice. Nothing is ever placed.
"""

import argparse
import datetime
import gzip
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill-india/0.1; +https://github.com/Jakeschincariol/day-trader-skill)"
PAUSE = 0.5      # seconds between requests, to be polite to any API
GRACE = 2        # seconds after a bar closes before watch asks for it
SETTLE = 60      # seconds a closed bar may wait for the data source to catch up
INTERVALS = {"1m": 60, "5m": 300}
DEFAULTS = {"no_entries_before": "09:30", "no_entries_after": "14:30"}

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


class EntryError(Exception):
    pass


# ---------------------------------------------------------------- time

def iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_now(text):
    """--now -> naive UTC. "2026-10-19 10:15" (or ...T10:15, or just 10:15 for
    today) is IST; an offset or Z makes it absolute."""
    if not text:
        return utc_now()
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_ist(utc_now()).date().isoformat(), t.zfill(5))
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise EntryError('--now wants an IST time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise EntryError("%s wants an IST time like 14:30, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def hm(t):
    """10:10, 09:45: the contract's wall-clock form."""
    return "%02d:%02d" % (t.hour, t.minute)


def short(t):
    """9:45, 10:10: for sentences."""
    return "%d:%02d" % (t.hour, t.minute)


def ampm(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def next_open(d):
    nd = d + datetime.timedelta(days=1)
    for _ in range(15):
        if session(nd):
            return "%s %s" % (nd.isoformat(), hm(OPEN))
        nd += datetime.timedelta(days=1)
    return None


# ---------------------------------------------------------------- data

def nse_symbol(sym):
    """The symbol people use: RELIANCE.NS -> RELIANCE (a leftover .NS from habit, or an
    old watchlist, is tolerated and stripped); ^NSEI stays."""
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def offline_file(path, sym):
    if os.path.isdir(path):
        s = nse_symbol(sym)
        for name in (s + ".json", s + ".json.gz", s + ".NS.json", s + ".NS.json.gz"):
            p = os.path.join(path, name)
            if os.path.exists(p):
                return p
        raise EntryError("no offline file for %s in %s (want %s.json)" % (sym, path, s))
    if not os.path.exists(path):
        raise EntryError("no offline file %s" % path)
    return path


def parse_chart(text, sym):
    """Parse a saved chart-JSON file for --offline (the pack's own chart-JSON shape,
    the same one tools/build_nse_fixtures.py writes) -> {symbol, meta, bars, prev_close,
    data_time}. Bars with a null close are dropped here; off-grid ticks later."""
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise EntryError("the chart file for %s was not readable" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise EntryError("no data in the chart file for %s: %s" % (sym, err.get("description") or "unknown ticker"))
    r = res[0] or {}
    meta = r.get("meta") or {}
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def at(key, i):
        arr = q.get(key) or []
        return arr[i] if i < len(arr) else None

    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = at("close", i)
        if t is None or c is None:
            continue
        o, h, lo = at("open", i), at("high", i), at("low", i)
        # every NSE tick is a multiple of Rs 0.01, so 2 decimals recover the exact
        # on-grid price from a chart feed's float noise (1161.0999755 -> 1161.10)
        bars.append({"t": int(t), "o": r2(c if o is None else o), "h": r2(c if h is None else h),
                     "l": r2(c if lo is None else lo), "c": r2(c), "v": int(at("volume", i) or 0)})
    return {"symbol": nse_symbol(meta.get("symbol") or sym), "meta": meta, "bars": bars,
            "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "data_time": meta.get("regularMarketTime")}


YAHOO_HOSTS = ("query2", "query1")
YAHOO_CHART_URL = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=%s"


def yahoo_ticker(sym):
    s = nse_symbol(sym)
    t = s if s.startswith("^") else s + ".NS"
    return urllib.parse.quote(t)


def fetch_yahoo_chart(sym, interval, timeout=20):
    """The free, automatic tier-3 fallback (no Dhan key, no --offline): Yahoo's own chart
    endpoint, read with a plain unauthenticated GET (no login, no scrape of a protected
    page). Returns the same JSON text shape parse_chart() already reads. Retries once on
    the alternate host for a 429 or a 5xx. See YAHOO_FALLBACK_WARNING and the SKILL.md
    "Data warning": a direct check found Yahoo's NSE bars can be wrong."""
    last = None
    for host in YAHOO_HOSTS:
        url = YAHOO_CHART_URL % (host, yahoo_ticker(sym), interval)
        req = urllib.request.Request(url, headers={"User-Agent": DHAN_UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
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
    raise EntryError("could not reach Yahoo's free chart endpoint for %s: %s" % (sym, last))


def get_chart(sym, interval, day=None, offline=None, today=None):
    """Bars for one ticker: --offline file if given, else the Dhan Data API if a key is
    set (DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN), else Yahoo's free chart endpoint automatically
    (prints YAHOO_FALLBACK_WARNING every time: see the SKILL.md "Data warning")."""
    sym = sym.upper()
    clean = nse_symbol(sym)
    if offline:
        f = offline_file(offline, sym)
        try:
            text = read_text(f)
        except (OSError, ValueError) as e:
            raise EntryError("could not read %s: %s" % (f, e))
        ch = parse_chart(text, clean)
        ch.setdefault("meta", {})
        ch["meta"].setdefault("source", "offline")
    elif dhan_available():
        today = today or to_ist(utc_now()).date()
        want_day = day or today
        try:
            ch = dhan_get_intraday(clean, interval, want_day)
        except DhanError as e:
            raise EntryError(str(e))
    else:
        print(YAHOO_FALLBACK_WARNING, file=sys.stderr)
        text = fetch_yahoo_chart(clean, interval)
        ch = parse_chart(text, clean)
        ch.setdefault("meta", {})
        ch["meta"].setdefault("source", "yahoo")
    if ch["symbol"] != clean:
        raise EntryError("the chart file holds %s, not %s" % (ch["symbol"], sym))
    gran = ch["meta"].get("dataGranularity")
    if gran and gran != interval:
        raise EntryError("%s has %s bars, not %s: use --interval %s" % (sym, gran, interval, gran))
    return ch


# ---------------------------------------------------------------- the rule

def read_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except ValueError as e:
            raise EntryError("%s is not valid JSON: %s" % (path, e))
        for k in DEFAULTS:
            if doc.get(k):
                cfg[k] = doc[k]
    for k in DEFAULTS:
        parse_hhmm(cfg[k], k)
    return cfg


def r2(x):
    return None if x is None else round(float(x), 2) + 0.0   # + 0.0 turns -0.0 into 0.0


def early_cutoff(day, t, close_dt):
    """The entry cutoff, never later than the end of continuous trading (NSE has no
    routine early closes; with the Closing Auction Session that end is 15:15)."""
    at = datetime.datetime.combine(day, t)
    return at if at < close_dt else close_dt


def evaluate(chart, now_utc, setup="orb", or_minutes=15, interval="5m", level=None,
             min_volume_x=None, cfg=None):
    """Pure function: the setup's status at now_utc from one session's bars."""
    cfg = cfg or dict(DEFAULTS)
    if interval not in INTERVALS:
        raise EntryError("--interval is 5m or 1m")
    step = INTERVALS[interval]
    if or_minutes <= 0 or (or_minutes * 60) % step:
        raise EntryError("--or-minutes must be a whole number of %s bars" % interval)
    if setup not in ("orb", "level"):
        raise EntryError("--setup is orb or level")
    if setup == "level" and level is None:
        raise EntryError("--setup level needs --level PRICE (for example a 20-day high)")
    n_or = or_minutes * 60 // step
    local = to_ist(now_utc)
    day = local.date()
    res = {"symbol": chart["symbol"], "setup": setup, "side": "buy", "date": day.isoformat(),
           "interval": interval, "or_minutes": or_minutes, "now": local.strftime("%Y-%m-%d %H:%M:%S"), "status": "waiting",
           "phase": "", "reason": "", "opening_range": None, "level": None, "trigger": None,
           "last": None, "forming": None, "min_volume_x": min_volume_x,
           "prev_close": chart.get("prev_close"), "calendar_known": day.year in CALENDAR_YEARS, "_bars": [],
           "source": (chart.get("meta") or {}).get("source", "dhan")}
    dt = chart.get("data_time")
    res["data_time"] = to_ist(utc_of(dt)).strftime("%Y-%m-%d %H:%M:%S") if dt else None
    ok, why = trading_day(day)
    if not ok:
        res.update(status="no-data", phase="closed", reason="the NSE is closed today (%s)" % why,
                   next_open=next_open(day))
        return res
    o, c = session(day)
    open_dt = datetime.datetime.combine(day, o)
    close_dt = datetime.datetime.combine(day, c)
    early = False   # NSE: no routine early closes
    before = datetime.datetime.combine(day, parse_hhmm(cfg["no_entries_before"], "no_entries_before"))
    cutoff = early_cutoff(day, parse_hhmm(cfg["no_entries_after"], "no_entries_after"), close_dt)
    or_end = open_dt + datetime.timedelta(minutes=or_minutes)
    res.update(opens=hm(o), closes=hm(c), early_close=None,
               no_entries_before=hm(before), no_entries_after=hm(cutoff))

    by_slot = {}
    for b in chart["bars"]:
        if b["t"] % step:
            continue                       # a live tick mid-bar (10:49:45) is not a closed bar
        et = to_ist(utc_of(b["t"]))
        if et.date() != day or not (open_dt <= et < close_dt):
            continue
        slot = int((et - open_dt).total_seconds()) // step
        by_slot[slot] = dict(b, et=et, end=et + datetime.timedelta(seconds=step), slot=slot)
    bars, pending = [], None
    now_ts = ts_of(now_utc)
    top = max(by_slot) if by_slot else -1
    for slot in sorted(by_slot):
        b = by_slot[slot]
        end_ts = b["t"] + step
        if end_ts <= now_ts:
            # Closed by the clock. On live data it counts once the source has caught up:
            # its last trade is at or after the close, a later bar exists, or a minute has passed.
            if dt is None or dt >= end_ts or slot < top or now_ts >= end_ts + SETTLE:
                bars.append(b)
            else:
                pending = b
                res["forming"] = {"bar_start": hm(b["et"]), "closes": ampm(b["end"]), "price": r2(b["c"])}
                res["lag_note"] = ("the data source's last trade is %s, before the %s bar's close: it counts once "
                                   "the source catches up (a minute at most)" % (
                                       to_ist(utc_of(dt)).strftime("%H:%M:%S"), short(b["et"])))
        elif b["et"] <= local:
            res["forming"] = {"bar_start": hm(b["et"]), "closes": ampm(b["end"]), "price": r2(b["c"])}
    res["_bars"] = bars
    res["bars_closed"] = len(bars)

    if local < open_dt:
        res.update(phase="pre-open", reason="the NSE opens at 9:15%s" % (
            " and the %d-minute range is set at %s" % (or_minutes, short(or_end)) if setup == "orb" else ""))
        return res

    or_bars = [b for b in bars if b["slot"] < n_or]
    # The 09:15 bar can carry 0 volume (the pre-open auction's volume is not in the
    # continuous-session feed), so the range's average volume uses only the range bars
    # that report volume.
    vol_bars = [b for b in or_bars if b["v"] > 0]
    avg_vol = (sum(b["v"] for b in vol_bars) / float(len(vol_bars))) if vol_bars else None
    if or_bars:
        res["opening_range"] = {"high": r2(max(b["h"] for b in or_bars)), "low": r2(min(b["l"] for b in or_bars)),
                                "minutes": or_minutes, "bars": len(or_bars), "of": n_or,
                                "complete": local >= or_end,
                                "avg_volume": int(round(avg_vol)) if avg_vol is not None else None}
    if setup == "orb":
        if local < or_end:
            res.update(phase="forming", reason="%d of %d range bars closed; the range is set at %s" % (
                len(or_bars), n_or, short(or_end)))
            return res
        if not or_bars:
            res.update(status="no-data", phase="open", reason="no bars for the opening range")
            return res
        lvl = max(b["h"] for b in or_bars)
    else:
        lvl = float(level)
    res["level"] = r2(lvl)

    first_slot = n_or if setup == "orb" else 0
    trig, thin = None, []
    for b in bars:
        if b["slot"] < first_slot or b["end"] < before or b["end"] > cutoff:
            continue
        if b["c"] > lvl:
            vx = (b["v"] / avg_vol) if avg_vol else None
            if min_volume_x and (vx is None or vx < min_volume_x):
                thin.append(b)
                continue
            trig = b
            break
    last = bars[-1] if bars else None
    if last:
        res["last"] = {"bar_start": hm(last["et"]), "close": r2(last["c"]),
                       "vs_level_pct": r2(100.0 * (last["c"] / lvl - 1))}

    if trig:
        vx = (trig["v"] / avg_vol) if avg_vol else None
        res.update(status="triggered", phase="open" if local < close_dt else "after-close",
                   reason="the %s bar closed above %s" % (short(trig["et"]), "the range high" if setup == "orb" else "the level"))
        res["trigger"] = {"time": ampm(trig["end"]), "close_time": hm(trig["end"]), "bar_start": hm(trig["et"]),
                          "bar_index": trig["slot"], "price": r2(trig["c"]), "close": trig["c"],
                          "volume": trig["v"], "volume_x": r2(vx), "pct_over": r2(100.0 * (trig["c"] / lvl - 1))}
        res["minutes_since"] = int((local - trig["end"]).total_seconds() // 60)
        if dt and utc_of(dt) < ist_to_utc(trig["end"]):
            res["data_note"] = ("the data source's last trade is %s, before this bar closed: its close can "
                                 "still move a cent or two" % to_ist(utc_of(dt)).strftime("%H:%M:%S"))
        return res
    if not bars:
        res.update(status="no-data", phase="open", reason="no bars yet for %s" % day.isoformat())
        return res
    what = "close above the range high Rs %.2f" % lvl if setup == "orb" else "close above Rs %.2f" % lvl
    if local >= cutoff and not (pending and pending["end"] <= cutoff):
        res.update(status="expired", phase="after-close" if local >= close_dt else "after-cutoff",
                   reason="no %s by %s, the last time for a new entry%s" % (
                       what, short(cutoff), ""))
        return res
    res["phase"] = "open"
    if last["c"] > lvl and last in thin:
        res["reason"] = "the %s bar closed above at Rs %.2f but on %.2fx the range's volume, under your %.2fx" % (
            short(last["et"]), last["c"], last["v"] / avg_vol if avg_vol else 0, min_volume_x)
    elif last["c"] > lvl and last["end"] < before:
        res["reason"] = "the %s bar closed above at Rs %.2f, but no entries before %s (config)" % (
            short(last["et"]), last["c"], short(before))
    else:
        gap = lvl - last["c"]
        res["reason"] = "last close Rs %.2f (%s bar), Rs %.2f %s %s (%+.2f%%)" % (
            last["c"], short(last["et"]), abs(gap), "under" if gap >= 0 else "over",
            "the range high Rs %.2f" % lvl if setup == "orb" else "the level Rs %.2f" % lvl,
            100.0 * (last["c"] / lvl - 1))
    return res


def public(res):
    """The result without the internal bar list, ready for JSON."""
    return {k: v for k, v in res.items() if not k.startswith("_")}


def entry_doc(res, now_utc):
    """trader/entry.json, the contract shape (plus a plain-words reason)."""
    t = res.get("trigger") or {}
    orr = res.get("opening_range")
    return {"created_by": "trader-entry", "symbol": res["symbol"], "setup": res["setup"], "side": "buy",
            "date": res["date"], "status": res["status"], "time": t.get("time"), "bar_start": t.get("bar_start"),
            "bar_index": t.get("bar_index"), "price": t.get("price"), "level": res.get("level"),
            "interval": res["interval"],
            "opening_range": {"high": orr["high"], "low": orr["low"], "minutes": orr["minutes"]} if orr else None,
            "source": res.get("source", "dhan"), "checked_at": iso(now_utc), "reason": res.get("reason")}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------- words

LABEL = {"triggered": "TRIGGERED", "waiting": "WAITING", "expired": "EXPIRED", "no-data": "NO DATA"}
PHASE_LABEL = {"pre-open": "NOT OPEN YET", "forming": "RANGE FORMING", "closed": "MARKET CLOSED"}
SHORT = {"pre-open": "not open", "forming": "forming", "closed": "closed"}
SOURCE_LABEL = {"dhan": "Dhan Data API", "offline": "offline file", "yahoo": "free Yahoo data"}


def setup_name(res):
    if res["setup"] == "orb":
        return "opening-range breakout (%d-minute range)" % res["or_minutes"]
    return "close above Rs %.2f" % res["level"] if res["level"] is not None else "close above a level"


def describe(res):
    """Plain lines for a person."""
    when = datetime.datetime.strptime(res["now"], "%Y-%m-%d %H:%M:%S")
    bars = "5-minute" if res["interval"] == "5m" else "1-minute"
    source = SOURCE_LABEL.get(res.get("source"), res.get("source") or "unknown source")
    head = "%s  %s %s IST  %s on %s bars (%s)" % (
        res["symbol"], when.strftime("%a %Y-%m-%d"), when.strftime("%H:%M"), setup_name(res), bars, source)
    if res.get("data_time") and res["data_time"] <= res["now"] and res["data_time"][:10] == res["date"]:
        head += ", data to %s" % res["data_time"][11:]
    out = [head]
    orr = res.get("opening_range")
    if orr:
        start = datetime.datetime.strptime(res["opens"], "%H:%M")
        end = start + datetime.timedelta(minutes=orr["minutes"])
        out.append("Opening range %s-%s: high Rs %.2f, low Rs %.2f (%d of %d bars%s)." % (
            short(start), short(end), orr["high"], orr["low"], orr["bars"], orr["of"],
            "" if orr["complete"] else ", still forming"))
    label = PHASE_LABEL.get(res["phase"]) or LABEL.get(res["status"], res["status"].upper())
    t = res.get("trigger")
    if t:
        out.append("TRIGGERED %s at Rs %.2f: the %s bar (bar %d) closed above %s Rs %.2f (%+.2f%%%s)." % (
            t["time"], t["price"], short(datetime.datetime.strptime(t["bar_start"], "%H:%M")), t["bar_index"],
            "the range high" if res["setup"] == "orb" else "the level", res["level"], t["pct_over"],
            (", %.2fx the range's volume" % t["volume_x"]) if res.get("min_volume_x") and t.get("volume_x") else ""))
        step = INTERVALS[res["interval"]] // 60
        if res.get("minutes_since", 0) > step and res.get("last"):
            out.append("That was %d minutes ago; the last close is Rs %.2f (%s bar). A late entry is a different "
                       "trade: the price, the stop distance and the odds have moved." % (
                           res["minutes_since"], res["last"]["close"], res["last"]["bar_start"]))
        if res.get("data_note"):
            out.append(res["data_note"] + ".")
        if res.get("minutes_since", 0) > step:
            out.append("Nothing was placed.")
        else:
            out.append("This is the moment to get in, if you take the trade. Nothing was placed.")
        out.append("Next: set the stop and target BEFORE you buy: /trader-exit "
                   "(exit.py plan --from trader/entry.json).")
    else:
        out.append("%s  %s." % (label, res["reason"]))
        f = res.get("forming")
        if res.get("lag_note") and res["status"] == "waiting":
            out.append(res["lag_note"] + ".")
        elif f and res["status"] == "waiting" and res["phase"] in ("open", "forming"):
            out.append("The %s bar is still forming (Rs %.2f so far, closes %s); it does not count until it closes." % (
                f["bar_start"].lstrip("0"), f["price"], f["closes"]))
        if res["status"] == "expired":
            out.append("No entry on this setup today. Nothing was placed.")
        if res.get("next_open"):
            out.append("Next open: %s IST." % res["next_open"])
    if not res.get("calendar_known"):
        out.append("Holiday list covers %s only: check nseindia.com for this date." % " and ".join(map(str, CALENDAR_YEARS)))
    return out


def bar_line(b, res):
    """One watch line for a bar that just closed."""
    start = short(b["et"])
    orr = res.get("opening_range") or {}
    lvl = res.get("level")
    t = res.get("trigger")
    n_or = orr.get("of") or 0
    if res["setup"] == "orb" and b["slot"] < n_or:
        seen = [x for x in res["_bars"] if x["slot"] <= b["slot"]]
        note = "range so far Rs %.2f to Rs %.2f" % (min(x["l"] for x in seen), max(x["h"] for x in seen))
        if b["slot"] == n_or - 1:
            note = "opening range set: high Rs %.2f, low Rs %.2f" % (orr["high"], orr["low"])
    elif t and t["bar_index"] == b["slot"]:
        note = "TRIGGERED: closed above Rs %.2f (%+.2f%%). Get in now if you take it: %s, Rs %.2f" % (
            lvl, t["pct_over"], t["time"], t["price"])
    elif lvl is None:
        note = ""
    elif b["c"] > lvl:
        note = "above Rs %.2f" % lvl
        if res["setup"] == "level" or b["slot"] >= n_or:
            avg = orr.get("avg_volume")
            if res.get("min_volume_x") and avg:
                note += ", but volume %.2fx is under %.2fx" % (b["v"] / float(avg), res["min_volume_x"])
            elif b["end"] < datetime.datetime.combine(b["et"].date(), parse_hhmm(res["no_entries_before"], "x")):
                note += ", but no entries before %s" % res["no_entries_before"]
    else:
        note = "Rs %.2f under Rs %.2f" % (lvl - b["c"], lvl)
    return "%5s bar  closed Rs %.2f  %s" % (start, b["c"], note)


# ---------------------------------------------------------------- watch

class SimClock(object):
    """A clock that moves only when it sleeps: replays a day without waiting."""

    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += datetime.timedelta(seconds=max(1, int(round(seconds))))


def _sleep(seconds):
    time.sleep(seconds)


def watch_loop(provider, clock, sleep, emit, opts, every=30, until=None, max_errors=5, on_trigger=None):
    """Poll bar by bar until a trigger (0), expiry, the close or --until (1).
    provider(now_utc) -> chart, clock() -> naive UTC, sleep(seconds), emit(line).
    It sleeps in steps of at most `every` seconds and re-reads the clock each time,
    so a computer that dozed off catches up as soon as it wakes."""
    step = INTERVALS[opts.get("interval", "5m")]
    printed, errors, said_preopen, first = -1, 0, False, True
    next_fetch = None
    while True:
        now = clock()
        if next_fetch is not None and now < next_fetch:
            sleep(max(1, min(every, (next_fetch - now).total_seconds())))
            continue
        local = to_ist(now)
        day = local.date()
        s = session(day)
        if not s:
            emit("%s: market closed today (%s). Nothing to watch." % (day.isoformat(), trading_day(day)[1]))
            return 1, None
        open_dt = datetime.datetime.combine(day, s[0])
        close_dt = datetime.datetime.combine(day, s[1])
        if local < open_dt + datetime.timedelta(seconds=step):
            if not said_preopen:
                emit("%s IST  %s: watching from the first bar's close at %s." % (
                    local.strftime("%H:%M"), "not open yet" if local < open_dt else "open, no bar closed yet",
                    short(open_dt + datetime.timedelta(seconds=step))))
                said_preopen = True
            next_fetch = ist_to_utc(open_dt) + datetime.timedelta(seconds=step + GRACE)
            continue
        try:
            chart = provider(now)
            errors = 0
        except EntryError as e:
            errors += 1
            emit("%s IST  could not read bars: %s" % (local.strftime("%H:%M:%S"), e))
            if errors >= max_errors:
                emit("Stopping after %d failed reads in a row." % errors)
                return 1, None
            next_fetch = now + datetime.timedelta(seconds=every)
            continue
        res = evaluate(chart, now, **opts)
        bars = res["_bars"]
        lines = describe(res)
        fresh = first
        if first:
            emit(lines[0])
            if len(lines) > 1 and lines[1].startswith("Opening range"):
                emit(lines[1])
            if bars and not res.get("trigger"):
                emit(bar_line(bars[-1], res))
            printed = bars[-1]["slot"] if bars else -1
            first = False
        else:
            for b in bars:
                if b["slot"] > printed:
                    emit(bar_line(b, res))
                    printed = b["slot"]
        if res["status"] == "triggered":
            for line in lines[1:]:
                if line.startswith("Opening range") or (line.startswith("TRIGGERED") and not fresh):
                    continue
                emit(line)
            if on_trigger:
                on_trigger(res, now)
            return 0, res
        if res["status"] == "expired" or local >= close_dt:
            emit("EXPIRED  %s." % (res["reason"] or "the market closed without a trigger"))
            return 1, res
        if until and local >= until:
            emit("Stopped at --until %s without a trigger. %s." % (short(until), res["reason"]))
            return 1, res
        # sleep until just after the next bar closes; poll every `every` seconds if the source is behind
        cur_slot = int((local - open_dt).total_seconds()) // step
        expected_last = cur_slot - 1
        behind = (not bars) or bars[-1]["slot"] < expected_last
        if behind:
            wait = every
        else:
            nxt = open_dt + datetime.timedelta(seconds=(cur_slot + 1) * step + GRACE)
            wait = (nxt - local).total_seconds()
        cutoff = datetime.datetime.combine(day, parse_hhmm(res.get("no_entries_after") or DEFAULTS["no_entries_after"], "cutoff"))
        for stop_at in (until, cutoff, close_dt):
            if stop_at and stop_at > local:
                wait = min(wait, (stop_at - local).total_seconds())
        next_fetch = now + datetime.timedelta(seconds=max(1, wait))


# ---------------------------------------------------------------- commands

def _opts(a, cfg):
    if a.side in ("sell", "short"):
        raise EntryError("v2 plans longs only: no short entries. Watch for a buy (--side buy) instead.")
    if a.setup == "orb" and a.level is not None:
        raise EntryError("--level is for --setup level (an orb's level is its range high)")
    if a.min_volume_x is not None and a.min_volume_x <= 0:
        raise EntryError("--min-volume-x must be above 0")
    if a.level is not None and a.level <= 0:
        raise EntryError("--level must be a price above 0")
    return {"setup": a.setup, "or_minutes": a.or_minutes, "interval": a.interval, "level": a.level,
            "min_volume_x": a.min_volume_x, "cfg": cfg}


def _load(a, sym, now):
    """(chart, now). An offline file without --now is read as of its own time."""
    real_today = to_ist(utc_now()).date()
    if a.offline and now is None:
        ch = get_chart(sym, a.interval, offline=a.offline)
        return ch, (utc_of(ch["data_time"]) if ch.get("data_time") else utc_now())
    now = now or utc_now()
    return get_chart(sym, a.interval, to_ist(now).date(), a.offline, real_today), now


def cmd_check(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    sym = a.symbol.upper()
    chart, now = _load(a, sym, parse_now(a.now) if a.now else None)
    res = evaluate(chart, now, **opts)
    if a.write:
        write_json(a.write, entry_doc(res, now))
    if a.json:
        print(json.dumps(public(res), indent=2))
    else:
        for line in describe(res):
            print(line)
        if a.write:
            print("Wrote %s (status %s)." % (a.write, res["status"]))
    return 0 if res["status"] == "triggered" else 1


def cmd_watch(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    sym = a.symbol.upper()
    if a.every <= 0:
        raise EntryError("--every must be above 0 seconds")
    start = parse_now(a.now) if a.now else None
    if a.offline or start is not None:
        if start is None:
            start = _load(a, sym, None)[1]
        clock = SimClock(start)
        now_fn, sleep_fn = clock.now, clock.sleep
    else:
        now_fn, sleep_fn = utc_now, _sleep
    real_today = to_ist(utc_now()).date()

    def provider(now):
        return get_chart(sym, a.interval, to_ist(now).date(), a.offline, real_today)

    until = None
    if a.until:
        t = parse_hhmm(a.until, "--until")
        until = datetime.datetime.combine(to_ist(now_fn()).date(), t)

    def on_trigger(res, now):
        write_json(a.out, entry_doc(res, now))
        print("Wrote %s." % a.out, flush=True)

    def emit(line):
        print(line, flush=True)

    code, _ = watch_loop(provider, now_fn, sleep_fn, emit, opts, every=a.every, until=until,
                         on_trigger=on_trigger)
    return code


def cmd_watchlist(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    if not os.path.exists(a.watchlist):
        raise EntryError("no watchlist at %s: run /trader-scanner first (it writes the hits there)" % a.watchlist)
    try:
        with open(a.watchlist, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise EntryError("%s is not valid JSON: %s" % (a.watchlist, e))
    hits = [h for h in (doc.get("hits") or []) if isinstance(h, dict) and h.get("symbol")]
    if not hits:
        raise EntryError("no hits in %s: the scanner found nothing to watch" % a.watchlist)
    if a.offline and not os.path.isdir(a.offline):
        raise EntryError("watchlist --offline wants a folder of <TICKER>.json files")
    now = parse_now(a.now) if a.now else None
    rows = []
    for i, h in enumerate(hits):
        sym = str(h["symbol"]).upper()
        o = dict(opts)
        if a.setup == "level" and o["level"] is None:
            o["level"] = h.get("level")
        try:
            if a.setup == "level" and o["level"] is None:
                raise EntryError("needs a level: --level PRICE or a \"level\" in the watchlist")
            if i and not a.offline:
                _sleep(PAUSE)
            chart, when = _load(a, sym, now)
            rows.append((sym, evaluate(chart, when, **o), None))
        except EntryError as e:
            rows.append((sym, None, str(e)))
    if a.json:
        print(json.dumps([{"symbol": s, "result": public(r) if r else None, "error": e} for s, r, e in rows], indent=2))
    else:
        print("%-10s  %-9s  %-8s  %-10s  %-10s  %-10s  %s" % ("SYMBOL", "STATUS", "TIME", "PRICE", "LEVEL", "LAST",
                                                                "VS LEVEL"))
        for s, r, e in rows:
            if e:
                print("%-10s  error: %s" % (s, e))
                continue
            t = r.get("trigger") or {}
            last = r.get("last") or {}
            status = SHORT.get(r["phase"], r["status"]) if r["status"] != "triggered" else "triggered"
            print("%-10s  %-9s  %-8s  %-10s  %-10s  %-10s  %s" % (
                s, status[:9], t.get("time") or "-", ("Rs %.2f" % t["price"]) if t else "-",
                ("Rs %.2f" % r["level"]) if r.get("level") is not None else "-",
                ("Rs %.2f" % last["close"]) if last else "-",
                ("%+.2f%%" % last["vs_level_pct"]) if last.get("vs_level_pct") is not None else "-"))
        n = sum(1 for _, r, _ in rows if r and r["status"] == "triggered")
        print("%d of %d triggered. Pick one, then: entry.py check <TICKER> --write, and /trader-exit." % (n, len(rows)))
        print("Bars from your own data source (Dhan Data API, or --offline). Nothing was placed.")
    return 0 if any(r and r["status"] == "triggered" for _, r, _ in rows) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Entry Timer (trader-entry): watches a setup bar by bar.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--setup", choices=["orb", "level"], default="orb")
        p.add_argument("--or-minutes", type=int, default=15, help="opening range length (default 15)")
        p.add_argument("--interval", choices=["5m", "1m"], default="5m")
        p.add_argument("--level", type=float, help="the price to close above (--setup level)")
        p.add_argument("--min-volume-x", type=float, help="trigger bar volume vs the range bars' average")
        p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="v2: buy only")
        p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-10-19 10:15"')
        p.add_argument("--offline", metavar="PATH", help="your own saved bars file (.json/.json.gz) or a folder of them")
        p.add_argument("--config", default="trader/config.json")

    p = sub.add_parser("check", help="the setup's status right now")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--write", nargs="?", const="trader/entry.json", metavar="PATH",
                   help="save the result (default trader/entry.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("watch", help="poll bar by bar until it triggers or expires")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--every", type=float, default=30, help="seconds between polls while the source is behind")
    p.add_argument("--until", help="stop at this IST time (HH:MM)")
    p.add_argument("--out", default="trader/entry.json", help="where a trigger is written")
    p = sub.add_parser("watchlist", help="check every hit in trader/watchlist.json")
    common(p)
    p.add_argument("--watchlist", default="trader/watchlist.json")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "watch": cmd_watch, "watchlist": cmd_watchlist}[a.cmd](a)
    except EntryError as e:
        print("entry.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("entry.py: stopped.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
