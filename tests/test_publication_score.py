import pandas as pd
from xml.etree import ElementTree as ET

from pubmed_ci_ready import (
    build_author_mapping_df,
    build_author_summary,
    build_publication_df,
    canonical_author,
    detect_corresponding,
)


def test_canonical_author():
    assert canonical_author("John A. Smith, MD") == "SMITH JOHN A"
    assert canonical_author("Jane Doe, Ph.D.") == "DOE JANE"


def test_canonical_author_empty():
    assert canonical_author("") == ""
    assert canonical_author(None) == ""


def test_detect_corresponding():
    authors = [
        {
            "canonical": "SMITH JOHN",
            "affiliations": ["Department of X, john@example.com"],
        },
        {
            "canonical": "DOE JANE",
            "affiliations": ["Department of Y"],
        },
    ]

    assert detect_corresponding(authors) == {"SMITH JOHN"}


def test_build_publication_df_deduplicates():
    records = [
        {"PMID": "1", "Publication Year": 2026},
        {"PMID": "1", "Publication Year": 2026},
        {"PMID": "2", "Publication Year": 2025},
    ]

    df = build_publication_df(records)

    assert len(df) == 2
    assert df.iloc[0]["PMID"] == "1"


def test_build_author_summary():
    records = [
        {
            "PMID": "1",
            "Publication Year": 2026,
            "Canonical Author Name": "SMITH JOHN",
            "Original Author Name": "John Smith",
            "Author Position": "First",
            "Is Corresponding": True,
            "Title": "Test publication",
        },
        {
            "PMID": "2",
            "Publication Year": 2024,
            "Canonical Author Name": "SMITH JOHN",
            "Original Author Name": "J. Smith",
            "Author Position": "Last",
            "Is Corresponding": False,
            "Title": "Another publication",
        },
    ]

    mapping = build_author_mapping_df(records)
    summary = build_author_summary(
        mapping,
        current_year=2026,
        recent_window=5,
    )

    row = summary.iloc[0]

    assert row["Canonical Author Name"] == "SMITH JOHN"
    assert row["Total Publications"] == 2
    assert row["Publications Last 5 Years"] == 2
    assert row["Latest Publication Year"] == 2026
    assert row["Years Since Last Publication"] == 0
    assert row["First Author Count"] == 1
    assert row["Last Author Count"] == 1