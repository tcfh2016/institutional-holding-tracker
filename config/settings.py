"""
项目全局配置
"""
import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 数据目录
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# SQLite 数据库路径
DB_PATH = DATA_DIR / "institutional_holding.db"

# 日志目录
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Tushare Pro Token（如需使用，在此填写）
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

# 跟踪的指数配置
TRACKED_INDICES = {
    "沪深300": {"code": "000300", "exchange": "sh"},
    "中证500": {"code": "000905", "exchange": "sh"},
    "创业板指": {"code": "399006", "exchange": "sz"},
    "科创50": {"code": "000688", "exchange": "sh"},
}

# 更新频率配置（cron 风格或间隔描述）
UPDATE_SCHEDULE = {
    "index_components": "0 9 1 * *",      # 每月1日检查指数成分股
    "top_holders": "0 10 1 5,9,11 *",    # 季报后：5月、9月、11月初
    "fund_holdings": "0 10 20 5,9,11 *", # 季报后15个工作日左右
    "research": "0 18 * * 1-5",          # 工作日收盘后
    "market_data": "0 18 * * 1-5",       # 工作日收盘后
}

# 请求重试配置
REQUEST_RETRIES = 3
REQUEST_TIMEOUT = 120  # socket 级超时（秒）：csindex 等慢接口合理上限，避免请求挂起数分钟
REQUEST_DELAY = 0.5  # 请求间隔秒数

# 采集状态缓存：无数据（no_data/error）股票距上次采集不足该天数则跳过请求，避免反复请求空接口
NO_DATA_RECHECK_DAYS = 7

# 演示模式：当网络环境不稳定无法获取实时行情时，使用模拟价格展示看板效果
# 生产环境请设为 False
DEMO_MODE = True

# 预警阈值
ALERT_THRESHOLDS = {
    "single_holder_change_ratio": 0.30,   # 单机构单季变动比例 >30%
    "index_holder_change_value_billion": 10,  # 指数内某类机构合计变动 >10亿
    "consecutive_quarters": 2,            # 连续季度数
}
