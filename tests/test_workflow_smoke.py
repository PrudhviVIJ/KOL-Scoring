from pathlib import Path


def test_cd_workflow_has_expected_pipeline_steps():
    workflow = Path(".github/workflows/cd.yml").read_text(encoding="utf-8")

    assert "Run PubMed extraction" in workflow
    assert "Run NIH extraction" in workflow
    assert "Run ClinicalTrials.gov extraction" in workflow
    assert "Run pipeline" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "NCBI_EMAIL" in workflow
    assert "NCBI_API_KEY" in workflow


def test_ci_workflow_runs_tests():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "Run unit tests" in workflow
    assert "pytest -q" in workflow

