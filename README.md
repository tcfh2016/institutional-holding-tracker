# A股大机构持仓跟踪系统

跟踪国家队、保险资金、社保基金、公募基金、QFII等大机构，在沪深300、中证500、创业板指、科创50等核心指数成分股中的持仓变化，并记录机构调研行为。

## 功能特性

- 📊 **指数成分股采集**：自动获取沪深300、中证500、创业板指、科创50成分股
- 🏛️ **十大股东追踪**：采集十大股东和十大流通股东明细
- 🏷️ **智能股东识别**：基于关键词和正则的规则引擎，自动分类机构类型
- 📈 **持仓变化计算**：识别新进、退出、增持、减持，计算持股市值变动
- 🔬 **机构调研记录**：跟踪成分股近期机构调研次数和参与机构
- ⚠️ **预警规则**：国家队新进/退出、大幅调仓、连续多季变化等自动预警
- 📑 **季度报告**：自动生成 Markdown 格式的持仓变化分析报告
- 🖥️ **可视化看板**：Streamlit 交互式看板，支持趋势图、热力图、Treemap

## 项目结构

```
institutional_holding_tracker/
├── config/
│   └── settings.py          # 全局配置（指数代码、阈值、更新频率等）
├── data/                    # 数据目录（SQLite 数据库、日志、报告）
├── database/
│   ├── schema.sql           # 数据库表结构
│   └── db_manager.py        # 数据库连接与 CRUD
├── ingestion/
│   ├── base.py              # 采集基类（重试、延迟、日志）
│   ├── index_components.py  # 指数成分股采集
│   ├── top_holders.py       # 十大股东/流通股东采集
│   ├── institutional_research.py # 机构调研采集
│   └── market_data.py       # 行情与股本数据采集
├── cleansing/
│   └── holder_classifier.py # 股东识别与分类规则引擎
├── analysis/
│   └── holding_changes.py   # 持仓变化计算、指数汇总、趋势分析
├── reporting/
│   └── quarterly_report.py  # 季度 Markdown 报告生成
├── alerting/
│   └── rules.py             # 预警规则引擎
├── dashboard/
│   └── app.py               # Streamlit 可视化看板
├── main.py                  # 主入口：一键运行完整流水线
└── requirements.txt         # Python 依赖
```

## 快速开始

### 1. 安装依赖

```bash
cd institutional_holding_tracker
pip install -r requirements.txt
```

### 2. 初始化数据库

```bash
python main.py --init-only
```

### 3. 运行完整采集与分析流水线（演示模式：前50只成分股）

```bash
python main.py
```

> 注：完整采集 950 只成分股的十大股东数据耗时较长（约1-2小时），建议首次运行时使用演示模式，或分阶段执行。

### 4. 分阶段执行（推荐生产环境）

```bash
# 仅采集指数成分股
python main.py --stage init index

# 采集指数成分股并同步股票基础信息
python main.py --stage init index stocks

# 仅采集十大股东（会自动确保股票基础信息已同步）
python main.py --stage holders classify

# 采集最近 2 天全市场机构调研，已入库日期会自动跳过
python main.py --stage research

# 重新补齐指定日期的全市场机构调研记录
python main.py --stage research --research-start-date 20260818 --research-end-date 20260821

# 补齐过去只保存成分股时遗漏的非成分股调研记录
python main.py --stage research --research-full-market --research-start-date 20260531 --research-end-date 20260821

# 仅运行分析
python main.py --stage analyze report alert
```

### 5. 测试指南

指数成分股采集测试、跨平台命令、日志保存方式和数据库核验步骤请参考 [TESTING.md](TESTING.md)。

### 6. 启动看板

```bash
streamlit run dashboard/app.py
```

看板将自动在浏览器中打开，支持：
- 按报告期和机构类型筛选
- 个股历史持仓趋势图
- 国家队持仓 Treemap
- 全市场机构调研活跃个股排名
- 非指数成分股机构调研活跃候选
- 预警清单

## 数据源

本项目优先使用免费数据源：

| 数据内容 | 来源 |
|---|---|
| 指数成分股 | 中证指数官网 / 东方财富 (akshare) |
| 十大股东/流通股东 | 东方财富 (akshare) |
| 机构调研 | 东方财富公开接口 |
| 日度行情 | 东方财富 / Baostock (akshare) |
| 股票基础信息 | 东方财富 (akshare) |

如需使用 **Tushare Pro** 补充数据，在 `config/settings.py` 中填入 Token：

```python
TUSHARE_TOKEN = "your_token_here"
```

## 机构识别规则

默认支持的机构类型：

| 类型 | 识别关键词示例 |
|---|---|
| 证金公司 | 中国证券金融股份有限公司 |
| 汇金公司 | 中央汇金投资/资产管理有限责任公司 |
| 证金资管计划 | 中证金融资产管理计划 |
| 保险 | 中国人寿、中国平安、太平洋人寿等 |
| 社保基金 | 全国社保基金xxx组合、基本养老保险基金 |
| QFII | 高盛、摩根士丹利、瑞银、阿布达比投资局等 |
| 北向资金 | 香港中央结算有限公司 |
| 券商 | 中信证券、国泰君安、券商资管计划 |
| 信托 | 信托计划、集合资金信托 |

可通过 `cleansing/holder_classifier.py` 中的 `add_custom_rule()` 或修改 `holder_mappings` 表来扩展规则。

## 预警规则

| 规则 | 说明 | 级别 |
|---|---|---|
| 单股单机构大幅变动 | 变动市值占持仓市值比例 > 30% | 重要 |
| 国家队新进/退出 | 证金/汇金/证金资管出现新进或退出 | 紧急/重要 |
| 指数层面大幅调仓 | 某指数内某类机构合计变动 > 10亿 | 重要 |
| 连续增持/减持 | 连续2个季度同向变动 | 普通 |

阈值可在 `config/settings.py` 中调整。

## 注意事项

1. **数据完整性**：上市公司仅披露前十大股东，机构可能未进入前十，存在低估。
2. **披露延迟**：季报数据存在 1-2 个月的滞后。
3. **股东名称变化**：机构名称可能变更，需定期维护映射表。
4. **合规声明**：本项目仅用于研究，不构成投资建议。

## License

MIT License
