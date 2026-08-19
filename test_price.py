import sys
sys.path.insert(0, '.')
import akshare as ak
import pandas as pd

# 测试价格获取
stock_code = "000001"
date = "2025-03-31"

try:
    df = ak.stock_zh_a_hist(symbol=stock_code, period="daily",
                            start_date="20250320", end_date="20250331", adjust="qfq")
    print(f"Shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    print(df.head())
    
    # 尝试获取收盘价
    if not df.empty:
        close = df.iloc[-1].get("收盘")
        print(f"\n收盘价: {close}")
except Exception as e:
    print(f"Error: {e}")
