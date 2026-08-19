"""
预警规则模块
"""
import logging
from typing import List, Dict
import pandas as pd
import numpy as np

from database.db_manager import query_df, execute_sql
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
                "message": (f"{recent.iloc[0]['stock_name']}({scode}) 被 {htype} "
                           f"连续 {n_quarters} 个季度减持"),
            })
    
    return alerts


def run_all_alerts(report_date: str) -> List[Dict]:
    """
    运行所有预警规则，返回预警列表并写入数据库
    """
    logger.info(f"[Alert] Running all alert checks for {report_date}...")
    
    all_alerts = []
    all_alerts.extend(check_single_stock_alert(report_date))
    all_alerts.extend(check_national_team_new_exit(report_date))
    all_alerts.extend(check_index_level_change(report_date))
    all_alerts.extend(check_consecutive_changes(report_date))
    
    # 写入数据库
    for alert in all_alerts:
        sql = """
            INSERT INTO alerts (alert_type, alert_level, stock_code, stock_name, holder_type, message)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        execute_sql(sql, (
            alert["alert_type"], alert["alert_level"],
            alert.get("stock_code"), alert.get("stock_name"),
            alert.get("holder_type"), alert["message"]
        ))
    
    logger.info(f"[Alert] Total alerts generated: {len(all_alerts)}")
    return all_alerts
