"""
PubMed extraction pipeline for the KOL Ranking project.

Designed for:
- Local execution
- GitHub Actions CI/CD
- Scheduled execution by Airflow later
- Testability without calling PubMed during unit tests
- Configuration through environment variables / CLI arguments
- Excel outputs written to a dedicated output directory

Environment variables:
    NCBI_EMAIL          Required for production execution
    NCBI_API_KEY        Optional
    PUBMED_SEARCH_QUERY Optional override
    PUBMED_OUTPUT_DIR   Default: outputs/pubmed
    PUBMED_START_YEAR   Default: 2000
    PUBMED_END_YEAR     Default: current year
    PUBMED_RECENT_WINDOW Default: 5
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from tqdm import tqdm


DEFAULT_SEARCH_QUERY = """
(
    psilocybin
    OR LSD
    OR "lysergic acid diethylamide"
    OR MDMA
    OR ketamine
    OR DMT
    OR ayahuasca
    OR mescaline
    OR ibogaine
    OR psychedelic*
)
""".strip()

DEGREES = {
    "MD", "M.D", "DO", "D.O", "PHD", "PH.D", "PHD.", "MSC", "M.SC",
    "MS", "MA", "MBA", "MPH", "FRCP", "FACP", "FRCPC", "DDS", "DMD",
    "RN", "BSC", "B.SC", "BPHARM", "PHARMD", "DVM", "MBBS", "MB",
    "BS", "BDS", "JD", "ESQ", "FRS", "FRCPCH", "FAAN", "FACC", "FACS",
    "FACE", "MRCP", "MRCPCH", "PROF", "PROFESSOR", "DR",
}

EMAIL_REGEX = re.compile(r"[\w.\-]+@[\w.\-]+\.\w+", re.IGNORECASE)

CORRESPONDENCE_TERMS = [
    "correspondence",
    "corresponding author",
    "address correspondence",
    "correspondence to",
    "electronic address",
]

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass(frozen=True)
class PubMedConfig:
    """Runtime configuration for the PubMed pipeline."""

    search_query: str = DEFAULT_SEARCH_QUERY
    email: str = ""
    api_key: str = ""
    start_year: int = 2000
    end_year: int = datetime.now().year
    recent_window: int = 5
    fetch_batch_size: int = 200
    request_delay: float = 0.75
    retries: int = 8
    request_timeout: int = 120
    output_dir: Path = Path("outputs/pubmed")

    @classmethod
    def from_env(cls) -> "PubMedConfig":
        return cls(
            search_query=os.getenv("PUBMED_SEARCH_QUERY", DEFAULT_SEARCH_QUERY),
            email=os.getenv("NCBI_EMAIL", ""),
            api_key=os.getenv("NCBI_API_KEY", ""),
            start_year=int(os.getenv("PUBMED_START_YEAR", "2000")),
            end_year=int(os.getenv("PUBMED_END_YEAR", str(datetime.now().year))),
            recent_window=int(os.getenv("PUBMED_RECENT_WINDOW", "5")),
            fetch_batch_size=int(os.getenv("PUBMED_FETCH_BATCH_SIZE", "200")),
            request_delay=float(os.getenv("PUBMED_REQUEST_DELAY", "0.75")),
            retries=int(os.getenv("PUBMED_RETRIES", "8")),
            request_timeout=int(os.getenv("PUBMED_TIMEOUT", "120")),
            output_dir=Path(os.getenv("PUBMED_OUTPUT_DIR", "outputs/pubmed")),
        )

    def validate(self) -> None:
        if not self.email:
            raise ValueError(
                "NCBI_EMAIL is required for a production PubMed run. "
                "Set it as an environment variable or pass --email."
            )
        if self.start_year > self.end_year:
            raise ValueError("start_year cannot be greater than end_year.")
        if self.fetch_batch_size <= 0:
            raise ValueError("fetch_batch_size must be greater than zero.")
        if self.recent_window <= 0:
            raise ValueError("recent_window must be greater than zero.")


class PubMedClient:
    """Small, testable client around the NCBI E-utilities API."""

    def __init__(
        self,
        config: PubMedConfig,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": "KOL-Ranking-Pipeline/1.0 (PubMed)"}
        )

    def request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> requests.Response:
        last_error: Exception | None = None

        for attempt in range(self.config.retries):
            try:
                if method.upper() == "GET":
                    response = self.session.get(
                        url,
                        timeout=self.config.request_timeout,
                        **kwargs,
                    )
                else:
                    response = self.session.post(
                        url,
                        timeout=self.config.request_timeout,
                        **kwargs,
                    )

                if response.status_code == 429:
                    wait = min(60, 5 * (2 ** attempt))
                    logging.warning(
                        "PubMed rate limit (429). Waiting %s seconds.",
                        wait,
                    )
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                return response

            except requests.exceptions.RequestException as exc:
                last_error = exc
                wait = min(60, 2 ** attempt)
                logging.warning(
                    "PubMed request failed (attempt %s/%s): %s. "
                    "Retrying in %s seconds.",
                    attempt + 1,
                    self.config.retries,
                    exc,
                    wait,
                )
                time.sleep(wait)

        raise RuntimeError(
            f"PubMed request failed after {self.config.retries} attempts: {url}"
        ) from last_error

    def search_pmids(self, year: int) -> list[str]:
        term = (
            f"({self.config.search_query}) "
            f'AND ("{year}"[Date - Publication])'
        )

        params = {
            "db": "pubmed",
            "term": term,
            "retmax": 9999,
            "retmode": "json",
            "email": self.config.email,
        }

        if self.config.api_key:
            params["api_key"] = self.config.api_key

        logging.info("Searching PubMed for %s...", year)

        response = self.request_with_retry(
            "GET",
            f"{EUTILS_BASE}/esearch.fcgi",
            params=params,
        )

        ids = response.json()["esearchresult"]["idlist"]
        logging.info("%s: %s PMIDs", year, f"{len(ids):,}")
        return ids

    def collect_all_pmids(self) -> list[str]:
        pmids: list[str] = []

        for year in range(self.config.start_year, self.config.end_year + 1):
            pmids.extend(self.search_pmids(year))
            time.sleep(self.config.request_delay)

        unique_pmids = sorted(set(pmids))
        logging.info("Total unique PMIDs: %s", f"{len(unique_pmids):,}")
        return unique_pmids

    def fetch_xml(self, pmids: list[str]) -> ET.Element:
        data = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "email": self.config.email,
        }

        if self.config.api_key:
            data["api_key"] = self.config.api_key

        response = self.request_with_retry(
            "POST",
            f"{EUTILS_BASE}/efetch.fcgi",
            data=data,
        )

        return ET.fromstring(response.content)

    def download_all_xml(
        self,
        pmids: list[str],
    ) -> Iterable[ET.Element]:
        for start in range(0, len(pmids), self.config.fetch_batch_size):
            batch = pmids[start:start + self.config.fetch_batch_size]

            logging.info(
                "Downloading XML batch %s-%s of %s PMIDs...",
                start + 1,
                min(start + len(batch), len(pmids)),
                len(pmids),
            )

            yield self.fetch_xml(batch)
            time.sleep(self.config.request_delay)


def canonical_author(name: str) -> str:
    """Convert author names into a stable canonical representation."""
    if not isinstance(name, str):
        return ""

    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r"\(.*?\)", "", name)

    if "," in name:
        base, suffix = name.split(",", 1)
        suffix_tokens = [
            token.replace(".", "").upper()
            for token in re.split(r"\s+", suffix)
            if token.strip()
        ]
        if suffix_tokens and any(token in DEGREES for token in suffix_tokens):
            name = base

    name = name.replace(",", " ").replace(".", " ").replace("'", "")
    name = re.sub(r"\s+", " ", name).strip()

    tokens = [
        token for token in name.split()
        if token.upper() not in DEGREES
    ]

    if not tokens:
        return ""

    if len(tokens) == 1:
        return tokens[0].upper()

    first = tokens[0]
    middle = tokens[1][0].upper() if len(tokens) > 2 else ""
    last = tokens[-1]

    return (
        f"{last.upper()} {first.upper()} {middle}"
        if middle
        else f"{last.upper()} {first.upper()}"
    )


def safe_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def extract_publication_year(article: ET.Element) -> int | None:
    year = article.findtext(".//PubDate/Year")

    if year and year.isdigit():
        return int(year)

    medline = article.findtext(".//PubDate/MedlineDate", "")
    match = re.search(r"\d{4}", medline)

    return int(match.group()) if match else None


def extract_doi(article: ET.Element) -> str:
    for aid in article.findall(".//ArticleId"):
        if aid.attrib.get("IdType") == "doi":
            return aid.text or ""
    return ""


def extract_abstract(article: ET.Element) -> str:
    parts = [
        safe_text(node)
        for node in article.findall(".//AbstractText")
        if safe_text(node)
    ]
    return " ".join(parts)


def extract_keywords(article: ET.Element) -> str:
    keywords = [
        node.text.strip()
        for node in article.findall(".//Keyword")
        if node.text and node.text.strip()
    ]
    return "; ".join(keywords)


def parse_publication(article: ET.Element) -> dict:
    pmid = article.findtext(".//PMID", "")
    title = safe_text(article.find(".//ArticleTitle"))
    journal = article.findtext(".//Journal/Title", "")
    year = extract_publication_year(article)
    doi = extract_doi(article)
    abstract = extract_abstract(article)
    keywords = extract_keywords(article)

    publication_types = [
        node.text
        for node in article.findall(".//PublicationType")
        if node.text
    ]

    mesh_terms = [
        node.text
        for node in article.findall(".//MeshHeading/DescriptorName")
        if node.text
    ]

    affiliations = [
        aff.text.strip()
        for aff in article.findall(".//Affiliation")
        if aff.text and aff.text.strip()
    ]

    grants = []
    for grant in article.findall(".//Grant"):
        agency = grant.findtext("Agency", "")
        grant_id = grant.findtext("GrantID", "")
        grants.append(f"{agency}:{grant_id}")

    return {
        "PMID": pmid,
        "Title": title,
        "Publication Year": year,
        "Journal": journal,
        "DOI": doi,
        "Abstract": abstract,
        "Keywords": keywords,
        "Affiliations": " | ".join(sorted(set(affiliations))),
        "PubMed URL": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "Publication Types": "; ".join(publication_types),
        "Grant Support": "; ".join(grants),
        "MeSH Terms": "; ".join(sorted(set(mesh_terms))),
    }


def detect_corresponding(authors: list[dict]) -> set[str]:
    """Detect possible corresponding authors from affiliation text."""
    corresponding: set[str] = set()
    email_candidate: str | None = None
    keyword_candidate: str | None = None

    for author in authors:
        aff_text = " ".join(author["affiliations"]).lower()

        has_email = EMAIL_REGEX.search(aff_text) is not None
        has_keyword = any(term in aff_text for term in CORRESPONDENCE_TERMS)

        if has_email and has_keyword:
            corresponding.add(author["canonical"])

        if has_email and email_candidate is None:
            email_candidate = author["canonical"]

        if has_keyword and keyword_candidate is None:
            keyword_candidate = author["canonical"]

    if email_candidate:
        corresponding.add(email_candidate)

    if keyword_candidate:
        corresponding.add(keyword_candidate)

    return corresponding


def parse_authors(
    article: ET.Element,
    publication: dict,
    author_variants: defaultdict[str, set[str]],
) -> list[dict]:
    """Parse authors and assign position/corresponding-author roles."""
    authors: list[dict] = []

    for node in article.findall(".//AuthorList/Author"):
        lastname = node.findtext("LastName", "")
        firstname = node.findtext("ForeName", "")
        initials = node.findtext("Initials", "")

        original = f"{firstname} {lastname}".strip()
        if not original:
            continue

        canonical = canonical_author(original)

        affiliations = [
            aff.text.strip()
            for aff in node.findall(".//Affiliation")
            if aff.text and aff.text.strip()
        ]

        author = {
            "original": original,
            "canonical": canonical,
            "initials": initials,
            "affiliations": affiliations,
        }

        authors.append(author)
        author_variants[canonical].add(original)

    if not authors:
        return []

    corresponding_authors = detect_corresponding(authors)
    total = len(authors)
    records: list[dict] = []

    for idx, author in enumerate(authors):
        if idx == 0:
            position = "First"
        elif idx == total - 1:
            position = "Last"
        else:
            position = "Middle"

        is_corresponding = author["canonical"] in corresponding_authors

        if position == "First" and is_corresponding:
            display_role = "First+Corresponding"
        elif position == "Last" and is_corresponding:
            display_role = "Last+Corresponding"
        elif is_corresponding:
            display_role = "Corresponding Author"
        elif position == "First":
            display_role = "First Author"
        elif position == "Last":
            display_role = "Last Author"
        else:
            display_role = "Middle Author"

        records.append(
            {
                "PMID": publication["PMID"],
                "Publication Year": publication["Publication Year"],
                "Original Author Name": author["original"],
                "Canonical Author Name": author["canonical"],
                "Author Position": position,
                "Total Authors": total,
                "Is Corresponding": is_corresponding,
                "Author Role": display_role,
                "Affiliations": "; ".join(author["affiliations"]),
                "Title": publication["Title"],
                "Journal": publication["Journal"],
                "DOI": publication["DOI"],
                "PubMed URL": publication["PubMed URL"],
            }
        )

    return records


def process_xml(
    root: ET.Element,
    publication_records: list[dict],
    author_records: list[dict],
    author_variants: defaultdict[str, set[str]],
) -> None:
    """Convert one PubMed XML response into normalized records."""
    for article in root.findall(".//PubmedArticle"):
        try:
            publication = parse_publication(article)

            if not publication.get("PMID"):
                continue

            publication_records.append(publication)

            author_records.extend(
                parse_authors(article, publication, author_variants)
            )

        except Exception:
            logging.exception(
                "Failed processing PMID %s",
                article.findtext(".//PMID", "Unknown PMID"),
            )


def build_publication_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)

    if df.empty:
        return df

    df = df.drop_duplicates(subset=["PMID"]).copy()
    df["Publication Year"] = pd.to_numeric(
        df["Publication Year"],
        errors="coerce",
    )

    return (
        df.sort_values(
            by=["Publication Year", "PMID"],
            ascending=[False, False],
        )
        .reset_index(drop=True)
    )


def build_author_mapping_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)

    if df.empty:
        return df

    df = df.drop_duplicates(
        subset=["PMID", "Canonical Author Name", "Author Position"]
    ).copy()

    df["Publication Year"] = pd.to_numeric(
        df["Publication Year"],
        errors="coerce",
    )

    return (
        df.sort_values(
            by=[
                "Canonical Author Name",
                "Publication Year",
                "Author Position",
            ],
            ascending=[True, False, True],
        )
        .reset_index(drop=True)
    )


def build_author_summary(
    author_mapping_df: pd.DataFrame,
    current_year: int,
    recent_window: int,
) -> pd.DataFrame:
    """Build author-level publication and role summary."""
    if author_mapping_df.empty:
        return pd.DataFrame()

    cutoff_year = current_year - recent_window + 1
    summary_records: list[dict] = []

    grouped = author_mapping_df.groupby(
        "Canonical Author Name",
        sort=True,
    )

    for canonical_name, group in tqdm(
        grouped,
        desc="Building Author Summary",
    ):
        unique_publications = group.drop_duplicates(subset="PMID")

        total_publications = len(unique_publications)

        recent_publications = len(
            unique_publications[
                unique_publications["Publication Year"] >= cutoff_year
            ]
        )

        latest_year = unique_publications["Publication Year"].max()

        if pd.isna(latest_year):
            latest_year_value = ""
            years_since_last = ""
        else:
            latest_year_value = int(latest_year)
            years_since_last = current_year - latest_year_value

        first_count = (group["Author Position"] == "First").sum()
        middle_count = (group["Author Position"] == "Middle").sum()
        last_count = (group["Author Position"] == "Last").sum()
        corresponding_count = group["Is Corresponding"].sum()

        first_corr = (
            (group["Author Position"] == "First")
            & group["Is Corresponding"]
        ).sum()

        last_corr = (
            (group["Author Position"] == "Last")
            & group["Is Corresponding"]
        ).sum()

        variants_text = " | ".join(
            sorted(set(group["Original Author Name"]))
        )

        pmids_text = "; ".join(
            sorted(unique_publications["PMID"].astype(str))
        )

        titles_text = " || ".join(
            unique_publications["Title"].astype(str).tolist()
        )

        summary_records.append(
            {
                "Canonical Author Name": canonical_name,
                "Original Author Variants": variants_text,
                "Total Publications": total_publications,
                "Publications Last 5 Years": recent_publications,
                "Latest Publication Year": latest_year_value,
                "Years Since Last Publication": years_since_last,
                "First Author Count": first_count,
                "Middle Author Count": middle_count,
                "Last Author Count": last_count,
                "Corresponding Author Count": corresponding_count,
                "First+Corresponding Count": first_corr,
                "Last+Corresponding Count": last_corr,
                "PMIDs": pmids_text,
                "Titles": titles_text,
            }
        )

    return (
        pd.DataFrame(summary_records)
        .sort_values(
            by=[
                "Publications Last 5 Years",
                "Total Publications",
                "Canonical Author Name",
            ],
            ascending=[False, False, True],
        )
        .reset_index(drop=True)
    )


def export_excel(
    publication_df: pd.DataFrame,
    author_mapping_df: pd.DataFrame,
    author_summary_df: pd.DataFrame,
    output_dir: Path,
) -> dict[str, Path]:
    """Write pipeline outputs and return their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "publications": output_dir / "Publications.xlsx",
        "author_mapping": output_dir / "Author_Publication_Mapping.xlsx",
        "author_summary": output_dir / "Author_Summary.xlsx",
    }

    publication_df.to_excel(paths["publications"], index=False)
    author_mapping_df.to_excel(paths["author_mapping"], index=False)
    author_summary_df.to_excel(paths["author_summary"], index=False)

    return paths


