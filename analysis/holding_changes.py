"""
持仓变化分析引擎
"""
import logging
from typing import List, Dict, Optional
import pandas as pd
import numpy as np

from database.db_manager import query_sql, query_df, execute_sql, upsert_df
from config.settings import TRACKED_INDICES

logger = logging.getLogger(__name__)


def get_report_dates(stock_code: Optional[str] = None) -> List[str]:
    """获取数据库中所有报告期日期列表"""
    if stock_code:
        sql = "SELECT DISTINCT report_date FROM top_holders WHERE stock_code=? ORDER BY report_date"
        rows = query_sql(sql, (stock_code,))
    else:
        sql = "SELECT DISTINCT report_date FROM top_holders ORDER BY report_date"
        rows = query_sql(sql)
    return [r["report_date"] for r in rows if r["report_date"]]


def get_close_price(stock_code: str, date: str) -> float:
    """
    获取指定日期收盘价。
    优先查本地 daily_prices 表，找不到则用 akshare 临时获取。
    """
    # 1. 先精确匹配本地数据库
    sql = "SELECT close_price FROM daily_prices WHERE stock_code=? AND trade_date=?"
    rows = query_sql(sql, (stock_code, date))
    if rows and rows[0]["close_price"]:
        return float(rows[0]["close_price"])
    
    # 2. 向前查找本地最近的价格
    sql = """
        SELECT close_price FROM daily_prices 
        WHERE stock_code=? AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 1
    """
    rows = query_sql(sql, (stock_code, date))
    if rows and rows[0]["close_price"]:
        return float(rows[0]["close_price"])
    
    # 3. Fallback：用 akshare 临时获取（不依赖预采集的行情表）
    try:
        import akshare as ak
        dt = pd.to_datetime(date)
        start = (dt - pd.Timedelta(days=10)).strftime("%Y%m%d")
        end = dt.strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily",
                                start_date=start, end_date=end, adjust="qfq")
        if df is not None and not df.empty:
            df = df.sort_values(df.columns[0], ascending=False)
            close = df.iloc[0].get("收盘") or df.iloc[0].get("Close")
            if close:
                return float(close)
    except Exception:
        pass
    
    # 4. 演示模式：使用模拟价格（基于股票代码生成固定价格，保证可复现）
    from config.settings import DEMO_MODE
    if DEMO_MODE:
        price = (hash(stock_code) % 19500 + 500) / 100
        return round(price, 2)
    
    return np.nan


