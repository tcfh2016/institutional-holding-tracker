"""机构调研活跃度与非成分股候选分析。"""
import math
from datetime import date, timedelta

import pandas as pd

from database.db_manager import query_df


QUALITY_TYPES = {
    "证券公司": 1.0,
    "公募": 1.0,
    "保险": 1.0,
    "私募": 1.0,
    "资产管理公司": 1.0,
    "基金": 1.0,
}


def get_research_candidates(
    days: int = 30,
    exclude_components: bool = True,
    min_survey_count: int = 2,
    min_institution_count: int = 3,
) -> pd.DataFrame:
    """返回近期机构调研活跃股票，默认排除当前指数成分股。"""
    start_date = (date.today() - timedelta(days=days)).isoformat()
    component_join = ""
    component_filter = ""
    if exclude_components:
        component_join = "LEFT JOIN (SELECT DISTINCT stock_code FROM index_components) components ON components.stock_code = research.stock_code"
        component_filter = "AND components.stock_code IS NULL"

    sql = f"""
        SELECT research.stock_code,
               MAX(research.stock_name) AS stock_name,
               COUNT(*) AS survey_count,
               COUNT(DISTINCT research.institution_name) AS institution_count,
               COUNT(DISTINCT CASE
                   WHEN research.institution_type IN ({','.join('?' for _ in QUALITY_TYPES)})
                   THEN research.institution_name END
               ) AS quality_institution_count,
               MAX(research.survey_date) AS latest_survey_date
        FROM institutional_research research
        {component_join}
        WHERE research.survey_date >= ?
          {component_filter}
        GROUP BY research.stock_code
        HAVING survey_count >= ? OR institution_count >= ?
        ORDER BY survey_count DESC, institution_count DESC
    """
    params = tuple(QUALITY_TYPES) + (start_date, min_survey_count, min_institution_count)
    candidates = query_df(sql, params)
    if candidates.empty:
        return candidates

    candidates["score"] = (
        candidates["survey_count"].map(math.log1p)
        + 1.5 * candidates["institution_count"].map(math.log1p)
        + candidates["quality_institution_count"]
    ).round(3)
    return candidates.sort_values(
        ["score", "survey_count", "institution_count"],
        ascending=False,
    ).reset_index(drop=True)
