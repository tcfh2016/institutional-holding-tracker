from datetime import datetime, timedelta

from ingestion.institutional_research import (
    _normalize_research_df,
    fetch_institutional_research,
)


start_date = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")
print(f"=== institutional research since {start_date} ===")
try:
    df = fetch_institutional_research(start_date)
    print(f"Success! Shape: {df.shape}, Columns: {list(df.columns)}")
    if not df.empty:
        normalized = _normalize_research_df(df)
        print(f"Latest survey date: {normalized['survey_date'].max()}")
        print(normalized.head(3))
        print(normalized.tail(3))
    else:
        print("No records returned.")
except Exception as e:
    print(f"Error: {e}")
