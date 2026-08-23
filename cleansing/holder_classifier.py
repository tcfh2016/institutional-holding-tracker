"""
股东识别与分类模块
将十大股东/流通股东的原始名称，分类为：
证金公司、汇金公司、证金资管计划、保险资金、社保基金、
公募基金、QFII、北向资金、券商、信托、其他
"""
import re
import logging
from typing import Optional, List, Dict, Tuple
import pandas as pd

from database.db_manager import (
    query_sql,
    execute_sql,
    execute_many,
    upsert_df,
    normalize_report_date,
)

logger = logging.getLogger(__name__)

# ============================================================
# 默认机构识别规则（关键词 → 机构类型）
# 优先级：数字越小越优先
# ============================================================
DEFAULT_RULES: List[Tuple[str, str, int, str]] = [
    # 证金公司（最高优先级，精确匹配）
    ("中国证券金融股份有限公司", "证金公司", 1, "exact"),
    ("中证金融资产管理计划", "证金资管计划", 2, "contains"),
    ("基金-农业银行-.*中证金融资产管理计划", "证金资管计划", 2, "regex"),
    
    # 汇金公司
    ("中央汇金投资有限责任公司", "汇金公司", 1, "exact"),
    ("中央汇金资产管理有限责任公司", "汇金公司", 1, "exact"),
    ("中央汇金", "汇金公司", 3, "contains"),
    
    # 社保基金
    ("全国社保基金", "社保基金", 2, "contains"),
    ("社保基金", "社保基金", 3, "contains"),
    ("基本养老保险基金", "社保基金", 3, "contains"),
    
    # 保险资金
    ("中国人寿保险", "保险", 2, "contains"),
    ("中国平安人寿保险", "保险", 2, "contains"),
    ("中国太平洋人寿保险", "保险", 2, "contains"),
    ("新华人寿保险", "保险", 2, "contains"),
    ("泰康人寿保险", "保险", 2, "contains"),
    ("中国人民人寿保险", "保险", 2, "contains"),
    ("太平人寿保险", "保险", 2, "contains"),
    ("前海人寿保险", "保险", 2, "contains"),
    ("保险股份有限公司", "保险", 3, "contains"),
    ("保险(-|－|—).*产品", "保险", 3, "regex"),
    ("保险资产管理", "保险", 3, "contains"),
    
    # 北向资金（陆股通）
    ("香港中央结算有限公司", "北向资金", 1, "exact"),
    ("香港中央结算", "北向资金", 2, "contains"),
    ("HKSCCL", "北向资金", 2, "contains"),
    
    # QFII
    ("高盛", "QFII", 3, "contains"),
    ("摩根士丹利", "QFII", 3, "contains"),
    ("摩根大通", "QFII", 3, "contains"),
    ("瑞银", "QFII", 3, "contains"),
    ("美林", "QFII", 3, "contains"),
    ("野村", "QFII", 3, "contains"),
    ("汇丰", "QFII", 3, "contains"),
    ("渣打", "QFII", 3, "contains"),
    ("花旗", "QFII", 3, "contains"),
    ("德意志银行", "QFII", 3, "contains"),
    ("法国巴黎银行", "QFII", 3, "contains"),
    ("新加坡政府投资", "QFII", 3, "contains"),
    ("挪威中央银行", "QFII", 3, "contains"),
    ("阿布达比投资局", "QFII", 3, "contains"),
    ("科威特政府投资局", "QFII", 3, "contains"),
    
    # 公募基金（十大股东中出现的一般不是公募，但基金重仓股会单独处理）
    # 这里主要是识别基金公司的资管计划
    ("易方达基金", "公募基金", 4, "contains"),
    ("华夏基金", "公募基金", 4, "contains"),
    ("嘉实基金", "公募基金", 4, "contains"),
    ("南方基金", "公募基金", 4, "contains"),
    ("广发基金", "公募基金", 4, "contains"),
    ("博时基金", "公募基金", 4, "contains"),
    ("汇添富基金", "公募基金", 4, "contains"),
    ("富国基金", "公募基金", 4, "contains"),
    ("招商基金", "公募基金", 4, "contains"),
    ("工银瑞信基金", "公募基金", 4, "contains"),
    
    # 券商
    ("证券股份有限公司", "券商", 4, "contains"),
    ("证券有限责任公司", "券商", 4, "contains"),
    ("国泰君安证券", "券商", 3, "contains"),
    ("中信证券", "券商", 3, "contains"),
    ("华泰证券", "券商", 3, "contains"),
    ("海通证券", "券商", 3, "contains"),
    ("招商证券", "券商", 3, "contains"),
    ("广发证券", "券商", 3, "contains"),
    ("中信建投证券", "券商", 3, "contains"),
    ("券商资产管理计划", "券商", 3, "contains"),
    
    # 信托
    ("信托有限责任公司", "信托", 3, "contains"),
    ("信托计划", "信托", 3, "contains"),
    ("集合资金信托", "信托", 3, "contains"),
]


