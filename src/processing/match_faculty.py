from __future__ import annotations

from typing import Any

import pandas as pd

from src.processing.normalize_names import (
    normalize_faculty_frame,
    normalize_name,
    normalize_name_series,
    parse_name_list,
)


def _explode_names(df: pd.DataFrame, source_col: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        names = parse_name_list(row.get(source_col, ""))
        if not names:
            names = [row.get(source_col, "")]
        for name in names:
            if not str(name).strip():
                continue
            record = row.to_dict()
            record["Matched Person Name"] = str(name).strip()
            record["Normalized Name"] = normalize_name(name)
            rows.append(record)

    return pd.DataFrame(rows)


def match_faculty_to_records(
    records_df: pd.DataFrame,
    faculty_df: pd.DataFrame,
    record_name_col: str,
    faculty_name_col: str = "Faculty Name",
) -> pd.DataFrame:
    """Attach faculty metadata to any record table with person names."""
    if records_df.empty or faculty_df.empty:
        return records_df.copy()

    faculty = normalize_faculty_frame(faculty_df, [faculty_name_col])
    if record_name_col not in records_df.columns:
        raise ValueError(f"Column not found: {record_name_col}")

    exploded = _explode_names(records_df, record_name_col)
    if exploded.empty:
        return records_df.copy()

    merged = exploded.merge(
        faculty,
        on="Normalized Name",
        how="left",
        suffixes=("", "_faculty"),
    )
    merged["Faculty Matched"] = merged["Normalized Name"].isin(
        faculty["Normalized Name"]
    )
    merged["Match Confidence"] = merged["Faculty Matched"].map(
        lambda matched: "Exact" if matched else "Unmatched"
    )

    return merged


def match_faculty(
    records_df: pd.DataFrame,
    faculty_df: pd.DataFrame,
    record_name_col: str = "Canonical Author Name",
    faculty_name_col: str = "Faculty Name",
) -> pd.DataFrame:
    return match_faculty_to_records(
        records_df=records_df,
        faculty_df=faculty_df,
        record_name_col=record_name_col,
        faculty_name_col=faculty_name_col,
    )
