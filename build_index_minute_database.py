"""从通达信 vipdoc 构建 6 个指数的按代码、年份分区分钟 Parquet 数据库。"""
from __future__ import annotations

import argparse
import json
import struct
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
VIPDOC = Path(r"D:\new_mpv1.28\vipdoc")
DB_ROOT = ROOT / "shared" / "data" / "analysis_db" / "minute"
REPORT_PATH = DB_ROOT / "quality_report.json"
QUARANTINE_ROOT = DB_ROOT / "quarantine"
RECORD = struct.Struct("<HHfffffII")

TARGETS = {
    "1999999": ("sh", "999999", "000001.SH", "上证综合指数"),
    "0399001": ("sz", "399001", "399001.SZ", "深证成指"),
    "2899050": ("bj", "899050", "899050.BJ", "北证50"),
    "0399006": ("sz", "399006", "399006.SZ", "创业板指数"),
    "1000688": ("sh", "000688", "000688.SH", "科创50"),
    "1000300": ("sh", "000300", "000300.SH", "沪深300"),
}


def source_path(export_code: str) -> Path:
    market, code, _, _ = TARGETS[export_code]
    return VIPDOC / market / "minline" / f"{market}{code}.lc1"


def decode(path: Path, export_code: str) -> pd.DataFrame:
    raw = path.read_bytes()
    if len(raw) % RECORD.size:
        raise ValueError(f"文件长度不是 32 字节记录整数倍: {path}")
    rows = []
    exchange_code, name = TARGETS[export_code][2:]
    for offset in range(0, len(raw), RECORD.size):
        date_num, minute_num, op, high, low, close, amount, volume, _ = RECORD.unpack_from(raw, offset)
        year = date_num // 2048 + 2004
        month = (date_num % 2048) // 100
        day = (date_num % 2048) % 100
        hour, minute = divmod(minute_num, 60)
        try:
            timestamp = datetime(year, month, day, hour, minute)
        except ValueError:
            timestamp = pd.NaT
        rows.append({
            "instrument_code": exchange_code,
            "tdx_code": export_code,
            "instrument_name": name,
            "trade_time": timestamp,
            "open": op,
            "high": high,
            "low": low,
            "close": close,
            "amount": amount,
            "volume": volume,
            "source_path": str(path),
        })
    frame = pd.DataFrame(rows)
    frame["trade_date"] = pd.to_datetime(frame["trade_time"], errors="coerce").dt.date.astype("string")
    frame["year"] = pd.to_datetime(frame["trade_time"], errors="coerce").dt.year.astype("Int64")
    return frame


def quality(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    work = frame.copy()
    work["quarantine_reason"] = ""
    bad_date = work["trade_time"].isna() | work["trade_date"].isna()
    bad_ohlc = work[["open", "high", "low", "close"]].isna().any(axis=1)
    bad_ohlc |= (work["high"] < work[["open", "close", "low"]].max(axis=1))
    bad_ohlc |= (work["low"] > work[["open", "close", "high"]].min(axis=1))
    nonpositive = (work[["open", "high", "low", "close"]] <= 0).any(axis=1)
    bad_volume = work["volume"].isna() | (work["volume"] < 0)
    bad_amount = work["amount"].isna() | (work["amount"] < 0)
    duplicate = work.duplicated(["instrument_code", "trade_time"], keep="last")
    reasons = [bad_date, bad_ohlc, nonpositive, bad_volume, bad_amount, duplicate]
    labels = ["invalid_datetime", "invalid_ohlc", "nonpositive_price", "invalid_volume", "invalid_amount", "duplicate_key"]
    for mask, label in zip(reasons, labels):
        work.loc[mask & work["quarantine_reason"].eq(""), "quarantine_reason"] = label
    bad = work[work["quarantine_reason"].ne("")].copy()
    good = work[work["quarantine_reason"].eq("")].drop(columns="quarantine_reason").copy()
    good = good.sort_values(["instrument_code", "trade_time"]).drop_duplicates(["instrument_code", "trade_time"], keep="last")
    report = {
        "rows_before": int(len(work)),
        "rows_after": int(len(good)),
        "rows_quarantined": int(len(bad)),
        "invalid_datetime": int(bad_date.sum()),
        "invalid_ohlc": int(bad_ohlc.sum()),
        "nonpositive_price": int(nonpositive.sum()),
        "invalid_volume": int(bad_volume.sum()),
        "invalid_amount": int(bad_amount.sum()),
        "duplicate_key": int(duplicate.sum()),
        "zero_volume": int((good["volume"] == 0).sum()),
        "zero_amount": int((good["amount"] == 0).sum()),
        "large_intraday_change": int((good["close"].div(good["open"]).sub(1).abs() > 0.30).sum()),
        "start_time": str(good["trade_time"].min()) if len(good) else None,
        "end_time": str(good["trade_time"].max()) if len(good) else None,
        "status": "PASS_WITH_WARNINGS" if len(bad) == 0 else "PASS_WITH_QUARANTINE",
    }
    return good, {"summary": report, "quarantine": bad}


def main() -> None:
    global VIPDOC, DB_ROOT, REPORT_PATH, QUARANTINE_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(VIPDOC))
    parser.add_argument("--output", default=str(DB_ROOT))
    args = parser.parse_args()
    VIPDOC = Path(args.source)
    DB_ROOT = Path(args.output)
    REPORT_PATH = DB_ROOT / "quality_report.json"
    QUARANTINE_ROOT = DB_ROOT / "quarantine"
    DB_ROOT.mkdir(parents=True, exist_ok=True)
    reports = {}
    for export_code in TARGETS:
        path = source_path(export_code)
        if not path.is_file():
            reports[export_code] = {"status": "MISSING_SOURCE", "source_path": str(path)}
            continue
        frame = decode(path, export_code)
        good, result = quality(frame)
        reports[export_code] = {"source_path": str(path), "records": result["summary"]}
        if len(result["quarantine"]):
            QUARANTINE_ROOT.mkdir(parents=True, exist_ok=True)
            result["quarantine"].to_parquet(QUARANTINE_ROOT / f"instrument_code={TARGETS[export_code][2]}.parquet", index=False)
        for year, part in good.groupby("year", dropna=True):
            target = DB_ROOT / f"instrument_code={export_code}" / f"year={int(year)}" / "part.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            part.drop(columns="year").to_parquet(target, index=False)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "layout": "instrument_code=CODE/year=YYYY/part.parquet", "instruments": reports}
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
