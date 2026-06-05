from datetime import datetime
from airflow import DAG
from airflow.providers.ssh.operators.ssh import SSHOperator

_BASE = "/home/kyle/Documents/projects/datasets"
_DB = "/datasets/arxiv/arxiv.db"

# Load .env (exporting every var so the Python process inherits DATASETS_EMAIL,
# required by the OAI-PMH polite rate limit) before activating the venv.
_ENV_PREFIX = f"set -a && source {_BASE}/.env && set +a && "

_INGEST_CMD = (
    f"{_ENV_PREFIX}"
    f"source {_BASE}/.venv/bin/activate && "
    f"python {_BASE}/scripts/arxiv/arxiv_ingest.py "
    f"--db {_DB} "
    '--from $(date -d "1 day ago" +%Y-%m-%d) '
    '--until $(date -d "1 day ago" +%Y-%m-%d)'
)
_DOWNLOAD_CMD = (
    f"{_ENV_PREFIX}"
    f"source {_BASE}/.venv/bin/activate && "
    f"python {_BASE}/scripts/arxiv/arxiv_download.py "
    f"--db {_DB} "
    '--oai-date $(date -d "1 day ago" +%Y-%m-%d) '
    "--category-prefix math "
    "--category math-ph "
    "--category-prefix physics "
    "--category-prefix astro-ph"
)
_FTS_INDEX_COMMAND = (
    f"source {_BASE}/.venv/bin/activate && "
    f"python {_BASE}/scripts/arxiv/arxiv_index_fts.py --db {_DB}"
)

with DAG(
    dag_id="arxiv_daily",
    start_date=datetime(2026, 1, 1),
    schedule="0 12 * * *",
    catchup=False,
    tags=["test"],
    default_args={
        "ssh_conn_id": "pop_os_ssh",
        "cmd_timeout": 1200,
    },
) as dag:
    ingest = SSHOperator(
        task_id="ingest",
        command=f"bash -c '{_INGEST_CMD}'",
    )
    download = SSHOperator(
        task_id="download",
        command=f"bash -c '{_DOWNLOAD_CMD}'",
        cmd_timeout=43200,
    )
    fts_index = SSHOperator(
        task_id="fts_index",
        command=f"bash -c '{_FTS_INDEX_COMMAND}'",
    )

    ingest >> download >> fts_index
