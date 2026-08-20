"""
北向资金持股数据采集
适配 akshare 1.18.92+
使用 stock_hsgt_individual_em 获取个股北向持股历史
"""
import logging
from datetime import datetime, timedelta
import pandas as pd
import akshare as ak

from ingestion.base import retry_on_error, safe_request
from database.db_manager import upsert_df, get_max_date
from ingestion.market_data import get_stock_name

logger = logging.getLogger(__name__)


@retry_on_error(max_retries=3)
def fetch_northbound_individual(stock_code: str) -> pd.DataFrame:
    """
    获取单只个股的北向资金历史持股明细
    """
    try:
        df = safe_request(ak.stock_hsgt_individual_em, symbol=stock_code)
    except Exception as e:
        logger.error(f"[Northbound] Fetch error for {stock_code}: {e}")
        return pd.DataFrame()
    
    if df is None or df.empty:
        return pd.DataFrame()
    
    return df


def _normalize_northbound_df(df: pd.DataFrame) -> pd.DataFrame:
    """标准化北向资金 DataFrame"""
    if df.empty:
        return df
    
    # akshare 1.18.92 返回列名:
    # 持股日期, 当日收盘价, 当日涨跌幅, 持股数量, 持股市值, 
    # 持股数量占A股百分比, 今日增持股数, 今日增持资金, 今日持股市值变化
    col_map = {
        "持股日期": "trade_date",
        "持股数量": "hold_shares",
        "持股市值": "hold_market_value",
        "持股数量占A股百分比": "hold_ratio",
        "今日增持股数": "net_buy_shares",
    }
    
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    
    # 日期转换
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    
    # 数值转换
    for col in ["hold_shares", "hold_market_value", "hold_ratio", "net_buy_shares"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce")
    
    # 筛选需要的列
    keep_cols = ["stock_code", "stock_name", "trade_date", "hold_shares", 
                 "hold_market_value", "hold_ratio", "net_buy_shares"]
    for col in keep_cols:
        if col not in df.columns:
            df[col] = None
    
    return df[keep_cols]


def ingest_northbound(stock_codes: list = None, days_back: int = 30):
    """
    采集北向资金个股持股数据
    如果 stock_codes 为 None，则采集全部（通过其他方式）
    """
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    if stock_codes is None:
        logger.info(f"[Northbound] No stock codes provided, skipping individual northbound fetch.")
        return 0
    
    logger.info(f"[Northbound] Fetching individual holdings for {len(stock_codes)} stocks...")
    
    total = 0
    for i, code in enumerate(stock_codes, 1):
        try:
            df = fetch_northbound_individual(code)
            if df.empty:
                continue
            
            df = _normalize_northbound_df(df)
            if df.empty:
                continue
            
            # 添加 stock_code
            df["stock_code"] = code
            df["stock_name"] = get_stock_name(code)
            if df["stock_name"].isna().all():
                logger.warning(f"[Northbound] No stock name found for {code}")
            
            # 日期过滤
            df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]
            
            if df.empty:
                continue
            
            upsert_df(df, "northbound_holdings", ["stock_code", "trade_date"])
            total += len(df)
            
            if i % 20 == 0:
                logger.info(f"[Northbound] Progress: {i}/{len(stock_codes)}, records: {total}")
                
        except Exception as e:
            logger.error(f"[Northbound] Error for {code}: {e}")
            continue
    
    logger.info(f"[Northbound] Ingestion completed. Total records: {total}")
    return total
