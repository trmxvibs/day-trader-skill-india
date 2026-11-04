---
name: trader-risk
description: >-
  Risk Manager for the NSE (India) version. Checks every proposed trade
  against your limits, in rupees, and stops anything over them before it can
  reach the paper journal. For a day trade: a stop is required, the loss at
  the stop must fit your max loss per trade (measured from the higher of the
  plan's entry and a fresh price), reward:risk must meet your minimum, the
  day's total risk must fit your daily loss limit, no more than your trades
  per day, and no new entries outside your entry window (09:30 to 14:30 IST
  by default) or when NSE is closed. It also keeps the basics: blocked
  symbols, order size, position cap, cash buffer, margin, short sales, F&O
  contracts, whole shares only, malformed or bad bracket orders, duplicates
  and a daily order limit. Getting out is never blocked by a day limit.
  Prints PASS or BLOCK with the reason and records the verdicts so
  trader-broker only shows an order that passed. Use when the user says
  "check my order", "is this safe", "risk check", "does this break my
  rules", "can I take this trade", "how much have I risked today", or before
  /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the paper journal. Every
order written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it does not go to the user for a yes. Most
people who day trade lose money. These limits keep each loss and each day's
losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 -B "${CLAUDE_SKILL_DIR}/check.py" --json
python3 -B "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 -B "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-15 09:56"  # pin the clock (IST)
python3 -B "${CLAUDE_SKILL_DIR}/check.py" --orders other.json
```

## What it reads and writes

| File | What |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits in rupees (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings of the paper account; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings |
| `trader/state/broker.json` | the paper journal: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

A config with the US keys (`max_loss_per_trade_usd` and the like) is refused
with the rename to make.

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, loss, reward:risk, blocked
list, size, duplicates) turns into a warning for an exit, never a BLOCK.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| Check | Blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, no whole-share qty (NSE equity trades whole shares, so a notional order is refused), a limit order with no limit price; a bracket that is not `day` or misses its stop-loss or take-profit |
| asset | an NSE F&O contract (for example `NIFTY26NOVFUT` or `RELIANCE26NOV1200CE`) while `allow_options` is false; anything that is not an NSE equity symbol |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need margin |
| stop | (entries) no stop while `require_stop` is true; a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already in the journal today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or NSE is closed |
| duplicate | (entries) the same symbol and side twice in one batch, or already in the journal today |
| daily limit | (entries) `max_orders_per_day` orders already in the journal today |

How the numbers work:

- **The entry it measures from**: the limit price for a limit order. For a
  market order, the HIGHER of the plan's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old). A plan written at 09:55 cannot
  understate the risk at 10:30 after the price ran up. An older price is not
  used, and it says so.
- **Daily loss** counts the full max loss of every entry in today's paper
  journal, win or lose. Three full-size trades use up a Rs 1,500 day.
- **The window** is IST, worked out in the tool, never from the computer's
  clock zone. Weekends, the 2026 NSE trading holidays and special sessions
  (Muhurat) are closed; continuous trading ends at 15:15 for Closing Auction
  Session stocks from 2026-08-03. Set both `no_entries_*` to null to switch
  the window off.
- There is no pattern-day-trader rule: that is a US (FINRA) rule and does not
  apply on NSE.
- Costs are not in the max loss. Dhan's brokerage plus STT and the other
  charges add about Rs 90 to a Rs 1.2 lakh round trip (`backtest/costs.json`).

Warnings do not block, but say them to the user: a number sent as a number
instead of a string, a stop that lives only in the plan, a price too old to
use.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop, reward:risk, and how much of today's limits it uses. For a
   BLOCK, give the reason and the fix (fewer shares, a closer stop from
   trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint, so an edited order is not ready
   until it passes again, and a verdict from yesterday does not count today.

## Example: RELIANCE, Thursday 2026-10-15

The RELIANCE bracket from trader-budget at 09:56 IST, on a fresh Rs 2,00,000
paper account with the example config:

```
PASS  #1 buy RELIANCE 102 shares, entry
          entry 1186.10 (the plan's entry), stop 1181.20, target 1191.00: risk 4.90 a share
          max loss Rs 499.80 (limit Rs 500), reward:risk 1.00 (min 1)
          today before this: 0 of 3 trades, Rs 0 of Rs 1,500 daily loss

1 passed, 0 blocked at 2026-10-15 09:56 IST. Cash after the passed orders: Rs 79,017.80 (paper account).
```

One more share and it is blocked:

```
BLOCK #1 buy RELIANCE 103 shares, entry
        - loss: 103 shares x 4.90 risk a share = Rs 504.70 at the stop, over your Rs 500 max loss per trade
```

At 09:20 the same order is blocked by the window (no new entries before
09:30 IST). The 14:55 sell that closes the position passes: it is an exit.

## Rules

- A PASS only means the order may be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the paper journal.
- Free Yahoo prices are unofficial and can lag, and a stop can fill worse than its price in a
  fast market.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

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
