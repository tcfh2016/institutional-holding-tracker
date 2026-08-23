#!/usr/bin/env python3
"""
历史报告期采集状态回填 + 分析结果全量重算脚本

背景：top_holder_fetch_status 采集状态表此前仅覆盖 2026-06-30 期，
历史各期（2025-03-31 ~ 2026-03-31）无状态记录，导致分析引擎无法区分
"数据缺失"与"真实退出"，可能误判退出。

用法：
    python scripts/backfill_fetch_status.py                # 回填 + 清理 + 全量重算
    python scripts/backfill_fetch_status.py --only-backfill  # 只回填采集状态，不重算
    python scripts/backfill_fetch_status.py --only-recompute # 只清理并重算分析（状态须已回填）
    python scripts/backfill_fetch_status.py --dates 20250331,20250630  # 指定报告期（YYYYMMDD）

说明：
    - 回填复用 ingest_all_top_holders：已有数据 skip 并回填 ok、无数据写 no_data、异常写 error；
      no_data/error 距上次采集不足 NO_DATA_RECHECK_DAYS 天（默认 7 天）的组合自动跳过请求。
    - 重算前必须全量 DELETE 三张分析/预警表（INSERT OR REPLACE 无法清除已消失的退出记录；
      run_all_alerts 只处理缺失报告期，不清理则不会重新生成）。
    - 重算前先 update_top_holders_type() 确保新增记录已分类（批量 UPDATE，分钟级）。
"""
import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_fetch_status")

from database.db_manager import init_database, get_tracked_stock_codes, execute_sql, query_sql
from cleansing.holder_classifier import init_holder_mappings, update_top_holders_type
from ingestion.top_holders import ingest_all_top_holders
from analysis.holding_changes import (
    compute_all_holding_changes,
    compute_all_index_summaries,
    get_report_dates,
)
from alerting.rules import run_all_alerts

# 历史报告期（YYYYMMDD，采集入参格式）
DEFAULT_DATES = ["20250331", "20250630", "20250930", "20251231", "20260331", "20260630"]

# 需全量清理的分析/预警表
ANALYSIS_TABLES = ["holding_changes_summary", "index_holding_summary", "alerts"]


def log_step(msg: str):
    logger.info("=" * 60)
    logger.info(msg)
    logger.info("=" * 60)


def backfill_fetch_status(dates: list):
    """回填采集状态：有数据 skip 回填 ok、无数据写 no_data、异常写 error。"""
    start = time.time()
    codes = get_tracked_stock_codes()
    logger.info(f"[Backfill] Tracked stock pool: {len(codes)} stocks, dates: {dates}")
    ingest_all_top_holders(codes, report_dates=dates, force_refresh=False)
    elapsed = time.time() - start
    logger.info(f"[Backfill] Backfill done in {elapsed:.1f}s")


def status_summary():
    """打印采集状态表覆盖统计（报告期 x 口径 x 状态）。"""
    rows = query_sql(
        """
        SELECT report_date, is_float_holder, status, COUNT(*) AS cnt
        FROM top_holder_fetch_status
        GROUP BY report_date, is_float_holder, status
        ORDER BY report_date, is_float_holder
        """
    )
    if not rows:
        logger.warning("[Backfill] top_holder_fetch_status is EMPTY!")
        return
    logger.info("[Backfill] Fetch status coverage:")
    for r in rows:
        holder = "float" if r["is_float_holder"] else "top10"
        logger.info(
            f"  {r['report_date']} [{holder:>5}] {r['status']:<8} {r['cnt']:>6}"
        )


def clear_analysis_tables():
    """全量清理分析/预警结果表（upsert 无法清除已消失的退出记录，必须显式 DELETE）。"""
    for table in ANALYSIS_TABLES:
        n = execute_sql(f"DELETE FROM {table}")
        logger.info(f"[Backfill] Cleared {table}: {n} rows deleted")


def recompute_analysis():
    """全量重算：分类 -> 持仓变化 -> 指数汇总 -> 预警。"""
    init_holder_mappings()
    start = time.time()
    log_step("Reclassifying unclassified holders (batch update)...")
    n = update_top_holders_type()
    logger.info(f"[Backfill] Classified {n} records in {time.time() - start:.1f}s")

    start = time.time()
    log_step("Computing holding changes for all adjacent periods...")
    compute_all_holding_changes()
    logger.info(f"[Backfill] Holding changes done in {time.time() - start:.1f}s")

    start = time.time()
    log_step("Computing index summaries...")
    compute_all_index_summaries()
    logger.info(f"[Backfill] Index summaries done in {time.time() - start:.1f}s")

    dates = get_report_dates()
    if len(dates) >= 2:
        log_step("Running alert rules...")
        run_all_alerts(dates[1:])  # 跳过第一个（没有上期对比，无法生成预警）
    else:
        logger.warning("[Backfill] Not enough report dates for alerts.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only-backfill", action="store_true",
        help="只回填采集状态，不清理/不重算分析",
    )
    parser.add_argument(
        "--only-recompute", action="store_true",
        help="只清理旧分析记录并全量重算（采集状态须已回填）",
    )
    parser.add_argument(
        "--dates", default=",".join(DEFAULT_DATES),
        help="报告期列表（YYYYMMDD，逗号分隔），默认全部 6 个历史期",
    )
    args = parser.parse_args()

    init_database()
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    if not dates:
        logger.error("[Backfill] No dates provided.")
        sys.exit(1)

    if args.only_recompute:
        log_step("Phase 2/2: clear analysis tables & full recompute")
        clear_analysis_tables()
        recompute_analysis()
        log_step("All done.")
    elif args.only_backfill:
        log_step("Phase 1/2: backfill fetch status for historical periods")
        backfill_fetch_status(dates)
        status_summary()
        log_step("Backfill done.")
    else:
        log_step("Phase 1/2: backfill fetch status for historical periods")
        backfill_fetch_status(dates)
        status_summary()
        log_step("Phase 2/2: clear analysis tables & full recompute")
        clear_analysis_tables()
        recompute_analysis()
        log_step("All done.")


if __name__ == "__main__":
    main()
