"""Strategy Lab v1.1 信号级历史表现分析模块。

本模块只统计买入信号发生后的历史结果，不参与信号生成、交易决策或回测。
"""

import json
from pathlib import Path

import pandas as pd


RETURN_WINDOWS = (1, 2, 3, 5, 10, 20, 30, 60)
EXCURSION_WINDOWS = (3, 5, 10, 20, 30, 60)
REQUIRED_QUOTES = {"日期", "板块名称", "收盘", "最高", "最低"}


def _normalise_quotes(quotes):
    """复制并标准化行情数据，避免修改调用方传入的DataFrame。"""
    missing = REQUIRED_QUOTES - set(quotes.columns)
    if missing:
        raise ValueError(f"行情数据缺少字段: {sorted(missing)}")
    data = quotes.copy()
    data["日期"] = pd.to_datetime(data["日期"], errors="coerce")
    for column in ("收盘", "最高", "最低"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data[["日期", "板块名称", "收盘", "最高", "最低"]].isna().any().any():
        raise ValueError("行情数据包含缺失或非法值")
    if (data[["收盘", "最高", "最低"]] <= 0).any().any():
        raise ValueError("价格字段必须为正数")
    return data.sort_values(["板块名称", "日期"]).reset_index(drop=True)


def _normalise_signals(signals):
    """接受DataFrame或字典列表，统一为日期/板块名称两列。"""
    if isinstance(signals, pd.DataFrame):
        result = signals.copy()
    else:
        result = pd.DataFrame(signals)
    aliases = {"signal_date": "日期", "date": "日期", "symbol": "板块名称", "name": "板块名称"}
    result = result.rename(columns={key: value for key, value in aliases.items() if key in result.columns})
    required = {"日期", "板块名称"}
    missing = required - set(result.columns)
    if missing:
        raise ValueError(f"买入信号缺少字段: {sorted(missing)}")
    result["日期"] = pd.to_datetime(result["日期"], errors="coerce")
    if result[["日期", "板块名称"]].isna().any().any():
        raise ValueError("买入信号包含缺失或非法值")
    return result[["日期", "板块名称"]].drop_duplicates().sort_values(["板块名称", "日期"]).reset_index(drop=True)


def analyze_signals(quotes, signals, observation_end=None):
    """计算逐信号未来收益、MFE、MAE和前三日风险。

    信号日收盘价作为买入价格；未来第N个交易日从信号日之后计算。
    数据不足时返回NaN，不填充为0。
    """
    data = _normalise_quotes(quotes)
    signal_rows = _normalise_signals(signals)
    end_date = pd.Timestamp(observation_end) if observation_end is not None else data["日期"].max()
    grouped = {symbol: group.reset_index(drop=True) for symbol, group in data.groupby("板块名称", sort=False)}
    records = []
    for signal in signal_rows.itertuples(index=False):
        series = grouped.get(signal.板块名称)
        if series is None:
            continue
        matches = series.index[series["日期"].eq(signal.日期)].tolist()
        if not matches:
            continue
        position = matches[0]
        entry = float(series.loc[position, "收盘"])
        record = {"signal_date": signal.日期, "symbol": signal.板块名称, "signal_price": entry}
        future = series.iloc[position + 1:]
        future = future.loc[future["日期"] <= end_date]
        for window in RETURN_WINDOWS:
            record[f"return_{window}d"] = (
                float(future.iloc[window - 1]["收盘"] / entry - 1) if len(future) >= window else float("nan")
            )
        for window in EXCURSION_WINDOWS:
            window_data = future.iloc[:window]
            record[f"mfe_{window}d"] = (
                float(window_data["最高"].max() / entry - 1) if len(window_data) >= window else float("nan")
            )
            record[f"mae_{window}d"] = (
                float(window_data["最低"].min() / entry - 1) if len(window_data) >= window else float("nan")
            )
        first_three = future.iloc[:3]
        record["loss_over_5pct_3d"] = (
            bool((first_three["最低"] / entry - 1 <= -0.05).any()) if len(first_three) >= 3 else pd.NA
        )
        record["loss_over_10pct_3d"] = (
            bool((first_three["最低"] / entry - 1 <= -0.10).any()) if len(first_three) >= 3 else pd.NA
        )
        records.append(record)
    columns = ["signal_date", "symbol", "signal_price"]
    columns += [f"return_{window}d" for window in RETURN_WINDOWS]
    columns += [f"mfe_{window}d" for window in EXCURSION_WINDOWS]
    columns += [f"mae_{window}d" for window in EXCURSION_WINDOWS]
    columns += ["loss_over_5pct_3d", "loss_over_10pct_3d"]
    return pd.DataFrame(records, columns=columns)


def _sample_grade(sample_count):
    if sample_count >= 500:
        return "A"
    if sample_count >= 100:
        return "B"
    if sample_count >= 30:
        return "C"
    return "D"


def summarize_signals(signal_results):
    """汇总信号样本，返回可直接序列化为JSON的事实统计。"""
    result = signal_results.copy()
    result["signal_date"] = pd.to_datetime(result["signal_date"], errors="coerce")
    sample_count = int(len(result))
    summary = {
        "sample_count": sample_count,
        "start_date": str(result["signal_date"].min().date()) if sample_count else None,
        "end_date": str(result["signal_date"].max().date()) if sample_count else None,
        "coverage_years": float((result["signal_date"].max() - result["signal_date"].min()).days / 365.25) if sample_count > 1 else 0.0,
        "sample_quality": _sample_grade(sample_count),
        "periods": {},
    }
    for window in RETURN_WINDOWS:
        column = f"return_{window}d"
        values = pd.to_numeric(result[column], errors="coerce").dropna()
        summary["periods"][f"{window}d"] = {
            "sample_count": int(len(values)),
            "up_probability": float((values > 0).mean()) if len(values) else None,
            "average_return": float(values.mean()) if len(values) else None,
            "average_loss": float(values[values < 0].mean()) if (values < 0).any() else None,
        }
    for window in EXCURSION_WINDOWS:
        mfe = pd.to_numeric(result[f"mfe_{window}d"], errors="coerce").dropna()
        mae = pd.to_numeric(result[f"mae_{window}d"], errors="coerce").dropna()
        summary["periods"][f"{window}d"].update({
            "mfe": float(mfe.mean()) if len(mfe) else None,
            "mae": float(mae.mean()) if len(mae) else None,
        })
    return summary


def save_signal_metrics(signal_results, output_path):
    """生成signal_metrics.json；不修改信号结果或行情数据。"""
    metrics = summarize_signals(signal_results)
    output_path = Path(output_path)
    output_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metrics
