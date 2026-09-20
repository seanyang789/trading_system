"""用 shared/data/行业板块 下的日线指数文本更新 shared/data/bk.csv。

保持旧版 bk.csv 结构：日期,代码,名称,开盘,最高,最低,收盘,成交量,成交额。
原始文件使用 GBK；更新前自动生成带时间戳的 .bak 备份。
"""

from __future__ import annotations

import csv
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "shared" / "data"
SOURCE_DIR = DATA_DIR / "行业板块"
TARGET = DATA_DIR / "bk.csv"
HEADER = ["日期", "板块代码", "板块名称", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]


def read_source(path: Path) -> tuple[str, list[list[str]]]:
    lines = path.read_text(encoding="gbk").splitlines()
    if len(lines) < 3:
        raise ValueError(f"数据行不足: {path}")
    title = lines[0].strip().split()
    if len(title) < 2:
        raise ValueError(f"无法识别名称: {path}")
    code, name = title[0], title[1]
    rows = []
    for line_no, line in enumerate(lines[2:], 3):
        fields = line.strip().split()
        if not fields:
            continue
        if fields[0].startswith("#"):
            continue
        if len(fields) != 7:
            raise ValueError(f"字段数不是 7: {path}:{line_no}: {line}")
        date, op, high, low, close, volume, amount = fields
        rows.append([date, code, name, op, high, low, close, volume, amount])
    return code, rows


def main() -> None:
    sources = sorted(SOURCE_DIR.glob("*.txt"))
    if not sources:
        raise SystemExit(f"未找到源文件: {SOURCE_DIR}")

    merged: dict[tuple[str, str], list[str]] = {}
    source_stats = []
    for path in sources:
        code, rows = read_source(path)
        for row in rows:
            merged[(row[0], row[1])] = row
        source_stats.append((path.name, code, len(rows)))

    backup = TARGET.with_name(
        f"{TARGET.stem}.before_industry_update_{datetime.now():%Y%m%d_%H%M%S}{TARGET.suffix}.bak"
    )
    shutil.copy2(TARGET, backup)

    ordered = sorted(merged.values(), key=lambda row: (row[1], row[0]))
    temp = TARGET.with_suffix(".csv.tmp")
    with temp.open("w", encoding="gbk", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(ordered)
    temp.replace(TARGET)

    codes = {row[1] for row in ordered}
    print(f"源文件数: {len(sources)}")
    print(f"指数代码数: {len(codes)}")
    print(f"合并后行数: {len(ordered)}")
    print(f"上证指数 999999: {'已包含' if '999999' in codes else '未找到'}")
    print(f"备份文件: {backup}")
    print(f"目标文件: {TARGET}")
    for filename, code, count in source_stats:
        print(f"{code}\t{count}\t{filename}")


if __name__ == "__main__":
    main()
