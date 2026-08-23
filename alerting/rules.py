"""
预警规则模块
"""
import logging
from typing import List, Dict
import pandas as pd
import numpy as np

from database.db_manager import query_df, execute_sql, normalize_report_date
from config.settings import ALERT_THRESHOLDS

logger = logging.getLogger(__name__)


def check_single_stock_alert(report_date: str) -> List[Dict]:
    """
    检查单只股票单机构变动比例是否超过阈值
    """
    threshold = ALERT_THRESHOLDS["single_holder_change_ratio"]
    sql = """
        SELECT stock_code, stock_name, holder_type, 
               total_market_value, change_market_value, change_status
        FROM holding_changes_summary
        WHERE report_date = ?
          AND ABS(change_market_value) > total_market_value * ?
          AND total_market_value > 0
    """
    df = query_df(sql, (report_date, threshold))
    
    alerts = []
    for _, row in df.iterrows():
        alerts.append({
            "alert_type": "单股单机构大幅变动",
            "alert_level": "重要",
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "holder_type": row["holder_type"],
            "report_date": report_date,
            "message": (f"{row['stock_name']}({row['stock_code']}) 的 {row['holder_type']} "
                       f"本季{row['change_status']}，变动市值占比超过 {threshold*100:.0f}%"),
        })
    return alerts


def check_national_team_new_exit(report_date: str) -> List[Dict]:
    """
    检查国家队（证金/汇金/证金资管）的新进/退出
    """
    sql = """
        SELECT stock_code, stock_name, holder_type, change_status, change_market_value
        FROM holding_changes_summary
        WHERE report_date = ?
          AND holder_type IN ('证金公司', '汇金公司', '证金资管计划')
          AND change_status IN ('新进', '退出')
    """
    df = query_df(sql, (report_date,))
    
    alerts = []
    for _, row in df.iterrows():
        level = "紧急" if row["change_status"] == "退出" else "重要"
        alerts.append({
            "alert_type": "国家队新进/退出",
            "alert_level": level,
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "holder_type": row["holder_type"],
            "report_date": report_date,
            "message": (f"【{level}】{row['stock_name']}({row['stock_code']}) "
                       f"被 {row['holder_type']} {row['change_status']}"),
        })
    return alerts


def check_index_level_change(report_date: str) -> List[Dict]:
    """
    检查某指数内某类机构合计持仓市值变动是否超过阈值
    """
    threshold = ALERT_THRESHOLDS["index_holder_change_value_billion"] * 1e8  # 转为元
    sql = """
        SELECT index_name, holder_type, total_change_value
        FROM index_holding_summary
        WHERE report_date = ?
          AND ABS(total_change_value) > ?
    """
    df = query_df(sql, (report_date, threshold))
    
    alerts = []
    for _, row in df.iterrows():
        direction = "增持" if row["total_change_value"] > 0 else "减持"
        amount_b = abs(row["total_change_value"]) / 1e8
        alerts.append({
            "alert_type": "指数层面机构大幅调仓",
            "alert_level": "重要",
            "stock_code": None,
            "stock_name": row["index_name"],
            "holder_type": row["holder_type"],
            "report_date": report_date,
            "message": (f"{row['index_name']} 的 {row['holder_type']} 合计{direction} {amount_b:.1f} 亿元"),
        })
    return alerts


def check_consecutive_changes(report_date: str, n_quarters: int = 2) -> List[Dict]:
    """
    检查连续多季同向增持/减持
    """
    sql = """
        SELECT stock_code, stock_name, holder_type, change_status, report_date
        FROM holding_changes_summary
        WHERE report_date <= ?
        ORDER BY stock_code, holder_type, report_date DESC
    """
    df = query_df(sql, (report_date,))
    if df.empty:
        return []
    
    alerts = []
    # 按 (stock_code, holder_type) 分组，检查最近 n 期是否同向
    grouped = df.groupby(["stock_code", "holder_type"])
    
    for (scode, htype), group in grouped:
        group = group.sort_values("report_date", ascending=False)
        recent = group.head(n_quarters)
        
        if len(recent) < n_quarters:
            continue
        
        statuses = recent["change_status"].tolist()
        if all(s == "增持" for s in statuses):
            alerts.append({
                "alert_type": "连续增持",
                "alert_level": "普通",
                "stock_code": scode,
                "stock_name": recent.iloc[0]["stock_name"],
                "holder_type": htype,
                "report_date": recent.iloc[0]["report_date"],
                "message": (f"{recent.iloc[0]['stock_name']}({scode}) 被 {htype} "
                           f"连续 {n_quarters} 个季度增持"),
            })
        elif all(s == "减持" for s in statuses):
            alerts.append({
                "alert_type": "连续减持",
                "alert_level": "普通",
                "stock_code": scode,
                "stock_name": recent.iloc[0]["stock_name"],
                "holder_type": htype,
                "report_date": recent.iloc[0]["report_date"],
                "message": (f"{recent.iloc[0]['stock_name']}({scode}) 被 {htype} "
                           f"连续 {n_quarters} 个季度减持"),
            })
    
    return alerts


