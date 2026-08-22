# 测试指南

本文档说明如何从 `main.py` 开始，逐步测试整个工程，并通过日志和 SQLite 数据库确认执行结果。

首次测试建议严格按照本文档顺序执行。不要一开始直接运行完整流水线，否则网络请求较多，出现问题时不容易判断是数据源、数据库还是业务逻辑故障。

## 测试范围

各阶段入口如下：

```text
help        检查命令行入口，不访问网络
init        初始化数据库和股东识别规则
index       采集沪深300、中证500、创业板指和科创50的成分股
stocks      从指数成分股同步股票基础信息
holders     采集十大股东和十大流通股东
research    采集全市场机构调研明细
northbound  采集北向资金个股持股数据
prices      采集日度行情和股票基础信息
analyze     计算持仓变化和指数层面汇总
report      生成季度 Markdown 报告
alert       执行预警规则并写入数据库
```

以下命令都应在项目根目录执行。

## 测试前准备

```powershell
python --version
python -m pip install -r requirements.txt
```

```powershell
Test-Path .\\main.py
```

项目使用 AkShare 访问公开数据接口。`northbound`、`holders`、`prices` 和 `index` 阶段需要网络连接，运行时间也会受接口响应速度影响。

注意：`config/settings.py` 中当前 `DEMO_MODE = True`。本地行情不存在时，分析模块可能使用可复现的模拟价格；这适合验证程序链路，不应把结果当作真实投资数据。

## 推荐测试顺序
### 1. 检查 main.py 入口

```powershell
python main.py --help
```

成功标准：能显示 `--stage` 和 `--init-only` 参数，且没有发起数据采集请求。

### 2. 初始化数据库

```powershell
python -u main.py --init-only 2>&1 | Tee-Object -FilePath data\logs\init_test.log
```

成功标准：

- 生成 `data\institutional_holding.db`
- `holder_mappings` 中存在默认股东识别规则

