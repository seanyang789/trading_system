"""统一实验结果分析：所有策略使用同一套指标口径。"""
import json
from pathlib import Path

import pandas as pd


REQUIRED_RESULT_COLUMNS = {"日期", "板块名称", "next_return", "cum_return"}


def analyze_result(result):
    missing = REQUIRED_RESULT_COLUMNS - set(result.columns)
    if missing:
        raise ValueError(f"结果缺少字段: {sorted(missing)}")
    result = result.sort_values("日期").reset_index(drop=True)
    returns = pd.to_numeric(result["next_return"], errors="coerce")
    if "next_return_date" in result:
        realized_dates = pd.to_datetime(result["next_return_date"], errors="coerce")
        valid_realized = realized_dates.notna()
        result = result.loc[valid_realized].copy().reset_index(drop=True)
        returns = pd.to_numeric(result["next_return"], errors="coerce")
    equity = pd.to_numeric(result["cum_return"], errors="coerce")
    peak = equity.cummax()
    drawdown = equity / peak - 1
    periods = max(len(result), 1)
    annualized_return = float(equity.iloc[-1] ** (252 / periods) - 1) if len(result) and equity.iloc[-1] > 0 else None
    annualized_volatility = float(returns.std(ddof=1) * (252 ** 0.5)) if len(result) > 1 else None
    sharpe = float(annualized_return / annualized_volatility) if annualized_return is not None and annualized_volatility and annualized_volatility > 0 else None
    max_dd = float(drawdown.min()) if len(result) else None
    calmar = float(annualized_return / abs(max_dd)) if annualized_return is not None and max_dd and max_dd < 0 else None
    loss_groups = (returns >= 0).cumsum()
    max_consecutive_loss_days = int(returns.lt(0).groupby(loss_groups).sum().max()) if len(result) else 0
    active = pd.to_numeric(result["active_count"], errors="coerce") if "active_count" in result else pd.Series(dtype=float)
    return {
        "rows": int(len(result)),
        "start_date": str(result["日期"].iloc[0]) if len(result) else None,
        "end_date": str(result["日期"].iloc[-1]) if len(result) else None,
        "final_net_value": float(equity.iloc[-1]) if len(result) else None,
        "cumulative_return": float(equity.iloc[-1] - 1) if len(result) else None,
        "annualized_return": annualized_return,
        "max_drawdown": max_dd,
        "calmar_ratio": calmar,
        "win_rate": float((returns > 0).mean()) if len(result) else None,
        "average_return": float(returns.mean()) if len(result) else None,
        "volatility": float(returns.std(ddof=1)) if len(result) > 1 else None,
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": sharpe,
        "positive_days": int((returns > 0).sum()),
        "negative_days": int((returns < 0).sum()),
        "flat_days": int((returns == 0).sum()),
        "max_consecutive_loss_days": max_consecutive_loss_days,
        "average_active_count": float(active.mean()) if len(active) else None,
        "max_active_count": int(active.max()) if len(active) else None,
        "flat_exposure_ratio": float((active == 0).mean()) if len(active) else None,
        "abnormal_return_count": int((returns.abs() > 0.20).sum()),
        "missing_return_count": int(returns.isna().sum()),
        "invalid_return_count": int((returns <= -1).sum()),
        "date_sorted": bool(result["日期"].is_monotonic_increasing),
        "duplicate_date_industry_count": int(result.duplicated(["日期", "板块名称"]).sum()),
    }


def analyze_file(path):
    path = Path(path)
    result = pd.read_csv(path, encoding="utf-8-sig")
    return analyze_result(result)


if __name__ == "__main__":
    import sys
    print(json.dumps(analyze_file(sys.argv[1]), ensure_ascii=False, indent=2))
