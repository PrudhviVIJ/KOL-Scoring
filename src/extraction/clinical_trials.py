"""
ClinicalTrials.gov extraction component for the KOL Ranking Pipeline.

Design goals:
- Pure parsing functions that can be unit-tested without network access.
- Runtime configuration through environment variables / CLI arguments.
- No global mutable result lists.
- Dedicated output directory.
- Retries and session reuse for API requests.
- Fail loudly on source errors instead of silently producing incomplete data.
- Suitable for GitHub Actions CI and later Airflow orchestration.

Environment variables:
    CTG_SEARCH_TERMS       Comma-separated terms. Defaults to psychedelic terms.
    CTG_OUTPUT_DIR         Default: outputs/clinical_trials
    CTG_PAGE_SIZE          Default: 1000
    CTG_REQUEST_DELAY      Default: 0.25
    CTG_RETRIES            Default: 5
    CTG_TIMEOUT            Default: 120
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from tqdm import tqdm


BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

DEFAULT_SEARCH_TERMS = (
    "psilocybin",
    "MDMA",
    "ketamine",
    "DMT",
    "ayahuasca",
    "ibogaine",
    "mescaline",
    "LSD",
)


@dataclass(frozen=True)
class ClinicalTrialsConfig:
    """Runtime configuration for the ClinicalTrials.gov component."""

    search_terms: tuple[str, ...] = DEFAULT_SEARCH_TERMS
    page_size: int = 1000
    request_delay: float = 0.25
    retries: int = 5
    timeout: int = 120
    output_dir: Path = Path("outputs/clinical_trials")
    output_file: str = "ClinicalTrials_Psychedelics.xlsx"

    @classmethod
    def from_env(cls) -> "ClinicalTrialsConfig":
        raw_terms = os.getenv("CTG_SEARCH_TERMS", "")

        if raw_terms.strip():
            search_terms = tuple(
                term.strip()
                for term in raw_terms.split(",")
                if term.strip()
            )
        else:
            search_terms = DEFAULT_SEARCH_TERMS

        return cls(
            search_terms=search_terms,
            page_size=int(os.getenv("CTG_PAGE_SIZE", "1000")),
            request_delay=float(os.getenv("CTG_REQUEST_DELAY", "0.25")),
            retries=int(os.getenv("CTG_RETRIES", "5")),
            timeout=int(os.getenv("CTG_TIMEOUT", "120")),
            output_dir=Path(
                os.getenv(
                    "CTG_OUTPUT_DIR",
                    "outputs/clinical_trials",
                )
            ),
            output_file=os.getenv(
                "CTG_OUTPUT_FILE",
                "ClinicalTrials_Psychedelics.xlsx",
            ),
        )

    def validate(self) -> None:
        if not self.search_terms:
            raise ValueError("At least one ClinicalTrials.gov search term is required.")

        if self.page_size <= 0:
            raise ValueError("page_size must be greater than zero.")

        if self.retries <= 0:
            raise ValueError("retries must be greater than zero.")

        if self.timeout <= 0:
            raise ValueError("timeout must be greater than zero.")


def safe_get(obj: Any, *keys: str) -> Any:
    """Safely traverse nested dictionaries."""
    current = obj

    for key in keys:
        if current is None:
            return None

        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None

    return current


def list_to_text(value: Any) -> str:
    if not value:
        return ""

    if isinstance(value, list):
        return "; ".join(str(x) for x in value if x is not None)

    return str(value)


def unique_join(values: list[Any]) -> str:
    """Join non-empty values while preserving first-seen order."""
    seen: set[str] = set()
    cleaned: list[str] = []

    for value in values:
        text = str(value).strip()

        if not text:
            continue

        key = text.lower()

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(text)

    return "; ".join(cleaned)


def first_text(*values: Any) -> str:
    """Return the first meaningful textual value."""
    for value in values:
        if isinstance(value, dict):
            value = first_text(
                value.get("name", ""),
                value.get("fullName", ""),
                value.get("title", ""),
                value.get("label", ""),
            )

        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip()

            if value:
                return value

        elif value:
            return str(value).strip()

    return ""


def get_contact_name(contact: Any) -> str:
    if not isinstance(contact, dict):
        return ""

    return first_text(
        contact.get("name", ""),
        contact.get("fullName", ""),
        contact.get("contactName", ""),
        contact.get("investigatorFullName", ""),
        contact.get("personName", ""),
    )


def get_contact_role(contact: Any) -> str:
    if not isinstance(contact, dict):
        return ""

    return first_text(
        contact.get("role", ""),
        contact.get("title", ""),
        contact.get("contactType", ""),
        contact.get("investigatorTitle", ""),
        contact.get("roleClass", ""),
    )


def get_contact_affiliation(contact: Any) -> str:
    if not isinstance(contact, dict):
        return ""

    return first_text(
        contact.get("affiliation", ""),
        contact.get("investigatorAffiliation", ""),
        contact.get("organization", ""),
        contact.get("organizationName", ""),
    )


def infer_center_type(
    location_texts: set[str],
    site_count: int | None,
) -> str:
    """Classify the study as single-center/multicenter/unknown."""
    if site_count is not None:
        try:
            site_count = int(site_count)

            if site_count > 1:
                return "Multicenter"

            if site_count == 1:
                return "Single center"

        except (TypeError, ValueError):
            pass

    if len(location_texts) > 1:
        return "Multicenter"

    if len(location_texts) == 1:
        return "Single center"

    return "Unknown"


class ClinicalTrialsClient:
    """HTTP client for ClinicalTrials.gov API v2."""

    def __init__(
        self,
        config: ClinicalTrialsConfig,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()

        self.session.headers.update(
            {
                "User-Agent": (
                    "KOL-Ranking-Pipeline/1.0 "
                    "(ClinicalTrials.gov)"
                )
            }
        )

    def request_with_retry(
        self,
        url: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(self.config.retries):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.config.timeout,
                )

                if response.status_code == 429:
                    wait = min(60, 2 ** attempt)

                    logging.warning(
                        "ClinicalTrials.gov rate limit (429). "
                        "Waiting %s seconds.",
                        wait,
                    )

                    time.sleep(wait)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.RequestException as exc:
                last_error = exc
                wait = min(60, 2 ** attempt)

                logging.warning(
                    "ClinicalTrials.gov request failed "
                    "(attempt %s/%s): %s. Retrying in %s seconds.",
                    attempt + 1,
                    self.config.retries,
                    exc,
                    wait,
                )

                time.sleep(wait)

        raise RuntimeError(
            f"ClinicalTrials.gov request failed after "
            f"{self.config.retries} attempts."
        ) from last_error

    def get_studies(self, search_term: str) -> list[dict]:
        """Fetch all studies matching one search term."""
        studies: list[dict] = []
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {
                "query.intr": search_term,
                "pageSize": self.config.page_size,
                "format": "json",
            }

            if page_token:
                params["pageToken"] = page_token

            data = self.request_with_retry(BASE_URL, params)

            batch = data.get("studies", [])

            if not isinstance(batch, list):
                raise ValueError(
                    "Unexpected ClinicalTrials.gov response: "
                    "'studies' is not a list."
                )

            studies.extend(batch)

            logging.info(
                "%s: %s studies collected",
                search_term,
                f"{len(studies):,}",
            )

            page_token = data.get("nextPageToken")

            if not page_token:
                break

            time.sleep(self.config.request_delay)

        return studies


def parse_study(study: dict) -> dict:
    """Convert one ClinicalTrials.gov study JSON object into one flat record."""
    protocol = study.get("protocolSection", {})

    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {})
    design = protocol.get("designModule", {})
    conditions = protocol.get("conditionsModule", {})
    arms = protocol.get("armsInterventionsModule", {})
    eligibility = protocol.get("eligibilityModule", {})
    contacts = protocol.get("contactsLocationsModule", {})

    overall_pi = ""
    overall_pi_title = ""
    overall_pi_affiliation = ""

    responsible_party = sponsor.get("responsibleParty", {})

    if responsible_party.get("type") == "PRINCIPAL_INVESTIGATOR":
        overall_pi = first_text(
            responsible_party.get("investigatorFullName", ""),
            responsible_party.get("name", ""),
            responsible_party.get("fullName", ""),
        )

        overall_pi_title = first_text(
            responsible_party.get("investigatorTitle", ""),
            responsible_party.get("title", ""),
        )

        overall_pi_affiliation = first_text(
            responsible_party.get("investigatorAffiliation", ""),
            responsible_party.get("affiliation", ""),
            responsible_party.get("organization", ""),
        )

    if not overall_pi:
        overall_pi = first_text(
            safe_get(
                protocol,
                "sponsorCollaboratorsModule",
                "responsibleParty",
                "investigatorFullName",
            ),
            safe_get(
                protocol,
                "oversightModule",
                "responsibleParty",
                "investigatorFullName",
            ),
        )

    site_pis: list[str] = []
    sub_investigators: list[str] = []
    location_texts: set[str] = set()

    def record_contact(
        contact: dict,
        location_label: str = "",
    ) -> None:
        name = get_contact_name(contact)
        role = get_contact_role(contact).upper()
        affiliation = get_contact_affiliation(contact)

        if not name:
            return

        parts = [
            name,
            affiliation,
            location_label,
        ]

        text = " | ".join(
            part for part in parts if part
        )

        if role == "PRINCIPAL_INVESTIGATOR":
            site_pis.append(text)

        elif role in {
            "SUB_INVESTIGATOR",
            "CO_INVESTIGATOR",
            "INVESTIGATOR",
        }:
            sub_investigators.append(text)

    for contact in contacts.get("centralContacts", []):
        record_contact(contact, "Central Contact")

    for contact in contacts.get("overallContacts", []):
        record_contact(contact, "Overall Contact")

    locations = contacts.get("locations", [])

    for location in locations:
        facility = first_text(
            location.get("facility", ""),
            safe_get(location, "facility", "name"),
        )

        city = first_text(
            location.get("city", ""),
            safe_get(location, "facility", "city"),
        )

        country = first_text(
            location.get("country", ""),
            safe_get(location, "facility", "country"),
        )

        state = first_text(
            location.get("state", ""),
            safe_get(location, "facility", "state"),
        )

        location_label = " | ".join(
            part
            for part in [facility, city, state, country]
            if part
        )

        if location_label:
            location_texts.add(location_label)

        for contact in location.get("contacts", []):
            record_contact(contact, location_label)

    nct_number = ident.get("nctId", "")

    return {
        "NCT Number": nct_number,
        "Study Title": ident.get("briefTitle", ""),
        "Official Title": ident.get("officialTitle", ""),
        "Sponsor": safe_get(sponsor, "leadSponsor", "name"),
        "Collaborators": unique_join(
            [
                x.get("name")
                for x in sponsor.get("collaborators", [])
                if isinstance(x, dict)
            ]
        ),
        "Overall Status": status.get("overallStatus", ""),
        "Study Type": design.get("studyType", ""),
        "Phase": list_to_text(design.get("phases", [])),
        "Enrollment": safe_get(
            design,
            "enrollmentInfo",
            "count",
        ),
        "Conditions": list_to_text(
            conditions.get("conditions", [])
        ),
        "Interventions": unique_join(
            [
                x.get("name")
                for x in arms.get("interventions", [])
                if isinstance(x, dict)
            ]
        ),
        "Sex": eligibility.get("sex", ""),
        "Minimum Age": eligibility.get("minimumAge", ""),
        "Maximum Age": eligibility.get("maximumAge", ""),
        "Start Date": safe_get(
            status,
            "startDateStruct",
            "date",
        ),
        "Primary Completion": safe_get(
            status,
            "primaryCompletionDateStruct",
            "date",
        ),
        "Completion Date": safe_get(
            status,
            "completionDateStruct",
            "date",
        ),
        "Trial Principal Investigator": overall_pi,
        "Trial PI Title": overall_pi_title,
        "Trial PI Affiliation": overall_pi_affiliation,
        "Center Type": infer_center_type(
            location_texts,
            len(locations),
        ),
        "Number of Sites": len(locations),
        "Site Principal Investigators": unique_join(site_pis),
        "Sub-Investigator / Co-Investigator": unique_join(
            sub_investigators
        ),
        "Study URL": (
            f"https://clinicaltrials.gov/study/{nct_number}"
            if nct_number
            else ""
        ),
    }


def build_dataframe(records: list[dict]) -> pd.DataFrame:
    """Build and validate the final ClinicalTrials dataframe."""
    df = pd.DataFrame(records)

    if df.empty:
        return df

    if "NCT Number" not in df.columns:
        raise ValueError("Output is missing required column: NCT Number")

    df = df.drop_duplicates(
        subset=["NCT Number"],
        keep="first",
    ).reset_index(drop=True)

    return df


def export_excel(
    df: pd.DataFrame,
    output_dir: Path,
    output_file: str,
) -> Path:
    """Write the final trial dataset to Excel."""
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / output_file
    df.to_excel(output_path, index=False)

    return output_path


def run_pipeline(config: ClinicalTrialsConfig) -> Path:
    """Run the complete ClinicalTrials.gov extraction component."""
    config.validate()

    client = ClinicalTrialsClient(config)
    all_records: list[dict] = []

    logging.info(
        "Starting ClinicalTrials.gov pipeline with %s search terms.",
        len(config.search_terms),
    )

    for term in config.search_terms:
        logging.info("Searching ClinicalTrials.gov: %s", term)

        # A source/API failure is allowed to fail the pipeline.
        # This prevents CI/CD from publishing silently incomplete data.
        studies = client.get_studies(term)

        logging.info(
            "%s: %s raw studies returned",
            term,
            f"{len(studies):,}",
        )

        for study in tqdm(studies, desc=term):
            try:
                all_records.append(parse_study(study))
            except Exception:
                logging.exception(
                    "Failed to parse one study for search term '%s'.",
                    term,
                )
                raise

    df = build_dataframe(all_records)

    if df.empty:
        raise RuntimeError(
            "ClinicalTrials.gov returned no usable study records."
        )

    output_path = export_excel(
        df,
        config.output_dir,
        config.output_file,
    )

    logging.info(
        "ClinicalTrials.gov pipeline completed successfully."
    )
    logging.info(
        "Unique trials: %s",
        f"{len(df):,}",
    )
    logging.info(
        "Output: %s",
        output_path,
    )

    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the ClinicalTrials.gov component "
            "of the KOL Ranking Pipeline."
        )
    )

    parser.add_argument(
        "--search-terms",
        help="Comma-separated search terms.",
    )

    parser.add_argument("--page-size", type=int)
    parser.add_argument("--request-delay", type=float)
    parser.add_argument("--output-dir")
    parser.add_argument("--output-file")

    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    args = parse_args()
    env_config = ClinicalTrialsConfig.from_env()

    search_terms = env_config.search_terms

    if args.search_terms:
        search_terms = tuple(
            term.strip()
            for term in args.search_terms.split(",")
            if term.strip()
        )

    config = ClinicalTrialsConfig(
        search_terms=search_terms,
        page_size=args.page_size or env_config.page_size,
        request_delay=(
            args.request_delay
            if args.request_delay is not None
            else env_config.request_delay
        ),
        retries=env_config.retries,
        timeout=env_config.timeout,
        output_dir=(
            Path(args.output_dir)
            if args.output_dir
            else env_config.output_dir
        ),
        output_file=args.output_file or env_config.output_file,
    )

    start = time.time()

    try:
        run_pipeline(config)
    except Exception:
        logging.exception(
            "ClinicalTrials.gov pipeline failed."
        )
        raise
    finally:
        logging.info(
            "Total runtime: %.2f minutes",
            (time.time() - start) / 60,
        )


if __name__ == "__main__":
    main()