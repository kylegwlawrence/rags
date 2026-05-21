#!/usr/bin/env python3
"""Fetch READMEs from GitHub repos discovered via awesome lists into SQLite.

Set GITHUB_TOKEN in the environment to raise the rate limit from 60 to 5000 req/hr.
Requires: requests
"""

import argparse
import os
import re
import sqlite3
import time
from typing import Optional

import requests

DEFAULT_DB = "./data/github/readmes.db"
DEFAULT_DELAY = 0.7

AWESOME_LISTS = [
    "sindresorhus/awesome",
    "vinta/awesome-python",
    "sorrycc/awesome-javascript",
    "avelino/awesome-go",
    "akullpp/awesome-java",
    "rust-unofficial/awesome-rust",
    "josephmisiti/awesome-machine-learning",
    "ChristosChristofidis/awesome-deep-learning",
    "academic/awesome-datascience",
    "vsouza/awesome-ios",
    "sindresorhus/awesome-nodejs",
    "enaqx/awesome-react",
    "awesome-selfhosted/awesome-selfhosted",
    "Hack-with-Github/Awesome-Hacking",
    "sindresorhus/awesome-electron",
]

BRANCHES = ["main", "master"]
README_NAMES = ["README.md", "readme.md", "Readme.md", "README.rst", "README.markdown"]
REPO_PATTERN = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")

