"""
行情与股本数据采集
"""
import logging
from datetime import datetime, timedelta
import pandas as pd
import akshare as ak

from ingestion.base import retry_on_error, safe_request
from database.db_manager import get_connection, query_sql, upsert_df

logger = logging.getLogger(__name__)


def _to_sina_symbol(stock_code: str) -> str:
    """将裸股票代码转换为新浪格式（sz000001 / sh600000）"""
    if stock_code.startswith(("sh", "sz")):
        return stock_code
    if stock_code.startswith(("0", "3")):
        return f"sz{stock_code}"
    if stock_code.startswith(("6", "9")):
        return f"sh{stock_code}"
    return stock_code


def fetch_stock_daily(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取个股日度行情（主接口失败自动切备选）"""
    # 主接口：新浪（需要带交易所前缀）
    try:
        sina_symbol = _to_sina_symbol(stock_code)
        df = safe_request(ak.stock_zh_a_daily, symbol=sina_symbol,
                          start_date=start_date, end_date=end_date, adjust="qfq",
                          verbose_error=False, fail_log_level=logging.WARNING)
        return df if df is not None else pd.DataFrame()
    except Exception:
        pass

    # 备选接口：东方财富
    try:
        df = safe_request(ak.stock_zh_a_hist, symbol=stock_code, period="daily",
                          start_date=start_date, end_date=end_date, adjust="qfq",
                          verbose_error=False, fail_log_level=logging.WARNING)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.warning(f"[MarketData] Failed to fetch {stock_code}: {e}")
        return pd.DataFrame()


def _normalize_daily_df(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    """标准化日度行情 DataFrame"""
    if df.empty:
        return df
    
    col_map = {
        # 东方财富（stock_zh_a_hist）中文列名
        "日期": "trade_date",
        "收盘": "close_price",
        "开盘": "open_price",
        "最高": "high_price",
        "最低": "low_price",
        "成交量": "volume",
        "成交额": "amount",
        # 新浪（stock_zh_a_daily）英文列名
        "date": "trade_date",
        "close": "close_price",
        "open": "open_price",
        "high": "high_price",
        "low": "low_price",
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


def _get_latest_price_date(stock_code: str) -> str:
    """查询某只股票在数据库中最新的行情日期，返回 'YYYY-MM-DD' 或 None"""
    rows = query_sql(
        "SELECT MAX(trade_date) AS max_date FROM daily_prices WHERE stock_code = ?",
        (stock_code,),
    )
    if rows and rows[0]["max_date"]:
        return rows[0]["max_date"]
    return None


def _latest_trading_day(ref_date: datetime = None) -> str:
    """返回 ref_date 当天或之前的最近可能交易日（简单跳过周末）"""
    d = ref_date or datetime.now()
    while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def ingest_daily_prices(
    stock_codes: list,
    days_back: int = 30,
    start_date: str = None,
    end_date: str = None,
):
    """
    批量采集个股日度行情（增量模式）

    - 默认采集最近 days_back 天的行情，自动跳过已有最新数据的股票
    - 指定 start_date / end_date（格式 YYYYMMDD）时按区间采集，不做跳过
    """
    req_end = end_date or datetime.now().strftime("%Y%m%d")
    req_start = start_date or (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
    # 用户显式指定日期范围时不做增量跳过，确保能补采任意历史区间
    incremental = start_date is None
    # 增量模式下用最近交易日做跳过判断，避免周末/节假日误触发无效请求
    effective_end = _latest_trading_day() if incremental else req_end

    logger.info(
        f"[MarketData] Fetching daily prices for {len(stock_codes)} stocks "
        f"(range: {req_start} ~ {req_end}, incremental: {incremental})..."
    )

    total = 0
    skipped = 0
    for i, code in enumerate(stock_codes, 1):
        try:
            fetch_start = req_start
            if incremental:
                # 增量检查：查数据库中该股票最新行情日期
                latest = _get_latest_price_date(code)
                if latest:
                    latest_compact = latest.replace("-", "")
                    if latest_compact >= effective_end:
                        skipped += 1
                        continue
                    # 只请求缺失段（从最新日期的下一天开始）
                    next_day = (
                        datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
                    ).strftime("%Y%m%d")
                    fetch_start = max(next_day, req_start)

            df = fetch_stock_daily(code, fetch_start, effective_end)
            if df.empty:
                continue

            df = _normalize_daily_df(df, code)
            if df.empty:
                continue

            upsert_df(df, "daily_prices", ["stock_code", "trade_date"])
            total += len(df)

            if i % 100 == 0:
                logger.info(
                    f"[MarketData] Progress: {i}/{len(stock_codes)}, "
                    f"fetched: {total}, skipped: {skipped}"
                )

        except Exception as e:
            logger.warning(f"[MarketData] Error for {code}: {e}")
            continue

    logger.info(
        f"[MarketData] Ingestion completed. "
        f"Total records: {total}, skipped stocks: {skipped}"
    )
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


def get_stock_name(stock_code: str):
    """从股票主数据表获取股票名称。"""
    rows = query_sql(
        """
        SELECT stock_name
        FROM stocks
        WHERE stock_code = ?
          AND stock_name IS NOT NULL
          AND stock_name != ''
        LIMIT 1
        """,
        (stock_code,),
    )
    return rows[0]["stock_name"] if rows else None


def sync_stocks_from_index_components():
    """使用已采集的指数成分股信息初始化股票主数据。"""
    rows = query_sql(
        """
        SELECT stock_code, stock_name
        FROM index_components
        WHERE stock_code IS NOT NULL
          AND stock_name IS NOT NULL
          AND stock_name != ''
        GROUP BY stock_code
        ORDER BY stock_code
        """
    )
    if not rows:
        logger.warning("[StockInfo] No named index components available.")
        return 0

    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO stocks (stock_code, stock_name)
            VALUES (?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET stock_name = excluded.stock_name
            WHERE stocks.stock_name IS NULL OR stocks.stock_name = ''
            """,
            [(row["stock_code"], row["stock_name"]) for row in rows],
        )
    logger.info(f"[StockInfo] Synced {len(rows)} stocks from index components.")
    return len(rows)
