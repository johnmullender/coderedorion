"""Turn what a person typed or said into a map point (latitude and longitude).

Lookup order, first match wins:
1. A Houston zip code (table in data/zips.json).
2. A known landmark name (table LANDMARKS below).
3. OpenStreetMap Nominatim search over the internet (rate limited, cached).
"""

import json
import re
import time
from pathlib import Path

import httpx

# The kiosk's fixed spot: UH Student Center South.
STATION_LAT: float = 29.7199
STATION_LNG: float = -95.3422

ZIPS_PATH = Path(__file__).resolve().parent / "data" / "zips.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "HelpLineHouston/0.1 (hackathon)"
# Box around Houston, written as: west lng, north lat, east lng, south lat.
HOUSTON_VIEWBOX = "-95.8,30.1,-95.0,29.5"
REQUEST_TIMEOUT_SECONDS = 5.0
MIN_SECONDS_BETWEEN_REQUESTS = 1.0

# Lowercase landmark name -> (lat, lng, label).
LANDMARKS: dict[str, tuple[float, float, str]] = {
    "uh": (29.7199, -95.3422, "University of Houston"),
    "university of houston": (29.7199, -95.3422, "University of Houston"),
    "tsu": (29.7230, -95.3580, "Texas Southern University"),
    "texas southern": (29.7230, -95.3580, "Texas Southern University"),
    "texas southern university": (29.7230, -95.3580, "Texas Southern University"),
    "mlk": (29.7190, -95.3620, "MLK Blvd (Third Ward)"),
    "mlk blvd": (29.7190, -95.3620, "MLK Blvd (Third Ward)"),
    "emancipation park": (29.7294, -95.3599, "Emancipation Park"),
    "downtown": (29.7555, -95.3635, "Downtown Houston"),
    "midtown": (29.7420, -95.3700, "Midtown"),
    "third ward": (29.7220, -95.3640, "Third Ward"),
    "3rd ward": (29.7220, -95.3640, "Third Ward"),
    "med center": (29.7070, -95.3990, "Texas Medical Center"),
    "medical center": (29.7070, -95.3990, "Texas Medical Center"),
    "texas medical center": (29.7070, -95.3990, "Texas Medical Center"),
    "montrose": (29.7420, -95.3880, "Montrose"),
    "eado": (29.7490, -95.3500, "EaDo (East Downtown)"),
    "east downtown": (29.7490, -95.3500, "EaDo (East Downtown)"),
    "gulfton": (29.6900, -95.4870, "Gulfton"),
    "rice university": (29.7174, -95.4018, "Rice University"),
    "hermann park": (29.7147, -95.3899, "Hermann Park"),
    "george r. brown": (29.7531, -95.3577, "George R. Brown Convention Center"),
    "george r brown": (29.7531, -95.3577, "George R. Brown Convention Center"),
}

ZIP_PATTERN = re.compile(r"\b77\d{3}\b")

# Filled the first time it is needed, so the file is read only once.
_zip_table: dict | None = None
# Text -> result (or None), so repeated questions do not hit the network.
_geocode_cache: dict[str, dict | None] = {}
# time.monotonic() value of the last Nominatim request, or None.
_last_request_time: float | None = None


def resolve_location(text: str | None) -> dict | None:
    """Return {"lat", "lng", "label", "source"} for the text, or None if unknown."""
    if text is None or not text.strip():
        return None
    return (
        find_zip(text)
        or find_landmark(text)
        or (geocode(text) if is_worth_geocoding(text) else None)
    )


def load_zip_table() -> dict:
    """Read data/zips.json once and remember it."""
    global _zip_table
    if _zip_table is None:
        try:
            _zip_table = json.loads(ZIPS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _zip_table = {}
    return _zip_table


def find_zip(text: str) -> dict | None:
    """Look for a Houston zip code (77xxx) in the text and check our table."""
    match = ZIP_PATTERN.search(text)
    if match is None:
        return None
    entry = load_zip_table().get(match.group(0))
    if entry is None:
        return None
    return {"lat": entry["lat"], "lng": entry["lng"], "label": entry["label"], "source": "zip"}


def find_landmark(text: str) -> dict | None:
    """Look for a landmark name as a whole word, ignoring case.

    Longer names are tried first, so "east downtown" wins over "downtown".
    """
    lowered = text.lower()
    for name in sorted(LANDMARKS, key=len, reverse=True):
        if landmark_appears(name, lowered):
            lat, lng, label = LANDMARKS[name]
            return {"lat": lat, "lng": lng, "label": label, "source": "landmark"}
    return None


def landmark_appears(name: str, lowered_text: str) -> bool:
    """True if the name is in the text with no letter or digit right before or after it."""
    pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
    return re.search(pattern, lowered_text) is not None


def is_worth_geocoding(text: str) -> bool:
    """Skip the internet lookup for text with fewer than 3 letters."""
    return sum(1 for ch in text if ch.isalpha()) >= 3


def geocode(text: str) -> dict | None:
    """Ask OpenStreetMap Nominatim where the text is. Any problem gives None."""
    key = text.strip()
    if key in _geocode_cache:
        return _geocode_cache[key]
    try:
        wait_for_rate_limit()
        response = httpx.get(
            NOMINATIM_URL,
            params={
                "q": key + ", Houston, TX",
                "format": "json",
                "limit": 1,
                "viewbox": HOUSTON_VIEWBOX,
                "bounded": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = first_geocode_result(response.json(), key)
    except Exception:
        # Do not remember failures, so a later try can still succeed.
        return None
    _geocode_cache[key] = result
    return result


def first_geocode_result(items: list, query: str) -> dict | None:
    """Turn Nominatim's list of hits into our location dict (or None if empty)."""
    if not items:
        return None
    first = items[0]
    return {
        "lat": float(first["lat"]),
        "lng": float(first["lon"]),
        "label": first.get("display_name", query),
        "source": "geocode",
    }


def wait_for_rate_limit() -> None:
    """Sleep if needed so Nominatim gets at most one request per second."""
    global _last_request_time
    if _last_request_time is not None:
        wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - _last_request_time)
        if wait > 0:
            time.sleep(wait)
    _last_request_time = time.monotonic()