def repair_null_stock_names() -> int:
    """
    修复 alerts 表中 stock_name 为 NULL 的记录，
    从 holding_changes_summary 中补充正确的股票名称。
    同时修复 report_date 为 NULL 的记录。
    """
    sql = """
        UPDATE alerts
        SET stock_name = COALESCE(stock_name, (
                SELECT hcs.stock_name
                FROM holding_changes_summary hcs
                WHERE hcs.stock_code = alerts.stock_code
                  AND hcs.holder_type = alerts.holder_type
                  AND hcs.stock_name IS NOT NULL
                LIMIT 1
            )),
            report_date = COALESCE(report_date, (
                SELECT hcs.report_date
                FROM holding_changes_summary hcs
                WHERE hcs.stock_code = alerts.stock_code
                  AND hcs.holder_type = alerts.holder_type
                LIMIT 1
            ))
        WHERE alerts.stock_code IS NOT NULL
          AND (alerts.stock_name IS NULL OR alerts.report_date IS NULL)
    """
    count = execute_sql(sql)
    if count > 0:
        logger.info(f"[Alert] Repaired {count} alerts with NULL stock_name or report_date.")
    return count


def repair_alert_times() -> int:
    """
    修复已有预警记录的 alert_time：
    使用 alerts 表自身的 report_date 列更新 alert_time。
    仅修复 alert_time 不是日期格式（即旧版 CURRENT_TIMESTAMP）的记录。
    """
    sql = """
        UPDATE alerts
        SET alert_time = report_date
        WHERE report_date IS NOT NULL
          AND alert_time NOT LIKE '____-__-__'
    """
    count = execute_sql(sql)
    if count > 0:
        logger.info(f"[Alert] Repaired {count} alerts' alert_time from report_date.")
    return count


def run_all_alerts(report_dates: list) -> List[Dict]:
    """
    对所有报告期运行预警规则，返回预警列表并写入数据库。
    优化：只处理 alerts 表中缺失的报告期；内存集合去重替代逐条 SELECT；executemany 批量插入。
    report_dates: 报告期列表，兼容 YYYYMMDD / YYYY-MM-DD，内部统一为 YYYY-MM-DD
    """
    # 入参归一化（兼容 YYYYMMDD / YYYY-MM-DD），避免格式混用导致 alerts 期数误判
    report_dates = [normalize_report_date(rd) for rd in report_dates]

    # 先修复历史 NULL stock_name / report_date
    repair_null_stock_names()
    # 修复历史 alert_time 为公告日期
    repair_alert_times()

    if not report_dates:
        logger.warning("[Alert] No report dates to process.")
        return []

    # 只处理缺失报告期：alerts 表已有预警的报告期直接跳过
    existing_dates = set(
        str(r["report_date"]) for r in query_df(
            "SELECT DISTINCT report_date FROM alerts WHERE report_date IS NOT NULL"
        ).to_dict("records")
    )
    missing_dates = [str(rd) for rd in report_dates if str(rd) not in existing_dates]
    if len(missing_dates) < len(report_dates):
        logger.info(f"[Alert] Skip report dates with existing alerts: {sorted(set(str(d) for d in report_dates) - set(missing_dates))}")
    if not missing_dates:
        logger.info("[Alert] All report dates already have alerts, nothing to generate.")
        return []

    all_alerts = []
    for rd in missing_dates:
        logger.info(f"[Alert] Running alert checks for {rd}...")
        all_alerts.extend(check_single_stock_alert(rd))
        all_alerts.extend(check_national_team_new_exit(rd))
        all_alerts.extend(check_index_level_change(rd))
        all_alerts.extend(check_consecutive_changes(rd))

    # 一次性加载已有去重键到内存 set（None 直接参与元组比较）
    existing_keys = set()
    for row in query_df(
        "SELECT report_date, alert_type, stock_code, holder_type, message FROM alerts"
    ).to_dict("records"):
        existing_keys.add((
            row["report_date"], row["alert_type"],
            row["stock_code"], row["holder_type"], row["message"],
        ))

    # 批量插入新预警（单连接 + executemany）
    from database.db_manager import get_connection
    insert_sql = """
        INSERT INTO alerts (alert_time, report_date, alert_type, alert_level, stock_code, stock_name, holder_type, message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    inserted = 0
    skipped = 0
    rows = []
    for alert in all_alerts:
        key = (
            alert.get("report_date"), alert["alert_type"],
            alert.get("stock_code"), alert.get("holder_type"), alert["message"],
        )
        if key in existing_keys:
            skipped += 1
            continue
        existing_keys.add(key)
        rows.append((
            alert.get("report_date"), alert.get("report_date"),
            alert["alert_type"], alert["alert_level"],
            alert.get("stock_code"), alert.get("stock_name"),
            alert.get("holder_type"), alert["message"],
        ))

    if rows:
        with get_connection() as conn:
            conn.executemany(insert_sql, rows)
        inserted = len(rows)

    logger.info(f"[Alert] Total alerts generated: {len(all_alerts)}, inserted: {inserted}, skipped (duplicate): {skipped}")
    return all_alerts