def run_pipeline(config: PubMedConfig) -> dict[str, Path]:
    """Run the complete PubMed extraction pipeline."""
    config.validate()

    client = PubMedClient(config)

    publication_records: list[dict] = []
    author_records: list[dict] = []
    author_variants: defaultdict[str, set[str]] = defaultdict(set)

    logging.info("Starting PubMed pipeline.")
    logging.info(
        "Configuration: years=%s-%s, recent_window=%s, batch_size=%s",
        config.start_year,
        config.end_year,
        config.recent_window,
        config.fetch_batch_size,
    )

    pmids = client.collect_all_pmids()

    if not pmids:
        raise RuntimeError("PubMed returned zero PMIDs.")

    for root in client.download_all_xml(pmids):
        process_xml(
            root,
            publication_records,
            author_records,
            author_variants,
        )

    publication_df = build_publication_df(publication_records)
    author_mapping_df = build_author_mapping_df(author_records)

    author_summary_df = build_author_summary(
        author_mapping_df,
        current_year=config.end_year,
        recent_window=config.recent_window,
    )

    if publication_df.empty:
        raise RuntimeError("No publication records were parsed.")

    if author_mapping_df.empty:
        raise RuntimeError("No author records were parsed.")

    paths = export_excel(
        publication_df,
        author_mapping_df,
        author_summary_df,
        config.output_dir,
    )

    logging.info("Pipeline completed successfully.")
    logging.info("Publications: %s", f"{len(publication_df):,}")
    logging.info("Author mapping rows: %s", f"{len(author_mapping_df):,}")
    logging.info("Unique authors: %s", f"{len(author_summary_df):,}")

    for name, path in paths.items():
        logging.info("%s output: %s", name, path)

    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the PubMed component of the KOL Ranking Pipeline."
    )

    parser.add_argument("--email", default=os.getenv("NCBI_EMAIL"))
    parser.add_argument("--api-key", default=os.getenv("NCBI_API_KEY", ""))
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--recent-window", type=int)
    parser.add_argument("--output-dir")
    parser.add_argument("--request-delay", type=float)

    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    args = parse_args()
    env_config = PubMedConfig.from_env()

    config = PubMedConfig(
        search_query=env_config.search_query,
        email=args.email or env_config.email,
        api_key=args.api_key,
        start_year=args.start_year or env_config.start_year,
        end_year=args.end_year or env_config.end_year,
        recent_window=args.recent_window or env_config.recent_window,
        fetch_batch_size=env_config.fetch_batch_size,
        request_delay=(
            args.request_delay
            if args.request_delay is not None
            else env_config.request_delay
        ),
        retries=env_config.retries,
        request_timeout=env_config.request_timeout,
        output_dir=Path(args.output_dir)
        if args.output_dir
        else env_config.output_dir,
    )

    start = time.time()

    try:
        run_pipeline(config)
    except Exception:
        logging.exception("PubMed pipeline failed.")
        raise
    finally:
        logging.info(
            "Total runtime: %.2f minutes",
            (time.time() - start) / 60,
        )


if __name__ == "__main__":
    main()
