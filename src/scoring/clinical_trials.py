from __future__ import annotations

from typing import Any

import pandas as pd

from src.processing.normalize_names import normalize_name, parse_name_list


DEFAULT_WEIGHTS = {
    "trial_count": 0.4,
    "multicenter": 0.2,
    "principal_role": 0.25,
    "recruiting": 0.15,
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


def score_clinical_trials(
    trial_df: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    if trial_df.empty:
        return trial_df.copy()

    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    name_columns = [
        col
        for col in [
            "Trial Principal Investigator",
            "Site Principal Investigators",
            "Sub-Investigator / Co-Investigator",
        ]
        if col in trial_df.columns
    ]

    exploded = _explode_people(trial_df, name_columns)
    if exploded.empty:
        return pd.DataFrame()

    if "Center Type" not in exploded.columns:
        exploded["Center Type"] = ""
    if "Overall Status" not in exploded.columns:
        exploded["Overall Status"] = ""
    exploded["Center Type"] = exploded["Center Type"].fillna("")
    exploded["Overall Status"] = exploded["Overall Status"].fillna("")

    grouped = exploded.groupby("Normalized Name", as_index=False)
    summary = grouped.agg(
        Trial_Count=("NCT Number", "nunique"),
        Multicenter_Count=("Center Type", lambda s: (s == "Multicenter").sum()),
        Principal_Count=("Source Role", lambda s: (s == "Trial Principal Investigator").sum()),
        Recruiting_Count=("Overall Status", lambda s: s.str.upper().eq("RECRUITING").sum()),
    )
    summary = summary.rename(
        columns={
            "Trial_Count": "Trial Count",
            "Multicenter_Count": "Multicenter Count",
            "Principal_Count": "Principal Count",
            "Recruiting_Count": "Recruiting Count",
        }
    )

    score = (
        _normalize(summary["Trial Count"]) * weights["trial_count"]
        + _normalize(summary["Multicenter Count"]) * weights["multicenter"]
        + _normalize(summary["Principal Count"]) * weights["principal_role"]
        + _normalize(summary["Recruiting Count"]) * weights["recruiting"]
    )
    summary["Clinical Trial Score"] = (score * 100).round(2)
    summary["Clinical Trial Rank"] = summary["Clinical Trial Score"].rank(
        ascending=False,
        method="dense",
    ).astype(int)
    return summary.sort_values(
        by=["Clinical Trial Score", "Normalized Name"],
        ascending=[False, True],
    ).reset_index(drop=True)


def aggregate_trial_scores(df: pd.DataFrame) -> pd.DataFrame:
    return score_clinical_trials(df)
