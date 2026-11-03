"""Cleaning of parsed price records and station metadata.

Rules (applied in this order; counts before/after are written to data/processed/cleaning_report.csv
and documented in docs/methodology.md):

Stations
  S1  department from postcode (Corsica 2A/2B), region, zone (mainland / corsica / overseas)
  S2  coordinates outside the bounding box of the station's zone -> set to null and flagged
  S3  one row per station: latest year's metadata kept; changes of the motorway flag tracked

Price records
  P1  out of scope: overseas departments or unknown department (prices regulated differently)
  P2  impossible values: price <= 0
  P3  outside hard bounds [hard_min_eur, hard_max_eur] (e.g. 9.999 typos, 0.004)
  P4  duplicates: same station, fuel and timestamp -> keep the first record of the file
  P5  robust outliers: |price - median| > k * max(1.4826 * MAD, floor), where median and MAD are computed
      over all records of the same day, fuel and road type (motorway vs other, so that the motorway
      premium is not mistaken for errors)
  P6  isolated spikes: a record that differs by more than `spike_jump_eur` from both the previous and the
      next record of the same station and fuel, while those two agree within `spike_return_eur`
      (e.g. 1.703 -> 0.71 -> 1.71: a typo corrected a few minutes later)
"""

from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd

from plein_tarif.config import cfg, duck, get_logger, path
from plein_tarif.geo import (DEPT_TO_REGION, department_from_postcode, in_bbox, standard_city,
                             zone_of)

log = get_logger(__name__)

OVERSEAS_BBOX_KEY = {"971": "guadeloupe", "972": "martinique", "973": "guyane", "974": "reunion",
                     "976": "mayotte"}


# --------------------------------------------------------------------------------------------------
# Stations
# --------------------------------------------------------------------------------------------------
def add_geography(st: pd.DataFrame) -> pd.DataFrame:
    """S1 + S2 on a station table with columns postcode, city, lat, lon."""
    st = st.copy()
    st["department"] = st["postcode"].map(department_from_postcode)
    st["zone"] = st["department"].map(zone_of)
    st["region_code"] = st["department"].map(lambda d: DEPT_TO_REGION.get(d, (None, None))[0])
    st["region"] = st["department"].map(lambda d: DEPT_TO_REGION.get(d, (None, None))[1])
    st["city_std"] = [standard_city(c, p) for c, p in zip(st["city"], st["postcode"])]

    bbox = cfg()["geography"]["bbox"]
    ok = pd.Series(False, index=st.index)
    for zone, key in (("mainland", "mainland"), ("corsica", "corsica")):
        m = st["zone"].eq(zone)
        ok |= m & in_bbox(st["lat"], st["lon"], bbox[key])
    for dept, key in OVERSEAS_BBOX_KEY.items():
        m = st["department"].eq(dept)
        ok |= m & in_bbox(st["lat"], st["lon"], bbox[key])
    st["coord_missing"] = st["lat"].isna() | st["lon"].isna()
    st["coord_out_of_bbox"] = ~ok & ~st["coord_missing"]
    st.loc[~ok, ["lat", "lon"]] = np.nan
    return st


def build_stations() -> tuple[pd.DataFrame, dict]:
    """Deduplicate yearly station files into one row per station (S3)."""
    yearly = pd.concat(
        [pd.read_parquet(p) for p in sorted(path("parsed").glob("stations_*.parquet"))], ignore_index=True
    )
    yearly = add_geography(yearly)
    yearly = yearly.sort_values(["station_id", "year"])

    hist = yearly.groupby("station_id").agg(
        first_year=("year", "min"),
        last_year=("year", "max"),
        n_years=("year", "nunique"),
        motorway_any=("is_motorway", "max"),
        motorway_flag_changes=("is_motorway", lambda s: int((s.astype(int).diff().fillna(0) != 0).sum())),
    )
    # Latest metadata, but keep the last valid coordinates if the latest year lost them.
    last_coords = (yearly.dropna(subset=["lat", "lon"]).groupby("station_id")[["lat", "lon"]].last())
    latest = yearly.groupby("station_id").tail(1).set_index("station_id")
    latest = latest.drop(columns=["lat", "lon"]).join(last_coords).join(hist).reset_index()
    cols = ["station_id", "lat", "lon", "address", "postcode", "city", "city_std", "department",
            "region_code", "region", "zone", "is_motorway", "pop", "services", "automate_24_24",
            "first_year", "last_year", "n_years", "motorway_any", "motorway_flag_changes",
            "coord_missing", "coord_out_of_bbox"]
    stations = latest[cols].copy()
    stations["coord_missing"] = stations["lat"].isna()
    report = {
        "station_year_rows": int(len(yearly)),
        "stations_unique": int(len(stations)),
        "stations_by_zone": stations["zone"].value_counts(dropna=False).to_dict(),
        "stations_unknown_department": int(stations["department"].isna().sum()),
        "station_year_rows_out_of_bbox": int(yearly["coord_out_of_bbox"].sum()),
        "station_year_rows_missing_coords": int(yearly["coord_missing"].sum()),
        "stations_without_valid_coords": int(stations["lat"].isna().sum()),
        "stations_motorway": int(stations["is_motorway"].sum()),
        "stations_motorway_flag_changed": int((stations["motorway_flag_changes"] > 0).sum()),
    }
    return stations, report


