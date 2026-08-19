from __future__ import annotations

"""Airflow DAG entrypoint for the KOL ranking pipeline.

This module is intentionally safe to import without Airflow installed.
"""

from pathlib import Path


def build_dag():
    try:
        from airflow import DAG
        from airflow.operators.python import PythonOperator
    except Exception:  # pragma: no cover - import guard for local dev
        return None

    from datetime import datetime

    from src.pipeline import PipelineConfig, run_pipeline

    default_args = {
        "owner": "codex",
        "depends_on_past": False,
        "retries": 1,
    }

    dag = DAG(
        dag_id="kol_ranking_pipeline",
        default_args=default_args,
        start_date=datetime(2026, 1, 1),
        schedule="@weekly",
        catchup=False,
        tags=["kol", "ranking"],
    )

    def _run():
        run_pipeline(PipelineConfig())

    PythonOperator(
        task_id="run_kol_ranking_pipeline",
        python_callable=_run,
        dag=dag,
    )
    return dag


dag = build_dag()

