"""统一的 bk.csv 行情读取与质量门禁。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BK_PATH = PROJECT_ROOT / "shared" / "data" / "bk.csv"
REQUIRED_COLUMNS = ["日期", "板块代码", "板块名称", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]
PRICE_COLUMNS = ["开盘", "最高", "最低", "收盘"]
NUMERIC_COLUMNS = PRICE_COLUMNS + ["成交量", "成交额"]


class DataQualityError(ValueError):
    """数据未通过质量门禁。"""

    def __init__(self, report: dict):
        self.report = report
        super().__init__("行情数据未通过质量检查: " + ", ".join(report["errors"][:5]))


def validate_bk(df: pd.DataFrame, *, source: str = "") -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    missing_columns = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_columns:
        errors.append(f"缺少字段: {missing_columns}")
        return {"status": "FAIL", "source": source, "rows": len(df), "errors": errors, "warnings": warnings, "counts": counts}

    work = df.copy()
    work.columns = [str(c).strip() for c in work.columns]
    counts["missing_required"] = int(work[REQUIRED_COLUMNS].isna().any(axis=1).sum())
    if counts["missing_required"]:
        errors.append(f"必填字段缺失行数: {counts['missing_required']}")

    work["日期"] = pd.to_datetime(work["日期"], errors="coerce")
    counts["invalid_date"] = int(work["日期"].isna().sum())
    if counts["invalid_date"]:
        errors.append(f"日期无法解析行数: {counts['invalid_date']}")
    for column in NUMERIC_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    counts["invalid_numeric"] = int(work[NUMERIC_COLUMNS].isna().any(axis=1).sum())
    if counts["invalid_numeric"]:
        errors.append(f"数值字段非法行数: {counts['invalid_numeric']}")

    counts["duplicate_key"] = int(work.duplicated(["日期", "板块代码"]).sum())
    if counts["duplicate_key"]:
        errors.append(f"日期+板块代码重复行数: {counts['duplicate_key']}")
    counts["nonpositive_price"] = int((work[PRICE_COLUMNS] <= 0).any(axis=1).sum())
    if counts["nonpositive_price"]:
        errors.append(f"价格非正行数: {counts['nonpositive_price']}")
    counts["negative_volume_or_amount"] = int(((work[["成交量", "成交额"]] < 0).any(axis=1)).sum())
    if counts["negative_volume_or_amount"]:
        errors.append(f"成交量或成交额为负行数: {counts['negative_volume_or_amount']}")

    ohlc_invalid = (
        (work["最高"] < work[["开盘", "收盘", "最低"]].max(axis=1))
        | (work["最低"] > work[["开盘", "收盘", "最高"]].min(axis=1))
    )
    counts["ohlc_logic"] = int(ohlc_invalid.sum())
    if counts["ohlc_logic"]:
        errors.append(f"OHLC 逻辑矛盾行数: {counts['ohlc_logic']}")

    counts["blank_code_or_name"] = int((work["板块代码"].astype(str).str.strip().eq("") | work["板块名称"].astype(str).str.strip().eq("")).sum())
    if counts["blank_code_or_name"]:
        errors.append(f"板块代码或名称为空行数: {counts['blank_code_or_name']}")
    code_name_pairs = work[["板块代码", "板块名称"]].drop_duplicates()
    counts["code_name_conflict"] = int(code_name_pairs.groupby("板块代码")["板块名称"].nunique().gt(1).sum())
    if counts["code_name_conflict"]:
        warnings.append(f"一个板块编码对应多个名称的数量: {counts['code_name_conflict']}")

    if not work["日期"].isna().all() and len(work):
        max_date = work["日期"].max()
        counts["future_date"] = int((work["日期"] > pd.Timestamp.now().normalize()).sum())
        if counts["future_date"]:
            errors.append(f"未来日期行数: {counts['future_date']}")
        report_dates = {"start": work["日期"].min(), "end": max_date}
    else:
        report_dates = {"start": None, "end": None}
    counts["code_count"] = int(work["板块代码"].nunique())
    counts["name_count"] = int(work["板块名称"].nunique())
    counts["date_count"] = int(work["日期"].nunique())
    status = "FAIL" if errors else ("WARN" if warnings else "PASS")
    return {"status": status, "source": source, "rows": int(len(work)), "date_range": {k: (v.strftime("%Y-%m-%d") if v is not None else None) for k, v in report_dates.items()}, "errors": errors, "warnings": warnings, "counts": counts}


def load_bk(path: str | Path = BK_PATH, *, report_path: str | Path | None = None) -> pd.DataFrame:
    path = Path(path)
    df = pd.read_csv(path, encoding="gbk")
    df.columns = [str(c).strip() for c in df.columns]
    report = validate_bk(df, source=str(path))
    if report_path:
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["status"] == "FAIL":
        raise DataQualityError(report)
    df["日期"] = pd.to_datetime(df["日期"], errors="raise")
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="raise")
    return df.sort_values(["板块代码", "日期"]).reset_index(drop=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="检查 shared/data/bk.csv")
    parser.add_argument("--input", default=str(BK_PATH))
    parser.add_argument("--report", default=str(PROJECT_ROOT / "shared" / "data" / "quality_report.json"))
    args = parser.parse_args()
    frame = pd.read_csv(args.input, encoding="gbk")
    report = validate_bk(frame, source=args.input)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] != "FAIL" else 1)
