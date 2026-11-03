-- Plein Tarif: analytical views on top of the star schema.
-- Minimum-sample rule: statistics are only published for groups with at least
-- getvariable('min_stations') stations and getvariable('min_days') days. These session variables are set
-- from config.yaml by model.connect_db() on every connection (DuckDB does not persist them).

-- Daily prices with readable attributes (main entry point for ad hoc queries).
CREATE OR REPLACE VIEW v_price_daily AS
SELECT d.date, d.year, d.year_month, d.weekday_num, f.fuel_code, f.fuel_label, f.is_main_fuel,
       s.station_key, s.station_id, s.city_std, s.department, s.region, s.zone, s.is_motorway, s.road_type,
       s.brand, s.brand_category, s.brand_category_label,
       p.price_ttc, p.price_ht, p.staleness_days
FROM fact_price_daily p
JOIN dim_date d USING (date_key)
JOIN dim_fuel f USING (fuel_key)
JOIN dim_station s USING (station_key);

-- National daily averages (mainland + Corsica) with Brent and taxes.
CREATE OR REPLACE VIEW v_national_daily AS
SELECT d.date, d.date_key, f.fuel_key, f.fuel_code, f.fuel_label,
       count(*)                     AS n_stations,
       avg(p.price_ttc)             AS price_ttc_mean,
       median(p.price_ttc)          AS price_ttc_median,
       avg(p.price_ht)              AS price_ht_mean,
       median(p.price_ht)           AS price_ht_median,
       any_value(m.brent_eur_l)     AS brent_eur_l,
       any_value(m.brent_usd_bbl)   AS brent_usd_bbl,
       any_value(m.eurusd)          AS eurusd,
       any_value(m.excise)          AS excise,
       any_value(m.vat)             AS vat,
       any_value(m.discount_ttc)    AS discount_ttc
FROM fact_price_daily p
JOIN dim_date d USING (date_key)
JOIN dim_fuel f USING (fuel_key)
JOIN dim_market m ON m.date_key = p.date_key AND m.fuel_key = p.fuel_key
GROUP BY ALL;

-- Monthly statistics per department and fuel (minimum-sample rule applied by `is_publishable`).
CREATE OR REPLACE VIEW v_department_month AS
SELECT d.year, d.year_month, s.department, s.region, f.fuel_code, f.fuel_label,
       count(DISTINCT p.station_key) AS n_stations,
       count(DISTINCT p.date_key)    AS n_days,
       median(p.price_ttc)           AS price_ttc_median,
       avg(p.price_ttc)              AS price_ttc_mean,
       avg(p.price_ht)               AS price_ht_mean,
       count(DISTINCT p.station_key) >= getvariable('min_stations') AS is_publishable
FROM fact_price_daily p
JOIN dim_date d USING (date_key)
JOIN dim_fuel f USING (fuel_key)
JOIN dim_station s USING (station_key)
GROUP BY ALL;

-- Station-level yearly averages.
CREATE OR REPLACE VIEW v_station_year AS
SELECT d.year, p.station_key, f.fuel_code,
       count(*)            AS n_days,
       avg(p.price_ttc)    AS price_ttc_mean,
       avg(p.price_ht)     AS price_ht_mean
FROM fact_price_daily p
JOIN dim_date d USING (date_key)
JOIN dim_fuel f USING (fuel_key)
GROUP BY ALL;

-- Matched motorway comparison: each motorway station vs the median of regular stations within 10 km,
-- on the same day and for the same fuel.
CREATE OR REPLACE VIEW v_motorway_gap_daily AS
WITH neighbour_prices AS (
    SELECT n.station_key AS motorway_key, p.date_key, p.fuel_key,
           median(p.price_ttc) AS road_median, count(*) AS n_road
    FROM bridge_station_neighbor n
    JOIN fact_price_daily p ON p.station_key = n.neighbor_key
    WHERE n.relation = 'motorway_vs_road'
    GROUP BY ALL
)
SELECT m.station_key, m.date_key, m.fuel_key, m.price_ttc AS motorway_price, np.road_median, np.n_road,
       m.price_ttc - np.road_median AS gap
FROM fact_price_daily m
JOIN neighbour_prices np ON np.motorway_key = m.station_key AND np.date_key = m.date_key
                        AND np.fuel_key = m.fuel_key;
