from __future__ import annotations

import pandas as pd


DEFAULT_WEIGHTS = {
    "first_author": 0.25,
    "last_author": 0.25,
    "corresponding_author": 0.2,
    "leadership": 0.3,
}


def _normalize(series: pd.Series) -> pd.Series:
    if not isinstance(series, pd.Series):
        series = pd.Series(series)
    numeric = pd.to_numeric(series, errors="coerce").fillna(0)
    max_value = numeric.max()
    if max_value <= 0:
        return numeric * 0
    return numeric / max_value


def score_society_engagement(
    df: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    result = df.copy()

    leadership = None
    for column in [
        "Leadership Count",
        "Leadership Roles",
        "Society Leadership Count",
    ]:
        if column in result.columns:
            leadership = result[column]
            break
    if leadership is None:
        leadership = pd.Series([0] * len(result))

    first = result["First Author Count"] if "First Author Count" in result.columns else pd.Series([0] * len(result))
    last = result["Last Author Count"] if "Last Author Count" in result.columns else pd.Series([0] * len(result))
    corr = result["Corresponding Author Count"] if "Corresponding Author Count" in result.columns else pd.Series([0] * len(result))

    score = (
        _normalize(first) * weights["first_author"]
        + _normalize(last) * weights["last_author"]
        + _normalize(corr) * weights["corresponding_author"]
        + _normalize(leadership) * weights["leadership"]
    )
    result["Society Score"] = (score * 100).round(2)
    result["Society Rank"] = result["Society Score"].rank(
        ascending=False,
        method="dense",
    ).astype(int)
    return result.sort_values(
        by=["Society Score"],
        ascending=False,
    ).reset_index(drop=True)
