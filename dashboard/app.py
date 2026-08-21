"""
Streamlit 看板：A股大机构持仓跟踪
启动命令: streamlit run dashboard/app.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px

from database.db_manager import query_df, init_database
from reporting.quarterly_report import generate_quarterly_report
from config.settings import TRACKED_INDICES
from analysis.research_candidates import get_research_candidates

st.set_page_config(page_title="A股大机构持仓跟踪", layout="wide")

# 初始化数据库
init_database()

# ============================================================
# 页面标题
# ============================================================
st.title("📊 A股大机构持仓跟踪系统")
st.caption("跟踪国家队、保险、社保、QFII等在核心指数成分股中的持仓变化，并关注机构调研行为")

# ============================================================
# 侧边栏
# ============================================================
st.sidebar.header("🔍 筛选条件")

# 获取可用的报告期
dates_df = query_df("SELECT DISTINCT report_date FROM holding_changes_summary ORDER BY report_date DESC")
available_dates = dates_df["report_date"].tolist() if not dates_df.empty else []

if available_dates:
    selected_date = st.sidebar.selectbox("选择报告期", available_dates)
else:
    selected_date = None
    st.sidebar.info("暂无报告期数据，请先运行数据采集。")

selected_holder_type = st.sidebar.multiselect(
    "机构类型",
    ["证金公司", "汇金公司", "证金资管计划", "保险", "社保基金", "QFII", "北向资金", "券商", "信托"],
    default=["证金公司", "汇金公司", "保险", "社保基金", "QFII"]
)

selected_index = st.sidebar.selectbox(
    "所属指数",
    ["全部"] + list(TRACKED_INDICES.keys())
)

# ============================================================
# 主页面 - Tab 布局
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 持仓总览", "🔎 个股查询", "🏛️ 国家队", "🔬 机构调研", "⚠️ 预警"
])

# ---------- Tab 1: 持仓总览 ----------
with tab1:
    if selected_date:
        st.subheader(f"{selected_date} 机构持仓总览")
        
        col1, col2, col3 = st.columns(3)
        
        sql = """
            SELECT holder_type, SUM(total_market_value) as mv, SUM(change_market_value) as chg
            FROM holding_changes_summary
            WHERE report_date = ? AND holder_type IN ({placeholders})
            GROUP BY holder_type
            ORDER BY mv DESC
        """
        placeholders = ",".join(["?"] * len(selected_holder_type)) if selected_holder_type else "''"
        sql = sql.format(placeholders=placeholders)
        params = (selected_date,) + tuple(selected_holder_type)
        df = query_df(sql, params)
        
        if not df.empty:
            total_mv = df["mv"].sum()
            total_chg = df["chg"].sum()
            
            col1.metric("机构合计持仓市值", f"¥{total_mv/1e8:.1f}亿", f"{total_chg/1e8:+.1f}亿")
            col2.metric("监控机构类型数", f"{df['holder_type'].nunique()} 类")
            col3.metric("覆盖股票数", f"{df.shape[0]} 只")
            
            df["mv_亿"] = df["mv"] / 1e8
            df["chg_亿"] = df["chg"] / 1e8
            fig = px.bar(df, x="holder_type", y="mv_亿", color="chg_亿",
                        title="各机构类型持仓市值（亿元）",
                        labels={"holder_type": "机构类型", "mv_亿": "持仓市值（亿）", "chg_亿": "变动（亿）"},
                        color_continuous_scale="RdYlGn")
            st.plotly_chart(fig, width='stretch')
            
            st.dataframe(df[["holder_type", "mv_亿", "chg_亿"]].rename(
                columns={"mv_亿": "持仓市值(亿)", "chg_亿": "变动市值(亿)"}
            ), width='stretch')
        else:
            st.info("该报告期暂无数据。")
    else:
        st.info("请先在侧边栏选择报告期，或运行数据采集流程。")

# ---------- Tab 2: 个股查询 ----------
with tab2:
    st.subheader("🔎 个股机构持仓查询")
    
    stock_code = st.text_input("输入股票代码", placeholder="例如: 600519")
    
    if stock_code:
        sql = """
            SELECT report_date, holder_type, total_market_value, change_market_value, change_status
            FROM holding_changes_summary
            WHERE stock_code = ? AND holder_type IN ({placeholders})
            ORDER BY report_date DESC
        """
        placeholders = ",".join(["?"] * len(selected_holder_type)) if selected_holder_type else "''"
        sql = sql.format(placeholders=placeholders)
        params = (stock_code,) + tuple(selected_holder_type)
        df_stock = query_df(sql, params)
        
        if not df_stock.empty:
            df_stock["mv_亿"] = df_stock["total_market_value"] / 1e8
            fig = px.line(df_stock, x="report_date", y="mv_亿", color="holder_type",
                         title=f"{stock_code} 机构持仓市值变化趋势",
                         markers=True)
            st.plotly_chart(fig, width='stretch')
            
            st.dataframe(df_stock, width='stretch')
        else:
            st.info(f"未找到 {stock_code} 的机构持仓数据。")

# ---------- Tab 3: 国家队 ----------
with tab3:
    st.subheader("🏛️ 国家队持仓监控")
    
    if selected_date:
        sql = """
            SELECT stock_code, stock_name, holder_type, 
                   total_market_value, change_market_value, change_status
            FROM holding_changes_summary
            WHERE report_date = ?
              AND holder_type IN ('证金公司', '汇金公司', '证金资管计划')
            ORDER BY ABS(change_market_value) DESC
            LIMIT 50
        """
        df_gjd = query_df(sql, (selected_date,))
        
        if not df_gjd.empty:
            df_gjd["mv_亿"] = df_gjd["total_market_value"] / 1e8
            df_gjd["chg_亿"] = df_gjd["change_market_value"] / 1e8
            
            # 柱状图（过滤空 stock_name）
            df_plot = df_gjd[df_gjd["stock_name"].notna() & (df_gjd["stock_name"] != "")].copy()
            if not df_plot.empty:
                fig = px.bar(df_plot, x="stock_name", y="mv_亿", color="holder_type",
                            title="国家队持仓市值 Top 个股（亿元）",
                            labels={"stock_name": "股票", "mv_亿": "持仓市值(亿)", "holder_type": "机构类型"},
                            barmode="group")
                st.plotly_chart(fig, width='stretch')
            else:
                st.info("暂无国家队持仓个股数据。")
            
            st.dataframe(df_gjd[["stock_code", "stock_name", "holder_type", "mv_亿", "chg_亿", "change_status"]],
                        width='stretch')
        else:
            st.info("暂无国家队持仓数据。")

# ---------- Tab 4: 机构调研 ----------
with tab4:
    st.subheader("🔬 机构调研记录")
    st.caption("调研活跃度仅反映公开调研行为，不代表实际持仓或投资建议。")
    
    latest_sql = "SELECT MAX(survey_date) as max_date FROM institutional_research"
    latest = query_df(latest_sql)
    latest_date = latest.iloc[0]["max_date"] if not latest.empty else None
    
    if latest_date:
        st.caption(f"数据截至: {latest_date}")
        
        sql = """
            SELECT stock_code, stock_name, COUNT(*) AS survey_count,
                   COUNT(DISTINCT institution_name) AS institution_count,
                   MAX(survey_date) AS latest_survey_date
            FROM institutional_research
            GROUP BY stock_code, stock_name
            ORDER BY survey_count DESC, institution_count DESC
            LIMIT 100
        """
        df_nb = query_df(sql)
        
        if not df_nb.empty:
            fig = px.bar(df_nb.head(30), x="stock_name", y="survey_count",
                        color="institution_count",
                        title="机构调研次数 Top 30",
                        labels={"stock_name": "股票", "survey_count": "调研次数",
                                "institution_count": "机构数量"})
            st.plotly_chart(fig, width='stretch')
            
            st.dataframe(df_nb, width='stretch')
        else:
            st.info("暂无机构调研数据。")
    else:
        st.info("暂无机构调研数据，请先运行采集。")

    st.subheader("🌟 非成分股调研活跃候选")
    candidate_df = get_research_candidates(days=30)
    if not candidate_df.empty:
        st.dataframe(candidate_df.head(50), width="stretch")
    else:
        st.info("近30天暂无满足条件的非成分股调研候选。")

# ---------- Tab 5: 预警 ----------
with tab5:
    st.subheader("⚠️ 预警清单")
    
    sql = """
        SELECT alert_time, alert_level, alert_type, stock_code, stock_name, holder_type, message
        FROM alerts
        ORDER BY alert_time DESC
        LIMIT 100
    """
    df_alert = query_df(sql)
    
    if not df_alert.empty:
        def highlight_level(val):
            if val == "紧急":
                return "background-color: #ffcccc"
            elif val == "重要":
                return "background-color: #ffe6cc"
            return ""
        
        styled = df_alert.style.applymap(highlight_level, subset=["alert_level"])
        st.dataframe(styled, width='stretch')
    else:
        st.info("暂无预警记录。")

# ============================================================
# 底部：生成报告按钮
# ============================================================
st.sidebar.markdown("---")
if st.sidebar.button("📝 生成季度报告"):
    if selected_date:
        with st.spinner("正在生成报告..."):
            report = generate_quarterly_report(selected_date)
            st.sidebar.success("报告已生成！")
            st.sidebar.download_button(
                label="下载 Markdown 报告",
                data=report,
                file_name=f"持仓报告_{selected_date}.md",
                mime="text/markdown"
            )
    else:
        st.sidebar.warning("请先选择报告期")