def init_holder_mappings():
    """
    将默认规则写入数据库 holder_mappings 表
    """
    logger.info("[HolderClassifier] Initializing holder mappings...")
    
    rows = []
    for keyword, holder_type, priority, match_type in DEFAULT_RULES:
        rows.append({
            "keyword": keyword,
            "holder_type": holder_type,
            "priority": priority,
            "match_type": match_type,
        })
    
    df = pd.DataFrame(rows)
    try:
        upsert_df(df, "holder_mappings", ["keyword", "holder_type"])
        logger.info(f"[HolderClassifier] Saved {len(df)} mapping rules.")
    except Exception as e:
        logger.error(f"[HolderClassifier] Failed to save mappings: {e}")


def load_rules_from_db() -> List[Dict]:
    """从数据库加载识别规则"""
    sql = """
        SELECT keyword, holder_type, priority, match_type
        FROM holder_mappings
        ORDER BY priority ASC
    """
    return query_sql(sql)


def classify_holder(holder_name: str, rules: Optional[List[Dict]] = None) -> str:
    """
    对单个股东名称进行分类
    返回：机构类型字符串，未匹配则返回 "其他"
    """
    if not holder_name or not isinstance(holder_name, str):
        return "其他"
    
    name = holder_name.strip()
    if not name:
        return "其他"
    
    if rules is None:
        rules = load_rules_from_db()
    
    for rule in rules:
        keyword = rule["keyword"]
        match_type = rule.get("match_type", "contains")
        
        try:
            if match_type == "exact":
                if name == keyword:
                    return rule["holder_type"]
            elif match_type == "regex":
                if re.search(keyword, name):
                    return rule["holder_type"]
            else:  # contains
                if keyword in name:
                    return rule["holder_type"]
        except re.error:
            # 正则表达式错误，降级为 contains
            if keyword in name:
                return rule["holder_type"]
    
    return "其他"


def classify_holders_batch(df: pd.DataFrame, holder_col: str = "holder_name",
                            output_col: str = "holder_type") -> pd.DataFrame:
    """
    批量分类 DataFrame 中的股东名称
    """
    if df.empty:
        return df
    
    if holder_col not in df.columns:
        logger.warning(f"[HolderClassifier] Column '{holder_col}' not found in DataFrame.")
        return df
    
    rules = load_rules_from_db()
    if not rules:
        logger.warning("[HolderClassifier] No rules found in DB, using defaults.")
        # 使用内存中的默认规则
        rules = [{"keyword": k, "holder_type": t, "priority": p, "match_type": m}
                 for k, t, p, m in DEFAULT_RULES]
    
    df[output_col] = df[holder_col].apply(lambda x: classify_holder(x, rules))
    return df


def update_top_holders_type(report_date: Optional[str] = None):
    """
    对 top_holders 表中尚未分类的记录进行批量分类。
    - pandas 内存批量分类 + execute_many 分批 UPDATE（DB 往返从 N 次降到 N/1000 次）
    - report_date: 可选，按报告期分片处理（兼容 YYYYMMDD / YYYY-MM-DD），支持断点续跑
    """
    logger.info("[HolderClassifier] Updating holder types in top_holders...")
    
    # 查询尚未分类的记录（可选按报告期分片）
    sql = """
        SELECT id, holder_name FROM top_holders
        WHERE holder_type IS NULL OR holder_type = '' OR holder_type = '其他'
    """
    sql_params: tuple = ()
    if report_date is not None:
        sql += " AND report_date = ?"
        sql_params = (normalize_report_date(report_date),)
    rows = query_sql(sql, sql_params)
    
    scope = f" for {report_date}" if report_date else ""
    if not rows:
        logger.info(f"[HolderClassifier] No unclassified holders found{scope}.")
        return 0
    
    logger.info(f"[HolderClassifier] Found {len(rows)} unclassified holders{scope}.")
    
    rules = load_rules_from_db()
    if not rules:
        rules = [{"keyword": k, "holder_type": t, "priority": p, "match_type": m}
                 for k, t, p, m in DEFAULT_RULES]
    
    # 内存批量分类
    df = pd.DataFrame(rows)
    df["holder_type"] = df["holder_name"].apply(lambda x: classify_holder(x, rules))
    
    # 分批 UPDATE：DB 往返从 len(rows) 次降到 ~len(rows)/1000 次
    params_list = list(zip(df["holder_type"].tolist(), df["id"].tolist()))
    updated = execute_many(
        "UPDATE top_holders SET holder_type = ? WHERE id = ?",
        params_list,
    )
    
    logger.info(f"[HolderClassifier] Updated {updated} records.")
    return updated


def add_custom_rule(keyword: str, holder_type: str, priority: int = 10, match_type: str = "contains"):
    """
    添加自定义识别规则
    """
    sql = """
        INSERT OR REPLACE INTO holder_mappings (keyword, holder_type, priority, match_type)
        VALUES (?, ?, ?, ?)
    """
    execute_sql(sql, (keyword, holder_type, priority, match_type))
    logger.info(f"[HolderClassifier] Added rule: '{keyword}' -> {holder_type}")
