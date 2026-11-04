---
name: trader-news
description: >-
  News Monitor for NSE stocks (India). Tracks headlines on every symbol you
  watch or hold and shows how far each stock is from its previous close:
  Google News headlines (India edition) per symbol, the ET Markets, Zerodha
  Pulse and Livemint market feeds, NSE's corporate announcements, and the
  move since the previous close (an --offline file if given, else your own
  Dhan Data API key, else Yahoo's free chart automatically). Shows only what
  is new since the last check, newest first, in
  IST, tags each item (earnings, analyst, deal, capital, halt or circuit,
  regulatory, macro...) and never repeats one. Use when the user says "any
  news on RELIANCE", "why is this moving", "what's the news on my watchlist",
  "morning briefing", "pre-open news", "monitor the news", "did anything
  break", or after /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on a results date, a SEBI
order or a circuit. Before a setup becomes a trade, this skill reads the
headlines for every stock on the watchlist and tells you only what is new. It
also prints how far each stock has moved today, so a headline and a move sit
side by side.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows use `python -B` (or `py -B`) instead of `python3`.

```bash
python3 -B "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 -B "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE HDFCBANK --hours 12
python3 -B "${CLAUDE_SKILL_DIR}/news.py" --json --dry-run
```

`--sources google,et,pulse,mint,nse` picks the feeds (all by default). `--now`
pins the clock; `--offline DIR` reads saved `gnews-SYMBOL.xml`,
`feed-et.xml` (and pulse, mint, nse) and `chart-SYMBOL.json` files (the legacy
Yahoo-shaped layout every fixture here uses) instead of the network. With no
`--offline`, the price-move line uses your own Dhan Data API key when it
finds one, and otherwise falls back automatically to Yahoo's free chart, with
a warning once a call (see "## Data warning" below). Nothing to set up
either way.

## What it reads and writes

| File / feed | What |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what the paper portfolio holds (trader-broker) |
| Google News RSS, India edition | `https://news.google.com/rss/search?q="RELIANCE" NSE share&hl=en-IN&gl=IN&ceid=IN:en`, one request per symbol |
| ET Markets stocks RSS | `https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms` |
| Zerodha Pulse | `https://pulse.zerodha.com/feed.php` (an aggregator of Indian business news) |
| Livemint markets RSS | `https://www.livemint.com/rss/markets` |
| NSE corporate announcements | `https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml` |
| `trader/universe.txt` | company names (`SYMBOL|Name`, trader-scanner writes it), so a story that names "Reliance Industries" counts for RELIANCE |
| the price-move line for each symbol | `--offline FILE`, else your own Dhan Data API key (two calls per symbol: today's bars, previous close), else Yahoo's free chart, automatically |
| `trader/state/news.json` | what has been shown (kept 30 days) |

Yahoo's own headline feed returns nothing for NSE symbols, so headlines come
from Google News, an aggregator: each item names its publisher (Moneycontrol,
Reuters, Livemint...). The search matches the symbol as text, so a story about
a different company with a similar name (Reliance Power for RELIANCE) can show
up. Read the title before counting it.

The market feeds are fetched once per check, not per symbol, and an item is
kept only when it names one of your symbols (in capitals, like `ITC` or
`M&M`) or companies (case-sensitive, without "Ltd."). `BSE` is also the
exchange's name, so for BSE Ltd. only `NSE: BSE` or `(BSE)` counts.
Moneycontrol's RSS files still answer but have not been updated since 2024,
so they are not used; StockEdge and Groww publish no public feed.

The US pack also read SEC EDGAR 8-K filings. They are US-only and are not used
here. The Indian equivalent is NSE's corporate announcements feed (results,
board meetings, outcomes, press releases): an announcement for one of your
companies is always marked `!`. Open the filing on nseindia.com before saying
what it says.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story and read it. Quote the publisher and link it. Never
  summarise a paywalled story you could not read: say so.
- **The company's own announcement beats a headline.** If they disagree, what
  the company filed with NSE is what counts.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags. Items marked `!` are NSE announcements for your companies, or are
  tagged earnings, halt (including upper or lower circuit and F&O ban), risk,
  legal or regulatory (SEBI, RBI, CCI, USFDA).
- **MOVING** marks a stock 3% or more from its previous close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  the previous close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 -B "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1
   means nothing new. `--all` shows items already seen, `--json` gives
   everything machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter first: every `!` item and every MOVING symbol.
3. Write a short brief per symbol: what happened, the publisher and link, the
   move, and one line on why it matters to the setup (for example "results
   are this week: an overnight gap can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`. Never act on a headline alone.

## Example: RELIANCE, Sunday 2026-11-01

The check at 11:30 IST with `--hours 96` (14 items in the feed) leads with
"Reliance Industries Q2 results: Profit may top Rs 21,000 crore as refining,
Jio drive earnings" (Moneycontrol.com, Fri 09:36 IST, `!` earnings) and the
Jio Platforms listing stories of Oct 28 (Reuters, Moneycontrol.com, ETV
Bharat). The feed also carries a Reliance Power price page: a different
company, left out of the brief. The price line reads
`RELIANCE   Rs 1,164.90 -0.21% vs previous close Rs 1,167.40 at 15:28 IST`.
The brief: "Results are due: the stock can gap at the open. A preview is an
estimate, not the result; check the NSE announcement once it is out."

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per symbol, with a pause
  between calls.

## Data warning

This pack is open source: it brings no data source of its own, so the price-move line
tries, in order (headlines still come from Google News and the market-wide feeds above;
this warning is about the price-move line only):

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

Whichever source is used, treat every level and move here as a rough view, and check it on
your broker's own chart before relying on it. Bringing your own Dhan key gets you a steadier
result than the automatic free tier. This pack is provided as-is: whoever built or modified
it takes no responsibility for any loss from using it, and the stock market carries real
risk: plan and invest only after your own careful judgement.

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
