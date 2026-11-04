# The Day Trader skill, NSE India version

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#the-tools)
[![Market: NSE India](https://img.shields.io/badge/market-NSE%20India-orange.svg)](#)
[![Tests: 268 passing](https://img.shields.io/badge/tests-268%20passing-brightgreen.svg)](#the-tools)
[![Zero setup](https://img.shields.io/badge/data-Yahoo%20%E2%80%A2%20Dhan%20%E2%80%A2%20offline-lightgrey.svg)](#data-warning)

11 Claude Code skills (a plugin) for **NSE (India) day-trade research**: scan the Nifty 100 for
opening-range breakouts, track the news on what you're watching, time every entry and exit in IST,
size positions in rupees against Dhan's real costs, check each order against your own limits, and
replay the odds with a Monte Carlo time machine before risking a rupee. No account, no API key and
no setup needed to start: it reads a free live chart automatically, and every order needs your
explicit yes before anything is recorded.

This is the India (NSE) version of [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill).
The original was built by Jake Schincariol, under the MIT license (the LICENSE file keeps the
original copyright notice exactly as it was); this fork keeps the same eleven skills and the same
pipeline, rebuilt for NSE cash equity, IST sessions, rupees, and India's free public data sources.

**Paper and analysis only.** Nothing in this pack places, modifies, or cancels an order with any
broker, and it never touches funds or positions. Not financial advice. Most people lose money day
trading.

**Disclaimer:** whoever built or modified this takes no responsibility for any loss. The stock
market carries real risk, so plan and invest only after your own careful thought.

> **Read this first:** none of this project's own tests found any rule profitable after costs:
> not ORB (stocks or NIFTY futures), not the 540 candlestick + indicator rules tested. These tools
> are for learning and testing, not a guarantee of making money.

## Contents

- [Install](#install)
- [The eleven](#the-eleven)
- [How to use it](#how-to-use-it)
- [The tools](#the-tools)
- [What changed from the US version](#what-changed-from-the-us-version)
- [Data warning](#data-warning)
- [Fine print](#fine-print)
- [Files](#files)

## Install

Needs Python 3.8+ and nothing else: every tool is the Python standard library, no `pip install`.

- **Paste the link into Claude.** Paste `https://github.com/trmxvibs/day-trader-skill-india` into
  a Claude chat and ask it to install the skill.
- **Claude Code plugin marketplace.** Add this repository as a marketplace, then install the
  plugin it lists:
  ```
  /plugin marketplace add trmxvibs/day-trader-skill-india
  /plugin install day-trader-skill-india@day-trader-skill-india
  ```
  The skills then run under the plugin's namespace, for example `/day-trader-skill-india:trader-scanner`.
- **Copy the folders yourself (global).** Clone the repository and copy every `trader-*` folder
  into `~/.claude/skills/`. The skills then run as plain commands, for example `/trader-scanner`.
- **Copy the folders yourself (one project only).** Copy the same folders into that project's
  `.claude/skills/` folder instead, so they only apply there.
- **Paste a single `SKILL.md`.** Paste one skill's `SKILL.md` into a chat to run it as a mode with
  no Python tools behind it. Works for a quick look, but none of the `python3 -B ...` commands
  below are available that way.

Optional, for better data than the automatic Yahoo fallback: a Dhan Data API key. See
[Data warning](#data-warning).

## The eleven

| Skill | What it does |
| --- | --- |
| `/trader-scanner` | Finds gappers and opening-range breakouts in the Nifty 100, with a traded-value filter |
| `/trader-news` | Google News, ET Markets, Zerodha Pulse, Livemint and NSE announcements; move from previous close, in IST |
| `/trader-entry` | Watches the opening range and tells you when the setup triggered |
| `/trader-exit` | Stop, target and flat-by time, rounded to the NSE tick |
| `/trader-timemachine` | Replays the rest of the session from the stock's own recent bars (10,000 Monte Carlo replays) |
| `/trader-risk` | Checks every order against your own limits: daily loss, trades per day, entry window, F&O block |
| `/trader-budget` | Sizes to whole shares from your rupee max-loss |
| `/trader-broker` | A paper journal on your explicit yes/no, one order at a time |
| `/trader-alerts` | Price and time alerts on the NSE clock |
| `/trader-analyst` | Not applicable (US SEC filings) |
| `/trader-pelosi` | Not applicable (US Congress disclosures) |

## How to use it

The nine skills on the trading path hand data to each other through a `trader/` folder, always in
the same order: scanner finds the setup, news checks there's nothing that should stop you, entry
times the trigger, exit plans the stop and target before you're in, the time machine estimates the
odds, budget sizes the position, risk checks it against your limits, broker records your explicit
yes, and alerts watches the clock after that. A worked example, using the numbers this project's
own docs and tests already use for RELIANCE:

1. **Scanner, 09:30 IST.** `/trader-scanner daily` checks the Nifty 100 against the 15-minute
   opening range and a traded-value floor. RELIANCE is one of the names through: opening range
   high 1185.00, low 1177.40.
2. **News.** `/trader-news` pulls RELIANCE's recent headlines (Google News, ET Markets, Zerodha
   Pulse, Livemint, NSE announcements) in IST. Nothing flagged for today, so the setup still
   stands.
3. **Entry timer.** `/trader-entry check RELIANCE` watches bar by bar. Before the 09:30 five-minute
   bar closes it reports waiting; on the close above the range it reports triggered, at 1186.10.
4. **Exit planner.** `/trader-exit plan` sets the stop at the opening range's midpoint, 1181.20,
   and the target at one times that risk, 1191.00, both rounded to the NSE tick, flat by 15:00 if
   neither is hit first (Dhan's MIS also auto-squares off at 15:10).
5. **Time Machine.** `/trader-timemachine run` replays the rest of the session 10,000 times from
   RELIANCE's own recent bars, with a fixed seed so the run is repeatable. The odds print as a
   plain hit-rate for the target and the stop, not a prediction.
6. **Budget.** `/trader-budget` sizes the trade from a rupee max loss: Rs 500 over a 1181.20 stop
   on a 1186.10 entry works out to 102 whole shares, written as one bracket order with the stop
   and target attached.
7. **Risk.** `/trader-risk` checks that order against your own limits (loss per trade, daily loss,
   trade count, reward-to-risk, entry window, F&O block) and returns PASS or BLOCK with the reason.
8. **Broker.** `/trader-broker show` prints the ticket in plain language. You approve that exact
   order by replying yes; it's a paper journal only, nothing reaches a real broker.
9. **Alerts.** `/trader-alerts` watches the NSE clock for the stop or target and reminds you to go
   flat by 15:00 if the position is still open.

## The tools

Every skill is a standalone script; `${CLAUDE_SKILL_DIR}` is that skill's own folder.

```
python3 -B "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,HDFCBANK,"M&M"
python3 -B "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE HDFCBANK --hours 12
python3 -B "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write
python3 -B "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 2R --write
python3 -B "${CLAUDE_SKILL_DIR}/timemachine.py" run --write
python3 -B "${CLAUDE_SKILL_DIR}/size.py" --write
python3 -B "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-15 09:56"
python3 -B "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan
```

Each skill's own `SKILL.md` has the full command reference, every flag, and what it reads from and
writes to `trader/`. The offline test suite needs no network and no key:

```
python3 -B -m unittest discover -s tests -v
```

268 tests, built on recorded fixtures (real NSE bars, feeds, and index lists under
`tests/fixtures/`), not on live calls.

## What changed from the US version

- **Market:** NSE cash equity, IST. Continuous trading 09:15 to 15:30 (15:15 for Closing Auction
  Session stocks, from 2026-08-03). 2026 NSE holidays, special sessions like Muhurat excluded, NSE
  tick sizes (the stop rounds away from entry).
- **Data:** open source, so no skill's live fetch ships its own data source. Three tiers, always
  tried in this order: **`--offline FILE`** (your own chart data, built from any broker) if you
  give one, otherwise your own **Dhan Data API key** (DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN) if set
  (the better result), otherwise **Yahoo's free chart**, fully automatic, with nothing to set up
  (same as the original pack). The Yahoo tier also prints a warning every time (see
  [Data warning](#data-warning)). The Nifty 100 list comes from NSE's own index files; news comes
  from Google News (India), ET Markets, Zerodha Pulse, Livemint and NSE announcements, all free
  public feeds that need no key.
- **Money:** rupees. Defaults: 15-minute opening range, no entry before 09:30 or after 14:30, flat
  by 15:00 (Dhan's MIS auto-squares off at 15:10 anyway), long only, max loss Rs 500 per trade.
- **Costs:** `backtest\costs.json`: Dhan's intraday brokerage plus NSE/SEBI/government charges,
  with a source URL and the date it was checked, plus 0.03% slippage per side. Matches Dhan's own
  brokerage calculator to within Rs 0.04.
- **Removed:** the PDT rule, the Alpaca / Interactive Brokers order path (trader-broker is now a
  manual paper journal), SEC EDGAR, and the Pelosi tracker (not applicable to NSE trading).

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

**Not financial advice.** Nothing here is a recommendation to buy, sell, or hold anything.

**Every order needs your explicit yes.** `trader-broker` never records a ticket without it, and
it's a paper journal by default: nothing reaches a real broker from this pack.

**Most people lose money day trading.** None of this project's own backtests found a rule
profitable after real costs; treat every number here as a research result, not a strategy to run
unattended.

**The time machine is a simulation, not a prediction.** It replays the stock's own recent bars; it
cannot see news, gaps, or anything that hasn't already happened in that data.

**A stop order can still lose more than planned.** Gaps, halts, and thin liquidity can fill a stop
well past its price; the sizing here assumes the stop fills at or near where it's placed.

**Price data comes from `--offline`, your own Dhan key, or Yahoo's automatic free fallback**, and
all three can be delayed, Yahoo least reliably (see [Data warning](#data-warning)). News feeds come
from free, unofficial public endpoints and can be late or wrong.

**Taxes, margin rules, and your broker's own terms are outside this pack.** Day-trading profit is
typically short-term in India too; talk to a tax professional before you trade, not after.

**This pack is provided as-is: whoever built or modified it takes no responsibility for any loss
from using it. The stock market carries real risk, so plan and invest only after your own careful
thought.**

## Files

| Folder | Files |
| --- | --- |
| `.claude-plugin/` | `plugin.json`, `marketplace.json` |
| `trader-scanner/` | `scan.py`, `SKILL.md` |
| `trader-news/` | `news.py`, `SKILL.md` |
| `trader-entry/` | `entry.py`, `SKILL.md` |
| `trader-exit/` | `exit.py`, `SKILL.md` |
| `trader-timemachine/` | `timemachine.py`, `SKILL.md` |
| `trader-budget/` | `size.py`, `SKILL.md`, `config.example.json`, `targets.example.json` |
| `trader-risk/` | `check.py`, `SKILL.md` |
| `trader-broker/` | `ticket.py`, `SKILL.md` |
| `trader-alerts/` | `alerts.py`, `SKILL.md`, `alerts.example.json` |
| `trader-analyst/` | `facts.py`, `SKILL.md` (not applicable: US SEC filings) |
| `trader-pelosi/` | `ptr.py`, `SKILL.md` (not applicable: US Congress disclosures) |
| `backtest/` | `backtest.py`, `costs.py`, `costs.json` |
| `tools/` | shared blocks, fixture builder, sync scripts |
| `tests/` | 268 tests, fixtures under `tests/fixtures/` |
| `tradingview/` | `orb_nse.pine` |

MIT license: original copyright (c) 2026 Jake Schincariol; the India-version changes are under the
same license.
