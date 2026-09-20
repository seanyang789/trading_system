"""Experiment Metrics Engine：只计算和归档客观实验结果。"""
import json
from pathlib import Path

import pandas as pd

try:
    from .result_analysis import analyze_result
except ImportError:
    from result_analysis import analyze_result


def save_standard_result_set(result, experiment_dir, meta, trades=None, validation=None):
    experiment_dir = Path(experiment_dir)
    experiment_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(experiment_dir / "result.csv", index=False, encoding="utf-8-sig")
    equity = result[[c for c in ("日期", "cum_return", "next_return") if c in result.columns]].copy()
    equity = equity.rename(columns={"cum_return": "account_value", "next_return": "daily_return"})
    if "account_value" in equity:
        equity["cumulative_return"] = equity["account_value"] - 1
        equity["drawdown"] = equity["account_value"] / equity["account_value"].cummax() - 1
    equity.to_csv(experiment_dir / "equity_curve.csv", index=False, encoding="utf-8-sig")
    if trades is None:
        trades = pd.DataFrame(columns=["buy_date", "sell_date", "symbol", "buy_price", "sell_price", "holding_days", "return"])
    trades.to_csv(experiment_dir / "trades.csv", index=False, encoding="utf-8-sig")
    metrics = analyze_result(result)
    (experiment_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validation = validation or {}
    validation.update({"result_rows": len(result), "required_files": True})
    (experiment_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metrics
