import sys
sys.path.insert(0, '.')
from database.db_manager import query_df

print("=== 指数成分股 ===")
print(query_df("SELECT index_code, COUNT(*) as cnt FROM index_components GROUP BY index_code"))

print("\n=== 十大股东样本（机构类型已识别）===")
df = query_df("SELECT stock_code, holder_name, holder_type, hold_shares, change_status, report_date FROM top_holders WHERE holder_type != '其他' LIMIT 15")
print(df.to_string(index=False))

print("\n=== 机构类型分布 ===")
print(query_df("SELECT holder_type, COUNT(*) as cnt FROM top_holders GROUP BY holder_type ORDER BY cnt DESC"))

print("\n=== 持仓变化汇总（亿元） ===")
df = query_df("""
    SELECT holder_type, change_status, COUNT(*) as cnt, 
           ROUND(SUM(change_market_value)/1e8, 2) as chg_亿
    FROM holding_changes_summary 
    GROUP BY holder_type, change_status 
    ORDER BY chg_亿 DESC
""")
print(df.to_string(index=False))

print("\n=== 指数层面汇总（亿元） ===")
df = query_df("""
    SELECT index_name, holder_type, stock_count, 
           ROUND(total_market_value/1e8, 2) as mv_亿,
           ROUND(total_change_value/1e8, 2) as chg_亿
    FROM index_holding_summary
""")
print(df.to_string(index=False))
