"""
项目主入口：一键运行数据采集 → 清洗 → 分析 → 报告
"""
import sys
import argparse
import logging
from pathlib import Path

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import DATA_DIR, TRACKED_INDICES
from database.db_manager import init_database
from ingestion.index_components import ingest_index_components, get_index_stock_codes
from ingestion.top_holders import ingest_all_top_holders
from ingestion.institutional_research import ingest_institutional_research
from ingestion.market_data import ingest_daily_prices, sync_stocks_from_index_components
from cleansing.holder_classifier import init_holder_mappings, update_top_holders_type
from analysis.holding_changes import compute_all_holding_changes, compute_all_index_summaries
from reporting.quarterly_report import generate_quarterly_report
from alerting.rules import run_all_alerts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def run_pipeline(
    stages: list = None,
    force_refresh: bool = False,
    research_start_date: str = None,
    research_end_date: str = None,
    research_full_market: bool = False,
    price_start_date: str = None,
    price_end_date: str = None,
):
    """
    执行完整数据流水线
    stages: 指定要运行的阶段，None 表示全部运行
    """
    all_stages = ["init", "index", "stocks", "holders", "research", "prices", 
                  "classify", "analyze", "report", "alert"]
    stages = stages or all_stages
    
    logger.info("=" * 60)
    logger.info("🚀 A股大机构持仓跟踪系统 - 数据流水线启动")
    logger.info("=" * 60)
    
    # 1. 初始化数据库
    if "init" in stages:
        logger.info("\n[1/9] 初始化数据库...")
        init_database()
        init_holder_mappings()
    
    # 2. 采集指数成分股
    stock_codes = []
    if "index" in stages:
        logger.info("\n[2/9] 采集指数成分股...")
        ingest_index_components()
        stock_codes = get_index_stock_codes()
        logger.info(f"    共获取 {len(stock_codes)} 只成分股")
    else:
        # 从数据库读取已有的成分股
        from database.db_manager import query_sql
        rows = query_sql("SELECT DISTINCT stock_code FROM index_components")
        stock_codes = [r["stock_code"] for r in rows]
    
    if not stock_codes:
        logger.error("❌ 没有获取到任何成分股代码，流水线终止。")
        return

    if "stocks" in stages or "holders" in stages or "research" in stages:
        logger.info("\n[3/10] 同步股票基础信息...")
        sync_stocks_from_index_components()
    
    # 3. 采集十大股东
    if "holders" in stages:
        logger.info("\n[4/10] 采集十大股东/十大流通股东...")
        # 为演示速度，这里只取前 50 只作为示例
        # 实际使用时可以去掉切片
        sample_codes = stock_codes
        logger.info(f"    本次采样 {len(sample_codes)} 只股票（演示模式）")
        ingest_all_top_holders(
            sample_codes,
            report_dates=["20260630"],
            force_refresh=force_refresh,
        )
    
    # 4. 采集机构调研
    if "research" in stages:
        logger.info("\n[5/10] 采集机构调研记录...")
        ingest_institutional_research(
            stock_codes=stock_codes,
            days_back=2,
            start_date=research_start_date,
            end_date=research_end_date,
            full_market=True,
            force_full_market=research_full_market,
        )
    
    # 5. 采集行情数据
    if "prices" in stages:
        logger.info("\n[6/10] 采集日度行情...")
        sample_codes = stock_codes[:]
        ingest_daily_prices(
            sample_codes,
            days_back=90,
            start_date=price_start_date,
            end_date=price_end_date,
        )
    
    # 6. 股东分类
    if "classify" in stages:
        logger.info("\n[7/10] 股东识别与分类...")
        update_top_holders_type()
    
    # 7. 持仓变化分析
    if "analyze" in stages:
        logger.info("\n[8/10] 计算持仓变化...")
        compute_all_holding_changes()
        compute_all_index_summaries()
    
    # 8. 生成报告
    if "report" in stages:
        logger.info("\n[9/10] 生成季度报告...")
        from analysis.holding_changes import get_report_dates
        dates = get_report_dates()
        if len(dates) >= 2:
            report_path = DATA_DIR / f"report_{dates[-1]}.md"
            generate_quarterly_report(dates[-1], output_path=str(report_path))
            logger.info(f"    报告已保存: {report_path}")
        else:
            logger.warning("    报告期不足，跳过报告生成。")
    
    # 9. 运行预警
    if "alert" in stages:
        logger.info("\n[10/10] 运行预警规则...")
        from analysis.holding_changes import get_report_dates
        dates = get_report_dates()
        if dates:
            run_all_alerts(dates[-1])
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ 流水线执行完毕")
    logger.info("=" * 60)
    logger.info(f"📁 数据库位置: {DATA_DIR / 'institutional_holding.db'}")
    logger.info(f"📊 启动看板: streamlit run dashboard/app.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A股大机构持仓跟踪系统")
    parser.add_argument("--stage", nargs="+", choices=["init", "index", "stocks", "holders", "research", 
                        "prices", "classify", "analyze", "report", "alert"],
                        help="指定要运行的阶段，不指定则运行全部")
    parser.add_argument("--init-only", action="store_true", help="仅初始化数据库和映射表")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="重新请求已存在的十大股东数据",
    )
    parser.add_argument(
        "--research-start-date",
        help="机构调研历史回补起始日期，格式 YYYYMMDD；查询该日期之后的数据",
    )
    parser.add_argument(
        "--research-end-date",
        help="机构调研历史回补结束日期，格式 YYYYMMDD；必须晚于起始日期",
    )
    parser.add_argument(
        "--research-full-market",
        action="store_true",
        help="重新请求指定日期的全市场调研数据，补齐历史遗漏",
    )
    parser.add_argument(
        "--price-start-date",
        help="行情采集起始日期，格式 YYYYMMDD；用于补采历史区间",
    )
    parser.add_argument(
        "--price-end-date",
        help="行情采集结束日期，格式 YYYYMMDD；默认今天",
    )
    
    args = parser.parse_args()
    
    if args.init_only:
        init_database()
        init_holder_mappings()
        logger.info("✅ 数据库和映射表初始化完成")
    elif args.stage:
        run_pipeline(
            stages=args.stage,
            force_refresh=args.force_refresh,
            research_start_date=args.research_start_date,
            research_end_date=args.research_end_date,
            research_full_market=args.research_full_market,
            price_start_date=args.price_start_date,
            price_end_date=args.price_end_date,
        )
    else:
        run_pipeline(
            force_refresh=args.force_refresh,
            research_start_date=args.research_start_date,
            research_end_date=args.research_end_date,
            research_full_market=args.research_full_market,
            price_start_date=args.price_start_date,
            price_end_date=args.price_end_date,
        )
