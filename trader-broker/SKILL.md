---
name: trader-broker
description: >-
  Paper Journal for the NSE (India) version. Records your decision on each
  proposed order: shows each order as a plain ticket in rupees (a bracket shows
  its stop-loss, take-profit and the max loss at the stop), records your yes
  word for word as a paper "placed" record, records a no, records paper fills,
  builds the sell ticket that closes a day trade by your flat-by time, and
  logs each trade's max loss so trader-risk can hold the daily limit. Nothing
  is sent to any broker: there is no broker connection in this version. Use
  when the user says "record it", "I'd take this one", "log my trade", "paper
  trade it", "close my position", "flatten", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

In the original US pack this skill could lead to a real order through a broker
connection. The India version removes that path completely: it is a paper
journal. Nothing is sent to any broker, nothing touches funds or positions,
and there is no order tool to approve. What stays is the discipline: one order
at a time, an explicit yes for that one order, and a log that trader-risk reads
so the daily limits hold.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 -B "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 -B "${CLAUDE_SKILL_DIR}/ticket.py" decline --index 1
python3 -B "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side buy --qty 102 --price 1186.10
python3 -B "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE
python3 -B "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`--now "2026-10-15 09:56"` pins the clock (IST).

## What it reads and writes

| File | What |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | the paper journal: every yes (as a paper "placed" record), no and fill, with each entry's max loss |
| `trader/portfolio.json` | the paper account: cash and shares, updated by `fill` |
| `trader/plan.json` | the flat-by time for `flatten` |

## Steps

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY goes back to `/trader-risk`.
2. Show one ticket and ask: "record this order in the paper journal? reply yes
   or no." One order per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this order
   (for example "sure", "yes to all", or a yes that names another symbol like
   HDFCBANK or M&M, or another amount). Ask again. Never turn a reply into a
   yes yourself.
4. A paper fill, if the user wants one: `ticket.py fill ...` with the price the
   user names.
5. By `flat_by` (15:00 IST), anything still open in the paper portfolio gets a
   flatten ticket: `ticket.py flatten --symbol SYMBOL`, then `/trader-risk` (a
   sell that only closes what is held is never blocked by the daily limits),
   then `show` and the user's yes. On a real Dhan account the broker squares
   off intraday (MIS) equity positions itself at 15:10, for a fee.

## Example: RELIANCE, 2026-10-15

```
ORDER 1 of 1, PAPER JOURNAL (India version: nothing is sent to any broker)
  BUY 102 RELIANCE, market, good for today
  stop-loss 1181.20
  take-profit 1191.00
  max loss about Rs 499.80 if the stop fills at 1181.20 (it can fill worse in a fast market)
  READY: trader-risk PASS, waiting for your yes
  Reply "yes" to record this one order in the paper journal, or "no" to skip it.
```

The user replies "yes". `approve` records it as a paper "placed" record with
the max loss, and trader-risk then counts it: the same order again is a
duplicate today, and it uses one of the day's three trades.

## What it never does

- Never sends anything to a broker, never asks for or handles passwords, PINs,
  TOTPs, OTPs or API keys.
- Never records two orders on one yes.
- Never runs from a scheduled or background task: nobody is there to say yes.
- Never promises a fill price.

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
