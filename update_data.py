"""统一更新日线和分钟线，两个数据频率分别设置新交易日门禁。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "shared" / "data" / "数据源"
MINUTE_ROOT = ROOT / "shared" / "data" / "analysis_db" / "minute"


def latest_daily_source() -> str | None:
    dates: list[str] = []
    for family in ("大盘指数", "申万指数"):
        for path in (SOURCE_ROOT / family / "日线" / "2026").glob("*.csv"):
            try:
                frame = pd.read_csv(path, encoding_errors="ignore", dtype=str)
                for name in ("交易日期", "日期", "trade_date"):
                    if name in frame.columns:
                        dates.extend(pd.to_datetime(frame[name], errors="coerce").dt.strftime("%Y-%m-%d").dropna().tolist())
                        break
            except Exception:
                continue
    return max(dates) if dates else None


def latest_daily_db() -> str | None:
    db = ROOT / "shared" / "data" / "analysis_db" / "bars"
    dates: list[str] = []
    for path in db.glob("family=*/year=*/part.parquet"):
        frame = pd.read_parquet(path, columns=["trade_date"])
        dates.extend(frame["trade_date"].dropna().astype(str).tolist())
    return max(dates) if dates else None


def latest_minute_source() -> str | None:
    # 必须直接扫描分钟源文件，不能读取质量报告；报告可能是上次更新的旧快照。
    from build_index_minute_database import TARGETS, decode, source_path
    dates: list[str] = []
    for export_code in TARGETS:
        path = source_path(export_code)
        if path.exists():
            frame = decode(path, export_code)
            dates.extend(frame["trade_date"].dropna().astype(str).tolist())
    return max(dates) if dates else None


def latest_minute_db() -> str | None:
    dates: list[str] = []
    for path in MINUTE_ROOT.glob("instrument_code=*/year=*/part.parquet"):
        table = pq.read_table(path, columns=["trade_date"])
        dates.extend(str(x) for x in table["trade_date"].to_pylist() if x)
    return max(dates) if dates else None


def run(script: str) -> None:
    subprocess.run([sys.executable, str(ROOT / script)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="分别检查并更新日线、分钟线")
    parser.add_argument("--force", action="store_true", help="忽略日期门禁，手动重建两类数据")
    args = parser.parse_args()

    daily_src, daily_db = latest_daily_source(), latest_daily_db()
    minute_src, minute_db = latest_minute_source(), latest_minute_db()
    daily_new = args.force or (daily_src is not None and (daily_db is None or daily_src > daily_db))
    minute_new = args.force or (minute_src is not None and (minute_db is None or minute_src > minute_db))

    print(f"日线：源={daily_src or '未知'}，库={daily_db or '空'}，{'更新' if daily_new else '停止'}")
    print(f"分钟线：源={minute_src or '未知'}，库={minute_db or '空'}，{'更新' if minute_new else '停止'}")
    if daily_new:
        run("build_analysis_database.py")
    if minute_new:
        run("build_index_minute_database.py")
    if not daily_new and not minute_new:
        print("日线和分钟线均没有新交易日，未执行更新。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
