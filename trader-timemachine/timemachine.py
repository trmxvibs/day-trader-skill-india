#!/usr/bin/env python3
"""Time Machine for trader-timemachine (NSE India version). Standard library only.

Replays a planned day trade before you risk a rupee: 10,000 simulated runs of
the rest of the session from your entry, plus the same rule on every past
session in the data.

    python -B timemachine.py run                        # trader/plan.json, 10,000 replays, seed 19
    python -B timemachine.py run --write                # also save trader/timemachine.json
    python -B timemachine.py run --until 15:00          # stop each replay at your flat-by time
    python -B timemachine.py run --seed 7 --replays 20000 --json
    python -B timemachine.py run --symbol RELIANCE --date 2026-10-15 --entry 1186.10 \\
        --stop 1181.20 --target 1191.00 --entry-time "9:55 AM"
    python -B timemachine.py chart                      # ASCII fan chart of the replays
    python -B timemachine.py chart --from trader/timemachine.json

How the replays work (the original's sim.py algorithm, same random draws):
  1. Fetch 60 days of 5-minute bars: --offline if given, else your own Dhan
     Data API key if you have one (one call), else Yahoo's free chart
     automatically (one call too, with a warning: see SKILL.md "Data
     warning"). Keep only FULL sessions before the plan's date:
     every bar of continuous trading, 72 bars from 09:15 to 15:15 IST for Closing
     Auction Session stocks from 2026-08-03 (75 to 15:30 before that). Sessions
     with a missing bar, special sessions and today are left out.
  2. The pool: for every bar slot 1..n-1 of the day, each past session's bar as
     ratios to the previous bar's close: (open, high, low, close) / prev close.
  3. One replay starts at the entry price and walks the rest of the session.
     For each bar i it picks a slot j = i + randint(-window, window), kept in
     1..n-1, then one past bar from pool[j] (random.choice), and scales it to
     the current price. The stop is checked before the target in every bar,
     so a bar that touches both counts as the stop (the conservative call).
     Bars are drawn INDEPENDENTLY: momentum from one bar to the next (a
     breakout that keeps going) is not in the replays.
  4. Outcomes: "target" or "stop" (whichever was touched first), or "neither"
     when the trade is still open at --until (default: the plan's flat_by,
     15:00 from trader-exit, else the end of continuous trading).
     "green" = target first, or above the entry at --until (below for a sell).
     R = the target's R at the target, -1 at the stop, otherwise
     (price at --until - entry) / risk. A gap through the stop still counts
     -1R here; a real fill can be worse. Costs are not in these numbers.
  5. The price at --until: median and the 5th to 95th percentile of all
     replays (pct(a, q) = sorted a at index int(q * n)). The fan chart uses a
     second run of 2,000 paths with the same seed. The histogram has 24 bins
     between the 1st and 99th percentile.
  6. History: the plan's own rule (an opening-range breakout with the same
     opening range, stop rule and target R) re-run on every one of those past
     full sessions: the first bar after the range that closes beyond it, with
     its close time inside 09:30 to 14:30 IST (the entry window), is the entry;
     stop and target on the NSE tick grid; same stop-first rule.

The verdict line looks at the replays that hit one side (target or stop) and
compares the target's share of them, target / (target + stop), with the share
that breaks even at the plan's target R: 1 / (1 + R), so 50 % at 1R and 33 % at
2R. The gap, in percentage points:
  less than 5 points    "Close to a coin flip" (1R) / "Close to break-even" (other R)
  5 to 15 points        "Leans toward the target" / "Leans toward the stop"
  15 points or more     "Strongly toward the target" / "Strongly toward the stop"
Below it: a note when more than 40 % of the replays hit neither (the price at
--until decides those), and the average R when its sign goes against the lean.

5-minute bars only. The run refuses with a plain message when there are fewer
than 20 full sessions or the entry bar is not in the data. Same plan, same data
and same --seed give the same numbers on any machine. A simulation built from the
stock's own recent bars is not a prediction: results, news and circuit limits can
do what the last 60 days never did. Not financial advice.

This pack is open source: no data source is built in. --offline FILE reads a
saved chart JSON (.json or .json.gz) instead of the network. With no
--offline, live bars come from your own Dhan Data API key
(DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN; see tools/dhan_block.py) when you have set
one up, one call for the whole 60-day window, and otherwise fall back
automatically to Yahoo's free chart endpoint, one call too, so a user with no
broker account never hits a hard failure here either. This pack takes no
responsibility for any loss from using it; the stock market carries real
risk.
Times are IST (UTC+05:30) wall-clock; --now "2026-10-15 09:56" pins the clock.
"""

import argparse
import datetime
import gzip
import json
import math
import os
import random
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request

