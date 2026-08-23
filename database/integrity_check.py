"""
数据完整性检查模块
检查各表之间的数据依赖关系是否完整，提示需要补跑的阶段
"""
import logging
from typing import List, Dict
from database.db_manager import query_sql

logger = logging.getLogger(__name__)


def check_unclassified_holders() -> Dict:
    """检查 top_holders 中未分类的记录。

    "未分类"仅指 holder_type 为 NULL 或空字符串；
    "其他"是分类器 classify_holder() 的合法兜底结果（普通股东），不算未分类。
    """
    sql = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN holder_type IS NULL OR holder_type = '' THEN 1 ELSE 0 END) as unclassified,
            SUM(CASE WHEN holder_type = '其他' THEN 1 ELSE 0 END) as others
        FROM top_holders
    """
    rows = query_sql(sql)
    if not rows:
        return {"total": 0, "unclassified": 0, "ok": True}
    
    total = rows[0]["total"]
    unclassified = rows[0]["unclassified"] or 0
    others = rows[0]["others"] or 0
    
    return {
        "total": total,
        "unclassified": unclassified,
        "ok": unclassified == 0,
        "message": f"top_holders: {total} 条记录, {unclassified} 条未分类(NULL/空), {others} 条为'其他'(普通股东)" if unclassified > 0 else None
    }


def check_missing_analysis() -> List[Dict]:
    """检查哪些报告期缺少 holding_changes_summary 数据"""
    # 获取 top_holders 中所有报告期
    sql1 = "SELECT DISTINCT report_date FROM top_holders ORDER BY report_date"
    holder_dates = [r["report_date"] for r in query_sql(sql1)]
    
    # 获取 holding_changes_summary 中所有报告期
    sql2 = "SELECT DISTINCT report_date FROM holding_changes_summary ORDER BY report_date"
    summary_dates = [r["report_date"] for r in query_sql(sql2)]
    
    # 找出缺少分析的报告期（排除第一个，因为第一个没有上期对比）
    missing = []
    if len(holder_dates) >= 2:
        for d in holder_dates[1:]:  # 跳过第一个
            if d not in summary_dates:
                missing.append(d)
    
    return missing


def check_missing_index_summary() -> List[Dict]:
    """检查哪些报告期缺少 index_holding_summary 数据"""
    sql1 = "SELECT DISTINCT report_date FROM holding_changes_summary ORDER BY report_date"
    changes_dates = [r["report_date"] for r in query_sql(sql1)]
    
    sql2 = "SELECT DISTINCT report_date FROM index_holding_summary ORDER BY report_date"
    index_dates = [r["report_date"] for r in query_sql(sql2)]
    
    missing = []
    for d in changes_dates:
        if d not in index_dates:
            missing.append(d)
    
    return missing


def check_stock_coverage() -> Dict:
    """检查 index_components 中的股票是否都有 top_holders 数据"""
    sql = """
        SELECT 
            ic.index_code,
            COUNT(DISTINCT ic.stock_code) as total_stocks,
            COUNT(DISTINCT th.stock_code) as covered_stocks
        FROM index_components ic
        LEFT JOIN top_holders th ON ic.stock_code = th.stock_code
        GROUP BY ic.index_code
    """
    rows = query_sql(sql)
    
    result = []
    all_ok = True
    for r in rows:
        total = r["total_stocks"]
        covered = r["covered_stocks"] or 0
        ok = covered == total
        if not ok:
            all_ok = False
        result.append({
            "index_code": r["index_code"],
            "total": total,
            "covered": covered,
            "missing": total - covered,
            "ok": ok
        })
    
    return {"details": result, "ok": all_ok}


def run_integrity_check():
    """运行完整的数据完整性检查"""
    logger.info("=" * 60)
    logger.info("[IntegrityCheck] 开始数据完整性检查...")
    logger.info("=" * 60)
    
    issues = []
    
    # 1. 检查未分类记录
    logger.info("\n[1/4] 检查股东分类完整性...")
    classify_result = check_unclassified_holders()
    if not classify_result["ok"]:
        issues.append(f"  - {classify_result['message']}")
        logger.warning(f"  [WARN] {classify_result['message']}")
        logger.warning("  [FIX] 运行: python main.py --stage classify")
    else:
        logger.info(f"  [OK] 所有 {classify_result['total']} 条记录已分类")
    
    # 2. 检查持仓变化分析
    logger.info("\n[2/4] 检查持仓变化分析完整性...")
    missing_analysis = check_missing_analysis()
    if missing_analysis:
        issues.append(f"  - 缺少持仓变化分析的报告期: {', '.join(missing_analysis)}")
        logger.warning(f"  [WARN] 缺少持仓变化分析: {', '.join(missing_analysis)}")
        logger.warning("  [FIX] 运行: python main.py --stage analyze")
    else:
        logger.info("  [OK] 所有报告期均有持仓变化分析")
    
    # 3. 检查指数汇总
    logger.info("\n[3/4] 检查指数汇总完整性...")
    missing_index = check_missing_index_summary()
    if missing_index:
        issues.append(f"  - 缺少指数汇总的报告期: {', '.join(missing_index)}")
        logger.warning(f"  [WARN] 缺少指数汇总: {', '.join(missing_index)}")
        logger.warning("  [FIX] 运行: python main.py --stage analyze")
    else:
        logger.info("  [OK] 所有报告期均有指数汇总")
    
    # 4. 检查股票覆盖率
    logger.info("\n[4/4] 检查指数成分股覆盖率...")
    coverage_result = check_stock_coverage()
    if not coverage_result["ok"]:
        for d in coverage_result["details"]:
            if not d["ok"]:
                issues.append(f"  - 指数 {d['index_code']}: {d['missing']}/{d['total']} 只股票缺少持仓数据")
                logger.warning(f"  [WARN] 指数 {d['index_code']}: {d['missing']}/{d['total']} 只股票缺少数据")
        logger.warning("  [FIX] 运行: python main.py --stage holders")
    else:
        total = sum(d["total"] for d in coverage_result["details"])
        logger.info(f"  [OK] 所有 {total} 只成分股均有持仓数据")
    
    # 总结
    logger.info("\n" + "=" * 60)
    if issues:
        logger.warning(f"[IntegrityCheck] 发现 {len(issues)} 个完整性问题:")
        for issue in issues:
            logger.warning(issue)
        logger.warning("[IntegrityCheck] 建议运行完整流水线: python main.py")
    else:
        logger.info("[IntegrityCheck] 数据完整性检查通过，所有表数据一致")
    logger.info("=" * 60)
    
    return len(issues) == 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    run_integrity_check()
