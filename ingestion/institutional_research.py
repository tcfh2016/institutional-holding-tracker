"""
机构调研记录采集
使用东方财富机构调研明细接口。
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

from database.db_manager import query_sql, table_exists, upsert_df
from config.settings import REQUEST_DELAY

logger = logging.getLogger(__name__)

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/jgdy/xx.html",
    "Accept": "application/json, text/plain, */*",
}


def _request_institutional_research(
    start_date: str = "",
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """获取指定日期之后的机构调研明细。日期格式为 YYYYMMDD。"""
    start = pd.to_datetime(start_date, format="%Y%m%d").strftime("%Y-%m-%d")
    date_filter = f'(IS_SOURCE="1")(RECEIVE_START_DATE>\'{start}\')'
    if end_date:
        end = pd.to_datetime(end_date, format="%Y%m%d").strftime("%Y-%m-%d")
        date_filter += f'(RECEIVE_START_DATE<=\'{end}\')'

    params = {
            "sortColumns": "NOTICE_DATE,RECEIVE_START_DATE,SECURITY_CODE,NUMBERNEW",
            "sortTypes": "-1,-1,1,-1",
            "pageSize": "50",
            "pageNumber": "1",
            "reportName": "RPT_ORG_SURVEY",
            "columns": (
                "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NOTICE_DATE,"
                "RECEIVE_START_DATE,RECEIVE_OBJECT,RECEIVE_PLACE,"
                "RECEIVE_WAY_EXPLAIN,INVESTIGATORS,RECEPTIONIST,ORG_TYPE"
            ),
            "quoteColumns": "f2~01~SECURITY_CODE~CLOSE_PRICE,f3~01~SECURITY_CODE~CHANGE_RATE",
            "quoteType": "0",
            "source": "WEB",
            "client": "WEB",
            "filter": date_filter,
    }
    response = requests.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params=params,
            headers=REQUEST_HEADERS,
            timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result")
    if not payload.get("success") or not result:
        raise RuntimeError(f"Unexpected research API response: {payload}")

    pages = int(result.get("pages", 1))
    records = list(result.get("data") or [])
    for page in range(2, pages + 1):
        params["pageNumber"] = str(page)
        page_response = requests.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params=params,
            headers=REQUEST_HEADERS,
            timeout=30,
        )
        page_response.raise_for_status()
        page_payload = page_response.json()
        page_result = page_payload.get("result")
        if not page_payload.get("success") or not page_result:
            raise RuntimeError(f"Unexpected research page response: {page_payload}")
        records.extend(page_result.get("data") or [])
        time.sleep(max(REQUEST_DELAY, 1.0))

    df = pd.DataFrame(records)

    if df is None or df.empty:
        return pd.DataFrame()
    return df


def fetch_institutional_research(
    start_date: str = "",
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """获取调研明细；接口暂时不可用时返回空表，不中断流水线。"""
    for attempt in range(1, 4):
        try:
            return _request_institutional_research(start_date, end_date)
        except Exception as exc:
            if attempt < 3:
                delay = float(attempt)
                logger.warning(
                    f"[Research] Request attempt {attempt}/3 failed for "
                    f"{start_date}-{end_date or 'latest'}: {exc}; "
                    f"retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            else:
                logger.warning(
                    f"[Research] Fetch unavailable from {start_date}-"
                    f"{end_date or 'latest'} after 3 attempts: {exc}"
                )
                return pd.DataFrame()
    return pd.DataFrame()


def _normalize_research_df(df: pd.DataFrame) -> pd.DataFrame:
    """将 AkShare 调研明细转换为数据库字段。"""
    if df.empty:
        return df

    col_map = {
        "代码": "stock_code",
        "名称": "stock_name",
        "公告日期": "notice_date",
        "调研日期": "survey_date",
        "调研机构": "institution_name",
        "机构类型": "institution_type",
        "接待方式": "survey_method",
        "接待地点": "survey_place",
        "调研人员": "investigators",
        "接待人员": "receptionists",
        "SECURITY_CODE": "stock_code",
        "SECURITY_NAME_ABBR": "stock_name",
        "NOTICE_DATE": "notice_date",
        "RECEIVE_START_DATE": "survey_date",
        "RECEIVE_OBJECT": "institution_name",
        "ORG_TYPE": "institution_type",
        "RECEIVE_WAY_EXPLAIN": "survey_method",
        "RECEIVE_PLACE": "survey_place",
        "INVESTIGATORS": "investigators",
        "RECEPTIONIST": "receptionists",
    }
    normalized = df.rename(
        columns={key: value for key, value in col_map.items() if key in df.columns}
    ).copy()

    for date_col in ["notice_date", "survey_date"]:
        if date_col in normalized.columns:
            normalized[date_col] = pd.to_datetime(
                normalized[date_col], errors="coerce"
            ).dt.strftime("%Y-%m-%d")

    keep_cols = [
        "stock_code",
        "stock_name",
        "survey_date",
        "notice_date",
        "institution_name",
        "institution_type",
        "survey_method",
        "survey_place",
        "investigators",
        "receptionists",
    ]
    for col in keep_cols:
        if col not in normalized.columns:
            normalized[col] = None

    normalized = normalized[keep_cols]
    normalized["stock_code"] = normalized["stock_code"].astype(str).str.zfill(6)
    normalized["institution_name"] = normalized["institution_name"].fillna("未披露")
    normalized["institution_type"] = normalized["institution_type"].fillna("未披露")
    normalized = normalized.dropna(subset=["stock_code", "survey_date"])
    return normalized


def ingest_institutional_research(
    stock_codes: Optional[list] = None,
    days_back: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> int:
    """采集调研记录，可选指定日期范围和股票代码过滤。"""
    today = datetime.now().date()
    historical_mode = start_date is not None
    code_set = None
    if stock_codes is not None:
        code_set = {str(code).zfill(6) for code in stock_codes}

    if start_date is not None:
        parsed_start = pd.to_datetime(start_date, format="%Y%m%d", errors="coerce")
        if pd.isna(parsed_start):
            raise ValueError("start_date must use YYYYMMDD format, for example 20260818")
        start_date = parsed_start.strftime("%Y%m%d")
        parsed_end = (
            pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")
            if end_date is not None
            else pd.Timestamp(today)
        )
        if pd.isna(parsed_end):
            raise ValueError("end_date must use YYYYMMDD format, for example 20260821")
        if parsed_end.date() <= parsed_start.date():
            raise ValueError("end_date must be later than start_date")
        end_date = parsed_end.strftime("%Y%m%d")
        logger.info(
            f"[Research] Historical mode: requesting records after {start_date} "
            f"through {end_date}; "
            "dates already present in the database will be skipped."
        )
    else:
        latest_sql = "SELECT MAX(survey_date) AS latest_date FROM institutional_research"
        latest_params = ()
        if code_set:
            placeholders = ",".join("?" for _ in code_set)
            latest_sql += f" WHERE stock_code IN ({placeholders})"
            latest_params = tuple(sorted(code_set))

        latest_rows = (
            query_sql(latest_sql, latest_params)
            if table_exists("institutional_research")
            else []
        )
        latest_value = latest_rows[0]["latest_date"] if latest_rows else None

        if latest_value:
            latest_date = pd.to_datetime(latest_value, errors="coerce").date()
            if latest_date >= today:
                logger.info(
                    f"[Research] Database is up to date through {latest_date}; "
                    "skipping remote request."
                )
                return 0
            start_date = latest_date.strftime("%Y%m%d")
            logger.info(
                f"[Research] Resuming after stored date {latest_date}; "
                f"requesting records newer than {start_date}."
            )
        else:
            start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
            logger.info(
                f"[Research] No stored records; fetching initial window since {start_date}."
            )

    def store_records(raw_df: pd.DataFrame, window_label: str) -> int:
        if raw_df.empty:
            logger.warning(f"[Research] No records returned for {window_label}.")
            return 0

        normalized = _normalize_research_df(raw_df)
        if code_set is not None:
            normalized = normalized[normalized["stock_code"].isin(code_set)]

        if normalized.empty:
            logger.info(f"[Research] No target-stock records for {window_label}.")
            return 0

        upsert_df(
            normalized,
            "institutional_research",
            ["stock_code", "survey_date", "notice_date", "institution_name"],
        )
        return len(normalized)

    total = 0
    if historical_mode:
        window_start = pd.to_datetime(start_date, format="%Y%m%d").date()
        historical_end = pd.to_datetime(end_date, format="%Y%m%d").date()
        while window_start < historical_end:
            query_date = window_start + timedelta(days=1)
            query_date_text = query_date.strftime("%Y%m%d")
            existing_rows = query_sql(
                """
                SELECT COUNT(*) AS row_count
                FROM institutional_research
                WHERE survey_date = ?
                """,
                (query_date.isoformat(),),
            ) if table_exists("institutional_research") else []
            if existing_rows and existing_rows[0]["row_count"] > 0:
                logger.info(
                    f"[Research] Date {query_date_text} already exists in database; "
                    "skipping remote request."
                )
                window_start = query_date
                continue

            logger.info(
                f"[Research] Fetching research records for {query_date_text}..."
            )
            window_df = fetch_institutional_research(
                window_start.strftime("%Y%m%d"),
                query_date_text,
            )
            total += store_records(
                window_df,
                query_date_text,
            )
            window_start = query_date
            time.sleep(max(REQUEST_DELAY, 1.0))
    else:
        logger.info(
            f"[Research] Fetching institutional research records after {start_date}..."
        )
        total = store_records(
            fetch_institutional_research(start_date),
            start_date,
        )

    logger.info(f"[Research] Ingestion completed. Total records: {total}")
    return total
