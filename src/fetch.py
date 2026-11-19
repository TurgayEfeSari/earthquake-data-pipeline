"""Extract step: download earthquake data from the USGS Earthquake Catalog API.

Full fetch (run this file): one request per year, from FIRST_YEAR to the
current year, each response saved unchanged (raw) in data/raw/.
Incremental fetch (used by update.py): fetch_updates() asks only for events
changed since a given time; save_update() stores that response in
data/raw/updates/.
"""

import json
import sys
import time
from datetime import date, datetime, timezone
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
UPDATES_DIR = RAW_DIR / "updates"

TIMEOUT_SECONDS = 60      # wait this long for the server before giving up
MAX_ATTEMPTS = 3          # tries per request for temporary problems
RETRY_WAIT_SECONDS = 10   # wait between tries
PAUSE_SECONDS = 1         # pause between years, to be polite to a free public API

# Status codes that mean "the server has a temporary problem, try again later".
# Other 4xx codes mean our request is wrong, so retrying would not help.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def describe_filters():
    """Return the request filters as text, for the first line of the output."""
    return (f"magnitude {BASE_PARAMS['minmagnitude']}+, "
            f"latitude {BASE_PARAMS['minlatitude']} to {BASE_PARAMS['maxlatitude']}, "
            f"longitude {BASE_PARAMS['minlongitude']} to {BASE_PARAMS['maxlongitude']}")


def request_events(params, label):
    """Send one query and return the raw response body (bytes).

    Retries timeouts, connection errors and temporary server errors.
    Raises an exception if the request still fails after MAX_ATTEMPTS tries.
    label is only used in the printed messages (e.g. "1999" or "updates").
    """
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
            print(f"  {label}: {problem}, retrying in {RETRY_WAIT_SECONDS} s "
                  f"(attempt {attempt} of {MAX_ATTEMPTS})")
            time.sleep(RETRY_WAIT_SECONDS)

    raise RuntimeError(f"failed after {MAX_ATTEMPTS} attempts ({problem})")


def fetch_year(year):
    """Request all events of one year."""
    params = {
        **BASE_PARAMS,
        "starttime": f"{year}-01-01",
        "endtime": f"{year + 1}-01-01",
    }
    return request_events(params, str(year))


def fetch_updates(since):
    """Request every event (of any year) that USGS changed after `since`.

    since must be a timezone-aware datetime; it is sent to the API in UTC.
    starttime is required: without it USGS only searches the last 30 days.
    """
    params = {
        **BASE_PARAMS,
        "starttime": f"{FIRST_YEAR}-01-01",
        "updatedafter": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return request_events(params, "updates")


def save_raw(year, content):
    """Write the raw response to data/raw/usgs_<year>.geojson and return the path."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"usgs_{year}.geojson"
    path.write_bytes(content)
    return path


def save_update(content):
    """Write an updates response to data/raw/updates/updates_<UTC time>.geojson."""
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = UPDATES_DIR / f"updates_{stamp}.geojson"
    path.write_bytes(content)
    return path


def main():
    last_year = date.today().year
    failed_years = []
    total_events = 0
    print(f"Full fetch from USGS: {describe_filters()}, "
          f"years {FIRST_YEAR} to {last_year}, one request per year\n")

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
