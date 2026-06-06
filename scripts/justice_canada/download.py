#!/usr/bin/env python3
"""Download and sync the consolidated Acts and regulations of Canada.

Source: the official Department of Justice XML repository
(https://github.com/justicecanada/laws-lois-xml), the authoritative source of
record for federal consolidated legislation. The repository mirrors the Justice
Laws Website and is generally updated about every two weeks. Each XML document
carries a ``lims:current-date`` attribute indicating the consolidation date it
is current to.

On-disk layout after sync (verified against the repository)::

    <destination>/eng/acts/*.xml          (~960 flat XML files, English)
    <destination>/eng/regulations/*.xml   (~4800 flat XML files, English)
    <destination>/fra/acts/*.xml          (French; only with --language fr/both)
    <destination>/fra/regulations/*.xml   (French; only with --language fr/both)
    <destination>/lookup/                 (index/lookup files)
    <destination>/regulation_web.dtd      (schema for validation)

Strategy:
    * Shallow (``--depth 1``) partial clone, sparse-checkout limited to the
      selected language(s), avoiding the unneeded half of the repository.
    * On subsequent runs, fetch and hard-reset to the remote tip. Because this
      is a read-only mirror and no local commits are kept, reset is more robust
      than ``pull --ff-only``, which can abort if upstream history was rewritten.

Requires git on PATH. No API key or authentication needed.
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DEST = REPO_ROOT / "data" / "justice_canada" / "laws-lois-xml"

REPOSITORY_URL: str = "https://github.com/justicecanada/laws-lois-xml.git"

# Non-cone sparse-checkout pattern building. A leading slash anchors to the
# repository root; a trailing slash marks a directory.
_LANG_CODES: dict[str, list[str]] = {"en": ["eng"], "fr": ["fra"], "both": ["eng", "fra"]}
_TYPE_DIRS: dict[str, list[str]] = {"acts": ["acts/"], "regulations": ["regulations/"], "both": [""]}


def build_patterns(language: str, doc_type: str) -> list[str]:
    """Return sparse-checkout patterns for the given language and document type."""
    patterns = [
        f"/{lang}/{type_dir}"
        for lang in _LANG_CODES[language]
        for type_dir in _TYPE_DIRS[doc_type]
    ]
    return patterns + ["/lookup/", "/regulation_web.dtd"]


def run_command(command: list[str], working_directory: Path | None = None) -> None:
    """Run a command, streaming its output; exit on failure."""
    print(f"  $ {' '.join(command)}")
    result = subprocess.run(command, cwd=working_directory)
    if result.returncode != 0:
        sys.exit(f"Command failed ({result.returncode}): {' '.join(command)}")


def run_command_and_capture_output(
    command: list[str], working_directory: Path | None = None
) -> tuple[int, str]:
    """Run a command and return (returncode, stdout)."""
    result = subprocess.run(
        command, cwd=working_directory, capture_output=True, text=True
    )
    return result.returncode, result.stdout.strip()


def is_target_repository(repository_path: Path) -> bool:
    """Return True if ``repository_path`` is a clone of :data:`REPOSITORY_URL`."""
    if not (repository_path / ".git").is_dir():
        return False
    return_code, remote_url = run_command_and_capture_output(
        ["git", "remote", "get-url", "origin"], working_directory=repository_path
    )
    if return_code != 0:
        return False
    normalized_url = remote_url.rstrip("/")
    if normalized_url.endswith(".git"):
        normalized_url = normalized_url[:-4]
    return normalized_url == REPOSITORY_URL[:-4]


def clone_repository(destination: Path, patterns: list[str]) -> None:
    """Shallow sparse clone the corpus into ``destination``."""
    print(f"Cloning corpus into {destination} ...")
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        "git", "clone",
        "--depth", "1",
        "--filter=blob:none",
        "--no-checkout",
        "--sparse",
        REPOSITORY_URL,
        str(destination),
    ])
    # Non-cone mode lets us mix directory subsets with a root-level file (DTD).
    run_command(
        ["git", "sparse-checkout", "set", "--no-cone", *patterns],
        working_directory=destination,
    )
    run_command(["git", "checkout"], working_directory=destination)
    print("Initial clone complete.")


def update_repository(destination: Path, patterns: list[str]) -> None:
    """Fetch and hard-reset an existing clone to the remote tip."""
    print(f"Updating existing corpus in {destination} ...")
    # Re-assert patterns in case they changed (e.g. --language flag changed).
    run_command(
        ["git", "sparse-checkout", "set", "--no-cone", *patterns],
        working_directory=destination,
    )
    run_command(["git", "fetch", "--depth", "1", "origin"], working_directory=destination)
    return_code, default_branch_ref = run_command_and_capture_output(
        ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
        working_directory=destination,
    )
    if return_code != 0 or not default_branch_ref:
        default_branch_ref = "origin/main"
    run_command(
        ["git", "reset", "--hard", default_branch_ref], working_directory=destination
    )
    print("Update complete.")


def print_corpus_summary(destination: Path) -> None:
    """Print a count of retrieved XML files for each synced language."""
    schema_file = destination / "regulation_web.dtd"
    print("\nCorpus summary:")
    found_any = False
    for lang_code, lang_name in [("eng", "English"), ("fra", "French")]:
        lang_dir = destination / lang_code
        if not lang_dir.is_dir():
            continue
        found_any = True
        acts_dir = lang_dir / "acts"
        regs_dir = lang_dir / "regulations"
        if acts_dir.is_dir():
            print(f"  {lang_name} Acts XML:        {len(list(acts_dir.rglob('*.xml')))}")
        if regs_dir.is_dir():
            print(f"  {lang_name} Regulations XML: {len(list(regs_dir.rglob('*.xml')))}")
    if not found_any:
        print("  Warning: no eng/ or fra/ directory found after sync.")
    print(f"  DTD schema present:    {schema_file.is_file()}")
    print(f"  Location:              {destination.resolve()}")


def main() -> None:
    """Parse arguments and clone or update the corpus accordingly."""
    parser = argparse.ArgumentParser(
        description="Download/sync the consolidated Acts & regulations of Canada."
    )
    parser.add_argument(
        "--dest",
        dest="destination",
        default=str(DEFAULT_DEST),
        help=f"Destination directory for the corpus clone (default: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--language",
        choices=["en", "fr", "both"],
        default="en",
        help="Language(s) to include: en (English), fr (French), both (default: en)",
    )
    parser.add_argument(
        "--type",
        dest="doc_type",
        choices=["acts", "regulations", "both"],
        default="both",
        help="Document type(s) to include: acts, regulations, or both (default: both)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without executing any git commands.",
    )
    arguments = parser.parse_args()
    destination = Path(arguments.destination)
    patterns = build_patterns(arguments.language, arguments.doc_type)

    if arguments.dry_run:
        if (destination / ".git").is_dir():
            print(f"[dry-run] Would update existing clone in {destination}")
            print(f"          Sparse patterns: {', '.join(patterns)}")
        elif destination.exists() and any(destination.iterdir()):
            sys.exit(
                f"{destination} exists and is not empty. "
                f"Choose an empty/new --dest or remove it."
            )
        else:
            print(f"[dry-run] Would clone {REPOSITORY_URL}")
            print(f"          --depth 1 --filter=blob:none --sparse → {destination}")
            print(f"          Sparse patterns: {', '.join(patterns)}")
        return

    if (destination / ".git").is_dir():
        if not is_target_repository(destination):
            sys.exit(
                f"{destination} is a git repository but its origin is not "
                f"{REPOSITORY_URL}. Choose a different --dest to avoid "
                f"clobbering it."
            )
        update_repository(destination, patterns)
    elif destination.exists() and any(destination.iterdir()):
        sys.exit(
            f"{destination} exists and is not empty. "
            f"Choose an empty/new --dest or remove it."
        )
    else:
        clone_repository(destination, patterns)

    print_corpus_summary(destination)


if __name__ == "__main__":
    main()
