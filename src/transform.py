"""Transform step: load the raw USGS files and clean the data with pandas.

load.py calls load_raw_events() and clean_events() and gets the cleaned
DataFrame in memory; nothing is written to disk here.
print_profile() is a data quality report used to choose the cleaning rules.
"""

import json
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Same bounding box as the API request in fetch.py.
MIN_LATITUDE, MAX_LATITUDE = 35, 43
MIN_LONGITUDE, MAX_LONGITUDE = 24.5, 45


def load_raw_events():
    """Read every raw GeoJSON file and return one DataFrame row per event."""
    rows = []
    for path in sorted(RAW_DIR.glob("usgs_*.geojson")):
        data = json.loads(path.read_bytes())
        for feature in data["features"]:
            properties = feature["properties"]
            # coordinates = [longitude, latitude, depth]. If geometry is missing
            # or shorter than 3 values, fill the gaps with None instead of crashing.
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            longitude, latitude, depth = (list(coordinates) + [None, None, None])[:3]
            rows.append({
                "id": feature.get("id"),
                "ids": properties.get("ids"),
                "time": properties.get("time"),
                "updated": properties.get("updated"),
                "mag": properties.get("mag"),
                "mag_type": properties.get("magType"),
                "place": properties.get("place"),
                "type": properties.get("type"),
                "status": properties.get("status"),
                "latitude": latitude,
                "longitude": longitude,
                "depth": depth,
                "source_file": path.name,  # kept to trace a row back to its raw file
            })
    return pd.DataFrame(rows)


def clean_events(df):
    """Return the cleaned events, one row per version (id, updated).

    Cleaning rules (chosen from the data profile):
    - drop source_file: only used for tracing, not stored in the database
    - drop repeated versions (same id and updated); none in the first full
      fetch, but the daily update may fetch the same version twice
    - time, updated: milliseconds since 1970-01-01 UTC -> UTC timestamps
    - ids: ",a,b," -> ["a", "b"]
    NULL mag_type is kept on purpose: the database stores what USGS sent.
    """
    df = df.drop(columns="source_file")
    df = df.drop_duplicates(subset=["id", "updated"])
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["updated"] = pd.to_datetime(df["updated"], unit="ms", utc=True)
    df["ids"] = df["ids"].str.strip(",").str.split(",")
    return df.reset_index(drop=True)


def print_profile(df):
    """Print the data quality checks used to choose the cleaning rules."""
    text_columns = ["id", "ids", "mag_type", "place", "type", "status"]

    print(f"Rows: {len(df)}  (files: {df['source_file'].nunique()})")

    print("\n--- NULL values per column ---")
    print(df.isna().sum().to_string())

    print("\n--- Empty strings per text column ---")
    for column in text_columns:
        print(f"{column}: {(df[column].astype('string').str.strip() == '').sum()}")

    print("\n--- Duplicates ---")
    print(f"Same id:            {df['id'].duplicated().sum()}")
    print(f"Same id + updated:  {df.duplicated(subset=['id', 'updated']).sum()}")
    print(f"Fully identical:    {df.drop(columns='source_file').duplicated().sum()}")

    print("\n--- Value counts ---")
    for column in ["type", "status", "mag_type"]:
        print(f"\n{column}:")
        print(df[column].value_counts(dropna=False).to_string())

    print("\n--- Numeric ranges ---")
    print(df[["mag", "latitude", "longitude", "depth"]].describe().to_string())

    print("\n--- Time range (UTC) ---")
    times = pd.to_datetime(df["time"], unit="ms", utc=True)
    updated = pd.to_datetime(df["updated"], unit="ms", utc=True)
    print(f"time:    {times.min()}  ->  {times.max()}")
    print(f"updated: {updated.min()}  ->  {updated.max()}")

    print("\n--- Region check (outside the bounding box) ---")
    outside = ~(
        df["latitude"].between(MIN_LATITUDE, MAX_LATITUDE)
        & df["longitude"].between(MIN_LONGITUDE, MAX_LONGITUDE)
    )
    print(f"Outside: {outside.sum()}")

    print("\n--- Sample of the ids field ---")
    print(df["ids"].head(5).to_string())
    id_lists = df["ids"].str.strip(",").str.split(",")
    ids_count = id_lists.str.len()
    print(f"\nids per event: min {ids_count.min()}, max {ids_count.max()}")

    print("\n--- ids checks needed by the schema ---")
    # The composite foreign key needs the preferred id inside the event's own ids.
    missing = sum(event_id not in ids for event_id, ids in zip(df["id"], id_lists))
    print(f"Preferred id missing from its own ids list: {missing}")
    # usgs_id is the primary key of earthquake_usgs_id, so it must not repeat.
    pairs = id_lists.explode().reset_index()
    pairs.columns = ["row", "usgs_id"]
    print(f"Same id twice in one event's list:    {pairs.duplicated().sum()}")
    pairs = pairs.drop_duplicates()
    print(f"USGS ids shared by different events:  {pairs['usgs_id'].duplicated().sum()}")

    print("\n--- Rows with NULL mag_type ---")
    print(df[df["mag_type"].isna()].drop(columns="ids").to_string())

    print("\n--- Common fixed depths (used when the real depth is unknown) ---")
    for fixed_depth in [0, 10, 33]:
        print(f"depth == {fixed_depth} km: {(df['depth'] == fixed_depth).sum()}")


if __name__ == "__main__":
    raw = load_raw_events()
    print_profile(raw)

    events = clean_events(raw)
    print("\n=== After cleaning ===")
    print(f"Rows: {len(events)}")
    print("\nColumn types:")
    print(events.dtypes.to_string())
    print("\nFirst 3 rows:")
    print(events.head(3).to_string())
