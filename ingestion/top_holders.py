"""
十大股东 / 十大流通股东 数据采集
适配 akshare 1.18.92+
"""
import logging
import re
from typing import Optional, List
import pandas as pd
import akshare as ak

from ingestion.base import retry_on_error, safe_request
from database.db_manager import upsert_df, get_max_date

logger = logging.getLogger(__name__)


def _add_exchange_prefix(stock_code: str) -> str:
    """
    给股票代码加上交易所前缀（sh/sz）
    如 000001 -> sz000001, 600519 -> sh600519
    """
    if not stock_code:
        return stock_code
    code = str(stock_code).strip()
    # 已经有前缀
    if code.startswith("sh") or code.startswith("sz"):
        return code
    # 判断交易所
    if re.match(r"^6", code) or re.match(r"^68", code) or re.match(r"^8", code) or re.match(r"^9", code):
        return f"sh{code}"
    else:
        return f"sz{code}"


@retry_on_error(max_retries=3)
def fetch_top_holders_em(stock_code: str, report_date: str, is_float: bool = False) -> pd.DataFrame:
    """
    从东方财富获取十大股东或十大流通股东
    stock_code: 如 000001
    report_date: YYYYMMDD 格式，如 20250331
    is_float: False=十大股东, True=十大流通股东
    """
    symbol = _add_exchange_prefix(stock_code)
    try:
        if is_float:
            df = safe_request(ak.stock_gdfx_free_top_10_em, symbol=symbol, date=report_date)
        else:
            df = safe_request(ak.stock_gdfx_top_10_em, symbol=symbol, date=report_date)
    except Exception as e:
        logger.warning(f"[TopHolders] EM fetch failed for {stock_code} {report_date}: {e}")
        return pd.DataFrame()
    
    if df is None or df.empty:
        return pd.DataFrame()
    
    return df


def _normalize_top_holders_df(df: pd.DataFrame, stock_code: str, report_date: str, is_float: bool) -> pd.DataFrame:
    """标准化十大股东 DataFrame"""
    if df.empty:
        return df
    
    # 列名映射（akshare 1.18.92 实际返回的列名）
    # 十大股东: 名次, 股东名称, 股份类型, 持股数, 占总股本持股比例, 增减, 变动比率
    # 十大流通股东: 名次, 股东名称, 股东性质, 股份类型, 持股数, 占总流通股本持股比例, 增减, 变动比率
    col_map = {
        "名次": "rank",
        "股东名称": "holder_name",
        "持股数": "hold_shares",
        "占总股本持股比例": "hold_ratio_total",
        "占总流通股本持股比例": "hold_ratio_float",
        "增减": "change_status",
        "变动比率": "change_ratio",
    }
    
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    
    # 添加 stock_code 和 report_date
    df["stock_code"] = stock_code
    df["report_date"] = pd.to_datetime(report_date, format="%Y%m%d").strftime("%Y-%m-%d")
    
    # 尝试从 df 获取 stock_name（没有 stock_name 列，需要另外查）
    df["stock_name"] = None
    
    # 数值转换
    for col in ["hold_shares", "hold_ratio_total", "hold_ratio_float", "change_ratio"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce")
    
    # 处理 change_status：可能是"增持"/"减持"/"不变"等文字，也可能是数字（持股变动数量）
    if "change_status" in df.columns:
        def normalize_change_status(val):
            if pd.isna(val):
                return "不变"
            s = str(val).strip()
            # 已经是标准文字
            if s in ["新进", "退出", "增持", "减持", "不变"]:
                return s
            # 尝试转为数字
            try:
                num = float(s.replace(",", ""))
                if num > 0:
                    return "增持"
                elif num < 0:
                    return "减持"
                else:
                    return "不变"
            except (ValueError, TypeError):
                # 其他文字，如"-"、"--"等
                if s in ["-", "--", "", "None", "nan"]:
                    return "不变"
                return s  # 保留原值
        df["change_status"] = df["change_status"].apply(normalize_change_status)
    
    # 标记是否为流通股东
    df["is_float_holder"] = 1 if is_float else 0
    
    # 筛选有效列
    required_cols = ["stock_code", "stock_name", "report_date", "holder_name", 
                     "hold_shares", "hold_ratio_total", "hold_ratio_float", 
                     "change_status", "rank", "is_float_holder"]
    
    for col in required_cols:
        if col not in df.columns:
            df[col] = None
    
    return df[required_cols]


def ingest_top_holders(stock_codes: list, report_dates: List[str], is_float: bool = False):
    """
    批量采集十大股东/十大流通股东数据
    report_dates: YYYYMMDD 格式列表，如 ['20250331', '20241231']
    """
    holder_type_str = "十大流通股东" if is_float else "十大股东"
    logger.info(f"[TopHolders] Start ingesting {holder_type_str} for {len(stock_codes)} stocks x {len(report_dates)} dates...")
    
    total = 0
    for i, code in enumerate(stock_codes, 1):
        for date in report_dates:
            try:
                df = fetch_top_holders_em(code, date, is_float=is_float)
                if df.empty:
                    continue
                
                df = _normalize_top_holders_df(df, code, date, is_float)
                if df.empty:
                    continue
                
                upsert_df(df, "top_holders", ["stock_code", "report_date", "holder_name", "is_float_holder"])
                total += len(df)
                
            except Exception as e:
                logger.error(f"[TopHolders] Error processing {code} {date}: {e}")
                continue
        
        if i % 20 == 0:
            logger.info(f"[TopHolders] Progress: {i}/{len(stock_codes)}, total records: {total}")
    
    logger.info(f"[TopHolders] Ingestion completed. Total records: {total}")
    return total


def ingest_all_top_holders(stock_codes: list, report_dates: List[str] = None):
    """
    采集十大股东和十大流通股东
    report_dates: 默认采集最近一个季报期
    """
    if report_dates is None:
        # 默认采集最近一期季报，可根据实际情况调整
        report_dates = ["20250331"]
    
    ingest_top_holders(stock_codes, report_dates, is_float=False)
    ingest_top_holders(stock_codes, report_dates, is_float=True)
