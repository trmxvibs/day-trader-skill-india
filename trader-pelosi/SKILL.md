---
name: trader-pelosi
description: >-
  NOT APPLICABLE in the NSE (India) version. The original US Pelosi Tracker
  read the House Clerk's STOCK Act trade disclosures by members of Congress.
  India has no counterpart that lists politicians' share trades, so this
  skill does nothing here: its tool prints a "not applicable" message and
  exits. Use when the user asks about "Pelosi trades" or "politician trades"
  in this pack, to explain that.
argument-hint: ""
---

# trader-pelosi (not applicable to NSE)

The US pack's Pelosi Tracker read Periodic Transaction Reports filed under the
US STOCK Act. There is no Indian counterpart, so in this India version the
tool is a stub:

```bash
python3 -B "${CLAUDE_SKILL_DIR}/ptr.py"     # prints "not applicable", exit code 3
```

The original is kept unchanged in `original/` (the upstream repository).

## Not the same thing

Insider trades by an NSE company's own promoters, directors and employees are
disclosed under the SEBI (Prohibition of Insider Trading) Regulations and are
published on nseindia.com. That is a different data set, and this pack does
not read it. Never present it as "what politicians are buying".

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
