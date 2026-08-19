# KOL Ranking Pipeline

This project builds a KOL ranking workflow from three source streams:

- PubMed publications
- NIH RePORTER grants
- ClinicalTrials.gov studies

The pipeline extracts source data, normalizes names, scores people across the available signals, and writes a ranked Excel output.

## Project Layout

- `src/extraction/` - source-specific extraction modules
- `src/processing/` - normalization, deduplication, and matching helpers
- `src/scoring/` - publication, NIH, trial, recency, and composite scoring
- `src/pipeline.py` - combines source tables into the final ranking
- `.github/workflows/` - CI and CD workflows

## Local Setup

1. Create or activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the test suite:

```bash
python -m pytest -q
```

## Running the Pipeline

The ranking pipeline expects these source files by default:

- `outputs/pubmed/Author_Summary.xlsx`
- `outputs/nih/NIH_Psychedelic_Grants.xlsx`
- `outputs/clinical_trials/ClinicalTrials_Psychedelics.xlsx`

Run it with:

```bash
python -m src.pipeline
```

The output workbook is written to:

- `outputs/ranking/KOL_Ranking.xlsx`

## GitHub Actions

The repo includes:

- CI: runs the test suite on push and pull request
- CD: runs the extractors, ranking pipeline, and uploads the final workbook

For PubMed extraction in GitHub Actions, set these secrets:

- `NCBI_EMAIL`
- `NCBI_API_KEY` optional

## Notes

- The pipeline uses pandas/openpyxl for Excel I/O.
- Source requests are retried so transient API errors fail fast but not silently.
- The test suite includes a smoke test for the workflow definition.
