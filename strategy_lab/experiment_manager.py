"""Strategy Lab v1 experiment manager."""

import json
import shutil
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS_ROOT = PROJECT_ROOT / "shared" / "experiments"
DOCS_ROOT = PROJECT_ROOT / "docs"
INDEX_PATH = EXPERIMENTS_ROOT / "index.csv"
INDEX_COLUMNS = [
    "strategy_name", "experiment_id", "experiment_name_zh", "description", "status",
    "data_start_date", "data_end_date", "source_sha256",
    "created_at", "result_rows", "max_return", "min_return",
    "alert_count", "experiment_path",
]


def build_experiment_name_zh(strategy_name, description):
    """根据研究方向和规则描述生成稳定、可读的中文实验名称。"""
    description = str(description or "").strip()
    prefix = {"moving_average": "均线研究", "momentum": "动量研究"}.get(
        str(strategy_name), f"{strategy_name}研究"
    )
    if "EMA" in description or str(strategy_name) == "moving_average":
        if "次日阳线" in description:
            label = "EMA次日阳线突破买入"
        elif "EMA5" in description and "EMA10" in description:
            label = "EMA拐点买入"
        else:
            label = "EMA趋势排列买入"
    elif "动量" in description or str(strategy_name) == "momentum":
        label = "动量选强"
    else:
        label = "规则验证"
    return f"{prefix}-{label}"


def load_index():
    """读取全局索引；索引不存在时返回空 DataFrame。"""
    if not INDEX_PATH.exists():
        return pd.DataFrame(columns=INDEX_COLUMNS)
    try:
        index = pd.read_csv(INDEX_PATH, encoding="utf-8-sig")
        if "experiment_name_zh" not in index.columns:
            index["experiment_name_zh"] = [
                build_experiment_name_zh(row.get("strategy_name", ""), row.get("description", ""))
                for _, row in index.iterrows()
            ]
        return index.reindex(columns=INDEX_COLUMNS)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        print(f"无法读取 index.csv: {exc}")
        return pd.DataFrame(columns=INDEX_COLUMNS)


def save_index(index):
    """写入全局索引。"""
    EXPERIMENTS_ROOT.mkdir(parents=True, exist_ok=True)
    index = index.reindex(columns=INDEX_COLUMNS)
    index.to_csv(INDEX_PATH, index=False, encoding="utf-8-sig")


def _safe_name(value, label):
    value = str(value)
    if not value or value in {".", ".."} or Path(value).name != value or any(c in value for c in "\\/"):
        raise ValueError(f"无效的 {label}: {value}")
    return value


def _confirm(prompt):
    return input(prompt + " (yes/no) ").strip().lower() == "yes"


def _experiment_dirs():
    if not EXPERIMENTS_ROOT.exists():
        return
    for strategy_dir in EXPERIMENTS_ROOT.iterdir():
        if not strategy_dir.is_dir() or strategy_dir.name.startswith("."):
            continue
        for experiment_dir in strategy_dir.glob("exp_*"):
            if experiment_dir.is_dir():
                yield strategy_dir.name, experiment_dir


def _reference_tokens(strategy_name, exp_id):
    """Return exact experiment reference forms that may occur in docs."""
    return {
        f"{strategy_name}/{exp_id}",
        f"{strategy_name}\\{exp_id}",
    }


def _scrub_doc_references(strategy_name, exp_id):
    """Remove explicit references to a deleted experiment from docs only."""
    tokens = _reference_tokens(strategy_name, exp_id)
    if not DOCS_ROOT.is_dir():
        return 0

    changed = 0
    for path in DOCS_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines(keepends=True)
        kept = [line for line in lines if not any(token in line for token in tokens)]
        if kept != lines:
            path.write_text("".join(kept), encoding="utf-8")
            changed += 1
    return changed


def _remove_index_records(records):
    """Remove exact experiment records from index.csv and preserve its schema."""
    index = load_index()
    if index.empty:
        return
    mask = pd.Series(True, index=index.index)
    for strategy_name, exp_id in records:
        mask &= ~(
            (index["strategy_name"].astype(str) == strategy_name)
            & (index["experiment_id"].astype(str) == exp_id)
        )
    save_index(index[mask])


def _verify_deleted(records):
    """Verify that experiment directories, index rows, and doc references are gone."""
    remaining = []
    index = load_index()
    for strategy_name, exp_id in records:
        target = EXPERIMENTS_ROOT / strategy_name / exp_id
        if target.exists():
            remaining.append(f"目录:{target}")
        if not index.empty:
            match = index[
                (index["strategy_name"].astype(str) == strategy_name)
                & (index["experiment_id"].astype(str) == exp_id)
            ]
            if not match.empty:
                remaining.append(f"索引:{strategy_name}/{exp_id}")
    if remaining:
        raise RuntimeError("永久删除校验失败: " + ", ".join(remaining))


