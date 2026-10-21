"""Strategy Lab 的轻量命令帮助入口。"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HELP_FILE = ROOT / "docs" / "HELP.md"
COMMANDS = {
    "帮助": "查看所有可用指令",
    "检查数据": "检查当前回顾数据是否存在缺失、重复或明显异常",
    "更新数据": "分别检查并更新日线和分钟线；只有对应频率出现新交易日才更新，二者互不推断",
    "更新日线": "从通达信 vipdoc 增量更新当前回顾数据",
    "重建日线": "从通达信 vipdoc 重新构建当前自定义板块回顾数据",
    "整合分钟线": "将指定代码的分钟数据整合为回顾数据",
    "检查分钟线": "统计分钟数据中的缺失、重复、错位和疑似脏数据",
    "构建分析数据库": "按代码和年份拆分数据，构建高效回顾数据库",
    "检查分析数据库": "检查分析数据库的完整性和质量门禁结果",
    "构建指数分钟数据库": "从通达信 vipdoc 读取 6 个指数分钟线，按代码和年份保存为 Parquet，并生成质量报告",
    "查看买点": "按照指定研究规则重新计算指定标的的全部买点",
    "查看卖点": "按照指定研究规则重新计算指定标的的全部卖点和对应交易结果",
    "生成通达信买点公式": "将已经梳理出的买点日期生成完整可执行的通达信主图公式",
    "运行回测": "按照当前研究规则运行历史回测并保存实验结果",
    "查看实验": "查看已有实验、实验指标和实验状态",
    "删除实验": "在确认后删除指定实验及其索引记录",
    "系统状态": "查看当前系统阶段、数据入口和已知限制",
}

def print_help(topic: str | None = None) -> None:
    if topic and topic not in COMMANDS:
        print(f"未找到指令：{topic}\n可用指令：" + ", ".join(COMMANDS))
        return
    if topic:
        print(f"{topic}：{COMMANDS[topic]}")
        return
    print("Strategy Lab 帮助\n")
    for name, description in COMMANDS.items():
        print(f"{name:<18} {description}")
    print("\n搜索：python help.py --search <关键词>")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("topic", nargs="?")
    parser.add_argument("--search", "-s")
    args = parser.parse_args(argv)
    if args.search:
        text = HELP_FILE.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if args.search.lower() in line.lower()]
        print("\n".join(lines) if lines else f"没有找到：{args.search}")
        return 0
    print_help(args.topic)
    return 0

if __name__ == "__main__":
    sys.exit(main())