INTERVAL = 300       # 5-minute bars, in seconds
OPEN_MIN = 9 * 60 + 15
MIN_SESSIONS = 20
SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP = 0.05     # verdict thresholds, see the docstring
STRONG = 0.15
MANY_NEITHER = 0.40
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
ENTRY_WINDOW = ("09:30", "14:30")   # history replay: the trigger bar must CLOSE inside this window (IST)
SYMBOL_RE = r"^(\^[A-Z0-9]{1,15}|[A-Z0-9][A-Z0-9&\-]{0,19})(\.NS)?$"

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


class TMError(Exception):
    pass


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


# ---------------------------------------------------------------- time

def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TMError("--now wants a time like \"2026-10-15 09:56\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_clock(text, what):
    """'9:55 AM', '9:55am', '15:00' or '3:00' (a bare 1 to 8 o'clock means p.m.) -> minutes after midnight."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*(?:([AaPp])\.?\s*[Mm]\.?)?\s*$", str(text or ""))
    if not m:
        raise TMError("%s wants an IST time like 9:55 AM or 15:00, not %r" % (what, text))
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mi > 59 or h > 23 or (ap and not 1 <= h <= 12):
        raise TMError("%s %r is not a clock time" % (what, text))
    if ap == "p" and h != 12:
        h += 12
    elif ap == "a" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 8:
        h += 12
    return h * 60 + mi


def clock12(minutes):
    h, m = divmod(minutes, 60)
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")


def clock24(minutes):
    return "%02d:%02d" % divmod(minutes, 60)


# ---------------------------------------------------------------- data

def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS, M&M -> M&M.NS; ^NSEI and SYMBOL.NS are kept. Used only to
    label the chart-JSON shape below; the Dhan Data API itself is not Yahoo's symbology."""
    s = sym.strip().upper()
    return s if (s.startswith("^") or "." in s) else s + ".NS"


def nse_symbol(sym):
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def dhan_chart_text(sym):
    """60 days of 5-minute bars from the Dhan Data API, reshaped into the same chart-JSON
    text every --offline file uses (meta/timestamp/indicators.quote), so the rest of this
    module (chart_result, parse_bars, split_sessions, ...) never needs to know the bars
    came from Dhan rather than a saved file."""
    today = to_ist(utc_now()).date()
    try:
        ch = dhan_get_intraday(sym, "5m", today, extra_days_back=60)
    except DhanError as e:
        raise TMError(str(e))
    bars = ch["bars"]
    doc = {"chart": {"result": [{
        "meta": {"symbol": yahoo_symbol(sym), "dataGranularity": "5m"},
        "timestamp": [b["t"] for b in bars],
        "indicators": {"quote": [{
            "open": [b["o"] for b in bars], "high": [b["h"] for b in bars],
            "low": [b["l"] for b in bars], "close": [b["c"] for b in bars],
            "volume": [b["v"] for b in bars],
        }]},
    }], "error": None}}
    return json.dumps(doc)


YAHOO_HOSTS = ("query2", "query1")
YAHOO_CHART_URL = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=60d&interval=5m"


def fetch_yahoo_chart_text(sym, timeout=20):
    """The free, automatic tier-3 fallback (no Dhan key, no --offline): 60 days of 5-minute
    bars from Yahoo's own chart endpoint, read with a plain unauthenticated GET (no login,
    no scrape of a protected page). Returns the same chart-JSON text shape read_offline()
    and dhan_chart_text() already produce. Retries once on the alternate host for a 429 or
    a 5xx. See YAHOO_FALLBACK_WARNING and the SKILL.md "Data warning": a direct check found
    Yahoo's NSE bars can be wrong."""
    last = None
    for host in YAHOO_HOSTS:
        url = YAHOO_CHART_URL % (host, urllib.parse.quote(yahoo_symbol(sym)))
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
    raise TMError("could not reach Yahoo's free chart endpoint for %s: %s" % (sym, last))


def read_offline(path, sym):
    """A saved chart JSON file (.json or .json.gz), or a folder holding <SYM>-60d5m.json(.gz)."""
    if os.path.isdir(path):
        for name in ("%s-60d5m.json" % sym.upper(), "%s-60d5m.json.gz" % sym.upper()):
            if os.path.exists(os.path.join(path, name)):
                path = os.path.join(path, name)
                break
        else:
            raise TMError("no %s-60d5m.json(.gz) in %s" % (sym.upper(), path))
    if not os.path.exists(path):
        raise TMError("no offline chart file %s" % path)
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise TMError("could not read %s: %s" % (path, e))


def chart_result(text, sym):
    try:
        data = json.loads(text)
    except ValueError:
        raise TMError("the chart data for %s was not readable (not JSON)" % sym.upper())
    chart = data.get("chart") if isinstance(data, dict) else None
    if not isinstance(chart, dict):
        raise TMError("the chart data for %s was not readable (unexpected shape)" % sym.upper())
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise TMError("no data for %s: %s" % (sym.upper(), err.get("description") or "unknown ticker"))
    return res[0]


def parse_bars(result, now_utc):
    """A chart result (Dhan or --offline, same shape) -> (meta, bars, dropped). Regular-session 5-min bars only,
    oldest first. Dropped: bars with a missing price, bars off the 5-minute grid
    (the live tick, e.g. 10:49:45), bars not complete yet (now < start + 5 min,
    where now is the earlier of --now and the data's own regularMarketTime), and
    bars outside continuous trading (09:15 to 15:15 IST for Closing Auction Session
    securities from 2026-08-03, else 15:30). Prices are kept in whole paise."""
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    as_of = now_utc
    rmt = meta.get("regularMarketTime")
    if isinstance(rmt, (int, float)) and utc_of(rmt) < as_of:
        as_of = utc_of(rmt)
    dropped = {"no_price": 0, "off_grid": 0, "not_complete": 0, "outside_hours": 0}
    bars = {}
    for i, ts in enumerate(stamps):
        o, h, l, c, v = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            dropped["no_price"] += 1
            continue
        ts = int(ts)
        if ts % INTERVAL:
            dropped["off_grid"] += 1
            continue
        start = utc_of(ts)
        local = to_ist(start)
        mins = local.hour * 60 + local.minute - OPEN_MIN
        if mins < 0 or mins >= continuous_bars(local.date(), INTERVAL) * 5:
            dropped["outside_hours"] += 1
            continue
        if start + datetime.timedelta(seconds=INTERVAL) > as_of:
            dropped["not_complete"] += 1
            continue
        bars[ts] = {"date": local.date().isoformat(), "slot": mins // 5, "start": local.strftime("%H:%M"),
                    "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2), "v": v or 0}
    return meta, [bars[t] for t in sorted(bars)], dropped


def split_sessions(bars):
    """{date: {slot: bar}}."""
    days = {}
    for b in bars:
        days.setdefault(b["date"], {})[b["slot"]] = b
    return days


def full_sessions(days, before, n):
    """Full sessions (all n continuous bars, 72 with the Closing Auction Session) strictly
    before the plan's date, oldest first, and the ones left out (gaps, missing bars).
    A pre-CAS day has 75 bars; its first n are the same clock slots and are used."""
    full, short = [], []
    for d in sorted(days):
        if d >= before:
            continue
        s = days[d]
        if all(i in s for i in range(n)):
            full.append((d, [s[i] for i in range(n)]))
        else:
            short.append({"date": d, "bars": len(s)})
    return full, short


# ---------------------------------------------------------------- the simulation (sim.py)

def build_pool(sessions):
    """pool[i] = every past session's bar i as (o, h, l, c) / bar i-1's close, in date order."""
    n = len(sessions[0][1]) if sessions else 0
    pool = [[] for _ in range(n)]
    for _, D in sessions:
        for i in range(1, n):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pct(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def simulate(pool, entry, stop, target, k, last, replays=10000, seed=19, window=2, side="buy"):
    """Monte Carlo replays of bars k+1..last. Same draws and order as the reel's sim.py."""
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    r_target = round(((target - entry) if long else (entry - target)) / risk, 4)
    nslots = len(pool)

    def slot(rng, i):
        return i if window == 0 else min(nslots - 1, max(1, i + rng.randint(-window, window)))

    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths = [], [], []
    for n in range(replays):
        p = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            hi, lo, cl = p * h, p * l, p * c
            if outcome is None:
                if long:
                    if lo <= stop:
                        outcome = "stop"        # both inside one bar: count the loss (conservative)
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            p = cl
            if keep:
                path.append(round(p, 2))
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes)
                if o == "target" or (o == "close" and (p > entry if long else p < entry)))
    Rs = [r_target if o == "target" else -1.0 if o == "stop" else ((p - entry) if long else (entry - p)) / risk
          for o, p in zip(outcomes, closes)]
    srt = sorted(closes)
    # percentile bands per bar for the fan chart: a second run of 2,000 paths, same seed
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(BAND_PATHS):
        p = entry
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            p = p * c
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(last - k + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    return {
        "replays": replays, "seed": seed, "window": window, "bars_left": last - k,
        "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
        "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
        "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
        "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3),
        "r_at_target": r_target, "band_paths": BAND_PATHS, "bands": bands, "sample_paths": paths,
        "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins},
    }


# ---------------------------------------------------------------- stop rules and the history replay

def tick_of(rule, price):
    """The rule's tick, or the NSE price band of the price."""
    return rule.get("tick") or tick_for(price)


def opening_range(D, n):
    """(high, low) of the first n bars of a session given as {slot: bar} or a list."""
    rows = [D[i] for i in range(n)]
    return max(b["h"] for b in rows), min(b["l"] for b in rows)


def rule_stop(rule, oh, ol, entry, long):
    """The stop on the tick grid, rounded away from the entry."""
    r = rule["stop_rule"]
    tk = tick_of(rule, entry)
    if r == "or_mid":
        return away_from_entry((oh + ol) / 2, tk, long)
    if r in ("or_low", "or_high"):
        return away_from_entry(ol if long else oh, tk, long)
    frac = rule["stop_pct"] / 100.0
    return away_from_entry(entry * (1 - frac) if long else entry * (1 + frac), tk, long)


def in_window(close_min, window):
    """A trigger bar that CLOSES at close_min (minutes after midnight IST) is inside the
    entry window (no_entries_before, no_entries_after), both ends included."""
    a, b = (int(x[:2]) * 60 + int(x[3:]) for x in window)
    return a <= close_min <= b


def history_replay(sessions, rule, last, side="buy", window=ENTRY_WINDOW):
    """The plan's rule on every past full session: entry = the first bar after the
    opening range to close beyond it, with its close time inside the entry window
    (and before the --until bar), then stop first, then target, bar by bar, else the
    close at --until. Stop and target on the tick grid like trader-exit."""
    long = side == "buy"
    n = rule["or_minutes"] // 5
    trades = []
    for d, D in sessions:
        oh, ol = opening_range(D, n)
        kk = next((i for i in range(n, last) if in_window(OPEN_MIN + 5 * (i + 1), window)
                   and (D[i]["c"] > oh if long else D[i]["c"] < ol)), None)
        if kk is None:
            continue
        e = round(D[kk]["c"], 2)
        s = rule_stop(rule, oh, ol, e, long)
        risk = round((e - s) if long else (s - e), 2)
        if risk <= 0:
            continue
        tk = tick_of(rule, e)
        t = toward_entry(e + rule["target_r"] * risk, tk, True) if long else toward_entry(e - rule["target_r"] * risk,
                                                                                            tk, False)
        oc = None
        for b in D[kk + 1:last + 1]:
            if (b["l"] <= s) if long else (b["h"] >= s):
                oc = "stop"
                break
            if (b["h"] >= t) if long else (b["l"] <= t):
                oc = "target"
                break
        cl = D[last]["c"]
        R = ((t - e) if long else (e - t)) / risk if oc == "target" else -1.0 if oc == "stop" else \
            ((cl - e) if long else (e - cl)) / risk
        trades.append({"date": d, "time": clock12(OPEN_MIN + 5 * (kk + 1)), "entry": e, "stop": s, "target": t,
                       "outcome": oc or "close", "R": round(R, 2)})
    return {
        "sessions": len(sessions), "triggered": len(trades),
        "target": sum(1 for x in trades if x["outcome"] == "target"),
        "stop": sum(1 for x in trades if x["outcome"] == "stop"),
        "close": sum(1 for x in trades if x["outcome"] == "close"),
        "green": sum(1 for x in trades if x["R"] > 0),
        "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
        "rule": rule, "trades": trades,
    }


# ---------------------------------------------------------------- the plan

def _num(v, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise TMError("the plan needs a number for %s, got %r" % (what, v))
    if not x > 0 or math.isinf(x):
        raise TMError("the plan's %s must be a positive price, got %r" % (what, v))
    return x


def load_plan(a, now_utc):
    """The plan from --plan (trader/plan.json) or from the flags, checked and normalized."""
    if a.symbol:
        missing = [f for f in ("entry", "stop", "target", "entry_time") if getattr(a, f) is None]
        if missing:
            raise TMError("with --symbol also give %s" % ", ".join("--" + m.replace("_", "-") for m in missing))
        raw = {"symbol": a.symbol, "side": a.side, "date": a.date or to_ist(now_utc).date().isoformat(),
               "entry": a.entry, "stop": a.stop, "target": a.target, "entry_time": a.entry_time,
               "setup": a.setup, "opening_range": {"minutes": a.or_minutes}, "stop_rule": a.stop_rule,
               "interval": "5m"}
        src = "flags"
    else:
        if not os.path.exists(a.plan):
            raise TMError("no plan at %s: run /trader-exit first, or pass --symbol --date --entry --stop "
                          "--target --entry-time" % a.plan)
        try:
            with open(a.plan, encoding="utf-8") as fh:
                raw = json.load(fh)
        except ValueError as e:
            raise TMError("%s is not valid JSON: %s" % (a.plan, e))
        if not isinstance(raw, dict):
            raise TMError("%s is not a plan" % a.plan)
        src = a.plan
    return normalize_plan(raw, src)


def normalize_plan(raw, src="flags"):
    """A plan dict (trader/plan.json shape) -> the checked plan the time machine runs."""
    sym = nse_symbol(raw.get("symbol"))
    if not re.match(SYMBOL_RE, sym):
        raise TMError("the plan needs an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO), got %r" % raw.get("symbol"))
    side = str(raw.get("side") or "buy").lower()
    if side not in ("buy", "sell"):
        raise TMError("side must be buy or sell, got %r" % raw.get("side"))
    interval = str(raw.get("interval") or "5m")
    if interval != "5m":
        raise TMError("5-minute bars only (the plan says %s): this tool pools 60 days of 5-minute sessions "
                      "for the replay, not 1-minute ones" % interval)
    try:
        date = datetime.date.fromisoformat(str(raw.get("date") or "")).isoformat()
    except ValueError:
        raise TMError("the plan needs a date like 2026-10-15, got %r" % raw.get("date"))
    entry, stop, target = (_num(raw.get(k), k) for k in ("entry", "stop", "target"))
    long = side == "buy"
    if long and not stop < entry < target:
        raise TMError("a buy needs stop < entry < target (got stop %s, entry %s, target %s)" % (stop, entry, target))
    if not long and not target < entry < stop:
        raise TMError("a sell needs target < entry < stop (got target %s, entry %s, stop %s)" % (target, entry, stop))
    k = None
    if raw.get("entry_time"):
        close_min = parse_clock(raw["entry_time"], "entry_time")
        if (close_min - OPEN_MIN) % 5:
            raise TMError("entry_time %s is not on the 5-minute grid (use the bar's close, e.g. 9:55 AM)"
                          % raw["entry_time"])
        k = (close_min - OPEN_MIN) // 5 - 1
    if raw.get("entry_bar") is not None:
        try:
            kb = int(raw["entry_bar"])
        except (TypeError, ValueError):
            raise TMError("entry_bar must be a bar number, got %r" % raw["entry_bar"])
        if k is not None and kb != k:
            raise TMError("the plan disagrees with itself: entry_bar %d but entry_time %s is bar %d"
                          % (kb, raw["entry_time"], k))
        k = kb
    if k is None:
        raise TMError("the plan needs entry_time (the entry bar's close, e.g. 9:55 AM) or entry_bar")
    ok, why = trading_day(datetime.date.fromisoformat(date))
    if not ok:
        raise TMError("%s is not an NSE trading day (%s)" % (date, why))
    nb = continuous_bars(datetime.date.fromisoformat(date), INTERVAL)
    if not 0 <= k <= nb - 2:
        raise TMError("the entry must close between %s and %s to leave bars to replay (got %s)"
                      % (clock12(OPEN_MIN + 5), clock12(OPEN_MIN + 5 * (nb - 1)), clock12(OPEN_MIN + 5 * (k + 1))))
    orng = raw.get("opening_range") or {}
    or_min = orng.get("minutes", 15) if isinstance(orng, dict) else 15
    try:
        or_min = int(or_min)
    except (TypeError, ValueError):
        raise TMError("opening_range.minutes must be a number")
    if or_min < 5 or or_min % 5 or or_min >= 375:
        raise TMError("opening_range.minutes must be a multiple of 5 (got %s)" % or_min)
    risk = (entry - stop) if long else (stop - entry)
    return {
        "symbol": sym, "side": side, "date": date, "setup": str(raw.get("setup") or "orb"),
        "entry": entry, "entry_time": clock12(OPEN_MIN + 5 * (k + 1)), "entry_bar": k,
        "stop": stop, "target": target, "risk_per_share": round(risk, 2),
        "target_r": round(((target - entry) if long else (entry - target)) / risk, 2),
        "opening_range": dict(orng, minutes=or_min) if isinstance(orng, dict) else {"minutes": or_min},
        "stop_rule": str(raw.get("stop_rule") or "auto"), "interval": "5m",
        "flat_by": raw.get("flat_by"), "from": src, "tick": raw.get("tick"),
    }


def resolve_rule(plan, entry_day):
    """The rule the history replay uses: orb, the plan's opening range, its stop rule
    (or_mid, or_low / or_high, or 'pct' = the plan's stop distance as a percent of
    the entry), and the plan's target R. 'auto' works the stop rule out from the
    plan day's own opening range. None when the setup is not orb."""
    if plan["setup"].lower() != "orb":
        return None
    long = plan["side"] == "buy"
    n = plan["opening_range"]["minutes"] // 5
    rule = {"setup": "orb", "or_minutes": plan["opening_range"]["minutes"], "stop_rule": plan["stop_rule"],
            "target_r": plan["target_r"],
            "stop_pct": round(100.0 * plan["risk_per_share"] / plan["entry"], 4), "tick": plan.get("tick")}
    far = "or_low" if long else "or_high"
    if rule["stop_rule"] == "auto":
        rule["stop_rule"] = "pct"
        if all(i in entry_day for i in range(n)):
            oh, ol = opening_range(entry_day, n)
            if abs(rule_stop(dict(rule, stop_rule="or_mid"), oh, ol, plan["entry"], long) - plan["stop"]) < 0.005:
                rule["stop_rule"] = "or_mid"
            elif abs(rule_stop(dict(rule, stop_rule=far), oh, ol, plan["entry"], long) - plan["stop"]) < 0.005:
                rule["stop_rule"] = far
    elif rule["stop_rule"] in ("or_low", "or_high") and rule["stop_rule"] != far:
        rule["stop_rule"] = "pct"
    elif rule["stop_rule"] not in ("or_mid", far):
        rule["stop_rule"] = "pct"
    if rule["stop_rule"] != "pct":
        del rule["stop_pct"]
    return rule


# ---------------------------------------------------------------- the run

def _until12(mc):
    t = mc.get("until") or "15:15"
    return clock12(int(t[:2]) * 60 + int(t[3:]))


def lean(mc):
    """(gap, share, break_even): the target's share of the replays that hit one side,
    minus the share that breaks even at the target's R. None when nothing hit a side."""
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    share, even = pt / (pt + ps), 1.0 / (1.0 + r)
    return share - even, share, even


def verdict(mc):
    """One line from the numbers, see the docstring for the thresholds."""
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    if lv is None:
        return "No replay reached the target or the stop by %s: the price at %s decides every one." % (
            _until12(mc), _until12(mc))
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.005
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * pt, 100 * ps)
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR the target has to come first in %.0f %% of the replays that hit one side; "
            "it did in %.0f %% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def verdict_notes(mc):
    """The lines printed right below the verdict, so nothing reads as one-sided."""
    notes = []
    pn = mc.get("p_neither", 0)
    if pn > MANY_NEITHER and lean(mc) is not None:
        notes.append("%.1f %% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * pn, _until12(mc), _until12(mc)))
    lv = lean(mc)
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg is not None and avg != 0 and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the replays that hit neither pull it the other way." % avg)
    return notes


def time_machine(text, plan, now_utc, replays=10000, seed=19, window=2, until=None, source="dhan"):
    """Chart JSON text + a normalized plan -> the timemachine.json document."""
    if not 1 <= replays <= 1000000:
        raise TMError("--replays must be between 1 and 1,000,000")
    if not 0 <= window <= 10:
        raise TMError("--window must be between 0 and 10 bars")
    sym = plan["symbol"]
    result = chart_result(text, sym)
    meta, bars, dropped = parse_bars(result, now_utc)
    msym = nse_symbol(meta.get("symbol"))
    if msym and msym != sym:
        raise TMError("the data is for %s but the plan is for %s" % (msym, sym))
    gran = meta.get("dataGranularity")
    if gran and gran != "5m":
        raise TMError("the data has %s bars; the time machine needs 5-minute bars" % gran)
    if not bars:
        raise TMError("no complete 5-minute bars for %s in the data" % sym)
    k = plan["entry_bar"]
    pday = datetime.date.fromisoformat(plan["date"])
    nb = continuous_bars(pday, INTERVAL)
    end_min = OPEN_MIN + 5 * nb                      # end of continuous trading: 15:15 with CAS, else 15:30
    # default: the plan's flat-by (trader-exit writes it), else the end of continuous trading
    until = until or plan.get("flat_by")
    until_min = end_min if until is None else parse_clock(until, "--until")
    if (until_min - OPEN_MIN) % 5 or until_min > end_min:
        raise TMError("--until must be on the 5-minute grid and no later than %s (end of continuous trading)"
                      % clock12(end_min))
    last = (until_min - OPEN_MIN) // 5 - 1
    if last <= k:
        raise TMError("--until %s leaves nothing to replay after the %s entry" % (clock12(until_min),
                                                                             plan["entry_time"]))
    days = split_sessions(bars)
    entry_day = days.get(plan["date"], {})
    if k not in entry_day:
        reason = ("no bars for that day" if not entry_day else
                  "the data stops at the %s bar" % entry_day[max(entry_day)]["start"])
        raise TMError("the entry is not in the data: no complete %s bar (closing %s) on %s for %s (%s)"
                      % (clock24(OPEN_MIN + 5 * k), plan["entry_time"], plan["date"], sym, reason))
    eb = entry_day[k]
    warn = []
    if not eb["l"] - 0.01 <= plan["entry"] <= eb["h"] + 0.01:
        warn.append("the plan's entry %.2f is outside that bar's range %.2f to %.2f"
                    % (plan["entry"], eb["l"], eb["h"]))
    sessions, short = full_sessions(days, plan["date"], nb)
    if len(sessions) < MIN_SESSIONS:
        raise TMError("only %d full session(s) of %s before %s in the data; the time machine needs at least %d"
                      % (len(sessions), sym, plan["date"], MIN_SESSIONS))
    pool = build_pool(sessions)
    mc = simulate(pool, plan["entry"], plan["stop"], plan["target"], k, last, replays, seed, window, plan["side"])
    mc = dict(mc, until=clock24(until_min), pool_sessions=len(sessions), pool_from=sessions[0][0],
              pool_to=sessions[-1][0],
              band_times=[clock24(OPEN_MIN + 5 * (j + 1)) for j in range(k, last + 1)])
    rule = resolve_rule(plan, entry_day)
    plan_out = {kk: v for kk, v in plan.items() if v is not None}
    if rule:
        plan_out["stop_rule"] = rule["stop_rule"]
        history = history_replay(sessions, rule, last, plan["side"])
    else:
        history = {"sessions": len(sessions), "triggered": 0, "rule": None, "trades": [],
                   "note": "skipped: the history replay knows the opening-range breakout (orb) only, "
                           "this plan's setup is %r" % plan["setup"]}
    last_bar = bars[-1]
    return {
        "created_by": "trader-timemachine",
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "data": {"symbol": msym or sym, "interval": "5m", "range": "60d",
                 "last_bar": "%s %s IST" % (last_bar["date"], last_bar["start"]),
                 "dropped": dropped, "short_sessions_left_out": short},
        "plan": plan_out,
        "mc": mc,
        "history": history,
        "verdict": verdict(mc),
        "verdict_notes": verdict_notes(mc),
        "warnings": warn,
        "note": ("A simulation built from %s's own last %d full sessions. It is not a prediction: earnings, "
                 "news and halts can do things those sessions never did. Same seed, same numbers." % (sym, len(sessions))),
    }


# ---------------------------------------------------------------- output

def pc(x):
    return "%.1f %%" % (100.0 * x)


def report(doc):
    p, m, h = doc["plan"], doc["mc"], doc["history"]
    long = p["side"] == "buy"
    until = clock12(int(m["until"][:2]) * 60 + int(m["until"][3:]))
    wd = WEEKDAYS[datetime.date.fromisoformat(p["date"]).weekday()]
    r = p["target_r"]
    lines = [
        "%s %s, %s %s: entry %.2f at %s (bar %d), stop %.2f, target %.2f (%sR, risk %.2f a share)."
        % (p["symbol"], p["side"], wd, p["date"], p["entry"], p["entry_time"], p["entry_bar"], p["stop"],
           p["target"], ("%g" % r), p["risk_per_share"]),
        "%s replays of the rest of the session, %s to %s (%d five-minute bars), seed %d."
        % ("{:,}".format(m["replays"]), p["entry_time"], until, m["bars_left"], m["seed"]),
        "Each bar is drawn whole from %s's own 5-min bars at the same time of day (+-%d bars) over its last "
        "%d full sessions (%s to %s)." % (p["symbol"], m["window"], m["pool_sessions"], m["pool_from"], m["pool_to"]),
        "",
        "  target first   %s" % pc(m["p_target"]),
        "  stop first     %s" % pc(m["p_stop"]),
        "  neither        %-8s still open at %s" % (pc(m["p_neither"]), until),
        "  green          %-8s target first, or %s the entry at %s" % (pc(m["p_green"]),
                                                                       "above" if long else "below", until),
        "",
        "  Price at %s: median %.2f, 90 %% of replays between %.2f and %.2f."
        % (until, m["median_close"], m["p5_close"], m["p95_close"]),
        "  Average R: %+.3f (R = the %.2f risk: %sR at the target, -1R at the stop)."
        % (m["avg_R"], p["risk_per_share"], ("+%g" % m["r_at_target"]), ),
        "",
    ]
    if h.get("rule"):
        ru = h["rule"]
        stop_words = {"or_mid": "opening-range midpoint", "or_low": "opening-range low",
                      "or_high": "opening-range high"}.get(ru["stop_rule"],
                                                           "%.2f %% from the entry" % ru.get("stop_pct", 0))
        if h["triggered"]:
            lines.append("History: the same rule (%d-min opening range, stop at the %s, %gR target) on each of "
                         "those %d real sessions fired %d times: %d target, %d stop, %d neither. Average R %+.3f."
                         % (ru["or_minutes"], stop_words, ru["target_r"], h["sessions"], h["triggered"],
                            h["target"], h["stop"], h["close"], h["avg_R"]))
        else:
            lines.append("History: the same rule never fired in those %d sessions." % h["sessions"])
    else:
        lines.append("History: " + h.get("note", "skipped."))
    lines += ["", doc["verdict"]] + list(doc.get("verdict_notes") or []) + [""]
    for w in doc.get("warnings") or []:
        lines.append("Note: " + w + ".")
    if doc["data"]["short_sessions_left_out"]:
        lines.append("Left out %d short session(s) (half days or gaps): %s."
                     % (len(doc["data"]["short_sessions_left_out"]),
                        ", ".join(x["date"] for x in doc["data"]["short_sessions_left_out"])))
    lines.append(doc["note"] + " Repeat this run with --seed %d." % m["seed"])
    return lines


def render_chart(doc, height=17):
    """ASCII fan chart of the bands: one column per 5-min bar."""
    p, m = doc["plan"], doc["mc"]
    bands, times = m["bands"], m["band_times"]
    lo = min([b[0] for b in bands] + [p["stop"], p["target"]])
    hi = max([b[4] for b in bands] + [p["stop"], p["target"]])
    pad = (hi - lo) * 0.04 or 0.01
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / (height - 1)

    def row(price):
        return max(0, min(height - 1, int(round((hi - price) / step))))
    grid = [[" "] * len(bands) for _ in range(height)]
    for j, b in enumerate(bands):
        r5, r25, r50, r75, r95 = [row(x) for x in b]
        for r in range(r95, r5 + 1):
            grid[r][j] = "."
        for r in range(r75, r25 + 1):
            grid[r][j] = ":"
        grid[r50][j] = "*"
    marks = {row(p["target"]): "target", row(p["stop"]): "stop"}
    if row(p["entry"]) not in marks:
        marks[row(p["entry"])] = "entry"
    for r in (row(p["target"]), row(p["stop"])):
        grid[r] = ["-" if ch == " " else ch for ch in grid[r]]
    out = ["%s %s, %s replay paths from the %s entry (seed %d)" % (
        p["symbol"], p["date"], "{:,}".format(m.get("band_paths", BAND_PATHS)), p["entry_time"], m["seed"])]
    for r in range(height):
        label = marks.get(r)
        price = {"target": p["target"], "stop": p["stop"], "entry": p["entry"]}.get(label, hi - r * step)
        left = ("%9.2f |" % price) if (label or r in (0, height - 1)) else "          |"
        out.append(left + "".join(grid[r]) + ("  " + label if label else ""))
    axis = [" "] * len(bands)
    for j in sorted({0, len(bands) // 2, len(bands) - 1}):
        t = times[j]
        hh, mm = int(t[:2]), t[3:]
        lab = "%d:%s" % (hh % 12 or 12, mm)
        s = max(0, min(j, len(bands) - len(lab)))
        axis[s:s + len(lab)] = list(lab)
    out.append("          +" + "-" * len(bands))
    out.append("           " + "".join(axis))
    out.append("* median   : middle half of the paths   . 90 % of the paths   - stop and target")
    return out


# ---------------------------------------------------------------- commands

def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")


def compute(a):
    now = parse_now(a.now)
    plan = load_plan(a, now)
    if a.offline:
        text, source = read_offline(a.offline, plan["symbol"]), "offline"
    elif dhan_available():
        text, source = dhan_chart_text(plan["symbol"]), "dhan"
    else:
        print(YAHOO_FALLBACK_WARNING, file=sys.stderr)
        text, source = fetch_yahoo_chart_text(plan["symbol"]), "yahoo"
    return time_machine(text, plan, now, a.replays, a.seed, a.window, a.until, source)


def cmd_run(a):
    doc = compute(a)
    if a.write:
        _write(a.write, doc)
    if a.json:
        slim = dict(doc, mc={k: v for k, v in doc["mc"].items() if k != "sample_paths"})
        print(json.dumps(slim, indent=2))
    else:
        print("\n".join(report(doc)))
        if a.write:
            print("Wrote %s." % a.write)
        print("Next: /trader-budget sizes it to your budget and your max loss per trade.")
    return 0


def cmd_chart(a):
    if a.from_file:
        if not os.path.exists(a.from_file):
            raise TMError("no %s: run timemachine.py run --write first" % a.from_file)
        try:
            with open(a.from_file, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["mc"]["bands"]
        except (ValueError, KeyError, TypeError):
            raise TMError("%s is not a time machine result" % a.from_file)
    else:
        doc = compute(a)
    print("\n".join(render_chart(doc)))
    print("\n".join([doc.get("verdict", "")] + list(doc.get("verdict_notes") or [])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Time Machine: replay a planned day trade (trader-timemachine).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--plan", default="trader/plan.json", help="the plan from trader-exit")
        p.add_argument("--symbol", help="or give the plan as flags: ticker")
        p.add_argument("--side", default="buy", choices=["buy", "sell"])
        p.add_argument("--date", help="the plan's day, YYYY-MM-DD (default today, IST)")
        p.add_argument("--entry", type=float)
        p.add_argument("--stop", type=float)
        p.add_argument("--target", type=float)
        p.add_argument("--entry-time", help='the entry bar\'s close, e.g. "9:55 AM"')
        p.add_argument("--setup", default="orb")
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--stop-rule", default="auto", choices=["auto", "or_mid", "or_low", "or_high", "pct"])
        p.add_argument("--replays", type=int, default=10000)
        p.add_argument("--seed", type=int, default=19)
        p.add_argument("--window", type=int, default=2, help="draw from the same time of day +- this many bars")
        p.add_argument("--until", help="end each replay at this IST time (default: the plan's flat_by, else the end "
                                          "of continuous trading)")
        p.add_argument("--offline", metavar="FILE", help="a saved chart JSON (.json or .json.gz), else Dhan, else Yahoo automatically")
        p.add_argument("--now", help='pretend it is this time ("2026-10-15 09:56" IST, or ISO with Z)')

    p = sub.add_parser("run", help="replay the plan: 10,000 simulated sessions plus the history")
    common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", nargs="?", const="trader/timemachine.json", metavar="PATH",
                   help="save the result (default trader/timemachine.json)")
    p = sub.add_parser("chart", help="ASCII fan chart of the replays")
    common(p)
    p.add_argument("--from", dest="from_file", nargs="?", const="trader/timemachine.json", metavar="PATH",
                   help="draw a saved result instead of running again")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "chart": cmd_chart}[a.cmd](a)
    except TMError as e:
        print("timemachine.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
