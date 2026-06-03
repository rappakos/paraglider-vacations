
# Project Handover: `paraglider-vacations`

## 1. Project Objective

A recommendation engine utilizing historical DHV-XC flight logs (2018–2025+) to evaluate and rank whitelisted paragliding regions across calendar weeks. The system matches micro-meteorological trends and site characteristics against specific user preferences (crowds, XC style, flight intensity, drive time, etc.).

## 2. System Architecture

The tool uses a **Decoupled Three-Layer Pattern** to insulate the frontend and LLM from raw data processing changes:

* **Data Layer (SQLite & JSON):** A wide, immutable database storing raw flight data alongside a static JSON registry mapping logical vacation spots (e.g., "Bassano") to their official DHV takeoff IDs and static metadata (drive time from Hannover, infrastructure notes).
* **Logic Layer (MCP Server):** A standalone Model Context Protocol server that applies user weights, dynamically normalizes scores, and outputs a strict API contract.
* **Interaction Layer (Grid UI + LLM Agent):** The UI strictly renders the matrix provided by the MCP server. The LLM Agent reads the same matrix to provide qualitative commentary, highlight trade-offs, and suggest fallback options.

---

## 3. Spot Configuration Registry (`regions.json`)

This structure decouples logical vacation zones from physical launch points by grouping multiple official DHV IDs under a unified region key. It also maps static distance constraints from Hannover and custom infrastructure variables.

```json
{
  "bassano": {
    "name": "Bassano del Grappa",
    "aliases": ["Semonzo", "Monte Grappa"],
    "dhv_site_ids":, 
    "coordinates": { "lat": 45.81, "lon": 11.76 },
    "travel_from_hannover": {
      "car_hours": 9.5,
      "distance_km": 980
    },
    "infrastructure_notes": [
      {
        "year": 2025,
        "note": "Col Rodella lift renovation altered shuttle routines. Check operations."
      }
    ],
    "tags": ["thermal", "winter_flyable"]
  }
}

```

---

## 4. Ingestion Layer: Core Raw Columns (`raw_flights`)

To ensure you never have to re-download the dataset if feature parameters change, the SQLite database acts as a wide, immutable sink. The table enforces a primary key constraint on `dhv_flight_id` using an `INSERT OR IGNORE` strategy for absolute idempotency.

The pipeline maps the following critical keys directly from the DHV-XC JSON payload:

| Target SQLite Column | Type | Source Payload JSON Key | Purpose / Signal |
| --- | --- | --- | --- |
| `dhv_flight_id` | `INTEGER PRIMARY KEY` | `"IDFlight"` | Unique flight identifier; enforces idempotency. |
| `dhv_site_id` | `INTEGER` | `"FKTakeoffWaypoint"` | Cross-references to `dhv_site_ids` in `regions.json`. |
| `pilot_id` | `INTEGER` | `"FKPilot"` | Used via `COUNT(DISTINCT pilot_id)` to measure unique crowds. |
| `flight_date` | `TEXT` | `"FlightDate"` | Parsed into ISO weeks to calculate rolling calendar aggregates. |
| `flight_duration_seconds` | `INTEGER` | `"FlightDuration"` | Used to extract quantiles (e.g., 67th percentile) of airtime. |
| `glider_class` | `TEXT` | `"GliderClassification"` | Filters wing certification (EN A, EN B, EN C, EN D) for pilot profiles. |
| `competition_class` | `TEXT` | `"CompetitionClass"` | Identifies `"Tandem"` flights directly to isolate commercial operations. |
| `takeoff_altitude` | `INTEGER` | `"TakeoffAltitude"` | Baseline altitude in meters ASL. |
| `max_altitude` | `INTEGER` | `"MaxAltitude"` | Maximum absolute height reached; crucial ceiling proxy. |
| `best_task_distance_m` | `INTEGER` | `"BestTaskDistance"` | Raw XC distance covered by the flight task. |
| `best_task_type_key` | `TEXT` | `"BestTaskTypeKey"` | Task layout profile (`"FAI_TRIANGLE"`, `"FREE_FLIGHT"`, etc.). |

---

## 5. Advanced Feature Engineering Pipeline

Instead of relying on hardcoded assumptions, the logic layer infers site conditions directly from pilot behavior across a rolling window ($\pm3\text{ days}$) centered on the targeted calendar week:

* **XC Profile (Site Style):** Computed as $\frac{\text{Count}(\text{FAI\_TRIANGLE})}{\text{Total Flights}}$. High FAI ratios indicate reliable, predictable thermal cycles that allow pilots to close loops and fly against the wind back to their cars.
* **Commercialism Traffic:** Computed as $\frac{\text{Count}(\text{Tandem})}{\text{Total Flights}}$. Serves as a proxy for both high launch-queue crowd density and highly developed local shuttle/lift infrastructure.
* **Beginner Friendliness ("Anfängerfreundlich"):** Computed as $\frac{\text{Count}(\text{EN-A}) + \text{Count}(\text{EN-B})}{\text{Total Flights}}$. Low ratios during specific weeks alert pilots that a site has shifted into an intense, high-performance alpine arena.
* **Dynamic Alpine Ceiling:** Instead of a hardcoded threshold value (e.g., 2,500m), the pipeline calculates a global or seasonal baseline (e.g., the 85th/90th percentile of all flights lasting $>30\text{ minutes}$). The metric stored is `% of flights exceeding this learned threshold`, which proxies cloudbase potential and alpine valley-crossing viability.

---

## 6. API & UI Contract Layout

The database views or code-level serialization components funnel data into a strict contract layout. The payload returns a clean row/column matrix where each cell provides both a `raw_value` (for human readability in the grid) and a `normalized_score` (from $0.0$ to $1.0$ derived via Min-Max scaling for color intensities and sorting). This allows your ranking math to evolve seamlessly without ever breaking frontend styling or component architectures.