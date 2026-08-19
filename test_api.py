import akshare as ak

# 测试北向资金历史
print("=== stock_hsgt_hist_em('北向资金') ===")
try:
    df = ak.stock_hsgt_hist_em(symbol="北向资金")
    print(f"Success! Shape: {df.shape}, Columns: {list(df.columns)}")
    print(df.head(3))
    print(df.tail(3))
except Exception as e:
    print(f"Error: {e}")

# 测试个股北向
print("\n=== stock_hsgt_individual_em('000001') ===")
try:
    df = ak.stock_hsgt_individual_em(symbol="000001")
    print(f"Success! Shape: {df.shape}, Columns: {list(df.columns)}")
    print(df.head(3))
except Exception as e:
    print(f"Error: {e}")
