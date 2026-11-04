---
name: trader-exit
description: >-
  Exit Planner for the NSE (India). Sets your stop and your target before you
  enter, so you know when to get out. Reads the entry from trader-entry (or a
  price you give), puts the stop at the opening-range midpoint by default (or
  the range low, your price, a percent, or a multiple of the ATR), rounds it
  down to the NSE tick, and sets the target at 1R (or 1.5R, 2R, or your
  price), in rupees. Refuses a stop at or above the entry, a target at or
  below it, or a stop too tight to survive the spread, and says why. Writes
  trader/plan.json for the time machine, the budget manager and price alerts.
  Long side only. Use when the user says "where's my stop", "set my stop and
  target", "plan the exit", "when do I get out", "stop loss", "what's my R",
  "risk reward", or right after /trader-entry triggers, before
  /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 1R|2R|price:X]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both are
set before the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a gap
or a fast market can fill past it. Day trading loses most people money, and a
neat plan does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/exit.py" plan                  # trader/entry.json: midpoint stop, 1R target
python3 -B "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 2R --write
python3 -B "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5   # needs the session's bars (--offline, Dhan key, or Yahoo automatically)
python3 -B "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1186.10 --or-high 1185 --or-low 1177.40
python3 -B "${CLAUDE_SKILL_DIR}/exit.py" show                  # the current trader/plan.json
```

Other flags: `--flat-by 14:45`, `--tick 0.05`, `--json`, `--now "2026-10-15 09:56"`
(IST), `--entry PRICE` with a waiting entry.json to plan ahead, `--setup level`,
`--offline FILE` (a saved chart JSON for an ATR stop). With no `--offline`
file, an ATR stop uses your own Dhan Data API key when it finds one, and
otherwise falls back automatically to Yahoo's free chart, with a warning (see
"## Data warning" below). Nothing to set up either way.

## What it reads and writes

| File | What |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:00), `target` (1R), `mis_square_off` (15:10) and `min_reward_risk` (1.0: a note here, trader-risk enforces it) |
| only for an `atr:K` stop: the session's bars up to the entry bar | `--offline FILE`, else your own Dhan Data API key (`DHAN_CLIENT_ID`/`DHAN_ACCESS_TOKEN`), else Yahoo's free chart, automatically |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by, tick. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| Stop | Where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar (--offline, Dhan, or Yahoo automatically). With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **The tick.** NSE prices move in ticks that depend on the price band (NSE
  circular 33/2025): below Rs 250 Rs 0.01, Rs 250 to 1,000 Rs 0.05, above
  1,000 to 5,000 Rs 0.10, to 10,000 Rs 0.50, to 20,000 Rs 1.00, above that Rs
  5.00. The NSE sets a stock's tick each month from the previous month's last
  close; this tool uses the entry's band unless you pass `--tick`.
- **Rounding.** The stop rounds to the tick away from the entry (down, for a
  long). Risk = entry minus stop. Target = entry + R x risk, rounded to the
  tick toward the entry, so it never promises more than the R you asked for
  (a 1.5R target can come out at 1.49R, and the label says so). `1R` by
  default; `1.5R`, `2R` or `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price (or under one tick): too tight to
  survive the spread, normal wiggle would stop you out. An entry.json that has
  not triggered (unless you give `--entry`). A short: longs only.
- **Flat by.** 15:00 IST by default (or `flat_by` in the config). Out by then,
  win or lose, no overnight hold. It must come before continuous trading ends
  (15:15 for Closing Auction Session stocks from 2026-08-03, else 15:30). Dhan
  squares off intraday (MIS) equity positions at 15:10 on its own, for a fee:
  a flat-by at or after `mis_square_off` (15:10) gets a note. An entry at or
  after the flat-by time is refused.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule, the
   target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther target).
   Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time.

## Example: RELIANCE, Thursday 2026-10-15

The entry is 1186.10 at 9:55 AM (the 9:50 bar, bar 7) and the opening range is
1177.40 to 1185.00. The price is in the Rs 1,000 to 5,000 band, so the tick is
Rs 0.10. `exit.py plan --write`:

```
Exit plan: RELIANCE long, Thu 2026-10-15, opening-range breakout on 5-minute bars
  Entry         Rs 1,186.10 at 9:55 AM (bar 7)
  Stop          Rs 1,181.20  opening-range midpoint: (Rs 1,185.00 + Rs 1,177.40) / 2 = Rs 1181.2
  Target        Rs 1,191.00  1R (one times the risk)
  Risk a share  Rs 4.90 (0.41% of the entry)
  Reward:risk   1.0
  Flat by       15:00 IST, win or lose: no overnight hold (intraday, MIS)
```

A 2R target would be 1186.10 + 2 x 4.90 = Rs 1,195.90. trader-budget then
sizes it: a Rs 500 max loss per trade buys floor(500 / 4.90) = 102 shares,
before costs.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It never
  prints a P&L or a return.
- Nothing here promises winning trades or money. A 1R target is not a
  prediction that the price gets there.
- Longs only for the live tools.

## What it never does

It never places an order, never sizes a position, never widens a stop to make
a plan pass, and never calls a stop a guarantee.

## Data warning

This pack is open source: it brings no data source of its own, so it tries, in order (only
for an `atr:K` stop, which is the only thing here that needs bars):

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
