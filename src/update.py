"""Daily incremental update: fetch and load only the events USGS changed
since the newest version stored in the database.

This is the script the scheduler runs. The full fetch and load
(fetch.py, then load.py) stay available for a complete resync.
"""

import json
import sys
from datetime import timedelta

from db import get_connection
from fetch import fetch_updates, save_update
from load import load_events, report_result
from transform import clean_events, load_raw_events

# Ask for a little more than needed, so no change at the border is missed.
# Versions we already have are skipped by the idempotent load.
SAFETY_MARGIN = timedelta(days=1)


def get_newest_stored_update():
    """Return max(updated) of all stored versions, or None if the table is empty."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(updated) FROM earthquake_version")
        return cur.fetchone()[0]


def main():
    # Short connection: no transaction stays open while we wait for the API.
    newest = get_newest_stored_update()
    if newest is None:
        print("The database is empty: run fetch.py and load.py first.")
        sys.exit(1)

    since = newest - SAFETY_MARGIN
    print(f"Newest stored version: {newest}")
    print(f"Fetching events changed after {since}...")

    # An exception here (API down after retries, over the 20000 limit) stops
    # the script with exit code 1; nothing has been written to the database.
    content = fetch_updates(since)
    event_count = len(json.loads(content)["features"])
    path = save_update(content)
    # These are all events changed in the whole window (margin included),
    # not only since yesterday; the load below decides what is really new.
    print(f"USGS returned {event_count} events changed after {since}")
    print(f"Raw API response saved to {path}")

    if event_count == 0:
        print("\nNothing to load.")
        return

    events = clean_events(load_raw_events([path]))
    with get_connection() as conn:  # one transaction, as in the full load
        result = load_events(conn, events)

    report_result(*result)


if __name__ == "__main__":
    main()
