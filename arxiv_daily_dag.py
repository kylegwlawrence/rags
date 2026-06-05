from datetime import datetime
from airflow import DAG
from airflow.providers.ssh.operators.ssh import SSHOperator

_BASE = "/home/kyle/Documents/projects/datasets"
# arxiv lives in a single monolithic DB outside the repo (too big for /home).
_DB = "/datasets/arxiv/arxiv.db"

# 1. Harvest yesterday's metadata straight into the monolith.
_INGEST_CMD = (
    f"source {_BASE}/.venv/bin/activate && "
    f"python {_BASE}/scripts/arxiv/arxiv_ingest.py "
    f"--db {_DB} "
    '--from $(date -d "1 day ago" +%Y-%m-%d) '
    '--until $(date -d "1 day ago" +%Y-%m-%d)'
)
# 2. Fetch HTML bodies, scoped to the same harvest day as the ingest above
#    (--oai-date matches papers.oai_datestamp, the field ingest scopes on;
#    submitted_date would miss most of them due to the arXiv announce lag) and
#    narrowed to maths / physics / astro-ph to bound daily disk growth.
#    Category flags OR together and match the full categories field, so a
#    cross-listed paper (e.g. astro-ph.HE + physics.space-ph) is included.
#    math.* needs the prefix; math-ph is a separate token, so it's added by
#    exact --category.
_DOWNLOAD_CMD = (
    f"source {_BASE}/.venv/bin/activate && "
    f"python {_BASE}/scripts/arxiv/arxiv_download.py "
    f"--db {_DB} "
    '--oai-date $(date -d "1 day ago" +%Y-%m-%d) '
    "--category-prefix math "
    "--category math-ph "
    "--category-prefix physics "
    "--category-prefix astro-ph"
)
# 3. Rebuild the single papers_fts index over the monolith.
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
        # One HTTP request per paper at arXiv's 3 s/request limit: ~800 in-scope
        # papers ≈ 40 min, and a busy day runs larger. Give it 2 h headroom;
        # ingest (page-based) and fts (~3 min) keep the 1200 s default.
        cmd_timeout=7200,
    )
    fts_index = SSHOperator(
        task_id="fts_index",
        command=f"bash -c '{_FTS_INDEX_COMMAND}'",
    )

    ingest >> download >> fts_index
