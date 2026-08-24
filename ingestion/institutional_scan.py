"""
全市场机构持仓扫描模块
逐股扫描全市场 A 股十大流通股东，识别机构持仓股票，写入 institutional_holdings 表。
用于扩展跟踪股票池：跟踪股票 = 指数成分股 ∪ 机构持仓股票。
- 有机构持仓的股票：holder_types 非空（纳入跟踪池）
- 无机构持仓的股票：holder_types 为 NULL（仅作扫描进度标记，支持断点续扫）
"""
import logging
from typing import Dict, List
import pandas as pd
import akshare as ak

from ingestion.base import safe_request
from ingestion.top_holders import fetch_top_holders_em
from ingestion.market_data import normalize_stock_name
from cleansing.holder_classifier import classify_holders_batch
from database.db_manager import query_sql, upsert_df, execute_sql

logger = logging.getLogger(__name__)


def get_all_a_stock_codes() -> List[Dict[str, str]]:
    """获取全市场 A 股列表（沪深），排除北交所与 ST/退市股。"""
    try:
        df = safe_request(ak.stock_info_a_code_name)
    except Exception as e:
        logger.error(f"[InstitutionalScan] Failed to fetch all A-share list: {e}")
        return []

    if df is None or df.empty:
        logger.error("[InstitutionalScan] All A-share list is empty.")
        return []

    # 兼容 akshare 不同版本的列名
    code_col = "code" if "code" in df.columns else "代码"
    name_col = "name" if "name" in df.columns else "名称"

    stocks: List[Dict[str, str]] = []
    for _, row in df.iterrows():
        code = str(row[code_col]).strip().zfill(6)
        name = str(row[name_col]).strip()
        # 排除北交所（4/8/92 开头）
        if code.startswith(("4", "8", "92")):
            continue
        # 排除 ST/退市（在去除临时前缀前判断，避免误删 ST 状态）
        if "ST" in name.upper() or "退" in name:
            continue
        stocks.append({"stock_code": code, "stock_name": normalize_stock_name(name)})
    return stocks


def scan_institutional_holdings(report_date: str = "20260630", resume: bool = False) -> Dict:
    """
    逐股扫描全市场十大流通股东，识别机构持仓股票并写入 institutional_holdings 表。
    report_date: 扫描报告期 YYYYMMDD（默认最新季报）
    resume: 断点续扫，跳过当天已扫描过的股票
    """
    stocks = get_all_a_stock_codes()
    if not stocks:
        logger.error("[InstitutionalScan] No A-share stocks fetched, abort.")
        return {}

    today = pd.Timestamp.now().strftime("%Y-%m-%d")

    # 断点续扫：跳过当天已扫描过的股票
    if resume:
        done = set(
            r["stock_code"]
            for r in query_sql(
                "SELECT stock_code FROM institutional_holdings WHERE last_scan_date = ?",
                (today,),
            )
        )
        stocks = [s for s in stocks if s["stock_code"] not in done]
        logger.info(
            f"[InstitutionalScan] Resume mode: skip {len(done)} already scanned "
            f"today, {len(stocks)} remaining."
        )
        if not stocks:
            logger.info("[InstitutionalScan] All stocks already scanned today.")
            return {}

    total = len(stocks)
    found = 0
    no_inst = 0
    no_data = 0
    errors = 0
    holder_type_counter: Dict[str, int] = {}
    db_report_date = pd.to_datetime(report_date, format="%Y%m%d").strftime("%Y-%m-%d")

    for i, stock in enumerate(stocks, 1):
        code = stock["stock_code"]
        try:
            df = fetch_top_holders_em(code, report_date, is_float=True)
            if df.empty:
                no_data += 1
                # 无数据也记录扫描进度，避免断点续扫重复请求
                execute_sql(
                    """INSERT OR REPLACE INTO institutional_holdings
                       (stock_code, stock_name, holder_types, report_date, last_scan_date)
                       VALUES (?, ?, NULL, NULL, ?)""",
                    (code, stock["stock_name"], today),
                )
                continue

            # 提取股东名称并分类，识别机构类型
            holder_names = []
            if "股东名称" in df.columns:
                holder_names = df["股东名称"].dropna().astype(str).tolist()
            if holder_names:
                tmp = pd.DataFrame({"holder_name": holder_names})
                tmp = classify_holders_batch(tmp, holder_col="holder_name", output_col="holder_type")
                types = sorted(set(t for t in tmp["holder_type"].tolist() if t != "其他"))
            else:
                types = []

            if types:
                found += 1
                holder_types_str = ",".join(types)
                for t in types:
                    holder_type_counter[t] = holder_type_counter.get(t, 0) + 1
                upsert_df(
                    pd.DataFrame([{
                        "stock_code": code,
                        "stock_name": stock["stock_name"],
                        "holder_types": holder_types_str,
                        "report_date": db_report_date,
                        "last_scan_date": today,
                    }]),
                    "institutional_holdings",
                    ["stock_code"],
                )
                # 同步到 stocks 表，保证后续股票名称可用
                execute_sql(
                    """INSERT INTO stocks (stock_code, stock_name) VALUES (?, ?)
                       ON CONFLICT(stock_code) DO UPDATE SET stock_name = excluded.stock_name""",
                    (code, normalize_stock_name(stock["stock_name"])),
                )
            else:
                no_inst += 1
                # 无机构持仓：仅记录扫描进度（holder_types 为 NULL，不纳入跟踪池）
                execute_sql(
                    """INSERT OR REPLACE INTO institutional_holdings
                       (stock_code, stock_name, holder_types, report_date, last_scan_date)
                       VALUES (?, ?, NULL, NULL, ?)""",
                    (code, stock["stock_name"], today),
                )
        except Exception as e:
            errors += 1
            logger.error(f"[InstitutionalScan] Error scanning {code} {stock['stock_name']}: {e}")
            continue

        if i % 500 == 0 or i == total:
            logger.info(
                f"[InstitutionalScan] Progress {i}/{total}: "
                f"institutional={found}, no_institutional={no_inst}, "
                f"no_data={no_data}, errors={errors}"
            )

    logger.info(
        f"[InstitutionalScan] Done: total={total}, institutional holdings={found}, "
        f"no_institutional={no_inst}, no_data={no_data}, errors={errors}"
    )
    if holder_type_counter:
        logger.info(f"[InstitutionalScan] Holder type distribution: {holder_type_counter}")
    return {
        "total": total,
        "found": found,
        "no_institutional": no_inst,
        "no_data": no_data,
        "errors": errors,
        "holder_types": holder_type_counter,
    }
