# 日线分析数据库设计与维护说明

## 1. 当前状态

数据源结构已经由 `docs/DATA_SOURCE_CATALOG.md` 和 `shared/data/data_source_inventory.json` 存档。本文档记录从原始数据源到可研究数据库的固定路径。

数据库位置：`shared/data/analysis_db/`

```text
analysis_db/
├─ analysis.db                 # SQLite：来源、入库状态、质量摘要、标的覆盖范围
└─ bars/
   ├─ family=market_index/year=YYYY/part.parquet
   └─ family=sw_industry/year=YYYY/part.parquet
```

Parquet 保存行情事实数据，SQLite 保存“数据在哪里、更新到哪、是否通过质量检查”。这样后续查询不需要重新分析整个原始目录。

## 2. 已纳入的目标数据

- `000001.SH`：上证综合指数
- `000300.SH`：沪深 300
- `000688.SH`：科创 50
- `399001.SZ`：深证成指
- `399006.SZ`：创业板指数
- 申万源层：代码匹配 `801xxx.SI`，当前入库 289 个代码。这个集合包含申万不同层级及部分特殊指数，不能直接等同于“31 个一级行业”。研究层必须根据申万分类版本建立明确的一级行业映射后，再发布 30/31 个一级行业序列；当前数据库先完整保留源层，避免错误丢弃或误分类。

当前统一为日线；成交额和市值字段从源文件的“千元”转换为“元”，字段名保持统一，来源路径保存在每条记录中。

分钟指数数据库单独存放于 `shared/data/analysis_db/minute/`，目录格式为：

```text
minute/
├─ instrument_code=1999999/year=2026/part.parquet
├─ instrument_code=0399001/year=2026/part.parquet
└─ ...
```

当前已纳入 6 个通达信指数代码：`1999999`、`0399001`、`2899050`、`0399006`、`1000688`、`1000300`。分钟数据来自 `D:\new_mpv1.28\vipdoc\*\minline\*.lc1`，质量报告位于 `shared/data/analysis_db/minute/quality_report.json`，异常记录位于 `minute/quarantine/`。

构建命令：

```powershell
python build_index_minute_database.py
```

指数分钟线可能出现价格有效但成交量、成交额为 0 的收盘竞价记录。这类记录保留并标记为警告；进行成交量或成交额因子研究时必须自行过滤。

## 3. 标准事实字段

`instrument_code, instrument_name, trade_date, open, high, low, close, prev_close, change, change_pct, volume, amount, pe, pb, float_mv, total_mv, family, amount_unit, source_path`

主键语义为 `instrument_code + trade_date`。重复导入同一来源不会重复增加记录；同一键出现新版本时，以新来源记录覆盖旧记录，并保留入库来源日志。

## 4. 更新方式

首次完整构建（读取历史 ZIP 和 2026 CSV）：

```powershell
python build_analysis_database.py --mode build
```

日常更新（只读取 `大盘指数/日线/2026/*.csv` 和 `申万指数/日线/2026/*.csv`，通过 SHA-256 判断新文件或文件变更）：

```powershell
python build_analysis_database.py --mode update
```

每次 `build` 或 `update` 在发布数据前都会自动执行 `data_quality_gate.py`。质量门禁会检查身份字段、日期、OHLC 逻辑、非正价格、重复主键、零成交量/额和异常涨跌；可以安全修复的内容会修复，不能可靠修复的原始行会进入 `quality_quarantine.parquet`，不会继续作为正常行情发布。只有收盘价而缺少开高低的历史行会保留并标记 `quality_status=close_only`、`is_valid_ohlc=false`，需要完整 K 线的研究必须过滤掉。

质量报告位置：`shared/data/analysis_db/quality_report.json`；质量门禁源码：`data_quality_gate.py`。

查看数据库状态：

```powershell
python build_analysis_database.py --mode status
```

更新程序不会修改原始数据源。新增文件会被记录到 `ingest_file`，每个来源文件的行数、日期范围、OHLC 异常、重复键、收盘价缺失、零成交量和零成交额会记录到 SQLite。

## 5. 研究使用约定

以后提出“提取某类日线数据”时，优先从 `analysis_db` 查询；只有目标标的尚未入库、数据库状态过期或质量报告不通过时，才回到 `数据源` 处理。数据源台账负责路由，分析数据库负责常规研究读取。

目前已验证增量流程：首次完整构建后再次运行 `--mode update`，已处理的 2026 文件通过哈希校验被跳过，加载数量为 0。新增或内容发生变化的 2026 文件才会进入标准化和入库流程。

需要特别注意：零成交量/零成交额对指数并不必然是错误，但不能未经策略说明直接用于流动性或成交量因子。申万指数版本、代码变更和一级行业数量也要以 `instrument` 及后续分类元数据为准，不按名称硬编码。
