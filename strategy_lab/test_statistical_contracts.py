"""统计输出字段契约测试。

统计维度发生变化时，本文件必须同步更新；测试失败表示统计输出与验证契约不同步。
"""

import unittest

import pandas as pd

try:
    from .result_analysis import analyze_result
    from .signal_analysis import (
        EXCURSION_WINDOWS,
        RETURN_WINDOWS,
        analyze_signals,
        summarize_signals,
    )
except ImportError:
    from result_analysis import analyze_result
    from signal_analysis import EXCURSION_WINDOWS, RETURN_WINDOWS, analyze_signals, summarize_signals


class StatisticalContractTests(unittest.TestCase):
    def test_signal_output_contract(self):
        quotes = pd.DataFrame({
            "日期": pd.date_range("2026-01-01", periods=65, freq="D"),
            "板块名称": ["测试板块"] * 65,
            "收盘": [100.0 + i for i in range(65)],
            "最高": [101.0 + i for i in range(65)],
            "最低": [99.0 + i for i in range(65)],
        })
        signals = pd.DataFrame({"日期": [pd.Timestamp("2026-01-01")], "板块名称": ["测试板块"]})
        result = analyze_signals(quotes, signals)
        expected = ["signal_date", "symbol", "signal_price"]
        expected += [f"return_{window}d" for window in RETURN_WINDOWS]
        expected += [f"mfe_{window}d" for window in EXCURSION_WINDOWS]
        expected += [f"mae_{window}d" for window in EXCURSION_WINDOWS]
        expected += ["loss_over_5pct_3d", "loss_over_10pct_3d"]
        self.assertEqual(list(result.columns), expected)

        summary = summarize_signals(result)
        self.assertEqual(set(summary), {"sample_count", "start_date", "end_date", "coverage_years", "sample_quality", "periods"})
        self.assertEqual(set(summary["periods"]), {f"{window}d" for window in RETURN_WINDOWS})

    def test_result_metrics_contract(self):
        result = pd.DataFrame({
            "日期": pd.date_range("2026-01-01", periods=3, freq="D"),
            "板块名称": ["等权组合"] * 3,
            "next_return": [0.01, -0.02, 0.03],
            "cum_return": [1.01, 0.9898, 1.019494],
            "active_count": [1, 1, 2],
        })
        expected = {
            "rows", "start_date", "end_date", "final_net_value", "cumulative_return",
            "annualized_return", "max_drawdown", "calmar_ratio", "win_rate",
            "average_return", "volatility", "annualized_volatility", "sharpe_ratio",
            "positive_days", "negative_days", "flat_days", "max_consecutive_loss_days",
            "average_active_count", "max_active_count", "flat_exposure_ratio",
            "abnormal_return_count", "missing_return_count", "invalid_return_count",
            "date_sorted", "duplicate_date_industry_count",
        }
        self.assertEqual(set(analyze_result(result)), expected)


if __name__ == "__main__":
    unittest.main()
