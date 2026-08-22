"""
数据库管理模块：连接、初始化、常用操作
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import List, Dict, Any, Optional
import pandas as pd

from config.settings import DB_PATH


def get_db_path() -> Path:
    """获取数据库文件路径，确保目录存在"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH


@contextmanager
def get_connection():
    """获取数据库连接的上下文管理器"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def init_database():
    """初始化数据库：执行 schema.sql"""
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.execute("DROP TABLE IF EXISTS northbound_holdings")
        conn.execute("DROP TABLE IF EXISTS institutional_research_fetch_log")
        
        # 迁移：为 alerts 表添加 report_date 列（兼容旧数据库）
        cursor = conn.execute("PRAGMA table_info(alerts)")
        existing_cols = [row[1] for row in cursor.fetchall()]
        if "report_date" not in existing_cols:
            conn.execute("ALTER TABLE alerts ADD COLUMN report_date DATE")
            print("[DB] Migrated: added report_date column to alerts table")
        
        conn.commit()
        print(f"[DB] Database initialized at {db_path}")
    finally:
        conn.close()


def execute_sql(sql: str, params: tuple = ()) -> int:
    """执行单条 SQL，返回影响行数"""
    with get_connection() as conn:
        cursor = conn.execute(sql, params)
        return cursor.rowcount


def query_sql(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """执行查询，返回字典列表"""
    with get_connection() as conn:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    """执行查询，返回 DataFrame"""
    with get_connection() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def upsert_df(df: pd.DataFrame, table: str, unique_cols: List[str]):
    """
    将 DataFrame 写入 SQLite，存在则更新，不存在则插入
    使用 INSERT OR REPLACE 策略
    """
    if df.empty:
        return 0
    
    with get_connection() as conn:
        # 先写入临时表
        df.to_sql("_temp_upsert", conn, if_exists="replace", index=False)
        
        # 获取目标表的列
        cursor = conn.execute(f"PRAGMA table_info({table})")
        table_cols = [row[1] for row in cursor.fetchall()]
        
        # 只保留目标表存在的列
        common_cols = [c for c in df.columns if c in table_cols]
        if not common_cols:
            raise ValueError(f"No matching columns between DataFrame and table '{table}'")
        
        col_str = ", ".join(common_cols)
        
        # INSERT OR REPLACE
        sql = f"""
            INSERT OR REPLACE INTO {table} ({col_str})
            SELECT {col_str} FROM _temp_upsert
        """
        cursor = conn.execute(sql)
        conn.execute("DROP TABLE _temp_upsert")
        return cursor.rowcount


def table_exists(table: str) -> bool:
    """检查表是否存在"""
    sql = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
    with get_connection() as conn:
        cursor = conn.execute(sql, (table,))
        return cursor.fetchone() is not None


def get_max_date(table: str, date_col: str = "report_date") -> Optional[str]:
    """获取表中某日期列的最大值"""
    sql = f"SELECT MAX({date_col}) as max_date FROM {table}"
    result = query_sql(sql)
    if result and result[0]["max_date"]:
        return result[0]["max_date"]
    return None
