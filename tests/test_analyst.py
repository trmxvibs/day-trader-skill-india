# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""trader-analyst, NSE India version: a "not applicable" stub (the US data source has no NSE counterpart)."""

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import ROOT, load

tool = load("trader-analyst", "facts")


class NotApplicable(unittest.TestCase):
    def test_the_tool_says_so_and_fetches_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tool.main([])
        self.assertEqual(code, 3)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("not applicable to the NSE (India) version", err.getvalue())
        with open(os.path.join(ROOT, "trader-analyst", "facts.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("urllib", src)

    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-analyst", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        self.assertIn("NOT APPLICABLE", text)
        self.assertIn("nseindia.com", text)
        self.assertNotIn(chr(0x2014), text)


if __name__ == "__main__":
    unittest.main()
