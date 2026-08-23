# 已知问题记录（执行中发现，待后期修复）

> 记录来源：2026-08-23 修复"数据缺失误判退出"任务执行过程中发现的耗时/超时问题。
> 状态：5 类问题已全部处理（详见各条状态）。

## 1. classify 全量耗时过长（约 25 分钟）—— 已修复

- 现象：`cleansing/holder_classifier.py` 的 `update_top_holders_type()` 对 top_holders 约 19.2 万条记录**逐条 UPDATE**，实测全量约 25 分钟（约每 500 条 / 4 秒）。
- 影响：首次重算脚本被 `timeout 900`（15 分钟）截断在 78.5k/201448，导致后续分析步骤未执行，需分两次重跑。
- 修复（2026-08-23）：
  - `database/db_manager.py` 新增 `execute_many(sql, params_list, batch_size=1000)`：单连接 `executemany` 分批执行，DB 往返从 N 次降到 N/1000 次。
  - `update_top_holders_type(report_date=None)` 重构为 pandas 内存批量分类 + `execute_many` 分批 UPDATE，并支持按报告期分片（`report_date` 参数，断点续跑）。
  - **实测**：185324 条记录分类 + 更新耗时 **3.2 秒**（原约 25 分钟，提升约 470 倍）。
- 说明：查询条件含 `holder_type='其他'`（兜底分类），该部分记录每次运行都会被重新处理，属既有语义（规则库更新后可重分类），当前性能可接受。

## 2. 采集阶段"无数据股票"耗时 —— 已修复

- 现象：重采某报告期时，约 500+ 只当期无数据的股票需**逐只请求**东财接口（十大股东 + 十大流通股东两个口径各一次），受 `REQUEST_DELAY` + 超时重试影响，单轮耗时数分钟。
- 影响：单报告期重采整体耗时偏长。
- 修复（2026-08-23）：
  - `config/settings.py` 新增 `NO_DATA_RECHECK_DAYS = 7`（可调）。
  - `ingestion/top_holders.py` 新增 `_load_fetch_status_map()`（预加载 `{key: (status, fetched_at)}`）与 `_within_recheck_interval()`；`ingest_top_holders` / `ingest_all_top_holders` 新增 `recheck_interval_days` 参数：`no_data`/`error` 状态且距上次采集不足间隔天数的组合**跳过请求**（计数 `recheck_skipped`，不重写状态）；`force_refresh=True` 时忽略间隔。
  - 时区说明：`fetched_at` 为 SQLite `CURRENT_TIMESTAMP`（UTC），按 UTC 比较，避免 8 小时偏差误判。
  - **实测**：历史期回填时 2026-06-30 的 528 + 532 只 no_data 股票全部被间隔缓存跳过，未重复请求。

## 3. 日期格式敏感（已修复，但易踩坑）—— 已加固

- 现象：数据库 `report_date` 存储格式为 `YYYY-MM-DD`，而采集入参为 `YYYYMMDD`（`ingest_top_holders` 内部转换）。
- 事故：重算脚本首次用 `20260630` 执行 `DELETE ... WHERE report_date=?` 与分析函数，匹配 0 行、旧结果未清除，导致首次重算看似完成实则未生效。
- 修复（2026-08-23）：
  - `database/db_manager.py` 新增 `normalize_report_date(date)`：兼容 `YYYYMMDD` / `YYYY-MM-DD` / `datetime/date`，统一返回 `YYYY-MM-DD`，非法输入抛 `ValueError`。
  - 应用点：`compute_holding_changes`、`compute_index_holding_summary`、`run_all_alerts` 入口归一化入参；`ingest_top_holders` 内部 db 日期转换复用。
  - 对外约定不变：采集入参仍为 `YYYYMMDD`（东财接口需要）；分析/预警函数现在兼容两种格式。
  - **实测**：以 `YYYYMMDD` 调用 `compute_holding_changes('20260630','20260331')` 等函数正常、结果幂等无重复。

## 4. 工具调用超时（环境层面，与代码无关）—— 已缓解

- 现象：执行过程中 `todo_write`、`plan_update`、`rm` 曾出现 Idle timeout 与权限审批超时。
- 影响：收尾动作延迟落盘。
- 缓解（2026-08-23）：
  - 代码层面：classify 批量优化（问题 1，25 分钟 → 3.2 秒）、no_data 采集间隔缓存（问题 2，跳过重复请求）、历史期回填/重算拆分为 `--only-backfill` / `--only-recompute` 两阶段独立执行（`scripts/backfill_fetch_status.py`，支持 `--dates` 分片）。
  - 执行层面：长任务使用 `nohup ... > data/logs/*.log 2>&1 &` 后台运行 + 日志落盘，关键状态尽早落盘。
  - 剩余风险：超长单命令（如全量回填 + 全量重算一条龙）仍建议拆分执行。

## 5. 其他报告期的采集状态未回填 —— 已修复

- 现象：`top_holder_fetch_status` 此前仅覆盖 2026-06-30 期（3388 条）。2026-03-31 及更早各期无状态记录。
- 影响：无状态时无法区分"数据缺失"与"真实退出"，重算其他期会再次误判退出。
- 修复（2026-08-23）：
  - 新增 `scripts/backfill_fetch_status.py`：对历史报告期重跑采集回填状态（有数据 skip 回填 `ok`、无数据写 `no_data`、异常写 `error`）；可选清理三张分析表并全量重算（`compute_all_holding_changes` + `compute_all_index_summaries` + `run_all_alerts`）；支持 `--only-backfill` / `--only-recompute` / `--dates`。
  - 已执行：6 个历史期（20250331 ~ 20260630）× 十大股东 + 十大流通股东，状态表从 3388 条扩展到 **20328 条**，覆盖全部报告期。
  - 重算后：中国银行（601988）在各期不再出现"退出"记录；历史各期因分类补全（问题 1 批量分类）分析记录更完整；`alerts` 中 29 条 `report_date IS NULL` 脏记录已清除。
  - 注意事项：`INSERT OR REPLACE`（upsert）无法清除已消失的退出记录，重算前必须显式 `DELETE` 三张分析/预警表（脚本已内置）。
