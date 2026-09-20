"""按 EMA 研究规则提取单个板块的全部买点并生成通达信公式。"""
import random
from pathlib import Path

import pandas as pd

try:
    from shared.data_quality import load_bk
except ImportError:
    from ..shared.data_quality import load_bk


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "shared" / "data" / "bk.csv"


def find_buy_points(target=None, seed=None):
    df = load_bk(DATA)
    g = df.groupby("板块名称", group_keys=False)
    for span in (5, 10):
        df[f"ema{span}"] = g["收盘"].transform(lambda s: s.ewm(span=span, adjust=False, min_periods=span).mean())
    prev5 = g["ema5"].shift(1)
    prev10, prevprev10 = g["ema10"].shift(1), g["ema10"].shift(2)
    df["buy"] = (df.ema5 > prev5) & (prevprev10 > prev10) & (prev10 <= df.ema10)
    df["sell"] = df.ema10 < prev10
    names = sorted(df["板块名称"].dropna().unique())
    if target is None:
        target = random.Random(seed).choice(names)
    if target not in names:
        raise ValueError(f"找不到板块: {target}")
    selected = df[(df["板块名称"] == target) & df.buy].copy()
    code = str(df.loc[df["板块名称"] == target, "板块编码"].iloc[0]) if "板块编码" in df else "未知"
    series = df[df["板块名称"] == target].copy()
    trades, entry = [], None
    for _, row in series.iterrows():
        if entry is None and row["buy"]:
            entry = row
        elif entry is not None and row["sell"]:
            gross = row["收盘"] / entry["收盘"] - 1
            trades.append({"return": gross - 0.001 - 0.001, "days": (row["日期"] - entry["日期"]).days})
            entry = None
    if trades:
        tr = pd.DataFrame(trades)
        trade_metrics = {"completed_trades": len(trades), "trade_win_rate": float((tr["return"] > 0).mean()),
                         "average_trade_return": float(tr["return"].mean()), "cumulative_trade_return": float((1 + tr["return"]).prod() - 1),
                         "average_holding_days": float(tr["days"].mean()), "max_trade_loss": float(tr["return"].min())}
    else:
        trade_metrics = {"completed_trades": 0, "note": "暂无可计算的完整交易"}
    dates = ("1" + selected["日期"].dt.strftime("%y%m%d")).tolist()
    lines = [f"D{i}:=DATE={date};" for i, date in enumerate(dates, 1)]
    formula = "\n".join(lines + ([f"\nBUY_DAY:=" + " OR ".join(f"D{i}" for i in range(1, len(dates) + 1)) + ";"] if dates else ["\nBUY_DAY:=0;"]))
    formula += "\n\nSTICKLINE(BUY_DAY,O,C,2,0),COLORRED;\nSTICKLINE(BUY_DAY,H,L,0,0),COLORRED;\nSTICKLINE(NOT(BUY_DAY),O,C,2,0),COLORSTICK;\nSTICKLINE(NOT(BUY_DAY),H,L,0,0),COLORSTICK;"
    return {"name": target, "code": code, "buy_dates": selected["日期"].dt.strftime("%Y-%m-%d").tolist(), "formula": formula,
            "research_rules": {"research_name": "moving_average", "experiment_id": "exp_003",
                               "buy": "今日EMA5>昨日EMA5、前日EMA10>昨日EMA10、昨日EMA10<=今日EMA10，以今日收盘价买入",
                               "sell": "今日EMA10<昨日EMA10，以今日收盘价卖出",
                               "holding": "满足条件的行业等权持有，卖出后可重新买入",
                               "cost": "买入或卖出单边0.10%"}, "trade_metrics": trade_metrics}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    result = find_buy_points(args.target, args.seed)
    rules = result["research_rules"]
    tm = result["trade_metrics"]
    metrics = (f"- 完成交易数: {tm['completed_trades']}\n- 交易胜率: {tm.get('trade_win_rate', '暂无')}\n- 平均单笔收益: {tm.get('average_trade_return', '暂无')}\n- 累计交易收益: {tm.get('cumulative_trade_return', '暂无')}\n- 平均持有天数: {tm.get('average_holding_days', '暂无')}\n- 最大单笔亏损: {tm.get('max_trade_loss', '暂无')}" if tm["completed_trades"] else "- 暂无可计算的完整交易")
    print(f"研究方向: {rules['research_name']}\n实验编号: {rules['experiment_id']}\n标的名称: {result['name']}\n标的代码: {result['code']}\n买点数量: {len(result['buy_dates'])}个\n研究规则:\n- 买入: {rules['buy']}\n- 卖出: {rules['sell']}\n{metrics}\n- 持仓: {rules['holding']}\n- 成本: {rules['cost']}\n买点日期: {', '.join(result['buy_dates']) or '无'}\n\n完整通达信公式:\n{result['formula']}")
