-- Plein Tarif: one named query per business question (run by analyze.py; results in reports/tables/).
-- Session variables (set by model.connect_db from config.yaml):
--   analysis_year, min_stations, min_days, focus_city, litres_per_year, event_window_days, big_cities
-- Prices are in EUR/litre; gaps are reported in cents per litre (cts).
-- Every statistic requires >= min_stations stations and >= min_days days per group.


-- name: q1_department_rank
-- Q1 Geography. What is the median pump price in each department in the latest full year, how does it
-- compare with the national median, and how do departments rank (1 = cheapest)?
WITH base AS (
    SELECT s.department, s.region, f.fuel_code, p.price_ttc, p.station_key, p.date_key
    FROM fact_price_daily p
    JOIN dim_date d USING (date_key)
    JOIN dim_station s USING (station_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE d.year = getvariable('analysis_year') AND f.is_main_fuel
),
national AS (
    SELECT fuel_code, median(price_ttc) AS national_median FROM base GROUP BY fuel_code
),
dept AS (
    SELECT department, any_value(region) AS region, fuel_code,
           median(price_ttc) AS median_ttc,
           count(DISTINCT station_key) AS n_stations,
           count(DISTINCT date_key) AS n_days
    FROM base
    GROUP BY department, fuel_code
    HAVING count(DISTINCT station_key) >= getvariable('min_stations')
       AND count(DISTINCT date_key) >= getvariable('min_days')
)
SELECT dept.fuel_code, dept.department, dept.region, round(dept.median_ttc, 4) AS median_ttc,
       round(100 * (dept.median_ttc - n.national_median), 2) AS gap_vs_national_cts,
       RANK() OVER (PARTITION BY dept.fuel_code ORDER BY dept.median_ttc) AS rank_cheapest,
       dept.n_stations, dept.n_days
FROM dept JOIN national n USING (fuel_code)
ORDER BY dept.fuel_code, rank_cheapest;


-- name: q1_department_spread
-- Q1 Geography. How large is the gap between the cheapest and the most expensive department?
WITH dept AS (
    SELECT s.department, f.fuel_code, median(p.price_ttc) AS median_ttc
    FROM fact_price_daily p
    JOIN dim_date d USING (date_key)
    JOIN dim_station s USING (station_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE d.year = getvariable('analysis_year') AND f.is_main_fuel
    GROUP BY ALL
    HAVING count(DISTINCT p.station_key) >= getvariable('min_stations')
       AND count(DISTINCT p.date_key) >= getvariable('min_days')
)
SELECT fuel_code,
       arg_min(department, median_ttc) AS cheapest_department,
       round(min(median_ttc), 4)       AS cheapest_median,
       arg_max(department, median_ttc) AS most_expensive_department,
       round(max(median_ttc), 4)       AS most_expensive_median,
       round(100 * (max(median_ttc) - min(median_ttc)), 2) AS spread_cts,
       round(100 * (quantile_cont(median_ttc, 0.9) - quantile_cont(median_ttc, 0.1)), 2) AS p90_p10_spread_cts,
       count(*) AS n_departments
FROM dept
GROUP BY fuel_code
ORDER BY fuel_code;


-- name: q1_region_rollup
-- Q1 Geography. Median price by region, with the national total on the same table (GROUPING SETS).
SELECT CASE WHEN grouping(s.region) = 1 THEN 'France (métropole + Corse)' ELSE s.region END AS region,
       f.fuel_code,
       round(median(p.price_ttc), 4)    AS median_ttc,
       round(avg(p.price_ht), 4)        AS mean_ht,
       count(DISTINCT p.station_key)    AS n_stations,
       grouping(s.region)               AS is_total
FROM fact_price_daily p
JOIN dim_date d USING (date_key)
JOIN dim_station s USING (station_key)
JOIN dim_fuel f USING (fuel_key)
WHERE d.year = getvariable('analysis_year') AND f.is_main_fuel
GROUP BY GROUPING SETS ((s.region, f.fuel_code), (f.fuel_code))
HAVING count(DISTINCT p.station_key) >= getvariable('min_stations')
ORDER BY f.fuel_code, is_total DESC, median_ttc;


-- name: q1_big_cities
-- Q1 Geography. Median price in the biggest cities (arrondissements merged) and gap to the national median.
WITH base AS (
    SELECT s.city_std, f.fuel_code, p.price_ttc, p.station_key
    FROM fact_price_daily p
    JOIN dim_date d USING (date_key)
    JOIN dim_station s USING (station_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE d.year = getvariable('analysis_year') AND f.is_main_fuel
),
national AS (SELECT fuel_code, median(price_ttc) AS national_median FROM base GROUP BY fuel_code)
SELECT b.city_std AS city, b.fuel_code,
       round(median(b.price_ttc), 4) AS median_ttc,
       round(100 * (median(b.price_ttc) - any_value(n.national_median)), 2) AS gap_vs_national_cts,
       count(DISTINCT b.station_key) AS n_stations
FROM base b JOIN national n USING (fuel_code)
WHERE list_contains(getvariable('big_cities'), b.city_std)
GROUP BY ALL
HAVING count(DISTINCT b.station_key) >= getvariable('min_stations')
ORDER BY b.fuel_code, median_ttc;


-- name: q1_department_yearly_lag
-- Q1 Geography over time. Yearly median per department and change versus the previous year (LAG).
WITH y AS (
    SELECT d.year, s.department, f.fuel_code, median(p.price_ttc) AS median_ttc
    FROM fact_price_daily p
    JOIN dim_date d USING (date_key)
    JOIN dim_station s USING (station_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE f.is_main_fuel
    GROUP BY ALL
    HAVING count(DISTINCT p.station_key) >= getvariable('min_stations')
)
SELECT year, department, fuel_code, round(median_ttc, 4) AS median_ttc,
       round(100 * (median_ttc - LAG(median_ttc) OVER w), 2) AS change_vs_previous_year_cts,
       RANK() OVER (PARTITION BY year, fuel_code ORDER BY median_ttc) AS rank_cheapest
FROM y
WINDOW w AS (PARTITION BY department, fuel_code ORDER BY year)
ORDER BY fuel_code, department, year;


-- name: q2_motorway_premium_summary
-- Q2 Motorway premium. For each motorway station, the median daily gap versus regular stations within
-- 10 km on the same day (matched comparison), then the distribution across motorway stations, per year.
WITH per_station AS (
    SELECT d.year, f.fuel_code, v.station_key, median(v.gap) AS median_gap, count(*) AS n_days
    FROM v_motorway_gap_daily v
    JOIN dim_date d USING (date_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE f.is_main_fuel
    GROUP BY ALL
    HAVING count(*) >= getvariable('min_days')
)
SELECT year, fuel_code,
       count(*)                                              AS n_motorway_stations,
       round(100 * median(median_gap), 1)                    AS median_premium_cts,
       round(100 * quantile_cont(median_gap, 0.25), 1)       AS p25_premium_cts,
       round(100 * quantile_cont(median_gap, 0.75), 1)       AS p75_premium_cts,
       round(100 * quantile_cont(median_gap, 0.90), 1)       AS p90_premium_cts,
       round(count(*) FILTER (WHERE median_gap > 0) / count(*), 3) AS share_more_expensive
FROM per_station
GROUP BY ROLLUP (fuel_code, year)
HAVING count(*) >= getvariable('min_stations')
ORDER BY fuel_code NULLS LAST, year NULLS LAST;


-- name: q2_motorway_premium_by_station
-- Q2 Motorway premium. Station-level detail for the latest year (feeds the distribution chart).
SELECT f.fuel_code, s.station_id, s.city_std, s.department, s.brand,
       round(100 * median(v.gap), 1) AS median_premium_cts,
       round(avg(v.n_road), 1)        AS avg_road_neighbours,
       count(*)                        AS n_days
FROM v_motorway_gap_daily v
JOIN dim_date d USING (date_key)
JOIN dim_fuel f USING (fuel_key)
JOIN dim_station s USING (station_key)
WHERE d.year = getvariable('analysis_year') AND f.is_main_fuel
GROUP BY ALL
HAVING count(*) >= getvariable('min_days')
ORDER BY f.fuel_code, median_premium_cts DESC;


-- name: q3_brand_category
-- Q3 Brands. Median price by brand category (supermarket / oil company / independent), regular roads only,
-- and the gap to supermarkets (window aggregate with FILTER).
WITH cat AS (
    SELECT d.year, f.fuel_code, s.brand_category, any_value(s.brand_category_label) AS category_label,
           median(p.price_ttc) AS median_ttc, avg(p.price_ht) AS mean_ht,
           count(DISTINCT p.station_key) AS n_stations
    FROM fact_price_daily p
    JOIN dim_date d USING (date_key)
    JOIN dim_station s USING (station_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE f.is_main_fuel AND NOT s.is_motorway AND s.brand_category IS NOT NULL
    GROUP BY ALL
    HAVING count(DISTINCT p.station_key) >= getvariable('min_stations')
)
SELECT year, fuel_code, brand_category, category_label, round(median_ttc, 4) AS median_ttc,
       round(mean_ht, 4) AS mean_ht, n_stations,
       round(100 * (median_ttc - max(median_ttc) FILTER (WHERE brand_category = 'supermarket')
                                    OVER (PARTITION BY year, fuel_code)), 2) AS gap_vs_supermarket_cts
FROM cat
ORDER BY fuel_code, year, median_ttc;


-- name: q3_brand_local_gap
-- Q3 Brands, controlling for location. Each station's daily price minus the median of the other stations
-- within 5 km on the same day, averaged by brand (latest year, regular roads, the 15 brands with the most
-- stations per fuel, each with >= min_stations stations).
WITH local_median AS (
    SELECT n.station_key, p.date_key, p.fuel_key, median(p.price_ttc) AS local_median
    FROM bridge_station_neighbor n
    JOIN fact_price_daily p ON p.station_key = n.neighbor_key
    JOIN dim_date d ON d.date_key = p.date_key
    WHERE n.relation = 'nearby' AND d.year = getvariable('analysis_year')
    GROUP BY ALL
),
gaps AS (
    SELECT s.brand, s.brand_category, f.fuel_code, p.station_key, p.price_ttc - l.local_median AS gap
    FROM fact_price_daily p
    JOIN local_median l USING (station_key, date_key, fuel_key)
    JOIN dim_station s USING (station_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE f.is_main_fuel AND NOT s.is_motorway
),
top_brands AS (
    SELECT fuel_code, brand, brand_category,
           avg(gap)                    AS mean_gap,
           median(gap)                 AS median_gap,
           count(DISTINCT station_key) AS n_stations
    FROM gaps
    GROUP BY fuel_code, brand, brand_category
    HAVING count(DISTINCT station_key) >= getvariable('min_stations')
    QUALIFY row_number() OVER (PARTITION BY fuel_code ORDER BY count(DISTINCT station_key) DESC) <= 15
)
SELECT fuel_code, brand, brand_category,
       round(100 * mean_gap, 2)   AS mean_local_gap_cts,
       round(100 * median_gap, 2) AS median_local_gap_cts,
       n_stations,
       RANK() OVER (PARTITION BY fuel_code ORDER BY mean_gap) AS rank_cheapest
FROM top_brands
ORDER BY fuel_code, mean_local_gap_cts;


-- name: q4_weekly_national
-- Q4 Rockets and feathers (input series). Weekly (week ending Friday) national average pre-tax price
-- and Brent in EUR/litre, main fuels.
SELECT CAST(date + INTERVAL (((5 - isodow(date)) + 7) % 7) DAY AS DATE) AS week_end,
       fuel_code,
       avg(price_ht_mean)   AS price_ht,
       avg(price_ttc_mean)  AS price_ttc,
       avg(brent_eur_l)     AS brent_eur_l,
       avg(n_stations)      AS n_stations,
       count(*)             AS n_days
FROM v_national_daily
WHERE fuel_code IN ('Gazole', 'E10')
GROUP BY ALL
ORDER BY fuel_code, week_end;


-- name: q4_national_rolling
-- Q4/Q5 context. National daily median price with 7- and 30-day rolling medians and daily change (LAG).
SELECT date, fuel_code, round(price_ttc_median, 4) AS median_ttc,
       round(median(price_ttc_median) OVER w7, 4)  AS rolling_7d_median,
       round(median(price_ttc_median) OVER w30, 4) AS rolling_30d_median,
       round(100 * (price_ttc_median - LAG(price_ttc_median) OVER (PARTITION BY fuel_code ORDER BY date)), 2)
           AS daily_change_cts,
       round(brent_eur_l, 4) AS brent_eur_l
FROM v_national_daily
WHERE fuel_code IN ('Gazole', 'E10')
WINDOW w7  AS (PARTITION BY fuel_code ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
       w30 AS (PARTITION BY fuel_code ORDER BY date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
ORDER BY fuel_code, date;


-- name: q5_discount_event_study
-- Q5 The 2022 crisis. Around each start/change/end of the state discount: change in the average pre-tax
-- price between the 14 days before and the 14 days from the event, compared with Brent over the same days.
WITH events AS (
    SELECT date AS event_date, discount_ht AS new_discount,
           LAG(discount_ht) OVER (ORDER BY date) AS old_discount
    FROM dim_date
    QUALIFY discount_ht <> LAG(discount_ht) OVER (ORDER BY date)
),
win AS (
    SELECT e.event_date, e.old_discount, e.new_discount, n.fuel_code,
           n.date < e.event_date AS is_before, n.price_ht_mean, n.price_ttc_mean, n.brent_eur_l
    FROM events e
    JOIN v_national_daily n
      ON n.date BETWEEN e.event_date - INTERVAL (getvariable('event_window_days')) DAY
                    AND e.event_date + INTERVAL (getvariable('event_window_days') - 1) DAY
    WHERE n.fuel_code IN ('Gazole', 'E10')
)
SELECT event_date, fuel_code, old_discount AS old_discount_ht, new_discount AS new_discount_ht,
       round(100 * (avg(price_ttc_mean) FILTER (WHERE NOT is_before) - avg(price_ttc_mean) FILTER (WHERE is_before)), 2)
           AS change_ttc_cts,
       round(100 * (avg(price_ht_mean) FILTER (WHERE NOT is_before) - avg(price_ht_mean) FILTER (WHERE is_before)), 2)
           AS change_ht_cts,
       round(100 * (avg(brent_eur_l) FILTER (WHERE NOT is_before) - avg(brent_eur_l) FILTER (WHERE is_before)), 2)
           AS change_brent_cts,
       round(-120 * (new_discount - old_discount), 2) AS expected_ttc_change_from_discount_cts
FROM win
GROUP BY ALL
ORDER BY event_date, fuel_code;


-- name: q6_savings_focus_city
-- Q6 Shopping around. In the focus city, for every station and day: gap between the station's price and
-- the cheapest station within 5 km (itself included). Yearly savings = mean gap x litres per year.
WITH city AS (
    SELECT station_key FROM dim_station WHERE city_std = getvariable('focus_city')
),
cheapest AS (
    SELECT c.station_key, p.date_key, p.fuel_key, min(p.price_ttc) AS cheapest_nearby
    FROM city c
    JOIN (SELECT station_key, neighbor_key FROM bridge_station_neighbor WHERE relation = 'nearby'
          UNION ALL SELECT station_key, station_key FROM city) n ON n.station_key = c.station_key
    JOIN fact_price_daily p ON p.station_key = n.neighbor_key
    JOIN dim_date d ON d.date_key = p.date_key
    WHERE d.year = getvariable('analysis_year')
    GROUP BY ALL
),
gaps AS (
    SELECT f.fuel_code, p.station_key, p.price_ttc, ch.cheapest_nearby, p.price_ttc - ch.cheapest_nearby AS gap
    FROM fact_price_daily p
    JOIN cheapest ch USING (station_key, date_key, fuel_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE f.is_main_fuel
)
SELECT fuel_code,
       count(DISTINCT station_key)                       AS n_stations,
       round(avg(price_ttc), 4)                          AS mean_price,
       round(avg(cheapest_nearby), 4)                    AS mean_cheapest_within_5km,
       round(100 * avg(gap), 2)                          AS mean_gap_cts,
       round(avg(gap) * getvariable('litres_per_year'), 0) AS yearly_savings_eur,
       round(quantile_cont(gap, 0.9) * getvariable('litres_per_year'), 0) AS yearly_savings_p90_eur
FROM gaps
GROUP BY fuel_code
HAVING count(DISTINCT station_key) >= getvariable('min_stations')
ORDER BY fuel_code;


-- name: q7_weekday_changes
-- Q7 Weekday effect. Number, share and size of price changes by weekday (all stations, main fuels),
-- normalised by the number of calendar days of each weekday.
WITH ch AS (
    SELECT d.weekday_num, d.weekday_name, f.fuel_code, c.change
    FROM fact_price_change c
    JOIN dim_date d USING (date_key)
    JOIN dim_fuel f USING (fuel_key)
    WHERE f.is_main_fuel
),
days AS (SELECT weekday_num, count(*) AS n_calendar_days FROM dim_date GROUP BY weekday_num)
SELECT ch.fuel_code, ch.weekday_num, any_value(ch.weekday_name) AS weekday,
       count(*)                                                AS n_changes,
       round(count(*) / any_value(days.n_calendar_days), 0)    AS changes_per_day,
       round(count(*) / sum(count(*)) OVER (PARTITION BY ch.fuel_code), 4) AS share_of_changes,
       round(count(*) FILTER (WHERE change > 0) / count(*), 4) AS share_increases,
       round(100 * avg(change) FILTER (WHERE change > 0), 2)   AS mean_increase_cts,
       round(100 * avg(change) FILTER (WHERE change < 0), 2)   AS mean_decrease_cts,
       round(100 * avg(abs(change)), 2)                        AS mean_abs_change_cts
FROM ch JOIN days USING (weekday_num)
GROUP BY ch.fuel_code, ch.weekday_num
ORDER BY ch.fuel_code, ch.weekday_num;


-- name: q7_holiday_eve_changes
-- Q7 Weekday effect. Are increases more frequent on the day before a weekend or a public holiday?
SELECT f.fuel_code, d.is_day_before_holiday_or_weekend,
       count(*) AS n_changes,
       round(count(*) / count(DISTINCT d.date_key), 0) AS changes_per_day,
       round(count(*) FILTER (WHERE c.change > 0) / count(*), 4) AS share_increases,
       round(100 * avg(c.change), 3) AS mean_change_cts
FROM fact_price_change c
JOIN dim_date d USING (date_key)
JOIN dim_fuel f USING (fuel_key)
WHERE f.is_main_fuel
GROUP BY ALL
ORDER BY ALL;


-- name: q8_isolated_stations
-- Q8 Regulator. Stations without any competitor within 5 km versus stations with competitors: price gap
-- to their department's median on the same day (latest year, regular roads).
WITH competitors AS (
    SELECT s.station_key, count(n.neighbor_key) AS n_competitors
    FROM dim_station s
    LEFT JOIN bridge_station_neighbor n ON n.station_key = s.station_key AND n.relation = 'nearby'
    GROUP BY s.station_key
),
dept_day AS (
    SELECT s.department, p.date_key, p.fuel_key, median(p.price_ttc) AS dept_median
    FROM fact_price_daily p JOIN dim_station s USING (station_key) JOIN dim_date d USING (date_key)
    WHERE d.year = getvariable('analysis_year')
    GROUP BY ALL
)
SELECT f.fuel_code,
       CASE WHEN c.n_competitors = 0 THEN '0 concurrent'
            WHEN c.n_competitors <= 2 THEN '1-2 concurrents'
            WHEN c.n_competitors <= 5 THEN '3-5 concurrents'
            ELSE '6+ concurrents' END AS competition,
       count(DISTINCT p.station_key)                       AS n_stations,
       round(100 * avg(p.price_ttc - dd.dept_median), 2)   AS mean_gap_vs_department_cts
FROM fact_price_daily p
JOIN dim_station s USING (station_key)
JOIN competitors c USING (station_key)
JOIN dim_date d USING (date_key)
JOIN dim_fuel f USING (fuel_key)
JOIN dept_day dd ON dd.department = s.department AND dd.date_key = p.date_key AND dd.fuel_key = p.fuel_key
WHERE d.year = getvariable('analysis_year') AND f.is_main_fuel AND NOT s.is_motorway
GROUP BY ALL
HAVING count(DISTINCT p.station_key) >= getvariable('min_stations')
ORDER BY f.fuel_code, competition;


-- name: q8_department_competition
-- Q8 Regulator. Departments where prices are high AND local competition is thin: median price gap,
-- share of stations without competitor within 5 km, and price dispersion (IQR).
WITH st AS (
    SELECT s.station_key, s.department,
           count(n.neighbor_key) FILTER (WHERE n.relation = 'nearby') AS n_competitors
    FROM dim_station s LEFT JOIN bridge_station_neighbor n ON n.station_key = s.station_key
    WHERE NOT s.is_motorway
    GROUP BY ALL
),
prices AS (
    SELECT s.department, p.station_key, p.price_ttc
    FROM fact_price_daily p
    JOIN dim_date d USING (date_key) JOIN dim_fuel f USING (fuel_key) JOIN dim_station s USING (station_key)
    WHERE d.year = getvariable('analysis_year') AND f.fuel_code = 'Gazole' AND NOT s.is_motorway
),
national AS (SELECT median(price_ttc) AS m FROM prices)
SELECT p.department,
       round(100 * (median(p.price_ttc) - any_value(national.m)), 2) AS gazole_gap_vs_national_cts,
       round(100 * (quantile_cont(p.price_ttc, 0.75) - quantile_cont(p.price_ttc, 0.25)), 2) AS iqr_cts,
       count(DISTINCT p.station_key) AS n_stations,
       round(avg((st.n_competitors = 0)::INT), 3) AS share_without_competitor_5km
FROM prices p JOIN st USING (station_key, department) CROSS JOIN national
GROUP BY p.department
HAVING count(DISTINCT p.station_key) >= getvariable('min_stations')
ORDER BY gazole_gap_vs_national_cts DESC;


-- name: check_national_monthly
-- Quality check. National average Gazole price (station-day mean) for three months; re-computed in pandas.
SELECT d.year_month, round(avg(p.price_ttc), 5) AS mean_ttc, count(*) AS n_station_days
FROM fact_price_daily p JOIN dim_date d USING (date_key) JOIN dim_fuel f USING (fuel_key)
WHERE f.fuel_code = 'Gazole' AND d.year_month IN ('2019-06', '2022-06', '2025-06')
GROUP BY d.year_month ORDER BY d.year_month;


-- name: check_department_median
-- Quality check. Median Gazole price in two departments for the latest year; re-computed in pandas.
SELECT s.department, round(median(p.price_ttc), 5) AS median_ttc, count(*) AS n_station_days
FROM fact_price_daily p JOIN dim_date d USING (date_key) JOIN dim_fuel f USING (fuel_key)
JOIN dim_station s USING (station_key)
WHERE f.fuel_code = 'Gazole' AND d.year = getvariable('analysis_year') AND s.department IN ('31', '75')
GROUP BY s.department ORDER BY s.department;


-- name: q7_weekday_price_level
-- Q7 Weekday effect, in money terms. Is the national average price lower on some weekdays? Daily national
-- mean minus its centred 7-day moving average (removes the trend), averaged by weekday.
WITH d AS (
    SELECT n.date, n.fuel_code, n.price_ttc_mean,
           avg(n.price_ttc_mean) OVER (PARTITION BY n.fuel_code ORDER BY n.date
                                       ROWS BETWEEN 3 PRECEDING AND 3 FOLLOWING) AS centred_7d
    FROM v_national_daily n
    WHERE n.fuel_code IN ('Gazole', 'E10')
)
SELECT d.fuel_code, dd.weekday_num, dd.weekday_name AS weekday,
       round(100 * avg(d.price_ttc_mean - d.centred_7d), 3) AS mean_gap_vs_week_cts,
       count(*) AS n_days
FROM d JOIN dim_date dd ON dd.date = d.date
GROUP BY ALL
ORDER BY d.fuel_code, dd.weekday_num;
