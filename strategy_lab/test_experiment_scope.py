import tempfile
import unittest
from pathlib import Path

import pandas as pd

try:
    from .experiment_scope import archive_source_snapshot, constrain_result_to_parent_range, load_parent_scope
    from .experiment_metrics import save_standard_result_set
    from .signal_analysis import analyze_signals
except ImportError:
    from experiment_scope import archive_source_snapshot, constrain_result_to_parent_range, load_parent_scope
    from experiment_metrics import save_standard_result_set
    from signal_analysis import analyze_signals


class ExperimentScopeTests(unittest.TestCase):
    def test_snapshot_is_copied_and_fingerprinted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.csv"
            source.write_text("日期,收盘\n2026-01-01,1\n", encoding="utf-8")
            fingerprint = archive_source_snapshot(source, root / "exp")
            self.assertTrue((root / "exp" / fingerprint["snapshot_path"]).is_file())
            self.assertEqual(len(fingerprint["sha256"]), 64)

    def test_signal_forward_windows_stop_at_observation_end(self):
        quotes = pd.DataFrame({
            "日期": pd.date_range("2026-01-01", periods=5),
            "板块名称": ["A"] * 5,
            "收盘": [10, 11, 12, 13, 14],
            "最高": [10, 11, 12, 13, 14],
            "最低": [10, 11, 12, 13, 14],
        })
        signals = pd.DataFrame({"日期": [pd.Timestamp("2026-01-02")], "板块名称": ["A"]})
        result = analyze_signals(quotes, signals, observation_end="2026-01-03")
        self.assertTrue(pd.isna(result.loc[0, "return_3d"]))
        self.assertAlmostEqual(result.loc[0, "return_1d"], 12 / 11 - 1)

    def test_parent_range_includes_only_in_scope_realized_returns(self):
        frame = pd.DataFrame({
            "日期": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "next_return_date": pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-04"]),
            "next_return": [0.01, 0.02, 0.03],
        })
        scope = {"result_start_date": "2026-01-01", "result_end_date": "2026-01-03",
                 "realized_end_date": "2026-01-03"}
        filtered = constrain_result_to_parent_range(frame, scope)
        self.assertEqual(filtered["next_return"].tolist(), [0.01, 0.02])

    def test_parent_scope_rejects_legacy_missing_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            (exp / "meta.json").write_text('{"experiment_id":"exp_001"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "缺少时间区间和归档结果"):
                load_parent_scope(exp)

    def test_parent_scope_infers_legacy_result_dates_but_marks_source_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            (exp / "meta.json").write_text('{"experiment_id":"exp_001"}', encoding="utf-8")
            pd.DataFrame({"日期": ["2025-03-01", "2025-04-30"]}).to_csv(exp / "result.csv", index=False, encoding="utf-8-sig")
            scope = load_parent_scope(exp)
            self.assertEqual(scope["result_start_date"], "2025-03-01")
            self.assertEqual(scope["result_end_date"], "2025-04-30")
            self.assertEqual(scope["source_version_status"], "unknown")

    def test_metrics_writer_keeps_fixed_result_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = pd.DataFrame({
                "日期": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
                "next_return_date": pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-04"]),
                "next_return": [0.01, 0.02, 0.03],
                "cum_return": [1.01, 1.0302, 1.061106],
                "板块名称": ["组合"] * 3,
            })
            meta = {"data_scope": {"result_start_date": "2026-01-01", "result_end_date": "2026-01-03",
                                   "realized_end_date": "2026-01-03", "source_fingerprint": {"sha256": "abc"}}}
            save_standard_result_set(frame, tmp, meta)
            result = pd.read_csv(Path(tmp) / "result.csv", encoding="utf-8-sig")
            self.assertEqual(len(result), 2)
            self.assertEqual(result["next_return_date"].iloc[-1], "2026-01-03")
            self.assertEqual(meta["data_scope"]["result_end_date"], "2026-01-02")

    def test_metrics_writer_excludes_returns_realized_outside_parent_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = pd.DataFrame({
                "日期": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
                "next_return_date": pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-04"]),
                "next_return": [0.01, 0.02, 0.03],
                "cum_return": [1.01, 1.0302, 1.061106],
                "板块名称": ["组合"] * 3,
            })
            meta = {"data_scope": {"result_start_date": "2026-01-01", "result_end_date": "2026-01-03",
                                   "realized_end_date": "2026-01-03", "source_fingerprint": {"sha256": "abc"}}}
            save_standard_result_set(frame, tmp, meta)
            result = pd.read_csv(Path(tmp) / "result.csv", encoding="utf-8-sig")
            self.assertEqual(result["next_return"].tolist(), [0.01, 0.02])


if __name__ == "__main__":
    unittest.main()
