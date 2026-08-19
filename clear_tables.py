import sys
sys.path.insert(0, '.')
from database.db_manager import execute_sql

# 清空相关表以便重新采集
execute_sql("DELETE FROM top_holders")
execute_sql("DELETE FROM holding_changes_summary")
execute_sql("DELETE FROM index_holding_summary")
execute_sql("DELETE FROM alerts")
print("Tables cleared.")
