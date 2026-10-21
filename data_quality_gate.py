"""Quality gate for the daily analysis database.

The gate is deliberately conservative: it removes only rows that cannot be
used as observations, preserves close-only rows with an explicit flag, and
never changes the raw source files.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
DB_ROOT = ROOT / "shared" / "data" / "analysis_db"
PARQUET_ROOT = DB_ROOT / "bars"
SQLITE_PATH = DB_ROOT / "analysis.db"
REPORT_PATH = DB_ROOT / "quality_report.json"
QUARANTINE_PATH = DB_ROOT / "quality_quarantine.parquet"


def inspect_and_repair(repair: bool = True) -> dict:
    rows = []
    quarantine = []
    file_reports = []
    for path in sorted(PARQUET_ROOT.glob("family=*/year=*/part.parquet")):
        frame = pd.read_parquet(path)
        before = len(frame)
        identity_bad = frame["instrument_code"].isna() | frame["trade_date"].isna()
        close_missing = frame["close"].isna()
        all_ohlc_missing = frame[["open", "high", "low", "close"]].isna().all(axis=1)
        invalid_ohlc = (
            frame["open"].notna() & frame["high"].notna() & frame["low"].notna() & frame["close"].notna()
            & ((frame["high"] < frame[["open", "close", "low"]].max(axis=1)) | (frame["low"] > frame[["open", "close", "high"]].min(axis=1)))
        )
        nonpositive = (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        duplicate = frame.duplicated(["instrument_code", "trade_date"], keep="last")
        remove = identity_bad | all_ohlc_missing | invalid_ohlc | nonpositive | duplicate
        bad = frame[remove].copy()
        if not bad.empty:
            bad["quarantine_reason"] = "identity_or_empty_ohlc_or_invalid_ohlc_or_duplicate"
            quarantine.append(bad)
        if repair:
            frame = frame.loc[~remove].copy()
            frame["quality_status"] = "valid_ohlc"
            close_only = frame[["open", "high", "low"]].isna().any(axis=1) & frame["close"].notna()
            frame.loc[close_only, "quality_status"] = "close_only"
            frame["is_valid_ohlc"] = frame["quality_status"].eq("valid_ohlc")
            frame["is_zero_volume"] = frame["volume"].fillna(0).eq(0)
            frame["is_zero_amount"] = frame["amount"].fillna(0).eq(0)
            frame["large_return_flag"] = False
            valid_prev = frame["prev_close"].notna() & frame["prev_close"].gt(0) & frame["close"].notna()
            frame.loc[valid_prev, "large_return_flag"] = ((frame.loc[valid_prev, "close"] / frame.loc[valid_prev, "prev_close"] - 1).abs() > 0.30)
            frame.to_parquet(path, index=False)
            rows.append(frame)
        file_reports.append({
            "path": str(path.relative_to(DB_ROOT)), "rows_before": before,
            "rows_after": int((~remove).sum()) if repair else before,
            "removed": int(remove.sum()), "close_only": int((close_missing & ~remove).sum()),
            "zero_volume": int(frame["volume"].fillna(0).eq(0).sum()) if repair else int(frame["volume"].fillna(0).eq(0).sum()),
            "large_return": int(frame.get("large_return_flag", pd.Series(False, index=frame.index)).sum()) if repair else 0,
        })
    if not repair:
        return {"repair": False, "files": file_reports}
    if quarantine:
        pd.concat(quarantine, ignore_index=True).to_parquet(QUARANTINE_PATH, index=False)
    elif QUARANTINE_PATH.exists():
        QUARANTINE_PATH.unlink()
    all_data = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS data_quality_run (run_id TEXT PRIMARY KEY, run_at TEXT, rows_before INTEGER, rows_after INTEGER, rows_quarantined INTEGER, close_only INTEGER, zero_volume INTEGER, large_return INTEGER, status TEXT)")
    conn.execute("DELETE FROM instrument")
    if not all_data.empty:
        for code, part in all_data.groupby("instrument_code"):
            conn.execute("INSERT INTO instrument VALUES (?,?,?,?,?,?,?)", (code, part["instrument_name"].dropna().iloc[-1] if not part["instrument_name"].dropna().empty else None, part["family"].iloc[0], part["trade_date"].min(), part["trade_date"].max(), len(part), datetime.now().isoformat(timespec="seconds")))
    now = datetime.now().isoformat(timespec="seconds")
    summary = {"run_at": now, "repair": True, "files": file_reports, "rows_before": sum(x["rows_before"] for x in file_reports), "rows_after": len(all_data), "rows_quarantined": sum(x["removed"] for x in file_reports), "close_only": int((all_data.get("quality_status", pd.Series(dtype=str)) == "close_only").sum()), "zero_volume": int(all_data.get("is_zero_volume", pd.Series(dtype=bool)).sum()), "large_return": int(all_data.get("large_return_flag", pd.Series(dtype=bool)).sum()), "status": "PASS"}
    conn.execute("INSERT INTO data_quality_run VALUES (?,?,?,?,?,?,?,?,?)", (now, now, summary["rows_before"], summary["rows_after"], summary["rows_quarantined"], summary["close_only"], summary["zero_volume"], summary["large_return"], summary["status"]))
    conn.commit()
    conn.close()
    REPORT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(inspect_and_repair(True), ensure_ascii=False, indent=2))
