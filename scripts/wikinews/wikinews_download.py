#!/usr/bin/env python3
"""Download and SHA-1-verify the latest English Wikinews multistream dump.

Fetches both the article bz2 and its index from
``https://dumps.wikimedia.org/enwikinews/latest/`` into
``data/wikinews/dumps/``. Each file is streamed to a ``.tmp`` sibling and
atomically renamed only after the SHA-1 matches Wikimedia's manifest, so an
interrupted transfer can never leave a partial file under the canonical name.
Re-running with files already in place skips them if they verify.

English Wikinews closed in May 2026 — the dump is a static archive.
"""

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DUMPS_DIR = REPO_ROOT / "data" / "wikinews" / "dumps"

WIKI = "enwikinews"
BASE_URL = "https://dumps.wikimedia.org"
# enwikinews is small enough that Wikimedia only provides the plain (non-multistream)
# articles dump — no random-access index file. We stream it once, which is fine.
TARGET_SUFFIXES = ("-pages-articles.xml.bz2",)

CHUNK_BYTES = 1 << 20  # 1 MiB
_HTTP_RETRIES = 3


class _FileResult(NamedTuple):
    filename: str
    size: int
    sha1: str
    status: str


def _client() -> httpx.Client:
    """httpx client with connection-level retries for transient network blips."""
    return httpx.Client(transport=httpx.HTTPTransport(retries=_HTTP_RETRIES))


def fetch_sha1sums() -> dict[str, str]:
    """Fetch the enwikinews sha1sums manifest, filtered to TARGET_SUFFIXES.

    Returns:
        Map ``{filename: hex_sha1}`` for the article bz2 dump.

    Raises:
        RuntimeError: If the manifest is missing a target file.
    """
    url = f"{BASE_URL}/{WIKI}/latest/{WIKI}-latest-sha1sums.txt"
    with _client() as client:
        resp = client.get(url, timeout=30.0)
        resp.raise_for_status()

    prefix = f"{WIKI}-"
    manifest: dict[str, str] = {}
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        sha1, _, filename = line.partition("  ")
        if not filename:
            continue
        if filename.startswith(prefix) and filename.endswith(TARGET_SUFFIXES):
            manifest[filename] = sha1

    matched = {s for f in manifest for s in TARGET_SUFFIXES if f.endswith(s)}
    missing = set(TARGET_SUFFIXES) - matched
    if missing:
        raise RuntimeError(
            f"sha1sums manifest for {WIKI} is missing target file(s): {sorted(missing)}"
        )
    return manifest


def hash_file(path: Path) -> str:
    """Return the lowercase hex SHA-1 digest of ``path``, hashed in 1 MiB blocks."""
    h = hashlib.sha1()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK_BYTES), b""):
            h.update(block)
    return h.hexdigest()


def verify_existing(path: Path, expected_sha1: str) -> bool:
    """Return True if ``path`` exists and its SHA-1 matches ``expected_sha1``."""
    if not path.exists():
        return False
    return hash_file(path) == expected_sha1


def download_with_verify(url: str, dest: Path, expected_sha1: str) -> None:
    """Stream a URL to ``dest`` via a ``.tmp`` sibling, verifying SHA-1 on completion.

    Atomically renames the tmp file onto ``dest`` only after the hash matches,
    so a failed transfer or hash mismatch leaves ``dest`` unchanged (or
    nonexistent on first attempt). The tmp file is removed on any failure.

    Raises:
        RuntimeError: On SHA-1 mismatch.
        httpx.HTTPStatusError: On non-2xx response.
    """
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    h = hashlib.sha1()
    bytes_read = 0
    try:
        with _client() as client, client.stream(
            "GET", url, timeout=None, follow_redirects=True
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0)) or None
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=CHUNK_BYTES):
                    f.write(chunk)
                    h.update(chunk)
                    bytes_read += len(chunk)
                    if total:
                        pct = 100.0 * bytes_read / total
                        sys.stderr.write(
                            f"\r  {dest.name}: {bytes_read/1e6:.1f}/{total/1e6:.1f} MB ({pct:.1f}%)"
                        )
                    else:
                        sys.stderr.write(f"\r  {dest.name}: {bytes_read/1e6:.1f} MB")
                    sys.stderr.flush()
            sys.stderr.write("\n")

        actual = h.hexdigest()
        if actual != expected_sha1:
            raise RuntimeError(
                f"SHA-1 mismatch for {dest.name}: expected {expected_sha1}, got {actual}"
            )
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """Download each target file into DUMPS_DIR. Exit 0 on success, 1 if any failed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = fetch_sha1sums()
    results: list[_FileResult] = []
    failed = False
    date_re = re.compile(rf"^{re.escape(WIKI)}-(\d{{8}})-")

    for filename, sha1 in manifest.items():
        dest = DUMPS_DIR / filename
        m = date_re.match(filename)
        if not m:
            results.append(
                _FileResult(filename, 0, sha1, f"FAILED: cannot extract date from {filename!r}")
            )
            failed = True
            continue
        # Use the dated subdirectory URL so a slow download won't race a "new
        # latest" rollover mid-transfer.
        url = f"{BASE_URL}/{WIKI}/{m.group(1)}/{filename}"

        try:
            if dest.exists():
                print(f"checking existing {filename} ...", file=sys.stderr, flush=True)
            if verify_existing(dest, sha1):
                status = "skipped (already verified)"
            else:
                download_with_verify(url, dest, sha1)
                status = "ok"
        except (RuntimeError, httpx.HTTPError, OSError) as e:
            status = f"FAILED: {e}"
            failed = True

        size = dest.stat().st_size if dest.exists() else 0
        results.append(_FileResult(filename, size, sha1, status))

    print("\n=== summary ===")
    for r in results:
        print(f"{r.filename}\n  size={r.size:,} bytes\n  sha1={r.sha1}\n  status={r.status}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
