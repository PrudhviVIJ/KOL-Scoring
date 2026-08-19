from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.processing.normalize_names import normalize_name
from src.scoring.clinical_trials import score_clinical_trials
from src.scoring.nih import score_nih_projects
from src.scoring.publication import score_publications
from src.scoring.recency import score_recency
from src.scoring.society import score_society_engagement


DEFAULT_WEIGHTS = {
    "publication": 0.4,
    "nih": 0.2,
    "clinical_trials": 0.2,
    "society": 0.1,
    "recency": 0.1,
}


def load_scoring_weights(path: str | Path = "config/scoring.yaml") -> dict[str, float]:
    file_path = Path(path)
    if not file_path.exists():
        return DEFAULT_WEIGHTS.copy()

    raw_text = file_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return DEFAULT_WEIGHTS.copy()

    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw_text) or {}
        weights = data.get("weights", data)
        if isinstance(weights, dict):
            merged = DEFAULT_WEIGHTS.copy()
            for key, value in weights.items():
                try:
                    merged[key] = float(value)
                except (TypeError, ValueError):
                    continue
            return merged
    except Exception:
        pass

    # Minimal fallback parser for simple "key: value" files.
    weights = DEFAULT_WEIGHTS.copy()
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        try:
            weights[key] = float(value)
        except ValueError:
            continue
    return weights


def _ensure_name_column(df: pd.DataFrame, source_column: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    result = df.copy()
    result["Normalized Name"] = result[source_column].map(normalize_name)
    return result[result["Normalized Name"] != ""].copy()


def _rename_to_normalized(df: pd.DataFrame, source_column: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    result = _ensure_name_column(df, source_column)
    return result


def build_personal_ranking(
    publication_scores: pd.DataFrame,
    nih_scores: pd.DataFrame | None = None,
    trial_scores: pd.DataFrame | None = None,
    society_scores: pd.DataFrame | None = None,
    recency_scores: pd.DataFrame | None = None,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    frames: list[pd.DataFrame] = []

    if not publication_scores.empty:
        pub = publication_scores.copy()
        if "Canonical Author Name" in pub.columns:
            pub["Normalized Name"] = pub["Canonical Author Name"].map(normalize_name)
        pub = pub[pub["Normalized Name"] != ""].copy()
        frames.append(
            pub[
                [
                    "Normalized Name",
                    "Canonical Author Name",
                    "Publication Score",
                    "Total Publications",
                    "Publications Last 5 Years",
                ]
            ].rename(columns={"Canonical Author Name": "Display Name"})
        )

    if nih_scores is not None and not nih_scores.empty:
        nih = nih_scores.copy()
        frames.append(
            nih.rename(
                columns={
                    "Normalized Name": "Normalized Name",
                }
            )[
                [
                    "Normalized Name",
                    "NIH Score",
                    "Project Count",
                    "Award Amount",
                ]
            ]
        )

    if trial_scores is not None and not trial_scores.empty:
        trials = trial_scores.copy()
        frames.append(
            trials[
                [
                    "Normalized Name",
                    "Clinical Trial Score",
                    "Trial Count",
                ]
            ]
        )

    if society_scores is not None and not society_scores.empty:
        society = society_scores.copy()
        if "Normalized Name" not in society.columns:
            for candidate in [
                "Canonical Author Name",
                "Display Name",
                "Name",
            ]:
                if candidate in society.columns:
                    society["Normalized Name"] = society[candidate].map(normalize_name)
                    break
        if "Normalized Name" in society.columns:
            frames.append(
                society[
                    [
                        "Normalized Name",
                        "Society Score",
                    ]
                ]
            )

    if recency_scores is not None and not recency_scores.empty:
        recency = recency_scores.copy()
        if "Normalized Name" not in recency.columns:
            for candidate in [
                "Canonical Author Name",
                "Display Name",
                "Name",
            ]:
                if candidate in recency.columns:
                    recency["Normalized Name"] = recency[candidate].map(normalize_name)
                    break
        if "Normalized Name" in recency.columns:
            frames.append(
                recency[
                    [
                        "Normalized Name",
                        "Recency Score",
                    ]
                ]
            )

    if not frames:
        return pd.DataFrame()

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="Normalized Name", how="outer")

    for column in [
        "Publication Score",
        "NIH Score",
        "Clinical Trial Score",
        "Society Score",
        "Recency Score",
    ]:
        if column not in merged.columns:
            merged[column] = 0.0
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(0)

    merged["KOL Score"] = (
        merged["Publication Score"] * weights["publication"]
        + merged["NIH Score"] * weights["nih"]
        + merged["Clinical Trial Score"] * weights["clinical_trials"]
        + merged["Society Score"] * weights["society"]
        + merged["Recency Score"] * weights["recency"]
    ).round(2)

    merged["KOL Rank"] = merged["KOL Score"].rank(
        ascending=False,
        method="dense",
    ).astype(int)

    display_cols = [
        "Normalized Name",
        "Display Name",
        "KOL Score",
        "KOL Rank",
        "Publication Score",
        "NIH Score",
        "Clinical Trial Score",
        "Society Score",
        "Recency Score",
    ]
    for column in display_cols:
        if column not in merged.columns:
            merged[column] = ""

    return merged.sort_values(
        by=["KOL Score", "Normalized Name"],
        ascending=[False, True],
    ).reset_index(drop=True)


def compute_kol_scores(
    publication_summary: pd.DataFrame,
    nih_projects: pd.DataFrame | None = None,
    clinical_trials: pd.DataFrame | None = None,
    society_frame: pd.DataFrame | None = None,
    current_year: int | None = None,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    pub_scores = score_publications(publication_summary)
    pub_scores = _ensure_name_column(pub_scores, "Canonical Author Name")
    pub_scores = score_recency(pub_scores, current_year=current_year)

    nih_scores = (
        score_nih_projects(nih_projects) if nih_projects is not None else pd.DataFrame()
    )
    trial_scores = (
        score_clinical_trials(clinical_trials) if clinical_trials is not None else pd.DataFrame()
    )

    society_scores = (
        score_society_engagement(society_frame)
        if society_frame is not None
        else pd.DataFrame()
    )

    return build_personal_ranking(
        publication_scores=pub_scores,
        nih_scores=nih_scores,
        trial_scores=trial_scores,
        society_scores=society_scores,
        recency_scores=pub_scores[["Normalized Name", "Recency Score"]]
        if "Recency Score" in pub_scores.columns
        else None,
        weights=weights or load_scoring_weights(),
    )

