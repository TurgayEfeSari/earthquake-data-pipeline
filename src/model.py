"""Model step: cluster earthquake epicenters with DBSCAN to find seismic zones.

Data: the latest version of each earthquake (view earthquake_current),
type 'earthquake', M4.5+, from 1973 on. Below M4.5 and before 1973 the
catalog is incomplete in this region (Stage 7), so areas with denser station
networks would look more active than they are.

This model describes where earthquakes happened in the past.
It does not predict earthquakes.
"""

from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors

from db import get_connection
from transform import MAX_LATITUDE, MAX_LONGITUDE, MIN_LATITUDE, MIN_LONGITUDE

# images/ relative to the project root, so the script works from any folder.
IMAGES_DIR = Path(__file__).resolve().parent.parent / "images"

# A few cities, only to help the reader find places on the map (lat, lon).
CITIES = {
    "Istanbul": (41.01, 28.98),
    "Ankara": (39.93, 32.86),
    "Izmir": (38.42, 27.14),
    "Antalya": (36.90, 30.70),
    "Kahramanmaras": (37.58, 36.94),
    "Erzurum": (39.90, 41.27),
    "Van": (38.50, 43.38),
    "Tbilisi": (41.72, 44.79),
}
# Where each city name goes, in points from its triangle (x, y). Default:
# up and right. Antalya and Van are moved so they do not hide cluster ids.
CITY_LABEL_OFFSETS = {"Antalya": (-4, -11), "Van": (5, -11)}

# k-distance chart: the y axis stops here, so the few very isolated
# earthquakes do not squeeze the rest of the curve.
K_DISTANCE_CHART_MAX_KM = 200

# One color per cluster. tab20 has 20 colors; its two grays are left out,
# because gray is used for noise.
CLUSTER_COLORS = [
    color for i, color in enumerate(matplotlib.colormaps["tab20"].colors) if i not in (14, 15)
]

MIN_MAGNITUDE = 4.5
START_TIME = datetime(1973, 1, 1, tzinfo=timezone.utc)

EARTH_RADIUS_KM = 6371.0
# DBSCAN: a point is a "core point" if at least MIN_SAMPLES earthquakes
# (itself included) are within eps of it. A seismic zone should have at least
# 10 M4.5+ earthquakes since 1973; fewer are treated as isolated events.
MIN_SAMPLES = 10
# eps values to compare (km), around the bend of the k-distance curve (35-55 km).
EPS_CANDIDATES_KM = [20, 30, 40, 45, 50, 60]
# Chosen eps: the largest value before the first questionable merge of two
# different structures (at 45 km). Reasons and alternatives: decision log.
EPS_KM = 40

# Column names with units, used only in printed tables.
NUMERIC_COLUMNS = ["mag", "latitude", "longitude", "depth"]
UNIT_LABELS = {
    "mag": "mag (no unit)",
    "latitude": "latitude (deg N)",
    "longitude": "longitude (deg E)",
    "depth": "depth (km)",
}

SELECT_EVENTS = """
    SELECT earthquake_id, time, mag, latitude, longitude, depth, place
    FROM earthquake_current
    WHERE type = 'earthquake'
      AND mag >= %(min_mag)s
      AND time >= %(start_time)s
    ORDER BY time
"""


