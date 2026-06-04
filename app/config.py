import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

DB_PATH = BASE_DIR / os.getenv("DB_PATH", "glider_vacations.db")
REGIONS_PATH = BASE_DIR / "regions.json"

# Web app
APP_PREFIX = os.getenv("VACATIONS_APP_PREFIX", "")  # e.g. "/para-vacations" behind the dashboard proxy
PORT = int(os.getenv("PORT", "3980"))
REFERENCE_YEAR = 2026

# DHV-XC API
DHV_BASE_URL = "https://de.dhv-xc.de/api/fli/flights"
PAGE_SIZE = 500
START_YEAR = 2018

# Glider filter: fkcat=1 → Gleitschirm (paraglider)
# fkcls 1=EN A, 2=EN B, 3=EN C — tandem flights appear here as EN B/C with CompetitionClass=Tandem
GLIDER_CATEGORY = 1
GLIDER_CLASSES = [1, 2, 3]

# Confirmed correct parameter name for site-ID filter (from paraglider-sites/dhv_loader.py).
SITE_FILTER_PARAM = "fkto[]"


def load_regions() -> dict:
    with open(REGIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
