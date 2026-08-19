import pandas as pd

from src.processing.normalize_names import (
    apply_name_map,
    load_name_map,
    normalize_faculty_frame,
    normalize_name,
    normalize_name_series,
    parse_name_list,
)


def test_normalize_name_strips_degrees_and_reorders():
    assert normalize_name("John A. Smith, MD") == "SMITH JOHN A"
    assert normalize_name("Jane Doe, Ph.D.") == "DOE JANE"


def test_normalize_name_series():
    series = pd.Series(["John Smith", None, "Jane Doe"])

    result = normalize_name_series(series)

    assert list(result) == ["SMITH JOHN", "", "DOE JANE"]


def test_parse_name_list():
    assert parse_name_list("John Smith; Jane Doe | Alex Kim") == [
        "John Smith",
        "Jane Doe",
        "Alex Kim",
    ]


def test_load_name_map_and_apply_name_map(tmp_path):
    path = tmp_path / "names.json"
    path.write_text(
        '{"John Smith": "J. Smith", "Jane Doe": "J. Doe"}',
        encoding="utf-8",
    )

    name_map = load_name_map(path)

    assert apply_name_map("John Smith", name_map) == "SMITH J"
    assert apply_name_map("Unknown Person", name_map) == "PERSON UNKNOWN"


def test_normalize_faculty_frame_deduplicates():
    df = pd.DataFrame(
        {
            "Faculty Name": [
                "John Smith",
                "John Smith",
                "Jane Doe",
                "",
            ],
            "Department": [
                "Psychiatry",
                "Psychiatry",
                "Neurology",
                "Ignored",
            ],
        }
    )

    result = normalize_faculty_frame(df)

    assert list(result["Normalized Name"]) == ["SMITH JOHN", "DOE JANE"]
    assert len(result) == 2

