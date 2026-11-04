# The Day Trader skill, NSE India version

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#running-on-windows)
[![Market: NSE India](https://img.shields.io/badge/market-NSE%20India-orange.svg)](#)
[![Tests: 268 passing](https://img.shields.io/badge/tests-268%20passing-brightgreen.svg)](#running-on-windows)
[![Zero setup](https://img.shields.io/badge/data-Yahoo%20%E2%80%A2%20Dhan%20%E2%80%A2%20offline-lightgrey.svg)](#data-warning)

11 Claude Code skills (plugin) for **NSE (India) day-trade research**: paper-trade, scan the
Nifty 100 for opening-range breakouts, time every entry/exit in IST, size positions in rupees with
Dhan's real costs, and replay the odds with a Monte Carlo time machine before risking a rupee. No
account, no API key and no setup needed to start: it reads a free live chart automatically.

This is the India (NSE) version of [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill).
The original was built by Jake Schincariol, under the MIT license (the LICENSE file keeps the
original copyright notice exactly as it was).

**Paper and analysis only.** Nothing in this pack places, modifies, or cancels an order with any
broker, and it never touches funds or positions. Not financial advice. Most people lose money day
trading.

**Disclaimer:** whoever built or modified this takes no responsibility for any loss. The stock
market carries real risk, so plan and invest only after your own careful thought.

> **Read this first:** none of this project's own tests found any rule profitable after costs:
> not ORB (stocks or NIFTY futures), not the 540 candlestick + indicator rules tested. These tools
> are for learning and testing, not a guarantee of making money.

## Contents

- [The eleven skills](#the-eleven)
- [Running on Windows (quick start)](#running-on-windows)
- [What changed from the US version](#what-changed-from-the-us-version)
- [Data warning](#data-warning)
- [Fine print](#fine-print)

## The eleven

| Skill | What it does |
| --- | --- |
| `/trader-scanner` | Finds gappers and opening-range breakouts in the Nifty 100, with a traded-value filter |
| `/trader-news` | Google News, ET Markets, Zerodha Pulse, Livemint and NSE announcements; move from previous close, in IST |
| `/trader-entry` | Watches the opening range and tells you when the setup triggered |
| `/trader-exit` | Stop, target and flat-by time, rounded to the NSE tick |
| `/trader-timemachine` | Replays the rest of the session from the stock's own recent bars (10,000 Monte Carlo replays) |
| `/trader-risk` | Rules check: daily loss, trades per day, entry window, F&O block |
| `/trader-budget` | Sizes to whole shares from your rupee max-loss |
| `/trader-broker` | A paper journal on your explicit yes/no, one order at a time |
| `/trader-alerts` | Price and time alerts on the NSE clock |
| `/trader-analyst` | Not applicable (US SEC filings) |
| `/trader-pelosi` | Not applicable (US Congress disclosures) |

## Running on Windows

Open Command Prompt in the repo folder (Python 3.8+ required, no packages to install):

```
python -B -m unittest discover -s tests
python -B trader-alerts\alerts.py clock
python -B trader-entry\entry.py check RELIANCE
python -B backtest\costs.py --qty 102 --buy 1186.10 --sell 1191.00
python -B backtest\backtest.py run --legacy-yahoo --data <folder of saved Yahoo chart JSON> --out <output folder> --no-tm
```

It runs live with no setup at all: without a Dhan key or `--offline`, it automatically falls back
to Yahoo's free chart, with a warning. For a better result, set up your own Dhan Data API key (set
`DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN`, or write them into `trader\broker.json`; each skill's
SKILL.md has the setup under its "Data warning" section), or pass your own data with
`--offline FILE`.

`-B` stops `__pycache__` folders from being created. `py -B` also works.

## What changed from the US version

- **Market:** NSE cash equity, IST. Continuous trading 09:15 to 15:30 (15:15 for Closing Auction
  Session stocks, from 2026-08-03). 2026 NSE holidays, special sessions like Muhurat excluded, NSE
  tick sizes (the stop rounds away from entry).
- **Data:** open source, so no skill's live fetch ships its own data source. Three tiers, always
  tried in this order: **`--offline FILE`** (your own chart data, built from any broker) if you
  give one, otherwise your own **Dhan Data API key** (DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN) if set
  (the better result), otherwise **Yahoo's free chart**, fully automatic, with nothing to set up
  (same as the original pack). The Yahoo tier also prints a warning every time (see "Data warning"
  below). The Nifty 100 list comes from NSE's own index files; news comes from Google News (India),
  ET Markets, Zerodha Pulse, Livemint and NSE announcements: all free public feeds that need no
  key.
- **Money:** rupees. Defaults: 15-minute opening range, no entry before 09:30 or after 14:30, flat
  by 15:00 (Dhan's MIS auto-squares off at 15:10 anyway), long only, max loss Rs 500 per trade.
- **Costs:** `backtest\costs.json`: Dhan's intraday brokerage plus NSE/SEBI/government charges,
  with a source URL and the date it was checked, plus 0.03% slippage per side. Matches Dhan's own
  brokerage calculator to within Rs 0.04.
- **Removed:** the PDT rule, the Alpaca / Interactive Brokers order path (trader-broker is now a
  manual paper journal), SEC EDGAR, and the Pelosi tracker (not applicable).

## Data warning

This pack has no data source of its own (it's open source): every skill's live path tries three
tiers, always in this order:

1. **`--offline FILE`**, if given: a chart JSON you built yourself from any broker. Always wins.
2. **Your own Dhan Data API key**, if set (`DHAN_CLIENT_ID`/`DHAN_ACCESS_TOKEN`, or
   `trader\broker.json`). Needs the paid Dhan Data API add-on, but it's the most reliable of the
   three.
3. **Yahoo's free chart, automatic**, when neither of the above is available: no account, no key,
   nothing to set up (same zero-setup design as the original pack). A direct check against Dhan's
   own chart found Yahoo's NSE 5-minute bars aren't fully reliable (the 09:15 bar's high short by
   up to Rs 20.50, the day's high or low missing on 31-37% of days). Whenever a skill falls back to
   this tier, this warning prints to the terminal, every time, never silently.

- `backtest.py` still doesn't download fresh Yahoo data; it only runs on an old Yahoo cache with
  `--legacy-yahoo`, and every output is marked "UNRELIABLE" (backtest hasn't moved to the Dhan Data
  API yet; only the live skills use the three-tier design).
- Every skill's SKILL.md has a "Data warning" section: details on all three tiers, and how setting
  up your own Dhan key or `--offline` file gets a better result.
- The `--offline` and Yahoo tiers both read the same old Yahoo-shaped chart JSON file (the fixtures
  are in that same shape); only the Dhan tier uses its own format, which each skill reshapes.
- Supplying your own Dhan key or `--offline` data always gives a better result than Yahoo's
  automatic free tier: use it before putting real money on the line.

## Fine print

Price data comes from `--offline`, from your own Dhan Data API key, or otherwise from Yahoo's
automatic free chart (all three can be delayed, Yahoo least reliably); news feeds come from free,
unofficial public endpoints and can be late or wrong. Nothing here is a recommendation. The
decision to trade live is yours alone, outside this pack.

**This pack is provided as-is: whoever built or modified it takes no responsibility for any loss
from using it. The stock market carries real risk, so plan and invest only after your own careful
thought.**

MIT license: original copyright (c) 2026 Jake Schincariol; the India-version changes are under the
same license.