def compute_holding_changes(report_date: str, prev_report_date: str):
    """
    计算单期持仓变化（新进/退出/增持/减持）
    结果写入 holding_changes_summary 表
    """
    logger.info(f"[Analysis] Computing changes: {prev_report_date} -> {report_date}")
    
    sql = """
        SELECT 
            t.stock_code, t.stock_name, t.holder_name, t.holder_type,
            t.hold_shares, t.is_float_holder
        FROM top_holders t
        WHERE t.report_date = ?
          AND t.holder_type IS NOT NULL
          AND t.holder_type != '其他'
    """
    curr_df = query_df(sql, (report_date,))
    prev_df = query_df(sql, (prev_report_date,))
    
    if curr_df.empty:
        logger.warning(f"[Analysis] No current data for {report_date}")
        return 0
    
    # 聚合到 (stock_code, holder_type) 维度
    curr_agg = curr_df.groupby(["stock_code", "stock_name", "holder_type"]).agg({
        "hold_shares": "sum"
    }).reset_index()
    
    prev_agg = prev_df.groupby(["stock_code", "holder_type"]).agg({
        "hold_shares": "sum"
    }).reset_index() if not prev_df.empty else pd.DataFrame(
        columns=["stock_code", "holder_type", "hold_shares"]
    )
    
    # 合并本期和上期
    merged = curr_agg.merge(
        prev_agg[["stock_code", "holder_type", "hold_shares"]].rename(
            columns={"hold_shares": "prev_hold_shares"}
        ),
        on=["stock_code", "holder_type"],
        how="left"
    )
    merged["prev_hold_shares"] = merged["prev_hold_shares"].fillna(0)
    
    # 补充上期有但本期没有的（退出）
    exit_df = prev_agg.merge(
        curr_agg[["stock_code", "holder_type"]],
        on=["stock_code", "holder_type"],
        how="left",
        indicator=True
    )
    exit_df = exit_df[exit_df["_merge"] == "left_only"][["stock_code", "holder_type", "hold_shares"]]
    exit_df = exit_df.rename(columns={"hold_shares": "prev_hold_shares"})
    exit_df["hold_shares"] = 0
    exit_df["stock_name"] = None
    
    if not exit_df.empty:
        # 补充 stock_name：先删掉全 None 的列，避免 merge 产生 _x/_y 后缀
        exit_df = exit_df.drop(columns=["stock_name"])
        name_map = query_df("SELECT DISTINCT stock_code, stock_name FROM top_holders WHERE report_date=?", 
                           (prev_report_date,))
        if not name_map.empty:
            exit_df = exit_df.merge(name_map, on="stock_code", how="left")
        else:
            exit_df["stock_name"] = None
        merged = pd.concat([merged, exit_df], ignore_index=True)
    
    # 计算变化
    merged["change_shares"] = merged["hold_shares"] - merged["prev_hold_shares"]
    
    # 持股市值（退出记录用上一期持股数，反映退出前的实际持仓规模）
    merged["effective_shares"] = merged.apply(
        lambda row: row["hold_shares"] if row["hold_shares"] > 0 else row["prev_hold_shares"], axis=1
    )
    merged["total_hold_market_value"] = merged.apply(
        lambda row: row["effective_shares"] * get_close_price(row["stock_code"], report_date), axis=1
    )
    merged["change_market_value"] = merged.apply(
        lambda row: row["change_shares"] * get_close_price(row["stock_code"], report_date), axis=1
    )
    
    # 变化状态
    def get_change_status(row):
        if row["prev_hold_shares"] == 0 and row["hold_shares"] > 0:
            return "新进"
        elif row["hold_shares"] == 0 and row["prev_hold_shares"] > 0:
            return "退出"
        elif row["change_shares"] > 0:
            return "增持"
        elif row["change_shares"] < 0:
            return "减持"
        else:
            return "不变"
    
    merged["change_status"] = merged.apply(get_change_status, axis=1)
    
    # 写入数据库
    output = merged[["stock_code", "stock_name", "holder_type", "hold_shares", 
                     "total_hold_market_value", "change_shares", "change_market_value", "change_status"]].copy()
    output["report_date"] = report_date
    output["prev_report_date"] = prev_report_date
    output = output.rename(columns={
        "hold_shares": "total_hold_shares",
        "total_hold_market_value": "total_market_value",
    })
    
    # 标准化列名
    keep_cols = ["report_date", "prev_report_date", "stock_code", "stock_name", 
                 "holder_type", "total_hold_shares", "total_market_value",
                 "change_shares", "change_market_value", "change_status"]
    for col in keep_cols:
        if col not in output.columns:
            output[col] = None
    output = output[keep_cols]
    
    try:
        upsert_df(output, "holding_changes_summary", ["report_date", "stock_code", "holder_type"])
        logger.info(f"[Analysis] Saved {len(output)} change records for {report_date}.")
        return len(output)
    except Exception as e:
        logger.error(f"[Analysis] Failed to save changes: {e}")
        return 0


def compute_all_holding_changes():
    """
    计算所有相邻报告期的持仓变化
    """
    dates = get_report_dates()
    if len(dates) < 2:
        logger.warning("[Analysis] Not enough report dates to compute changes.")
        return 0
    
    total = 0
    for i in range(1, len(dates)):
        prev_date = dates[i - 1]
        curr_date = dates[i]
        total += compute_holding_changes(curr_date, prev_date)
    
    logger.info(f"[Analysis] All changes computed. Total records: {total}")
    return total


def compute_index_holding_summary(report_date: str):
    """
    按指数汇总某报告期的机构持仓
    """
    logger.info(f"[Analysis] Computing index summary for {report_date}...")
    
    # 获取该日期各指数成分股的机构持仓汇总
    sql = """
        SELECT 
            ic.index_code,
            hcs.holder_type,
            COUNT(DISTINCT hcs.stock_code) as stock_count,
            SUM(hcs.total_market_value) as total_market_value,
            SUM(hcs.change_market_value) as total_change_value
        FROM holding_changes_summary hcs
        JOIN index_components ic ON hcs.stock_code = ic.stock_code
        WHERE hcs.report_date = ?
        GROUP BY ic.index_code, hcs.holder_type
    """
    df = query_df(sql, (report_date,))
    
    if df.empty:
        logger.warning(f"[Analysis] No index summary data for {report_date}")
        return 0
    
    # 补充指数名称
    index_names = {v["code"]: k for k, v in TRACKED_INDICES.items()}
    df["index_name"] = df["index_code"].map(index_names)
    df["report_date"] = report_date
    
    keep_cols = ["report_date", "index_code", "index_name", "holder_type",
                 "stock_count", "total_market_value", "total_change_value"]
    for col in keep_cols:
        if col not in df.columns:
            df[col] = None
    df = df[keep_cols]
    
    try:
        upsert_df(df, "index_holding_summary", ["report_date", "index_code", "holder_type"])
        logger.info(f"[Analysis] Saved {len(df)} index summary records.")
        return len(df)
    except Exception as e:
        logger.error(f"[Analysis] Failed to save index summary: {e}")
        return 0


def compute_all_index_summaries():
    """计算所有报告期的指数汇总"""
    dates = get_report_dates()
    total = 0
    for d in dates[1:]:  # 跳过第一个（没有上期对比）
        total += compute_index_holding_summary(d)
    return total