# First-path-segment values that are GitHub site routes, not repo owners.
NON_OWNER_PATHS = frozenset({
    "sponsors", "topics", "about", "features", "pricing", "marketplace",
    "orgs", "users", "settings", "notifications", "login", "join", "apps",
    "collections", "events", "explore", "search", "stars", "watching",
    "dashboard", "account",
})
# Second-path-segment values that are repo sub-pages, not repo names.
NON_REPO_PATHS = frozenset({
    "", "blob", "tree", "wiki", "issues", "pulls", "releases", "tags",
    "commits", "commit", "graphs", "network", "archive", "branches",
    "actions", "security", "pulse", "projects", "discussions", "compare",
    "raw", "blame", "find", "settings", "watchers", "stargazers", "forks",
    "milestones", "labels",
})


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS readmes (
            repo        TEXT PRIMARY KEY,
            owner       TEXT,
            name        TEXT,
            source_list TEXT,
            readme      TEXT,
            status      TEXT
        );
    """)
    # Migrate DBs created before the status column existed, so miss-tracking
    # works against an already-populated readmes.db.
    cols = {row[1] for row in cur.execute("PRAGMA table_info(readmes)")}
    if "status" not in cols:
        cur.execute("ALTER TABLE readmes ADD COLUMN status TEXT")


def make_session() -> requests.Session:
    """Build a requests session, attaching GITHUB_TOKEN if set."""
    session = requests.Session()
    token = os.environ.get("GITHUB_TOKEN")
    headers: dict[str, str] = {"User-Agent": "readme-fetcher"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        print("Note: GITHUB_TOKEN not set — unauthenticated requests are limited to 60/hr.")
    session.headers.update(headers)
    return session


def fetch_raw(session: requests.Session, owner: str, repo: str, filename: str,
              branch: str, max_retries: int = 5) -> Optional[str]:
    """Fetch one raw file from GitHub.

    Sleeps and retries only on genuine rate-limit responses (capped at
    ``max_retries``). A 403 that carries no rate-limit signal is treated as a
    permanent miss rather than retried forever.
    """
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
    retries = 0
    while True:
        try:
            r = session.get(url, timeout=30)
        except requests.RequestException:
            return None

        if r.status_code == 200:
            return r.text
        if r.status_code == 404:
            return None

        if r.status_code in (403, 429):
            retry_after = r.headers.get("Retry-After")
            reset_ts = r.headers.get("X-RateLimit-Reset")
            remaining = r.headers.get("X-RateLimit-Remaining")
            # Only a depleted budget (remaining == 0) or an explicit Retry-After
            # means "come back later". Any other 403 is a permanent denial.
            is_rate_limited = remaining == "0" or retry_after is not None
            if not is_rate_limited:
                return None
            retries += 1
            if retries > max_retries:
                print(f"  Giving up on {owner}/{repo}/{filename} after {max_retries} rate-limit retries")
                return None
            if reset_ts:
                sleep_for = max(0, int(reset_ts) - int(time.time())) + 5
            elif retry_after:
                sleep_for = int(retry_after) + 1
            else:
                sleep_for = 60
            print(f"  Rate limited — sleeping {sleep_for}s...")
            time.sleep(sleep_for)
            continue

        return None


def fetch_readme(session: requests.Session, owner: str, repo: str) -> Optional[str]:
    """Try common branch/filename combos and return the first hit."""
    for branch in BRANCHES:
        for name in README_NAMES:
            content = fetch_raw(session, owner, repo, name, branch)
            if content is not None:
                return content
    return None


def discover_repos(session: requests.Session, delay: float) -> dict[tuple[str, str], str]:
    """Parse all awesome lists and return {(owner, repo): source_list}."""
    discovered: dict[tuple[str, str], str] = {}
    print("Parsing awesome lists...")
    for awesome in AWESOME_LISTS:
        a_owner, a_repo = awesome.split("/")
        print(f"\n  Fetching list: {awesome}")
        list_content = fetch_readme(session, a_owner, a_repo)
        if not list_content:
            print(f"    Could not fetch {awesome} — skipping")
            time.sleep(delay)
            continue

        count = 0
        for owner, repo in REPO_PATTERN.findall(list_content):
            repo = repo.replace(".git", "").rstrip(".")
            if owner.lower() in NON_OWNER_PATHS:
                continue
            if repo.lower() in NON_REPO_PATHS:
                continue
            key = (owner, repo)
            if key not in discovered:
                discovered[key] = awesome
                count += 1
        print(f"    Found {count} new repos (total discovered: {len(discovered)})")
        time.sleep(delay)

    return discovered


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch READMEs from GitHub repos found in awesome lists."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Seconds between requests (default: {DEFAULT_DELAY})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of new repos to fetch this run (default: no cap). "
                             "Repos already recorded in the DB don't count against it.")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    session = make_session()
    discovered = discover_repos(session, args.delay)
    print(f"\nTotal unique repos discovered: {len(discovered)}")

    print("\nFetching individual repo READMEs...")
    total_fetched = 0
    total_missing = 0
    attempted = 0

    for i, ((owner, repo), source_list) in enumerate(discovered.items(), 1):
        repo_full = f"{owner}/{repo}"

        # Skip repos already recorded (fetched OR missing) so re-runs don't
        # repeat network calls — including the up-to-10 attempts a miss costs.
        cur.execute("SELECT 1 FROM readmes WHERE repo = ?", (repo_full,))
        if cur.fetchone():
            continue

        if args.limit is not None and attempted >= args.limit:
            print(f"  Reached --limit {args.limit} new repos — stopping.")
            break
        attempted += 1

        readme = fetch_readme(session, owner, repo)
        if readme:
            cur.execute("""
                INSERT OR IGNORE INTO readmes (repo, owner, name, source_list, readme, status)
                VALUES (?, ?, ?, ?, ?, 'fetched')
            """, (repo_full, owner, repo, source_list, readme))
            total_fetched += cur.rowcount
        else:
            # Record the miss so future runs skip it instead of re-attempting.
            cur.execute("""
                INSERT OR IGNORE INTO readmes (repo, owner, name, source_list, readme, status)
                VALUES (?, ?, ?, ?, NULL, 'missing')
            """, (repo_full, owner, repo, source_list))
            total_missing += cur.rowcount

        if attempted % 50 == 0:
            con.commit()
            print(f"  Attempted {attempted} (scanned {i}/{len(discovered)}) — fetched {total_fetched}, missing {total_missing}")

        time.sleep(args.delay)

    con.commit()
    con.close()
    print(f"\nDone. READMEs fetched: {total_fetched}, missing: {total_missing}")


if __name__ == "__main__":
    main()
