"""
指数成分股采集模块
"""
import logging
from typing import List, Dict
import pandas as pd
import akshare as ak

from ingestion.base import retry_on_error, safe_request
from database.db_manager import upsert_df, query_sql
from config.settings import TRACKED_INDICES

logger = logging.getLogger(__name__)


@retry_on_error(max_retries=3)
def fetch_index_components_csindex(index_code: str) -> pd.DataFrame:
    """
    从中证指数官网获取指数成分股及权重
    """
    df = safe_request(ak.index_stock_cons_weight_csindex, symbol=index_code)
    if df is None or df.empty:
        return pd.DataFrame()
    
    # 统一列名c
    col_map = {
        "成分券代码": "stock_code",
        "成分券名称": "stock_name",
        "权重": "weight",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    
    # 处理不同的列名变体
    if "stock_code" not in df.columns:
        for c in df.columns:
            if "代码" in c or "code" in c.lower():
                df = df.rename(columns={c: "stock_code"})
                break
    if "stock_name" not in df.columns:
        for c in df.columns:
            if "名称" in c or "name" in c.lower():
                df = df.rename(columns={c: "stock_name"})
                break
    if "weight" not in df.columns:
        for c in df.columns:
            if "权重" in c or "weight" in c.lower():
                df = df.rename(columns={c: "weight"})
                break
    
    df["weight"] = pd.to_numeric(df.get("weight", 0), errors="coerce")
    return df


@retry_on_error(max_retries=3)
def fetch_index_components_em(index_code: str) -> pd.DataFrame:
    """
    从东方财富获取指数成分股（备用源）
    """
    df = safe_request(ak.index_stock_cons, symbol=index_code)
    if df is None or df.empty:
        return pd.DataFrame()
    
    col_map = {
        "品种代码": "stock_code",
        "品种名称": "stock_name",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df["weight"] = None
    return df


def ingest_index_components():
    """
    采集所有跟踪指数的成分股，写入数据库
    """
    logger.info("[IndexComponents] Start ingestion...")
    
    for name, info in TRACKED_INDICES.items():
        code = info["code"]
        logger.info(f"[IndexComponents] Fetching {name} ({code})...")

        try:
            df = fetch_index_components_csindex(code)
        except Exception as e:
            logger.warning(
                f"[IndexComponents] {name}: csindex failed: {e}; "
                "trying Eastmoney fallback..."
            )
            df = pd.DataFrame()

        if df.empty:
            logger.warning(
                f"[IndexComponents] {name}: csindex returned no data; "
                "trying Eastmoney fallback..."
            )
            try:
                df = fetch_index_components_em(code)
            except Exception as e:
                logger.error(f"[IndexComponents] Eastmoney fallback failed for {name}: {e}")
                continue
        
        if df.empty:
            logger.warning(f"[IndexComponents] No data for {name}")
            continue
        
        # 添加指数代码
        df["index_code"] = code
        df["effective_date"] = pd.Timestamp.now().strftime("%Y-%m-%d")
        
        # 确保列存在
        for col in ["stock_code", "stock_name", "weight", "index_code", "effective_date"]:
            if col not in df.columns:
                df[col] = None
        
        # 写入数据库
        try:
            upsert_df(df[["index_code", "stock_code", "stock_name", "weight", "effective_date"]],
                      "index_components", ["index_code", "stock_code", "effective_date"])
            logger.info(f"[IndexComponents] {name}: {len(df)} components saved.")
        except Exception as e:
            logger.error(f"[IndexComponents] DB error for {name}: {e}")
    
    # 更新指数基本信息
    _update_indices_info()
    logger.info("[IndexComponents] Ingestion completed.")


def _update_indices_info():
    """更新 indices 表"""
    for name, info in TRACKED_INDICES.items():
        code = info["code"]
        exchange = info.get("exchange", "")
        
        # 统计成分股数量
        result = query_sql(
            "SELECT COUNT(DISTINCT stock_code) as cnt FROM index_components WHERE index_code=?",
            (code,)
        )
        count = result[0]["cnt"] if result else 0
        
        sql = """
            INSERT OR REPLACE INTO indices (index_name, index_code, exchange, component_count, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
        """
        from database.db_manager import execute_sql
        execute_sql(sql, (name, code, exchange, count))


def get_index_stock_codes(index_name: str = None) -> List[str]:
    """获取指定指数或所有指数的成分股代码列表"""
    if index_name:
        info = TRACKED_INDICES.get(index_name)
        if not info:
            return []
        code = info["code"]
        sql = "SELECT DISTINCT stock_code FROM index_components WHERE index_code=?"
        rows = query_sql(sql, (code,))
    else:
        sql = "SELECT DISTINCT stock_code FROM index_components"
        rows = query_sql(sql)
    
    return [r["stock_code"] for r in rows]
