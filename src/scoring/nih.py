from __future__ import annotations

from typing import Any

import pandas as pd

from src.processing.normalize_names import normalize_name, parse_name_list


DEFAULT_WEIGHTS = {
    "project_count": 0.5,
    "award_amount": 0.35,
    "principal_role": 0.15,
}


def _normalize(series: pd.Series) -> pd.Series:
    if not isinstance(series, pd.Series):
        series = pd.Series(series)
    numeric = pd.to_numeric(series, errors="coerce").fillna(0)
    max_value = numeric.max()
    if max_value <= 0:
        return numeric * 0
    return numeric / max_value


def _explode_people(df: pd.DataFrame, name_columns: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        for col in name_columns:
            if col not in row or pd.isna(row[col]):
                continue
            for name in parse_name_list(row[col]):
                normalized = normalize_name(name)
                if not normalized:
                    continue
                record = row.to_dict()
                record["Normalized Name"] = normalized
                record["Matched Person Name"] = str(name).strip()
                record["Source Role"] = col
                rows.append(record)
    return pd.DataFrame(rows)


def score_nih_projects(
    nih_df: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    if nih_df.empty:
        return nih_df.copy()

    weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    name_columns = [
        col
        for col in [
            "Contact PI",
            "Principal Investigators",
            "Co-Principal Investigators",
            "Co-Investigators",
            "Site Investigators",
            "Consultants",
        ]
        if col in nih_df.columns
    ]

    exploded = _explode_people(nih_df, name_columns)
    if exploded.empty:
        return pd.DataFrame()

    if "Award Amount" not in exploded.columns:
        exploded["Award Amount"] = 0
    exploded["Award Amount"] = pd.to_numeric(
        exploded["Award Amount"],
        errors="coerce",
    ).fillna(0)

    grouped = exploded.groupby("Normalized Name", as_index=False)
    summary = grouped.agg(
        Project_Count=("Project Number", "nunique"),
        Award_Amount=("Award Amount", "sum"),
        Principal_Count=("Source Role", lambda s: (s == "Contact PI").sum()),
    )
    summary = summary.rename(
        columns={
            "Project_Count": "Project Count",
            "Award_Amount": "Award Amount",
            "Principal_Count": "Principal Count",
        }
    )

    score = (
        _normalize(summary["Project Count"]) * weights["project_count"]
        + _normalize(summary["Award Amount"]) * weights["award_amount"]
        + _normalize(summary["Principal Count"]) * weights["principal_role"]
    )
    summary["NIH Score"] = (score * 100).round(2)
    summary["NIH Rank"] = summary["NIH Score"].rank(
        ascending=False,
        method="dense",
    ).astype(int)
    return summary.sort_values(
        by=["NIH Score", "Normalized Name"],
        ascending=[False, True],
    ).reset_index(drop=True)


def aggregate_nih_scores(df: pd.DataFrame) -> pd.DataFrame:
    return score_nih_projects(df)
