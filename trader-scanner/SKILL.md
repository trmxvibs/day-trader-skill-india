---
name: trader-scanner
description: >-
  Chart Scanner for the NSE (India). Scans the Nifty 100 (or Nifty 50, or your
  own list) for setups: opening-range breakouts, flags, oversold bounces.
  Builds the universe from NSE's own index constituent file, runs a pre-market
  scan of daily closes (20-day closing-high breakout, bull flag, reclaimed the
  20-day average, oversold, oversold bounce) with a traded-value liquidity
  floor in rupees, and an intraday opening-range breakout scan on 5-minute
  bars from 9:15 IST, then writes trader/watchlist.json for trader-news and
  trader-entry. --offline bars you made yourself, else your own Dhan Data API
  key, else Yahoo's free chart automatically, bar by bar. Use when the user says
  "scan the market", "find me setups", "what is breaking out", "opening range
  breakouts", "scan for bull flags", "oversold stocks", "pre-market scan",
  "build my watchlist", or at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads free charts and
lists the ones that match a rule. That is all it does. A scan is a list of
charts that match a rule, not a list of stocks to buy, and a setup on a chart
is not a prediction. Most people who day trade lose money; the scanner does
not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/scan.py" universe                 # NSE Nifty 100 -> trader/universe.txt
python3 -B "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty50
python3 -B "${CLAUDE_SKILL_DIR}/scan.py" daily                    # before the 9:15 open -> trader/watchlist.json
python3 -B "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,HDFCBANK,"M&M"
python3 -B "${CLAUDE_SKILL_DIR}/scan.py" orb                      # after 9:35 AM IST -> trader/watchlist.json
python3 -B "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:30           # cut the clock at 10:30 AM IST
```

Flags: `--min-price 20` (rupees), `--min-value` (traded value in rupees, price x
volume: daily Rs 25 crore, orb 0), `--min-volume 0`, `--or-minutes 15`,
`--now "2026-10-29 10:30"` (IST) to pin the clock, `--offline PATH` to read
saved chart files instead of the network, `--pause 0.3` (seconds between
calls), `--no-write`, `--show N`, `--json`. With no `--offline`, this skill
uses your own Dhan Data API key when it finds one, and otherwise falls back
automatically to Yahoo's free chart, one call a symbol either way, with a
warning on Yahoo (see "## Data warning" below). Exit code 0 when the scan
found something, 1 when it found nothing or could not run (the reason is one
plain line).

## What it reads and writes

| File | What |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `filters`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:30) |

## The universe

`universe` downloads NSE's index constituent file (`ind_nifty100list.csv`,
first from nsearchives.nseindia.com, then niftyindices.com) and keeps every EQ
series row with a plain NSE symbol (letters, digits, `&` and `-`: `M&M`,
`BAJAJ-AUTO`). On the 2026-11-01 file that is 100 symbols. It is today's list:
a backtest over past months on it carries some survivorship bias (stocks that
left the index are missing).

## The daily scan (before the open)

`--offline`, else one Dhan Data API call a symbol (about 130 days of daily
bars, needs your own `DHAN_CLIENT_ID`/`DHAN_ACCESS_TOKEN`), else one Yahoo
call a symbol automatically: there is no batch endpoint for either live
source, so the Nifty 100 is 100 calls and `--pause` between them matters.
Liquidity: last price at least `--min-price` and a
traded value (price x last session's volume) at least `--min-value`; treat the
traded-value floor as rough, volume can differ between two readings. Run it
before the open or after the close: today's daily bar is left out until
continuous trading ends.

| Setup | The rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars. A hit is the first completed 5-minute bar to
close above the range high, at that bar's close time.

`--offline`, else one Dhan Data API call a symbol (needs your own
`DHAN_CLIENT_ID`/`DHAN_ACCESS_TOKEN`), else one Yahoo call a symbol
automatically: all three get the whole session's 5-minute bars with real
highs and lows already, so unlike the old two-stage Yahoo design there is no
separate confirm stage, and `--max-confirm` is a leftover flag that now does
nothing.

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt, a thin stock or a data gap (2026-10-15 had
no 11:45 or 11:50 bar on any NSE symbol). Continuous trading ends at 15:15 for
Closing Auction Session stocks (from 2026-08-03); any 15:15 to 15:25 stamps
on those days are auction artifacts and never count. Before 9:35 AM, on a
weekend, an NSE holiday or a special session (Muhurat), after continuous
trading, or after `no_entries_after` (an expired setup) it prints one plain
line and writes nothing.

## Example: Thursday 2026-10-29, scan at 10:30 AM IST

Four real charts (`tests/fixtures/nse/wall5m-2026-10-29.json.gz`):

```
python3 -B scan.py orb --offline wall5m-2026-10-29.json.gz --now "2026-10-29 10:30"
Fetched 4 of 4 charts; 4 usable; 2 candidates, 2 confirmed with real highs and lows.
2 of 4 charts closed above their opening-range high by 10:30 AM:
   9:35 AM  HDFCBANK        Rs 682.50  range 678.30 to 682.45  +0.01% over
   9:40 AM  RELIANCE      Rs 1,174.30  range 1,156.20 to 1,170.50  +0.32% over
