# paraglider-vacations

A FastAPI backend that ingests historical DHV-XC paraglider flight logs and exposes a ranked vacation-week recommendation endpoint. Consumed by a separate MCP server.

---

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

---

## Data Ingestion

Flight data is pulled from the public DHV-XC JSON API and stored in a local SQLite database (`glider_vacations.db`).

Run the ingestion script from the project root with the venv active:

```powershell
# Ingest a single region (auto-detects next missing year)
python -m app.services.ingestion --region greifenburg

# Ingest a specific region starting from a given year
python -m app.services.ingestion --region tolmin --year 2022

# Ingest all regions in regions.json
python -m app.services.ingestion --region all

# Dry run — fetch and log without writing to the database
python -m app.services.ingestion --region speikboden --dry-run
python -m app.services.ingestion --region all --year 2023 --dry-run
```

### How idempotency works

The ingestion is **safe to re-run**. Two layers protect against duplicates:

1. **Year-level skip** — on startup the script queries `MAX(flight_date)` for the region's site IDs. It only fetches years that come *after* the last year already in the database (or from 2018 if the DB is empty).
2. **Row-level dedup** — every batch is written with `INSERT OR IGNORE` keyed on `IDFlight` (the DHV-XC primary key), so partial re-fetches of the same year are safe.

### Fetch strategy

- **Unit of work**: one region × one calendar year, paginated in batches of 500.
- **Filters applied at the API**: paraglider category (`fkcat[]=1`), glider classes EN A / EN B / EN C (`fkcls[]=1,2,3`), and the region's DHV site IDs (`fktakeoff[]=...`).
- **Post-fetch safety filter**: returned rows are re-checked against the region's `dhv_site_ids` before insert, in case the API ignores the site filter.
- **Tandem detection**: tandem flights appear as EN B/C with `CompetitionClass = Tandem` — they are captured by the EN B/C filter and identified by the `competition_class` column in the DB.

### Stored columns (`raw_flights`)

| Column | Source API field |
|---|---|
| `dhv_flight_id` *(PK)* | `IDFlight` |
| `dhv_site_id` | `FKTakeoffWaypoint` |
| `takeoff_site_name` | `TakeoffWaypointName` |
| `takeoff_country` | `TakeoffCountry` |
| `pilot_id` | `FKPilot` |
| `flight_date` | `FlightDate` |
| `flight_duration_sec` | `FlightDuration` |
| `glider_model` | `Glider` |
| `glider_brand` | `GliderBrand` |
| `glider_class` | `GliderClassification` |
| `competition_class` | `CompetitionClass` |
| `takeoff_altitude` | `TakeoffAltitude` |
| `max_altitude` | `MaxAltitude` |
| `max_climb` | `MaxClimb` |
| `best_task_distance_m` | `BestTaskDistance` |
| `best_task_type_key` | `BestTaskTypeKey` |
| `best_task_points` | `BestTaskPoints` |

---

## API

Start the development server:

```powershell
uvicorn app.main:app --reload
```

### `POST /recommend`

Returns a ranked matrix of regions for a target calendar week, weighted by user preferences.

```json
{
  "date": "2025-06-15",
  "preferences": {
    "xc_style":          { "weight": 0.8 },
    "low_crowds":        { "weight": 0.6 },
    "beginner_friendly": { "weight": 0.0 },
    "alpine_ceiling":    { "weight": 0.5 },
    "short_drive":       { "weight": 0.3 }
  }
}
```

---

## Region Registry

Whitelisted regions and their DHV site IDs are defined in [`regions.json`](./regions.json).

Current regions: **Lijak · Meduno · Tolmin · Greifenburg · Speikboden · Gemona**
