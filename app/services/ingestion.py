import json
import logging
import urllib.parse
from datetime import date, datetime
from typing import Optional

import httpx

from app.config import (
    DHV_BASE_URL,
    GLIDER_CATEGORY,
    GLIDER_CLASSES,
    PAGE_SIZE,
    SITE_FILTER_PARAM,
    START_YEAR,
    load_regions,
)
from app.database import get_connection

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Column mapping: API response field → DB column name
# --------------------------------------------------------------------------- #

COLUMNS_MAP: dict[str, str] = {
    "IDFlight":              "dhv_flight_id",
    "FKTakeoffWaypoint":     "dhv_site_id",
    "TakeoffWaypointName":   "takeoff_site_name",
    "TakeoffCountry":        "takeoff_country",
    "FKPilot":               "pilot_id",
    "FlightDate":            "flight_date",
    "FlightDuration":        "flight_duration_sec",
    "Glider":                "glider_model",
    "GliderBrand":           "glider_brand",
    "GliderClassification":  "glider_class",
    "CompetitionClass":      "competition_class",
    "TakeoffAltitude":       "takeoff_altitude",
    "MaxAltitude":           "max_altitude",
    "MaxClimb":              "max_climb",
    "BestTaskDistance":      "best_task_distance_m",
    "BestTaskTypeKey":       "best_task_type_key",
    "BestTaskPoints":        "best_task_points",
}

DB_COLUMNS = list(COLUMNS_MAP.values())

# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def get_last_flight_year(site_ids: list[int]) -> Optional[int]:
    """Return the year of the latest stored flight for a set of site IDs, or None."""
    placeholders = ",".join("?" * len(site_ids))
    query = f"SELECT MAX(flight_date) FROM raw_flights WHERE dhv_site_id IN ({placeholders})"
    with get_connection() as conn:
        row = conn.execute(query, site_ids).fetchone()
        if row[0]:
            return datetime.strptime(row[0], "%Y-%m-%d").year
        return None


def insert_flights(rows: list[dict]) -> int:
    """INSERT OR IGNORE a batch of flight rows. Returns the number of new rows inserted."""
    if not rows:
        return 0

    placeholders = ",".join("?" * len(DB_COLUMNS))
    sql = (
        f"INSERT OR IGNORE INTO raw_flights ({','.join(DB_COLUMNS)}) "
        f"VALUES ({placeholders})"
    )

    with get_connection() as conn:
        data = [[row.get(col) for col in DB_COLUMNS] for row in rows]
        conn.executemany(sql, data)
        return conn.total_changes


# --------------------------------------------------------------------------- #
# Fetch helpers
# --------------------------------------------------------------------------- #

def build_fetch_url(site_ids: list[int], year: int, page: int) -> str:
    """
    Build the DHV-XC API URL for one page of flights.

    Filters applied:
      - Year:          y={year}
      - Category:      fkcat[]=1  (Gleitschirm / paraglider)
      - Glider class:  fkcls[]=1,2,3  (EN A, EN B, EN C; tandems included as B/C)
      - Site IDs:      fkto[]={ids}  (comma separated list of site ids)
      - Pagination:    navpars JSON with start/limit
    """
    navpars = json.dumps(
        {
            "start": page * PAGE_SIZE,
            "limit": PAGE_SIZE,
            "sort": [
                {"field": "FlightDate", "dir": -1},
                {"field": "BestTaskPoints", "dir": -1},
            ],
        },
        separators=(",", ":"),
    )

    params: list[tuple] = [
        ("y", year),
        ("fkcat[]", GLIDER_CATEGORY),
        *[("fkcls[]", cls) for cls in GLIDER_CLASSES],
        *[(SITE_FILTER_PARAM, sid) for sid in site_ids],
        ("navpars", navpars),
    ]

    return f"{DHV_BASE_URL}?{urllib.parse.urlencode(params)}"


def extract_row(flight: dict) -> dict:
    return {db_col: flight.get(api_key) for api_key, db_col in COLUMNS_MAP.items()}


# --------------------------------------------------------------------------- #
# Main ingestion entry point
# --------------------------------------------------------------------------- #

