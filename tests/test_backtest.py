# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""backtest/backtest.py: the baseline ORB rule, fills, costs, the portfolio rules and the
statistics. The real-data checks use the RELIANCE 60-day 5-minute fixture (Yahoo, saved
2026-11-01) and compare every trade with trader-timemachine's own history replay."""

import datetime
import gzip
import os
import shutil
import tempfile
import unittest

from _load import FIX, load

bt = load("backtest", "backtest")
tm = bt.tm
NDIR = os.path.join(FIX, "nse")
CC = bt.costs.load()


def bar(o, h, l, c, v=1000):
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


def session(n=72, price=1000.0):
    """A quiet 72-bar day just above price (inside the 995-1005 opening range, above a 1000 stop)."""
    D = [bar(price + 3, price + 4, price + 2, price + 3) for _ in range(n)]
    D[0] = bar(1000, 1005, 995, 1000)
    return D


class Rule(unittest.TestCase):
    def test_trigger_needs_a_close_above_the_range_inside_the_window(self):
        D = session()
        D[4] = bar(1000, 1008, 999, 1004)        # high above the range, close inside: no trigger
        D[5] = bar(1004, 1012, 1003, 1010)       # closes at 09:45, above 1005: trigger
        k, plan = bt.plan_for(D, bt.BASE)
        self.assertEqual(k, 5)
        self.assertEqual(plan["trigger_close"], "09:45")
        self.assertEqual((plan["entry"], plan["stop"], plan["risk"], plan["target"]), (1010.0, 1000.0, 10.0, 1020.0))

    def test_no_trigger_after_1430_and_the_range_bars_do_not_count(self):
        D = session()
        D[2] = bar(1000, 1010, 999, 1009)        # inside the 15-minute range itself
        D[63] = bar(1000, 1012, 999, 1011)       # closes 14:35: too late
        k, why = bt.plan_for(D, bt.BASE)
        self.assertIsNone(k)
        D[62] = bar(1000, 1012, 999, 1011)       # closes 14:30: allowed
        self.assertEqual(bt.plan_for(D, bt.BASE)[0], 62)

    def test_a_stop_too_tight_is_refused_like_trader_exit(self):
        D = session()
        for i in range(3):
            D[i] = bar(1000, 1000.2, 999.9, 1000)  # range 999.90-1000.20, midpoint 1000.05
        D[3] = bar(1000, 1000.6, 1000, 1000.5)   # risk 0.45 < 0.05% of 1000.50
        self.assertEqual(bt.plan_for(D, bt.BASE), (None, "stop too tight"))

    def test_ticks_stop_rounds_down_target_rounds_down(self):
        D = session(price=1176)
        D[0] = bar(1186, 1189.4, 1173.1, 1180)   # mid 1181.25 -> 1181.20 on the 0.10 tick
        D[3] = bar(1186, 1191, 1185, 1190.05)
        k, plan = bt.plan_for(D, bt.BASE)
        # risk 1190.05 - 1181.20 = 8.85; target 1198.90 rounded down to the tick
        self.assertEqual((plan["stop"], plan["risk"], plan["target"], plan["tick"]), (1181.2, 8.85, 1198.9, 0.10))


class Fills(unittest.TestCase):
    def setUp(self):
        self.D = session()
        self.D[3] = bar(1000, 1011, 1000, 1010)   # trigger at 09:35: entry 1010, stop 1000, target 1020

    def run_(self, rule=None):
        t, why = bt.trade("TEST", "2026-10-15", self.D, rule or bt.BASE, bt.CONFIG, CC)
        self.assertIsNotNone(t, why)
        return t

    def test_stop_first_when_one_bar_touches_both(self):
        self.D[6] = bar(1010, 1025, 995, 1010)
        t = self.run_()
        self.assertEqual((t["outcome"], t["exit_price"], t["R_rule"]), ("stop", 1000.0, -1.0))

    def test_a_gap_through_the_stop_exits_at_the_open(self):
        self.D[6] = bar(990, 995, 985, 990)
        t = self.run_()
        self.assertEqual((t["outcome"], t["exit_price"], t["R_rule"]), ("gap stop", 990, -2.0))

    def test_target_and_the_net_numbers(self):
        self.D[7] = bar(1012, 1021, 1011, 1019)
        t = self.run_()
        self.assertEqual((t["outcome"], t["exit_price"], t["qty"]), ("target", 1020.0, 50))
        e, x = 1010 * 1.0003, 1020 * 0.9997
        self.assertAlmostEqual(t["entry_fill"], e, places=4)
        self.assertAlmostEqual(t["gross_inr"], 50 * (x - e), places=2)
        c = bt.costs.charges(50, e, x, CC)["total"]
        self.assertAlmostEqual(t["net_inr"], 50 * (x - e) - c, places=2)
        self.assertAlmostEqual(t["R_net"], t["net_inr"] / 500.0, places=4)
        self.assertLess(t["R_net"], 1.0)       # costs and slippage always cost something

    def test_flat_at_1500_uses_the_1455_bar_close(self):
        self.D[68] = bar(1003, 1004, 1002, 1003.5)
        t = self.run_()
        self.assertEqual((t["outcome"], t["exit_time"], t["exit_price"]), ("flat 15:00", "15:00", 1003.5))

    def test_next_open_fill_and_short_side(self):
        self.D[4] = bar(1012, 1013, 1011, 1012)
        t = self.run_(dict(bt.BASE, name="x", fill="next_open"))
        self.assertAlmostEqual(t["entry_fill"], 1012 * 1.0003, places=4)
        D = session()
        D[3] = bar(1000, 1000, 989, 990)          # closes below the 995 range low
        t, _ = bt.trade("TEST", "2026-10-15", D, dict(bt.BASE, name="s", side="sell"), bt.CONFIG, CC)
        self.assertEqual((t["side"], t["entry"], t["stop"], t["target"]), ("sell", 990.0, 1000.0, 980.0))


