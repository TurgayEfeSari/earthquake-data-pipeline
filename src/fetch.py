"""Extract step: download earthquake data from the USGS Earthquake Catalog API.

Fetches one year per request, from FIRST_YEAR to the current year, and saves
each API response unchanged (raw) as a GeoJSON file in data/raw/.
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# Filters used in every request: Turkey and surrounding areas, M2.5 and above.
BASE_PARAMS = {
    "format": "geojson",
    "minlatitude": 35,
    "maxlatitude": 43,
    "minlongitude": 24.5,
    "maxlongitude": 45,
    "minmagnitude": 2.5,
}

FIRST_YEAR = 1900

# data/raw/ relative to the project root, so the script works from any folder.
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

TIMEOUT_SECONDS = 60      # wait this long for the server before giving up
MAX_ATTEMPTS = 3          # tries per year for temporary problems
RETRY_WAIT_SECONDS = 10   # wait between tries
PAUSE_SECONDS = 1         # pause between years, to be polite to a free public API

# Status codes that mean "the server has a temporary problem, try again later".
# Other 4xx codes mean our request is wrong, so retrying would not help.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def fetch_year(year):
    """Request all events of one year and return the raw response body (bytes).

    Retries timeouts, connection errors and temporary server errors.
    Raises an exception if the year still fails after MAX_ATTEMPTS tries.
    """
    params = {
        **BASE_PARAMS,
        "starttime": f"{year}-01-01",
        "endtime": f"{year + 1}-01-01",
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as error:
            problem = type(error).__name__
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                if not response.ok:
                    # Other 4xx / 5xx: stop, do not retry. The body says why, e.g.
                    # USGS answers 400 when more than 20000 events match the query.
                    raise RuntimeError(
                        f"HTTP {response.status_code}: {response.text.strip()[:300]}"
                    )
                return response.content
            problem = f"HTTP {response.status_code}"

        if attempt < MAX_ATTEMPTS:
            print(f"  {year}: {problem}, retrying in {RETRY_WAIT_SECONDS} s "
                  f"(attempt {attempt} of {MAX_ATTEMPTS})")
            time.sleep(RETRY_WAIT_SECONDS)

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts ({problem})")


def save_raw(year, content):
    """Write the raw response to data/raw/usgs_<year>.geojson and return the path."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"usgs_{year}.geojson"
    path.write_bytes(content)
    return path


def main():
    last_year = date.today().year
    failed_years = []
    total_events = 0

    for year in range(FIRST_YEAR, last_year + 1):
        try:
            content = fetch_year(year)
            # Check that the body is GeoJSON before saving it.
            event_count = len(json.loads(content)["features"])
        except (requests.RequestException, RuntimeError, ValueError, KeyError) as error:
            print(f"{year}: FAILED - {error}")
            failed_years.append(year)
            continue

        save_raw(year, content)
        total_events += event_count
        print(f"{year}: {event_count} events")
        time.sleep(PAUSE_SECONDS)

    print(f"\nDone: {total_events} events saved to {RAW_DIR}")
    if failed_years:
        print(f"Failed years: {failed_years}. Run the script again to retry.")
        sys.exit(1)  # non-zero exit code tells a scheduler that the run failed


if __name__ == "__main__":
    main()
