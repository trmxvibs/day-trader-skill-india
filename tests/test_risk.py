# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""trader-risk tests, NSE India version.

The order is the 2026-10-15 RELIANCE plan (entry 1186.10, stop 1181.20, target 1191.00,
risk Rs 4.90 a share). With the example config's Rs 500 max loss per trade, 102 shares
risk Rs 499.80 (PASS) and 103 shares risk Rs 504.70 (BLOCK). All limits are in rupees and
there is no pattern-day-trader rule in India. Paper only: a PASS never sends anything.
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
NOW = "2026-10-15 09:56"
TODAY = "2026-10-15"
PRICES = {"RELIANCE": 1186.1}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def bracket(sym="RELIANCE", qty=102, entry=1186.10, stop=1181.20, target=1191.00):
    return {"symbol": sym, "side": "buy", "qty": str(qty), "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "%.2f" % target,
            "stop_loss_stop_price": "%.2f" % stop, "plan": {"entry": entry, "stop": stop, "target": target}}


def placed(sym, side="buy", day=TODAY, max_loss=499.8, status="placed"):
    return {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T09:56:00+05:30",
            "max_loss": max_loss}


def run_check(orders, now=NOW, prices=PRICES, **kw):
    return check.check(orders, kw.pop("c", cfg()), prices=prices, now=at(now), **kw)


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


class TheTrade(unittest.TestCase):
    def test_102_shares_pass_every_check(self):
        res = run_check([bracket()])
        self.assertEqual(verdicts(res), ["PASS"], reasons(res))

    def test_103_shares_are_blocked_by_the_max_loss_only(self):
        res = run_check([bracket(qty=103)])
        self.assertEqual(verdicts(res), ["BLOCK"])
        r = res["results"][0]["reasons"]
        self.assertEqual(len(r), 1, r)
        self.assertIn("Rs 504.70", r[0])
        self.assertIn("Rs 500", r[0])

    def test_a_fresh_higher_price_is_the_entry(self):
        res = run_check([bracket()], prices={"RELIANCE": 1187.00})
        self.assertEqual(verdicts(res), ["BLOCK"])             # 102 x (1187.00 - 1181.20) = Rs 591.60
        self.assertIn("Rs 591.60", reasons(res))

    def test_reward_risk_stop_side_and_a_required_stop(self):
        res = run_check([bracket(target=1189.00)])                # 2.90 / 4.90 = 0.59R
        self.assertIn("reward:risk", reasons(res))
        o = bracket()
        o.pop("stop_loss_stop_price")
        o.pop("plan")
        o.pop("order_class")
        o.pop("take_profit_limit_price")
        res = run_check([o])
        self.assertIn("stop", reasons(res))

    def test_daily_loss_and_trades_per_day(self):
        state = {"orders": [placed("HDFCBANK"), placed("TCS")]}
        res = run_check([bracket()], state=state)                 # 499.80 x 3 = 1,499.40: inside Rs 1,500
        self.assertEqual(verdicts(res), ["PASS"], reasons(res))
        state = {"orders": [placed("HDFCBANK"), placed("TCS"), placed("ITC")]}
        res = run_check([bracket()], state=state)
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("trades", reasons(res))

    def test_no_pattern_day_trader_rule(self):
        # four day trades in a row on earlier days: in India that is not a rule, nothing blocks
        days = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
        state = {"orders": [placed("ITC", day=d) for d in days] + [placed("ITC", "sell", day=d) for d in days]}
        res = run_check([bracket()], state=state)
        self.assertEqual(verdicts(res), ["PASS"], reasons(res))
        self.assertNotIn("pdt", json.dumps(res).lower())
        self.assertNotIn("pdt_guard", check.DEFAULTS)


