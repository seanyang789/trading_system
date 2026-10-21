"""实验数据区间、来源指纹和父实验范围约束。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


def source_fingerprint(path: str | Path) -> dict:
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {"path": str(path.resolve()), "size_bytes": stat.st_size,
            "modified_at": pd.Timestamp(stat.st_mtime, unit="s").isoformat(),
            "sha256": digest.hexdigest()}


def archive_source_snapshot(source: str | Path, experiment_dir: str | Path) -> dict:
    """Copy the exact input file beside experiment outputs and verify its digest."""
    import shutil

    source = Path(source)
    target_dir = Path(experiment_dir) / "input_snapshot"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copy2(source, target)
    expected = source_fingerprint(source)
    actual = source_fingerprint(target)
    if expected["sha256"] != actual["sha256"]:
        target.unlink(missing_ok=True)
        raise IOError("实验输入快照校验失败，已停止归档")
    return {**actual, "snapshot_path": str(target.relative_to(Path(experiment_dir)))}


def describe_scope(df: pd.DataFrame, source: str | Path, *, requested_start=None,
                   requested_end=None, indicator_warmup_start=None) -> dict:
    dates = pd.to_datetime(df["日期"], errors="raise")
    return {
        "schema_version": 1,
        "result_start_date": dates.min().date().isoformat() if len(dates) else None,
        "result_end_date": dates.max().date().isoformat() if len(dates) else None,
        "source_start_date": dates.min().date().isoformat() if len(dates) else None,
        "source_end_date": dates.max().date().isoformat() if len(dates) else None,
        "requested_start_date": _date_text(requested_start),
        "requested_end_date": _date_text(requested_end),
        "indicator_warmup_start_date": _date_text(indicator_warmup_start),
        "source_fingerprint": source_fingerprint(source),
    }


def load_parent_scope(experiment_dir: str | Path) -> dict:
    meta_path = Path(experiment_dir) / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"父实验缺少 meta.json，无法继承数据区间: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    experiment_dir = Path(experiment_dir)
    scope = meta.get("data_scope")
    if scope and not scope.get("source_fingerprint") and meta.get("source_fingerprint"):
        scope = {**scope, "source_fingerprint": meta["source_fingerprint"]}
    if not scope or not scope.get("result_start_date") or not scope.get("result_end_date"):
        result_path = experiment_dir / "result.csv"
        if not result_path.is_file():
            raise ValueError("父实验缺少时间区间和归档结果，无法安全继承")
        frame = pd.read_csv(result_path, encoding="utf-8-sig", usecols=lambda col: col == "日期")
        dates = pd.to_datetime(frame["日期"], errors="coerce").dropna()
        if dates.empty:
            raise ValueError("父实验结果中没有可识别日期，无法安全继承时间范围")
        scope = {"result_start_date": dates.min().date().isoformat(),
                 "result_end_date": dates.max().date().isoformat(),
                 "scope_status": "legacy_result_range_only",
                 "source_version_status": "unknown"}
    return {**scope, "experiment_id": meta.get("experiment_id", experiment_dir.name),
            "strategy_name": meta.get("strategy_name"), "research_name": meta.get("research_name"),
            "description": meta.get("description")}


def resolve_experiment_path(reference: str | Path, project_root: str | Path) -> Path:
    candidate = Path(reference)
    if candidate.is_absolute():
        path = candidate
    else:
        path = Path(project_root) / "shared" / "experiments" / candidate
    if (path / "meta.json").is_file():
        return path
    root = Path(project_root) / "shared" / "experiments"
    matches = []
    for meta_path in root.glob("*/exp_*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if reference in {meta_path.parent.name, meta.get("experiment_name_zh")}:
            matches.append(meta_path.parent)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"父实验名称不唯一，请使用研究目录/实验编号：{reference}")
    raise FileNotFoundError(f"找不到父实验：{reference}")


def constrain_to_parent_range(df: pd.DataFrame, scope: dict, *, date_column="日期") -> pd.DataFrame:
    result = df.copy()
    result[date_column] = pd.to_datetime(result[date_column], errors="raise")
    start = pd.Timestamp(scope["result_start_date"])
    end = pd.Timestamp(scope.get("requested_end_date") or scope.get("realized_end_date") or scope["result_end_date"])
    return result.loc[result[date_column].between(start, end)].copy()


def constrain_result_to_parent_range(result: pd.DataFrame, scope: dict) -> pd.DataFrame:
    """Keep only observations whose signal date and realized return date stay in scope."""
    frame = result.copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="raise")
    start = pd.Timestamp(scope["result_start_date"])
    end = pd.Timestamp(scope.get("requested_end_date") or scope.get("realized_end_date") or scope["result_end_date"])
    frame = frame.loc[frame["日期"].between(start, end)].copy()
    if "next_return_date" in frame:
        realized = pd.to_datetime(frame["next_return_date"], errors="coerce")
        frame = frame.loc[realized.notna() & realized.between(start, end)].copy()
    elif "next_return" in frame.columns and len(frame):
        # Legacy result schema cannot identify realization date; conservatively drop
        # the terminal observation because it has no guaranteed in-scope next bar.
        frame = frame.iloc[:-1].copy()
    return frame


def _date_text(value):
    if value is None:
        return None
    return pd.Timestamp(value).date().isoformat()
