---
name: trader-analyst
description: >-
  NOT APPLICABLE in the NSE (India) version. The original US Earnings Analyst
  read a company's numbers from SEC EDGAR (10-Q, 10-K and the earnings 8-K).
  Indian companies do not file with the SEC, so this skill does nothing here:
  its tool prints a "not applicable" message and exits. Use when the user asks
  "analyze the earnings" or "check the 10-Q" for an NSE stock, to tell them
  where Indian results are published instead.
argument-hint: "[symbol]"
---

# trader-analyst (not applicable to NSE)

The US pack's Earnings Analyst read XBRL company facts from SEC EDGAR. NSE
companies do not file with the SEC, so in this India version the tool is a
stub:

```bash
python3 -B "${CLAUDE_SKILL_DIR}/facts.py"     # prints "not applicable", exit code 3
```

The original is kept unchanged in `original/` (the upstream repository).

## What to tell the user instead

- Quarterly results of an NSE company are in its corporate announcements on
  nseindia.com (Corporate filings, Financial results) and on the company's
  investor relations page. Read the filed results, not a preview headline.
- The results date (board meeting) is announced there too. If it falls inside
  a planned trade, say so: the stock can gap at the open.
- /trader-news shows headlines about results; they are leads, not facts.

Do not compute margins or growth from memory or from headlines.

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
