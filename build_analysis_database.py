"""Build and incrementally update the daily analysis database.

The database is intentionally separate from ``shared/data/数据源``:
- ``shared/data/analysis_db/bars/``: partitioned Parquet daily bars
- ``shared/data/analysis_db/analysis.db``: SQLite metadata, coverage and ingest log

Examples:
    python build_analysis_database.py --mode build
    python build_analysis_database.py --mode update
    python build_analysis_database.py --mode status
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from data_quality_gate import inspect_and_repair


ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "shared" / "data" / "数据源"
DB_ROOT = ROOT / "shared" / "data" / "analysis_db"
PARQUET_ROOT = DB_ROOT / "bars"
SQLITE_PATH = DB_ROOT / "analysis.db"

MARKET_CODES = {
    "000001.SH": "上证综合指数",
    "000300.SH": "沪深300",
    "000688.SH": "科创50",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指数",
}


def decode_bytes(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace"), "utf-8"


def csv_encoding(path_or_bytes) -> str:
    if isinstance(path_or_bytes, Path):
        raw = path_or_bytes.read_bytes()[:8192]
    else:
        raw = path_or_bytes[:8192]
    text, enc = decode_bytes(raw)
    header = next(csv.reader(io.StringIO(text.splitlines()[0] if text.splitlines() else "")), [])
    if any("指数代码" in x or "交易日期" in x for x in header):
        return enc
    return enc


def read_csv_source(source, source_path: str) -> pd.DataFrame:
    if isinstance(source, Path):
        raw = source.read_bytes()
    else:
        raw = source
    text, enc = decode_bytes(raw)
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    df["source_path"] = source_path
    return df


def col(df: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series([None] * len(df), index=df.index, dtype="object")


def col_or_position(df: pd.DataFrame, position: int, *names: str) -> pd.Series:
    """Use semantic names first; source files have a stable positional fallback."""
    result = col(df, *names)
    if result.notna().any() or position >= len(df.columns):
        return result
    return df.iloc[:, position]


def normalize(df: pd.DataFrame, family: str, source_path: str) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["instrument_code"] = col_or_position(df, 0, "指数代码", "TS指数代码", "代码", "ts_code").astype("string").str.strip()
    out["instrument_name"] = col_or_position(df, 1, "指数名称", "名称", "板块名称").astype("string").str.strip()
    out["trade_date"] = pd.to_datetime(col_or_position(df, 2, "交易日期", "日期", "trade_date"), errors="coerce").dt.date.astype("string")
    out["open"] = pd.to_numeric(col_or_position(df, 3, "开盘价", "开盘点数", "开盘点位", "开盘", "open"), errors="coerce")
    out["high"] = pd.to_numeric(col_or_position(df, 4, "最高价", "最高点数", "最高点位", "最高", "high"), errors="coerce")
    out["low"] = pd.to_numeric(col_or_position(df, 5, "最低价", "最低点数", "最低点位", "最低", "low"), errors="coerce")
    out["close"] = pd.to_numeric(col_or_position(df, 6, "收盘价", "收盘点数", "收盘点位", "收盘", "close"), errors="coerce")
    out["prev_close"] = pd.to_numeric(col_or_position(df, 7, "前收盘点", "前收盘点位", "昨收", "prev_close"), errors="coerce")
    out["change"] = pd.to_numeric(col_or_position(df, 8, "涨跌点", "涨跌点位", "涨跌额", "change"), errors="coerce")
    out["change_pct"] = pd.to_numeric(col_or_position(df, 9, "涨跌幅(%)", "涨跌幅", "涨跌幅（%）", "change_pct"), errors="coerce")
    out["volume"] = pd.to_numeric(col_or_position(df, 10, "成交量(股)", "成交量（股）", "成交量", "vol"), errors="coerce")
    amount = col_or_position(df, 10 if family == "申万指数" else 11, "成交额(千元)", "成交额（千元）", "成交金额(千元)", "成交金额（千元）")
    out["amount"] = pd.to_numeric(amount, errors="coerce") * 1000
    out["pe"] = pd.to_numeric(col_or_position(df, 11, "市盈率", "pe"), errors="coerce") if family == "申万指数" else pd.Series(index=df.index, dtype="float64")
    out["pb"] = pd.to_numeric(col_or_position(df, 12, "市净率", "pb"), errors="coerce") if family == "申万指数" else pd.Series(index=df.index, dtype="float64")
    out["float_mv"] = pd.to_numeric(col_or_position(df, 13, "流通市值(千元)", "流通市值（千元）"), errors="coerce") * 1000 if family == "申万指数" else pd.Series(index=df.index, dtype="float64")
    out["total_mv"] = pd.to_numeric(col_or_position(df, 14, "总市值(千元)", "总市值（千元）"), errors="coerce") * 1000 if family == "申万指数" else pd.Series(index=df.index, dtype="float64")
    out["family"] = "sw_industry" if family == "申万指数" else "market_index"
    out["amount_unit"] = "CNY"  # normalized from source 千元
    out["source_path"] = source_path
    out = out[out["instrument_code"].notna() & out["trade_date"].notna()]
    if family == "申万指数":
        out = out[out["instrument_code"].str.match(r"^801\d{3}\.SI$", na=False)]
    else:
        out = out[out["instrument_code"].isin(MARKET_CODES)]
    return out


def source_files(mode: str):
    for family_dir in (SOURCE_ROOT / "大盘指数", SOURCE_ROOT / "申万指数"):
        daily = family_dir / "日线"
        if not daily.exists():
            continue
        family = family_dir.name
        if mode == "update":
            paths = sorted((daily / "2026").glob("*.csv"))
            for p in paths:
                yield family, str(p.relative_to(SOURCE_ROOT)), p.read_bytes()
        else:
            for p in sorted(daily.glob("*.zip")):
                with zipfile.ZipFile(p) as z:
                    for member in z.infolist():
                        if not member.is_dir() and member.filename.lower().endswith(".csv"):
                            yield family, f"{p.relative_to(SOURCE_ROOT)}::{member.filename}", z.read(member)
            for p in sorted((daily / "2026").glob("*.csv")):
                yield family, str(p.relative_to(SOURCE_ROOT)), p.read_bytes()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ingest_file (
        source_path TEXT PRIMARY KEY, source_hash TEXT NOT NULL, row_count INTEGER NOT NULL,
        min_date TEXT, max_date TEXT, loaded_at TEXT NOT NULL, status TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS instrument (
        instrument_code TEXT PRIMARY KEY, instrument_name TEXT, family TEXT,
        first_date TEXT, last_date TEXT, row_count INTEGER, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS quality_summary (
        source_path TEXT PRIMARY KEY, invalid_ohlc INTEGER, duplicate_keys INTEGER,
        null_close INTEGER, zero_volume INTEGER, zero_amount INTEGER
    );
    """)


