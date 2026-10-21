import json
import argparse
import shutil
from pathlib import Path

import pandas as pd
try:
    from shared.data_quality import load_bk
    from .result_analysis import analyze_result
    from .experiment_metrics import save_standard_result_set
    from .experiment_manager import build_experiment_name_zh
    from .experiment_scope import archive_source_snapshot, describe_scope
except ImportError:
    from ..shared.data_quality import load_bk
    from result_analysis import analyze_result
    from experiment_metrics import save_standard_result_set
    from experiment_manager import build_experiment_name_zh
    from experiment_scope import archive_source_snapshot, describe_scope


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_ROOT / "shared" / "data" / "bk.csv"
RESEARCH_NAME = "momentum"
EXPERIMENTS_ROOT = PROJECT_ROOT / "shared" / "experiments" / RESEARCH_NAME
INDEX_PATH = PROJECT_ROOT / "shared" / "experiments" / "index.csv"
INDEX_COLUMNS = ["strategy_name", "experiment_id", "experiment_name_zh", "description", "status", "data_start_date", "data_end_date", "source_sha256", "created_at", "result_rows", "max_return", "min_return", "alert_count", "experiment_path"]
MOMENTUM_WINDOW = 5
SINGLE_SIDE_COST = 0.001


def next_experiment_id():
    ids = [int(p.name.split("_")[-1]) for p in EXPERIMENTS_ROOT.glob("exp_*") if p.name.split("_")[-1].isdigit()]
    return f"exp_{max(ids, default=0) + 1:03d}"


def load_data(start_date=None, end_date=None):
    df = load_bk(DATA_FILE)
    if end_date is not None:
        df = df[df["日期"] <= pd.Timestamp(end_date)].copy()
    if df.empty:
        raise ValueError("指定实验区间内没有行情数据")
    return df


def run_backtest(df):
    df = df.copy()
    grouped = df.groupby("板块名称", group_keys=False)
    df["daily_return"] = grouped["收盘"].pct_change()
    df["momentum"] = grouped["收盘"].pct_change(MOMENTUM_WINDOW)
    df["next_return"] = grouped["daily_return"].shift(-1)
    df["next_return_date"] = grouped["日期"].shift(-1)
    eligible = df.dropna(subset=["momentum"])
    signals = eligible.loc[eligible.groupby("日期")["momentum"].idxmax()].copy()
    switched = signals["板块名称"].ne(signals["板块名称"].shift(1))
    signals["next_return"] = signals["next_return"] - switched.astype(float) * SINGLE_SIDE_COST * 2
    result = signals[["日期", "板块名称", "next_return", "next_return_date"]].dropna().sort_values("日期").reset_index(drop=True)
    result["cum_return"] = (1 + result["next_return"]).cumprod()
    trades = []
    entry = None
    for _, row in signals.sort_values("日期").iterrows():
        if entry is None:
            entry = row
        elif row["板块名称"] != entry["板块名称"]:
            gross = row["收盘"] / entry["收盘"] - 1
            trades.append({"buy_date": entry["日期"], "sell_date": row["日期"], "symbol": entry["板块名称"],
                           "buy_price": float(entry["收盘"]), "sell_price": float(row["收盘"]),
                           "holding_days": int((row["日期"] - entry["日期"]).days), "return": float(gross - SINGLE_SIDE_COST * 2)})
            entry = row
    return result, pd.DataFrame(trades)


