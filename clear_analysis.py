import sys
sys.path.insert(0, '.')
from database.db_manager import execute_sql

execute_sql("DELETE FROM holding_changes_summary")
execute_sql("DELETE FROM index_holding_summary")
print("Analysis tables cleared.")
