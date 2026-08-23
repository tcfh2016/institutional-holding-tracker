"""
十大股东 / 十大流通股东 数据采集

直接请求东方财富股东数据接口，规避 akshare stock_gdfx_*_top_10_em 的缺陷：
akshare 1.18.94 在目标报告期无数据（东财返回空列表）时会抛出
"Length mismatch: Expected axis has 1 elements, new values have 12 elements"，
将"正常无数据"误报为异常并反复打印错误日志。
"""
import datetime
import logging
import re
import socket
from typing import Optional, List
import pandas as pd
import requests

from config.settings import REQUEST_TIMEOUT, NO_DATA_RECHECK_DAYS
from ingestion.base import retry_on_error
from database.db_manager import query_sql, upsert_df, normalize_report_date
from ingestion.market_data import get_stock_name

logger = logging.getLogger(__name__)

# 东财股东数据接口（与 akshare 内部使用的接口一致）
_EM_GDFX_FREE_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageSDLTGD"
)  # 十大流通股东
_EM_GDFX_TOP_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageSDGD"
)  # 十大股东

# 东财 JSON 字段 key -> 中文列名（与 akshare 返回列名保持一致，兼容下游处理）
_EM_FIELD_MAP = {
    "HOLDER_RANK": "名次",
    "HOLDER_NAME": "股东名称",
    "HOLDER_TYPE": "股东性质",              # 仅流通股东
    "SHARES_TYPE": "股份类型",
    "HOLD_NUM": "持股数",
    "FREE_HOLDNUM_RATIO": "占总流通股本持股比例",  # 仅流通股东
    "HOLD_NUM_RATIO": "占总股本持股比例",          # 仅十大股东
    "HOLD_NUM_CHANGE": "增减",
    "CHANGE_RATIO": "变动比率",
}
_EM_ALL_COLS = [
    "名次", "股东名称", "股东性质", "股份类型", "持股数",
    "占总流通股本持股比例", "占总股本持股比例", "增减", "变动比率",
]


def _has_top_holders_data(stock_code: str, report_date: str, is_float: bool) -> bool:
    """判断指定股票、报告期和股东口径是否已有数据。"""
    db_report_date = normalize_report_date(report_date)
    rows = query_sql(
        """
        SELECT 1
        FROM top_holders
        WHERE stock_code = ? AND report_date = ? AND is_float_holder = ?
        LIMIT 1
        """,
        (stock_code, db_report_date, 1 if is_float else 0),
    )
    return bool(rows)


def _load_existing_holder_keys() -> set:
    """
    批量预加载 top_holders 全部 (stock_code, report_date, is_float_holder) 组合到内存 set，
    供跳过检查使用，消除补采历史报告期时的 N+1 逐条查询。
    """
    rows = query_sql(
        "SELECT DISTINCT stock_code, report_date, is_float_holder FROM top_holders"
    )
    return {(r["stock_code"], r["report_date"], r["is_float_holder"]) for r in rows}


def _load_fetch_status_map() -> dict:
    """
    批量预加载采集状态表全部 (stock_code, report_date, is_float_holder) -> (status, fetched_at)，
    供 skip 分支判断：已有数据回填 ok、no_data/error 且未超重试间隔则跳过请求。
    """
    rows = query_sql(
        "SELECT stock_code, report_date, is_float_holder, status, fetched_at "
        "FROM top_holder_fetch_status"
    )
    return {
        (r["stock_code"], r["report_date"], r["is_float_holder"]): (r["status"], r["fetched_at"])
        for r in rows
    }


def _within_recheck_interval(
    status: str, fetched_at, now_utc: datetime.datetime, interval_days: int
) -> bool:
    """
    判断 no_data/error 状态组合是否距上次采集不足 interval_days 天（是则跳过请求）。
    fetched_at 为 SQLite CURRENT_TIMESTAMP（UTC），统一按 UTC 比较，避免时区偏差。
    """
    if status not in ("no_data", "error") or not fetched_at:
        return False
    try:
        fetched_dt = pd.to_datetime(fetched_at)
        if fetched_dt.tzinfo is None:
            fetched_dt = fetched_dt.tz_localize("UTC")
        else:
            fetched_dt = fetched_dt.tz_convert("UTC")
    except (ValueError, TypeError):
        return False
    return (now_utc - fetched_dt) < datetime.timedelta(days=interval_days)


