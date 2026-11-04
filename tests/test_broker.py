# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""trader-broker tests, NSE India version: a paper journal. Nothing in this tool talks to
a broker; a yes becomes a paper "placed" record that trader-risk counts toward today's limits.
The order is the 2026-10-15 RELIANCE bracket (102 shares, stop 1181.20, target 1191.00)."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
NOW = "2026-10-15 09:56"
EM_DASH = chr(0x2014)
ORDER = {"symbol": "RELIANCE", "side": "buy", "qty": "102", "type": "market", "time_in_force": "day",
         "order_class": "bracket", "take_profit_limit_price": "1191.00", "stop_loss_stop_price": "1181.20",
         "why": "opening-range breakout on RELIANCE: 102 shares",
         "plan": {"entry": 1186.1, "stop": 1181.2, "target": 1191.0, "risk_per_share": 4.9, "max_loss": 499.8}}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


class Yes(unittest.TestCase):
    def test_plain_yes_counts_and_nothing_else(self):
        self.assertTrue(ticket.parse_yes("yes", ORDER)[0])
        self.assertTrue(ticket.parse_yes("Yes.", ORDER)[0])
        for reply in ("sure", "ok", "yes to all", "yes but wait", "maybe", "no"):
            self.assertFalse(ticket.parse_yes(reply, ORDER)[0], reply)

    def test_the_yes_must_name_this_order(self):
        batch = ["RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO"]
        for reply in ("yes HDFCBANK", "yes M&M", "yes bajaj-auto", "yes 103", "yes but stop at 1180"):
            self.assertFalse(ticket.parse_yes(reply, ORDER, batch)[0], reply)
        for reply in ("yes RELIANCE", "yes 102 RELIANCE", "yes 1181.20", "yes max loss 499.80", "yes reliance.ns"):
            self.assertTrue(ticket.parse_yes(reply, ORDER, batch)[0], reply)

    def test_plain_english_in_rupees(self):
        text = ticket.plain(ORDER)
        self.assertIn("BUY 102 RELIANCE", text)
        self.assertIn("Rs 499.80", text)
        self.assertNotIn("$", text)


class Journal(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        shutil.copy(CFG_PATH, os.path.join("trader", "config.json"))
        with open(os.path.join("trader", "orders.json"), "w") as fh:
            json.dump({"created_by": "trader-budget", "orders": [ORDER]}, fh)
        with open(os.path.join("trader", "prices.json"), "w") as fh:
            json.dump({"prices": {"RELIANCE": 1186.1}}, fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def state(self):
        with open(os.path.join("trader", "state", "broker.json")) as fh:
            return json.load(fh)

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(ticket, ["show", "--now", NOW])
        self.assertIn("NOT READY", text)
        code, _, err = run(ticket, ["approve", "--index", "1", "--reply", "yes", "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_paper_flow(self):
        self.assertEqual(run(check, ["--now", NOW])[0], 0)
        code, text, _ = run(ticket, ["show", "--now", NOW])
        self.assertIn("PAPER JOURNAL (India version: nothing is sent to any broker)", text)
        self.assertIn("READY: trader-risk PASS", text)
        for never in ("Alpaca", "place_stock_order", "Allow once", "Interactive Brokers", "$"):
            self.assertNotIn(never, text)
        code, text, _ = run(ticket, ["approve", "--index", "1", "--reply", "yes", "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("Nothing was sent to any broker", text)
        rec = self.state()["orders"][-1]
        self.assertEqual((rec["status"], rec["paper"], rec["kind"], rec["max_loss"]), ("placed", True, "entry", 499.8))
        self.assertTrue(rec["at"].endswith("+05:30"))
        # the paper record counts: the same order again is a duplicate today
        code, text, _ = run(check, ["--now", "2026-10-15 10:05"])
        self.assertEqual(code, 1)
        self.assertIn("already", text)
        code, text, _ = run(ticket, ["log", "--now", "2026-10-15 10:06"])
        self.assertIn("1 placed in the paper journal today", text)

    def test_an_edited_order_is_not_ready(self):
        run(check, ["--now", NOW])
        with open(os.path.join("trader", "orders.json"), "w") as fh:
            json.dump({"orders": [dict(ORDER, qty="110")]}, fh)
        code, text, _ = run(ticket, ["show", "--now", NOW])
        self.assertIn("NOT READY: this exact order was not in the last risk check", text)

    def test_decline_is_logged(self):
        run(check, ["--now", NOW])
        code, text, _ = run(ticket, ["decline", "--index", "1", "--now", NOW])
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["orders"][-1]["status"], "declined")

    def test_paper_fill_and_flatten(self):
        with open(os.path.join("trader", "portfolio.json"), "w") as fh:
            json.dump({"cash": 200000, "positions": []}, fh)
        code, text, err = run(ticket, ["fill", "--symbol", "RELIANCE", "--side", "buy", "--qty", "102", "--price",
                                       "1186.10", "--now", NOW])
        self.assertEqual(code, 0, err)
        with open(os.path.join("trader", "portfolio.json")) as fh:
            pf = json.load(fh)
        self.assertAlmostEqual(pf["cash"], 200000 - 102 * 1186.10, places=2)
        code, text, err = run(ticket, ["flatten", "--symbol", "RELIANCE", "--now", "2026-10-15 14:55"])
        self.assertEqual(code, 0, err)
        self.assertIn("flat by 15:00 IST", text)
        with open(os.path.join("trader", "orders.json")) as fh:
            o = json.load(fh)["orders"][0]
        self.assertEqual((o["side"], o["qty"]), ("sell", "102"))

    def test_there_is_no_broker_result_command(self):
        with self.assertRaises(SystemExit):
            run(ticket, ["result", "--index", "1", "--status", "placed"])
        self.assertFalse(os.path.exists(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")))


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-broker", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("paper journal", "nothing is sent to any broker", "one order at a time"):
            self.assertIn(must, low)
        for never in (EM_DASH, "alpaca", "place_stock_order", "allow once", "new york"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
