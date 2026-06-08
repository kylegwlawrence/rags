"""Compress the Gutenberg book shards (gutenberg/0 .. gutenberg/9) with zstd.

Each shard folder is bundled into its own ``{n}.tar.zst`` archive via
``tar`` piped through ``zstd``. Originals are left in place; delete them
manually once you have verified the archives.

Run from the repo root with the venv active::

    python scripts/gutenberg/gutenberg_archive.py
    python scripts/gutenberg/gutenberg_archive.py --level 12 --force
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Shard folders to archive: data/gutenberg/0 .. data/gutenberg/9.
SHARDS = [str(n) for n in range(10)]


def human_size(num_bytes: int) -> str:
    """Return a human-readable byte count (e.g. ``"9.8G"``)."""
    size = float(num_bytes)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


def dir_size(path: Path) -> int:
    """Total size in bytes of every file under ``path`` (follows the tree)."""
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def archive_shard(
    base_dir: Path,
    shard: str,
    out_dir: Path,
    level: int,
    threads: int,
    force: bool,
) -> bool:
    """Compress one shard folder into ``out_dir/{shard}.tar.zst``.

    Returns ``True`` if an archive was created, ``False`` if it was skipped
    (missing source, or an archive already exists and ``force`` is off).
    """
    source = base_dir / shard
    if not source.is_dir():
        print(f"  skip {shard}: no such folder ({source})")
        return False

    out_path = out_dir / f"{shard}.tar.zst"
    if out_path.exists() and not force:
        print(f"  skip {shard}: {out_path.name} already exists (use --force to rebuild)")
        return False

    src_bytes = dir_size(source)
    print(f"  archiving {shard}/  ({human_size(src_bytes)}) -> {out_path.name} ...")

    # Explicit pipe (not `tar -I 'zstd ...'`) so zstd's inherited stderr shows
    # its live progress counter per shard. -C base_dir stores the relative
    # "shard/..." path so the archive extracts cleanly anywhere.
    tar_proc = subprocess.Popen(
        ["tar", "-cf", "-", "-C", str(base_dir), shard],
        stdout=subprocess.PIPE,
    )
    zstd_proc = subprocess.Popen(
        ["zstd", f"-{level}", f"-T{threads}", "-f", "-o", str(out_path)],
        stdin=tar_proc.stdout,
    )
    # Let tar receive SIGPIPE if zstd dies, and avoid a dangling handle.
    if tar_proc.stdout is not None:
        tar_proc.stdout.close()

    zstd_rc = zstd_proc.wait()
    tar_rc = tar_proc.wait()
    if tar_rc != 0 or zstd_rc != 0:
        # Drop a partial archive so a re-run starts clean.
        out_path.unlink(missing_ok=True)
        print(
            f"  ERROR archiving {shard}: tar exited {tar_rc}, zstd exited {zstd_rc}",
            file=sys.stderr,
        )
        raise subprocess.CalledProcessError(zstd_rc or tar_rc, "tar | zstd")

    out_bytes = out_path.stat().st_size
    ratio = src_bytes / out_bytes if out_bytes else 0
    print(
        f"  done {shard}: {human_size(src_bytes)} -> {human_size(out_bytes)} "
        f"({ratio:.1f}x smaller)"
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("data/gutenberg"),
        help="Folder holding the numbered shards (default: data/gutenberg).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Where to write the archives (default: <base-dir>/archives).",
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
        "--folder",
        default=None,
        help="Archive only this one shard folder (e.g. --folder 0); "
        "default is all of 0-9.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild archives even if they already exist.",
    )
    args = parser.parse_args()

    if shutil.which("zstd") is None or shutil.which("tar") is None:
        sys.exit("error: both 'tar' and 'zstd' must be installed and on PATH.")

    base_dir = args.base_dir.resolve()
    if not base_dir.is_dir():
        sys.exit(f"error: base dir not found: {base_dir}")

    out_dir = (args.out_dir or base_dir / "archives").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"base dir : {base_dir}")
    print(f"out dir  : {out_dir}")
    print(f"zstd     : level {args.level}, threads {args.threads or 'all'}")
    print()

    shards = [args.folder] if args.folder is not None else SHARDS

    created = 0
    for shard in shards:
        if archive_shard(base_dir, shard, out_dir, args.level, args.threads, args.force):
            created += 1

    print()
    print(f"finished: {created} archive(s) written to {out_dir}")
    print("originals left in place — delete them manually after verifying.")


if __name__ == "__main__":
    main()