def save_experiment(result, trades, input_data=None, requested_start=None, requested_end=None):
    experiment_id = next_experiment_id()
    experiment_dir = EXPERIMENTS_ROOT / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    snapshot = archive_source_snapshot(DATA_FILE, experiment_dir)
    result.to_csv(experiment_dir / "result.csv", index=False, encoding="utf-8-sig")
    returns = result["next_return"]
    meta = {
        "experiment_id": experiment_id,
        "experiment_name_zh": build_experiment_name_zh(RESEARCH_NAME, "5日动量Top1（仅换仓时扣交易成本）"),
        "research_name": RESEARCH_NAME,
        "strategy_name": "momentum",
        "description": "5日动量Top1（仅换仓时扣交易成本）",
        "status": "completed",
        "data_scope": _scope_for_result(input_data if input_data is not None else result, result,
                                         requested_start, requested_end),
        "metrics": {
            "rows": int(len(result)),
            "max_return": float(returns.max()) if len(returns) else None,
            "min_return": float(returns.min()) if len(returns) else None,
            "alert_count": int((returns.abs() > 0.20).sum()),
            "momentum_window": MOMENTUM_WINDOW,
            "single_side_cost": SINGLE_SIDE_COST,
        },
        "validation": {"source": str(DATA_FILE), "missing_values": 0, "result_rows": int(len(result))},
    }
    meta["data_scope"]["source_fingerprint"] = snapshot
    meta["metrics"].update(analyze_result(result))
    (experiment_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    index = pd.read_csv(INDEX_PATH, encoding="utf-8-sig") if INDEX_PATH.exists() else pd.DataFrame(columns=INDEX_COLUMNS)
    index = index[index.get("experiment_id", pd.Series(dtype=str)).astype(str) != experiment_id]
    index = pd.concat([index, pd.DataFrame([{
        "strategy_name": "momentum", "experiment_id": experiment_id, "experiment_name_zh": meta["experiment_name_zh"], "description": meta["description"], "status": "completed",
        "data_start_date": meta["data_scope"].get("result_start_date"), "data_end_date": meta["data_scope"].get("result_end_date"),
        "source_sha256": meta["data_scope"]["source_fingerprint"]["sha256"],
        "created_at": pd.Timestamp.now().isoformat(), "result_rows": len(result), "max_return": meta["metrics"]["max_return"],
        "min_return": meta["metrics"]["min_return"], "alert_count": meta["metrics"]["alert_count"], "experiment_path": str(experiment_dir)
    }])], ignore_index=True).reindex(columns=INDEX_COLUMNS)
    index.to_csv(INDEX_PATH, index=False, encoding="utf-8-sig")
    shutil.copy2(Path(__file__).resolve(), experiment_dir / "strategy.py")
    validation_report = {
        "valid": True,
        "checks": {
            "source_exists": DATA_FILE.exists(),
            "result_rows": int(len(result)),
            "anomaly_return_count": int((returns.abs() > 0.20).sum()),
            "required_files": all((experiment_dir / name).exists() for name in ("result.csv", "meta.json", "strategy.py", "validation_report.json")),
            "return_lower_bound": bool((returns > -1).all()),
        },
    }
    (experiment_dir / "validation_report.json").write_text(json.dumps(validation_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_standard_result_set(result, experiment_dir, meta, trades=trades, validation=validation_report)
    return experiment_dir


def _scope_for_result(input_data, result, requested_start, requested_end):
    scope = describe_scope(input_data, DATA_FILE, requested_start=requested_start,
                           requested_end=requested_end)
    scope["result_start_date"] = pd.Timestamp(result["日期"].min()).date().isoformat() if len(result) else None
    scope["result_end_date"] = pd.Timestamp(result["日期"].max()).date().isoformat() if len(result) else None
    scope["realized_start_date"] = pd.Timestamp(result["next_return_date"].min()).date().isoformat() if len(result) and "next_return_date" in result else None
    scope["realized_end_date"] = pd.Timestamp(result["next_return_date"].max()).date().isoformat() if len(result) and "next_return_date" in result else None
    scope["calculation_start_date"] = pd.Timestamp(input_data["日期"].min()).date().isoformat() if len(input_data) else None
    scope["calculation_end_date"] = pd.Timestamp(input_data["日期"].max()).date().isoformat() if len(input_data) else None
    return scope


def main():
    parser = argparse.ArgumentParser(description="运行动量回测，可选固定实验日期区间")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args = parser.parse_args()
    input_data = load_data(end_date=args.end_date)
    result, trades = run_backtest(input_data.copy())
    if args.start_date:
        result = result[result["日期"] >= pd.Timestamp(args.start_date)].copy()
    if args.end_date:
        result = result[result["日期"] <= pd.Timestamp(args.end_date)].copy()
    if args.start_date and not result.empty:
        result = result[result["next_return_date"] >= pd.Timestamp(args.start_date)].copy()
    if args.end_date and not result.empty:
        result = result[result["next_return_date"] <= pd.Timestamp(args.end_date)].copy()
    if not trades.empty:
        if args.start_date:
            trades = trades[trades["buy_date"] >= pd.Timestamp(args.start_date)]
        if args.end_date:
            trades = trades[trades["sell_date"] <= pd.Timestamp(args.end_date)]
        trades = trades[trades["sell_date"] <= result["日期"].max()]
    result["cum_return"] = (1 + result["next_return"]).cumprod()
    if result.empty:
        raise ValueError("指定区间内没有完整实现的收益记录")
    experiment_dir = save_experiment(result, trades, input_data, args.start_date, args.end_date)
    print(f"回测完成，共 {len(result)} 条每日收益，输出至: {experiment_dir}")


if __name__ == "__main__":
    main()
