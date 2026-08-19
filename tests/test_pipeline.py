import pandas as pd

from src.pipeline import PipelineConfig, run_pipeline


def test_run_pipeline_keeps_secondary_name_fields(tmp_path):
    pubmed_path = tmp_path / "Author_Summary.xlsx"
    nih_path = tmp_path / "NIH_Psychedelic_Grants.xlsx"
    trials_path = tmp_path / "ClinicalTrials_Psychedelics.xlsx"
    output_dir = tmp_path / "ranking"

    pd.DataFrame(
        [
            {
                "Canonical Author Name": "SMITH JOHN",
                "Original Author Variants": "John Smith",
                "Total Publications": 3,
                "Publications Last 5 Years": 2,
                "Latest Publication Year": 2026,
                "Years Since Last Publication": 0,
                "First Author Count": 1,
                "Middle Author Count": 0,
                "Last Author Count": 1,
                "Corresponding Author Count": 1,
                "First+Corresponding Count": 1,
                "Last+Corresponding Count": 1,
                "PMIDs": "1; 2",
                "Titles": "Paper A || Paper B",
            }
        ]
    ).to_excel(pubmed_path, index=False)

    pd.DataFrame(
        [
            {
                "Project Number": "R01TEST001",
                "Principal Investigators": "John Smith",
                "Contact PI": "",
                "Co-Investigators": "",
                "Award Amount": 100000,
                "Fiscal Year": 2026,
                "Matched Search Term": "psilocybin",
            }
        ]
    ).to_excel(nih_path, index=False)

    pd.DataFrame(
        [
            {
                "NCT Number": "NCT00000001",
                "Trial Principal Investigator": "",
                "Site Principal Investigators": "John Smith",
                "Sub-Investigator / Co-Investigator": "",
                "Overall Status": "RECRUITING",
                "Center Type": "Multicenter",
            }
        ]
    ).to_excel(trials_path, index=False)

    config = PipelineConfig(
        pubmed_summary_path=pubmed_path,
        nih_path=nih_path,
        trials_path=trials_path,
        output_dir=output_dir,
        output_file="KOL_Ranking.xlsx",
    )

    output_path = run_pipeline(config)
    ranking = pd.read_excel(output_path)

    assert not ranking.empty
    row = ranking.loc[ranking["Normalized Name"] == "SMITH JOHN"].iloc[0]
    assert row["NIH Score"] > 0
    assert row["Clinical Trial Score"] > 0

