-- Plein Tarif: DuckDB star schema (data/plein_tarif.duckdb)
-- Grain and keys are documented in docs/data_dictionary.md. Tables are (re)created by model.py.

-- Fuels ------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE dim_fuel (
    fuel_key        TINYINT PRIMARY KEY,     -- same id as the official files (1 = Gazole ... 6 = SP98)
    fuel_code       VARCHAR NOT NULL,        -- code in the official files (Gazole, E10, ...)
    fuel_label      VARCHAR NOT NULL,        -- display label (SP95-E10, ...)
    is_main_fuel    BOOLEAN NOT NULL,        -- Gazole and SP95-E10
    display_order   TINYINT NOT NULL
);

-- Calendar ---------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE dim_date (
    date_key        INTEGER PRIMARY KEY,     -- yyyymmdd
    date            DATE NOT NULL,
    year            SMALLINT NOT NULL,
    quarter         TINYINT NOT NULL,
    month           TINYINT NOT NULL,
    month_name      VARCHAR NOT NULL,        -- French
    year_month      VARCHAR NOT NULL,        -- 2022-03
    iso_week        TINYINT NOT NULL,
    weekday_num     TINYINT NOT NULL,        -- 1 = Monday ... 7 = Sunday
    weekday_name    VARCHAR NOT NULL,        -- French
    is_weekend      BOOLEAN NOT NULL,
    is_holiday      BOOLEAN NOT NULL,        -- French national public holiday
    holiday_name    VARCHAR,
    is_day_before_holiday_or_weekend BOOLEAN NOT NULL,
    discount_ht     DOUBLE NOT NULL,         -- 2022 state discount in force (EUR/l, excl. VAT)
    discount_label  VARCHAR,
    event_label     VARCHAR                  -- annotation (lockdowns, invasion of Ukraine, ...)
);

-- Stations ---------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE dim_station (
    station_key             INTEGER PRIMARY KEY,
    station_id              BIGINT NOT NULL UNIQUE,   -- official point-of-sale id
    lat                     DOUBLE,
    lon                     DOUBLE,
    address                 VARCHAR,
    postcode                VARCHAR,
    city                    VARCHAR,
    city_std                VARCHAR,                  -- standardised city name (arrondissements merged)
    department              VARCHAR,
    region_code             VARCHAR,
    region                  VARCHAR,
    zone                    VARCHAR,                  -- mainland | corsica
    tax_zone                VARCHAR,                  -- mainland | idf | corsica
    is_motorway             BOOLEAN NOT NULL,
    road_type               VARCHAR NOT NULL,         -- Autoroute | Route
    services                VARCHAR,                  -- pipe-separated list
    automate_24_24          BOOLEAN,
    brand                   VARCHAR,                  -- normalised OSM brand ('Non identifiée' if unmatched)
    brand_category          VARCHAR,                  -- supermarket | oil_company | independent_other | unmatched
    brand_category_label    VARCHAR,                  -- French label
    osm_match_distance_m    DOUBLE,
    brand_ambiguous         BOOLEAN,
    first_year              SMALLINT,
    last_year               SMALLINT,
    motorway_flag_changes   TINYINT
);

-- Market and taxes, daily, per fuel (mainland taxes outside Île-de-France) --------------------------
CREATE OR REPLACE TABLE dim_market (
    date_key        INTEGER NOT NULL,
    fuel_key        TINYINT NOT NULL,
    brent_usd_bbl   DOUBLE,
    eurusd          DOUBLE,                  -- USD per EUR
    brent_eur_bbl   DOUBLE,
    brent_eur_l     DOUBLE,
    is_trading_day  BOOLEAN,
    vat             DOUBLE NOT NULL,
    excise          DOUBLE NOT NULL,         -- EUR/l incl. regional majoration
    discount_ht     DOUBLE NOT NULL,
    discount_ttc    DOUBLE NOT NULL,
    PRIMARY KEY (date_key, fuel_key)
);

-- Tax table used to compute pre-tax prices per station tax zone --------------------------------------
CREATE OR REPLACE TABLE ref_tax (
    date_key        INTEGER NOT NULL,
    fuel_key        TINYINT NOT NULL,
    tax_zone        VARCHAR NOT NULL,
    vat             DOUBLE NOT NULL,
    excise          DOUBLE NOT NULL,
    discount_ht     DOUBLE NOT NULL,
    PRIMARY KEY (date_key, fuel_key, tax_zone)
);

-- Facts ------------------------------------------------------------------------------------------
-- One row per station, fuel and day with a known price (carried forward at most N days).
CREATE OR REPLACE TABLE fact_price_daily (
    date_key        INTEGER NOT NULL,
    station_key     INTEGER NOT NULL,
    fuel_key        TINYINT NOT NULL,
    price_ttc       DOUBLE NOT NULL,         -- displayed price (EUR/l, net of the 2022 discount)
    price_ht        DOUBLE NOT NULL,         -- distributor price before excise, VAT and discount
    staleness_days  TINYINT NOT NULL         -- days since the last update
);

-- One row per actual price change reported by a station.
CREATE OR REPLACE TABLE fact_price_change (
    station_key     INTEGER NOT NULL,
    fuel_key        TINYINT NOT NULL,
    date_key        INTEGER NOT NULL,
    ts              TIMESTAMP NOT NULL,
    old_price       DOUBLE NOT NULL,
    new_price       DOUBLE NOT NULL,
    change          DOUBLE NOT NULL,
    hours_since_previous DOUBLE
);

-- Pre-computed spatial neighbour pairs (haversine BallTree in model.py).
--   relation = 'motorway_vs_road' : motorway station -> regular stations within motorway_neighbour_km
--   relation = 'nearby'           : any station -> other stations within savings_radius_km
CREATE OR REPLACE TABLE bridge_station_neighbor (
    station_key     INTEGER NOT NULL,
    neighbor_key    INTEGER NOT NULL,
    relation        VARCHAR NOT NULL,
    distance_km     DOUBLE NOT NULL
);
