"""分析通达信 infoharbor_block.dat 板块文件。

该文件是文本格式，不是常见的固定长度二进制数据库：每个板块的元数据行以
``#GN_``、``#ZS_`` 或 ``#FG_`` 开头，后续行是逗号分隔的 ``市场#代码``。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


DEFAULT_PATH = r"D:\new_mpv1.28\T0002\hq_cache\infoharbor_block.dat"


def decode_file(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("gbk", "utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    return raw.decode("gbk", errors="replace"), "gbk(errors=replace)"


def parse(path: Path) -> tuple[dict, list[dict]]:
    text, encoding = decode_file(path)
    lines = text.splitlines()
    blocks: list[dict] = []
    current = None
    malformed: list[dict] = []

    for line_no, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        if line.startswith(("#GN_", "#ZS_", "#FG_")):
            fields = next(csv.reader([line[4:]]), [])
            current = {
                "type": line[:3],
                "name": fields[0] if fields else "",
                "raw_fields": fields,
                "field_names_inferred": [
                    "name", "stock_count", "category_or_id", "created_or_start_date",
                    "updated_or_end_date", "reserved_1", "reserved_2",
                ],
                "codes": [],
                "line": line_no,
            }
            blocks.append(current)
            continue
        if current is None:
            malformed.append({"line": line_no, "text": line[:200]})
            continue
        for item in line.split(","):
            item = item.strip()
            if not item:
                continue
            if "#" not in item:
                malformed.append({"line": line_no, "text": item})
                continue
            market, code = item.split("#", 1)
            current["codes"].append({"market": market, "code": code})

    for block in blocks:
        block["parsed_stock_count"] = len(block["codes"])
        declared = block["raw_fields"][1] if len(block["raw_fields"]) > 1 else ""
        try:
            block["declared_stock_count"] = int(declared)
        except ValueError:
            block["declared_stock_count"] = None
        block["count_matches"] = (
            block["declared_stock_count"] is None
            or block["declared_stock_count"] == block["parsed_stock_count"]
        )

    report = {
        "path": str(path),
        "encoding": encoding,
        "line_count": len(lines),
        "block_count": len(blocks),
        "block_types": dict(Counter(b["type"] for b in blocks)),
        "malformed_item_count": len(malformed),
        "malformed_examples": malformed[:10],
        "count_mismatch_count": sum(not b["count_matches"] for b in blocks),
        "field_interpretation": {
            "raw_fields": "#GN_或#ZS_后的逗号字段；第1个是板块名，第2个通常是股票数，后续日期/编号字段需结合版本验证",
            "membership": "市场#股票代码，例如 0#000001；市场编号是通达信市场标识，不是股票代码的一部分",
        },
    }
    return report, blocks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=DEFAULT_PATH)
    parser.add_argument("--json", dest="json_path", help="写出完整解析结果 JSON")
    parser.add_argument("--show", type=int, default=10, help="显示前 N 个板块")
    args = parser.parse_args()
    path = Path(args.path)
    if not path.is_file():
        raise SystemExit(f"文件不存在: {path}")
    report, blocks = parse(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n板块样例:")
    for block in blocks[: max(0, args.show)]:
        print(f"  {block['type']} {block['name']}: declared={block['declared_stock_count']}, parsed={block['parsed_stock_count']}, fields={block['raw_fields']}")
        print(f"    codes={block['codes'][:5]}")
    if args.json_path:
        output = {"report": report, "blocks": blocks}
        Path(args.json_path).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整 JSON 已写入: {args.json_path}")


if __name__ == "__main__":
    main()
