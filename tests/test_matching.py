import pandas as pd

from src.processing.match_faculty import match_faculty, match_faculty_to_records


def test_match_faculty_exact_match():
    records = pd.DataFrame(
        {
            "Canonical Author Name": ["SMITH JOHN", "DOE JANE"],
            "PMID": ["1", "2"],
        }
    )
    faculty = pd.DataFrame(
        {
            "Faculty Name": ["John Smith"],
            "Department": ["Psychiatry"],
        }
    )

    result = match_faculty(
        records,
        faculty,
        record_name_col="Canonical Author Name",
    )

    assert len(result) == 2
    assert bool(result.loc[0, "Faculty Matched"]) is True
    assert result.loc[0, "Match Confidence"] == "Exact"
    assert result.loc[1, "Match Confidence"] == "Unmatched"


def test_match_faculty_to_records_splits_multiple_names():
    records = pd.DataFrame(
        {
            "Principal Investigators": ["John Smith; Jane Doe"],
            "Project Number": ["R01TEST001"],
        }
    )
    faculty = pd.DataFrame(
        {
            "Faculty Name": ["Jane Doe"],
            "Department": ["Neurology"],
        }
    )

    result = match_faculty_to_records(
        records,
        faculty,
        record_name_col="Principal Investigators",
    )

    assert set(result["Matched Person Name"]) == {"John Smith", "Jane Doe"}
    assert result["Faculty Matched"].sum() == 1


def test_match_faculty_is_not_dependent_on_column_order():
    records = pd.DataFrame(
        {
            "Canonical Author Name": ["SMITH JOHN"],
            "PMID": ["1"],
        }
    )
    faculty = pd.DataFrame(
        {
            "Department": ["Psychiatry"],
            "Faculty Name": ["John Smith"],
        }
    )

    result = match_faculty(
        records,
        faculty,
        record_name_col="Canonical Author Name",
    )

    assert bool(result.loc[0, "Faculty Matched"]) is True
