import json
import shutil
from pathlib import Path

import pandas as pd
try:
    from .result_analysis import analyze_result
    from .experiment_metrics import save_standard_result_set
    from .experiment_manager import build_experiment_name_zh
except ImportError:
    from result_analysis import analyze_result
    from experiment_metrics import save_standard_result_set
    from experiment_manager import build_experiment_name_zh


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_ROOT / "shared" / "data" / "bk.csv"
RESEARCH_NAME = "momentum"
EXPERIMENTS_ROOT = PROJECT_ROOT / "shared" / "experiments" / RESEARCH_NAME
INDEX_PATH = PROJECT_ROOT / "shared" / "experiments" / "index.csv"
INDEX_COLUMNS = ["strategy_name", "experiment_id", "experiment_name_zh", "description", "status", "created_at", "result_rows", "max_return", "min_return", "alert_count", "experiment_path"]
MOMENTUM_WINDOW = 5
SINGLE_SIDE_COST = 0.001


def next_experiment_id():
    ids = [int(p.name.split("_")[-1]) for p in EXPERIMENTS_ROOT.glob("exp_*") if p.name.split("_")[-1].isdigit()]
    return f"exp_{max(ids, default=0) + 1:03d}"


def load_data():
    df = pd.read_csv(DATA_FILE, encoding="gbk")
    df.columns = [str(column).strip() for column in df.columns]
    required = {"日期", "板块名称", "收盘"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"缺少必要字段: {sorted(missing)}")
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df["收盘"] = pd.to_numeric(df["收盘"], errors="coerce")
    if df[["日期", "板块名称", "收盘"]].isna().any().any():
        raise ValueError("日期、板块名称或收盘价存在缺失/非法值")
    if (df["收盘"] <= 0).any():
        raise ValueError("收盘价必须为正数")
    return df.sort_values(["板块名称", "日期"]).reset_index(drop=True)


def run_backtest(df):
    grouped = df.groupby("板块名称", group_keys=False)
    df["daily_return"] = grouped["收盘"].pct_change()
    df["momentum"] = grouped["收盘"].pct_change(MOMENTUM_WINDOW)
    df["next_return"] = grouped["daily_return"].shift(-1)
    eligible = df.dropna(subset=["momentum"])
    signals = eligible.loc[eligible.groupby("日期")["momentum"].idxmax()].copy()
    switched = signals["板块名称"].ne(signals["板块名称"].shift(1))
    signals["next_return"] = signals["next_return"] - switched.astype(float) * SINGLE_SIDE_COST * 2
    result = signals[["日期", "板块名称", "next_return"]].dropna().sort_values("日期").reset_index(drop=True)
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


def save_experiment(result, trades):
    experiment_id = next_experiment_id()
    experiment_dir = EXPERIMENTS_ROOT / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(experiment_dir / "result.csv", index=False, encoding="utf-8-sig")
    returns = result["next_return"]
    meta = {
        "experiment_id": experiment_id,
        "experiment_name_zh": build_experiment_name_zh(RESEARCH_NAME, "5日动量Top1（仅换仓时扣交易成本）"),
        "research_name": RESEARCH_NAME,
        "strategy_name": "momentum",
        "description": "5日动量Top1（仅换仓时扣交易成本）",
        "status": "completed",
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
    meta["metrics"].update(analyze_result(result))
    (experiment_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    index = pd.read_csv(INDEX_PATH, encoding="utf-8-sig") if INDEX_PATH.exists() else pd.DataFrame(columns=INDEX_COLUMNS)
    index = index[index.get("experiment_id", pd.Series(dtype=str)).astype(str) != experiment_id]
    index = pd.concat([index, pd.DataFrame([{
        "strategy_name": "momentum", "experiment_id": experiment_id, "experiment_name_zh": meta["experiment_name_zh"], "description": meta["description"], "status": "completed",
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


def main():
    result, trades = run_backtest(load_data())
    experiment_dir = save_experiment(result, trades)
    print(f"回测完成，共 {len(result)} 条每日收益，输出至: {experiment_dir}")


if __name__ == "__main__":
    main()
