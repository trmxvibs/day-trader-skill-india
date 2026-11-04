---
name: trader-timemachine
description: >-
  Time Machine for NSE stocks (India). Replays the possible outcomes of a
  trade before you risk a rupee: 10,000 simulated sessions plus every past
  time the setup fired. Takes the plan from trader-exit (entry, stop,
  target), rebuilds the rest of the day 10,000 times from the stock's own
  5-minute bars over the last 60 days (IST, NSE continuous trading only), and
  re-runs the same rule on every past session. Prints the odds of target
  first, stop first, or neither by your flat-by time, where the price sits
  then, the average R, the history, and one honest verdict, then hands off to
  /trader-budget. Use when the user says "run the time machine", "replay this
  trade", "what are the odds", "simulate this trade", "monte carlo", "how
  often does this hit the target", "is this a coin flip", or after
  /trader-exit writes a plan, before /trader-budget.
argument-hint: "[path to plan.json, or SYMBOL]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. trader-exit has set the
entry, the stop and the target. Before anything is sized, this skill asks one
question: if today plays out like the stock's own last 60 days, how often does
this plan hit the target first, the stop first, or neither? It answers with
10,000 replays and with the real record of the same rule on every past
session. It is a simulation built from the stock's own recent bars, not a
prediction. Costs are not in these numbers. Day trading loses most people
money, and a good-looking replay does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays, seed 19
python3 -B "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader/timemachine.json
python3 -B "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:00      # end each replay at this IST time
python3 -B "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 -B "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-15 \
    --entry 1186.10 --stop 1181.20 --target 1191.00 --entry-time "9:55 AM"
python3 -B "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved chart JSON, `.json` or `.json.gz`),
`--now "2026-10-15 09:56"` (IST) to pin the clock. With no `--offline`, this
skill uses your own Dhan Data API key when it finds one, and otherwise falls
back automatically to Yahoo's free chart, with a warning (see "## Data
warning" below). Nothing to set up either way.

## What it reads and writes

| File | What |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit: symbol, date, entry, entry_time / entry_bar, stop, stop_rule, target, target_r, opening range, flat_by |
| one call: 60 days of 5-minute bars for `SYMBOL` | `--offline FILE`, else your own Dhan Data API key (`DHAN_CLIENT_ID`/`DHAN_ACCESS_TOKEN`), else Yahoo's free chart, automatically |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict. `mc` holds the odds, percentiles, the fan-chart bands, 160 sample paths and a 24-bin histogram of the price at the end |

## How it works

1. **The pool.** It keeps only FULL sessions before the plan's date with the
   same number of continuous-trading bars as the plan's day: 72 five-minute
   bars (09:15 to 15:15 IST) for Closing Auction Session stocks from
   2026-08-03, 75 bars (to 15:30) before that. The 09:00 to 09:15 pre-open and
   the closing session are not continuous trading and are left out, as are
   gaps, holidays, Muhurat and today. Every bar becomes four ratios to the bar
   before it: open, high, low and close over the previous close.
2. **One replay.** Start at the entry price. For each bar left in the day,
   pick a past bar from the same time of day (plus or minus 2 bars) at
   random, and scale it to the current price. Bar by bar, it checks the stop
   first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at `--until` (default: the plan's
   flat-by time, 15:00 IST in the example config, else the end of continuous
   trading). Green, which means target first or above the entry at `--until`.
   R is +1 at a 1R target, -1 at the stop, and otherwise the distance from
   the entry at `--until` divided by the risk. A gap through the stop still
   counts as -1R here. A real fill can be worse.
4. **The history.** The plan's own rule (the same opening range, stop rule
   and target R, entries only inside the 09:30 to 14:30 window, stops and
   targets rounded to the NSE tick) runs again on every one of those past
   sessions. That is what really happened, not a simulation.
5. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side, and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them.
6. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither (the price at `--until` decides those), and the
   average R when its sign goes against the lean.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

**5-minute bars only.** This tool pools 60 days of 5-minute sessions for the
replay, not 1-minute ones. It refuses, in one plain line, when there are
fewer than 20 full sessions, when the entry bar is
not in the data (wrong date, not closed yet, a future day), when the date is
not an NSE trading day, or when the plan does not make sense (a stop above a
buy's entry, an entry off the 5-minute grid). A live in-progress bar is never
used.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`. If there
   is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write`. With a plan from trader-exit, "neither"
   already means still open at the flat-by time.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at the end, the average R, the history line, then the
   verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do
   not dress it up. If the history and the replays disagree, say that too.
   Costs (about Rs 90 on a Rs 1.2 lakh round trip at Dhan, plus slippage)
   come on top, so a coin flip before costs loses after them.
5. Say once that this is built from the last 60 days and is not a prediction.
   Results, news and circuits can do things those sessions never did. Check
   `/trader-news` for the stock before going on.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Example: RELIANCE, Thursday 2026-10-15

The 15-minute opening range (09:15 to 09:30 IST) breaks on the 09:50 bar:
entry 1186.10 at 9:55 AM (bar 7), stop 1181.20 (the range midpoint, rounded
down to the tick), target 1191.00 (1R, risk 4.90 a share), flat by 15:00.

```
10,000 replays of the rest of the session, 9:55 AM to 3:00 PM (61 five-minute bars), seed 19.
Each bar is drawn whole from RELIANCE's own 5-min bars at the same time of day (+-2 bars) over its last 49 full sessions (2026-08-05 to 2026-10-14).

  target first   45.4 %
  stop first     50.8 %
  neither        3.9 %    still open at 3:00 PM
  green          47.3 %   target first, or above the entry at 3:00 PM

  Price at 3:00 PM: median 1185.45, 90 % of replays between 1171.71 and 1199.31.
  Average R: -0.055 (R = the 4.90 risk: +1R at the target, -1R at the stop).

History: the same rule (15-min opening range, stop at the opening-range midpoint, 1R target) on each of those 49 real sessions fired 22 times: 11 target, 9 stop, 2 neither. Average R +0.086.

Close to a coin flip: target first 45.4 % vs stop first 50.8 %.
```

At 1R the target has to come first in half of the replays that hit one side.
It did in 47.2 %. The replays came out close to a coin flip and slightly
negative; the real history was slightly positive, on only 22 trades and
before costs. That is the honest answer for this setup, and it is what the
user should hear before `/trader-budget` sizes 102 shares.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them. Never judge a 2R or 3R plan by target first vs
  stop first alone: the verdict already weighs the target's R.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Your own Dhan Data API key (or an --offline file, or the automatic Yahoo
  fallback) can lag. Bars are 5-minute bars, checked bar by bar, never tick by
  tick and never real time.
- Always say the seed, so any run can be repeated.
- One plan per run. A changed entry, stop, target or date is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. It never prints a P&L or a profit.
It never uses a bar that has not closed yet, and never mixes today's bars
into the pool.

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