# --------------------------------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------------------------------
def clean_prices(con: duckdb.DuckDBPyConnection, prices_relation: str, stations: pd.DataFrame,
                 params: dict) -> list[dict]:
    """Apply rules P1-P5 inside DuckDB. Creates table `prices_clean`; returns per-rule counts."""
    con.register("stations_df", stations[["station_id", "zone", "is_motorway"]])
    steps: list[dict] = []

    def count(table: str) -> int:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE p0 AS
        SELECT row_number() OVER () AS rid, station_id, fuel::VARCHAR AS fuel, ts, price
        FROM {prices_relation}""")
    n0 = count("p0")
    steps.append({"rule": "P0 parsed records", "before": n0, "removed": 0, "after": n0})

    def step(rule: str, prev: str, new: str, sql: str) -> None:
        before = count(prev)
        con.execute(f"CREATE OR REPLACE TEMP TABLE {new} AS {sql}")
        after = count(new)
        steps.append({"rule": rule, "before": before, "removed": before - after, "after": after})
        log.info("%-55s %12s -> %12s (-%s)", rule, f"{before:,}", f"{after:,}", f"{before - after:,}")

    step("P1 out of scope (overseas / unknown department)", "p0", "p1", """
        SELECT p.*, s.is_motorway FROM p0 p JOIN stations_df s USING (station_id)
        WHERE s.zone IN ('mainland', 'corsica')""")
    step("P2 non-positive price", "p1", "p2", "SELECT * FROM p1 WHERE price > 0")
    step("P3 outside hard bounds", "p2", "p3",
         f"SELECT * FROM p2 WHERE price BETWEEN {params['hard_min_eur']} AND {params['hard_max_eur']}")
    step("P4 duplicate station/fuel/timestamp", "p3", "p4", """
        SELECT * FROM p3 QUALIFY row_number() OVER (PARTITION BY station_id, fuel, ts ORDER BY rid) = 1""")
    k, floor = params["mad_k"], params["robust_sd_floor_eur"]
    step(f"P5 more than {k} robust SD from daily median", "p4", "p5", f"""
        WITH stats AS (
            SELECT CAST(ts AS DATE) AS d, fuel, is_motorway, median(price) AS med
            FROM p4 GROUP BY ALL
        ), mad AS (
            SELECT CAST(p.ts AS DATE) AS d, p.fuel, p.is_motorway, s.med,
                   median(abs(p.price - s.med)) AS mad
            FROM p4 p JOIN stats s ON s.d = CAST(p.ts AS DATE) AND s.fuel = p.fuel
                                   AND s.is_motorway = p.is_motorway
            GROUP BY ALL
        )
        SELECT p.* FROM p4 p
        JOIN mad m ON m.d = CAST(p.ts AS DATE) AND m.fuel = p.fuel AND m.is_motorway = p.is_motorway
        WHERE abs(p.price - m.med) <= {k} * greatest(1.4826 * m.mad, {floor})""")
    jump, back = params["spike_jump_eur"], params["spike_return_eur"]
    step("P6 isolated spike (typo corrected right away)", "p5", "p6", f"""
        WITH w AS (
            SELECT *, lag(price) OVER s AS prev_p, lead(price) OVER s AS next_p
            FROM p5 WINDOW s AS (PARTITION BY station_id, fuel ORDER BY ts, rid)
        )
        SELECT * EXCLUDE (prev_p, next_p) FROM w
        WHERE NOT (abs(price - prev_p) > {jump} AND abs(price - next_p) > {jump}
                   AND abs(prev_p - next_p) <= {back})
           OR prev_p IS NULL OR next_p IS NULL""")
    con.execute("""
        CREATE OR REPLACE TABLE prices_clean AS
        SELECT station_id, fuel, ts, price FROM p6 ORDER BY station_id, fuel, ts""")
    return steps


def main() -> None:
    params = cfg()["cleaning"]
    out = path("processed")
    stations, st_report = build_stations()
    stations.to_parquet(out / "stations.parquet", index=False)
    log.info("stations: %s", st_report)

    con = duck()
    con.execute("SET preserve_insertion_order = true")
    prices = f"read_parquet('{(path('parsed') / 'prices_*.parquet').as_posix()}')"
    steps = clean_prices(con, prices, stations, params)
    con.execute(f"COPY prices_clean TO '{(out / 'prices_clean.parquet').as_posix()}' (FORMAT parquet)")

    by_rule_fuel = con.sql("""
        SELECT fuel, count(*) AS n FROM prices_clean GROUP BY fuel ORDER BY n DESC""").df()
    pd.DataFrame(steps).to_csv(out / "cleaning_report.csv", index=False)
    (out / "cleaning_report.json").write_text(json.dumps(
        {"stations": st_report, "prices": steps, "clean_records_by_fuel": by_rule_fuel.to_dict("records")},
        indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
