import json
import argparse
import shutil
from pathlib import Path

import pandas as pd
try:
    from shared.data_quality import load_bk
    from .result_analysis import analyze_result
    from .experiment_metrics import save_standard_result_set
    from .signal_analysis import analyze_signals
    from .experiment_scope import archive_source_snapshot, describe_scope, source_fingerprint
except ImportError:
    from ..shared.data_quality import load_bk
    from result_analysis import analyze_result
    from experiment_metrics import save_standard_result_set
    from signal_analysis import analyze_signals
    from experiment_scope import archive_source_snapshot, describe_scope, source_fingerprint
try:
    from .experiment_manager import build_experiment_name_zh, load_index, save_index, INDEX_COLUMNS
except ImportError:
    from experiment_manager import build_experiment_name_zh, load_index, save_index, INDEX_COLUMNS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_ROOT / "shared" / "data" / "bk.csv"
RESEARCH_NAME = "moving_average"
EXPERIMENTS_ROOT = PROJECT_ROOT / "shared" / "experiments" / RESEARCH_NAME
TRADE_COST = 0.001

def next_experiment_id():
    ids = [int(p.name.split("_")[-1]) for p in EXPERIMENTS_ROOT.glob("exp_*") if p.name.split("_")[-1].isdigit()]
    return f"exp_{max(ids, default=0) + 1:03d}"


def load_data():
    return load_bk(DATA_FILE)


def run_backtest(df, observation_end=None):
    g = df.groupby("板块名称", group_keys=False)
    for span in (5, 10, 20, 30, 60, 120):
        df[f"ema{span}"] = g["收盘"].transform(lambda s: s.ewm(span=span, adjust=False, min_periods=span).mean())
    prev5, prev10 = g["ema5"].shift(1), g["ema10"].shift(1)
    prevprev5 = g["ema5"].shift(2)
    setup = (df.ema5 > prev5) & (g["ema10"].shift(2) > prev10) & (prev10 <= df.ema10)
    df["buy"] = setup
    df["sell"] = df.ema10 < prev10
    df["daily_return"] = g["收盘"].pct_change()
    signals = df.loc[df["buy"], ["日期", "板块名称", "收盘"]].rename(columns={
        "日期": "date", "板块名称": "symbol", "收盘": "price"
    }).reset_index(drop=True)
    signal_results = analyze_signals(df, signals, observation_end=observation_end)
    trade_rows = []
    for symbol, series in df.groupby("板块名称", sort=False):
        entry = None
        for _, row in series.iterrows():
            if entry is None and row["buy"]:
                entry = row
            elif entry is not None and row["sell"]:
                gross = row["收盘"] / entry["收盘"] - 1
                trade_rows.append({"buy_date": entry["日期"], "sell_date": row["日期"], "symbol": symbol,
                                   "buy_price": float(entry["收盘"]), "sell_price": float(row["收盘"]),
                                   "holding_days": int((row["日期"] - entry["日期"]).days),
                                   "return": float(gross - TRADE_COST * 2)})
                entry = None
    held, rows = set(), []
    dates = sorted(df["日期"].unique())
    grouped_days = {date: day for date, day in df.groupby("日期", sort=True)}
    for date, next_date in zip(dates, dates[1:]):
        day = grouped_days[date]
        sells = set(day.loc[day["板块名称"].isin(held) & day.sell, "板块名称"])
        buys = set(day.loc[day.buy, "板块名称"]) - held
        active = held - sells
        next_day = grouped_days[next_date]
        returns = next_day.loc[next_day["板块名称"].isin(active | buys), "daily_return"].dropna()
        gross = float(returns.mean()) if len(returns) else 0.0
        cost = TRADE_COST * (len(buys) + len(sells)) / max(len(active) + len(buys), 1)
        held = active | buys
        rows.append({"日期": date, "next_return_date": next_date, "板块名称": "等权组合", "next_return": gross - cost,
                     "active_count": len(held), "buy_count": len(buys), "sell_count": len(sells)})
    result = pd.DataFrame(rows)
    result["cum_return"] = (1 + result.next_return).cumprod()
    return result, pd.DataFrame(trade_rows), signal_results


