---
name: trader-alerts
description: >-
  Price alerts and the NSE clock (India). Rings when a price, a stop or the
  opening bell hits. Turns the trade plan into three alerts in one go (the
  stop, the target and "flat by" at your flat-by time, 15:00 IST by default),
  plus above / below / stop / target levels, percent moves from the previous
  close, any time of day, and the NSE opening bell at 09:15, each ringing once.
  Knows NSE hours (continuous trading to 15:15 for Closing Auction Session
  stocks), the 2026 NSE holidays and special sessions, works in IST, fetches
  delayed quotes one symbol at a time (your own --offline file, else the Dhan
  Data API, else Yahoo's free chart automatically), and writes
  trader/prices.json for sizing and the risk check. Use when the user says
  "alert me when", "tell me if RELIANCE hits", "watch my stop", "remind me to
  close by 3 PM", "is the market open", "get me a price", or right after
  /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price alerts watch the numbers so the user does not have to stare at a chart.
An alert is information, never an order, and nothing in this India version
touches a broker. When a stop alert rings, the user decides what to do and can
record it in the paper journal.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" clock                     # NSE open or closed, next open
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan           # stop, target, flat by from trader/plan.json
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1181.20
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:00 --label "flat by"
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" check                     # exit 0 = something rang
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write    # -> trader/prices.json
python3 -B "${CLAUDE_SKILL_DIR}/alerts.py" list
```

`--now "2026-10-30 15:01"` pins the clock (IST); `--offline DIR` reads saved
quote files (`yahoo-SYMBOL.json`, the legacy filename every fixture here
uses) instead of the network. With no `--offline`, this skill uses your own
Dhan Data API key when it finds one, and otherwise falls back automatically
to Yahoo's free chart, with a warning once a call (see "## Data warning"
below). Nothing to set up either way.

## What it reads and writes

| File | What |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| the latest quote for each symbol | `--offline FILE`, else your own Dhan Data API key (two calls per symbol: today's bars, previous close), else Yahoo's free chart, automatically |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held in the paper portfolio, for `quote --holdings` |

## The NSE clock

Continuous trading 09:15 to 15:30 IST, and to 15:15 for Closing Auction Session
stocks from 2026-08-03 (NSE circular 108/2026). The 09:00 to 09:15 pre-open
auction is not open for these tools. Closed on weekends, on the 2026 NSE
trading holidays (NSE circulars 172/2025 and 07/2026: Jan 15, Jan 26, Mar 3,
Mar 26, Mar 31, Apr 3, Apr 14, May 1, May 28, Jun 26, Sep 14, Oct 2, Oct 20,
Nov 10, Nov 24, Dec 25) and on special sessions such as Muhurat trading (Sun
Nov 8). IST is UTC+05:30 all year. For any other year it says the holiday list
is unknown.

## Quotes

`--offline` bars you read off your own broker's chart if given; else two Dhan
Data API calls per symbol (today's intraday bars for price/open/high/low, the
previous daily bar for previous close), half a second apart, if you have a
Dhan key; else one Yahoo chart call per symbol, automatically, with a
warning (see "## Data warning" below). Quotes are delayed and can lag: fine
for alerts and for checking a size, not for timing an order. Each price in
`trader/prices.json` keeps its quote time, and trader-risk only uses one
under 15 minutes old.

## Example

The plan: RELIANCE on Fri 2026-10-30, stop 1165.00, target 1175.00, flat by
15:00. `add --from-plan` writes three rules for that day. A `check` at 14:00
with the 1164.90 quote:

```
NSE OPEN (2026-10-30 14:00 IST, continuous trading).
ALERT RELIANCE is Rs 1164.90: at or below your stop Rs 1165 (plan stop).
```

At 15:01 the flat-by rule rings once: continuous trading ends at 15:15, and
Dhan squares off intraday (MIS) equity positions at 15:10 on its own.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live.
- Confirm every new rule back to the user: symbol, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is live,
and never predicts where a price is going.

## Data warning

This pack is open source: it brings no data source of its own, so it tries, in order:

1. **`--offline FILE`,** if you pass it: a chart JSON you made yourself, by reading your own
   broker's chart (any broker) and typing or scripting the bars in. Always wins when given.
2. **Your own Dhan Data API key,** if you have set one up. Needs your own Dhan trading
   account plus the Data API add-on (a paid monthly subscription at the time of writing) and
   an access token from web.dhan.co (My Profile > DhanHQ Trading APIs). Set `DHAN_CLIENT_ID`
   and `DHAN_ACCESS_TOKEN` (environment variables), or `trader/broker.json:
   {"dhan_client_id": "...", "dhan_access_token": "..."}`. See `tools/dhan_block.py` for the
   exact setup and the API docs it links to. Usually the most reliable of the three.
3. **Yahoo's free chart, automatically,** if neither of the above applies: no account, no
   key, nothing to set up. A direct check against Dhan's own chart found Yahoo's 5-minute NSE bars miss part of
   the price range (the 09:15 bar's high short by up to Rs 20.50, the day's high or low
   missing on 31-37% of days). This skill prints a warning to that effect once a call, not
   once a symbol.

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
