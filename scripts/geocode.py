"""Fill in lat/lng for the places in data/resources.json from their street address.

Order for each place: the US Census geocoder first, then OpenStreetMap Nominatim
(one request per second). A place whose coords_source is "census" or "manual" and
that already has lat/lng is left alone.

Usage (needs internet):
    python3 scripts/geocode.py [path] [--dry-run]

Exit code is 1 if any place could not be geocoded (the successes are still written).
"""

import argparse
import json
import os
import stat
import tempfile
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "data" / "resources.json"

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CENSUS_BENCHMARK = "Public_AR_Current"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "HelpLineHouston/0.1 (hackathon)"
TIMEOUT_SECONDS = 10.0
NOMINATIM_MIN_INTERVAL = 1.0  # seconds between Nominatim requests
KEEP_SOURCES = ("census", "manual")
DECIMALS = 5

_last_nominatim_call = 0.0  # time.monotonic() of the last Nominatim request


class GeocodeError(Exception):
    """An address could not be geocoded by either service."""


def needs_geocode(place):
    """True when the place has no coordinates or its coordinates are not census/manual."""
    if place.get("lat") is None or place.get("lng") is None:
        return True
    return place.get("coords_source") not in KEEP_SOURCES


def census_lookup(client, address):
    """Ask the Census geocoder. Returns (lat, lng) or None when there is no match."""
    response = client.get(
        CENSUS_URL,
        params={"address": address, "benchmark": CENSUS_BENCHMARK, "format": "json"},
    )
    response.raise_for_status()
    matches = response.json().get("result", {}).get("addressMatches") or []
    if not matches:
        return None
    coords = matches[0]["coordinates"]  # the Census API gives x = lng, y = lat
    return float(coords["y"]), float(coords["x"])


def nominatim_lookup(client, address):
    """Ask Nominatim, waiting so that requests are at least one second apart.

    Returns (lat, lng) or None when there is no match.
    """
    global _last_nominatim_call
    wait = NOMINATIM_MIN_INTERVAL - (time.monotonic() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim_call = time.monotonic()
    response = client.get(
        NOMINATIM_URL,
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def _describe(exc):
    """A short reason for a log line."""
    return str(exc) or type(exc).__name__


def geocode_address(client, address):
    """Return (lat, lng, source) with 5-decimal coordinates, or raise GeocodeError."""
    text = str(address or "").strip()
    if not text:
        raise GeocodeError("no address")
    problems = []
    for source, lookup in (("census", census_lookup), ("nominatim", nominatim_lookup)):
        try:
            found = lookup(client, text)
        except Exception as exc:  # network, HTTP status or bad JSON: try the next service
            problems.append(f"{source}: {_describe(exc)}")
            continue
        if found is not None:
            lat, lng = found
            return round(lat, DECIMALS), round(lng, DECIMALS), source
        problems.append(f"{source}: no match")
    raise GeocodeError("; ".join(problems))


def process_places(places, client, dry_run=False, log=print):
    """Geocode every place that needs it, in place. Returns a stats dict.

    stats keys: "geocoded" (count), "kept" (count), "failed" (list of (id, reason)).
    Failures never stop the run.
    """
    stats = {"geocoded": 0, "kept": 0, "failed": []}
    for index, place in enumerate(places, start=1):
        if not isinstance(place, dict):
            stats["failed"].append((f"#{index}", "record is not an object"))
            log(f"FAIL  #{index}: record is not an object")
            continue
        pid = place.get("id") or f"#{index}"
        if not needs_geocode(place):
            stats["kept"] += 1
            log(f"KEEP  {pid}: {place.get('coords_source')} coordinates kept")
            continue
        try:
            lat, lng, source = geocode_address(client, place.get("address"))
        except GeocodeError as exc:
            stats["failed"].append((pid, str(exc)))
            log(f"FAIL  {pid}: {exc}")
            continue
        except Exception as exc:  # never let one bad place stop the run
            stats["failed"].append((pid, _describe(exc)))
            log(f"FAIL  {pid}: unexpected error: {_describe(exc)}")
            continue
        if not dry_run:
            place["lat"] = lat
            place["lng"] = lng
            place["coords_source"] = source
        stats["geocoded"] += 1
        prefix = "WOULD " if dry_run else "OK    "
        log(f"{prefix}{source:<9}{pid}: {lat:.5f}, {lng:.5f}")
    return stats


def write_json_atomic(path, data):
    """Write JSON to a temp file in the same folder, then move it over the old file."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if path.exists():  # mkstemp makes 0600 files; keep the original's permissions
            os.chmod(tmp_name, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fill lat/lng for places from their address.")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_PATH), help="resources JSON file")
    parser.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    args = parser.parse_args(argv)

    path = Path(args.path)
    try:
        places = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read {path}: {exc}")
        return 1
    if not isinstance(places, list):
        print(f"{path} must hold a JSON list of places")
        return 1

    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        stats = process_places(places, client, dry_run=args.dry_run)

    if stats["geocoded"] and not args.dry_run:
        try:
            write_json_atomic(path, places)
        except OSError as exc:
            print(f"Cannot write {path}: {exc}")
            return 1

    print()
    print(
        f"Summary: {len(places)} place(s), {stats['geocoded']} geocoded"
        f"{' (dry run, nothing written)' if args.dry_run else ''}, "
        f"{stats['kept']} kept, {len(stats['failed'])} failed"
    )
    for pid, reason in stats["failed"]:
        print(f"  failed: {pid}: {reason}")
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
