from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.processing.deduplicate import deduplicate_dataframe
from src.processing.normalize_names import normalize_name
from src.scoring.kol_score import compute_kol_scores, load_scoring_weights


DEFAULT_OUTPUT_DIR = Path("outputs/ranking")
RUN_METADATA_FILE = "run_metadata.json"


@dataclass(frozen=True)
class PipelineConfig:
    pubmed_summary_path: Path = Path("outputs/pubmed/Author_Summary.xlsx")
    nih_path: Path = Path("outputs/nih/NIH_Psychedelic_Grants.xlsx")
    trials_path: Path = Path("outputs/clinical_trials/ClinicalTrials_Psychedelics.xlsx")
    output_dir: Path = DEFAULT_OUTPUT_DIR
    output_file: str = "KOL_Ranking.xlsx"

  
def _read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported input format: {path}")


def _standardize_publications(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    result = df.copy()
    if "Canonical Author Name" not in result.columns and "Normalized Name" in result.columns:
        result["Canonical Author Name"] = result["Normalized Name"]
    if "Canonical Author Name" in result.columns:
        result["Normalized Name"] = result["Canonical Author Name"].map(normalize_name)
    return result


def _write_run_metadata(
    *,
    config: PipelineConfig,
    output_path: Path,
    pubmed_raw_rows: int,
    pubmed_processed_rows: int,
    nih_rows: int,
    trials_rows: int,
    ranking_rows: int,
    weights: dict[str, float],
) -> Path:
    """Write an auditable manifest for a completed ranking run."""
    metadata = {
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "output_file": str(output_path),
        "input_files": {
            "pubmed": str(config.pubmed_summary_path),
            "nih": str(config.nih_path),
            "clinical_trials": str(config.trials_path),
        },
        "source_rows": {
            "pubmed_raw": pubmed_raw_rows,
            "pubmed_after_deduplication": pubmed_processed_rows,
            "nih": nih_rows,
            "clinical_trials": trials_rows,
        },
        "ranking_rows": ranking_rows,
        "scoring_weights": weights,
    }
    metadata_path = config.output_dir / RUN_METADATA_FILE
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logging.info("Run metadata written to %s", metadata_path)
    return metadata_path


def run_pipeline(config: PipelineConfig) -> Path:
    logging.info("Loading source tables.")

    pubmed = _read_table(config.pubmed_summary_path)
    nih = _read_table(config.nih_path)
    trials = _read_table(config.trials_path)
    pubmed_raw_rows = len(pubmed)
    nih_rows = len(nih)
    trials_rows = len(trials)

    if pubmed.empty and nih.empty and trials.empty:
        raise RuntimeError("No input tables found for KOL ranking.")

    pubmed = _standardize_publications(pubmed)

    if not pubmed.empty:
        pubmed = deduplicate_dataframe(
            pubmed,
            subset=[
                "Normalized Name",
                "PMID",
            ] if "PMID" in pubmed.columns else ["Normalized Name"],
        )

    weights = load_scoring_weights()
    ranking = compute_kol_scores(
        publication_summary=pubmed,
        nih_projects=nih,
        clinical_trials=trials,
        current_year=None,
        weights=weights,
    )

    if ranking.empty:
        raise RuntimeError("No ranking rows were produced.")

    config.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.output_dir / config.output_file
    ranking.to_excel(output_path, index=False)
    _write_run_metadata(
        config=config,
        output_path=output_path,
        pubmed_raw_rows=pubmed_raw_rows,
        pubmed_processed_rows=len(pubmed),
        nih_rows=nih_rows,
        trials_rows=trials_rows,
        ranking_rows=len(ranking),
        weights=weights,
    )
    logging.info("KOL ranking written to %s", output_path)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the KOL ranking pipeline.")
    parser.add_argument("--pubmed-summary-path")
    parser.add_argument("--nih-path")
    parser.add_argument("--trials-path")
    parser.add_argument("--output-dir")
    parser.add_argument("--output-file")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = parse_args()
    config = PipelineConfig(
        pubmed_summary_path=Path(args.pubmed_summary_path) if args.pubmed_summary_path else PipelineConfig.pubmed_summary_path,
        nih_path=Path(args.nih_path) if args.nih_path else PipelineConfig.nih_path,
        trials_path=Path(args.trials_path) if args.trials_path else PipelineConfig.trials_path,
        output_dir=Path(args.output_dir) if args.output_dir else PipelineConfig.output_dir,
        output_file=args.output_file or PipelineConfig.output_file,
    )
    run_pipeline(config)


if __name__ == "__main__":
    main()
