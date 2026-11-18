-- Views for the analysis queries.
-- Safe to run again at any time (CREATE OR REPLACE): a view stores no data.
--
-- Run after schema.sql, as the project user, so the project user owns the views:
--   psql -U earthquake_app -d earthquake_db -v ON_ERROR_STOP=1 -f sql/views.sql

-- Latest version of each earthquake, with its current preferred USGS id.
-- Analysis queries read from this view, so no earthquake is counted twice
-- once the daily update has stored several versions of it.
-- Includes every event type; analysis queries filter type = 'earthquake'.
CREATE OR REPLACE VIEW earthquake_current AS
SELECT
    e.earthquake_id,
    e.preferred_id,
    v.time,
    v.mag,
    v.mag_type,
    v.status,
    v.place,
    v.type,
    v.latitude,
    v.longitude,
    v.depth,
    v.updated
FROM earthquake AS e
JOIN (
    -- Number the versions of each earthquake, newest = 1.
    -- A window function cannot be used in WHERE directly, so it is
    -- computed here and filtered in the outer query.
    SELECT
        earthquake_version.*,
        row_number() OVER (
            PARTITION BY earthquake_id
            ORDER BY updated DESC
        ) AS version_rank
    FROM earthquake_version
) AS v ON v.earthquake_id = e.earthquake_id
WHERE v.version_rank = 1;
