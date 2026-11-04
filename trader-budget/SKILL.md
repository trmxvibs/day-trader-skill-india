---
name: trader-budget
description: >-
  Budget Manager for the NSE (India). Sizes a day trade to your budget and
  your max loss per trade, in rupees. Reads the trade plan from trader-exit
  (entry, stop, target), finds the whole shares that fit inside four limits
  (max loss per trade, position cap, max order, cash after your buffer), says
  which limit decided it, and writes one bracket order record to
  trader/orders.json. A paper record only: nothing goes to a broker. A stop too
  far away for your max loss is a plain refusal, not a smaller order. Use when
  the user says "how many shares", "size this trade", "how much should I buy",
  "I have 2 lakh", "I only want to risk 500 a trade", "set my rules", "keep 10%
  in cash", or after /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy RELIANCE at 1186.10, stop 1181.20") into an
exact size ("102 shares"), inside rules the user sets once. The stop decides
the size: the further away the stop, the fewer shares. It writes a proposal
only. trader-risk checks it and trader-broker records the user's decision in a
paper journal. Day trading loses most people money. Sizing to a max loss keeps
each loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/size.py"                      # trader/plan.json + trader/config.json
python3 -B "${CLAUDE_SKILL_DIR}/size.py" --write              # write trader/orders.json
python3 -B "${CLAUDE_SKILL_DIR}/size.py" --limit --write      # a limit order at the plan's entry
python3 -B "${CLAUDE_SKILL_DIR}/size.py" --limit 1187 --json  # a limit order at 1187.00
```

`--now "2026-10-15 09:56"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit.

## What it reads and writes

| File | What |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (the trader-broker paper journal keeps it) |
| `trader/orders.json` | the one proposed bracket order record it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for (example Rs 2,00,000). Only money they
  can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at
  the stop (default Rs 500), before costs.
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest
  position (75% of the budget), the biggest order (Rs 2,00,000) and the
  smallest order (Rs 500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (1,500),
  **max_trades_per_day** (3), **min_reward_risk** (1.0), **require_stop** (true),
  **no_entries_before** / **no_entries_after** ("09:30" / "14:30") and
  **flat_by** ("15:00"), all IST. **mis_square_off** ("15:10") is Dhan's
  intraday auto-square-off time.
- **blocked**: symbols they never want to trade.
- Keep **paper** true. This India version is paper and analysis only.

A config with the US keys (`max_loss_per_trade_usd` and the like) is refused
with the rename to make.

## Step 2: size the trade

Whole shares only: NSE equity takes no fractions. The size is the smallest of
four limits:

| Limit | Shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A lower price never shrinks the risk.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not below
the entry, the target is not above it, the plan is for another day, the symbol
is blocked, or the plan is a short.

## Example: RELIANCE, Thursday 2026-10-15

Entry 1186.10 at 9:55 AM, stop 1181.20 (the opening-range midpoint), target
1191.00 (1R). Risk Rs 4.90 a share. The example config: Rs 2,00,000 budget, 10%
cash buffer, Rs 500 max loss.

| Limit | The math | Shares |
| --- | --- | --- |
| max loss | Rs 500 / 4.90 | **102 (decides it)** |
| position cap | 75% of Rs 2,00,000 = Rs 1,50,000 / 1186.10 | 126 |
| max order | Rs 2,00,000 / 1186.10 | 168 |
| cash | Rs 2,00,000 minus Rs 20,000 = Rs 1,80,000 / 1186.10 | 151 |

102 shares = Rs 1,20,982.20. Max loss at the stop: 102 x 4.90 = Rs 499.80,
before costs: brokerage, STT, exchange charges, SEBI fee, stamp duty and GST add
about Rs 90 on a round trip this size at Dhan's published rates (Rs 40 of it is
brokerage), and slippage comes on top. A stop is an order, not a promise: in a
fast market it can fill below the stop.

## Weights mode

`--targets file` (`targets.example.json` here, an example only: equal weights,
not a recommendation) or `--weights RELIANCE=50,TCS=50` splits a budget across a
list by weight, in whole shares, with prices (`--prices trader/prices.json`).
These are plain buys with no stop: trader-risk blocks them while
`require_stop` is true.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and
  the max loss.
- Every number comes from the config, the plan or the prices. Nothing is
  guessed.
- No projections: never say what a trade will make.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes options, margin
or short positions.

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
