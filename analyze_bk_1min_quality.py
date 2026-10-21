"""全量审计 bk_1min.csv 的分钟数据质量。"""
from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from pathlib import Path

PATH = Path(__file__).resolve().parent / "shared" / "data" / "bk_1min.csv"

def main() -> None:
    required = ["指数代码", "通达信代码", "指数名称", "交易时间", "开盘价", "最高价", "最低价", "收盘价", "成交量(股)", "成交金额(元)"]
    rows = total = bad_date = bad_numeric = bad_ohlc = negative = zero_price = 0
    duplicate = 0
    seen: set[tuple[str, str]] = set()
    code_rows = Counter()
    code_dates: defaultdict[str, set[str]] = defaultdict(set)
    day_counts: Counter[tuple[str, str]] = Counter()
    time_counts: Counter[str] = Counter()
    min_price = float("inf")
    max_price = 0.0
    max_return: tuple[float, str] = (0.0, "")
    previous_close: dict[str, float] = {}
    missing_fields = Counter()

    with PATH.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != required:
            print("HEADER_MISMATCH", reader.fieldnames)
        for row in reader:
            total += 1
            code = row.get("通达信代码", "")
            stamp = row.get("交易时间", "")
            date = stamp[:10]
            key = (code, stamp)
            if key in seen:
                duplicate += 1
            seen.add(key)
            code_rows[code] += 1
            code_dates[code].add(date)
            day_counts[(code, date)] += 1
            time_counts[stamp[11:]] += 1

            for field in required:
                if not row.get(field, "").strip():
                    missing_fields[field] += 1
            try:
                values = [float(row[x]) for x in ["开盘价", "最高价", "最低价", "收盘价", "成交量(股)", "成交金额(元)"]]
                if not all(math.isfinite(x) for x in values):
                    bad_numeric += 1
                    continue
            except (TypeError, ValueError):
                bad_numeric += 1
                continue
            op, high, low, close, volume, amount = values
            if high < max(op, close, low) - 1e-9 or low > min(op, close, high) + 1e-9:
                bad_ohlc += 1
            if min(op, high, low, close) <= 0:
                zero_price += 1
            if volume < 0 or amount < 0:
                negative += 1
            min_price = min(min_price, op, high, low, close)
            max_price = max(max_price, op, high, low, close)
            if code in previous_close and previous_close[code] > 0:
                ret = abs(close / previous_close[code] - 1)
                if ret > max_return[0]:
                    max_return = (ret, f"{code} {stamp}")
            previous_close[code] = close

    counts = Counter(day_counts.values())
    print(f"rows={total}")
    print(f"codes={len(code_rows)}")
    print(f"duplicate_code_time={duplicate}")
    print(f"missing_fields={dict(missing_fields)}")
    print(f"invalid_numeric={bad_numeric}")
    print(f"ohlc_logic_errors={bad_ohlc}")
    print(f"nonpositive_price={zero_price}")
    print(f"negative_volume_or_amount={negative}")
    print(f"price_range={min_price}..{max_price}")
    print(f"max_abs_return={max_return[0]*100:.4f}% at {max_return[1]}")
    print(f"day_count_distribution={dict(sorted(counts.items()))}")
    print("code_rows=" + ",".join(f"{k}:{v}" for k, v in sorted(code_rows.items())))
    print("time_range=" + ",".join(sorted(time_counts)))
    print("day_counts_by_code=" + ",".join(f"{code}:{min(v for (c, _), v in day_counts.items() if c == code)}-{max(v for (c, _), v in day_counts.items() if c == code)}" for code in sorted(code_rows)))

if __name__ == "__main__":
    main()