class Portfolio(unittest.TestCase):
    def row(self, sym, eb, xb, notional=120000, risk=500):
        return {"symbol": sym, "date": "2026-10-15", "entry_bar": eb, "exit_bar": xb, "notional_inr": notional,
                "risk_inr": risk, "net_inr": 0.0}

    def test_cash_and_trade_count(self):
        rows = [self.row("A", 3, 10), self.row("B", 4, 12), self.row("C", 11, 20), self.row("D", 13, 30),
                self.row("E", 21, 40)]
        taken = bt.portfolio(rows, bt.CONFIG)
        # B needs cash while A is open (1.8 lakh usable): skipped. C after A exits. D after C? C open: skipped
        # until... D at bar 13 while C (to 20) holds 1.2 lakh: skipped. E at 21: third trade.
        self.assertEqual([t["symbol"] for t in taken], ["A", "C", "E"])

    def test_daily_loss_limit(self):
        rows = [self.row(s, i, i + 1, 10000, 600) for i, s in enumerate("ABCD")]
        self.assertEqual([t["symbol"] for t in bt.portfolio(rows, bt.CONFIG)], ["A", "B"])


class Stats(unittest.TestCase):
    def test_bootstrap_is_repeatable_and_clustered_by_day(self):
        rows = [{"date": "d%d" % (i % 7), "R_net": (i % 5) - 2.0} for i in range(40)]
        days = sorted({r["date"] for r in rows})
        a = bt.boot_ci(rows, "R_net", days, n=500)
        self.assertEqual(a, bt.boot_ci(rows, "R_net", days, n=500))
        self.assertLess(a["lo"], a["hi"])

    def test_drawdown(self):
        self.assertEqual(bt.max_drawdown([100, -50, -80, 40, -200, 300]), -290.0)


class RealData(unittest.TestCase):
    """RELIANCE, 60 days of real 5-minute bars."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        os.mkdir(os.path.join(cls.d, "yahoo5m"))
        shutil.copy(os.path.join(NDIR, "RELIANCE-60d5m.json.gz"),
                    os.path.join(cls.d, "yahoo5m", "RELIANCE-60d5m.json.gz"))
        cls.sessions, cls.short = bt.load_sessions(cls.d, "RELIANCE")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def test_sessions_are_72_bar_cas_days(self):
        self.assertTrue(all(len(D) == 72 for d, D in self.sessions.items() if d >= "2026-08-03"))
        self.assertNotIn("2026-10-20", self.sessions)   # Dussehra

    def test_every_trade_matches_the_time_machine_history_replay(self):
        rows = []
        for d, D in sorted(self.sessions.items()):
            t, why = bt.trade("RELIANCE", d, D, bt.BASE, bt.CONFIG, CC)
            if t:
                rows.append(t)
        self.assertGreater(len(rows), 10)
        sess = [(d, D) for d, D in sorted(self.sessions.items())]
        rule = {"or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0}
        h = tm.history_replay(sess, rule, (bt.hm("15:00") - tm.OPEN_MIN) // 5 - 1)
        by = {x["date"]: x for x in h["trades"]}
        for t in rows:
            x = by[t["date"]]
            self.assertEqual((x["entry"], x["stop"], x["target"]), (t["entry"], t["stop"], t["target"]), t["date"])
            want = {"target": "target", "stop": "stop", "gap stop": "stop", "flat 15:00": "close"}[t["outcome"]]
            self.assertEqual(x["outcome"], want, t["date"])
            if t["outcome"] != "gap stop":   # the history replay books a gap at -1R
                self.assertAlmostEqual(x["R"], t["R_rule"], places=2)


class YahooRemoved(unittest.TestCase):
    def cli(self, argv):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = bt.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_fetch_is_disabled_and_run_needs_the_legacy_flag(self):
        code, _, err = self.cli(["fetch", "--data", "/nonexistent"])
        self.assertEqual(code, 2)
        self.assertIn("Yahoo was removed", err)
        code, _, err = self.cli(["run", "--data", "/nonexistent"])
        self.assertEqual(code, 2)
        self.assertIn("--legacy-yahoo", err)

    def test_the_summary_says_unreliable(self):
        self.assertIn("DATA WARNING", bt.__doc__)
        self.assertIn("UNRELIABLE", bt.summary_md.__code__.co_consts.__repr__())


if __name__ == "__main__":
    unittest.main()
