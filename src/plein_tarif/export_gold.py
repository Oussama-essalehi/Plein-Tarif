"""Export the gold layer for Power BI: pre-aggregated CSV tables in data/gold/.

Format: UTF-8, comma separator, '.' decimal separator, ISO dates (yyyy-mm-dd), one header row.
The daily station panel (~70 million rows) is NOT exported; Power BI gets aggregates sized for a laptop:

  dim_date, dim_fuel, dim_station, dim_department, dim_market   dimensions (dim_market: date x fuel)
  fact_national_daily                              national daily averages by fuel (+ Brent, taxes)
  fact_department_month                            monthly statistics by department x fuel
  fact_brand_category_month                        monthly statistics by brand category x road type x fuel
  fact_station_year                                station x year x fuel averages, gap to the department median,
                                                   to the cheapest station within 5 km (savings simulator) and,
                                                   for motorway stations, to regular stations within 10 km
  fact_rockets_feathers                            cumulative responses (rise / fall), both estimation samples
  dim_event                                        discount periods and annotations
A README.md with the column list is written next to the files.
"""

from __future__ import annotations

import pandas as pd

from plein_tarif.analyze import department_names, tables_dir
from plein_tarif.config import cfg, events, get_logger, path
from plein_tarif.model import connect_db

log = get_logger(__name__)