检查数据库文件和核心表：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print('tables:'); print(*c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\").fetchall(), sep='\n'); print('holder mappings:', c.execute('SELECT COUNT(*) FROM holder_mappings').fetchone()[0]); c.close()"
```
`research`、`holders`、`prices` 和 `index` 阶段需要网络连接。`research` 默认保存全市场调研数据。

### 3. 测试 AkShare 接口

这两个脚本是接口探针，当前主要打印返回结果，还没有自动化断言：

```powershell
python test_api.py 2>&1 | Tee-Object -FilePath data\logs\api_test.log
python test_price.py 2>&1 | Tee-Object -FilePath data\logs\price_api_test.log
```

记录以下信息：

- 接口是否可访问
- 返回行数
- 返回列名是否包含代码、日期和行情字段
- 是否出现超时、限流、参数错误或接口变更

如果这里失败，应先排查网络、AkShare 版本和接口参数，不要直接继续排查分析模块。

### 4. 采集指数成分股

```powershell
python -u main.py --stage init index 2>&1 | Tee-Object -FilePath data\logs\index_test.log
```

成功标准：

- 日志最后出现 `Ingestion completed.`
- 四个指数在 `indices` 表中都有记录
- 各指数的成分股数量大于 0
- `index_components` 中能查询到股票代码和名称

查询各指数保存数量：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_name,index_code,component_count FROM indices ORDER BY index_code').fetchall(), sep='\n'); c.close()"
```

查询成分股数量和采集日期：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_code,COUNT(DISTINCT stock_code) AS count,MIN(effective_date),MAX(effective_date) FROM index_components GROUP BY index_code').fetchall(), sep='\n'); c.close()"
```

查询样例：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_code,stock_code,stock_name,weight,effective_date FROM index_components ORDER BY index_code LIMIT 20').fetchall(), sep='\n'); c.close()"
```

不同指数之间可能有重复股票，因此各指数数量相加后会大于去重后的股票总数。

### 5. 同步股票基础信息

指数成分股已经包含股票代码和名称。主流程会在 `index` 后自动同步这些信息；也可以单独执行：

```powershell
python -u main.py --stage stocks 2>&1 | Tee-Object -FilePath data\logs\stocks_test.log
```

检查股票主数据：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print('stocks:', c.execute('SELECT COUNT(*) FROM stocks').fetchone()[0]); print(*c.execute('SELECT stock_code,stock_name FROM stocks ORDER BY stock_code LIMIT 5').fetchall(), sep='\\n'); c.close()"
```

### 6. 小样本采集十大股东

`main.py` 当前固定使用前 50 只成分股，并采集两个报告期：`20250331` 和 `20241231`。

```powershell
python -u main.py --stage holders 2>&1 | Tee-Object -FilePath data\logs\holders_test.log
```

检查报告期、股东类型和数据量：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print('dates:', c.execute('SELECT report_date,COUNT(*) FROM top_holders GROUP BY report_date ORDER BY report_date').fetchall()); print('float flag:', c.execute('SELECT is_float_holder,COUNT(*) FROM top_holders GROUP BY is_float_holder').fetchall()); print('rows:', c.execute('SELECT COUNT(*) FROM top_holders').fetchone()[0]); c.close()"
```

重点检查：

- 两个报告期是否都有数据
- `holder_name`、`stock_code`、`report_date` 是否为空
- `is_float_holder` 是否同时包含 `0` 和 `1`
- 同一股票、报告期、股东和流通标志是否重复

如果 50 只股票耗时过长，可以先在 Python 交互环境中对 1 只股票、1 个报告期调用 `fetch_top_holders_em()` 做最小探针。

### 7. 股东分类

```powershell
python -u main.py --stage classify 2>&1 | Tee-Object -FilePath data\logs\classify_test.log
```

查看分类分布：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT holder_type,COUNT(*) AS count FROM top_holders GROUP BY holder_type ORDER BY count DESC').fetchall(), sep='\n'); c.close()"
```

应额外验证四类名称：

- 精确匹配，例如 `中国证券金融股份有限公司`
- 包含匹配，例如包含 `中央汇金` 的名称
- 正则匹配，例如包含 `中证金融资产管理计划` 的名称
- 未匹配名称应返回 `其他`

### 8. 采集机构调研

机构调研是独立的数据采集模块，不是行情采集的前置步骤。需要先完成第 4 步，确保数据库中已有成分股；然后单独运行：

```powershell
python -u main.py --stage research 2>&1 | Tee-Object -FilePath data\\logs\\research_test.log
```

检查机构调研：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print('range:', c.execute('SELECT MIN(survey_date),MAX(survey_date) FROM institutional_research').fetchone()); print('rows:', c.execute('SELECT COUNT(*) FROM institutional_research').fetchone()[0]); print(*c.execute('SELECT stock_code,stock_name,survey_date,institution_name,institution_type FROM institutional_research ORDER BY survey_date DESC LIMIT 5').fetchall(), sep='\\n'); c.close()"
```

检查行情：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print('range:', c.execute('SELECT MIN(trade_date),MAX(trade_date) FROM daily_prices').fetchone()); print('rows:', c.execute('SELECT COUNT(*) FROM daily_prices').fetchone()[0]); print(*c.execute('SELECT stock_code,trade_date,close_price,volume,amount FROM daily_prices ORDER BY trade_date DESC LIMIT 5').fetchall(), sep='\n'); c.close()"
```

重点检查日期是否有效、价格和数量列是否为数值，以及唯一键是否生效。

机构调研默认保存全市场数据，并根据数据库中的 `survey_date` 增量采集：已有日期时不会发起网络请求；首次采集默认回补最近 2 天。

如需主动回补其他日期，可显式指定起始日期。下面命令查询 2026-08-18 之后的调研记录，并按已有唯一键写入数据库：

```powershell
python -u main.py --stage research --research-start-date 20260818 --research-end-date 20260821
```

历史回补模式会逐日检查 `institutional_research.survey_date`：数据库已有该日期记录时跳过网络请求，缺少该日期时才请求，不依赖额外的窗口日志表。

若历史日期之前只保存过指数成分股，需要使用 `--research-full-market` 强制重新请求这些日期，补齐非成分股记录。唯一键会阻止已有明细重复写入。

### 9. 采集行情

行情用于持仓变化分析中的市值计算：分析模块按报告期和股票代码查找收盘价，计算持仓市值和市值变动。它不参与机构调研记录的采集，也不决定调研记录是否入库。

行情同样需要先完成第 4 步，且当前只采集前 50 只成分股：

```powershell
python -u main.py --stage prices 2>&1 | Tee-Object -FilePath data\\logs\\prices_test.log
```

检查行情：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print('range:', c.execute('SELECT MIN(trade_date),MAX(trade_date) FROM daily_prices').fetchone()); print('rows:', c.execute('SELECT COUNT(*) FROM daily_prices').fetchone()[0]); print(*c.execute('SELECT stock_code,trade_date,close_price,volume,amount FROM daily_prices ORDER BY trade_date DESC LIMIT 5').fetchall(), sep='\\n'); c.close()"
```

重点检查日期有效、价格和数量列为数值，以及唯一键是否生效。若本地没有对应日期行情，分析模块会向前查找最近行情，再尝试临时请求；`DEMO_MODE=True` 时最后还会使用可复现的模拟价格，因此“分析能运行”不等于“行情采集成功”。

### 10. 执行持仓变化分析

分析至少需要两个报告期。确认日期后执行：

```powershell
python -u main.py --stage analyze 2>&1 | Tee-Object -FilePath data\logs\analyze_test.log
```

检查分析结果：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print('changes:', c.execute('SELECT report_date,prev_report_date,COUNT(*) FROM holding_changes_summary GROUP BY report_date,prev_report_date').fetchall()); print('statuses:', c.execute('SELECT change_status,COUNT(*) FROM holding_changes_summary GROUP BY change_status').fetchall()); print('index summaries:', c.execute('SELECT COUNT(*) FROM index_holding_summary').fetchone()[0]); c.close()"
```

重点验证：

- 新进、退出、增持、减持、不变状态是否合理
- `change_shares` 是否等于本期持股减上期持股
- 市值变化是否使用了本地行情或明确的演示价格
- 重复运行分析不会重复增加汇总记录

如果只有一个报告期，出现 `Not enough report dates` 是预期行为，不应当作程序故障。

### 11. 生成报告和运行预警

```powershell
python -u main.py --stage report alert 2>&1 | Tee-Object -FilePath data\logs\report_alert_test.log
```

检查报告文件：

```powershell
Get-ChildItem data\report_*.md
Get-Content (Get-ChildItem data\report_*.md | Sort-Object LastWriteTime | Select-Object -Last 1).FullName -TotalCount 40
```

报告应包含机构总览、国家队、保险、社保、QFII、北向资金、指数汇总和风险提示章节。没有数据的章节应明确显示“暂无数据”。

检查预警：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print('by type:', c.execute('SELECT alert_type,alert_level,COUNT(*) FROM alerts GROUP BY alert_type,alert_level').fetchall()); print(*c.execute('SELECT alert_time,alert_type,alert_level,stock_code,message FROM alerts ORDER BY id DESC LIMIT 10').fetchall(), sep='\n'); c.close()"
```

注意：`alerts` 表目前没有唯一约束，重复执行 `alert` 可能写入重复预警。测试时应记录执行次数，或在确认数据库后使用 `clear_tables.py` 清理相关数据。

### 12. 数据统一验收

```powershell
python verify_data.py | Tee-Object -FilePath data\logs\verify_data.log
```

该脚本汇总检查指数、已分类股东、机构类型分布、持仓变化和指数层面汇总，是端到端测试后的最后一个命令行验收入口。

### 13. 启动看板

```powershell
streamlit run dashboard/app.py
```

依次检查：

- 没有报告期数据时是否显示提示
- 报告期和机构类型筛选
- 个股代码查询和趋势图
- 国家队持仓表格和图表
- 北向资金最新日期、排名和图表
- 预警清单

看板启动时会再次执行 `init_database()`，这是预期行为。

## Windows（PowerShell）

进入项目目录并运行测试，同时在终端显示和保存日志：

```powershell
Set-Location 'd:\KimiData\kimi\workspace\institutional_holding_tracker'
python -u main.py --stage init index 2>&1 | Tee-Object -FilePath data\logs\index_test.log
```

日志默认覆盖旧文件；如需追加：

```powershell
python -u main.py --stage init index 2>&1 | Tee-Object -Append -FilePath data\logs\index_test.log
```

仅在终端运行：

```powershell
python -u main.py --stage init index
```

PowerShell 可能将 Python 的 stderr 显示为 `NativeCommandError`。这通常只是错误流的显示方式，不代表程序失败。

## Linux

```bash
cd /path/to/institutional_holding_tracker
python3 -u main.py --stage init index 2>&1 | tee data/logs/index_test.log
```

追加日志：

```bash
python3 -u main.py --stage init index 2>&1 | tee -a data/logs/index_test.log
```

仅在终端运行：

```bash
python3 -u main.py --stage init index
```

## macOS

```bash
cd /path/to/institutional_holding_tracker
python3 -u main.py --stage init index 2>&1 | tee data/logs/index_test.log
```

追加日志：

```bash
python3 -u main.py --stage init index 2>&1 | tee -a data/logs/index_test.log
```

仅在终端运行：

```bash
python3 -u main.py --stage init index
```

## 测试结果查询

### 查询各指数保存数量

Windows PowerShell：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_name,index_code,component_count FROM indices ORDER BY index_code').fetchall(), sep='\n'); c.close()"
```

Linux/macOS：

```bash
python3 -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_name,index_code,component_count FROM indices ORDER BY index_code').fetchall(), sep='\\n'); c.close()"
```

### 查询成分股数量和采集日期

Linux/macOS：

```bash
python3 -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_code,COUNT(DISTINCT stock_code) AS count,MIN(effective_date),MAX(effective_date) FROM index_components GROUP BY index_code').fetchall(), sep='\\n'); c.close()"
```

Windows PowerShell：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_code,COUNT(DISTINCT stock_code) AS count,MIN(effective_date),MAX(effective_date) FROM index_components GROUP BY index_code').fetchall(), sep='\n'); c.close()"
```

### 查看成分股样例

Linux/macOS：

```bash
python3 -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_code,stock_code,stock_name,weight,effective_date FROM index_components ORDER BY index_code LIMIT 20').fetchall(), sep='\\n'); c.close()"
```

Windows PowerShell：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/institutional_holding.db'); print(*c.execute('SELECT index_code,stock_code,stock_name,weight,effective_date FROM index_components ORDER BY index_code LIMIT 20').fetchall(), sep='\n'); c.close()"
```

## 成功判断

正常情况下应满足：

- 日志最后出现 `Ingestion completed.`
- 四个指数在 `indices` 表中都有记录
- 各指数的成分股数量大于 0
- `index_components` 表中能查询到股票代码和名称

不同指数之间可能有重复股票，因此各指数数量相加后会大于去重后的股票总数。

## 常见问题与定位顺序

### 没有获取到任何成分股

先检查 `index_test.log` 和 `test_api.py` 的输出，再判断是网络不可用、AkShare 接口变更、返回列名变化还是数据库写入失败。后续 `holders`、`northbound` 和 `prices` 都依赖 `index_components`，没有成分股时不应继续排查分析模块。

### 分析没有生成结果

依次确认：

1. `top_holders` 是否至少包含两个 `report_date`
2. `holder_type` 是否已经分类
3. `holding_changes_summary` 是否有当前报告期数据
4. `daily_prices` 是否有价格；若没有，确认是否处于 `DEMO_MODE`

### 需要清理测试数据

以下脚本会删除数据，使用前确认数据库路径和数据是否需要保留：

```powershell
python clear_analysis.py
python clear_tables.py
```

首次测试不建议执行清理脚本。生产数据与测试数据应使用不同的数据库文件。

## 后续自动化测试建议

手工端到端链路稳定后，再补充带断言的 pytest：

- `holder_classifier`：精确、包含、正则和未匹配规则
- 各采集模块的 DataFrame 标准化函数
- `db_manager`：建表、查询、upsert 和唯一约束
- `holding_changes`：新进、退出、增持、减持和市值计算
- `alerting.rules`：阈值边界和国家队新进/退出

联网采集测试应使用 mock 或固定 DataFrame，不应依赖 AkShare 网络；测试数据库也应使用临时 SQLite 文件，避免污染 `data\institutional_holding.db`。
