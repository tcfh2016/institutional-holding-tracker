from datetime import datetime, timedelta

from ingestion.institutional_research import fetch_institutional_research


start_date = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")
print(f"=== institutional research since {start_date} ===")
try:
    df = fetch_institutional_research(start_date)
    print(f"Success! Shape: {df.shape}, Columns: {list(df.columns)}")
    if not df.empty:
        print(f"Latest survey date: {df['调研日期'].max()}")
        print(df.head(3))
        print(df.tail(3))
    else:
        print("No records returned.")
except Exception as e:
    print(f"Error: {e}")
