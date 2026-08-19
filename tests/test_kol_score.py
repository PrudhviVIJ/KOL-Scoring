import pandas as pd

from src.scoring.kol_score import (
    build_personal_ranking,
    compute_kol_scores,
    load_scoring_weights,
)


def test_load_scoring_weights_defaults_when_missing(tmp_path):
    path = tmp_path / "missing.yaml"

    weights = load_scoring_weights(path)

    assert weights["publication"] == 0.4
    assert weights["nih"] == 0.2


def test_load_scoring_weights_from_simple_file(tmp_path):
    path = tmp_path / "scoring.yaml"
    path.write_text(
        "publication: 0.5\nnih: 0.1\nclinical_trials: 0.2\nsociety: 0.1\nrecency: 0.1\n",
        encoding="utf-8",
    )

    weights = load_scoring_weights(path)

    assert weights["publication"] == 0.5
    assert weights["nih"] == 0.1


def test_build_personal_ranking_orders_by_score():
    publication_scores = pd.DataFrame(
        {
            "Canonical Author Name": ["SMITH JOHN", "DOE JANE"],
            "Publication Score": [100, 50],
            "Total Publications": [10, 5],
            "Publications Last 5 Years": [4, 2],
            "Normalized Name": ["SMITH JOHN", "DOE JANE"],
            "Recency Score": [100, 80],
        }
    )

    ranking = build_personal_ranking(
        publication_scores=publication_scores,
        weights={
            "publication": 0.7,
            "nih": 0.1,
            "clinical_trials": 0.1,
            "society": 0.05,
            "recency": 0.05,
        },
    )

    assert list(ranking["Normalized Name"]) == ["SMITH JOHN", "DOE JANE"]
    assert ranking.iloc[0]["KOL Rank"] == 1
    assert ranking.iloc[0]["KOL Score"] >= ranking.iloc[1]["KOL Score"]


def test_compute_kol_scores_publication_only():
    publication_summary = pd.DataFrame(
        {
            "Canonical Author Name": ["SMITH JOHN", "DOE JANE"],
            "Original Author Variants": ["John Smith", "Jane Doe"],
            "Total Publications": [8, 2],
            "Publications Last 5 Years": [5, 1],
            "Latest Publication Year": [2026, 2024],
            "Years Since Last Publication": [0, 2],
            "First Author Count": [3, 1],
            "Middle Author Count": [1, 0],
            "Last Author Count": [2, 1],
            "Corresponding Author Count": [2, 0],
            "First+Corresponding Count": [1, 0],
            "Last+Corresponding Count": [1, 0],
            "PMIDs": ["1; 2", "3"],
            "Titles": ["Paper A || Paper B", "Paper C"],
        }
    )

    ranking = compute_kol_scores(publication_summary=publication_summary)

    assert not ranking.empty
    assert "KOL Score" in ranking.columns
    assert ranking.iloc[0]["Normalized Name"] == "SMITH JOHN"

