"""
数据库管理模块：连接、初始化、常用操作
"""
import datetime
import re
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


def execute_many(sql: str, params_list: list, batch_size: int = 1000) -> int:
    """
    单连接 executemany 分批执行写操作（批量 UPDATE/INSERT），返回总影响行数。
    相比逐条 execute_sql，DB 往返次数从 N 降到 N/batch_size，适用于大批量写。
    """
    if not params_list:
        return 0
    total = 0
    with get_connection() as conn:
        for i in range(0, len(params_list), batch_size):
            batch = params_list[i : i + batch_size]
            cursor = conn.executemany(sql, batch)
            total += cursor.rowcount
    return total


def normalize_report_date(date) -> str:
    """
    归一化报告期日期为数据库格式 YYYY-MM-DD。
    兼容：'20260630'（采集入参）、'2026-06-30'（数据库格式）、datetime/date/pd.Timestamp。
    非法输入抛 ValueError。
    """
    if date is None:
        raise ValueError("report_date cannot be None")
    if isinstance(date, (datetime.datetime, datetime.date, pd.Timestamp)):
        return pd.Timestamp(date).strftime("%Y-%m-%d")
    s = str(date).strip()
    if not s:
        raise ValueError(f"Invalid empty report_date: {date!r}")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    if re.match(r"^\d{8}$", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        raise ValueError(f"Unrecognized report_date format: {date!r}") from None


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


def get_tracked_stock_codes() -> List[str]:
    """
    获取完整跟踪股票池：指数成分股 ∪ 机构持仓股票。
    - 指数视角（index_holding_summary 等）仍用 index_components
    - 个股/机构持仓视角（holders/prices/analyze 等）用本函数
    - institutional_holdings 中 holder_types 为 NULL 的仅作扫描进度标记，不纳入跟踪池
    """
    sql = """
        SELECT DISTINCT stock_code FROM index_components
        UNION
        SELECT stock_code FROM institutional_holdings
        WHERE holder_types IS NOT NULL
    """
    try:
        rows = query_sql(sql)
    except sqlite3.OperationalError:
        # 旧库尚无 institutional_holdings 表：降级为仅指数成分
        rows = query_sql("SELECT DISTINCT stock_code FROM index_components")
    return [r["stock_code"] for r in rows]
