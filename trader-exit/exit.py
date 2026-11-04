#!/usr/bin/env python3
"""Exit Planner for trader-exit (NSE India version): sets the stop and the target
BEFORE you enter, so you know when to get out. Standard library only. Long side
only. It never places an order.

    python -B exit.py plan                                  # from trader/entry.json: or-mid stop, 1R target
    python -B exit.py plan --stop or-mid --target 1R --write
    python -B exit.py plan --symbol RELIANCE --entry 1186.10 --or-high 1185.00 --or-low 1177.40
    python -B exit.py plan --stop atr:1.5 --offline RELIANCE.json   # ATR needs the session's bars
    python -B exit.py plan --symbol HDFCBANK --entry 683.30 --setup level --stop pct:1
    python -B exit.py show                                  # the current trader/plan.json

Prices are rupees on the NSE tick grid. The tick comes from the security's price
band (NSE circular 33/2025: below Rs 250 0.01, 250 to 1,000 0.05, above 1,000 to
5,000 0.10, to 10,000 0.50, to 20,000 1.00, above 20,000 5.00). NSE sets it each
month from the previous month's last close; this tool uses the entry's band unless
you give --tick (or the plan says otherwise).

Stops (a long's stop rounds DOWN to the tick, away from the entry):
  or-mid     the opening-range midpoint (the default for an orb entry)
  or-low     the opening-range low
  price:X    your price
  pct:X      X percent under the entry
  atr:K      K times the 14-bar ATR under the entry, on the session's bars up
             to the entry bar (--offline FILE, else your own Dhan Data API
             key if you have one, else Yahoo's free chart automatically, with
             a warning: see SKILL.md "Data warning").
             With fewer than 14 bars by then it averages all of them and says so.
  A level setup has no opening-range default: give a stop.

Targets (round DOWN to the tick too, toward the entry: never more than the R asked for):
  1R (default, or "target" in the config), 1.5R, 2R ...   entry + R x risk
  price:X                                                  your price

Refused, with the reason: a stop at or above the entry, a target at or below
it, a risk under 0.05% of the price or under one tick (too tight to survive the
spread), an entry.json that has not triggered (unless you give --entry), a
short (longs only).

flat_by (default 15:00 IST, or "flat_by" in trader/config.json): out by then, win
or lose. It must come before continuous trading ends (15:15 for Closing Auction
Session stocks from 2026-08-03, else 15:30). At or after "mis_square_off"
(default 15:10, Dhan's intraday equity auto-square-off) it adds a note: the
broker would square the position off first, for a fee. "min_reward_risk" in the
config (default 1.0) only warns here; trader-risk enforces it.

A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
Exit codes: plan 0 = a plan, 1 = refused or an error. show 0 = a plan exists.
Not financial advice. Nothing is ever placed.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

INTERVALS = {"1m": 60, "5m": 300}
DEFAULTS = {"flat_by": "15:00", "min_reward_risk": 1.0, "target": "1R", "mis_square_off": "15:10"}
ATR_BARS = 14
MIN_RISK_PCT = Decimal("0.05")     # risk under 0.05% of the price is inside the spread's noise
PAISA = Decimal("0.01")
NOTE = "A stop is a plan, not a guarantee: a gap or a fast market can fill past it."

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


class PlanError(Exception):
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
    """--now -> naive UTC. A time without an offset is IST."""
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
        raise PlanError('--now wants an IST time like "2026-10-15 09:55" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise PlanError("%s wants an IST time like 15:00, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def parse_clock(text, what):
    """10:15 AM, 10:15, 3:05 PM -> time."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$", str(text))
    if not m:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        if not 1 <= h <= 12:
            raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    if h > 23 or mi > 59:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    return datetime.time(h, mi)


def hm(t):
    return "%02d:%02d" % (t.hour, t.minute)