def load_events():
    """Return the earthquakes used by the model, one row per earthquake."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SELECT_EVENTS, {"min_mag": MIN_MAGNITUDE, "start_time": START_TIME})
            columns = [column.name for column in cur.description]
            rows = cur.fetchall()
    events = pd.DataFrame(rows, columns=columns)
    # psycopg returns times in the session time zone; use UTC like the rest
    # of the project.
    events["time"] = pd.to_datetime(events["time"], utc=True)
    return events


def print_summary(events):
    """Print a short check of the data before clustering."""
    print(f"Model data: earthquakes with magnitude {MIN_MAGNITUDE}+ since "
          f"{START_TIME:%Y-%m-%d} (UTC), latest version of each "
          f"(view earthquake_current)\n")
    print(f"Earthquakes: {len(events)}")
    print(f"Time (UTC):  first {events['time'].min()}  ->  last {events['time'].max()}")

    print("\n--- Numeric ranges ---")
    print("Each column is read on its own. count = earthquakes with a value,")
    print("mean = average, std = typical distance from the mean (same unit as the column),")
    print("min / max = smallest / largest value, 25% / 50% / 75% = this share of the")
    print("earthquakes has this value or less (50% = median).")
    median_mag = events["mag"].median()
    print(f"Example: mag 50% = {median_mag:.2f} -> half of the earthquakes are "
          f"M{median_mag:.1f} or smaller.")
    print(events[NUMERIC_COLUMNS].rename(columns=UNIT_LABELS).describe().round(2).to_string())

    print("\n--- Earthquakes per decade ---")
    print("decade = first year of the decade; earthquakes = count in that decade.")
    print(f"1970 covers only {START_TIME.year}-1979, and the 2020s are not over yet.")
    decades = events["time"].dt.year // 10 * 10
    counts = decades.value_counts().sort_index().rename_axis("decade").rename("earthquakes")
    print(counts.to_frame().to_string())


def to_radians(events):
    """Return [latitude, longitude] in radians: the input the haversine distance needs."""
    return np.radians(events[["latitude", "longitude"]].to_numpy())


def k_distances_km(coordinates, k):
    """For each earthquake, the distance (km) to the k-th nearest earthquake.

    The earthquake itself counts as the first neighbor (distance 0), the same
    way DBSCAN counts it in min_samples. So this is the smallest eps at which
    the earthquake would be a core point.
    """
    neighbors = NearestNeighbors(n_neighbors=k, metric="haversine")
    neighbors.fit(coordinates)
    distances, _ = neighbors.kneighbors(coordinates)
    # haversine returns an angle in radians; times the Earth radius = km
    return distances[:, -1] * EARTH_RADIUS_KM


def print_k_distances(distances_km):
    """Print percentiles of the k-distances, to choose eps for DBSCAN."""
    print(f"\n--- Distance to the {MIN_SAMPLES}th nearest earthquake, itself included ---")
    print(f"For each earthquake: the radius (km) of the smallest circle around it that "
          f"holds {MIN_SAMPLES} earthquakes.")
    print("Small = the earthquake is in a dense area; large = it is isolated.")
    median_km = pd.Series(distances_km).median()
    print(f"Read '50% of earthquakes  {median_km:.1f} km' as: for half of the earthquakes, "
          f"a {median_km:.1f} km circle is enough.")
    print("For DBSCAN: with eps = that distance, about that share of the earthquakes "
          "become core points.")
    percentiles = [0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
    values = pd.Series(distances_km).quantile(percentiles)
    values.index = [f"{p:.0%} of earthquakes" for p in percentiles]
    print(values.map(lambda km: f"{km:6.1f} km").to_string())


def run_dbscan(coordinates, eps_km):
    """Cluster the earthquakes; return one label per earthquake.

    Labels 0, 1, 2, ... are cluster numbers; -1 means noise (an isolated
    earthquake that belongs to no cluster). eps is given to scikit-learn as an
    angle in radians, because that is the unit of the haversine distance.
    """
    model = DBSCAN(
        eps=eps_km / EARTH_RADIUS_KM,
        min_samples=MIN_SAMPLES,
        metric="haversine",
        algorithm="ball_tree",  # a search tree that supports haversine
    )
    return model.fit_predict(coordinates)


def print_sensitivity(coordinates):
    """Run DBSCAN with each eps candidate and print one summary row per run."""
    print(f"\n--- DBSCAN with different eps values (min_samples = {MIN_SAMPLES}) ---")
    print("One row per run. clusters = number of clusters found;")
    print("noise = earthquakes in no cluster; largest = size of the biggest cluster;")
    print("(%) = share of all earthquakes. Too small eps: many clusters and much noise.")
    print("Too large eps: separate zones join into one big cluster.")
    rows = []
    for eps_km in EPS_CANDIDATES_KM:
        labels = run_dbscan(coordinates, eps_km)
        is_noise = labels == -1
        cluster_sizes = pd.Series(labels[~is_noise]).value_counts()
        largest = cluster_sizes.max() if len(cluster_sizes) else 0
        rows.append({
            "eps (km)": eps_km,
            "clusters": len(cluster_sizes),
            "noise (earthquakes)": int(is_noise.sum()),
            "noise (%)": round(100 * is_noise.mean(), 1),
            "largest (earthquakes)": int(largest),
            "largest (%)": round(100 * largest / len(labels), 1),
        })
    print(pd.DataFrame(rows).to_string(index=False))


def describe_clusters(events, labels, eps_km):
    """Print one row per cluster, largest first, to see what each cluster is."""
    clustered = events.assign(cluster=labels, year=events["time"].dt.year)
    clustered = clustered[clustered["cluster"] != -1]
    print(f"\n--- Clusters with eps = {eps_km} km: {clustered['cluster'].nunique()} clusters, "
          f"noise left out ---")
    print("lat / lon = from the southern / western edge to the northern / eastern edge (degrees).")
    print("years = first and last year with an earthquake in the cluster.")
    print("busiest year (%) = share of the cluster's earthquakes in its most active year:")
    print("high (over 50%) = mostly one aftershock sequence; low = activity over many years.")
    print("Cluster numbers are only names: they change when eps changes.")
    rows = []
    for cluster, group in clustered.groupby("cluster"):
        strongest = group.loc[group["mag"].idxmax()]
        busiest_share = group["year"].value_counts().iloc[0] / len(group)
        rows.append({
            "cluster": cluster,
            "earthquakes": len(group),
            "lat (deg N)": f"{group['latitude'].min():.1f}-{group['latitude'].max():.1f}",
            "lon (deg E)": f"{group['longitude'].min():.1f}-{group['longitude'].max():.1f}",
            "years": f"{group['year'].min()}-{group['year'].max()}",
            "busiest year (%)": round(100 * busiest_share),
            "max mag": strongest["mag"],
            # Place kept short so a row fits in a wide terminal window.
            "strongest earthquake": f"{strongest['year']}, {strongest['place'][:30]}",
        })
    table = pd.DataFrame(rows).sort_values("earthquakes", ascending=False)
    print(table.to_string(index=False))


def plot_clusters(events, labels, path):
    """Draw the epicenters colored by cluster and save the map as a PNG file."""
    fig, ax = plt.subplots(figsize=(12, 6.5))

    noise = labels == -1
    ax.scatter(events.loc[noise, "longitude"], events.loc[noise, "latitude"],
               s=5, color="lightgray")

    # Largest cluster first, so it gets the first color.
    sizes = pd.Series(labels[~noise]).value_counts()
    for i, cluster in enumerate(sizes.index):
        members = events[labels == cluster]
        color = CLUSTER_COLORS[i % len(CLUSTER_COLORS)]  # colors repeat after 18 clusters
        ax.scatter(members["longitude"], members["latitude"], s=8, color=color)
        # The cluster id from the printed table, at the middle of the cluster.
        ax.text(members["longitude"].median(), members["latitude"].median(), str(cluster),
                fontsize=9, fontweight="bold", ha="center", va="center",
                bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "alpha": 0.8})

    for name, (lat, lon) in CITIES.items():
        ax.plot(lon, lat, marker="^", color="black", markersize=5)
        dx, dy = CITY_LABEL_OFFSETS.get(name, (4, 4))
        ax.annotate(name, (lon, lat), xytext=(dx, dy), textcoords="offset points",
                    fontsize=8, ha="right" if dx < 0 else "left")

    # The map shows the same box as the API request.
    ax.set_xlim(MIN_LONGITUDE, MAX_LONGITUDE)
    ax.set_ylim(MIN_LATITUDE, MAX_LATITUDE)
    # One degree of longitude is shorter than one degree of latitude here
    # (cos 39 deg = 0.78), so stretch the y axis to keep shapes right.
    middle_latitude = (MIN_LATITUDE + MAX_LATITUDE) / 2
    ax.set_aspect(1 / np.cos(np.radians(middle_latitude)))
    ax.set_xlabel("Longitude (deg E)")
    ax.set_ylabel("Latitude (deg N)")
    ax.set_title(
        f"DBSCAN clusters of M{MIN_MAGNITUDE}+ earthquakes since {START_TIME.year} "
        f"(eps = {EPS_KM} km, min_samples = {MIN_SAMPLES})\n"
        f"Colors = clusters, numbers = cluster ids, gray = noise, triangles = cities"
    )
    ax.grid(alpha=0.3)

    path.parent.mkdir(exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)  # free the memory; the figure is only saved, not shown
    print(f"\nMap saved to {path}")


def plot_k_distances(distances_km, path):
    """Draw the sorted k-distances with the chosen eps and save the chart as PNG.

    x = share of the earthquakes (sorted from dense to isolated),
    y = distance to the MIN_SAMPLES-th nearest earthquake (km).
    The curve rises slowly inside dense areas and steeply for isolated
    earthquakes; a good eps is near the bend.
    """
    sorted_km = np.sort(distances_km)
    share = np.arange(1, len(sorted_km) + 1) / len(sorted_km) * 100
    core_share = (sorted_km <= EPS_KM).mean() * 100
    hidden = (sorted_km > K_DISTANCE_CHART_MAX_KM).sum()

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(share, sorted_km, color="tab:blue")
    ax.axhline(EPS_KM, color="tab:red", linestyle="--",
               label=f"chosen eps = {EPS_KM} km")
    ax.axvline(core_share, color="gray", linestyle=":",
               label=f"{core_share:.0f}% of earthquakes are core points at this eps")
    ax.set_ylim(0, K_DISTANCE_CHART_MAX_KM)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Earthquakes, sorted from dense area to isolated (%)")
    ax.set_ylabel(f"Distance to the {MIN_SAMPLES}th nearest earthquake (km)")
    ax.set_title(
        f"k-distance curve (k = min_samples = {MIN_SAMPLES}), "
        f"M{MIN_MAGNITUDE}+ earthquakes since {START_TIME.year}\n"
        f"y axis cut at {K_DISTANCE_CHART_MAX_KM} km: {hidden} very isolated "
        f"earthquakes are above it"
    )
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)

    path.parent.mkdir(exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"k-distance chart saved to {path}")


if __name__ == "__main__":
    events = load_events()
    print_summary(events)
    coordinates = to_radians(events)
    distances_km = k_distances_km(coordinates, MIN_SAMPLES)
    print_k_distances(distances_km)
    print_sensitivity(coordinates)
    labels = run_dbscan(coordinates, EPS_KM)
    describe_clusters(events, labels, EPS_KM)
    plot_clusters(events, labels, IMAGES_DIR / "clusters_map.png")
    plot_k_distances(distances_km, IMAGES_DIR / "k_distance.png")
