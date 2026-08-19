from __future__ import annotations

from datetime import datetime

import pandas as pd


def score_recency(
    df: pd.DataFrame,
    year_col: str = "Latest Publication Year",
    current_year: int | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    result = df.copy()
    current_year = current_year or datetime.now().year

    if year_col in result.columns:
        years_since = current_year - pd.to_numeric(
            result[year_col], errors="coerce"
        )
    else:
        years_since = pd.Series([current_year + 1] * len(result))
    years_since = years_since.fillna(current_year + 1)

    result["Years Since Last Publication"] = years_since
    result["Recency Score"] = (1 - (years_since / 10)).clip(lower=0) * 100
    return result