def ingest_region(
    region_key: str,
    force_year: Optional[int] = None,
    dry_run: bool = False,
) -> dict:
    """
    Idempotent incremental ingestion for one region.

    Strategy:
      1. Look up the region's dhv_site_ids from regions.json.
      2. Query the DB for the latest flight_date among those sites.
      3. Derive start_year:  2018 if no data, else last_year + 1.
      4. For each year from start_year → current year:
           - Paginate the DHV-XC API (page_size=500) until exhausted.
           - Filter returned rows to the region's site IDs (safety net).
           - INSERT OR IGNORE into raw_flights (skipped in dry-run mode).

    Args:
        region_key:  Key in regions.json (e.g. "greifenburg").
        force_year:  Override start_year (useful for backfill or re-fetch).
        dry_run:     If True, fetch and log but do NOT write to the database.

    Returns:
        Summary dict with years fetched and total rows inserted (or would-be inserted).
    """
    regions = load_regions()
    if region_key not in regions:
        raise ValueError(f"Unknown region key: '{region_key}'. Available: {list(regions)}")

    region = regions[region_key]
    site_ids: list[int] = region["dhv_site_ids"]
    site_id_set = set(site_ids)

    last_year = get_last_flight_year(site_ids)
    start_year = force_year or (START_YEAR if last_year is None else last_year + 1)
    current_year = date.today().year

    dry_tag = "  [DRY RUN — no writes]" if dry_run else ""
    logger.info(
        f"[{region_key}] sites={site_ids}  last_year_in_db={last_year}  "
        f"fetching {start_year}–{current_year}{dry_tag}"
    )

    summary: dict = {
        "region": region_key,
        "site_ids": site_ids,
        "start_year": start_year,
        "years": [],
        "total_inserted": 0,
    }

    for year in range(start_year, current_year + 1):
        page = 0
        year_fetched = 0
        year_inserted = 0

        while True:
            url = build_fetch_url(site_ids, year, page)
            logger.debug(f"  GET {url}")

            try:
                resp = httpx.get(url, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
            except httpx.HTTPStatusError as exc:
                logger.error(f"  HTTP {exc.response.status_code} on page {page}: {exc}")
                break
            except Exception as exc:
                logger.error(f"  Fetch error on page {page}: {exc}")
                break

            flights = payload.get("data", [])
            if not flights:
                logger.info(f"  [{region_key}] {year} page {page}: empty — done.")
                break

            year_fetched += len(flights)

            rows = [extract_row(f) for f in flights]

            # Safety net: drop rows that don't belong to our sites
            # (in case the API ignores the fktakeoff[] filter)
            rows = [r for r in rows if r.get("dhv_site_id") in site_id_set]

            if dry_run:
                inserted = len(rows)  # would-be insertions
                logger.info(
                    f"  [{region_key}] {year} page {page}: "
                    f"{len(flights)} fetched → {len(rows)} matched sites → "
                    f"{inserted} would be inserted [DRY RUN]"
                )
            else:
                inserted = insert_flights(rows)
                logger.info(
                    f"  [{region_key}] {year} page {page}: "
                    f"{len(flights)} fetched → {len(rows)} matched sites → {inserted} new rows"
                )
            year_inserted += inserted

            if len(flights) < PAGE_SIZE:
                # Last page reached
                break

            page += 1

        year_summary = {"year": year, "fetched": year_fetched, "inserted": year_inserted}
        summary["years"].append(year_summary)
        summary["total_inserted"] += year_inserted
        logger.info(f"[{region_key}] {year} complete: {year_inserted} new rows")

    return summary


def ingest_all_regions(
    force_year: Optional[int] = None,
    dry_run: bool = False,
) -> list[dict]:
    """Run ingest_region for every key in regions.json."""
    regions = load_regions()
    return [ingest_region(key, force_year=force_year, dry_run=dry_run) for key in regions]


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Ingest DHV-XC flight data into the local SQLite database."
    )
    parser.add_argument(
        "--region",
        required=True,
        metavar="KEY",
        help=(
            "Region key from regions.json to ingest "
            "(e.g. greifenburg, tolmin).  Use 'all' to ingest every region."
        ),
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        metavar="YYYY",
        help=(
            "Force ingestion to start from this calendar year "
            "(overrides the auto-detected last year in the DB)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch data and log what would be written, but do NOT write to the database.",
    )

    args = parser.parse_args()

    from app.database import init_db
    init_db()  # always: CREATE TABLE IF NOT EXISTS is idempotent; dry-run still needs the schema to query last year

    try:
        if args.region == "all":
            results = ingest_all_regions(force_year=args.year, dry_run=args.dry_run)
        else:
            results = [ingest_region(args.region, force_year=args.year, dry_run=args.dry_run)]
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    # Print final summary
    print()
    for r in results:
        tag = " [DRY RUN]" if args.dry_run else ""
        print(f"{'─'*55}")
        print(f"  Region : {r['region']}{tag}")
        print(f"  Sites  : {r['site_ids']}")
        for y in r["years"]:
            action = "would insert" if args.dry_run else "inserted"
            print(f"    {y['year']}  fetched={y['fetched']:>5}  {action}={y['inserted']:>5}")
        action = "would insert" if args.dry_run else "inserted"
        print(f"  Total {action}: {r['total_inserted']}")
    print(f"{'─'*55}")
