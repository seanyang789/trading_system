"""整合数据源中的 6 个大盘指数 1 分钟数据。"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "shared" / "data" / "数据源" / "大盘指数" / "1min"
TARGET = ROOT / "shared" / "data" / "bk_1min.csv"

CODE_MAP = {
    "000001.SH": "1999999",
    "399001.SZ": "0399001",
    "899050.BJ": "2899050",
    "399006.SZ": "0399006",
    "000688.SH": "1000688",
    "000300.SH": "1000300",
}

HEADER = [
    "指数代码", "通达信代码", "指数名称", "交易时间", "开盘价", "最高价", "最低价",
    "收盘价", "成交量(股)", "成交金额(元)",
]


def iter_csv_files():
    for path in sorted(SOURCE_ROOT.rglob("*.zip")):
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().endswith(".csv"):
                    yield path, (archive, name)
    for path in sorted(SOURCE_ROOT.rglob("*.csv")):
        yield path, None


def read_rows(path: Path, member):
    if member is None:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    else:
        archive, name = member
        handle = io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", newline="")
    try:
        header = next(csv.reader(handle))
        positions = {name: index for index, name in enumerate(header)}
        for line in handle:
            if not any(line.startswith(code + ",") for code in CODE_MAP):
                continue
            fields = next(csv.reader([line]))
            yield {name: fields[index] if index < len(fields) else "" for name, index in positions.items()}
    finally:
        handle.close()


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    files = 0
    with TARGET.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(HEADER)
        for source_path, member in iter_csv_files():
            files += 1
            for row in read_rows(source_path, member):
                code = (row.get("指数代码") or "").strip()
                if code not in CODE_MAP:
                    continue
                writer.writerow([
                    code,
                    CODE_MAP[code],
                    (row.get("指数名称") or "").strip(),
                    (row.get("交易时间") or "").strip(),
                    (row.get("开盘价") or "").strip(),
                    (row.get("最高价") or "").strip(),
                    (row.get("最低价") or "").strip(),
                    (row.get("收盘价") or "").strip(),
                    (row.get("成交量(股)") or "").strip(),
                    (row.get("成交金额（元）") or row.get("成交金额(元)") or "").strip(),
                ])
                counts[code] += 1
    print(f"源文件数: {files}")
    print(f"输出文件: {TARGET}")
    print(f"总记录数: {sum(counts.values())}")
    for code, export_code in CODE_MAP.items():
        print(f"{export_code}\t{code}\t{counts[code]}")
    if any(counts[code] == 0 for code in CODE_MAP):
        raise SystemExit("存在未匹配到数据的指数")


if __name__ == "__main__":
    main()
