import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

CREATE_RAW_FLIGHTS = """
CREATE TABLE IF NOT EXISTS raw_flights (
    dhv_flight_id        INTEGER PRIMARY KEY,   -- IDFlight
    dhv_site_id          INTEGER NOT NULL,       -- FKTakeoffWaypoint
    takeoff_site_name    TEXT,                   -- TakeoffWaypointName
    takeoff_country      TEXT,                   -- TakeoffCountry
    pilot_id             INTEGER,                -- FKPilot
    flight_date          TEXT NOT NULL,          -- FlightDate  (YYYY-MM-DD)
    flight_duration_sec  INTEGER,                -- FlightDuration
    glider_model         TEXT,                   -- Glider
    glider_brand         TEXT,                   -- GliderBrand
    glider_class         TEXT,                   -- GliderClassification  (EN A / EN B / EN C)
    competition_class    TEXT,                   -- CompetitionClass  (Tandem / Ohne Wertung / ...)
    takeoff_altitude     INTEGER,                -- TakeoffAltitude (m ASL)
    max_altitude         INTEGER,                -- MaxAltitude (m ASL)
    max_climb            REAL,                   -- MaxClimb (m/s)
    best_task_distance_m INTEGER,                -- BestTaskDistance
    best_task_type_key   TEXT,                   -- BestTaskTypeKey  (FAI_TRIANGLE / FREE_FLIGHT / ...)
    best_task_points     REAL                    -- BestTaskPoints
);
"""

CREATE_INDICES = [
    # Primary query pattern: site + date window
    "CREATE INDEX IF NOT EXISTS idx_rf_site_date ON raw_flights (dhv_site_id, flight_date);",
    # For ISO-week aggregation across all sites of a region
    "CREATE INDEX IF NOT EXISTS idx_rf_date ON raw_flights (flight_date);",
]

# --------------------------------------------------------------------------- #
# Connection helper
# --------------------------------------------------------------------------- #

@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(CREATE_RAW_FLIGHTS)
        for idx in CREATE_INDICES:
            conn.execute(idx)
