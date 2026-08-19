import pandas as pd

from nih_ci_ready import (
    build_dataframe,
    build_source_url,
    clean_excel_text,
    classify_role,
    collect_people,
    format_person_name,
    infer_center_type,
    parse_project,
)


def test_clean_excel_text():
    value = "  Hello\u00a0World\x00  "

    result = clean_excel_text(value)

    assert result == "Hello World"
    assert "\x00" not in result


def test_build_source_url():
    project = {"project_num": "R01AB123456"}

    assert build_source_url(project).endswith(
        "project_num=R01AB123456"
    )


def test_format_person_name():
    person = {
        "last_name": "Smith",
        "first_name": "John",
        "middle_initial": "A",
    }

    assert format_person_name(person) == "Smith, John A"


def test_classify_role():
    assert classify_role("Principal Investigator") == "principal"
    assert classify_role("Co-Investigator") == "co_investigator"
    assert classify_role("Co-Principal Investigator") == "co_principal"
    assert classify_role("Site Investigator") == "site"
    assert classify_role("Consultant") == "consultant"
    assert classify_role("Contact") == "contact"


def test_collect_people():
    project = {
        "principal_investigators": [
            {
                "last_name": "Smith",
                "first_name": "John",
                "role": "Principal Investigator",
                "organization": "Example University",
            }
        ]
    }

    people = collect_people(project)

    assert len(people) == 1
    assert people[0]["name"] == "Smith, John"
    assert people[0]["role"] == "Principal Investigator"


def test_infer_center_type():
    project = {
        "organization": {
            "org_name": "Example University",
        }
    }

    people = []

    assert infer_center_type(project, people) == "Single center"


def test_parse_project():
    project = {
        "project_num": "R01TEST001",
        "core_project_num": "TEST001",
        "project_title": "Psilocybin Research",
        "abstract_text": "Example abstract",
        "organization": {
            "org_name": "Example University",
            "org_city": "Boston",
            "org_state": "MA",
            "org_country": "USA",
        },
        "activity_code": "R01",
        "project_start_date": "2026-01-01",
        "project_end_date": "2030-12-31",
        "fiscal_year": 2026,
        "award_amount": 100000,
        "Matched Search Term": "psilocybin",
        "principal_investigators": [
            {
                "last_name": "Smith",
                "first_name": "John",
                "role": "Principal Investigator",
                "organization": "Example University",
            },
            {
                "last_name": "Doe",
                "first_name": "Jane",
                "role": "Co-Investigator",
                "organization": "Example University",
            },
        ],
        "agency_ic_fundings": [
            {"ic_name": "NIDA"}
        ],
    }

    record = parse_project(project)

    assert record["Project Number"] == "R01TEST001"
    assert record["Project Title"] == "Psilocybin Research"
    assert record["Organization"] == "Example University"
    assert record["Funding Institute"] == "NIDA"
    assert record["Matched Search Term"] == "psilocybin"
    assert "Smith, John" in record["Principal Investigators"]
    assert "Doe, Jane" in record["Co-Investigators"]
    assert record["Center Type"] == "Single center"


def test_build_dataframe_deduplicates_and_sorts():
    records = [
        {
            "Project Number": "P1",
            "Fiscal Year": 2024,
            "Project Title": "Older",
        },
        {
            "Project Number": "P1",
            "Fiscal Year": 2024,
            "Project Title": "Duplicate",
        },
        {
            "Project Number": "P2",
            "Fiscal Year": 2026,
            "Project Title": "Newer",
        },
    ]

    df = build_dataframe(records)

    assert len(df) == 2
    assert df.iloc[0]["Project Number"] == "P2"
    assert set(df["Project Number"]) == {"P1", "P2"}