def _status_row(stock_code: str, report_date: str, is_float: bool, status: str) -> dict:
    """构造一条采集状态记录。"""
    return {
        "stock_code": stock_code,
        "report_date": report_date,
        "is_float_holder": 1 if is_float else 0,
        "status": status,
    }


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


def _fetch_gdfx_raw(symbol: str, report_date: str, is_float: bool) -> list:
    """直接请求东财股东接口，返回原始股东列表（可能为空列表）。

    网络/HTTP/JSON 解析异常会向上抛出，交由调用方重试；
    "该报告期无数据"（东财返回空列表）则静默返回 []，不视为异常。
    """
    url = _EM_GDFX_FREE_URL if is_float else _EM_GDFX_TOP_URL
    params = {
        "code": symbol.upper(),
        "date": "-".join([report_date[:4], report_date[4:6], report_date[6:]]),
    }
    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(REQUEST_TIMEOUT)
    try:
        resp = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        resp.raise_for_status()
        data = resp.json()
        key = "sdltgd" if is_float else "sdgd"
        lst = data.get(key)
        return lst if isinstance(lst, list) else []
    finally:
        socket.setdefaulttimeout(prev_timeout)


def _gdfx_raw_to_df(raw_list: list, is_float: bool) -> pd.DataFrame:
    """将东财原始股东列表转换为中文列名 DataFrame（列名与 akshare 保持一致）。"""
    if not raw_list:
        return pd.DataFrame()
    df = pd.DataFrame(raw_list)
    rename = {k: v for k, v in _EM_FIELD_MAP.items() if k in df.columns}
    df = df.rename(columns=rename)
    # 十大股东与十大流通股东字段差异：缺失列补 None
    for col in _EM_ALL_COLS:
        if col not in df.columns:
            df[col] = None
    return df[_EM_ALL_COLS]


@retry_on_error(max_retries=3)
def fetch_top_holders_em(stock_code: str, report_date: str, is_float: bool = False) -> pd.DataFrame:
    """
    从东方财富获取十大股东或十大流通股东
    stock_code: 如 000001
    report_date: YYYYMMDD 格式，如 20250331
    is_float: False=十大股东, True=十大流通股东

    无数据（目标报告期尚未披露/东财未收录）时返回空 DataFrame，不打错误日志；
    仅网络/接口异常时抛出并由重试装饰器处理。
    """
    symbol = _add_exchange_prefix(stock_code)
    holder_type = "十大流通股东" if is_float else "十大股东"
    try:
        raw_list = _fetch_gdfx_raw(symbol, report_date, is_float)
    except Exception as e:
        logger.warning(
            f"[TopHolders] {holder_type} request failed for {stock_code} "
            f"{report_date}: {type(e).__name__}: {e}"
        )
        raise

    if not raw_list:
        logger.debug(f"[TopHolders] No {holder_type} data for {stock_code} {report_date}")
        return pd.DataFrame()

    return _gdfx_raw_to_df(raw_list, is_float)


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
    df["report_date"] = normalize_report_date(report_date)
    
    df["stock_name"] = get_stock_name(stock_code)
    if df["stock_name"].isna().all():
        logger.debug(f"[TopHolders] No stock name found for {stock_code}")
    
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


