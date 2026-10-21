"""从通达信 vipdoc 日线文件构建独立的自定义板块回顾数据集。

输入是用户导出的 7 位标识：市场前缀 + 6 位通达信代码。
输出保持现有 bk.csv 的 9 列 CSV 结构，作为当前回顾数据文件；
不参与 shared/data/数据源 的更新流程。
"""

from __future__ import annotations

import csv
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VIPDOC = Path(r"D:\new_mpv1.28\vipdoc")
TARGET = ROOT / "shared" / "data" / "bk.csv"
HEADER = ["日期", "板块代码", "板块名称", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]

CUSTOM_CODES = """1880301 1880459 1880414 1880431 1880351 1880324 1880430 1880305
1880492 1880344 1880490 1880318 1880465 1880330 1880455 1880473 1880497
1880447 1880471 1880335 1880437 1880446 1880387 1880310 1880476 1880360
1880440 1880350 1880432 1880464 1880372 1880456 1880390 1880472 1880454
1880474 1880453 1880452 1880406 1880399 1880367 1880491 1880489 1880424
1880422 1880423 1880421 1880493 1880380 1880400 1880448 1880398 1880482
1880355 1880494 1880418 1999999 0399001 2899050 0399006 1000688 1000300""".split()

MARKET_DIR = {"0": "sz", "1": "sh", "2": "bj"}


def locate(export_code: str) -> tuple[str, Path]:
    market = MARKET_DIR[export_code[0]]
    code = export_code[1:]
    path = VIPDOC / market / "lday" / f"{market}{code}.day"
    return code, path


def read_day(path: Path):
    data = path.read_bytes()
    if len(data) % 32:
        raise ValueError(f"文件长度不是 32 字节记录的整数倍: {path}")
    # 通达信 .day 通常按 1000 缩放价格；880xxx 行业指数文件按 100 缩放。
    # 该差异必须按实际文件代码处理，否则行业指数会整体缩小 10 倍。
    price_scale = 100 if path.stem[2:4] == "88" or path.stem[2:] == "999999" else 1000
    for offset in range(0, len(data), 32):
        date, op, high, low, close, amount, volume, _reserved = struct.unpack_from("<5if2i", data, offset)
        yield (
            f"{date // 10000:04d}/{date // 100 % 100:02d}/{date % 100:02d}",
            op / price_scale,
            high / price_scale,
            low / price_scale,
            close / price_scale,
            volume,
            amount,
        )


def load_names() -> dict[str, str]:
    """尽量复用旧 bk.csv 中已有的名称；没有名称时使用代码，保证可追溯。"""
    old = ROOT / "shared" / "data" / "bk.csv"
    names: dict[str, str] = {}
    if old.exists():
        with old.open("r", encoding="gbk", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) >= 3 and row[1] != "板块代码":
                    names.setdefault(row[1], row[2])
    return names


def main() -> None:
    names = load_names()
    merged: list[list[object]] = []
    missing: list[str] = []
    for export_code in CUSTOM_CODES:
        code, path = locate(export_code)
        if not path.exists():
            missing.append(f"{export_code} -> {path}")
            continue
        name = names.get(code, code)
        for date, op, high, low, close, volume, amount in read_day(path):
            merged.append([date, code, name, op, high, low, close, volume, amount])

    if missing:
        raise FileNotFoundError("缺少日线文件:\n" + "\n".join(missing))
    merged.sort(key=lambda row: (row[1], row[0]))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("w", encoding="gbk", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(merged)
    print(f"标识数: {len(CUSTOM_CODES)}")
    print(f"合并后行数: {len(merged)}")
    print(f"目标文件: {TARGET}")
    print("映射规则: 7位标识首位为市场前缀，后6位为 vipdoc 文件代码")


if __name__ == "__main__":
    main()
