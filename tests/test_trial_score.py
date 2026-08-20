import pandas as pd

from src.extraction.clinical_trials import (
    build_dataframe,
    first_text,
    infer_center_type,
    list_to_text,
    parse_study,
    safe_get,
    unique_join,
)


def test_safe_get():
    obj = {"a": {"b": {"c": 123}}}

    assert safe_get(obj, "a", "b", "c") == 123
    assert safe_get(obj, "a", "missing") is None


def test_list_to_text():
    assert list_to_text(["Phase 1", "Phase 2"]) == "Phase 1; Phase 2"
    assert list_to_text([]) == ""
    assert list_to_text(None) == ""


def test_unique_join():
    values = ["PI", "pi", "", "Co-PI", "PI"]

    assert unique_join(values) == "PI; Co-PI"


def test_first_text():
    assert first_text("", None, "Study Name") == "Study Name"
    assert first_text({"name": "John Smith"}) == "John Smith"


def test_center_type():
    assert infer_center_type({"A"}, 1) == "Single center"
    assert infer_center_type({"A", "B"}, 2) == "Multicenter"
    assert infer_center_type(set(), 0) == "Unknown"


def test_parse_study():
    study = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT00000001",
                "briefTitle": "Example Psilocybin Trial",
                "officialTitle": "Example Official Title",
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "startDateStruct": {"date": "2026-01-01"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Example University"},
                "responsibleParty": {
                    "type": "PRINCIPAL_INVESTIGATOR",
                    "investigatorFullName": "John Smith",
                    "investigatorTitle": "Professor",
                    "investigatorAffiliation": "Example University",
                },
            },
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE1"],
                "enrollmentInfo": {"count": 20},
            },
            "conditionsModule": {
                "conditions": ["Depression"],
            },
            "armsInterventionsModule": {
                "interventions": [
                    {"name": "Psilocybin"},
                ],
            },
            "eligibilityModule": {
                "sex": "ALL",
                "minimumAge": "18 Years",
                "maximumAge": "65 Years",
            },
            "contactsLocationsModule": {
                "locations": [
                    {
                        "facility": "Example Hospital",
                        "city": "Boston",
                        "state": "Massachusetts",
                        "country": "United States",
                        "contacts": [
                            {
                                "name": "Jane Doe",
                                "role": "PRINCIPAL_INVESTIGATOR",
                                "affiliation": "Example Hospital",
                            }
                        ],
                    }
                ]
            },
        }
    }

    record = parse_study(study)

    assert record["NCT Number"] == "NCT00000001"
    assert record["Study Title"] == "Example Psilocybin Trial"
    assert record["Sponsor"] == "Example University"
    assert record["Trial Principal Investigator"] == "John Smith"
    assert record["Center Type"] == "Single center"
    assert record["Number of Sites"] == 1
    assert "Jane Doe" in record["Site Principal Investigators"]
    assert record["Study URL"].endswith("/NCT00000001")


def test_build_dataframe_deduplicates():
    records = [
        {"NCT Number": "NCT1", "Study Title": "A"},
        {"NCT Number": "NCT1", "Study Title": "A duplicate"},
        {"NCT Number": "NCT2", "Study Title": "B"},
    ]

    df = build_dataframe(records)

    assert len(df) == 2
    assert set(df["NCT Number"]) == {"NCT1", "NCT2"}