def ingest_top_holders(
    stock_codes: list,
    report_dates: List[str],
    is_float: bool = False,
    force_refresh: bool = False,
    recheck_interval_days: int = NO_DATA_RECHECK_DAYS,
):
    """
    批量采集十大股东/十大流通股东数据
    report_dates: YYYYMMDD 格式列表，如 ['20250331', '20241231']
    recheck_interval_days: no_data/error 状态组合距上次采集不足该天数时跳过请求（force_refresh 忽略）
    """
    holder_type_str = "十大流通股东" if is_float else "十大股东"
    logger.info(f"[TopHolders] Start ingesting {holder_type_str} for {len(stock_codes)} stocks x {len(report_dates)} dates...")
    
    # 批量预加载已有数据组合，消除 N+1 逐条查询
    existing_keys = set() if force_refresh else _load_existing_holder_keys()
    # 批量预加载已有采集状态：{(code, date, flag): (status, fetched_at)}
    fetch_status_map = {} if force_refresh else _load_fetch_status_map()
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    total = 0
    no_data = 0
    errors = 0
    skipped = 0
    recheck_skipped = 0
    status_records = []  # 待批量写入的采集状态记录
    for i, code in enumerate(stock_codes, 1):
        for date in report_dates:
            float_flag = 1 if is_float else 0
            db_report_date = normalize_report_date(date)
            try:
                if not force_refresh:
                    key = (code, db_report_date, float_flag)
                    if key in existing_keys:
                        skipped += 1
                        logger.info(
                            f"[TopHolders] Skip existing data for {code} {date} "
                            f"({holder_type_str})"
                        )
                        # 已有数据但状态缺失（改造前存量数据）：幂等回填 ok
                        if key not in fetch_status_map:
                            status_records.append(
                                _status_row(code, db_report_date, is_float, "ok")
                            )
                        continue
                    # no_data/error 且未超重试间隔：跳过请求（计数但不重写状态）
                    status_entry = fetch_status_map.get(key)
                    if status_entry and _within_recheck_interval(
                        status_entry[0], status_entry[1], now_utc, recheck_interval_days
                    ):
                        recheck_skipped += 1
                        continue

                df = fetch_top_holders_em(code, date, is_float=is_float)
                if df.empty:
                    no_data += 1
                    status_records.append(
                        _status_row(code, db_report_date, is_float, "no_data")
                    )
                    continue
                
                df = _normalize_top_holders_df(df, code, date, is_float)
                if df.empty:
                    no_data += 1
                    status_records.append(
                        _status_row(code, db_report_date, is_float, "no_data")
                    )
                    continue
                
                upsert_df(df, "top_holders", ["stock_code", "report_date", "holder_name", "is_float_holder"])
                total += len(df)
                status_records.append(
                    _status_row(code, db_report_date, is_float, "ok")
                )
                
            except Exception as e:
                errors += 1
                status_records.append(
                    _status_row(code, db_report_date, is_float, "error")
                )
                logger.error(f"[TopHolders] Error processing {code} {date}: {e}")
                continue
        
        if i % 20 == 0:
            logger.info(f"[TopHolders] Progress: {i}/{len(stock_codes)}, total records: {total}")

    # 批量写入采集状态
    if status_records:
        upsert_df(
            pd.DataFrame(status_records),
            "top_holder_fetch_status",
            ["stock_code", "report_date", "is_float_holder"],
        )

    logger.info(
        f"[TopHolders] Ingestion completed. Total records: {total}; "
        f"no data: {no_data}; skipped: {skipped}; "
        f"recheck skipped (within {recheck_interval_days}d): {recheck_skipped}; "
        f"processing errors: {errors}"
    )
    return total


def ingest_all_top_holders(
    stock_codes: list,
    report_dates: List[str] = None,
    force_refresh: bool = False,
    recheck_interval_days: int = NO_DATA_RECHECK_DAYS,
):
    """
    采集十大股东和十大流通股东
    report_dates: 默认采集最近一个季报期
    recheck_interval_days: no_data/error 状态组合距上次采集不足该天数时跳过请求（force_refresh 忽略）
    """
    if report_dates is None:
        # 默认采集最近一期季报，可根据实际情况调整
        report_dates = ["20250331"]
    
    ingest_top_holders(
        stock_codes, report_dates, is_float=False,
        force_refresh=force_refresh, recheck_interval_days=recheck_interval_days,
    )
    ingest_top_holders(
        stock_codes, report_dates, is_float=True,
        force_refresh=force_refresh, recheck_interval_days=recheck_interval_days,
    )
