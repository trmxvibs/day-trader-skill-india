#!/usr/bin/env python3
# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""trader-pelosi, NSE India version: NOT APPLICABLE. Standard library only.

The original US tool read the US House Clerk's STOCK Act Periodic Transaction Reports
(trades by members of Congress).
There is no Indian counterpart that lists politicians' share trades, so
there is nothing for it to read.
The original is kept unchanged in the upstream repository (original/ next to this
folder) and is not used here.

Not replaced. Insider trades by an NSE company's own promoters and
directors (SEBI PIT disclosures) are on nseindia.com; that is a different
thing and this pack does not read it.
"""

import sys

MESSAGE = ("trader-pelosi is not applicable to the NSE (India) version: it reads US Congress (STOCK Act) trade disclosures, which have no NSE counterpart. "
           "See trader-pelosi/SKILL.md. Nothing was fetched.")


def main(argv=None):
    print(MESSAGE, file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