def ampm(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


# ---------------------------------------------------------------- money math (Decimal, rupees on the tick grid)

def D(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        return Decimal(str(x).strip().replace("Rs", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise PlanError("%s is not a number: %r" % (what, x))


def paise(x):
    """Round half up to the paisa (every NSE tick is a whole number of paise)."""
    return D(x).quantize(PAISA, rounding=ROUND_HALF_UP)


def to_tick(x, tick, rounding):
    """x on the tick grid: ROUND_FLOOR (down) or ROUND_CEILING (up), exact in Decimal."""
    t = D(tick)
    return ((D(x) / t).to_integral_value(rounding=rounding) * t).quantize(PAISA)


def money(x):
    return "Rs %s" % format(D(x).quantize(PAISA), ",f")


def num(x):
    """1, 1.5, 2.25: a Decimal without trailing zeros."""
    s = format(D(x).normalize(), "f")
    return s


def ratio(x):
    """reward:risk for people: 1.0, 1.5, 1.37."""
    s = "%.2f" % float(x)
    return s[:-1] if s.endswith("0") else s


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid", "or-midpoint"):
        return "or_mid", None
    if t in ("or-low", "or_low", "low"):
        return "or_low", None
    m = re.match(r"^(price|pct|atr):\s*(?:rs\.?\s*)?([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, price:X, pct:X or atr:K (got %r)" % text)
    kind, val = m.group(1), D(m.group(2), "--stop")
    if val <= 0:
        raise PlanError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 100:
        raise PlanError("--stop pct:X needs X under 100")
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = D(m.group(1), "--target")
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*(?:rs\.?\s*)?([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", D(m.group(1), "--target")
    raise PlanError("--target is 1R, 1.5R, 2R ... or price:X (got %r)" % text)


def r_label(r):
    n = int(r) if r == r.to_integral_value() else None
    return "%sR (%s times the risk)" % (num(r), WORDS.get(n, num(r)))


def compute(entry, stop_rule, stop_param, target_rule, target_param, or_high=None, or_low=None,
            atr=None, atr_bars=None, tick=None):
    """The stop, the target and the risk in rupees on the NSE tick grid. A long's stop
    rounds DOWN to the tick (away from the entry); the target rounds DOWN too (toward
    the entry, so it never promises more than the R asked for). tick defaults to the
    price band of the entry. Raises PlanError with the reason."""
    e = paise(entry)
    if e <= 0:
        raise PlanError("the entry price must be above Rs 0")
    tk = D(tick if tick is not None else tick_for(float(e)), "--tick")
    if tk <= 0:
        raise PlanError("--tick must be above 0")
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        oh, ol = paise(or_high), paise(or_low)
        if oh <= ol:
            raise PlanError("the opening-range high %s must be above its low %s" % (money(oh), money(ol)))
        if stop_rule == "or_mid":
            raw = (oh + ol) / 2
            label = "opening-range midpoint"
            detail = "(%s + %s) / 2 = Rs %s" % (money(oh), money(ol), num(raw))
        else:
            raw, label = ol, "opening-range low"
    elif stop_rule == "price":
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        raw = e * (1 - stop_param / 100)
        label = "%s%% under the entry" % num(stop_param)
        detail = "%s x (1 - %s%%) = Rs %s" % (money(e), num(stop_param), num(raw))
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs the session's bars")
        a = D(round(atr, 6))
        raw = e - stop_param * a
        label = "%s x ATR(%d) under the entry" % (num(stop_param), atr_bars)
        detail = "ATR Rs %s over the %d bars to the entry%s" % (
            num(a.quantize(Decimal("0.0001"))), atr_bars, " (fewer than 14 so far)" if atr_bars < ATR_BARS else "")
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)
    stop = to_tick(raw, tk, ROUND_FLOOR)
    if stop != D(raw):
        what = "rounded down to the Rs %s tick" % num(tk)
        detail = (detail + ", " + what if detail else "Rs %s %s" % (num(raw), what))
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                        % (money(stop), label, money(e)))
    if stop <= 0:
        raise PlanError("the stop works out to %s: pick a stop above Rs 0" % money(stop))
    risk = e - stop
    floor_risk = max(e * MIN_RISK_PCT / 100, tk)
    if risk < floor_risk:
        raise PlanError("the stop is %s under the entry, less than %s (0.05%% of the price, and at least one Rs %s "
                        "tick): too tight to survive the spread, normal wiggle would stop you out. Widen it."
                        % (money(risk), money(floor_risk.quantize(PAISA, rounding=ROUND_HALF_UP)), num(tk)))
    if target_rule == "r":
        raw_t = e + target_param * risk
        t_label = r_label(target_param)
    else:
        raw_t = target_param
        t_label = None
    target = to_tick(raw_t, tk, ROUND_FLOOR)
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a long's target goes above it"
                        % (money(target), money(e)))
    rr = ((target - e) / risk).quantize(PAISA, rounding=ROUND_HALF_UP)
    if t_label is None:
        t_label = "your price (%sR)" % ratio(rr)
    elif target != D(raw_t):
        t_label += ", rounded down to the tick (%sR)" % ratio(rr)
    return {"entry": e, "stop": stop, "stop_label": label, "stop_detail": detail, "target": target,
            "target_r": target_param if target_rule == "r" else rr, "target_label": t_label,
            "risk": risk, "reward_risk": rr, "tick": tk}


# ---------------------------------------------------------------- bars for ATR (same reader as trader-entry)

def read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parse_chart_text(text, sym, ysym):
    """The legacy chart-JSON shape every --offline file in this pack uses -> bars for the
    ATR (just t/h/l/c; the ATR does not need open or volume)."""
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise PlanError("the chart file for %s was not readable" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise PlanError("no data in the chart file for %s: %s" % (sym, err.get("description") or "unknown ticker"))
    r = res[0] or {}
    meta = r.get("meta") or {}
    if str(meta.get("symbol") or ysym).upper() != ysym:
        raise PlanError("the chart file holds %s, not %s" % (meta.get("symbol"), sym))
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def at(key, i):
        arr = q.get(key) or []
        return arr[i] if i < len(arr) else None

    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = at("close", i)
        if t is None or c is None:
            continue
        h, lo = at("high", i), at("low", i)
        # whole paise: float noise from a chart feed (1186.0999755) would otherwise add up
        # inside the ATR and can push an ATR stop one tick the wrong way
        bars.append({"t": int(t), "h": round(float(c if h is None else h), 2), "l": round(float(c if lo is None else lo), 2),
                     "c": round(float(c), 2)})
    return {"bars": bars, "data_time": meta.get("regularMarketTime")}


YAHOO_HOSTS = ("query2", "query1")
YAHOO_CHART_URL = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=%s"


def fetch_yahoo_chart(sym, ysym, interval, timeout=20):
    """The free, automatic tier-3 fallback (no Dhan key, no --offline): Yahoo's own chart
    endpoint, read with a plain unauthenticated GET (no login, no scrape of a protected
    page). Returns the same JSON text shape parse_chart_text() already reads. Retries once
    on the alternate host for a 429 or a 5xx. See YAHOO_FALLBACK_WARNING and the SKILL.md
    "Data warning": a direct check found Yahoo's NSE bars can be wrong."""
    last = None
    for host in YAHOO_HOSTS:
        url = YAHOO_CHART_URL % (host, urllib.parse.quote(ysym), interval)
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
    raise PlanError("could not reach Yahoo's free chart endpoint for %s: %s" % (sym, last))


def load_chart(sym, interval, day, offline=None):
    """Bars for the ATR stop: --offline file if given, else the Dhan Data API if a key is
    set (DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN), else Yahoo's free chart endpoint automatically
    (prints YAHOO_FALLBACK_WARNING every time: see the SKILL.md "Data warning")."""
    s = sym.strip().upper()
    ysym = s if (s.startswith("^") or "." in s) else s + ".NS"
    if offline:
        path = offline
        if os.path.isdir(offline):
            for name in (s + ".json", s + ".json.gz", s + ".NS.json", s + ".NS.json.gz"):
                path = os.path.join(offline, name)
                if os.path.exists(path):
                    break
        if not os.path.exists(path):
            raise PlanError("no offline file %s" % path)
        try:
            text = read_text(path)
        except (OSError, ValueError) as e:
            raise PlanError("could not read %s: %s" % (path, e))
        return parse_chart_text(text, sym, ysym)
    if dhan_available():
        try:
            ch = dhan_get_intraday(s, interval, day)
        except DhanError as e:
            raise PlanError(str(e))
        bars = [{"t": b["t"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in ch["bars"]]
        return {"bars": bars, "data_time": ch["data_time"]}
    print(YAHOO_FALLBACK_WARNING, file=sys.stderr)
    text = fetch_yahoo_chart(sym, ysym, interval)
    return parse_chart_text(text, sym, ysym)


def session_bars(chart, day, interval, upto_slot=None, now_utc=None):
    """Closed, on-grid bars of one IST session's continuous trading, oldest first (the live tick is dropped)."""
    step = INTERVALS[interval]
    s = session(day)
    if not s:
        return []
    open_dt = datetime.datetime.combine(day, s[0])
    close_dt = datetime.datetime.combine(day, s[1])
    if now_utc is None and chart.get("data_time"):
        now_utc = utc_of(chart["data_time"])
    local = to_ist(now_utc) if now_utc else None
    out = {}
    for b in chart["bars"]:
        if b["t"] % step:
            continue
        et = to_ist(utc_of(b["t"]))
        if et.date() != day or not (open_dt <= et < close_dt):
            continue
        if local and et + datetime.timedelta(seconds=step) > local:
            continue
        slot = int((et - open_dt).total_seconds()) // step
        if upto_slot is not None and slot > upto_slot:
            continue
        out[slot] = b
    return [out[k] for k in sorted(out)]


def atr_of(bars, n=ATR_BARS):
    """Average true range of the last n bars (all of them if fewer). The first
    bar of the session uses its own high - low (no overnight gap)."""
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"]
        if prev is not None:
            tr = max(tr, abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


# ---------------------------------------------------------------- plan

def read_json(path, what):
    if not path or not os.path.exists(path):
        raise PlanError("no %s at %s" % (what, path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise PlanError("%s is not valid JSON: %s" % (path, e))


def read_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        doc = read_json(path, "config")
        for k in DEFAULTS:
            if doc.get(k) is not None:
                cfg[k] = doc[k]
    return cfg


def resolve_flat_by(day, explicit, cfg, entry_time):
    """(HH:MM, note). Flat-by must come before continuous trading ends (15:15 with the
    Closing Auction Session, else 15:30). A flat-by at or after the broker's intraday
    (MIS) auto-square-off time ("mis_square_off", default 15:10, Dhan's equity time)
    gets a note: the broker would close the position first, for a fee."""
    what = "--flat-by" if explicit else "flat_by"
    t = parse_hhmm(explicit or cfg.get("flat_by") or DEFAULTS["flat_by"], what)
    note = ""
    s = session(day) if day else None
    if s and t >= s[1]:
        raise PlanError("%s %s is at or after the end of continuous trading (%s IST) on %s"
                        % (what, hm(t), hm(s[1]), day))
    mis = parse_hhmm(cfg.get("mis_square_off") or DEFAULTS["mis_square_off"], "mis_square_off")
    if t >= mis:
        note = ("at or after the broker's intraday auto-square-off (%s IST): the broker would square off the MIS "
                "position first, and charge for it. Keep a margin before %s" % (hm(mis), hm(mis)))
    if entry_time and entry_time >= t:
        raise PlanError("the entry at %s is at or after your flat-by %s: there is no time left to hold it"
                        % (ampm(entry_time), hm(t)))
    return hm(t), note


def gather(a):
    """The entry facts, from trader/entry.json or the flags."""
    if a.symbol and a.from_path:
        raise PlanError("use --from or --symbol, not both")
    if a.side in ("sell", "short"):
        raise PlanError("longs only: no stops above a short entry. Plan a buy instead.")
    if a.symbol:
        if a.entry is None:
            raise PlanError("--symbol needs --entry PRICE")
        if (a.or_high is None) != (a.or_low is None):
            raise PlanError("give both --or-high and --or-low, or neither")
        info = {"symbol": a.symbol.upper(), "entry": a.entry, "setup": a.setup or ("orb" if a.or_high is not None else "level"),
                "date": a.date, "entry_time": a.time, "entry_bar": a.entry_bar, "interval": a.interval or "5m",
                "or_high": a.or_high, "or_low": a.or_low, "or_minutes": a.or_minutes, "source": "flags"}
    else:
        path = a.from_path or "trader/entry.json"
        doc = read_json(path, "entry (run /trader-entry first, or give --symbol and --entry)")
        if str(doc.get("side") or "buy").lower() not in ("buy", "long"):
            raise PlanError("%s is a %s: longs only" % (path, doc.get("side")))
        if doc.get("status") != "triggered" and a.entry is None:
            raise PlanError("%s says %s is %s, not triggered: plan once it triggers, or give --entry PRICE to plan ahead"
                            % (path, doc.get("symbol"), doc.get("status") or "unknown"))
        orr = doc.get("opening_range") or {}
        info = {"symbol": str(doc.get("symbol") or "").upper(), "entry": a.entry if a.entry is not None else doc.get("price"),
                "setup": a.setup or doc.get("setup") or "orb", "date": a.date or doc.get("date"),
                "entry_time": a.time or (doc.get("time") if a.entry is None else None),
                "entry_bar": a.entry_bar if a.entry_bar is not None else (doc.get("bar_index") if a.entry is None else None),
                "interval": a.interval or doc.get("interval") or "5m",
                "or_high": a.or_high if a.or_high is not None else orr.get("high"),
                "or_low": a.or_low if a.or_low is not None else orr.get("low"),
                "or_minutes": orr.get("minutes") or a.or_minutes, "source": path}
        if not info["symbol"] or info["entry"] is None:
            raise PlanError("%s has no symbol or price" % path)
    if info["interval"] not in INTERVALS:
        raise PlanError("interval is 5m or 1m")
    return info


def build_plan(a, now_utc):
    info = gather(a)
    cfg = read_config(a.config)
    stop_text = a.stop
    if not stop_text:
        if info["setup"] != "orb":
            raise PlanError("a %s setup has no opening-range stop to default to: give --stop price:X, pct:X or atr:K"
                            % info["setup"])
        stop_text = "or-mid"
    stop_rule, stop_param = parse_stop(stop_text)
    target_rule, target_param = parse_target(a.target or cfg.get("target") or DEFAULTS["target"])
    day = None
    if info["date"]:
        try:
            day = datetime.date.fromisoformat(str(info["date"]))
        except ValueError:
            raise PlanError("date wants YYYY-MM-DD, not %r" % info["date"])
    else:
        day = to_ist(now_utc).date()
    entry_time = parse_clock(info["entry_time"], "the entry time") if info["entry_time"] else None
    atr = atr_n = None
    if stop_rule == "atr":
        chart = load_chart(info["symbol"], info["interval"], day, a.offline)
        bars = session_bars(chart, day, info["interval"], info["entry_bar"], parse_now(a.now) if a.now else None)
        if len(bars) < 3:
            raise PlanError("an atr stop needs at least 3 closed %s bars of %s on %s by the entry; found %d"
                            % (info["interval"], info["symbol"], day, len(bars)))
        atr, atr_n = atr_of(bars)
    c = compute(info["entry"], stop_rule, stop_param, target_rule, target_param, info["or_high"], info["or_low"],
                atr, atr_n, a.tick)
    flat_by, flat_note = resolve_flat_by(day, a.flat_by, cfg, entry_time)
    orr = None
    if info["or_high"] is not None and info["or_low"] is not None:
        orr = {"high": float(paise(info["or_high"])), "low": float(paise(info["or_low"])),
               "minutes": int(info["or_minutes"] or 15)}
    plan = {"created_by": "trader-exit", "symbol": info["symbol"], "side": "buy", "date": day.isoformat(),
            "setup": info["setup"], "entry": float(c["entry"]),
            "entry_time": ampm(entry_time) if entry_time else None,
            "entry_bar": info["entry_bar"], "interval": info["interval"], "opening_range": orr,
            "stop": float(c["stop"]), "stop_rule": stop_rule, "stop_label": c["stop_label"],
            "target": float(c["target"]), "target_r": float(c["target_r"]), "target_label": c["target_label"],
            "risk_per_share": float(c["risk"]), "reward_risk": float(c["reward_risk"]), "flat_by": flat_by,
            "tick": float(c["tick"]), "currency": "INR",
            "stop_detail": c["stop_detail"], "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "note": NOTE}
    if atr is not None:
        plan["atr"] = round(atr, 4)
        plan["atr_bars"] = atr_n
    warnings = []
    if flat_note:
        warnings.append("Flat-by %s: %s." % (flat_by, flat_note))
    mrr = cfg.get("min_reward_risk")
    try:
        if mrr is not None and c["reward_risk"] < D(mrr):
            warnings.append("Reward:risk %s is under your min_reward_risk %s in the config: trader-risk checks that."
                            % (ratio(c["reward_risk"]), num(D(mrr))))
    except PlanError:
        pass
    today = to_ist(now_utc).date()
    if day != today:
        warnings.append("This plan is for %s, not today (%s)." % (day.isoformat(), today.isoformat()))
    return plan, warnings


def describe(plan):
    day = datetime.date.fromisoformat(plan["date"])
    setup = {"orb": "opening-range breakout", "level": "close above a level"}.get(plan["setup"], plan["setup"])
    e = D(plan["entry"])
    risk = D(plan["risk_per_share"])
    pct = (risk / e * 100).quantize(PAISA, rounding=ROUND_HALF_UP)
    when = ""
    if plan.get("entry_time"):
        when = " at %s" % plan["entry_time"]
        if plan.get("entry_bar") is not None:
            when += " (bar %d)" % plan["entry_bar"]
    out = ["Exit plan: %s long, %s, %s on %s bars" % (
        plan["symbol"], day.strftime("%a %Y-%m-%d"), setup, "5-minute" if plan["interval"] == "5m" else "1-minute")]
    out.append("  Entry         %s%s" % (money(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (money(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (money(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s (%s%% of the entry)" % (money(plan["risk_per_share"]), pct))
    out.append("  Reward:risk   %s" % ratio(plan["reward_risk"]))
    out.append("  Flat by       %s IST, win or lose: no overnight hold (intraday, MIS)" % plan["flat_by"])
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(NOTE)
    return out


def cmd_plan(a):
    now = parse_now(a.now)
    plan, warnings = build_plan(a, now)
    if a.write:
        d = os.path.dirname(a.write)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.write, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2)
            fh.write("\n")
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings), indent=2))
        return 0
    for line in describe(plan):
        print(line)
    for w in warnings:
        print("Note: " + w)
    if a.write:
        print("Wrote %s." % a.write)
    print("Next: /trader-timemachine replays it, then /trader-budget sizes it to your max loss. Nothing was placed.")
    return 0


def cmd_show(a):
    if not os.path.exists(a.plan):
        print("No plan at %s yet. Make one: exit.py plan --write" % a.plan)
        return 1
    plan = read_json(a.plan, "plan")
    for k in ("symbol", "date", "entry", "stop", "target", "risk_per_share", "reward_risk", "flat_by"):
        if plan.get(k) is None:
            raise PlanError("%s is missing %r: make a new plan with exit.py plan --write" % (a.plan, k))
    if a.json:
        print(json.dumps(plan, indent=2))
        return 0
    for line in describe(dict({"setup": "orb", "interval": "5m", "stop_label": "", "target_label": ""}, **plan)):
        print(line)
    today = to_ist(parse_now(a.now)).date().isoformat()
    if plan["date"] != today:
        print("Note: this plan is for %s, not today (%s)." % (plan["date"], today))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit Planner (trader-exit): the stop and target before the entry.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="set the stop and target")
    p.add_argument("--from", dest="from_path", metavar="PATH", help="entry.json to plan from (default trader/entry.json)")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float, help="entry price (or a plan-ahead price with --from)")
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--setup", choices=["orb", "level"])
    p.add_argument("--date", help="session date YYYY-MM-DD (default: the entry's, or today)")
    p.add_argument("--time", help="entry time, e.g. 10:15 AM")
    p.add_argument("--entry-bar", type=int)
    p.add_argument("--interval", choices=["5m", "1m"])
    p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="v2: buy only")
    p.add_argument("--stop", help="or-mid (orb default), or-low, price:X, pct:X, atr:K")
    p.add_argument("--target", help="1R (default, or config target), 1.5R, 2R, price:X")
    p.add_argument("--flat-by", help="HH:MM IST (default: config flat_by, 15:00)")
    p.add_argument("--tick", type=float, help="tick size in rupees (default: the NSE price band of the entry)")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--offline", metavar="PATH", help="saved chart JSON for an atr stop (else Dhan key, else Yahoo automatically)")
    p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-10-15 09:56"')
    p.add_argument("--write", nargs="?", const="trader/plan.json", metavar="PATH",
                   help="save the plan (default trader/plan.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="print the current plan")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except PlanError as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
