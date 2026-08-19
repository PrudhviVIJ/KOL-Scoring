"""
NIH RePORTER extraction component for the KOL Ranking Pipeline.

This module preserves the existing NIH extraction/parsing logic while making
the component suitable for:
    - Local execution
    - GitHub Actions CI
    - Later Airflow orchestration
    - Unit testing without NIH API calls
    - Environment-based configuration
    - Dedicated output directories

Environment variables:
    NIH_SEARCH_TERMS       Comma-separated terms
    NIH_OUTPUT_DIR         Default: outputs/nih
    NIH_OUTPUT_FILE        Default: NIH_Psychedelic_Grants.xlsx
    NIH_LIMIT              Default: 500
    NIH_REQUEST_DELAY      Default: 0.25
    NIH_RETRIES            Default: 5
    NIH_TIMEOUT            Default: 120
"""

from __future__ import annotations

import argparse
import logging
import os
import time
import unicodedata
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from tqdm import tqdm


API_URL = "https://api.reporter.nih.gov/v2/projects/search"
SOURCE_URL_TEMPLATE = (
    "https://reporter.nih.gov/search?project_num={project_num}"
)

DEFAULT_SEARCH_TERMS = (
    "psilocybin",
    "MDMA",
    "ketamine",
    "lysergic acid diethylamide",
    "LSD",
    "DMT",
    "ayahuasca",
    "mescaline",
    "ibogaine",
    "psychedelic",
)

PERSON_CONTAINER_KEYS = [
    "principal_investigators",
    "investigators",
    "project_investigators",
    "project_personnel",
    "project_team",
    "study_team",
    "team_members",
    "personnel",
]

PERSON_NAME_KEYS = [
    "last_name",
    "family_name",
    "surname",
    "first_name",
    "given_name",
    "middle_name",
    "full_name",
    "name",
    "person_name",
    "investigator_name",
    "pi_name",
    "contact_pi_name",
]

PERSON_ROLE_KEYS = [
    "role",
    "role_name",
    "investigator_role",
    "project_role",
    "position",
    "type",
]

PERSON_ORG_KEYS = [
    "organization",
    "org_name",
    "institution",
    "affiliation",
    "site_name",
    "site",
]


@dataclass(frozen=True)
class NIHConfig:
    """Runtime configuration for the NIH component."""

    search_terms: tuple[str, ...] = DEFAULT_SEARCH_TERMS
    limit: int = 500
    request_delay: float = 0.25
    retries: int = 5
    timeout: int = 120
    output_dir: Path = Path("outputs/nih")
    output_file: str = "NIH_Psychedelic_Grants.xlsx"

    @classmethod
    def from_env(cls) -> "NIHConfig":
        raw_terms = os.getenv("NIH_SEARCH_TERMS", "")

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
            limit=int(os.getenv("NIH_LIMIT", "500")),
            request_delay=float(
                os.getenv("NIH_REQUEST_DELAY", "0.25")
            ),
            retries=int(os.getenv("NIH_RETRIES", "5")),
            timeout=int(os.getenv("NIH_TIMEOUT", "120")),
            output_dir=Path(
                os.getenv(
                    "NIH_OUTPUT_DIR",
                    "outputs/nih",
                )
            ),
            output_file=os.getenv(
                "NIH_OUTPUT_FILE",
                "NIH_Psychedelic_Grants.xlsx",
            ),
        )

    def validate(self) -> None:
        if not self.search_terms:
            raise ValueError(
                "At least one NIH search term is required."
            )

        if self.limit <= 0:
            raise ValueError("NIH limit must be greater than zero.")

        if self.retries <= 0:
            raise ValueError(
                "NIH retries must be greater than zero."
            )

        if self.timeout <= 0:
            raise ValueError(
                "NIH timeout must be greater than zero."
            )


# ============================================================
# CLEAN EXCEL TEXT
# ============================================================

