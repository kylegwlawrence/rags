#!/usr/bin/env python3
"""
Compress the per-category arxiv shards with zstd, keeping a chosen few live.

Every ``{parent}.db`` under data/arxiv/categories/ is compressed to
data/arxiv/archives/{parent}.db.zst, EXCEPT the parents named in ``--keep``
(default: math, math-ph, physics) which stay live and uncompressed.

After each archive is written it is integrity-checked with ``zstd -t``; only
once that passes is the original ``{parent}.db`` (and any leftover ``-wal`` /
``-shm`` sidecar) deleted. Pass ``--keep-originals`` to leave them for a manual
check instead.

Unarchiving later: ``zstd -d archives/{parent}.db.zst -o categories/{parent}.db``
then restart uvicorn — the API picks the shard up automatically.

Run from the repo root with the venv active::

    python scripts/arxiv/arxiv_archive.py
    python scripts/arxiv/arxiv_archive.py --keep math,math-ph,physics,stat
    python scripts/arxiv/arxiv_archive.py --level 12 --force --keep-originals
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Parents that stay live (uncompressed) unless overridden with --keep.
DEFAULT_KEEP = ["math", "math-ph", "physics"]


def human_size(num_bytes: int) -> str:
    """Return a human-readable byte count (e.g. ``"9.8G"``)."""
    size = float(num_bytes)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


def archive_shard(
    db_path: Path,
    out_dir: Path,
    level: int,
    threads: int,
    force: bool,
    delete_original: bool,
) -> bool:
    """Compress one ``{parent}.db`` into ``out_dir/{parent}.db.zst``.

    The archive is verified with ``zstd -t`` before the original is removed.
    Returns ``True`` if an archive was created, ``False`` if it was skipped
    (already exists and ``force`` is off).
    """
    out_path = out_dir / f"{db_path.name}.zst"
    if out_path.exists() and not force:
        print(f"  skip {db_path.stem}: {out_path.name} already exists (use --force)")
        return False

    src_bytes = db_path.stat().st_size
    print(f"  archiving {db_path.name}  ({human_size(src_bytes)}) -> {out_path.name} ...")

    # Compress the single DB file. -T0 = all cores; -f overwrites any partial
    # archive from a previous failed run; -o names the output explicitly.
    rc = subprocess.run(
        ["zstd", f"-{level}", f"-T{threads}", "-f", "-o", str(out_path), str(db_path)]
    ).returncode
    if rc != 0:
        out_path.unlink(missing_ok=True)  # drop a partial so a re-run starts clean
        print(f"  ERROR compressing {db_path.stem}: zstd exited {rc}", file=sys.stderr)
        raise subprocess.CalledProcessError(rc, "zstd")

    # Integrity-check the archive before we trust it enough to delete the source.
    verify = subprocess.run(
        ["zstd", "-t", str(out_path)], capture_output=True
    ).returncode
    if verify != 0:
        out_path.unlink(missing_ok=True)
        print(
            f"  ERROR verifying {out_path.name}: zstd -t exited {verify} — "
            "original kept",
            file=sys.stderr,
        )
        raise subprocess.CalledProcessError(verify, "zstd -t")

    out_bytes = out_path.stat().st_size
    ratio = src_bytes / out_bytes if out_bytes else 0
    print(
        f"  done {db_path.stem}: {human_size(src_bytes)} -> "
        f"{human_size(out_bytes)} ({ratio:.1f}x smaller)"
    )

    if delete_original:
        db_path.unlink()
        # WAL shards can leave empty -wal / -shm sidecars behind.
        for suffix in ("-wal", "-shm"):
            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
        print(f"  removed original {db_path.name} (verified)")

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("data/arxiv/categories"),
        help="Folder holding the {parent}.db shards (default: data/arxiv/categories).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Where to write archives (default: <base-dir>/../archives, "
        "i.e. data/arxiv/archives).",
    )
    parser.add_argument(
        "--keep",
        default=",".join(DEFAULT_KEEP),
        help="Comma-separated parents to leave live, NOT archive "
        f"(default: {','.join(DEFAULT_KEEP)}).",
    )
    parser.add_argument(
        "--level",
        type=int,
        default=10,
        help="zstd compression level 1-22 (default: 10).",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="zstd threads; 0 = all cores (default: 0).",
    )
    parser.add_argument(
        "--keep-originals",
        action="store_true",
        help="Do NOT delete the .db after a verified archive (delete manually).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild archives even if they already exist.",
    )
    args = parser.parse_args()

    if shutil.which("zstd") is None:
        sys.exit("error: 'zstd' must be installed and on PATH.")

    base_dir = args.base_dir.resolve()
    if not base_dir.is_dir():
        sys.exit(f"error: base dir not found: {base_dir}")

    out_dir = (args.out_dir or base_dir.parent / "archives").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    keep = {p.strip() for p in args.keep.split(",") if p.strip()}

    # Every shard except the keep-list, busiest-looking first is irrelevant here
    # so just go alphabetically for a stable, predictable order.
    shards = sorted(p for p in base_dir.glob("*.db") if p.stem not in keep)

    print(f"base dir : {base_dir}")
    print(f"out dir  : {out_dir}")
    print(f"keep live: {', '.join(sorted(keep)) or '(none)'}")
    print(f"zstd     : level {args.level}, threads {args.threads or 'all'}")
    print(f"delete   : {'no (--keep-originals)' if args.keep_originals else 'yes, after verify'}")
    print(f"to archive ({len(shards)}): {', '.join(p.stem for p in shards) or '(none)'}")
    print()

    created = 0
    for db_path in shards:
        if archive_shard(
            db_path,
            out_dir,
            args.level,
            args.threads,
            args.force,
            delete_original=not args.keep_originals,
        ):
            created += 1

    print()
    print(f"finished: {created} archive(s) written to {out_dir}")
    if args.keep_originals:
        print("originals left in place — delete them manually after verifying.")


if __name__ == "__main__":
    main()
