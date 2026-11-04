---
name: trader-entry
description: >-
  Entry Timer for the NSE (India). Watches a setup bar by bar and tells you
  the moment to get in. Reads 1-minute or 5-minute bars for NSE stocks
  (RELIANCE, M&M, BAJAJ-AUTO): a saved file with --offline, else your own Dhan
  Data API key, else Yahoo's free chart automatically. Builds the opening
  range (the first 15 minutes from 9:15 IST by default) and calls the entry on
  the first completed bar that CLOSES above the range high, or above a level
  you give it, with an optional volume check. Says waiting and how far,
  triggered with the time and the price in rupees, expired, or not open yet,
  and writes trader/entry.json for /trader-exit. Long side only. It never
  places an order. Use when the user says "tell me when to get in", "watch
  RELIANCE", "has it broken out", "did it break the opening range", "entry
  signal", "time my entry", "check my watchlist", or after /trader-scanner
  and /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not an entry. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything. Day trading loses most
people money, and a clean signal does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE                # 15-minute opening range, 5-minute bars
python3 -B "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write        # also save trader/entry.json
python3 -B "${CLAUDE_SKILL_DIR}/entry.py" check HDFCBANK --setup level --level 690.50
python3 -B "${CLAUDE_SKILL_DIR}/entry.py" check "M&M" --interval 1m --min-volume-x 1.5
python3 -B "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:30  # one line per closed bar, stops on the trigger
python3 -B "${CLAUDE_SKILL_DIR}/entry.py" watchlist                     # every hit in trader/watchlist.json
```

Other flags: `--or-minutes N` (default 15), `--json`, `--offline FILE` (a
saved chart JSON, `.json` or `.json.gz`, or a folder of `<SYMBOL>.json`
or `<SYMBOL>.NS.json`), `--now "2026-10-15 09:55"` (IST) to replay a day.
`watch` with `--now` or `--offline` runs on a simulated clock and replays the
day bar by bar without waiting. With no `--offline` file, this skill uses
your own Dhan Data API key when it finds one (`DHAN_CLIENT_ID`/
`DHAN_ACCESS_TOKEN`), and otherwise falls back automatically to Yahoo's free
chart, with a warning (see "## Data warning" below). Nothing to set up
either way, so the skill always has somewhere to read bars from.

## What it reads and writes

| File | What |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (14:30), IST. Optional |
| today's 1m/5m bars for `SYMBOL` | `--offline FILE`, else your own Dhan Data API key (`DHAN_CLIENT_ID`/`DHAN_ACCESS_TOKEN`), else Yahoo's free chart, automatically |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, setup, status, time, bar, price, level, opening range |

## The market

- **NSE cash market, IST.** IST is UTC+05:30 all year (no daylight time).
- **Session.** Continuous trading 9:15 to 15:30. From 2026-08-03 the NSE runs a
  Closing Auction Session for F&O stocks, so their continuous trading ends at
  15:15 (NSE circular 108/2026). The 9:00 to 9:15 pre-open auction is not part
  of the session, and any 15:15 to 15:25 stamps a chart feed sends on those
  days are auction artifacts, not bars.
- **Closed days.** Weekends, the NSE trading-holiday list (NSE circular
  172/2025 plus 07/2026) and special sessions such as Muhurat trading.

## The rule

- **Opening range (orb).** The high and low of the first 15 minutes: the
  9:15, 9:20 and 9:25 five-minute bars, or fifteen 1-minute bars. The level to
  beat is the range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 9:50 bar closes at 9:55 AM). The
  price is its close in rupees, on the exchange's tick grid (a chart feed's
  float noise like 1186.0999755 is read as Rs 1186.10).
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and the data source has caught up to that moment (it
  waits a minute at most). A live tick, like 10:29:45, is never a bar.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the average of the range bars that report volume.
  The 9:15 bar can show 0 volume for NSE, so that bar is left out of the
  average. NSE volume can also differ between two readings: treat volume
  checks as rough.
- **Time limits.** No entries before 9:30 or after 14:30 (config), never later
  than the end of continuous trading.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. Before 9:30: the
  range is still forming. Weekends, NSE holidays and special sessions: the
  market is closed.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line: waiting
   and how far, triggered at what time and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 14:30` in the
   background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes, prints one line per bar,
   and exits 0 on the trigger (it writes `trader/entry.json`) or 1 when the
   setup expires. Keep the computer awake while it watches: a sleeping laptop
   pauses it, and it catches up, late, when it wakes.
4. When it triggers, say the time and the price, and that nothing was placed.
   If the trigger is more than a bar old, say that too: a late entry is a
   different trade.
5. Hand off to `/trader-exit` to set the stop and the target before anything
   is bought.

## Example: RELIANCE, Thursday 2026-10-15

5-minute bars. The opening range is 1177.40 to 1185.00. At 9:54 the 9:50 bar is
still open, so it waits:

```
WAITING  last close Rs 1181.00 (9:45 bar), Rs 4.00 under the range high Rs 1185.00 (-0.34%).
The 9:50 bar is still forming (Rs 1186.10 so far, closes 9:55 AM); it does not count until it closes.
```

At 9:55 the bar has closed:

```
RELIANCE  Thu 2026-10-15 09:55 IST  opening-range breakout (15-minute range) on 5-minute bars (Dhan Data API)
Opening range 9:15-9:30: high Rs 1185.00, low Rs 1177.40 (3 of 3 bars).
TRIGGERED 9:55 AM at Rs 1186.10: the 9:50 bar (bar 7) closed above the range high Rs 1185.00 (+0.09%).
This is the moment to get in, if you take the trade. Nothing was placed.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json).
```

With `--min-volume-x 1` the 9:50 bar is a little thin (0.98x the range's
volume) and the trigger moves to 10:10 AM at Rs 1194.70 (the 10:05 bar, 1.86x).
That day also shows two real data quirks: the 11:45 and 11:50 bars are missing
on every NSE symbol, and the 15:15 to 15:25 stamps are Closing Auction Session
artifacts.

## Rules

- Bar by bar, on 1-minute or 5-minute bars (--offline file, your own Dhan
  Data API key, or Yahoo's free chart automatically). A paid, delayed, or free
  feed can lag or be wrong. Say so when a minute matters.
- A signal is information, not an order, and not a promise. Nothing here
  promises winning trades or money.
- Longs only for the live tools. If the user wants a short, say that.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call an entry early because a bar looks like it will close above.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed.

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
   key, nothing to set up, so the skill never hard-fails for a user with no broker. A direct
   check against Dhan's own chart found Yahoo's
   5-minute NSE bars miss part of the price range (the 09:15 bar's high short by up to
   Rs 20.50, the day's high or low missing on 31-37% of days). This skill prints a warning to
   that effect every time it falls back to this tier.

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
