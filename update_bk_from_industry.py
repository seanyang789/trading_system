"""从通达信 vipdoc 增量更新 shared/data/bk.csv。

更新策略：先检查一个哨兵文件是否出现新交易日；若没有，立即退出。
若有，则假定同批 vipdoc 文件均已更新，并批量合并全部目标文件。
"""
from __future__ import annotations
import csv
import shutil
import struct
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VIPDOC = Path(r"D:\new_mpv1.28\vipdoc")
TARGET = ROOT / "shared" / "data" / "bk.csv"
HEADER = ["日期", "板块代码", "板块名称", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]
SENTINEL = "1880301"
MARKET_DIR = {"0": "sz", "1": "sh", "2": "bj"}
CUSTOM_CODES = """1880301 1880459 1880414 1880431 1880351 1880324 1880430 1880305
1880492 1880344 1880490 1880318 1880465 1880330 1880455 1880473 1880497
1880447 1880471 1880335 1880437 1880446 1880387 1880310 1880476 1880360
1880440 1880350 1880432 1880464 1880372 1880456 1880390 1880472 1880454
1880474 1880453 1880452 1880406 1880399 1880367 1880491 1880489 1880424
1880422 1880423 1880421 1880493 1880380 1880400 1880448 1880398 1880482
1880355 1880494 1880418 1999999 0399001 2899050 0399006 1000688 1000300""".split()

def locate(export_code: str) -> tuple[str, Path]:
    market = MARKET_DIR[export_code[0]]
    code = export_code[1:]
    return code, VIPDOC / market / "lday" / f"{market}{code}.day"

def read_day(path: Path) -> list[list[str]]:
    data = path.read_bytes()
    if len(data) % 32:
        raise ValueError(f"文件长度不是 32 字节记录的整数倍: {path}")
    price_scale = 100 if path.stem[2:4] == "88" or path.stem[2:] == "999999" else 1000
    rows = []
    for offset in range(0, len(data), 32):
        date, op, high, low, close, amount, volume, _ = struct.unpack_from("<5if2i", data, offset)
        date_text = f"{date // 10000:04d}/{date // 100 % 100:02d}/{date % 100:02d}"
        rows.append([date_text, f"{op / price_scale:g}", f"{high / price_scale:g}", f"{low / price_scale:g}", f"{close / price_scale:g}", str(volume), f"{amount:g}"])
    return rows

def read_existing() -> tuple[dict[tuple[str, str], list[str]], dict[str, str]]:
    merged = {}
    names = {}
    with TARGET.open("r", encoding="gbk", newline="") as handle:
        for row in csv.reader(handle):
            if row == HEADER or len(row) < 9:
                continue
            row = row[:9]
            merged[(row[0], row[1])] = row
            names.setdefault(row[1], row[2])
    return merged, names

def main() -> None:
    merged, names = read_existing()
    sentinel_code, sentinel_path = locate(SENTINEL)
    if not sentinel_path.exists():
        raise SystemExit(f"哨兵文件不存在: {sentinel_path}")
    sentinel_rows = read_day(sentinel_path)
    existing_dates = [date for date, code in merged if code == sentinel_code]
    existing_latest = max(existing_dates) if existing_dates else None
    source_latest = sentinel_rows[-1][0] if sentinel_rows else None
    if source_latest is None or (existing_latest is not None and source_latest <= existing_latest):
        print(f"未发现新日线，停止更新。哨兵 {sentinel_code}: 源={source_latest}, 当前={existing_latest}")
        return

    backup = TARGET.with_name(f"{TARGET.stem}.before_vipdoc_update_{datetime.now():%Y%m%d_%H%M%S}{TARGET.suffix}.bak")
    shutil.copy2(TARGET, backup)
    for export_code in CUSTOM_CODES:
        code, path = locate(export_code)
        if not path.exists():
            raise FileNotFoundError(f"缺少源文件: {path}")
        name = names.get(code, code)
        for date, op, high, low, close, volume, amount in read_day(path):
            merged[(date, code)] = [date, code, name, op, high, low, close, volume, amount]

    ordered = sorted(merged.values(), key=lambda row: (row[1], row[0]))
    temp = TARGET.with_suffix(".csv.tmp")
    with temp.open("w", encoding="gbk", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(ordered)
    temp.replace(TARGET)
    print(f"发现新日线，已更新源文件数: {len(CUSTOM_CODES)}")
    print(f"合并后行数: {len(ordered)}")
    print(f"备份文件: {backup}")
    print(f"目标文件: {TARGET}")

if __name__ == "__main__":
    main()