QUERIES: dict[str, str] = {
    "dim_date": """
        SELECT date_key, strftime(date, '%Y-%m-%d') AS date, year, quarter, month, month_name, year_month,
               iso_week, weekday_num, weekday_name, is_weekend, is_holiday, holiday_name,
               is_day_before_holiday_or_weekend, discount_ht, discount_label, event_label
        FROM dim_date ORDER BY date_key""",
    "dim_fuel": "SELECT * FROM dim_fuel ORDER BY display_order",
    "dim_station": """
        SELECT station_key, station_id, round(lat, 6) AS lat, round(lon, 6) AS lon, address, postcode, city, city_std,
               department, region_code, region, zone, tax_zone, is_motorway, road_type, automate_24_24,
               brand, brand_category, brand_category_label, osm_match_distance_m, brand_ambiguous,
               first_year, last_year
        FROM dim_station ORDER BY station_key""",
    "dim_market": """
        SELECT date_key, fuel_key, round(brent_usd_bbl, 3) AS brent_usd_bbl, round(eurusd, 5) AS eurusd,
               round(brent_eur_l, 5) AS brent_eur_l, is_trading_day, vat, round(excise, 5) AS excise,
               discount_ht, round(discount_ttc, 5) AS discount_ttc
        FROM dim_market ORDER BY date_key, fuel_key""",
    "fact_national_daily": """
        SELECT date_key, fuel_key, n_stations, round(price_ttc_mean, 5) AS price_ttc_mean,
               round(price_ttc_median, 5) AS price_ttc_median, round(price_ht_mean, 5) AS price_ht_mean,
               round(price_ht_median, 5) AS price_ht_median
        FROM v_national_daily ORDER BY date_key, fuel_key""",
    "fact_department_month": """
        SELECT CAST(strftime(min(d.date), '%Y%m%d') AS INTEGER) AS month_date_key, d.year_month, s.department,
               any_value(s.region) AS region, p.fuel_key,
               count(DISTINCT p.station_key) AS n_stations, count(DISTINCT p.date_key) AS n_days, count(*) AS n_station_days,
               round(median(p.price_ttc), 5) AS price_ttc_median, round(avg(p.price_ttc), 5) AS price_ttc_mean,
               round(avg(p.price_ht), 5) AS price_ht_mean,
               round(sum(p.price_ttc), 3) AS price_ttc_sum
        FROM fact_price_daily p JOIN dim_date d USING (date_key) JOIN dim_station s USING (station_key)
        GROUP BY d.year_month, s.department, p.fuel_key
        ORDER BY d.year_month, s.department, p.fuel_key""",
    "fact_brand_category_month": """
        SELECT CAST(strftime(min(d.date), '%Y%m%d') AS INTEGER) AS month_date_key, d.year_month,
               s.brand_category, any_value(s.brand_category_label) AS brand_category_label, s.road_type, p.fuel_key,
               count(DISTINCT p.station_key) AS n_stations, count(*) AS n_station_days,
               round(median(p.price_ttc), 5) AS price_ttc_median, round(avg(p.price_ttc), 5) AS price_ttc_mean,
               round(avg(p.price_ht), 5) AS price_ht_mean, round(sum(p.price_ttc), 3) AS price_ttc_sum
        FROM fact_price_daily p JOIN dim_date d USING (date_key) JOIN dim_station s USING (station_key)
        GROUP BY d.year_month, s.brand_category, s.road_type, p.fuel_key
        ORDER BY 2, 3, 5, 6""",
    "fact_station_year": """
        WITH nb AS (
            SELECT station_key, neighbor_key FROM bridge_station_neighbor WHERE relation = 'nearby'
            UNION ALL SELECT station_key, station_key FROM dim_station
        ),
        cheapest AS (
            SELECT nb.station_key, p.date_key, p.fuel_key, min(p.price_ttc) AS cheapest_5km
            FROM nb JOIN fact_price_daily p ON p.station_key = nb.neighbor_key
            JOIN dim_fuel f ON f.fuel_key = p.fuel_key
            WHERE f.is_main_fuel
            GROUP BY ALL
        ),
        dept_day AS (
            SELECT s.department, p.date_key, p.fuel_key, median(p.price_ttc) AS dept_median
            FROM fact_price_daily p JOIN dim_station s USING (station_key) GROUP BY ALL
        ),
        motorway AS (
            SELECT d.year, v.station_key, v.fuel_key, median(v.gap) AS premium
            FROM v_motorway_gap_daily v JOIN dim_date d USING (date_key)
            GROUP BY ALL HAVING count(*) >= getvariable('min_days')
        )
        SELECT d.year, CAST(d.year AS INTEGER) * 10000 + 101 AS year_date_key, p.station_key, p.fuel_key, count(*) AS n_days,
               round(avg(p.price_ttc), 5) AS price_ttc_mean, round(avg(p.price_ht), 5) AS price_ht_mean,
               round(100 * avg(p.price_ttc - dd.dept_median), 3) AS gap_vs_department_median_cts,
               round(100 * avg(p.price_ttc - c.cheapest_5km), 3) AS gap_vs_cheapest_5km_cts,
               round(100 * any_value(mw.premium), 3) AS motorway_premium_10km_cts
        FROM fact_price_daily p
        JOIN dim_date d USING (date_key)
        JOIN dim_station s USING (station_key)
        JOIN dept_day dd ON dd.department = s.department AND dd.date_key = p.date_key AND dd.fuel_key = p.fuel_key
        LEFT JOIN cheapest c ON c.station_key = p.station_key AND c.date_key = p.date_key AND c.fuel_key = p.fuel_key
        LEFT JOIN motorway mw ON mw.year = d.year AND mw.station_key = p.station_key AND mw.fuel_key = p.fuel_key
        GROUP BY d.year, p.station_key, p.fuel_key
        ORDER BY d.year, p.station_key, p.fuel_key""",
}


def export_table(con, name: str, sql: str) -> int:
    out = path("gold") / f"{name}.csv"
    con.execute(f"COPY ({sql}) TO '{out.as_posix()}' (HEADER, DELIMITER ',', QUOTE '\"')")
    n = con.execute(f"SELECT count(*) FROM read_csv_auto('{out.as_posix()}')").fetchone()[0]
    log.info("gold %-28s %10s rows  %6.1f MB", name, f"{n:,}", out.stat().st_size / 1e6)
    return n


def export_departments(con) -> pd.DataFrame:
    """One row per department with its name, region and a geocoding-friendly location for map visuals."""
    names = department_names()
    df = con.execute("""SELECT department, any_value(region) AS region, any_value(region_code) AS region_code,
                               count(*) AS n_stations
                        FROM dim_station GROUP BY department ORDER BY department""").df()
    df["department_name"] = df["department"].map(names)
    df["map_location"] = df["department_name"] + ", France"
    df = df[["department", "department_name", "region", "region_code", "map_location", "n_stations"]]
    df.to_csv(path("gold") / "dim_department.csv", index=False)
    return df