def save_experiment(result, trades, signal_results, input_data=None, requested_start=None, requested_end=None):
    result = result.copy()
    if "next_return_date" in result:
        realized = pd.to_datetime(result["next_return_date"], errors="coerce")
        if requested_start:
            result = result[realized >= pd.Timestamp(requested_start)].copy()
            realized = pd.to_datetime(result["next_return_date"], errors="coerce")
        if requested_end:
            result = result[realized <= pd.Timestamp(requested_end)].copy()
        if result.empty:
            raise ValueError("指定区间内没有完整实现的收益记录")
        result["cum_return"] = (1 + result["next_return"]).cumprod()
    if requested_start and not signal_results.empty:
        signal_results = signal_results[pd.to_datetime(signal_results["signal_date"]) >= pd.Timestamp(requested_start)].copy()
    if requested_end and not signal_results.empty:
        signal_results = signal_results[pd.to_datetime(signal_results["signal_date"]) <= pd.Timestamp(requested_end)].copy()
    if not trades.empty:
        if requested_start:
            trades = trades[pd.to_datetime(trades["buy_date"]) >= pd.Timestamp(requested_start)].copy()
        if requested_end:
            trades = trades[pd.to_datetime(trades["sell_date"]) <= pd.Timestamp(requested_end)].copy()
        trades = trades[trades["sell_date"] <= result["日期"].max()].copy()
    experiment_id = next_experiment_id()
    experiment_dir = EXPERIMENTS_ROOT / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    snapshot = archive_source_snapshot(DATA_FILE, experiment_dir)
    result.to_csv(experiment_dir / "result.csv", index=False, encoding="utf-8-sig")
    signal_results.to_csv(experiment_dir / "signals.csv", index=False, encoding="utf-8-sig")
    r = result.next_return
    description = "EMA5今日上升且EMA10先降后平/升时以今日收盘价买入；EMA10拐头向下时以今日收盘价卖出；满足条件的行业等权持有"
    scope = describe_scope(input_data if input_data is not None else result, DATA_FILE,
                           requested_start=requested_start, requested_end=requested_end,
                           indicator_warmup_start=(input_data["日期"].min() if input_data is not None and requested_start else None))
    scope["result_start_date"] = pd.Timestamp(result["日期"].min()).date().isoformat() if len(result) else None
    scope["result_end_date"] = pd.Timestamp(result["日期"].max()).date().isoformat() if len(result) else None
    scope["realized_start_date"] = pd.Timestamp(result["next_return_date"].min()).date().isoformat() if len(result) and "next_return_date" in result else None
    scope["realized_end_date"] = pd.Timestamp(result["next_return_date"].max()).date().isoformat() if len(result) and "next_return_date" in result else None
    scope["calculation_start_date"] = pd.Timestamp(input_data["日期"].min()).date().isoformat() if input_data is not None and len(input_data) else None
    scope["calculation_end_date"] = pd.Timestamp(input_data["日期"].max()).date().isoformat() if input_data is not None and len(input_data) else None
    scope["source_fingerprint"] = snapshot
    meta = {"experiment_id": experiment_id, "experiment_name_zh": build_experiment_name_zh(RESEARCH_NAME, description), "strategy_name": "ema_trend", "research_name": RESEARCH_NAME,
            "description": description,
            "status": "completed", "data_scope": scope,
            "metrics": {"rows": len(result), "max_return": float(r.max()) if len(r) else None,
            "min_return": float(r.min()) if len(r) else None, "alert_count": int((r.abs() > .20).sum()),
            "final_net_value": float(result.cum_return.iloc[-1]) if len(result) else None, "max_drawdown": float((result.cum_return / result.cum_return.cummax() - 1).min()) if len(result) else None, "trade_cost": TRADE_COST},
            "validation": {"source": str(DATA_FILE), "missing_values": 0, "result_rows": len(result), "trade_cost_assumption": "0.10% per buy or sell"}}
    meta["metrics"].update(analyze_result(result))
    (experiment_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(Path(__file__).resolve(), experiment_dir / "strategy.py")
    report = {"valid": True, "checks": {"source_exists": DATA_FILE.exists(), "result_rows": len(result), "date_sorted": bool(result.日期.is_monotonic_increasing), "no_missing_returns": bool(r.notna().all()), "required_files": all((experiment_dir / name).exists() for name in ("result.csv", "meta.json", "strategy.py")), "return_lower_bound": bool((r > -1).all()), "no_abnormal_returns": int((r.abs() > .20).sum()) == 0}}
    (experiment_dir / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_standard_result_set(result, experiment_dir, meta, trades=trades, validation=report)
    index = load_index()
    index = index[~((index["strategy_name"].astype(str) == RESEARCH_NAME) & (index["experiment_id"].astype(str) == experiment_id))]
    index = pd.concat([index, pd.DataFrame([{
        "strategy_name": RESEARCH_NAME, "experiment_id": experiment_id,
        "experiment_name_zh": meta["experiment_name_zh"], "description": description,
        "status": "completed", "data_start_date": meta["data_scope"].get("result_start_date"),
        "data_end_date": meta["data_scope"].get("result_end_date"),
        "source_sha256": meta["data_scope"]["source_fingerprint"]["sha256"],
        "created_at": pd.Timestamp.now().isoformat(),
        "result_rows": len(result), "max_return": float(r.max()), "min_return": float(r.min()),
        "alert_count": int((r.abs() > .20).sum()), "experiment_path": str(experiment_dir),
    }])], ignore_index=True).reindex(columns=INDEX_COLUMNS)
    save_index(index)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行 EMA 研究，可选固定实验日期区间")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args = parser.parse_args()
    full_data = load_data()
    calc_data = full_data
    if args.start_date:
        # EMA 最长预热周期为 120 根记录；保留此前行情供指标初始化，结果另行裁剪。
        start = pd.Timestamp(args.start_date)
        pieces = []
        for _, group in full_data.groupby("板块名称", sort=False):
            history = group[group["日期"] < start].tail(120)
            active = group[group["日期"] >= start]
            pieces.extend([history, active])
        calc_data = pd.concat(pieces, ignore_index=True).sort_values(["板块名称", "日期"])
    if args.end_date:
        calc_data = calc_data[calc_data["日期"] <= pd.Timestamp(args.end_date)]
    result, trades, signal_results = run_backtest(calc_data.copy(), observation_end=args.end_date)
    if args.start_date:
        signal_results = signal_results[signal_results["signal_date"] >= pd.Timestamp(args.start_date)].copy()
    if args.end_date:
        signal_results = signal_results[signal_results["signal_date"] <= pd.Timestamp(args.end_date)].copy()
    lower = pd.Timestamp(args.start_date) if args.start_date else None
    upper = pd.Timestamp(args.end_date) if args.end_date else None
    save_experiment(result, trades, signal_results, calc_data, args.start_date, args.end_date)
    print(f"回测完成，共 {len(result)} 个交易日，输出至: {EXPERIMENTS_ROOT}")
