-- Database schema for the earthquake data pipeline.
-- Design decisions and their reasons are in the decision log.
--
-- Run once on an empty database:
--   psql -U earthquake_app -d earthquake_db -v ON_ERROR_STOP=1 -f sql/schema.sql

-- All statements run in one transaction: if one fails, nothing is created.
BEGIN;

-- Strong entity: one row per earthquake.
-- earthquake_id is our own surrogate key; USGS ids can change over time.
CREATE TABLE earthquake (
    earthquake_id INTEGER GENERATED ALWAYS AS IDENTITY,
    preferred_id  TEXT NOT NULL,  -- current preferred USGS id, no history

    CONSTRAINT earthquake_pkey PRIMARY KEY (earthquake_id)
);

-- Multivalued attribute "ids": every USGS id of an earthquake.
-- A USGS id belongs to only one earthquake, so usgs_id alone is the primary key.
CREATE TABLE earthquake_usgs_id (
    usgs_id       TEXT    NOT NULL,
    earthquake_id INTEGER NOT NULL,

    CONSTRAINT earthquake_usgs_id_pkey PRIMARY KEY (usgs_id),
    -- Not a key of its own: it only lets earthquake.preferred_id reference
    -- the pair (earthquake_id, usgs_id) with a composite foreign key.
    CONSTRAINT earthquake_usgs_id_pair_key UNIQUE (earthquake_id, usgs_id),
    CONSTRAINT earthquake_usgs_id_earthquake_fkey
        FOREIGN KEY (earthquake_id) REFERENCES earthquake (earthquake_id)
        ON DELETE RESTRICT
);

-- The preferred id must exist in earthquake_usgs_id and belong to the same
-- earthquake. The two tables reference each other, so this foreign key can
-- only be added after both tables exist, and it is checked at COMMIT
-- (DEFERRABLE INITIALLY DEFERRED) so both rows can be inserted in one transaction.
ALTER TABLE earthquake
    ADD CONSTRAINT earthquake_preferred_id_fkey
        FOREIGN KEY (earthquake_id, preferred_id)
        REFERENCES earthquake_usgs_id (earthquake_id, usgs_id)
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED;

-- Weak entity: one row per version of an earthquake, as published by USGS.
-- A new version arrives when USGS changes "updated".
CREATE TABLE earthquake_version (
    earthquake_id INTEGER          NOT NULL,
    updated       TIMESTAMPTZ      NOT NULL,  -- partial key
    time          TIMESTAMPTZ      NOT NULL,  -- origin time of the earthquake
    mag           DOUBLE PRECISION NOT NULL,
    mag_type      TEXT             NOT NULL,
    status        TEXT             NOT NULL,
    place         TEXT             NOT NULL,
    type          TEXT             NOT NULL,
    latitude      DOUBLE PRECISION NOT NULL,  -- hypocenter
    longitude     DOUBLE PRECISION NOT NULL,  -- hypocenter
    depth         DOUBLE PRECISION NOT NULL,  -- hypocenter, km; negative = above sea level

    CONSTRAINT earthquake_version_pkey PRIMARY KEY (earthquake_id, updated),
    CONSTRAINT earthquake_version_earthquake_fkey
        FOREIGN KEY (earthquake_id) REFERENCES earthquake (earthquake_id)
        ON DELETE RESTRICT,
    -- Same threshold as the API filter (minmagnitude=2.5).
    CONSTRAINT earthquake_version_mag_check CHECK (mag >= 2.5),
    CONSTRAINT earthquake_version_latitude_check CHECK (latitude BETWEEN -90 AND 90),
    CONSTRAINT earthquake_version_longitude_check CHECK (longitude BETWEEN -180 AND 180),
    CONSTRAINT earthquake_version_status_check
        CHECK (status IN ('automatic', 'reviewed', 'deleted'))
);

COMMIT;
