import unittest

import pandas as pd

try:
    from .signal_analysis import analyze_signals, summarize_signals
except ImportError:
    from signal_analysis import analyze_signals, summarize_signals


class SignalAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.quotes = pd.DataFrame({
            "日期": pd.date_range("2026-01-01", periods=5, freq="D"),
            "板块名称": ["测试板块"] * 5,
            "收盘": [100.0, 105.0, 110.0, 90.0, 95.0],
            "最高": [102.0, 108.0, 115.0, 100.0, 98.0],
            "最低": [99.0, 95.0, 100.0, 80.0, 88.0],
        })

    def test_forward_returns_mfe_mae_and_insufficient_data(self):
        signals = pd.DataFrame({"日期": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-05")], "板块名称": ["测试板块", "测试板块"]})
        result = analyze_signals(self.quotes, signals)
        first = result.iloc[0]
        self.assertAlmostEqual(first["return_1d"], 0.05)
        self.assertAlmostEqual(first["return_2d"], 0.10)
        self.assertAlmostEqual(first["return_3d"], -0.10)
        self.assertAlmostEqual(first["mfe_3d"], 0.15)
        self.assertAlmostEqual(first["mae_3d"], -0.20)
        last = result.iloc[1]
        self.assertTrue(pd.isna(last["return_1d"]))
        self.assertTrue(pd.isna(last["mfe_3d"]))
        self.assertTrue(pd.isna(last["mae_3d"]))

    def test_sample_quality(self):
        rows = []
        for i in range(30):
            rows.append({"signal_date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i), "symbol": "测试板块", "return_1d": 0.01,
                         "return_2d": 0.01, "return_3d": 0.01, "return_5d": 0.01, "return_10d": 0.01, "return_20d": 0.01,
                         "return_30d": 0.01, "return_60d": 0.01, "mfe_3d": 0.02, "mfe_5d": 0.02, "mfe_10d": 0.02,
                         "mfe_20d": 0.02, "mfe_30d": 0.02, "mfe_60d": 0.02, "mae_3d": -0.01, "mae_5d": -0.01,
                         "mae_10d": -0.01, "mae_20d": -0.01, "mae_30d": -0.01, "mae_60d": -0.01})
        summary = summarize_signals(pd.DataFrame(rows))
        self.assertEqual(summary["sample_count"], 30)
        self.assertEqual(summary["sample_quality"], "C")


if __name__ == "__main__":
    unittest.main()
