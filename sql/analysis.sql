-- Analysis queries for the earthquake data pipeline.
--
-- Every query reads the earthquake_current view (one row per earthquake,
-- latest version) and keeps only type = 'earthquake'.
-- Comparisons over time use M4.5+ only: below that the catalog is
-- incomplete in this region, so counts follow network coverage, not activity.
-- Times are converted to UTC before taking the year or month, so the result
-- does not depend on the time zone of the database session.
--
-- Run one query at a time: select it in the pgAdmin Query Tool and press F5.


-- 1. Earthquakes per year: all events (M2.5+) next to the complete part of
--    the catalog (M4.5+). The M2.5+ column swings with network coverage;
--    the M4.5+ column shows the real trend.
SELECT
    extract(year FROM time AT TIME ZONE 'UTC')::int  AS year,
    count(*)                                         AS m25_plus,
    count(*) FILTER (WHERE mag >= 4.5)               AS m45_plus
FROM earthquake_current
WHERE type = 'earthquake'
GROUP BY year
ORDER BY year;


-- 2. The 10 strongest earthquakes.
--    Magnitude types differ (Mw for modern events, other scales for some old
--    ones), so the ranking is approximate across very different periods.
--    Ties are broken by time, so the order is always the same.
SELECT
    time AT TIME ZONE 'UTC'  AS time_utc,
    mag,
    mag_type,
    place,
    depth,
    preferred_id
FROM earthquake_current
WHERE type = 'earthquake'
ORDER BY mag DESC, time
LIMIT 10;


-- 3. Magnitude distribution in 0.5 bins (2.5 = 2.5 to 2.9, 3.0 = 3.0 to 3.4, ...).
--    Small earthquakes are far more common than large ones; this is the
--    starting point of the Gutenberg-Richter model in Stage 9.
SELECT
    floor(mag * 2) / 2  AS mag_bin,
    count(*)            AS earthquakes
FROM earthquake_current
WHERE type = 'earthquake'
GROUP BY mag_bin
ORDER BY mag_bin;


-- 4. Depth distribution.
--    When USGS cannot compute the real depth, it uses a fixed value
--    (0, 10 or 33 km). Those rows are counted in their own groups, so they
--    are not read as real "shallow earthquakes". A real depth of exactly
--    10.0 km is counted as fixed too; this is rare and accepted.
--    CASE stops at the first matching WHEN, so the fixed values are checked
--    before the ranges.
--    Groups follow the usual seismology classes: shallow (under 70 km),
--    intermediate (70-300 km), deep (300 km and deeper).
SELECT
    CASE
        WHEN depth = 0   THEN 'fixed 0 km'
        WHEN depth = 10  THEN 'fixed 10 km'
        WHEN depth = 33  THEN 'fixed 33 km'
        WHEN depth < 10  THEN 'under 10 km'
        WHEN depth < 20  THEN '10-20 km'
        WHEN depth < 70  THEN '20-70 km'
        WHEN depth < 300 THEN '70-300 km'
        ELSE                  '300 km and deeper'
    END                          AS depth_group,
    count(*)                     AS earthquakes,
    round(avg(mag)::numeric, 2)  AS avg_mag
FROM earthquake_current
WHERE type = 'earthquake'
GROUP BY depth_group
ORDER BY min(depth);


-- 5. The most active areas: 1 x 1 degree grid cells
--    (about 110 km north-south and 85 km east-west at this latitude).
--    A cell is named by its south-west corner: cell (38, 37) covers
--    latitude 38.0-38.99 and longitude 37.0-37.99.
--    Uses coordinates, not place: place is a label that changes over time
--    and has lost letters ('?').
--    M4.5+ only, so areas with denser station networks do not win just
--    because more small earthquakes were recorded there.
SELECT
    floor(latitude)::int   AS cell_lat,
    floor(longitude)::int  AS cell_lon,
    count(*)               AS earthquakes,
    max(mag)               AS max_mag
FROM earthquake_current
WHERE type = 'earthquake'
  AND mag >= 4.5
GROUP BY cell_lat, cell_lon
ORDER BY earthquakes DESC, cell_lat, cell_lon
LIMIT 15;


-- 6. The 2023 Kahramanmaras sequence: M4.5+ earthquakes per month,
--    2022-2024, inside a box around the rupture
--    (latitude 35.5-39, longitude 35.5-39.5; approximate, so a few
--    unrelated earthquakes may be counted too).
--    generate_series lists every month. The LEFT JOIN keeps months with no
--    earthquakes, so they appear as 0 instead of being left out.
--    count(s.time) counts only matched rows: count(*) would count an empty
--    month as 1 (the row that the LEFT JOIN fills with NULLs).
--    The earthquake filters are inside the subquery. In the outer WHERE
--    they would remove the NULL rows and the empty months with them.
SELECT
    m.month::date  AS month,
    count(s.time)  AS earthquakes
FROM generate_series(
         timestamp '2022-01-01',
         timestamp '2024-12-01',
         interval '1 month'
     ) AS m(month)