def clean_excel_text(value: Any) -> str:
    """
    Clean text so it can safely be written to Excel.

    Removes:
    - Illegal XML characters
    - ASCII control characters
    - Broken unicode
    - Excess whitespace
    """
    if value is None:
        return ""

    if not isinstance(value, str):
        value = str(value)

    value = unicodedata.normalize("NFKC", value)

    replacements = {
        "\u00bf": " ",
        "\ufffd": " ",
        "\xa0": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = ILLEGAL_CHARACTERS_RE.sub("", value)

    value = "".join(
        ch
        for ch in value
        if ord(ch) >= 32 or ch in "\n\r\t"
    )

    value = re.sub(r"\s+", " ", value)

    return value.strip()


# ============================================================
# NIH API CLIENT
# ============================================================

class NIHReporterClient:
    """HTTP client for NIH RePORTER API v2."""

    def __init__(
        self,
        config: NIHConfig,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()

        self.session.headers.update(
            {
                "User-Agent": "KOL-Ranking-Pipeline/1.0 (NIH RePORTER)"
            }
        )

    def request_with_retry(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(self.config.retries):
            try:
                response = self.session.post(
                    API_URL,
                    json=payload,
                    timeout=self.config.timeout,
                )

                if response.status_code == 429:
                    wait = min(60, 2 ** attempt)

                    logging.warning(
                        "NIH RePORTER rate limit (429). "
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
                    "NIH API request failed "
                    "(attempt %s/%s): %s. Retrying in %s seconds.",
                    attempt + 1,
                    self.config.retries,
                    exc,
                    wait,
                )

                time.sleep(wait)

        raise RuntimeError(
            f"NIH RePORTER request failed after "
            f"{self.config.retries} attempts."
        ) from last_error

    def search_term(self, term: str) -> list[dict]:
        """Fetch all NIH projects matching one search term."""
        offset = 0
        all_projects: list[dict] = []

        while True:
            payload = {
                "criteria": {
                    "advanced_text_search": {
                        "operator": "and",
                        "search_field": (
                            "projecttitle,abstracttext,terms"
                        ),
                        "search_text": term,
                    }
                },
                "offset": offset,
                "limit": self.config.limit,
                "sort_field": "project_start_date",
                "sort_order": "desc",
            }

            data = self.request_with_retry(payload)

            results = data.get("results", [])
            total = data.get("meta", {}).get("total", 0)

            if not results:
                break

            logging.info(
                "%s: %s/%s projects",
                term,
                f"{offset + len(results):,}",
                f"{total:,}",
            )

            for project in results:
                project["Matched Search Term"] = term
                all_projects.append(project)

            offset += self.config.limit

            if offset >= total:
                break

            time.sleep(self.config.request_delay)

        return all_projects


# ============================================================
# HELPERS
# ============================================================

def build_source_url(project: dict) -> str:
    """Build a stable NIH RePORTER source link."""
    project_num = (
        project.get("project_num", "")
        or project.get("core_project_num", "")
    )

    if not project_num:
        return ""

    return SOURCE_URL_TEMPLATE.format(
        project_num=project_num
    )


def as_list(value: Any) -> list:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue

        if isinstance(value, dict):
            value = first_non_empty(
                value.get("full_name", ""),
                value.get("name", ""),
                value.get("label", ""),
                value.get("text", ""),
            )

        if isinstance(value, str):
            stripped = value.strip()

            if stripped:
                return stripped

        elif value:
            return value

    return ""


def format_person_name(person: Any) -> str:
    if not isinstance(person, dict):
        return clean_excel_text(person)

    last_name = first_non_empty(
        person.get("last_name", ""),
        person.get("family_name", ""),
        person.get("surname", ""),
    )

    first_name = first_non_empty(
        person.get("first_name", ""),
        person.get("given_name", ""),
    )

    middle_name = first_non_empty(
        person.get("middle_name", ""),
        person.get("middle_initial", ""),
    )

    if last_name and (first_name or middle_name):
        given = " ".join(
            part
            for part in [first_name, middle_name]
            if part
        )

        return clean_excel_text(
            f"{last_name}, {given}"
        )

    return clean_excel_text(
        first_non_empty(
            *(person.get(key, "") for key in PERSON_NAME_KEYS)
        )
    )


def extract_person_name(person: Any) -> str:
    if not isinstance(person, dict):
        return clean_excel_text(person)

    return format_person_name(person)


def extract_contact_pi_name(project: dict) -> str:
    direct_candidates = [
        project.get("contact_pi_name", ""),
        project.get("contact_pi", ""),
        project.get("contact_principal_investigator", ""),
        project.get("project_leader_name", ""),
        project.get("project_leader", ""),
    ]

    for candidate in direct_candidates:
        if isinstance(candidate, dict):
            value = format_person_name(candidate)
        else:
            value = clean_excel_text(candidate)

        if value:
            return value

    return ""


def extract_person_role(person: Any) -> str:
    if not isinstance(person, dict):
        return ""

    return clean_excel_text(
        first_non_empty(
            *(person.get(key, "") for key in PERSON_ROLE_KEYS)
        )
    )


def extract_person_org(person: Any) -> str:
    if not isinstance(person, dict):
        return ""

    for key in PERSON_ORG_KEYS:
        value = person.get(key, "")

        if isinstance(value, dict):
            value = first_non_empty(
                value.get("org_name", ""),
                value.get("name", ""),
                value.get("institution_name", ""),
                value.get("city", ""),
                value.get("state", ""),
            )

        if value:
            return clean_excel_text(value)

    return ""


def collect_people(project: dict) -> list[dict]:
    collected: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for key in PERSON_CONTAINER_KEYS:
        for person in as_list(project.get(key)):
            if not isinstance(person, dict):
                continue

            name = extract_person_name(person)
            role = extract_person_role(person)
            org = extract_person_org(person)

            marker = (
                name.lower(),
                role.lower(),
                org.lower(),
            )

            if marker in seen:
                continue

            seen.add(marker)

            collected.append(
                {
                    "name": name,
                    "role": role,
                    "org": org,
                    "raw": person,
                }
            )

    if collected:
        return collected

    # Fallback: inspect list-valued project fields for personnel-like objects.
    for value in project.values():
        if not isinstance(value, list):
            continue

        for person in value:
            if not isinstance(person, dict):
                continue

            if not any(
                key in person
                for key in PERSON_NAME_KEYS
            ):
                continue

            name = extract_person_name(person)
            role = extract_person_role(person)
            org = extract_person_org(person)

            marker = (
                name.lower(),
                role.lower(),
                org.lower(),
            )

            if marker in seen:
                continue

            seen.add(marker)

            collected.append(
                {
                    "name": name,
                    "role": role,
                    "org": org,
                    "raw": person,
                }
            )

    return collected


def classify_role(role: str) -> str:
    role_lower = (role or "").lower()

    if not role_lower:
        return "principal"

    if "contact" in role_lower:
        return "contact"

    if (
        "co-principal" in role_lower
        or "co principal" in role_lower
    ):
        return "co_principal"

    if (
        "co-investigator" in role_lower
        or "co investigator" in role_lower
    ):
        return "co_investigator"

    if "site" in role_lower:
        return "site"

    if (
        "consultant" in role_lower
        or "advisor" in role_lower
    ):
        return "consultant"

    if (
        "principal" in role_lower
        or role_lower in {
            "pi",
            "p.i.",
            "lead pi",
            "lead investigator",
        }
    ):
        return "principal"

    if "investigator" in role_lower:
        return "co_investigator"

    return "principal"


def join_unique(values: list[Any]) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()

    for value in values:
        value = clean_excel_text(value)

        if not value:
            continue

        key = value.lower()

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(value)

    return "; ".join(cleaned)


def append_unique(values: list[str], value: Any) -> None:
    value = clean_excel_text(value)

    if not value:
        return

    normalized = value.lower()

    if normalized in {
        item.lower()
        for item in values
    }:
        return

    values.append(value)


def remove_name_case_insensitive(
    values: list[str],
    name: str,
) -> list[str]:
    name = clean_excel_text(name)

    if not name:
        return values

    target = name.lower()

    return [
        value
        for value in values
        if clean_excel_text(value).lower() != target
    ]


def infer_center_type(
    project: dict,
    people: list[dict],
) -> str:
    """Infer center type using the same hierarchy as the original script."""

    direct_text = " ".join(
        clean_excel_text(str(value))
        for value in project.values()
        if isinstance(value, (str, int, float))
    ).lower()

    if any(
        term in direct_text
        for term in (
            "multi-center",
            "multicenter",
            "multi center",
            "multi-site",
            "multi site",
        )
    ):
        return "Multicenter"

    if any(
        term in direct_text
        for term in (
            "single-center",
            "single center",
            "unicenter",
            "uni-center",
            "uni center",
        )
    ):
        return "Single center"

    for key in (
        "multicenter",
        "multi_center",
        "multi_site",
        "multi-site",
        "is_multicenter",
        "is_multi_site",
        "single_center",
        "single_site",
        "uni_center",
        "uni-center",
    ):
        value = project.get(key)

        if isinstance(value, bool):
            return (
                "Multicenter"
                if value
                else "Single center"
            )

        if (
            isinstance(value, (int, float))
            and value in (0, 1)
        ):
            if "single" in key or "uni" in key:
                return (
                    "Single center"
                    if value
                    else "Multicenter"
                )

            return (
                "Multicenter"
                if value
                else "Single center"
            )

        if isinstance(value, str):
            value_lower = value.strip().lower()

            if value_lower in {
                "yes",
                "true",
                "y",
                "multicenter",
                "multi-center",
                "multi site",
                "multi-site",
            }:
                return "Multicenter"

            if value_lower in {
                "no",
                "false",
                "n",
                "single center",
                "single-center",
                "uni-center",
                "uni center",
            }:
                return "Single center"

    site_count = (
        project.get("site_count")
        or project.get("number_of_sites")
        or project.get("sites_count")
    )

    if isinstance(site_count, (int, float)):
        if site_count > 1:
            return "Multicenter"

        if site_count == 1:
            return "Single center"

    location_candidates: set[str] = set()
    organization_candidates: set[str] = set()

    for person in people:
        org = person.get("org", "")

        if org:
            organization_candidates.add(org.lower())

        raw = person.get("raw") or {}

        for key in (
            "site_name",
            "site",
            "location",
            "center",
            "institution",
        ):
            value = raw.get(key, "")

            if isinstance(value, dict):
                value = first_non_empty(
                    value.get("name", ""),
                    value.get("org_name", ""),
                    value.get("institution_name", ""),
                    value.get("city", ""),
                    value.get("state", ""),
                )

            value = clean_excel_text(value)

            if value:
                location_candidates.add(
                    value.lower()
                )

    org = project.get("organization") or {}

    if isinstance(org, dict):
        org_name = clean_excel_text(
            org.get("org_name", "")
        )

        if org_name:
            organization_candidates.add(
                org_name.lower()
            )

        org_city = clean_excel_text(
            org.get("org_city", "")
        )

        org_state = clean_excel_text(
            org.get("org_state", "")
        )

        if org_city or org_state:
            location_candidates.add(
                f"{org_city}, {org_state}".strip(", ")
            )

    if len(location_candidates) > 1:
        return "Multicenter"

    if len(organization_candidates) > 1:
        return "Multicenter"

    if (
        location_candidates
        or organization_candidates
    ):
        return "Single center"

    return "Unknown"


# ============================================================
# PARSER
# ============================================================

def parse_project(project: dict) -> dict:
    """Flatten one NIH RePORTER project into a KOL-friendly record."""

    people = collect_people(project)

    contact_pi = extract_contact_pi_name(project)

    principal_investigators: list[str] = []
    co_principal_investigators: list[str] = []
    co_investigators: list[str] = []
    site_investigators: list[str] = []
    consultants: list[str] = []

    for person in people:
        name = person["name"]
        role_bucket = classify_role(person["role"])

        if not name:
            continue

        if role_bucket == "contact":
            contact_pi = name

        elif role_bucket == "principal":
            append_unique(
                principal_investigators,
                name,
            )

        elif role_bucket == "co_principal":
            append_unique(
                co_principal_investigators,
                name,
            )

        elif role_bucket == "co_investigator":
            append_unique(
                co_investigators,
                name,
            )

        elif role_bucket == "site":
            append_unique(
                site_investigators,
                name,
            )

        elif role_bucket == "consultant":
            append_unique(
                consultants,
                name,
            )

        else:
            append_unique(
                principal_investigators,
                name,
            )

    principal_investigators = remove_name_case_insensitive(
        principal_investigators,
        contact_pi,
    )

    org = project.get("organization") or {}

    ic_name = ""

    fundings = project.get("agency_ic_fundings") or []

    if fundings:
        first = fundings[0]

        if isinstance(first, dict):
            ic_name = first.get(
                "ic_name",
                "",
            )

    return {
        "Project Number": project.get(
            "project_num",
            "",
        ),
        "Core Project Number": project.get(
            "core_project_num",
            "",
        ),
        "Project Title": clean_excel_text(
            project.get(
                "project_title",
                "",
            )
        ),
        "Abstract": clean_excel_text(
            project.get(
                "abstract_text",
                "",
            )
        ),
        "Contact PI": clean_excel_text(
            contact_pi
        ),
        "Principal Investigators": join_unique(
            principal_investigators
        ),
        "Co-Principal Investigators": join_unique(
            co_principal_investigators
        ),
        "Co-Investigators": join_unique(
            co_investigators
        ),
        "Site Investigators": join_unique(
            site_investigators
        ),
        "Consultants": join_unique(
            consultants
        ),
        "Organization": clean_excel_text(
            org.get("org_name", "")
        ),
        "City": clean_excel_text(
            org.get("org_city", "")
        ),
        "State": clean_excel_text(
            org.get("org_state", "")
        ),
        "Country": clean_excel_text(
            org.get("org_country", "")
        ),
        "Funding Institute": clean_excel_text(
            ic_name
        ),
        "Activity Code": project.get(
            "activity_code",
            "",
        ),
        "Project Start": project.get(
            "project_start_date",
            "",
        ),
        "Project End": project.get(
            "project_end_date",
            "",
        ),
        "Fiscal Year": project.get(
            "fiscal_year",
            "",
        ),
        "Award Amount": project.get(
            "award_amount",
            "",
        ),
        "Source URL": build_source_url(project),
        "Center Type": infer_center_type(
            project,
            people,
        ),
        "Matched Search Term": project.get(
            "Matched Search Term",
            "",
        ),
    }


# ============================================================
# DATAFRAME / OUTPUT
# ============================================================

def build_dataframe(
    records: list[dict],
) -> pd.DataFrame:
    """Create, clean, deduplicate and sort the NIH dataset."""

    df = pd.DataFrame(records)

    if df.empty:
        return df

    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].apply(
                clean_excel_text
            )

    if "Project Number" in df.columns:
        df = df.drop_duplicates(
            subset=["Project Number"],
            keep="first",
        )

    if "Fiscal Year" in df.columns:
        df = df.sort_values(
            by="Fiscal Year",
            ascending=False,
        )

    return df.reset_index(drop=True)


def export_excel(
    df: pd.DataFrame,
    output_dir: Path,
    output_file: str,
) -> Path:
    """Write the NIH dataset to a dedicated output directory."""

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = output_dir / output_file

    df.to_excel(
        output_path,
        index=False,
    )

    return output_path


# ============================================================
# PIPELINE
# ============================================================

def run_pipeline(config: NIHConfig) -> Path:
    """Run the complete NIH extraction component."""

    config.validate()

    client = NIHReporterClient(config)
    all_projects: list[dict] = []

    logging.info(
        "Starting NIH RePORTER pipeline."
    )

    logging.info(
        "Search terms: %s",
        ", ".join(config.search_terms),
    )

    for term in config.search_terms:
        logging.info(
            "Searching NIH RePORTER: %s",
            term,
        )

        # Fail the pipeline on source/API errors.
        # This avoids publishing an apparently complete but incomplete dataset.
        projects = client.search_term(term)

        all_projects.extend(projects)

    if not all_projects:
        raise RuntimeError(
            "NIH RePORTER returned no projects."
        )

    records: list[dict] = []

    for project in tqdm(
        all_projects,
        desc="Parsing NIH Projects",
    ):
        records.append(
            parse_project(project)
        )

    df = build_dataframe(records)

    if df.empty:
        raise RuntimeError(
            "No usable NIH project records were produced."
        )

    output_path = export_excel(
        df,
        config.output_dir,
        config.output_file,
    )

    logging.info(
        "NIH RePORTER pipeline completed successfully."
    )

    logging.info(
        "Unique projects: %s",
        f"{len(df):,}",
    )

    logging.info(
        "Output: %s",
        output_path,
    )

    return output_path


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the NIH RePORTER component "
            "of the KOL Ranking Pipeline."
        )
    )

    parser.add_argument(
        "--search-terms",
        help="Comma-separated NIH search terms.",
    )

    parser.add_argument(
        "--limit",
        type=int,
    )

    parser.add_argument(
        "--request-delay",
        type=float,
    )

    parser.add_argument(
        "--output-dir",
    )

    parser.add_argument(
        "--output-file",
    )

    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
    )

    args = parse_args()
    env_config = NIHConfig.from_env()

    search_terms = env_config.search_terms

    if args.search_terms:
        search_terms = tuple(
            term.strip()
            for term in args.search_terms.split(",")
            if term.strip()
        )

    config = NIHConfig(
        search_terms=search_terms,
        limit=args.limit or env_config.limit,
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
        output_file=(
            args.output_file
            or env_config.output_file
        ),
    )

    start = time.time()

    try:
        run_pipeline(config)

    except Exception:
        logging.exception(
            "NIH RePORTER pipeline failed."
        )
        raise

    finally:
        logging.info(
            "Total runtime: %.2f minutes",
            (time.time() - start) / 60,
        )


if __name__ == "__main__":
    main()
