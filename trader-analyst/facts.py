#!/usr/bin/env python3
# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""trader-analyst, NSE India version: NOT APPLICABLE. Standard library only.

The original US tool read a company's reported numbers from US SEC EDGAR (XBRL company facts
from 10-Qs and 10-Ks, plus the earnings 8-K).
NSE companies do not file with the SEC, so there is nothing for it to read.
The original is kept unchanged in the upstream repository (original/ next to this
folder) and is not used here.

For an Indian company, the results are in its NSE corporate announcements
(nseindia.com, Corporate filings > Financial results) and on the company's
own investor relations page.
"""

import sys

MESSAGE = ("trader-analyst is not applicable to the NSE (India) version: it reads US SEC EDGAR filings (10-Q, 10-K, 8-K), which Indian companies do not file. "
           "See trader-analyst/SKILL.md. Nothing was fetched.")


def main(argv=None):
    print(MESSAGE, file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