class Window(unittest.TestCase):
    def test_the_ist_entry_window(self):
        for now, ok, words in (("2026-10-15 09:20", False, "no new entries before 09:30 IST"),
                               ("2026-10-15 09:30", True, ""),
                               ("2026-10-15 14:30", True, ""),
                               ("2026-10-15 14:31", False, "no new entries after 14:30 IST"),
                               ("2026-10-15 15:20", False, "continuous trading is 09:15 to 15:15"),
                               ("2026-10-15 09:05", False, "the market is closed"),
                               ("2026-10-20 10:00", False, "NSE holiday: Dussehra"),
                               ("2026-10-17 10:00", False, "weekend"),
                               ("2026-11-08 18:30", False, "Muhurat")):
            res = run_check([bracket()], now=now)
            self.assertEqual(verdicts(res), ["PASS" if ok else "BLOCK"], (now, reasons(res)))
            if words:
                self.assertIn(words, reasons(res), now)

    def test_the_window_uses_ist_not_the_machine(self):
        self.assertEqual(at("2026-10-15 09:56"), datetime.datetime(2026, 10, 15, 4, 26))
        self.assertEqual(at("2026-10-15T04:26:00Z"), datetime.datetime(2026, 10, 15, 4, 26))

    def test_getting_out_is_never_blocked_by_the_window(self):
        sell = {"symbol": "RELIANCE", "side": "sell", "qty": "102", "type": "market", "time_in_force": "day"}
        port = {"cash": 100000, "positions": [{"symbol": "RELIANCE", "qty": 102, "avg_price": 1186.1}]}
        res = run_check([sell], now="2026-10-15 14:55", portfolio=port)
        self.assertEqual(verdicts(res), ["PASS"], reasons(res))


class SymbolsAndFormat(unittest.TestCase):
    def test_nse_symbols(self):
        for sym in ("M&M", "BAJAJ-AUTO", "RELIANCE.NS"):
            o = bracket(sym=sym)
            res = run_check([o], prices={sym.replace(".NS", ""): 1186.1, sym: 1186.1})
            self.assertNotIn("asset", reasons(res), sym)
        for bad, words in (("BTC/USD", "asset"), ("NIFTY26NOV24000CE", "F&O contract"),
                           ("RELIANCE26NOVFUT", "F&O contract"), ("reliance corp", "asset")):
            res = run_check([bracket(sym=bad)])
            self.assertEqual(verdicts(res), ["BLOCK"], bad)
            self.assertIn(words, reasons(res), bad)

    def test_whole_shares_only(self):
        o = {"symbol": "RELIANCE", "side": "buy", "notional": "50000", "type": "market", "time_in_force": "day"}
        res = run_check([o])
        self.assertIn("NSE equity trades whole shares", reasons(res))
        o = {"symbol": "RELIANCE", "side": "buy", "type": "market", "time_in_force": "day"}
        self.assertIn("give qty", reasons(run_check([o])))

    def test_short_sales_are_blocked_unless_allowed(self):
        o = {"symbol": "RELIANCE", "side": "sell", "qty": "10", "type": "market", "time_in_force": "day"}
        res = run_check([o])
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("short", reasons(res))

    def test_us_dollar_keys_are_refused(self):
        with self.assertRaises(check.RiskError) as cm:
            check.check([bracket()], cfg(max_loss_per_trade_usd=50), prices=PRICES, now=at(NOW))
        self.assertIn("max_loss_per_trade_inr", str(cm.exception))

    def test_size_limits_in_rupees(self):
        res = run_check([bracket(qty=200)], c=cfg(max_loss_per_trade_inr=5000, max_order_inr=200000))
        self.assertIn("Rs 237,220", reasons(res))                 # 200 x 1186.10 over the Rs 2,00,000 cap
        res = run_check([bracket(qty=1, stop=1181.20)], c=cfg(min_order_inr=5000))
        self.assertIn("size", reasons(res))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        shutil.copy(CFG_PATH, os.path.join("trader", "config.json"))
        with open(os.path.join("trader", "orders.json"), "w") as fh:
            json.dump({"created_by": "trader-budget", "orders": [bracket(), bracket(sym="HDFCBANK", qty=103)]}, fh)
        with open(os.path.join("trader", "prices.json"), "w") as fh:
            json.dump({"prices": {"RELIANCE": 1186.1}}, fh)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = check.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_report_and_verdict_file(self):
        code, text, err = self.cli("--now", NOW)
        self.assertEqual(code, 1, err)                           # the second order is blocked
        self.assertIn("1 passed, 1 blocked at 2026-10-15 09:56 IST", text)
        self.assertNotIn("$", text)
        self.assertNotIn("New York", text)
        with open(os.path.join("trader", "state", "risk.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(len(doc["results"]), 2)

    def test_errors_are_one_line(self):
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump(cfg(max_order_usd=4000), fh)
        code, text, err = self.cli("--now", NOW)
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("max_order_inr", err)


if __name__ == "__main__":
    unittest.main()
