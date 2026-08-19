"""
行情与股本数据采集
"""
import logging
from datetime import datetime, timedelta
import pandas as pd
import akshare as ak

from ingestion.base import retry_on_error, safe_request
from database.db_manager import upsert_df

logger = logging.getLogger(__name__)


@retry_on_error(max_retries=3)
def fetch_stock_daily(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取个股日度行情"""
    try:
        # 尝试带 symbol 参数的接口
        df = safe_request(ak.stock_zh_a_hist, symbol=stock_code, period="daily",
                          start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        # 备选接口
        try:
            df = safe_request(ak.stock_zh_a_daily, symbol=stock_code, 
                              start_date=start_date, end_date=end_date, adjust="qfq")
        except Exception as e:
            logger.warning(f"[MarketData] Failed to fetch {stock_code}: {e}")
            return pd.DataFrame()
    
    return df if df is not None else pd.DataFrame()


def _normalize_daily_df(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    """标准化日度行情 DataFrame"""
    if df.empty:
        return df
    
    col_map = {
        "日期": "trade_date",
        "收盘": "close_price",
        "开盘": "open_price",
        "最高": "high_price",
        "最低": "low_price",
        "成交量": "volume",
        "成交额": "amount",
    }
    
    # 处理 ak.stock_zh_a_hist 返回的列名
    for c in df.columns:
        for key, val in col_map.items():
            if key in c and val not in df.columns:
                df = df.rename(columns={c: val})
                break
    
    df["stock_code"] = stock_code
    
    # 日期转换
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    
    # 数值转换
    for col in ["close_price", "open_price", "high_price", "low_price", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")
    
    keep_cols = ["stock_code", "trade_date", "close_price", "open_price", 
                 "high_price", "low_price", "volume", "amount"]
    for col in keep_cols:
        if col not in df.columns:
            df[col] = None
    
    return df[keep_cols]


def ingest_daily_prices(stock_codes: list, days_back: int = 30):
    """
    批量采集个股日度行情
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
    
    logger.info(f"[MarketData] Fetching daily prices for {len(stock_codes)} stocks...")
    
    total = 0
    for i, code in enumerate(stock_codes, 1):
        try:
            df = fetch_stock_daily(code, start_date, end_date)
            if df.empty:
                continue
            
            df = _normalize_daily_df(df, code)
            if df.empty:
                continue
            
            upsert_df(df, "daily_prices", ["stock_code", "trade_date"])
            total += len(df)
            
            if i % 100 == 0:
                logger.info(f"[MarketData] Progress: {i}/{len(stock_codes)}, records: {total}")
                
        except Exception as e:
            logger.error(f"[MarketData] Error for {code}: {e}")
            continue
    
    logger.info(f"[MarketData] Ingestion completed. Total records: {total}")
    return total


@retry_on_error(max_retries=3)
def fetch_stock_info() -> pd.DataFrame:
    """获取全部A股基础信息（总股本、流通股本、行业等）"""
    try:
        df = safe_request(ak.stock_info_a_code_name)
        if df is None or df.empty:
            df = safe_request(ak.stock_zh_a_spot_em)
    except Exception as e:
        logger.error(f"[StockInfo] Fetch error: {e}")
        return pd.DataFrame()
    
    return df


def ingest_stock_info():
    """更新股票基础信息"""
    logger.info("[StockInfo] Fetching stock basic info...")
    
    try:
        df = fetch_stock_info()
        if df.empty:
            logger.warning("[StockInfo] No data returned.")
            return 0
        
        # 尝试获取更详细的股本信息
        try:
            cap_df = safe_request(ak.stock_zh_a_gdhs, symbol="全部股票")
            if cap_df is not None and not cap_df.empty:
                # 合并股本数据
                pass  # 列名差异较大，简化处理
        except Exception:
            pass
        
        # 写入 stocks 表（简化版，只有代码和名称）
        if "代码" in df.columns and "名称" in df.columns:
            out = df[["代码", "名称"]].rename(columns={"代码": "stock_code", "名称": "stock_name"})
            upsert_df(out, "stocks", ["stock_code"])
            logger.info(f"[StockInfo] Saved {len(out)} stocks.")
            return len(out)
        
    except Exception as e:
        logger.error(f"[StockInfo] Ingestion failed: {e}")
    
    return 0
