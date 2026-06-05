# paraglider-vacations

A FastAPI app that ingests historical DHV-XC paraglider flight logs, aggregates them onto a calendar-week grid, and ranks vacation regions for a target week by user-weighted preferences. Exposes a web UI, an HTTP API (consumed by a separate MCP server), and CLIs for review.

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

## Seasonal Aggregation

Maps every historical flight (2018–2025+) onto the **2026 ISO-week calendar** and
buckets it into the week whose midpoint (Thursday) lies within `±window_days` of
the flight's calendar position. Cross-year matching uses a *circular day-of-year*
axis so the same slice of the season lines up regardless of the original year
(see [PLAN.md](./PLAN.md) §7). At the default `window=3` the windows tile the year
(each flight lands in one week); widening it makes neighbouring weeks overlap.

This is the **raw data layer** — counts and levels only. Feature representation
(ratios), polarity and normalization live in the [scoring layer](#scoring--recommendations).

```powershell
# All (week × region) buckets for 2026, ±3 day window
python -m app.services.aggregation

# A single ISO week
python -m app.services.aggregation --week 25

# One region, wider window
python -m app.services.aggregation --region greifenburg --window 5

# Different reference year
python -m app.services.aggregation --year 2027
```

Output is a plain pandas `DataFrame`, one row per `(iso_week, region)`:

| Column | Meaning |
|---|---|
| `year`, `iso_week` | Target 2026 week |
| `region_key` | Region key from [`regions.json`](./regions.json) |
| `flights_in_window` | Flights matched to this week (data-coverage proxy / ratio denominator) |
| `flying_days` | Distinct calendar days (across years) with ≥1 flight |
| `observed_day_slots` | `window_span × candidate_years` — the flyability denominator |
| `flyability` | `flying_days / observed_day_slots` (capped at 1.0) |
| `mean_duration_h` | Arithmetic-mean flight duration (hours) |
| `p67_duration_sec` | 67th-pct airtime |
| `expected_weekly_airtime_h` | `flyability × mean_duration × 7` |
| `flights_per_flyable_day` | Crowd-density proxy (flights ÷ flying days) |
| `distinct_pilots` | `COUNT(DISTINCT pilot_id)` |
| `fai_triangle_count` | FAI-triangle count |
| `en_a_count`, `en_b_count` | EN A / EN B flight counts |
| `tandem_count` | `CompetitionClass = Tandem` — commercialism proxy |
| `p67_xc_points` | 67th-pct XC points (`BestTaskPoints`) |
| `median_max_altitude` | Alpine-ceiling proxy |
| `years_covered` | Source years contributing to the bucket |

> Flyability also absorbs **popularity**: DHV-XC only logs *flown* days (no
> "unflyable day" record), so a quiet region reads as less flyable.

To use it from Python:

```python
from app.services.aggregation import aggregate

df = aggregate(window_days=3, year=2026)
```

---

## Scoring & Recommendations

The scoring layer ([`app/services/scoring.py`](./app/services/scoring.py)) turns the
raw aggregation rows into a **ranked region matrix** for a target week, driven by
per-feature preference **weights** (`0.0–1.0`). The data layer stays raw; the scoring
layer owns feature derivation, polarity and normalization via a **feature registry**:

| Feature | Derivation | Higher is better |
|---|---|---|
| `xc_style` | `fai_triangle_count / flights_in_window` | yes |
| `low_crowds` | `flights_per_flyable_day` | **no (inverted)** |
| `beginner_friendly` | `en_a_count / flights_in_window` | yes |
| `alpine_ceiling` | `median_max_altitude` | yes |
| `short_drive` | `travel_from_hannover.car_hours` (from `regions.json`) | **no (inverted)** |
| `airtime` | `expected_weekly_airtime_h` | yes |

**Two ranking methods**, both weight-driven:

- **`minmax`** *(default)* — Min-Max normalize each feature across the region set to
  `[0,1]` (flipped for lower-is-better), then `total_score = Σ(weight × norm) / Σ(weight)`.
- **`rrf`** — Reciprocal Rank Fusion: rank per feature, `Σ(weight × 1/(k+rank))` with
  a small fusion constant (`k=10`) suited to the small region set.

When no weights are supplied, the **default profile is airtime-only** (`{"airtime": 1.0}`).

### Web UI

```powershell
uvicorn app.main:app --reload     # http://localhost:3980/
```

The page at `/` is the manual-review surface: a date picker, a Min-Max/RRF dropdown,
and a weight input per feature. The grid shows each region's rank, `total_score`, and
per-feature **normalized score · raw value** so you can see *why* a region ranked where
it did. Tweak weights → resubmit → live re-rank.

### HTTP API

Both live under the app prefix (`{VACATIONS_APP_PREFIX}/api`); interactive docs at `/docs`.

```
GET  /api/recommend?date=2026-06-15
GET  /api/recommend?date=2026-06-15&method=rrf&xc_style=0.8&low_crowds=0.5&airtime=0
POST /api/recommend
```

`GET` applies the default profile and accepts per-feature overrides via
`?<feature>=<0..1>` plus `?method=minmax|rrf`. `POST` takes a full preferences body:

```json
{
  "date": "2026-06-15",
  "method": "minmax",
  "preferences": {
    "xc_style":          { "weight": 0.8 },
    "low_crowds":        { "weight": 0.6 },
    "beginner_friendly": { "weight": 0.0 },
    "alpine_ceiling":    { "weight": 0.5 },
    "short_drive":       { "weight": 0.3 },
    "airtime":           { "weight": 1.0 }
  }
}
```

Both return a `RecommendResponse` (`calendar_week`, `year`, `method`, applied `weights`,
and a ranked `regions[]` with `total_score`, per-feature `raw_value`/`normalized_score`,
and `data_coverage`).

### CLI (terminal review)

```powershell
# Default airtime-only profile for the ISO week of a date
python -m app.services.scoring --date 2026-06-15

# RRF with custom weights
python -m app.services.scoring --date 2026-06-15 --method rrf `
    --weight xc_style=0.8 --weight low_crowds=0.5 --weight short_drive=0.3
```

---

## Tests

```powershell
python -m pytest -q
```

Unit tests cover the scoring math (feature derivation, Min-Max polarity/degenerate
cases, RRF ties, NaN handling); end-to-end tests exercise the GET/POST endpoints and
page render with the aggregation layer patched (no database required).

---

## Region Registry

Whitelisted regions and their DHV site IDs are defined in [`regions.json`](./regions.json).

Current regions: **Lijak · Meduno · Tolmin · Greifenburg · Speikboden · Gemona**