def file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_frame(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    frame["year"] = frame["trade_date"].str[:4]
    for (family, year), part in frame.groupby(["family", "year"], sort=False):
        target = PARQUET_ROOT / f"family={family}" / f"year={year}" / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            old = pd.read_parquet(target)
            part = pd.concat([old, part], ignore_index=True)
        part = part.drop_duplicates(["instrument_code", "trade_date"], keep="last").sort_values(["instrument_code", "trade_date"])
        part.to_parquet(target, index=False)


def run(mode: str) -> dict:
    DB_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    init_db(conn)
    seen = loaded = rows = 0
    for family, source_path, raw in source_files(mode):
        seen += 1
        digest = file_hash(raw)
        old = conn.execute("SELECT source_hash FROM ingest_file WHERE source_path=?", (source_path,)).fetchone()
        if old and old[0] == digest:
            continue
        frame = normalize(read_csv_source(raw, source_path), family, source_path)
        if frame.empty:
            continue
        load_frame(frame)
        invalid = ((frame["high"] < frame[["open", "close", "low"]].max(axis=1)) | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))).sum()
        conn.execute("INSERT OR REPLACE INTO ingest_file VALUES (?,?,?,?,?,?,?)", (source_path, digest, len(frame), frame.trade_date.min(), frame.trade_date.max(), datetime.now().isoformat(timespec="seconds"), "loaded"))
        conn.execute("INSERT OR REPLACE INTO quality_summary VALUES (?,?,?,?,?,?)", (source_path, int(invalid), int(frame.duplicated(["instrument_code", "trade_date"]).sum()), int(frame.close.isna().sum()), int((frame.volume.fillna(0) == 0).sum()), int((frame.amount.fillna(0) == 0).sum())))
        for code, part in frame.groupby("instrument_code"):
            conn.execute("""INSERT INTO instrument VALUES (?,?,?,?,?,?,?) ON CONFLICT(instrument_code) DO UPDATE SET instrument_name=excluded.instrument_name, family=excluded.family, first_date=MIN(instrument.first_date, excluded.first_date), last_date=MAX(instrument.last_date, excluded.last_date), row_count=COALESCE(instrument.row_count,0)+excluded.row_count, updated_at=excluded.updated_at""", (code, part.instrument_name.dropna().iloc[-1] if not part.instrument_name.dropna().empty else None, part.family.iloc[0], part.trade_date.min(), part.trade_date.max(), len(part), datetime.now().isoformat(timespec="seconds")))
        loaded += 1
        rows += len(frame)
    conn.commit()
    instruments = conn.execute("SELECT COUNT(*) FROM instrument").fetchone()[0]
    files = conn.execute("SELECT COUNT(*) FROM ingest_file").fetchone()[0]
    conn.close()
    quality = inspect_and_repair(True)
    return {"mode": mode, "source_files_seen": seen, "files_loaded_now": loaded, "rows_loaded_now": rows, "database_files": files, "instruments": instruments, "quality": {key: quality[key] for key in ("rows_before", "rows_after", "rows_quarantined", "close_only", "zero_volume", "large_return", "status")}, "database": str(DB_ROOT)}


def status() -> None:
    if not SQLITE_PATH.exists():
        print(json.dumps({"status": "NOT_BUILT"}, ensure_ascii=False, indent=2))
        return
    conn = sqlite3.connect(SQLITE_PATH)
    print(json.dumps({"database": str(DB_ROOT), "files": conn.execute("SELECT COUNT(*) FROM ingest_file").fetchone()[0], "instruments": conn.execute("SELECT COUNT(*) FROM instrument").fetchone()[0], "date_range": conn.execute("SELECT MIN(first_date), MAX(last_date) FROM instrument").fetchone()}, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("build", "update", "status"), default="update")
    args = parser.parse_args()
    if args.mode == "status":
        status()
    else:
        print(json.dumps(run(args.mode), ensure_ascii=False, indent=2))
