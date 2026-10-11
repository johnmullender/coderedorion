"""Load data/resources.json into the Tiger Data database.

Run it once after you set DATABASE_URL in .env:

    python3 seed_db.py
"""

import json
import pathlib
import sys

import db

RESOURCES_FILE = pathlib.Path(__file__).resolve().parent / "data" / "resources.json"


def main():
    # Stop early if there is no database URL in .env.
    if not db.enabled():
        print("Set DATABASE_URL in .env first")
        sys.exit(1)

    # Create the tables if they are not there yet.
    db.init_db()

    # Read the places and save them: new ones are added, old ones updated, and
    # places no longer in the file (like the old samples) are removed.
    rows = json.loads(RESOURCES_FILE.read_text(encoding="utf-8"))
    count = db.upsert_resources(rows, remove_others=True)

    # upsert_resources returns 0 when the database could not be reached.
    if count == 0 and rows:
        print("Nothing was loaded. Check DATABASE_URL and that the database is reachable.")
        sys.exit(1)

    print(f"Loaded {count} resources into Tiger Data")


if __name__ == "__main__":
    main()
