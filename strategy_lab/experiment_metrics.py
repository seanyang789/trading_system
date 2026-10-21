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
    result = result.copy()
    if "日期" in result.columns:
        result["日期"] = pd.to_datetime(result["日期"], errors="raise")
        result = result.sort_values("日期").reset_index(drop=True)
        if "next_return_date" in result.columns:
            realized = pd.to_datetime(result["next_return_date"], errors="coerce")
            scope = meta.get("data_scope", {})
            start = scope.get("requested_start_date") or scope.get("result_start_date")
            end = scope.get("requested_end_date") or scope.get("realized_end_date") or scope.get("result_end_date")
            in_scope = realized.notna()
            if start:
                in_scope &= realized >= pd.Timestamp(start)
            if end:
                in_scope &= realized <= pd.Timestamp(end)
            result = result.loc[in_scope].copy()
            if len(result):
                result["cum_return"] = (1 + pd.to_numeric(result["next_return"], errors="raise")).cumprod()
        elif "next_return" in result.columns and len(result) > 1:
            result = result.iloc[:-1].copy()
            result["cum_return"] = (1 + pd.to_numeric(result["next_return"], errors="raise")).cumprod()
    if "data_scope" not in meta:
        dates = pd.to_datetime(result["日期"], errors="coerce") if "日期" in result else pd.Series(dtype="datetime64[ns]")
        meta["data_scope"] = {
            "schema_version": 1,
            "result_start_date": dates.min().date().isoformat() if dates.notna().any() else None,
            "result_end_date": dates.max().date().isoformat() if dates.notna().any() else None,
            "scope_status": "result_range_only",
            "note": "该实验未记录原始输入数据版本；此区间只描述归档结果，不代表输入数据版本可复现。",
        }
    if meta.get("data_scope", {}).get("scope_status") == "result_range_only":
        scope_start = meta["data_scope"].get("result_start_date")
        scope_end = meta["data_scope"].get("result_end_date")
        if scope_start and scope_end and "日期" in result:
            result = result[pd.to_datetime(result["日期"]).between(pd.Timestamp(scope_start), pd.Timestamp(scope_end))].copy()
    if "日期" in result.columns and len(result):
        meta["data_scope"]["result_start_date"] = pd.Timestamp(result["日期"].min()).date().isoformat()
        meta["data_scope"]["result_end_date"] = pd.Timestamp(result["日期"].max()).date().isoformat()
        if "next_return_date" in result:
            meta["data_scope"]["realized_start_date"] = pd.Timestamp(result["next_return_date"].min()).date().isoformat()
            meta["data_scope"]["realized_end_date"] = pd.Timestamp(result["next_return_date"].max()).date().isoformat()
    if "source_fingerprint" not in meta and "source_fingerprint" in meta.get("data_scope", {}):
        meta["source_fingerprint"] = meta["data_scope"]["source_fingerprint"]
    if "data_scope" in meta and meta["data_scope"].get("source_fingerprint"):
        fp = meta["data_scope"]["source_fingerprint"]
        if isinstance(fp, dict) and fp.get("sha256"):
            meta["source_fingerprint"] = fp
    result.to_csv(experiment_dir / "result.csv", index=False, encoding="utf-8-sig")
    equity = result[[c for c in ("日期", "next_return_date", "cum_return", "next_return") if c in result.columns]].copy()
    equity = equity.rename(columns={"cum_return": "account_value", "next_return": "daily_return"})
    if "account_value" in equity:
        equity["cumulative_return"] = equity["account_value"] - 1
        equity["drawdown"] = equity["account_value"] / equity["account_value"].cummax() - 1
    equity.to_csv(experiment_dir / "equity_curve.csv", index=False, encoding="utf-8-sig")
    if trades is None:
        trades = pd.DataFrame(columns=["buy_date", "sell_date", "symbol", "buy_price", "sell_price", "holding_days", "return"])
    trades.to_csv(experiment_dir / "trades.csv", index=False, encoding="utf-8-sig")
    metrics = analyze_result(result)
    metrics["data_scope"] = meta["data_scope"]
    (experiment_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validation = validation or {}
    validation.update({"result_rows": len(result), "required_files": True})
    (experiment_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (experiment_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metrics
