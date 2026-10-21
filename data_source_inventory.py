"""扫描 shared/data/数据源，生成可重复更新的数据源结构与质量报告。

设计目标：
1. 不把全部原始数据加载到内存；
2. 识别 CSV/ZIP 的目录、文件、字段和日期范围；
3. 对 CSV 逐文件统计行数、代码、日期、缺失值、数值异常和 OHLC 逻辑；
4. 输出 JSON 报告，供 docs/DATA_SOURCE_CATALOG.md 更新和后续数据入库使用。

用法：
    python data_source_inventory.py
    python data_source_inventory.py --root shared/data/数据源 --output shared/data/data_source_inventory.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ROOT / "shared" / "data" / "数据源"
DEFAULT_OUTPUT = ROOT / "shared" / "data" / "data_source_inventory.json"


def decode_text(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gbk", "utf-16"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def read_header(path: Path) -> tuple[list[str], str]:
    with path.open("rb") as handle:
        raw = handle.read(8192)
    text, encoding = decode_text(raw)
    first = text.splitlines()[0] if text.splitlines() else ""
    try:
        row = next(csv.reader([first]))
    except csv.Error:
        row = first.split(",")
    return [str(value).strip() for value in row], encoding


def parse_date(value: str) -> str | None:
    value = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).isoformat(sep=" ")
        except ValueError:
            continue
    return None


def numeric(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def field_index(header: list[str], *names: str) -> int | None:
    for name in names:
        if name in header:
            return header.index(name)
    return None


def scan_csv(path: Path, root: Path, sample_rows: int = 5_000) -> dict:
    header, encoding = read_header(path)
    index = {
        "code": field_index(header, "指数代码", "TS指数代码", "代码", "ts_code"),
        "name": field_index(header, "指数名称", "名称", "板块名称"),
        "time": field_index(header, "交易时间", "交易日期", "日期", "trade_time"),
        "open": field_index(header, "开盘价", "开盘点数", "开盘点位", "开盘", "open"),
        "high": field_index(header, "最高价", "最高点数", "最高点位", "最高", "high"),
        "low": field_index(header, "最低价", "最低点数", "最低点位", "最低", "low"),
        "close": field_index(header, "收盘价", "收盘点数", "收盘点位", "收盘", "close"),
        "volume": field_index(header, "成交量(股)", "成交量（股）", "成交量", "vol"),
        "amount": field_index(header, "成交金额（元）", "成交金额(元)", "成交额", "amount"),
    }
    counts = Counter()
    codes: set[str] = set()
    names: set[str] = set()
    dates: list[str] = []
    sample: list[list[str]] = []
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    previous_key: tuple[str, str] | None = None
    out_of_order = 0
    duplicate_keys = 0
    missing_by_field = Counter()
    negative_by_field = Counter()
    zero_by_field = Counter()
    ohlc_invalid = 0
    malformed = 0
    with path.open("rb") as raw_handle:
        text_handle = (line.decode(encoding, errors="replace") for line in raw_handle)
        reader = csv.reader(text_handle)
        try:
            next(reader)
        except StopIteration:
            return {"path": str(path.relative_to(root)), "format": "csv", "status": "EMPTY"}
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                malformed += 1
                continue
            counts["rows"] += 1
            if len(sample) < sample_rows:
                sample.append(row)
            code = row[index["code"]].strip() if index["code"] is not None else ""
            name = row[index["name"]].strip() if index["name"] is not None else ""
            timestamp = parse_date(row[index["time"]]) if index["time"] is not None else None
            if code:
                codes.add(code)
            if name:
                names.add(name)
            if timestamp:
                dates.append(timestamp)
                first_seen.setdefault(code, timestamp)
                last_seen[code] = timestamp
                key = (code, timestamp)
                if previous_key and key < previous_key:
                    out_of_order += 1
                if key == previous_key:
                    duplicate_keys += 1
                previous_key = key
            else:
                counts["invalid_time"] += 1
            values: dict[str, float | None] = {}
            for label, position in index.items():
                if label not in {"open", "high", "low", "close", "volume", "amount"} or position is None:
                    continue
                value = numeric(row[position])
                values[label] = value
                if value is None:
                    missing_by_field[label] += 1
                elif value < 0:
                    negative_by_field[label] += 1
                elif value == 0:
                    zero_by_field[label] += 1
            if all(values.get(label) is not None for label in ("open", "high", "low", "close")):
                if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"], values["high"]):
                    ohlc_invalid += 1
    dates.sort()
    rel = str(path.relative_to(root))
    return {
        "path": rel,
        "format": "csv",
        "size_bytes": path.stat().st_size,
        "header": header,
        "row_count": counts["rows"],
        "code_count": len(codes),
        "codes_sample": sorted(codes)[:20],
        "name_count": len(names),
        "names_sample": sorted(names)[:20],
        "date_start": dates[0] if dates else None,
        "date_end": dates[-1] if dates else None,
        "invalid_time_rows": counts["invalid_time"],
        "malformed_rows": malformed,
        "duplicate_adjacent_keys": duplicate_keys,
        "out_of_order_rows": out_of_order,
        "missing_by_field": dict(missing_by_field),
        "negative_by_field": dict(negative_by_field),
        "zero_by_field": dict(zero_by_field),
        "ohlc_invalid_rows": ohlc_invalid,
        "sample_rows": sample[:5],
    }


def scan_zip(path: Path, root: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        extensions = Counter(Path(item.filename).suffix.lower() or "[none]" for item in members)
        total_uncompressed = sum(item.file_size for item in members)
    return {
        "path": str(path.relative_to(root)),
        "format": "zip",
        "size_bytes": path.stat().st_size,
        "member_count": len(members),
        "member_extensions": dict(extensions),
        "uncompressed_bytes": total_uncompressed,
    }


def scan(root: Path, output: Path, full: bool = False) -> dict:
    files = sorted(item for item in root.rglob("*") if item.is_file())
    by_extension = Counter(item.suffix.lower() or "[none]" for item in files)
    records = []
    csv_files = [path for path in files if path.suffix.lower() == ".csv"]
    zip_files = [path for path in files if path.suffix.lower() == ".zip"]
    if full:
        csv_to_scan = csv_files
    else:
        # 默认每个数据类型、频率和年份选少量代表文件，快速完成结构审查。
        groups: dict[tuple[str, str, str], list[Path]] = defaultdict(list)
        for path in csv_files:
            parts = path.relative_to(root).parts
            family = parts[0] if parts else "unknown"
            frequency = parts[1] if len(parts) > 1 else "unknown"
            year = next((part for part in parts if re.fullmatch(r"20\d{2}", part)), "unknown")
            groups[(family, frequency, year)].append(path)
        csv_to_scan = [sorted(paths)[0] for paths in sorted(groups.values(), key=lambda values: str(values[0]))]
    for path in csv_to_scan:
        records.append(scan_csv(path, root))
    for path in zip_files:
        records.append(scan_zip(path, root))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(root),
        "file_count": len(files),
        "file_count_by_extension": dict(by_extension),
        "csv_count": len(csv_files),
        "zip_count": sum(item.get("format") == "zip" for item in records),
        "csv_scanned_count": len(csv_to_scan),
        "scan_mode": "full" if full else "representative",
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="扫描项目数据源并生成结构质量报告")
    parser.add_argument("--root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--full", action="store_true", help="逐行扫描全部 CSV，耗时较长")
    args = parser.parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"数据源目录不存在: {args.root}")
    report = scan(args.root, args.output, full=args.full)
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, ensure_ascii=False, indent=2))
    print(f"明细报告: {args.output}")


if __name__ == "__main__":
    main()
