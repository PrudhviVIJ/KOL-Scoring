from __future__ import annotations

from typing import Any

import pandas as pd


def _join_unique(values: pd.Series) -> str:
    seen: set[str] = set()
    items: list[str] = []

    for value in values.dropna().astype(str):
        text = value.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)

    return "; ".join(items)


def _first_non_empty(values: pd.Series) -> Any:
    for value in values:
        if pd.isna(value):
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
        elif value is not None:
            return value
    return ""


def deduplicate_dataframe(
    df: pd.DataFrame,
    subset: list[str] | None = None,
) -> pd.DataFrame:
    """Collapse duplicate rows while preserving useful values."""
    if df.empty:
        return df.copy()

    working = df.copy()

    if subset is None:
        subset = [
            col
            for col in [
                "Normalized Name",
                "Canonical Author Name",
                "PMID",
                "Project Number",
                "NCT Number",
            ]
            if col in working.columns
        ]

    if not subset:
        return working.drop_duplicates().reset_index(drop=True)

    grouped = working.groupby(subset, dropna=False, as_index=False)
    aggregated: dict[str, Any] = {}

    for column in working.columns:
        if column in subset:
            continue
        if pd.api.types.is_numeric_dtype(working[column]):
            aggregated[column] = "max"
        else:
            aggregated[column] = _join_unique

    result = grouped.agg(aggregated)

    for column in result.columns:
        if result[column].dtype == object:
            result[column] = result[column].apply(
                lambda value: value if isinstance(value, str) else value
            )

    return result.reset_index(drop=True)


def deduplicate_by_key(
    df: pd.DataFrame,
    key: str,
) -> pd.DataFrame:
    if key not in df.columns:
        raise ValueError(f"Column not found: {key}")
    return deduplicate_dataframe(df, subset=[key])

