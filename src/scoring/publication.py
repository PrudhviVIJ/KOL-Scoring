from __future__ import annotations

import pandas as pd


DEFAULT_WEIGHTS = {
    "total_publications": 0.45,
    "recent_publications": 0.2,
    "first_author": 0.15,
    "last_author": 0.15,
    "corresponding_author": 0.05,
}


def _normalize(series: pd.Series) -> pd.Series:
    if not isinstance(series, pd.Series):
        series = pd.Series(series)
    numeric = pd.to_numeric(series, errors="coerce").fillna(0)
    max_value = numeric.max()
    if max_value <= 0:
        return numeric * 0
    return numeric / max_value


def score_publications(
    author_summary_df: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    if author_summary_df.empty:
        return author_summary_df.copy()

    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    result = author_summary_df.copy()

    recent = _normalize(
        result["Publications Last 5 Years"]
        if "Publications Last 5 Years" in result.columns
        else pd.Series([0] * len(result))
    )
    total = _normalize(
        result["Total Publications"]
        if "Total Publications" in result.columns
        else pd.Series([0] * len(result))
    )
    first = _normalize(
        result["First Author Count"]
        if "First Author Count" in result.columns
        else pd.Series([0] * len(result))
    )
    last = _normalize(
        result["Last Author Count"]
        if "Last Author Count" in result.columns
        else pd.Series([0] * len(result))
    )
    corr = _normalize(
        result["Corresponding Author Count"]
        if "Corresponding Author Count" in result.columns
        else pd.Series([0] * len(result))
    )

    raw = (
        total * weights["total_publications"]
        + recent * weights["recent_publications"]
        + first * weights["first_author"]
        + last * weights["last_author"]
        + corr * weights["corresponding_author"]
    )

    result["Publication Score"] = (raw * 100).round(2)
    result["Publication Rank"] = result["Publication Score"].rank(
        ascending=False,
        method="dense",
    ).astype(int)
    return result.sort_values(
        by=["Publication Score", "Canonical Author Name"],
        ascending=[False, True],
    ).reset_index(drop=True)


def aggregate_publication_scores(df: pd.DataFrame) -> pd.DataFrame:
    return score_publications(df)