```

M&M first closed above its range high at 1:00 PM; BAJAJ-AUTO never did that
day. The user picks one chart and Claude hands it to `/trader-news SYMBOL`,
then `/trader-entry SYMBOL`.

## Data and limits

- This pack is open source: bars come from `--offline` files, else your own
  Dhan Data API key, else Yahoo's free chart automatically, bar by bar.
  Neither live source has a batch endpoint, so a big universe is one call a
  symbol and takes a while; `--pause` keeps it polite.
- Partial results are said as partial. Never round a partial scan up to "the
  whole market".
- NSE holidays from NSE circulars 172/2025 and 07/2026 (2026 only; check
  nseindia.com for later years).

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L or a return.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy, and
never promises that a setup works.

## Data warning

This pack is open source: it brings no data source of its own, so it tries, in order:

1. **`--offline FILE`,** if you pass it: saved chart files you made yourself, by reading
   your own broker's chart (any broker) and scripting or typing the bars in. Always wins
   when given.
2. **Your own Dhan Data API key,** if you have set one up. Needs your own Dhan trading
   account plus the Data API add-on (a paid monthly subscription at the time of writing) and
   an access token from web.dhan.co (My Profile > DhanHQ Trading APIs). Set `DHAN_CLIENT_ID`
   and `DHAN_ACCESS_TOKEN` (environment variables), or `trader/broker.json:
   {"dhan_client_id": "...", "dhan_access_token": "..."}`. See `tools/dhan_block.py` for the
   exact setup and the API docs it links to. Dhan has no batch endpoint, so a scan over a big
   universe is one call a symbol: `--pause` between calls matters. Usually the most reliable
   of the three.
3. **Yahoo's free chart, automatically,** if neither of the above applies: no account, no
   key, nothing to set up, one call a symbol, same as Dhan. A direct check against Dhan's own
   chart found Yahoo's 5-minute NSE bars miss part
   of the price range (the 09:15 bar's high short by up to Rs 20.50, the day's high or low
   missing on 31-37% of days). This skill prints a warning to that effect every time it falls
   back to this tier.

Whichever source is used, treat every level, range and trigger here as a rough view, and
check it on your broker's own chart before relying on it. Bringing your own Dhan key gets you
a steadier result than the automatic free tier. This pack is provided as-is: whoever built or
modified it takes no responsibility for any loss from using it, and the stock market carries
real risk: plan and invest only after your own careful judgement.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- India version (NSE): paper and analysis only. Nothing in this pack places, modifies or cancels an order
  at any broker, and nothing touches funds or positions. trader-broker keeps a manual paper journal of
  the decisions you type yourself.
- Never type a password, PIN, TOTP or OTP into the chat or a tool. Log in to a broker yourself.
- No options, margin, short selling or leverage unless the config allows it and the user confirms. In this
  pack the short side exists only as a backtest variant.
- Going live is the user's own decision, made outside this pack. Day trading loses most people money.
- This pack is provided as-is: whoever built or modified it takes no responsibility for any loss from using it. The stock market carries real risk, so plan and invest only after your own careful thought.
