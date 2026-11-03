"""Build the DuckDB star schema (data/plein_tarif.duckdb) from the processed Parquet files.

Steps: run sql/01_schema.sql (DDL), load dimensions, compute neighbour pairs with a haversine BallTree,
load the facts (pre-tax price computed in SQL from ref_tax), then create the views of sql/02_views.sql.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from plein_tarif.config import ROOT, cfg, duck, events, get_logger, path
from plein_tarif.enrich_brands import CATEGORY_LABELS
from plein_tarif.geo import french_holidays
from plein_tarif.market import tax_zone_of
from plein_tarif.parse_xml import FUELS

log = get_logger(__name__)
EARTH_KM = 6371.0088
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
             "octobre", "novembre", "décembre"]
WEEKDAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def set_sample_rule(con) -> None:
    """Session variables used by the views and analysis queries (minimum-sample rule and parameters)."""
    a, g = cfg()["analysis"], cfg()["geography"]
    con.execute(f"SET VARIABLE min_stations = {int(a['min_stations'])}")
    con.execute(f"SET VARIABLE min_days = {int(a['min_days'])}")
    con.execute(f"SET VARIABLE analysis_year = {int(cfg()['period']['end_year'])}")
    con.execute(f"SET VARIABLE focus_city = '{g['focus_city'].upper()}'")
    con.execute(f"SET VARIABLE litres_per_year = {a['km_per_year'] * a['litres_per_100km'] / 100.0}")
    con.execute(f"SET VARIABLE event_window_days = {int(a['event_window_days'])}")
    cities = ", ".join(f"'{c.upper()}'" for c in g["big_cities"])
    con.execute(f"SET VARIABLE big_cities = [{cities}]")


def connect_db(read_only: bool = True):
    """Connection to the project database with the minimum-sample variables set."""
    con = duck(str(path("duckdb")), read_only=read_only)
    set_sample_rule(con)
    return con


def run_sql_file(con, name: str) -> None:
    """Execute every statement of a file in sql/."""
    con.execute((ROOT / "sql" / name).read_text(encoding="utf-8"))


def build_dim_fuel() -> pd.DataFrame:
    labels, main = cfg()["fuels"]["labels"], cfg()["fuels"]["main"]
    order = main + cfg()["fuels"]["secondary"]
    return pd.DataFrame([
        {"fuel_key": fid, "fuel_code": code, "fuel_label": labels[code], "is_main_fuel": code in main,
         "display_order": order.index(code) + 1}
        for code, fid in FUELS.items()
    ]).sort_values("display_order")


def build_dim_date(start: str, end: str) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    holidays: dict = {}
    for y in range(days[0].year, days[-1].year + 2):
        holidays.update(french_holidays(y))
    ev = events()
    df = pd.DataFrame({"date": days.date})
    ts = pd.to_datetime(df["date"])
    df["date_key"] = ts.dt.strftime("%Y%m%d").astype(int)
    df["year"], df["quarter"], df["month"] = ts.dt.year, ts.dt.quarter, ts.dt.month
    df["month_name"] = ts.dt.month.map(lambda m: MONTHS_FR[m - 1])
    df["year_month"] = ts.dt.strftime("%Y-%m")
    df["iso_week"] = ts.dt.isocalendar().week.astype(int)
    df["weekday_num"] = ts.dt.weekday + 1
    df["weekday_name"] = ts.dt.weekday.map(lambda d: WEEKDAYS_FR[d])
    df["is_weekend"] = df["weekday_num"] >= 6
    df["is_holiday"] = df["date"].map(lambda d: d in holidays)
    df["holiday_name"] = df["date"].map(holidays.get)
    nxt = df["date"].map(lambda d: d + timedelta(days=1))
    df["is_day_before_holiday_or_weekend"] = (df["weekday_num"] == 5) | nxt.map(lambda d: d in holidays)
    df["discount_ht"], df["discount_label"] = 0.0, None
    for p in ev["discount_periods"]:
        m = (ts >= pd.Timestamp(p["start"])) & (ts <= pd.Timestamp(p["end"]))
        df.loc[m, "discount_ht"], df.loc[m, "discount_label"] = p["amount_ht"], p["label"]
    df["event_label"] = None
    for a in ev["annotations"]:
        end_ = pd.Timestamp(a.get("end", a["date"]))
        m = (ts >= pd.Timestamp(a["date"])) & (ts <= end_) & df["event_label"].isna()
        df.loc[m, "event_label"] = a["label"]
    cols = ["date_key", "date", "year", "quarter", "month", "month_name", "year_month", "iso_week",
            "weekday_num", "weekday_name", "is_weekend", "is_holiday", "holiday_name",
            "is_day_before_holiday_or_weekend", "discount_ht", "discount_label", "event_label"]
    return df[cols]


def build_dim_station() -> pd.DataFrame:
    proc = path("processed")
    st = pd.read_parquet(proc / "stations.parquet")
    with_prices = pd.read_parquet(proc / "prices_clean.parquet", columns=["station_id"])["station_id"].unique()
    st = st[st["station_id"].isin(with_prices)].sort_values("station_id").reset_index(drop=True)
    st["station_key"] = np.arange(1, len(st) + 1, dtype="int32")
    st["tax_zone"] = st["department"].map(tax_zone_of)
    st["road_type"] = np.where(st["is_motorway"], "Autoroute", "Route")
    brands_file = proc / "station_brands.parquet"
    if brands_file.exists():
        b = pd.read_parquet(brands_file)[["station_id", "brand", "brand_category", "match_distance_m", "ambiguous"]]
        st = st.merge(b, on="station_id", how="left")
        st["brand"] = st["brand"].fillna("Non identifiée")
        st["brand_category"] = st["brand_category"].fillna("unmatched")
    else:
        log.warning("no brand table: brand columns left empty")
        st["brand"], st["brand_category"], st["match_distance_m"], st["ambiguous"] = None, None, np.nan, None
    st["brand_category_label"] = st["brand_category"].map(CATEGORY_LABELS)
    st = st.rename(columns={"match_distance_m": "osm_match_distance_m", "ambiguous": "brand_ambiguous"})
    cols = ["station_key", "station_id", "lat", "lon", "address", "postcode", "city", "city_std", "department",
            "region_code", "region", "zone", "tax_zone", "is_motorway", "road_type", "services",
            "automate_24_24", "brand", "brand_category", "brand_category_label", "osm_match_distance_m",
            "brand_ambiguous", "first_year", "last_year", "motorway_flag_changes"]
    return st[cols]


def neighbour_pairs(st: pd.DataFrame, motorway_km: float, nearby_km: float) -> pd.DataFrame:
    """Pairs of stations within a radius (haversine BallTree)."""
    s = st.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    xy = np.radians(s[["lat", "lon"]].to_numpy())
    tree = BallTree(xy, metric="haversine")
    out = []

    def pairs(src_mask, radius_km, target_mask, relation):
        src = s[src_mask]
        idx, dist = tree.query_radius(xy[src_mask.to_numpy()], r=radius_km / EARTH_KM, return_distance=True)
        for key, nbrs, ds in zip(src["station_key"], idx, dist):
            keep = target_mask.to_numpy()[nbrs] & (s["station_key"].to_numpy()[nbrs] != key)
            for j, d in zip(nbrs[keep], ds[keep]):
                out.append((key, int(s["station_key"].iat[j]), relation, round(float(d) * EARTH_KM, 3)))

    pairs(s["is_motorway"], motorway_km, ~s["is_motorway"], "motorway_vs_road")
    pairs(pd.Series(True, index=s.index), nearby_km, pd.Series(True, index=s.index), "nearby")
    return pd.DataFrame(out, columns=["station_key", "neighbor_key", "relation", "distance_km"])


def main() -> None:
    period = cfg()["period"]
    start, end = f"{period['start_year']}-01-01", f"{period['end_year']}-12-31"
    proc = path("processed")
    db = path("duckdb")
    if db.exists():
        db.unlink()
    con = duck(str(db))
    con.execute("SET preserve_insertion_order = false")
    set_sample_rule(con)
    run_sql_file(con, "01_schema.sql")

    dim_fuel, dim_date, dim_station = build_dim_fuel(), build_dim_date(start, end), build_dim_station()
    for name, df in (("dim_fuel", dim_fuel), ("dim_date", dim_date), ("dim_station", dim_station)):
        con.register("df_tmp", df)
        con.execute(f"INSERT INTO {name} BY NAME SELECT * FROM df_tmp")
        con.unregister("df_tmp")

    a = cfg()["analysis"]
    pairs = neighbour_pairs(dim_station, a["motorway_neighbour_km"], a["savings_radius_km"])
    con.register("df_tmp", pairs)
    con.execute("INSERT INTO bridge_station_neighbor SELECT * FROM df_tmp")
    con.unregister("df_tmp")

    tax = f"read_parquet('{(proc / 'tax_daily.parquet').as_posix()}')"
    market = f"read_parquet('{(proc / 'market_daily.parquet').as_posix()}')"
    con.execute(f"""
        INSERT INTO ref_tax
        SELECT CAST(strftime(t.date, '%Y%m%d') AS INTEGER), f.fuel_key, t.tax_zone, t.vat, t.excise, t.discount_ht
        FROM {tax} t JOIN dim_fuel f ON f.fuel_code = t.fuel""")
    con.execute(f"""
        INSERT INTO dim_market
        SELECT CAST(strftime(m.date, '%Y%m%d') AS INTEGER), f.fuel_key, m.brent_usd_bbl, m.eurusd,
               m.brent_eur_bbl, m.brent_eur_l, m.is_trading_day, m.vat, m.excise, m.discount_ht, m.discount_ttc
        FROM {market} m JOIN dim_fuel f ON f.fuel_code = m.fuel""")

    panel = f"read_parquet('{(proc / 'panel' / '**' / '*.parquet').as_posix()}', hive_partitioning = true)"
    con.execute(f"""
        INSERT INTO fact_price_daily
        SELECT CAST(strftime(p.date, '%Y%m%d') AS INTEGER) AS date_key, s.station_key, f.fuel_key,
               p.price AS price_ttc,
               round(p.price / (1 + t.vat) - t.excise + t.discount_ht, 5) AS price_ht,
               p.staleness_days
        FROM {panel} p
        JOIN dim_station s ON s.station_id = p.station_id
        JOIN dim_fuel f ON f.fuel_code = p.fuel
        JOIN ref_tax t ON t.date_key = CAST(strftime(p.date, '%Y%m%d') AS INTEGER)
                      AND t.fuel_key = f.fuel_key AND t.tax_zone = s.tax_zone""")
    changes = f"read_parquet('{(proc / 'price_changes.parquet').as_posix()}')"
    con.execute(f"""
        INSERT INTO fact_price_change
        SELECT s.station_key, f.fuel_key, CAST(strftime(c.ts, '%Y%m%d') AS INTEGER), c.ts,
               c.old_price, c.new_price, c.change,
               round(epoch(c.ts - c.prev_ts) / 3600.0, 2)
        FROM {changes} c
        JOIN dim_station s ON s.station_id = c.station_id
        JOIN dim_fuel f ON f.fuel_code = c.fuel
        WHERE c.ts >= DATE '{start}' AND c.ts < DATE '{end}' + INTERVAL 1 DAY""")
    run_sql_file(con, "02_views.sql")

    for t in ("dim_fuel", "dim_date", "dim_station", "dim_market", "ref_tax", "bridge_station_neighbor",
              "fact_price_daily", "fact_price_change"):
        log.info("%-25s %12s rows", t, f"{con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:,}")
    con.close()


if __name__ == "__main__":
    main()
