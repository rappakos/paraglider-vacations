
> ⚠️ **Deprecated — superseded by [README.md](./README.md).** This is the original
> design sketch and has drifted from the implementation (the feature set, names and
> scoring all evolved; ratios moved to the scoring layer). Kept for historical context
> only; see the README for current behavior. To be removed.

# Project Plan: `paraglider-vacations`

## 1. Project Objective

A **FastAPI backend service** that ingests historical DHV-XC flight logs (2018–2025+), stores them in a wide immutable SQLite database, and exposes a single ranked recommendation endpoint. The scoring logic lives here — the consuming **MCP server** (a separate project) calls this API and exposes the results to the LLM agent and UI.

---

## 2. System Architecture

This project's responsibility is the **Data + Scoring Layer only**:

```
[DHV-XC API] ──► [Ingestion Pipeline] ──► [SQLite: raw_flights]
                                                    │
                                              [FastAPI App]
                                                    │
                                        POST /recommend
                                        { date, preferences }
                                                    │
                                         Ranked Region Matrix
                                                    │
                                         ▼ (consumed by)
                                    [External MCP Server]
                                    [External Dashboard UI]
```

**Out of scope for this project:** MCP server, LLM agent integration, grid UI rendering.

---

## 3. Proposed App Structure

```
paraglider-vacations/
├── app/
│   ├── main.py              # FastAPI app, router registration
│   ├── config.py            # DB path, regions.json path, constants
│   ├── database.py          # SQLite connection + schema init
│   ├── models.py            # Pydantic request/response models
│   ├── routers/
│   │   └── recommend.py     # POST /recommend endpoint
│   └── services/
│       ├── ingestion.py     # DHV-XC raw data fetcher + INSERT OR IGNORE
│       └── scoring.py       # Feature engineering + Min-Max ranking
├── regions.json             # Static region registry (DHV site IDs, metadata)
├── glider_vacations.db      # SQLite database (gitignored)
├── requirements.txt
├── .env.example
├── PLAN.md
└── README.md
```

---

## 4. API Endpoint Contract

### `POST /recommend`

**Request body:**
```json
{
  "date": "2025-06-15",
  "preferences": {
    "xc_style":         { "weight": 0.8 },
    "low_crowds":       { "weight": 0.6 },
    "beginner_friendly":{ "weight": 0.0 },
    "alpine_ceiling":   { "weight": 0.5 },
    "short_drive":      { "weight": 0.3 }
  }
}
```

- `date` resolves to an **ISO calendar week** (±3 day window used for aggregation).
- Each preference key maps directly to a computed feature; `weight` is `0.0–1.0`.

**Response body** — ranked matrix, one row per region:
```json
{
  "calendar_week": 25,
  "year": 2025,
  "regions": [
    {
      "region_key": "greifenburg",
      "name": "Greifenburg",
      "rank": 1,
      "total_score": 0.83,
      "features": {
        "xc_style":          { "raw_value": 0.42, "normalized_score": 0.91 },
        "low_crowds":        { "raw_value": 187,  "normalized_score": 0.74 },
        "beginner_friendly": { "raw_value": 0.31, "normalized_score": 0.55 },
        "alpine_ceiling":    { "raw_value": 0.67, "normalized_score": 0.88 },
        "short_drive":       { "raw_value": 9.0,  "normalized_score": 0.70 }
      },
      "data_coverage": {
        "flights_in_window": 312,
        "years_covered": [2019, 2020, 2021, 2022, 2023, 2024]
      }
    }
  ]
}
```

- `normalized_score` is Min-Max scaled **across regions** for that request, so color intensity and ranking are always relative.
- `data_coverage` allows the consumer to surface confidence warnings (e.g. "only 12 flights in window").

---

## 5. Spot Configuration Registry (`regions.json`)

Decouples logical vacation zones from physical launch points. Each region groups multiple DHV site IDs and carries static metadata used for the `short_drive` feature and infrastructure context.

See [`regions.json`](./regions.json) for the current registry (6 regions: Lijak, Meduno, Tolmin, Greifenburg, Speikboden, Gemona).

---

## 6. Ingestion Layer: Core Raw Columns (`raw_flights`)

The SQLite database is a **wide, immutable sink**. Schema is designed so feature parameters can change without re-downloading data. Primary key constraint + `INSERT OR IGNORE` ensures idempotency.

| SQLite Column          | Type                    | DHV-XC JSON Key            | Purpose / Signal                                          |
|------------------------|-------------------------|-----------------------------|-----------------------------------------------------------|
| `dhv_flight_id`        | `INTEGER PRIMARY KEY`   | `"IDFlight"`                | Unique flight ID; enforces idempotency.                   |
| `dhv_site_id`          | `INTEGER`               | `"FKTakeoffWaypoint"`       | Cross-references `dhv_site_ids` in `regions.json`.        |
| `pilot_id`             | `INTEGER`               | `"FKPilot"`                 | `COUNT(DISTINCT pilot_id)` → crowd density proxy.         |
| `flight_date`          | `TEXT`                  | `"FlightDate"`              | Parsed to ISO week for rolling window aggregation.        |
| `flight_duration_sec`  | `INTEGER`               | `"FlightDuration"`          | Airtime quantiles (e.g. 67th pct).                        |
| `glider_class`         | `TEXT`                  | `"GliderClassification"`    | EN A/B/C/D filter for pilot profile.                      |
| `competition_class`    | `TEXT`                  | `"CompetitionClass"`        | `"Tandem"` → commercialism proxy.                         |
| `takeoff_altitude`     | `INTEGER`               | `"TakeoffAltitude"`         | Baseline ASL altitude.                                    |
| `max_altitude`         | `INTEGER`               | `"MaxAltitude"`             | Alpine ceiling proxy.                                     |
| `best_task_distance_m` | `INTEGER`               | `"BestTaskDistance"`        | Raw XC distance.                                          |
| `best_task_type_key`   | `TEXT`                  | `"BestTaskTypeKey"`         | `"FAI_TRIANGLE"`, `"FREE_FLIGHT"`, etc.                   |

---

## 7. Feature Engineering (Scoring Layer)

All features computed over a **±3 calendar-day window** centered on the target ISO week midpoint, aggregated across all historical years matching that week.

| Feature Key         | Formula                                                                                      | High = Good For             |
|---------------------|----------------------------------------------------------------------------------------------|-----------------------------|
| `xc_style`          | `COUNT(FAI_TRIANGLE) / total_flights`                                                        | XC / triangle pilots        |
| `low_crowds`        | Inverted `COUNT(DISTINCT pilot_id)` (per unit time window)                                   | Crowd-averse pilots         |
| `beginner_friendly` | `(COUNT(EN-A) + COUNT(EN-B)) / total_flights`                                                | Beginners / family trips    |
| `alpine_ceiling`    | `% flights > learned_ceiling_threshold` (85th pct of all >30min flights across full dataset) | High-alpine / vol-biv goals |
| `short_drive`       | Inverted `car_hours` from `regions.json` (static)                                            | Weekend trips               |

Min-Max normalization applied **per-request across the filtered region set** so scores are always relative.

---

## 8. Build Order

1. **[x] Regions registry** — `regions.json` ✅
2. **[ ] App skeleton** — FastAPI boilerplate, config, DB init, Pydantic models
3. **[ ] Ingestion pipeline** — DHV-XC fetcher → `raw_flights` (to be discussed)
4. **[ ] Scoring service** — feature computation + weighted ranking
5. **[ ] `/recommend` endpoint** — wire together models, scoring, response
6. **[ ] Hardening** — error handling, data coverage warnings, logging