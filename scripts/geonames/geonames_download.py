#!/usr/bin/env python3
"""Download the GeoNames allCountries dataset and parse it into SQLite."""

import argparse
import csv
import os
import sqlite3
import sys
import zipfile

import requests

DEFAULT_DB = "./data/geonames/geonames.db"
DEFAULT_DOWNLOAD_DIR = "./data/geonames/raw"

ALLCOUNTRIES_URL = "https://download.geonames.org/export/dump/allCountries.zip"
COUNTRYINFO_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
FEATURECODES_URL = "https://download.geonames.org/export/dump/featureCodes_en.txt"

# allCountries.txt columns (tab-separated, no header)
COLUMNS = [
    "geonameid", "name", "asciiname", "alternatenames",
    "latitude", "longitude", "feature_class", "feature_code",
    "country_code", "cc2", "admin1_code", "admin2_code",
    "admin3_code", "admin4_code", "population", "elevation",
    "dem", "timezone", "modification_date",
]


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS places (
            geonameid    INTEGER PRIMARY KEY,
            name         TEXT,
            latitude     REAL,
            longitude    REAL,
            feature_class TEXT,
            feature_code TEXT,
            feature_description TEXT,
            country_code TEXT,
            country_name TEXT,
            population   INTEGER,
            elevation    INTEGER,
            timezone     TEXT,
            sentence     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_places_name ON places(name);
        CREATE INDEX IF NOT EXISTS idx_places_country_feature
            ON places(country_code, feature_class, feature_code);
        CREATE INDEX IF NOT EXISTS idx_places_population ON places(population);
    """)


def download_file(url: str, dest: str) -> None:
    """Download a file, skipping if already present. Writes atomically via a .tmp sibling."""
    if os.path.exists(dest):
        print(f"  Already downloaded: {os.path.basename(dest)}")
        return
    print(f"  Downloading {url}...")
    tmp = dest + ".tmp"
    try:
        r = requests.get(url, stream=True, timeout=300)
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        os.replace(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load_country_names(path: str) -> dict[str, str]:
    """Build ISO country code → name lookup from countryInfo.txt."""
    names: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) > 4:
                names[parts[0]] = parts[4]
    return names


def load_feature_descriptions(path: str) -> dict[str, str]:
    """Build feature-code → description lookup from featureCodes_en.txt."""
    descs: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.split("\t")
            if len(parts) >= 2:
                descs[parts[0]] = parts[1]
    return descs


def make_sentence(
    name: str,
    feature_class: str,
    feature_code: str,
    country_name: str,
    population: int,
    lat: float | None,
    lon: float | None,
    timezone: str,
    feature_descriptions: dict[str, str],
) -> str:
    """Generate a natural language description suitable for embedding."""
    feature_key = f"{feature_class}.{feature_code}"
    feature_desc = feature_descriptions.get(feature_key, "place")

    parts = [f"{name} is a {feature_desc}"]
    if country_name:
        parts.append(f"in {country_name}")
    if lat is not None and lon is not None:
        parts.append(f"located at {lat}, {lon}")
    if population and population > 0:
        parts.append(f"with a population of {population:,}")
    if timezone:
        parts.append(f"in the {timezone} timezone")
    return ", ".join(parts) + "."


def main() -> None:
    parser = argparse.ArgumentParser(description="Download GeoNames allCountries into SQLite.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR,
                        help=f"Directory for downloaded files (default: {DEFAULT_DOWNLOAD_DIR})")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after inserting this many rows (for testing)")
    parser.add_argument("--reset", action="store_true",
                        help="Drop and recreate the places table before importing")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(args.download_dir, exist_ok=True)

    # sys.maxsize overflows the C long limit on Linux; cap at 2^31-1
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

    # Download lookup files
    print("Downloading lookup files...")
    countryinfo_path = os.path.join(args.download_dir, "countryInfo.txt")
    featurecodes_path = os.path.join(args.download_dir, "featureCodes_en.txt")
    download_file(COUNTRYINFO_URL, countryinfo_path)
    download_file(FEATURECODES_URL, featurecodes_path)

    country_names = load_country_names(countryinfo_path)
    feature_descriptions = load_feature_descriptions(featurecodes_path)

    # Download main dataset
    print("Downloading allCountries.zip (~330 MB)...")
    zip_path = os.path.join(args.download_dir, "allCountries.zip")
    download_file(ALLCOUNTRIES_URL, zip_path)

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    if args.reset:
        print("Resetting places table...")
        cur.execute("DROP TABLE IF EXISTS places")
        con.commit()
    create_schema(cur)
    con.commit()

    print("Parsing allCountries.txt and inserting...")
    total = 0
    with zipfile.ZipFile(zip_path, "r") as z:
        with z.open("allCountries.txt") as txt_file:
            reader = csv.reader(
                (line.decode("utf-8") for line in txt_file),
                delimiter="\t",
            )
            for row in reader:
                if len(row) < 19:
                    continue
                record = dict(zip(COLUMNS, row))

                try:
                    geonameid = int(record["geonameid"])
                    lat  = float(record["latitude"])  if record["latitude"]  else None
                    lon  = float(record["longitude"]) if record["longitude"] else None
                    pop  = int(record["population"])  if record["population"] else 0
                    elev = int(record["elevation"]) if record["elevation"] else None
                    if elev == -9999:  # GeoNames missing-elevation sentinel
                        elev = None
                except ValueError:
                    continue

                country_name = country_names.get(record["country_code"], "")
                sentence = make_sentence(
                    record["name"], record["feature_class"], record["feature_code"],
                    country_name, pop, lat, lon, record["timezone"],
                    feature_descriptions,
                )

                feature_key = f"{record['feature_class']}.{record['feature_code']}"
                feature_description = feature_descriptions.get(feature_key, "")

                cur.execute("""
                    INSERT OR IGNORE INTO places
                    (geonameid, name, latitude, longitude, feature_class, feature_code,
                     feature_description, country_code, country_name, population,
                     elevation, timezone, sentence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    geonameid, record["name"], lat, lon,
                    record["feature_class"], record["feature_code"],
                    feature_description, record["country_code"], country_name,
                    pop, elev, record["timezone"], sentence,
                ))
                total += cur.rowcount

                if total > 0 and total % 50000 == 0:
                    con.commit()
                    print(f"  {total} places inserted...")

                if args.limit and total >= args.limit:
                    break

    con.commit()
    con.close()

    print(f"\nDone. {total} places inserted into {args.db}")
    print(f"Raw files kept in {args.download_dir} — delete manually if not needed.")


if __name__ == "__main__":
    main()
