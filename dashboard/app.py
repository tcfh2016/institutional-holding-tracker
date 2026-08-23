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
from pypinyin import lazy_pinyin

from database.db_manager import query_df, init_database
from reporting.quarterly_report import generate_quarterly_report
from analysis.research_candidates import get_research_candidates

st.set_page_config(page_title="A股大机构持仓跟踪", layout="wide")

# 初始化数据库
init_database()

# ============================================================
# 页面标题
# ============================================================
st.title("📊 A股大机构持仓跟踪系统")
st.caption("跟踪国家队、保险、社保、QFII等在核心指数成分股及机构持仓个股中的持仓变化，并关注机构调研行为")

# ============================================================
# 全局数据：可用报告期（持仓总览选择器与国家队默认值来源）
# ============================================================
dates_df = query_df("SELECT DISTINCT report_date FROM holding_changes_summary ORDER BY report_date DESC")
available_dates = dates_df["report_date"].tolist() if not dates_df.empty else []

# ============================================================
# 主页面 - Tab 布局
# ============================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 持仓总览", "🔎 个股查询", "🏛️ 国家队", "🔬 机构调研", "⚠️ 预警"
])

# ---------- Tab 1: 持仓总览 ----------
with tab1:
    # 报告期选择器与生成报告按钮（原侧边栏控件移入页面）
    if available_dates:
        title_col, date_col, btn_col = st.columns([3, 1, 1])
        with title_col:
            st.subheader("📊 机构持仓总览")
        with date_col:
            selected_date = st.selectbox("选择报告期", available_dates)
        with btn_col:
            st.write("")
            report_clicked = st.button("📝 生成季度报告")
    else:
        selected_date = None
        report_clicked = False
        st.info("暂无报告期数据，请先运行数据采集。")

    if selected_date:
        st.caption(f"当前报告期：{selected_date}，展示全部机构类型汇总")

        if report_clicked:
            with st.spinner("正在生成报告..."):
                report = generate_quarterly_report(selected_date)
            st.success("报告已生成！")
            st.download_button(
                label="下载 Markdown 报告",
                data=report,
                file_name=f"持仓报告_{selected_date}.md",
                mime="text/markdown"
            )

        col1, col2, col3 = st.columns(3)

        sql = """
            SELECT holder_type, SUM(total_market_value) as mv, SUM(change_market_value) as chg
            FROM holding_changes_summary
            WHERE report_date = ?
            GROUP BY holder_type
            ORDER BY mv DESC
        """
        df = query_df(sql, (selected_date,))
        
        if not df.empty:
            total_mv = df["mv"].sum()
            total_chg = df["chg"].sum()
            
            col1.metric("机构合计持仓市值", f"¥{total_mv/1e8:.1f}亿", f"{total_chg/1e8:+.1f}亿")
            col2.metric("监控机构类型数", f"{df['holder_type'].nunique()} 类")
            
            # 覆盖股票数应统计持仓记录中的不同股票数量
            stock_sql = """
                SELECT COUNT(DISTINCT stock_code) as stock_count
                FROM holding_changes_summary
                WHERE report_date = ?
            """
            stock_count = query_df(stock_sql, (selected_date,))["stock_count"].iloc[0] or 0
            col3.metric("覆盖股票数", f"{stock_count} 只")
            
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
            
            # ============================================================
            # 股票持仓明细区块
            # ============================================================
            st.markdown("---")
            st.subheader("📋 股票持仓明细")
            
            # 按股票汇总查询：一次取回全部股票，避免循环查询
            stock_sql = """
                SELECT stock_code, stock_name,
                       SUM(total_market_value) AS total_mv,
                       SUM(change_market_value) AS change_mv,
                       SUM(total_hold_shares)   AS total_shares
                FROM holding_changes_summary
                WHERE report_date = ?
                GROUP BY stock_code, stock_name
            """
            stock_df = query_df(stock_sql, (selected_date,))
            
            if not stock_df.empty:
                # 无上期数据的变动市值补 0，避免排序与绘图出错
                stock_df["change_mv"] = stock_df["change_mv"].fillna(0)
                stock_df["total_shares"] = stock_df["total_shares"].fillna(0)
                
                # 市值换算为亿元
                stock_df["total_mv_亿"] = stock_df["total_mv"] / 1e8
                stock_df["change_mv_亿"] = stock_df["change_mv"] / 1e8
                
                # ---- Top10 持仓市值图 ----
                # 固定按持仓市值取 Top10，不受排序切换影响
                top10_mv = stock_df.sort_values("total_mv", ascending=False).head(10)
                fig_mv = px.bar(
                    top10_mv,
                    x="total_mv_亿",
                    y="stock_name",
                    orientation="h",
                    title="Top10 持仓市值（亿元）",
                    labels={"total_mv_亿": "持仓市值（亿）", "stock_name": "股票"},
                    color="total_mv_亿",
                    color_continuous_scale="Blues",
                    text="total_mv_亿",
                )
                fig_mv.update_traces(texttemplate="%{text:.1f}", textposition="outside")
                fig_mv.update_layout(coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig_mv, width='stretch')
                
                # ---- Top10 增持/减持双图 ----
                add_df = stock_df[stock_df["change_mv"] > 0].nlargest(10, "change_mv")
                reduce_df = stock_df[stock_df["change_mv"] < 0].nsmallest(10, "change_mv")
                
                col_a, col_b = st.columns(2)
                with col_a:
                    if not add_df.empty:
                        fig_add = px.bar(
                            add_df,
                            x="change_mv_亿",
                            y="stock_name",
                            orientation="h",
                            title="增持 Top10（亿元）",
                            labels={"change_mv_亿": "变动市值（亿）", "stock_name": "股票"},
                            color="change_mv_亿",
                            color_continuous_scale="Greens",
                            text="change_mv_亿",
                        )
                        fig_add.update_traces(texttemplate="%{text:.2f}", textposition="outside")
                        fig_add.update_layout(coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
                        st.plotly_chart(fig_add, width='stretch')
                    else:
                        st.info("暂无增持数据")
                
                with col_b:
                    if not reduce_df.empty:
                        fig_red = px.bar(
                            reduce_df.sort_values("change_mv", ascending=False),
                            x="change_mv_亿",
                            y="stock_name",
                            orientation="h",
                            title="减持 Top10（亿元）",
                            labels={"change_mv_亿": "变动市值（亿）", "stock_name": "股票"},
                            color="change_mv_亿",
                            color_continuous_scale="Reds",
                            text="change_mv_亿",
                        )
                        fig_red.update_traces(texttemplate="%{text:.2f}", textposition="outside")
                        fig_red.update_layout(coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
                        st.plotly_chart(fig_red, width='stretch')
                    else:
                        st.info("暂无减持数据")
                
                # ---- 全量明细表格 ----
                st.markdown("#### 全部股票明细")
                
                # 表格排序切换控件，仅控制下方明细表格
                sort_option = st.radio(
                    "表格排序方式",
                    ["持仓市值", "变动市值", "股票代码"],
                    horizontal=True,
                )
                if sort_option == "持仓市值":
                    stock_sorted = stock_df.sort_values("total_mv", ascending=False)
                elif sort_option == "变动市值":
                    stock_sorted = stock_df.sort_values("change_mv", ascending=False)
                else:
                    stock_sorted = stock_df.sort_values("stock_code")
                
                table_df = stock_sorted[["stock_code", "stock_name", "total_mv_亿", "change_mv_亿", "total_shares"]].copy()
                table_df["持股数量(亿股)"] = (table_df["total_shares"] / 1e8).round(2)
                table_df = table_df.drop(columns=["total_shares"]).rename(columns={
                    "stock_code": "股票代码",
                    "stock_name": "股票名称",
                    "total_mv_亿": "持仓市值(亿)",
                    "change_mv_亿": "变动市值(亿)",
                })
                st.dataframe(table_df, width='stretch')
            else:
                st.info("该报告期暂无股票明细数据。")
        else:
            st.info("该报告期暂无数据。")
    else:
        st.info("请先在侧边栏选择报告期，或运行数据采集流程。")

# ---------- Tab 2: 个股查询 ----------
with tab2:
    st.subheader("🔎 个股机构持仓查询")
    
    # ---- 股票搜索补全组件 ----
    # 获取所有股票列表（代码 + 名称）
    @st.cache_data(ttl=300)
    def get_all_stocks():
        """从数据库获取所有股票代码和名称"""
        sql = """
            SELECT DISTINCT stock_code, stock_name
            FROM holding_changes_summary
            WHERE stock_name IS NOT NULL AND stock_name != ''
            ORDER BY stock_code
        """
        return query_df(sql)
    
    def to_pinyin(text):
        """将中文转换为拼音字符串（无空格）"""
        return ''.join(lazy_pinyin(text))
    
    def filter_stocks(query, stocks_df):
        """根据输入过滤股票：支持代码、名称、拼音搜索"""
        if not query:
            return stocks_df
        query_lower = query.lower().strip()
        results = []
        for _, row in stocks_df.iterrows():
            code = str(row['stock_code'])
            name = str(row['stock_name']) if pd.notna(row['stock_name']) else ''
            # 代码匹配
            if query_lower in code.lower():
                results.append(row)
                continue
            # 名称匹配
            if query_lower in name.lower():
                results.append(row)
                continue
            # 拼音匹配
            pinyin_str = to_pinyin(name).lower()
            if query_lower in pinyin_str:
                results.append(row)
                continue
        return pd.DataFrame(results) if results else pd.DataFrame()
    
    all_stocks_df = get_all_stocks()
    
    stock_search = st.text_input(
        "搜索股票（支持代码 / 名称 / 拼音）",
        placeholder="例如: 600519 或 茅台 或 mt",
        key="stock_search_input"
    )
    
    stock_code = None
    stock_name_display = None
    
    if stock_search:
        matched = filter_stocks(stock_search, all_stocks_df)
        if not matched.empty:
            # 构建显示选项：代码 + 名称
            matched = matched.copy()
            matched['display'] = matched['stock_code'] + ' - ' + matched['stock_name']
            options = matched['display'].tolist()
            
            selected_display = st.selectbox(
                "请选择股票",
                options,
                index=0 if len(options) == 1 else None,
                label_visibility="collapsed",
                key="stock_selectbox"
            )
            
            if selected_display:
                # 从选项中提取代码和名称
                parts = selected_display.split(' - ', 1)
                stock_code = parts[0]
                stock_name_display = parts[1]
        else:
            st.info(f"未找到匹配 '{stock_search}' 的股票")
    
    if stock_code:
        sql = """
            SELECT report_date, holder_type, total_hold_shares, total_market_value,
                   change_shares, change_market_value, change_status
            FROM holding_changes_summary
            WHERE stock_code = ?
            ORDER BY report_date DESC
        """
        df_stock = query_df(sql, (stock_code,))
        
        if not df_stock.empty:
            # 显示股票标题（代码 + 名称）
            title_text = f"{stock_code} {stock_name_display} 机构持仓市值变化趋势" if stock_name_display else f"{stock_code} 机构持仓市值变化趋势"
            
            # 保留趋势图用的 mv_亿 列
            df_stock["mv_亿"] = df_stock["total_market_value"] / 1e8
            fig = px.line(df_stock, x="report_date", y="mv_亿", color="holder_type",
                         title=title_text,
                         markers=True)
            st.plotly_chart(fig, width='stretch')
            
            # 参照国家队 Tab 展示：换算单位并输出中文表头
            display_df = df_stock.copy()
            # 无上期数据的变动列补 0，避免显示 NaN
            display_df["change_market_value"] = display_df["change_market_value"].fillna(0)
            display_df["change_shares"] = display_df["change_shares"].fillna(0)
            
            display_df["期末市值(亿)"] = (display_df["total_market_value"] / 1e8).round(2)
            display_df["市值增减(亿)"] = (display_df["change_market_value"] / 1e8).round(2)
            display_df["期末持股数(万股)"] = (display_df["total_hold_shares"] / 1e4).round(2)
            display_df["持股增减(万股)"] = (display_df["change_shares"] / 1e4).round(2)
            
            display_df = display_df[["holder_type", "期末市值(亿)", "市值增减(亿)",
                                     "期末持股数(万股)", "持股增减(万股)",
                                     "change_status", "report_date"]].rename(columns={
                "holder_type": "机构类型",
                "change_status": "变动状态",
                "report_date": "报告期",
            })
            st.dataframe(display_df, width='stretch')
        else:
            name_part = f" {stock_name_display}" if stock_name_display else ""
            st.info(f"未找到 {stock_code}{name_part} 的机构持仓数据。")

# ---------- Tab 3: 国家队 ----------
with tab3:
    st.subheader("🏛️ 国家队持仓监控")
    
    if selected_date:
        # 变动状态筛选
        status_options = ["全部", "新进", "退出", "增持", "减持", "不变"]
        selected_status = st.multiselect("变动状态", status_options, default=["全部"])
        
        # 报告期多选（默认选中当前选定的报告期）
        all_report_dates = available_dates  # 全部可用报告期
        selected_dates = st.multiselect(
            "选择报告期", 
            all_report_dates, 
            default=[selected_date],
            help="可以选择多个报告期进行对比分析"
        )
        
        status_filter = ""
        if selected_status and "全部" not in selected_status:
            placeholders = ",".join(["?"] * len(selected_status))
            status_filter = f" AND change_status IN ({placeholders})"
        elif not selected_status:
            # 用户未选择任何状态，显示空结果
            st.info("请选择至少一种变动状态。")
            status_filter = " AND 1=0"
        
        # 构建报告期筛选条件
        if selected_dates:
            date_placeholders = ",".join(["?"] * len(selected_dates))
            date_filter = f" AND report_date IN ({date_placeholders})"
        else:
            # 用户未选择任何报告期，显示空结果
            st.info("请选择至少一个报告期。")
            date_filter = " AND 1=0"
        
        sql = """
            SELECT stock_code, stock_name, holder_type, 
                   total_hold_shares, total_market_value,
                   change_shares, change_market_value, change_status, report_date
            FROM holding_changes_summary
            WHERE holder_type IN ('证金公司', '汇金公司', '证金资管计划')
              {date_filter}
              {status_filter}
            ORDER BY report_date DESC, total_market_value DESC
            LIMIT 200
        """.format(date_filter=date_filter, status_filter=status_filter)
        
        # 构建参数
        params = []
        if selected_dates:
            params.extend(selected_dates)
        if selected_status and "全部" not in selected_status:
            params.extend(selected_status)
        params = tuple(params) if params else ()
        
        df_gjd = query_df(sql, params)
        
        if not df_gjd.empty:
            # 期末口径（与数据库一致）：退出记录当期已清仓，市值/持股数均为 0
            df_gjd["end_mv_亿"] = df_gjd["total_market_value"] / 1e8
            df_gjd["chg_亿"] = df_gjd["change_market_value"] / 1e8
            df_gjd["end_hold_wan"] = df_gjd["total_hold_shares"] / 1e4
            df_gjd["chg_shares_wan"] = df_gjd["change_shares"] / 1e4
            df_gjd[["end_mv_亿", "chg_亿", "end_hold_wan", "chg_shares_wan"]] = df_gjd[
                ["end_mv_亿", "chg_亿", "end_hold_wan", "chg_shares_wan"]].round(2)
            
            # 柱状图（仅展示当前实际持仓：期末市值 > 0 且股票名称非空）
            df_plot = df_gjd[(df_gjd["end_mv_亿"] > 0) 
                             & df_gjd["stock_name"].notna() 
                             & (df_gjd["stock_name"] != "")].copy()
            if not df_plot.empty:
                fig = px.bar(df_plot, x="stock_name", y="end_mv_亿", color="holder_type",
                            title="国家队持仓市值 Top 个股（期末，亿元）",
                            labels={"stock_name": "股票", "end_mv_亿": "期末市值(亿)", "holder_type": "机构类型"},
                            barmode="group")
                st.plotly_chart(fig, width='stretch')
            else:
                st.info("暂无国家队持仓个股数据。")
            
            st.dataframe(df_gjd[["stock_code", "stock_name", "holder_type", "end_mv_亿", "chg_亿",
                                 "end_hold_wan", "chg_shares_wan", "change_status", "report_date"]].rename(
                columns={
                    "stock_code": "股票代码",
                    "stock_name": "股票名称",
                    "holder_type": "机构类型",
                    "end_mv_亿": "期末市值(亿)",
                    "chg_亿": "市值增减(亿)",
                    "end_hold_wan": "期末持股数(万股)",
                    "chg_shares_wan": "持股增减(万股)",
                    "change_status": "变动状态",
                    "report_date": "报告期",
                }
            ), width='stretch')
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
    
    # 获取预警表中的报告期
    alert_dates_df = query_df("SELECT DISTINCT report_date FROM alerts WHERE report_date IS NOT NULL ORDER BY report_date DESC")
    alert_dates = alert_dates_df["report_date"].tolist() if not alert_dates_df.empty else []
    
    # 等级筛选
    level_options = ["全部", "紧急", "重要", "普通"]
    selected_level = st.selectbox("预警等级", level_options, index=0)
    
    # 报告期筛选
    selected_alert_date = st.selectbox("公告日期", ["全部"] + alert_dates, index=0)
    
    # 构建查询条件
    conditions = []
    params = []
    if selected_level != "全部":
        conditions.append("alert_level = ?")
        params.append(selected_level)
    if selected_alert_date != "全部":
        conditions.append("report_date = ?")
        params.append(selected_alert_date)
    
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    
    sql = f"""
        SELECT alert_time, report_date, alert_level, alert_type, stock_code, stock_name, holder_type, message
        FROM alerts
        {where_clause}
        ORDER BY 
            CASE alert_level WHEN '紧急' THEN 1 WHEN '重要' THEN 2 WHEN '普通' THEN 3 END,
            report_date DESC
    """
    df_alert = query_df(sql, tuple(params))
    
    if not df_alert.empty:
        # 重命名显示列名
        display_df = df_alert.rename(columns={
            "alert_time": "记录时间",
            "report_date": "公告日期",
            "alert_level": "预警等级",
            "alert_type": "预警类型",
            "stock_code": "股票代码",
            "stock_name": "股票名称",
            "holder_type": "机构类型",
            "message": "预警内容",
        })
        
        def highlight_level(val):
            if val == "紧急":
                return "background-color: #ffcccc"
            elif val == "重要":
                return "background-color: #ffe6cc"
            return ""
        
        styled = display_df.style.map(highlight_level, subset=["预警等级"])
        st.dataframe(styled, width='stretch')
    else:
        st.info("暂无预警记录。")


