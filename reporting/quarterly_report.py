"""
季度报告生成模块
生成 Markdown 格式的持仓变化分析报告
"""
import logging
from datetime import datetime
from typing import Optional
import pandas as pd

from database.db_manager import query_df
from config.settings import TRACKED_INDICES

logger = logging.getLogger(__name__)


def _format_billion(val: float) -> str:
    """将数值格式化为亿元"""
    if pd.isna(val):
        return "N/A"
    return f"{val / 1e8:.2f} 亿"


def _format_million(val: float) -> str:
    """将数值格式化为万元"""
    if pd.isna(val):
        return "N/A"
    return f"{val / 1e4:.2f} 万"


def generate_quarterly_report(report_date: str, output_path: Optional[str] = None) -> str:
    """
    生成指定报告期的季度持仓变化分析报告
    返回 Markdown 文本
    """
    logger.info(f"[Report] Generating report for {report_date}...")
    
    lines = []
    lines.append(f"# A股大机构持仓变化季度报告")
    lines.append(f"\n**报告期：** {report_date}")
    lines.append(f"**生成时间：** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("\n---\n")
    
    # 1. 各类型机构持仓市值汇总
    lines.append("## 一、机构持仓总览\n")
    sql = """
        SELECT holder_type, 
               SUM(total_market_value) as total_mv,
               SUM(change_market_value) as change_mv
        FROM holding_changes_summary
        WHERE report_date = ?
        GROUP BY holder_type
        ORDER BY total_mv DESC
    """
    df_overview = query_df(sql, (report_date,))
    if not df_overview.empty:
        lines.append("| 机构类型 | 持仓市值（亿元） | 变动市值（亿元） |")
        lines.append("|---|---|---|")
        for _, row in df_overview.iterrows():
            mv = _format_billion(row["total_mv"])
            chg = _format_billion(row["change_mv"])
            lines.append(f"| {row['holder_type']} | {mv} | {chg} |")
    else:
        lines.append("> 暂无数据。\n")
    
    lines.append("\n---\n")
    
    # 2. 国家队持仓变化（证金+汇金+证金资管）
    lines.append("## 二、国家队持仓变化\n")
    sql = """
        SELECT stock_code, stock_name, holder_type,
               total_market_value, change_market_value, change_status
        FROM holding_changes_summary
        WHERE report_date = ?
          AND holder_type IN ('证金公司', '汇金公司', '证金资管计划')
        ORDER BY ABS(change_market_value) DESC
        LIMIT 20
    """
    df_gjd = query_df(sql, (report_date,))
    if not df_gjd.empty:
        lines.append("### 持仓变动 Top 20\n")
        lines.append("| 股票代码 | 股票名称 | 机构类型 | 持股市值 | 变动市值 | 变动状态 |")
        lines.append("|---|---|---|---|---|---|")
        for _, row in df_gjd.iterrows():
            mv = _format_billion(row["total_market_value"])
            chg = _format_billion(row["change_market_value"])
            lines.append(f"| {row['stock_code']} | {row['stock_name']} | {row['holder_type']} | {mv} | {chg} | {row['change_status']} |")
    else:
        lines.append("> 暂无国家队持仓数据。\n")
    
    lines.append("\n---\n")
    
    # 3. 保险资金持仓变化
    lines.append("## 三、保险资金持仓变化\n")
    sql = """
        SELECT stock_code, stock_name,
               SUM(total_market_value) as mv, SUM(change_market_value) as chg_mv
        FROM holding_changes_summary
        WHERE report_date = ? AND holder_type = '保险'
        GROUP BY stock_code, stock_name
        ORDER BY ABS(SUM(change_market_value)) DESC
        LIMIT 20
    """
    df_ins = query_df(sql, (report_date,))
    if not df_ins.empty:
        lines.append("| 股票代码 | 股票名称 | 持股市值 | 变动市值 |")
        lines.append("|---|---|---|---|")
        for _, row in df_ins.iterrows():
            mv = _format_billion(row["mv"])
            chg = _format_billion(row["chg_mv"])
            lines.append(f"| {row['stock_code']} | {row['stock_name']} | {mv} | {chg} |")
    else:
        lines.append("> 暂无保险资金数据。\n")
    
    lines.append("\n---\n")
    
    # 4. 社保基金持仓变化
    lines.append("## 四、社保基金持仓变化\n")
    sql = """
        SELECT stock_code, stock_name,
               SUM(total_market_value) as mv, SUM(change_market_value) as chg_mv
        FROM holding_changes_summary
        WHERE report_date = ? AND holder_type = '社保基金'
        GROUP BY stock_code, stock_name
        ORDER BY ABS(SUM(change_market_value)) DESC
        LIMIT 20
    """
    df_ss = query_df(sql, (report_date,))
    if not df_ss.empty:
        lines.append("| 股票代码 | 股票名称 | 持股市值 | 变动市值 |")
        lines.append("|---|---|---|---|")
        for _, row in df_ss.iterrows():
            mv = _format_billion(row["mv"])
            chg = _format_billion(row["chg_mv"])
            lines.append(f"| {row['stock_code']} | {row['stock_name']} | {mv} | {chg} |")
    else:
        lines.append("> 暂无社保基金数据。\n")
    
    lines.append("\n---\n")
    
    # 5. QFII 持仓变化
    lines.append("## 五、QFII 持仓变化\n")
    sql = """
        SELECT stock_code, stock_name,
               SUM(total_market_value) as mv, SUM(change_market_value) as chg_mv
        FROM holding_changes_summary
        WHERE report_date = ? AND holder_type = 'QFII'
        GROUP BY stock_code, stock_name
        ORDER BY ABS(SUM(change_market_value)) DESC
        LIMIT 20
    """
    df_qfii = query_df(sql, (report_date,))
    if not df_qfii.empty:
        lines.append("| 股票代码 | 股票名称 | 持股市值 | 变动市值 |")
        lines.append("|---|---|---|---|")
        for _, row in df_qfii.iterrows():
            mv = _format_billion(row["mv"])
            chg = _format_billion(row["chg_mv"])
            lines.append(f"| {row['stock_code']} | {row['stock_name']} | {mv} | {chg} |")
    else:
        lines.append("> 暂无 QFII 数据。\n")
    
    lines.append("\n---\n")
    
    # 6. 北向资金（日度最新）
    lines.append("## 六、北向资金最新持股\n")
    sql = """
        SELECT stock_code, stock_name, trade_date, 
               hold_market_value, hold_ratio, net_buy_shares
        FROM northbound_holdings
        WHERE trade_date = (SELECT MAX(trade_date) FROM northbound_holdings)
        ORDER BY hold_market_value DESC
        LIMIT 20
    """
    df_nb = query_df(sql)
    if not df_nb.empty:
        latest_date = df_nb.iloc[0]["trade_date"]
        lines.append(f"*数据截至：{latest_date}*\n")
        lines.append("| 股票代码 | 股票名称 | 持股市值 | 占流通比 | 当日净买入 |")
        lines.append("|---|---|---|---|---|")
        for _, row in df_nb.iterrows():
            mv = _format_billion(row["hold_market_value"])
            ratio = f"{row['hold_ratio']:.2f}%" if pd.notna(row["hold_ratio"]) else "N/A"
            net = _format_million(row["net_buy_shares"] * 1)  # 简化，实际应乘股价
            lines.append(f"| {row['stock_code']} | {row['stock_name']} | {mv} | {ratio} | {net} |")
    else:
        lines.append("> 暂无北向资金数据。\n")
    
    lines.append("\n---\n")
    
    # 7. 指数层面汇总
    lines.append("## 七、指数层面机构持仓汇总\n")
    sql = """
        SELECT index_name, holder_type, stock_count, 
               total_market_value, total_change_value
        FROM index_holding_summary
        WHERE report_date = ?
        ORDER BY total_market_value DESC
    """
    df_idx = query_df(sql, (report_date,))
    if not df_idx.empty:
        lines.append("| 指数 | 机构类型 | 持仓股票数 | 持仓市值 | 变动市值 |")
        lines.append("|---|---|---|---|---|")
        for _, row in df_idx.iterrows():
            mv = _format_billion(row["total_market_value"])
            chg = _format_billion(row["total_change_value"])
            lines.append(f"| {row['index_name']} | {row['holder_type']} | {row['stock_count']} | {mv} | {chg} |")
    else:
        lines.append("> 暂无指数汇总数据。\n")
    
    lines.append("\n---\n")
    
    # 8. 风险提示
    lines.append("## 八、风险提示\n")
    lines.append(
        "> **免责声明**：本报告仅基于公开数据整理，不构成任何投资建议。"
        "十大股东数据存在披露不完整、滞后等固有限制，请结合其他信息综合判断。\n"
    )
    
    report_md = "\n".join(lines)
    
    # 保存到文件
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_md)
        logger.info(f"[Report] Saved to {output_path}")
    
    return report_md
