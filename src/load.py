"""Load step: write the cleaned events into PostgreSQL.

The whole load runs in one transaction: the database ends up either in its
old state or fully updated, never half loaded. Running it twice adds nothing.
"""

import sys

import pandas as pd

from db import get_connection
from transform import clean_events, load_raw_events

# Columns of earthquake_version that come straight from the cleaned DataFrame.
VERSION_COLUMNS = [
    "updated", "time", "mag", "mag_type", "status", "place", "type",
    "latitude", "longitude", "depth",
]

FIND_EARTHQUAKES = """
    SELECT DISTINCT earthquake_id
    FROM earthquake_usgs_id
    WHERE usgs_id = ANY(%s)
"""

INSERT_EARTHQUAKE = """
    INSERT INTO earthquake (preferred_id)
    VALUES (%s)
    RETURNING earthquake_id
"""

# Ids that already exist belong to this same earthquake (checked before),
# so skipping them on conflict cannot hide an id of another earthquake.
INSERT_USGS_ID = """
    INSERT INTO earthquake_usgs_id (usgs_id, earthquake_id)
    VALUES (%s, %s)
    ON CONFLICT (usgs_id) DO NOTHING
"""

INSERT_VERSION = """
    INSERT INTO earthquake_version (
        earthquake_id, updated, time, mag, mag_type, status, place, type,
        latitude, longitude, depth
    )
    VALUES (
        %(earthquake_id)s, %(updated)s, %(time)s, %(mag)s, %(mag_type)s,
        %(status)s, %(place)s, %(type)s, %(latitude)s, %(longitude)s, %(depth)s
    )
    ON CONFLICT (earthquake_id, updated) DO NOTHING
"""

# Change the preferred id only if this version is the newest one stored,
# so loading an old raw file cannot overwrite a newer preferred id.
UPDATE_PREFERRED_ID = """
    UPDATE earthquake
    SET preferred_id = %(preferred_id)s
    WHERE earthquake_id = %(earthquake_id)s
      AND preferred_id <> %(preferred_id)s
      AND %(updated)s = (
          SELECT max(updated) FROM earthquake_version
          WHERE earthquake_id = %(earthquake_id)s
      )
"""


def to_db_value(value):
    """pandas marks missing values as NaN / NA; psycopg needs None for NULL."""
    return None if pd.isna(value) else value


def load_events(conn, events):
    """Insert or update every event version. Returns counts and skipped events."""
    new_earthquakes = 0
    new_versions = 0
    skipped = []

    # Oldest version first, so the newest one is processed last.
    rows = events.sort_values("updated").to_dict("records")

    with conn.cursor() as cur:
        for number, row in enumerate(rows, start=1):
            # 1. Which stored earthquakes own any of the incoming ids? (read only)
            cur.execute(FIND_EARTHQUAKES, (row["ids"],))
            matches = [earthquake_id for (earthquake_id,) in cur.fetchall()]

            if len(matches) > 1:
                # USGS merged events we stored separately: skip before writing.
                skipped.append((row["id"], matches))
                continue

            # 2. New earthquake, or the one we already know.
            if matches:
                earthquake_id = matches[0]
            else:
                cur.execute(INSERT_EARTHQUAKE, (row["id"],))
                earthquake_id = cur.fetchone()[0]
                new_earthquakes += 1

            # 3. Ids. The preferred-id foreign key is checked at COMMIT,
            #    so the earthquake row could be inserted before its ids.
            cur.executemany(
                INSERT_USGS_ID, [(usgs_id, earthquake_id) for usgs_id in row["ids"]]
            )

            # 4. The version itself; rowcount is 0 if it was already stored.
            params = {column: to_db_value(row[column]) for column in VERSION_COLUMNS}
            params["earthquake_id"] = earthquake_id
            cur.execute(INSERT_VERSION, params)
            new_versions += cur.rowcount

            # 5. Keep the preferred id up to date.
            cur.execute(UPDATE_PREFERRED_ID, {
                "preferred_id": row["id"],
                "earthquake_id": earthquake_id,
                "updated": row["updated"],
            })

            if number % 5000 == 0:
                print(f"  {number} of {len(rows)} processed")

    return new_earthquakes, new_versions, skipped


def report_result(new_earthquakes, new_versions, skipped):
    """Print the load result; exit with code 1 if any event was skipped."""
    print(f"\nDone: {new_earthquakes} new earthquakes, {new_versions} new versions "
          f"added to the database")
    if skipped:
        print(f"Skipped {len(skipped)} events whose ids match more than one "
              f"stored earthquake (USGS merged them):")
        for usgs_id, earthquake_ids in skipped:
            print(f"  {usgs_id} -> earthquake_id {earthquake_ids}")
        sys.exit(1)  # non-zero exit code tells a scheduler that the run needs attention


def main():
    """Full load: every yearly raw file in data/raw/."""
    print("Full load: every yearly raw file in data/raw/ into the database "
          "(versions already stored are skipped)")
    events = clean_events(load_raw_events())
    print(f"Loading {len(events)} event versions...")

    # Leaving the "with" block commits the transaction, or rolls it back if an
    # exception was raised. The deferred foreign key is checked at that commit.
    with get_connection() as conn:
        result = load_events(conn, events)

    report_result(*result)


if __name__ == "__main__":
    main()
