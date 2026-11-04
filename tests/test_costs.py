# Original: Jake Schincariol (MIT License) - https://github.com/Jakeschincariol/day-trader-skill
# Modified by: Lokesh Kumar - https://github.com/trmxvibs

"""The Dhan / NSE intraday cost model (backtest/costs.json), rates checked 2026-11-01."""

import os
import unittest

from _load import ROOT, load

costs = load("backtest", "costs")


class Costs(unittest.TestCase):
    def test_the_reliance_round_trip(self):
        # 102 RELIANCE bought at 1186.10, sold at 1191.00: Rs 1,20,982.20 in, Rs 1,21,482.00 out
        c = costs.charges(102, 1186.10, 1191.00)
        self.assertEqual(c, {"brokerage": 40.0, "stt": 30.37, "exchange_txn": 7.44, "sebi_fee": 0.24,
                             "ipft": 0.0, "stamp": 3.63, "gst": 8.58, "total": 90.26})

    def test_small_orders_pay_0_03_percent_not_rs_20(self):
        c = costs.charges(10, 500.0, 500.0)          # Rs 5,000 a side: 0.03% = Rs 1.50 an order
        self.assertEqual(c["brokerage"], 3.0)
        self.assertEqual(c["stt"], 1.25)

    def test_slippage_is_against_you(self):
        self.assertAlmostEqual(costs.slipped(1000.0, "buy"), 1000.3)
        self.assertAlmostEqual(costs.slipped(1000.0, "sell"), 999.7)

    def test_every_rate_has_a_source_and_a_date(self):
        cfg = costs.load()
        self.assertEqual(cfg["date_checked"], "2026-11-01")
        for k in ("dhan_pricing", "nse_transaction_charges", "slippage"):
            self.assertIn(k, cfg["sources"])
        self.assertIn("https://dhan.co/pricing/", cfg["sources"]["dhan_pricing"])
        self.assertIn("FA73061", cfg["sources"]["nse_transaction_charges"])
        self.assertEqual(cfg["slippage_per_side"], 0.0003)


if __name__ == "__main__":
    unittest.main()