LEFT JOIN (
    SELECT time
    FROM earthquake_current
    WHERE type = 'earthquake'
      AND mag >= 4.5
      AND latitude  BETWEEN 35.5 AND 39
      AND longitude BETWEEN 35.5 AND 39.5
) AS s
    ON date_trunc('month', s.time AT TIME ZONE 'UTC') = m.month
GROUP BY m.month
ORDER BY m.month;


-- 7. Time between consecutive M6+ earthquakes, 1973 onwards.
--    lag(time) is a window function: it returns the time of the previous
--    row, in the order given inside OVER (...).
--    WHERE runs before window functions, so the previous row is the
--    previous M6+ earthquake, not the previous earthquake of any size.
--    The first row has no previous row, so its gap is NULL.
--    Subtracting two timestamptz values gives an interval (days and time).
SELECT
    time AT TIME ZONE 'UTC'                  AS time_utc,
    mag,
    place,
    time - lag(time) OVER (ORDER BY time)    AS time_since_previous
FROM earthquake_current
WHERE type = 'earthquake'
  AND mag >= 6
  AND time >= timestamptz '1973-01-01 00:00:00+00'
ORDER BY time;


-- 8. The 3 strongest earthquakes of each decade.
--    rank() numbers the rows inside each decade (PARTITION BY), strongest
--    first. Equal magnitudes get the same rank (1, 1, 3, ...), so a decade
--    can return more than 3 rows; no tied earthquake is dropped at random.
--    A window function cannot be used in WHERE, so the ranking is done in
--    a subquery and filtered outside (same pattern as earthquake_current).
--    The decade expression is repeated inside OVER (...): a column alias
--    from the same SELECT cannot be used there.
SELECT
    decade,
    rank_in_decade,
    time_utc,
    mag,
    mag_type,
    place
FROM (
    SELECT
        (extract(year FROM time AT TIME ZONE 'UTC')::int / 10) * 10  AS decade,
        rank() OVER (
            PARTITION BY (extract(year FROM time AT TIME ZONE 'UTC')::int / 10) * 10
            ORDER BY mag DESC
        )                                                            AS rank_in_decade,
        time AT TIME ZONE 'UTC'                                      AS time_utc,
        mag,
        mag_type,
        place
    FROM earthquake_current
    WHERE type = 'earthquake'
) AS ranked
WHERE rank_in_decade <= 3
ORDER BY decade, rank_in_decade, time_utc;


-- 9. Magnitude types per decade.
--    USGS uses different magnitude scales, so magnitudes from different
--    periods are not fully comparable. The types are grouped into families:
--      Mw    moment magnitude (mw, mww, mwc, mwb, mwr, ...): the modern standard
--      mb    body-wave magnitude, measured by distant stations
--      Ms    surface-wave magnitude, common in older records
--      local local network scales (ML, Md)
--      other everything else, including NULL
--    lower() is used because LIKE is case-sensitive.
--    CASE sends NULL to ELSE: NULL LIKE 'mw%' is NULL, not true.
SELECT
    decade,
    count(*)                                   AS earthquakes,
    count(*) FILTER (WHERE family = 'Mw')      AS mw,
    count(*) FILTER (WHERE family = 'mb')      AS mb,
    count(*) FILTER (WHERE family = 'Ms')      AS ms,
    count(*) FILTER (WHERE family = 'local')   AS local,
    count(*) FILTER (WHERE family = 'other')   AS other
FROM (
    SELECT
        (extract(year FROM time AT TIME ZONE 'UTC')::int / 10) * 10  AS decade,
        CASE
            WHEN lower(mag_type) LIKE 'mw%' THEN 'Mw'
            WHEN lower(mag_type) LIKE 'mb%' THEN 'mb'
            WHEN lower(mag_type) LIKE 'ms%' THEN 'Ms'
            WHEN lower(mag_type) LIKE 'ml%'
              OR lower(mag_type) LIKE 'md%' THEN 'local'
            ELSE                                 'other'
        END                                                          AS family
    FROM earthquake_current
    WHERE type = 'earthquake'
) AS typed
GROUP BY decade
ORDER BY decade;


-- 10. Number of USGS ids per earthquake vs. magnitude.
--     Each USGS id comes from a network or catalog that reported the
--     earthquake, so more ids means more networks recorded it.
--     The ids are counted per earthquake in a subquery first, then joined.
--     Joining earthquake_usgs_id directly would repeat each earthquake once
--     per id, and count(*) and avg(mag) would count ids, not earthquakes.
SELECT
    ids.id_count,
    count(*)                       AS earthquakes,
    round(avg(c.mag)::numeric, 2)  AS avg_mag,
    min(c.mag)                     AS min_mag,
    max(c.mag)                     AS max_mag
FROM earthquake_current AS c
JOIN (
    SELECT earthquake_id, count(*) AS id_count
    FROM earthquake_usgs_id
    GROUP BY earthquake_id
) AS ids ON ids.earthquake_id = c.earthquake_id
WHERE c.type = 'earthquake'
GROUP BY ids.id_count
ORDER BY ids.id_count;