def export_events() -> pd.DataFrame:
    ev = events()
    rows = [{"event_type": "remise_etat", "label": p["label"], "start_date": str(p["start"]), "end_date": str(p["end"]),
             "amount_ht": p["amount_ht"], "amount_ttc": p["amount_ttc_mainland"]} for p in ev["discount_periods"]]
    rows += [{"event_type": "remise_totalenergies", "label": f"TotalEnergies -{int(100 * r['amount_ttc'])} cts",
              "start_date": str(r["start"]), "end_date": str(r["end"]), "amount_ht": None, "amount_ttc": r["amount_ttc"]}
             for r in ev["company_rebates"]]
    rows += [{"event_type": "annotation", "label": a["label"], "start_date": str(a["date"]),
              "end_date": str(a.get("end", a["date"])), "amount_ht": None, "amount_ttc": None} for a in ev["annotations"]]
    df = pd.DataFrame(rows)
    df.insert(0, "event_key", range(1, len(df) + 1))
    df.to_csv(path("gold") / "dim_event.csv", index=False)
    return df


def export_rockets_feathers() -> pd.DataFrame:
    rf = pd.read_csv(tables_dir() / "rf_cumulative_response.csv")
    rf["sample"] = rf["fuel_code"].str.contains("hors_2022").map({True: "2019-2025 hors 2022", False: "2019-2025"})
    rf["fuel_code"] = rf["fuel_code"].str.replace("_hors_2022", "", regex=False)
    keys = {"Gazole": 1, "E10": 5}
    rf.insert(0, "fuel_key", rf["fuel_code"].map(keys))
    rf = rf.round(4)
    rf.to_csv(path("gold") / "fact_rockets_feathers.csv", index=False)
    return rf


README = """# Gold layer (Power BI inputs)

Generated by `python run.py export` from `data/plein_tarif.duckdb`. CSV, UTF-8, comma separator,
'.' decimal separator, ISO dates. Keys: `date_key` (yyyymmdd), `fuel_key`, `station_key`.

| File | Grain | Rows |
|---|---|---|
{rows}

Prices are in EUR per litre; `*_cts` columns in cents per litre. `price_ht_*` is the distributor price before
excise, VAT and the 2022 state discount (see docs/methodology.md). `n_*` columns allow Power BI measures to apply
the minimum-sample rule ({min_stations} stations, {min_days} days).
"""

GRAINS = {
    "dim_date": "one row per day", "dim_fuel": "one row per fuel", "dim_station": "one row per station",
    "dim_department": "one row per department",
    "dim_market": "day x fuel", "dim_event": "one row per event", "fact_national_daily": "day x fuel",
    "fact_department_month": "month x department x fuel", "fact_brand_category_month": "month x brand category x road type x fuel",
    "fact_station_year": "year x station x fuel", "fact_rockets_feathers": "sample x fuel x direction x horizon (weeks)",
}


def main() -> None:
    con = connect_db()
    counts = {name: export_table(con, name, sql) for name, sql in QUERIES.items()}
    counts["dim_department"] = len(export_departments(con))
    con.close()
    counts["dim_event"] = len(export_events())
    counts["fact_rockets_feathers"] = len(export_rockets_feathers())
    a = cfg()["analysis"]
    rows = "\n".join(f"| `{n}.csv` | {GRAINS[n]} | {c:,} |" for n, c in counts.items())
    (path("gold") / "README.md").write_text(README.format(rows=rows, min_stations=a["min_stations"],
                                                          min_days=a["min_days"]), encoding="utf-8")
    total = sum(f.stat().st_size for f in path("gold").glob("*.csv")) / 1e6
    log.info("gold layer: %.1f MB in total", total)


if __name__ == "__main__":
    main()
