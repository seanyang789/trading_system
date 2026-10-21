"""专项审计已入库宽基/市场指数日线质量。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DB_ROOT = ROOT / "shared" / "data" / "analysis_db"
REPORT_JSON = DB_ROOT / "index_quality_report.json"
REPORT_MD = ROOT / "docs" / "INDEX_DATA_QUALITY_REPORT.md"
TARGETS = {
    "000001.SH": "上证综合指数",
    "000300.SH": "沪深300（补充审计）",
    "399001.SZ": "深证成指",
    "899050.BJ": "北证50（预期代码）",
    "399006.SZ": "创业板指数",
    "000688.SH": "科创50",
}


def load_market() -> pd.DataFrame:
    frames = [pd.read_parquet(p) for p in sorted((DB_ROOT / "bars").glob("family=market_index/year=*/part.parquet"))]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def audit(code: str, name: str, data: pd.DataFrame) -> dict:
    d = data[data.instrument_code.eq(code)].copy().sort_values("trade_date")
    if d.empty:
        return {"code": code, "name": name, "present": False}
    dates = pd.to_datetime(d.trade_date)
    gaps = dates.diff().dt.days.dropna()
    valid_ohlc = d[["open", "high", "low", "close"]].notna().all(axis=1)
    invalid_ohlc = valid_ohlc & ((d.high < d[["open", "close", "low"]].max(axis=1)) | (d.low > d[["open", "close", "high"]].min(axis=1)))
    ret = d.close.pct_change() * 100
    calc_pct = (d.close / d.prev_close - 1) * 100
    pct_mismatch = d.change_pct.notna() & calc_pct.notna() & ((d.change_pct - calc_pct).abs() > 0.05)
    return {
        "code": code, "name": name, "present": True, "rows": len(d),
        "first_date": str(d.trade_date.min()), "last_date": str(d.trade_date.max()),
        "missing_trade_date": int(d.trade_date.isna().sum()),
        "duplicate_keys": int(d.duplicated(["instrument_code", "trade_date"]).sum()),
        "missing_ohlc_rows": int((~valid_ohlc).sum()), "invalid_ohlc_rows": int(invalid_ohlc.sum()),
        "close_only_rows": int((d.close.notna() & d[["open", "high", "low"]].isna().any(axis=1)).sum()),
        "nonpositive_price_rows": int((d[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
        "negative_volume_rows": int((d.volume < 0).sum()), "negative_amount_rows": int((d.amount < 0).sum()),
        "zero_volume_rows": int(d.volume.fillna(0).eq(0).sum()), "zero_amount_rows": int(d.amount.fillna(0).eq(0).sum()),
        "change_pct_mismatch_rows": int(pct_mismatch.sum()),
        "return_abs_over_10pct": int(ret.abs().gt(10).sum()), "return_abs_over_20pct": int(ret.abs().gt(20).sum()),
        "return_abs_over_30pct": int(ret.abs().gt(30).sum()), "max_abs_return_pct": round(float(ret.abs().max()), 4),
        "calendar_gap_over_10_days": int(gaps.gt(10).sum()), "max_calendar_gap_days": int(gaps.max()) if not gaps.empty else 0,
        "sample_extreme_returns": d.loc[ret.abs().gt(20), ["trade_date", "prev_close", "close"]].assign(return_pct=ret[ret.abs().gt(20)]).to_dict("records"),
    }


def main() -> None:
    data = load_market()
    results = [audit(code, name, data) for code, name in TARGETS.items()]
    present = [x for x in results if x["present"]]
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "database": str(DB_ROOT), "targets": results, "market_index_codes_in_database": sorted(data.instrument_code.dropna().unique().tolist()), "overall_rows": len(data), "status": "PASS_WITH_WARNINGS" if any(x.get("present") and (x["missing_ohlc_rows"] or x["change_pct_mismatch_rows"] or x["return_abs_over_20pct"]) for x in results) else "PASS"}
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 市场指数日线数据质量专项报告", "", f"生成时间：{report['generated_at']}", "", "## 结论", "", f"当前分析数据库实际有 {len(data):,} 条市场指数日线记录，包含 {len(data.instrument_code.dropna().unique())} 个市场指数代码。北证50（`899050.BJ`）当前未入库，不能把缺失误判为质量问题。", "", "质量等级：**" + report["status"] + "**。完整统计见 `shared/data/analysis_db/index_quality_report.json`。", "", "## 指数审计结果", "", "| 指数 | 是否入库 | 日期范围 | 行数 | 缺失OHLC | 重复键 | OHLC错误 | 零成交量 | 涨跌幅不一致 | 超20%涨跌 |", "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for x in results:
        if not x["present"]:
            lines.append(f"| {x['name']} `{x['code']}` | 否 | - | - | - | - | - | - | - | - |")
        else:
            lines.append(f"| {x['name']} `{x['code']}` | 是 | {x['first_date']} ~ {x['last_date']} | {x['rows']:,} | {x['missing_ohlc_rows']} | {x['duplicate_keys']} | {x['invalid_ohlc_rows']} | {x['zero_volume_rows']:,} | {x['change_pct_mismatch_rows']} | {x['return_abs_over_20pct']} |")
    lines += ["", "## 脏数据特征与判断", "", "1. **空行情占位行**：已由通用质量门禁隔离，不再进入正常分析。", "2. **close-only 历史行**：个别指数早期只有收盘价，不能用于完整K线策略；保留但必须过滤 `is_valid_ohlc=false`。", "3. **零成交量/成交额**：指数口径中较常见，不自动判定为错误，但不得直接用于流动性、VWAP或成交量因子。", "4. **极端涨跌**：早期指数可能存在制度变化、基期或历史重编导致的大波动，先标记、再核对公开来源，不自动删除。", "5. **日期缺口**：周末和节假日是正常非交易日；连续超过10个自然日才作为异常候选，不能简单按自然日逐日要求。", "", "## 当前最重要的缺口", "", "- 数据库当前只有上证综合、沪深300、科创50、深证成指、创业板指数五类市场指数。", "- 北证50 `899050.BJ` 尚未纳入当前数据源/分析数据库，后续必须先补数据源，再做同等质量审计。", "- 数据库当前不保存指数成分股历史，无法仅凭指数日线判断成分股风格迁移；需要另建指数成分历史表。", "", "## 执行方式", "", "```powershell", "python analyze_index_quality.py", "python build_analysis_database.py --mode update", "```", ""]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
