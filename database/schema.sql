-- ============================================
-- A股大机构持仓跟踪系统 - 数据库表结构
-- SQLite 方言
-- ============================================

-- 指数基本信息
CREATE TABLE IF NOT EXISTS indices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    index_name  TEXT NOT NULL,
    index_code  TEXT NOT NULL UNIQUE,
    exchange    TEXT,
    component_count INTEGER,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 指数成分股
CREATE TABLE IF NOT EXISTS index_components (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    index_code  TEXT NOT NULL,
    stock_code  TEXT NOT NULL,
    stock_name  TEXT,
    weight      REAL,           -- 权重（%）
    effective_date DATE,        -- 生效日期
    FOREIGN KEY (index_code) REFERENCES indices(index_code),
    UNIQUE(index_code, stock_code, effective_date)
);

-- 股票基础信息
CREATE TABLE IF NOT EXISTS stocks (
    stock_code      TEXT PRIMARY KEY,
    stock_name      TEXT,
    total_shares    REAL,       -- 总股本（万股）
    float_shares    REAL,       -- 流通股本（万股）
    industry        TEXT,       -- 申万行业
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 日度行情
CREATE TABLE IF NOT EXISTS daily_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code  TEXT NOT NULL,
    trade_date  DATE NOT NULL,
    close_price REAL,
    open_price  REAL,
    high_price  REAL,
    low_price   REAL,
    volume      REAL,
    amount      REAL,
    UNIQUE(stock_code, trade_date)
);

-- 十大股东 / 十大流通股东 明细
CREATE TABLE IF NOT EXISTS top_holders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code          TEXT NOT NULL,
    stock_name          TEXT,
    report_date         DATE NOT NULL,      -- 报告期（季报日期）
    holder_name         TEXT NOT NULL,      -- 股东名称
    holder_type         TEXT,               -- 机构类型（清洗后）
    holder_type_raw     TEXT,               -- 原始类型
    hold_shares         REAL,               -- 持股数量（股）
    hold_ratio_total    REAL,               -- 占总股本比例（%）
    hold_ratio_float    REAL,               -- 占流通A股比例（%）
    change_status       TEXT,               -- 新进/退出/增持/减持/不变
    change_shares       REAL,               -- 变动数量
    change_ratio        REAL,               -- 变动比例（%）
    rank                INTEGER,            -- 股东排名
    is_float_holder     INTEGER DEFAULT 0,  -- 是否为流通股东 1=是
    announce_date       DATE,               -- 披露日期
    data_source         TEXT DEFAULT 'akshare',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, report_date, holder_name, is_float_holder)
);

-- 基金重仓股
CREATE TABLE IF NOT EXISTS fund_holdings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date         DATE NOT NULL,
    fund_code           TEXT NOT NULL,
    fund_name           TEXT,
    stock_code          TEXT NOT NULL,
    stock_name          TEXT,
    hold_shares         REAL,               -- 持股数量
    hold_market_value   REAL,               -- 持股市值（万元）
    net_value_ratio     REAL,               -- 占净值比例（%）
    stock_value_ratio   REAL,               -- 占股票市值比例（%）
    seq                 INTEGER,            -- 重仓排名
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_date, fund_code, stock_code)
);

-- 机构调研明细
CREATE TABLE IF NOT EXISTS institutional_research (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code          TEXT NOT NULL,
    stock_name          TEXT,
    survey_date         DATE NOT NULL,
    notice_date         DATE,
    institution_name    TEXT NOT NULL,
    institution_type    TEXT,
    survey_method       TEXT,
    survey_place        TEXT,
    investigators       TEXT,
    receptionists       TEXT,
    data_source         TEXT DEFAULT 'akshare',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, survey_date, notice_date, institution_name)
);

-- 机构名称映射表（用于股东识别分类）
CREATE TABLE IF NOT EXISTS holder_mappings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword         TEXT NOT NULL,          -- 匹配关键词
    holder_type     TEXT NOT NULL,          -- 标准机构类型
    priority        INTEGER DEFAULT 100,    -- 匹配优先级，数字越小越优先
    match_type      TEXT DEFAULT 'contains', -- contains / exact / regex
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(keyword, holder_type)
);

-- 持仓变化汇总（按报告期）
CREATE TABLE IF NOT EXISTS holding_changes_summary (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date         DATE NOT NULL,
    prev_report_date    DATE,
    stock_code          TEXT NOT NULL,
    stock_name          TEXT,
    holder_type         TEXT NOT NULL,
    total_hold_shares   REAL,               -- 该类机构合计持股
    total_market_value  REAL,               -- 合计持股市值
    change_shares       REAL,
    change_market_value REAL,
    change_status       TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_date, stock_code, holder_type)
);

-- 指数层面持仓汇总
CREATE TABLE IF NOT EXISTS index_holding_summary (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date         DATE NOT NULL,
    index_code          TEXT NOT NULL,
    index_name          TEXT,
    holder_type         TEXT NOT NULL,
    stock_count         INTEGER,            -- 持仓股票数量
    total_market_value  REAL,               -- 合计持仓市值
    total_change_value  REAL,               -- 合计变动市值
    avg_hold_ratio      REAL,               -- 平均持仓比例
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_date, index_code, holder_type)
);

-- 预警记录
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_time      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    report_date     DATE,               -- 公告日期（报告期）
    alert_type      TEXT NOT NULL,          -- 预警类型
    alert_level     TEXT NOT NULL,          -- 普通/重要/紧急
    stock_code      TEXT,
    stock_name      TEXT,
    holder_type     TEXT,
    message         TEXT,
    is_read         INTEGER DEFAULT 0
);

-- 机构持仓股票（全市场扫描结果：指数成分之外的机构持仓股票，纳入跟踪池）
CREATE TABLE IF NOT EXISTS institutional_holdings (
    stock_code      TEXT PRIMARY KEY,
    stock_name      TEXT,
    holder_types    TEXT,               -- 识别到的机构类型，逗号分隔（如 "证金公司,社保基金"）
    report_date     DATE,               -- 扫描到的报告期
    last_scan_date  DATE,               -- 最近扫描日期，断点续扫依据
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 股东数据采集状态（区分"真实清仓"与"数据缺失"，供分析引擎排除误判退出）
CREATE TABLE IF NOT EXISTS top_holder_fetch_status (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code      TEXT NOT NULL,
    report_date     DATE NOT NULL,
    is_float_holder INTEGER NOT NULL,   -- 0=十大股东, 1=十大流通股东
    status          TEXT NOT NULL,      -- ok / no_data / error
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, report_date, is_float_holder)
);

-- 创建常用索引
CREATE INDEX IF NOT EXISTS idx_th_stock ON top_holders(stock_code, report_date);
CREATE INDEX IF NOT EXISTS idx_th_type ON top_holders(holder_type, report_date);
CREATE INDEX IF NOT EXISTS idx_dp_date ON daily_prices(stock_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_research_date ON institutional_research(stock_code, survey_date);
CREATE INDEX IF NOT EXISTS idx_fh_date ON fund_holdings(report_date, stock_code);
CREATE INDEX IF NOT EXISTS idx_hcs ON holding_changes_summary(report_date, holder_type);
CREATE INDEX IF NOT EXISTS idx_thfs_date ON top_holder_fetch_status(report_date, status);