def list_experiments():
    print("中文实验名称\t内部目录\t结果区间\t数据版本\t状态\t规则描述")
    found = False
    for strategy_name, experiment_dir in sorted(_experiment_dirs() or []):
        meta_path = experiment_dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        name_zh = meta.get("experiment_name_zh") or build_experiment_name_zh(strategy_name, meta.get("description", ""))
        scope = meta.get("data_scope", {})
        if (not scope.get("result_start_date") or not scope.get("result_end_date")) and (experiment_dir / "result.csv").is_file():
            try:
                legacy = pd.read_csv(experiment_dir / "result.csv", encoding="utf-8-sig", usecols=lambda col: col == "日期")
                dates = pd.to_datetime(legacy["日期"], errors="coerce").dropna()
                if not dates.empty:
                    scope = {"result_start_date": dates.min().date().isoformat(),
                             "result_end_date": dates.max().date().isoformat(),
                             "scope_status": "legacy_result_range_only"}
            except (OSError, ValueError, pd.errors.ParserError):
                pass
        period = f"{scope.get('result_start_date', '区间未知')}~{scope.get('result_end_date', '区间未知')}"
        source = scope.get("source_fingerprint", {})
        version = source.get("sha256", "源版本未知")[:12] if isinstance(source, dict) else "源版本未知"
        print(f"{name_zh}\t{strategy_name}/{experiment_dir.name}\t{period}\t{version}\t{meta.get('status', 'unknown')}\t{meta.get('description', '')}")
        if scope.get("scope_status") == "legacy_result_range_only":
            print("  注：日期范围来自旧归档结果，原始输入数据版本未知。")
        found = True
    if not found:
        print("暂无实验。")


def _resolve_experiment_name(name_zh):
    """按中文名称解析唯一实验，返回内部 strategy_name/exp_id。"""
    matches = []
    for strategy_name, experiment_dir in sorted(_experiment_dirs() or []):
        try:
            meta = json.loads((experiment_dir / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        display_name = meta.get("experiment_name_zh") or build_experiment_name_zh(strategy_name, meta.get("description", ""))
        if display_name == name_zh:
            matches.append((strategy_name, experiment_dir.name))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"中文实验名称不唯一，请使用内部目录标识: {name_zh}")
    raise ValueError(f"找不到中文实验名称: {name_zh}")


def delete_experiment(strategy_name, exp_id=None):
    if exp_id is None:
        try:
            strategy_name, exp_id = _resolve_experiment_name(str(strategy_name))
        except ValueError as exc:
            print(exc)
            return
    try:
        strategy_name = _safe_name(strategy_name, "strategy_name")
        exp_id = _safe_name(exp_id, "exp_id")
    except ValueError as exc:
        print(exc)
        return
    target = EXPERIMENTS_ROOT / strategy_name / exp_id
    if not target.is_dir():
        print(f"找不到实验路径: {target}")
        return
    if not _confirm(f"确认删除 {strategy_name}/{exp_id}?"):
        print("已取消。")
        return
    shutil.rmtree(target)
    _remove_index_records([(strategy_name, exp_id)])
    docs_changed = _scrub_doc_references(strategy_name, exp_id)
    _verify_deleted([(strategy_name, exp_id)])
    print(f"已永久删除 {strategy_name}/{exp_id}（文档引用清理 {docs_changed} 个文件）。")


def clear_strategy(strategy_name):
    try:
        strategy_name = _safe_name(strategy_name, "strategy_name")
    except ValueError as exc:
        print(exc)
        return
    strategy_dir = EXPERIMENTS_ROOT / strategy_name
    if not strategy_dir.is_dir():
        print(f"找不到策略路径: {strategy_dir}")
        return
    experiments = [p for p in strategy_dir.glob("exp_*") if p.is_dir()]
    if not _confirm(f"确认清空策略 {strategy_name} 的 {len(experiments)} 个实验?"):
        print("已取消。")
        return
    records = [(strategy_name, path.name) for path in experiments]
    for path in experiments:
        shutil.rmtree(path)
    _remove_index_records(records)
    docs_changed = sum(_scrub_doc_references(*record) for record in records)
    _verify_deleted(records)
    print(f"已永久清空策略 {strategy_name}，策略文件夹已保留；文档引用清理 {docs_changed} 次。")


def reset_all_experiments():
    if not EXPERIMENTS_ROOT.exists():
        print("实验目录不存在，系统已是空状态。")
        return
    if not _confirm("确认重置整个实验系统?"):
        print("已取消。")
        return
    records = [(strategy_name, path.name) for strategy_name, path in _experiment_dirs()]
    for path in EXPERIMENTS_ROOT.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
    save_index(pd.DataFrame(columns=INDEX_COLUMNS))
    docs_changed = sum(_scrub_doc_references(*record) for record in records)
    _verify_deleted(records)
    print(f"实验系统已永久重置，index.csv 已清空；文档引用清理 {docs_changed} 次。")


def main():
    args = sys.argv[1:]
    if not args or args[0] == "list":
        list_experiments()
    elif args[0] == "delete" and len(args) == 2:
        delete_experiment(args[1])
    elif args[0] == "delete" and len(args) == 3:
        delete_experiment(args[1], args[2])
    elif args[0] == "clear" and len(args) == 2:
        clear_strategy(args[1])
    elif args[0] == "reset" and len(args) == 1:
        reset_all_experiments()
    else:
        print("用法: python experiment_manager.py [list|delete <中文实验名称>|delete <strategy> <exp_id>|clear <strategy>|reset]")


if __name__ == "__main__":
    main()
