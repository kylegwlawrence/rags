#!/usr/bin/env bash
# Deploy and run the enwiki body FTS indexer on raspberrypi6.
#
# Run steps individually — steps 4 and 5 are long-running and interactive.
# Step 5 (full run) can take several hours; run it in a tmux session on the pi.
#
# Usage: run each block manually, or source the file and call steps one at a time.

set -euo pipefail

PI=raspberrypi6
VENV="source ~/datasets/.venv/bin/activate"
SCRIPT="~/datasets/enwiki_index_fts.py"

# ── Step 1: copy the indexer script to the pi ────────────────────────────────
scp scripts/enwiki/enwiki_index_fts.py $PI:$SCRIPT

# ── Step 2: check available disk space on the pi ─────────────────────────────
ssh $PI "df -h ~/datasets/enwiki/enwiki.db"

# ── Step 3: timing test — index 10k articles and extrapolate ─────────────────
ssh -t $PI "bash -lc '$VENV && python3 $SCRIPT --limit 10000'"

# ── Step 4: full run (hours — consider running inside tmux on the pi) ─────────
ssh -t $PI "bash -lc '$VENV && python3 $SCRIPT'"

# ── Step 5: restart the remote server on the pi ──────────────────────────────
ssh $PI "tmux kill-session -t enwiki; tmux new-session -d -s enwiki 'cd ~/datasets && exec .venv/bin/uvicorn enwiki_remote_server:app --host 0.0.0.0 --port 8765 2>&1 | tee /tmp/enwiki.log'"

echo "Done. Restart local uvicorn to pick up the new FTS index."
