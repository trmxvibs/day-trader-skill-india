# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""trader-budget tests, NSE India version.

The plan is the 2026-10-15 RELIANCE trade (entry 1186.10, stop 1181.20, risk Rs 4.90). With
the example config (budget Rs 2,00,000, 10 % cash buffer, 75 % position cap, Rs 500 max loss)
the four limits are, by hand:
  max loss      floor(500 / 4.90)              = 102 shares   <- binds
  position cap  floor(150,000 / 1186.10)       = 126
  max order     floor(200,000 / 1186.10)       = 168
  cash          floor(180,000 / 1186.10)       = 151
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

size = load("trader-budget", "size")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
NOW = "2026-10-15 09:56"
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-15", "setup": "orb",
        "entry": 1186.1, "entry_time": "9:55 AM", "entry_bar": 7, "interval": "5m",
        "opening_range": {"high": 1185.0, "low": 1177.4, "minutes": 15}, "stop": 1181.2, "stop_rule": "or_mid",
        "target": 1191.0, "target_r": 1.0, "risk_per_share": 4.9, "reward_risk": 1.0, "flat_by": "15:00",
        "tick": 0.1, "currency": "INR"}
EM_DASH = chr(0x2014)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


class InTemp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump(cfg(), fh)
        with open(os.path.join("trader", "plan.json"), "w") as fh:
            json.dump(PLAN, fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def run_size(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = size.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TheTrade(unittest.TestCase):
    def test_sized_by_the_max_loss(self):
        s = size.size_trade(PLAN, cfg(), today="2026-10-15")
        self.assertEqual((s["qty"], s["bound_by"]), (102, "max loss"))
        limits = {k: v for k, v in [(x["name"], x["shares"]) for x in s["limits"]]}
        self.assertEqual(limits, {"max loss": 102, "position cap": 126, "max order": 168, "cash": 151})

    def test_the_bracket_order_record(self):
        s = size.size_trade(PLAN, cfg(), today="2026-10-15")
        o = size.bracket_order(s)
        self.assertEqual({k: o[k] for k in ("symbol", "side", "qty", "type", "order_class", "take_profit_limit_price",
                                            "stop_loss_stop_price")},
                         {"symbol": "RELIANCE", "side": "buy", "qty": "102", "type": "market", "order_class": "bracket",
                          "take_profit_limit_price": "1191.00", "stop_loss_stop_price": "1181.20"})
        self.assertEqual(o["plan"]["max_loss"], 499.8)
        self.assertIn("Rs 500 max loss per trade", o["why"])
        self.assertNotIn("notional", o)

    def test_a_bigger_max_loss_hits_the_position_cap(self):
        s = size.size_trade(PLAN, cfg(max_loss_per_trade_inr=5000), today="2026-10-15")
        self.assertEqual((s["qty"], s["bound_by"]), (126, "position cap"))

    def test_zero_shares_is_a_refusal(self):
        s = size.size_trade(PLAN, cfg(max_loss_per_trade_inr=4), today="2026-10-15")
        self.assertEqual(s["qty"], 0)
        self.assertIn("one share risks Rs 4.90", s["refusal"])
        self.assertIn("No order", s["refusal"])

    def test_a_plan_for_another_day_is_refused(self):
        with self.assertRaises(size.SizeError) as cm:
            size.size_trade(PLAN, cfg(), today="2026-10-16")
        self.assertIn("(IST)", str(cm.exception))

    def test_a_limit_order_sizes_at_its_limit(self):
        s = size.size_trade(PLAN, cfg(), limit=1187.0, today="2026-10-15")
        self.assertEqual(s["qty"], 86)                          # floor(500 / (1187.00 - 1181.20)) = 86

    def test_a_fresh_higher_price_is_the_entry(self):
        s = size.size_trade(PLAN, cfg(), price=1188.0, today="2026-10-15")
        self.assertEqual(s["qty"], 73)                          # floor(500 / 6.80) = 73


class Weights(unittest.TestCase):
    PRICES = {"RELIANCE": 1164.9, "HDFCBANK": 692.8, "TCS": 2502.6, "M&M": 2840.3, "ITC": 405.0}

    def rows(self):
        with open(TARGETS) as fh:
            return size.parse_weights(json.load(fh))

    def test_whole_shares_always(self):
        r = size.size(self.rows(), cfg(fractional=True), budget=200000, prices=self.PRICES)
        self.assertTrue(r["whole_shares"])
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]), p)
            self.assertLessEqual(p["dollars"], 200000 * 0.75)
        orders = size.orders_from(r)
        self.assertTrue(orders)
        self.assertTrue(all("qty" in o and "notional" not in o for o in orders))

    def test_the_example_weights_are_labelled_as_an_example(self):
        with open(TARGETS) as fh:
            doc = json.load(fh)
        self.assertIn("EXAMPLE ONLY", doc["source"])
        self.assertIn("Not a recommendation", doc["source"])


class Cli(InTemp):
    def test_plan_write_and_text(self):
        code, text, err = self.run_size("--now", NOW, "--write")
        self.assertEqual(code, 0, err)
        self.assertIn("Size: 102 shares of RELIANCE = Rs 120,982.20", text)
        self.assertIn("102 x 4.90 = Rs 499.80", text)
        self.assertIn("Flat by 15:00 IST", text)
        self.assertIn("paper", text)
        self.assertNotIn("$", text)
        self.assertNotIn("Alpaca", text)
        with open(os.path.join("trader", "orders.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["orders"][0]["qty"], "102")
        self.assertIn("Nothing is ever sent to a broker", doc["note"])

    def test_us_dollar_keys_are_refused(self):
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump(cfg(max_loss_per_trade_usd=50), fh)
        code, text, err = self.run_size("--now", NOW)
        self.assertEqual(code, 1)
        self.assertIn("max_loss_per_trade_inr", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-budget", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("102 shares", "rs 500", "whole shares", "paper"):
            self.assertIn(must, low)
        for never in (EM_DASH, "alpaca", "new york", "13f", "berkshire"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")


if __name__ == "__main__":
    unittest.main()
