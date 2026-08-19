from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd

DEGREES = {
    "MD",
    "M.D",
    "DO",
    "D.O",
    "PHD",
    "PH.D",
    "PHD.",
    "MSC",
    "M.SC",
    "MS",
    "MA",
    "MBA",
    "MPH",
    "FRCP",
    "FACP",
    "FRCPC",
    "DDS",
    "DMD",
    "RN",
    "BSC",
    "B.SC",
    "BPHARM",
    "PHARMD",
    "DVM",
    "MBBS",
    "MB",
    "BS",
    "BDS",
    "JD",
    "ESQ",
    "FRS",
    "FRCPCH",
    "FAAN",
    "FACC",
    "FACS",
    "FACE",
    "MRCP",
    "MRCPCH",
    "PROF",
    "PROFESSOR",
    "DR",
}


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def normalize_name(value: Any) -> str:
    """Create a stable canonical representation for a person name."""
    original = _to_text(value)
    if not original.strip():
        return ""

    text = original
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"\(.*?\)", "", text)

    if "," in text:
        base, suffix = text.split(",", 1)
        suffix_compact = re.sub(r"[^A-Za-z]", "", suffix).upper()

        if suffix_compact and any(
            suffix_compact.startswith(degree.replace(".", "").upper())
            for degree in DEGREES
        ):
            text = base

    text = text.replace(",", " ")
    text = text.replace(".", " ")
    text = text.replace("'", "")
    text = re.sub(r"[^A-Za-z0-9\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    if "," not in original and text == text.upper():
        return text

    tokens = []
    for token in text.split():
        if token.upper() in DEGREES:
            continue
        tokens.append(token)

    if not tokens:
        return ""

    if len(tokens) == 1:
        return tokens[0].upper()

    first = tokens[0].upper()
    last = tokens[-1].upper()
    middle = " ".join(token[0].upper() for token in tokens[1:-1] if token)

    if middle:
        return f"{last} {first} {middle}"
    return f"{last} {first}"


def normalize_name_series(series: pd.Series) -> pd.Series:
    return series.fillna("").map(normalize_name)


def load_name_map(path: str | Path) -> dict[str, str]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    with file_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return {
        normalize_name(key): normalize_name(value)
        for key, value in data.items()
        if normalize_name(key) and normalize_name(value)
    }


def apply_name_map(value: Any, name_map: dict[str, str]) -> str:
    canonical = normalize_name(value)
    if not canonical:
        return ""
    return name_map.get(canonical, canonical)


def normalize_faculty_frame(
    df: pd.DataFrame,
    name_columns: list[str] | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    result = df.copy()
    candidates = name_columns or [
        "Faculty Name",
        "Name",
        "Full Name",
        "Provider Name",
        "Physician Name",
    ]
    source_col = next((col for col in candidates if col in result.columns), None)
    if source_col is None:
        raise ValueError("No faculty name column found.")
    result["Normalized Name"] = normalize_name_series(result[source_col])
    result = result[result["Normalized Name"] != ""].copy()
    result = result.drop_duplicates(subset=["Normalized Name"])
    return result.reset_index(drop=True)


def parse_name_list(value: Any) -> list[str]:
    text = _to_text(value).strip()
    if not text:
        return []
    parts = re.split(r"\s*[;|]\s*", text)
    return [part.strip() for part in parts if part.strip()]
