"""From price updates to a daily price panel, plus the table of actual price changes.

Stations only send a record when they update a price (some also re-confirm an unchanged price).
The daily price of a station and fuel on day d is the last price recorded on or before the end of day d,
carried forward for at most `max_staleness_days` days. Beyond that the station is considered inactive
for that fuel and the day is missing (no row). The panel is built in DuckDB with an ASOF JOIN, which keeps
memory low even for ~100 million station-fuel-days.

Outputs (data/processed/):
- panel/year=YYYY/*.parquet : station_id, fuel, date, price, last_update, staleness_days
- price_changes.parquet     : station_id, fuel, ts, old_price, new_price, change (records where the price moved)
"""

from __future__ import annotations

import json
import shutil

import duckdb
import pandas as pd

from plein_tarif.config import cfg, duck, get_logger, path

log = get_logger(__name__)


def build_panel(con: duckdb.DuckDBPyConnection, prices: str, start: str, end: str, max_staleness: int) -> None:
    """Create table `panel` from relation `prices` (station_id, fuel, ts, price)."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE day_last AS
        SELECT station_id, fuel, CAST(ts AS DATE) AS d, arg_max(price, ts) AS price
        FROM {prices}
        GROUP BY ALL""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE grid AS
        WITH bounds AS (
            SELECT station_id, fuel, min(d) AS d0,
                   least(max(d) + INTERVAL {max_staleness} DAY, DATE '{end}') AS d1
            FROM day_last GROUP BY ALL
        )
        SELECT station_id, fuel,
               CAST(unnest(generate_series(greatest(d0, DATE '{start}'), d1, INTERVAL 1 DAY)) AS DATE) AS date
        FROM bounds
        WHERE d1 >= DATE '{start}'""")
    con.execute(f"""
        CREATE OR REPLACE TABLE panel AS
        SELECT g.station_id, g.fuel, g.date, x.price, x.d AS last_update,
               CAST(g.date - x.d AS INTEGER) AS staleness_days
        FROM grid g ASOF JOIN day_last x
          ON g.station_id = x.station_id AND g.fuel = x.fuel AND g.date >= x.d
        WHERE g.date - x.d <= {max_staleness}
        ORDER BY g.date, g.station_id, g.fuel""")
    con.execute("DROP TABLE grid")


def build_changes(con: duckdb.DuckDBPyConnection, prices: str) -> None:
    """Create table `price_changes`: records whose price differs from the previous record."""
    con.execute(f"""
        CREATE OR REPLACE TABLE price_changes AS
        WITH ordered AS (
            SELECT station_id, fuel, ts, price AS new_price,
                   lag(price) OVER (PARTITION BY station_id, fuel ORDER BY ts) AS old_price,
                   lag(ts) OVER (PARTITION BY station_id, fuel ORDER BY ts) AS prev_ts
            FROM {prices}
        )
        SELECT station_id, fuel, ts, prev_ts, old_price, new_price,
               round(new_price - old_price, 4) AS change
        FROM ordered
        WHERE old_price IS NOT NULL AND new_price <> old_price
        ORDER BY station_id, fuel, ts""")


def coverage_table(con: duckdb.DuckDBPyConnection, prices: str, start: str, end: str, windows: list[int]) -> pd.DataFrame:
    """Stations with a record in the last N days, relative to the widest window, on the 1st of each quarter.

    Shows how many active stations a given forward-fill limit keeps in the panel.
    """
    widest = max(windows)
    rows = []
    for d in pd.date_range(start, end, freq="QS"):
        if d == pd.Timestamp(start):
            continue
        ds = d.strftime("%Y-%m-%d")
        cols = ", ".join(f"count(DISTINCT station_id) FILTER (WHERE ts >= DATE '{ds}' - INTERVAL {w} DAY) AS d{w}"
                         for w in windows)
        r = con.execute(f"""SELECT fuel, {cols} FROM {prices}
                            WHERE ts < DATE '{ds}' AND ts >= DATE '{ds}' - INTERVAL {widest} DAY
                              AND fuel IN ('Gazole', 'E10') GROUP BY fuel""").df()
        r.insert(0, "date", ds)
        rows.append(r)
    out = pd.concat(rows, ignore_index=True)
    for w in windows:
        out[f"share_{w}d"] = (out[f"d{w}"] / out[f"d{widest}"]).round(3)
    return out


def main() -> None:
    period, n = cfg()["period"], cfg()["panel"]["max_staleness_days"]
    start, end = f"{period['start_year']}-01-01", f"{period['end_year']}-12-31"
    out = path("processed")
    con = duck()
    prices = f"read_parquet('{(out / 'prices_clean.parquet').as_posix()}')"

    build_panel(con, prices, start, end, n)
    panel_dir = out / "panel"
    if panel_dir.exists():
        shutil.rmtree(panel_dir)
    con.execute(f"""COPY (SELECT *, year(date) AS year FROM panel)
                    TO '{panel_dir.as_posix()}' (FORMAT parquet, PARTITION_BY (year))""")
    build_changes(con, prices)
    con.execute(f"COPY price_changes TO '{(out / 'price_changes.parquet').as_posix()}' (FORMAT parquet)")

    stats = {
        "max_staleness_days": n,
        "panel_rows": con.execute("SELECT count(*) FROM panel").fetchone()[0],
        "panel_series": con.execute("SELECT count(*) FROM (SELECT DISTINCT station_id, fuel FROM panel)").fetchone()[0],
        "panel_stations": con.execute("SELECT count(DISTINCT station_id) FROM panel").fetchone()[0],
        "filled_rows_share": con.execute("SELECT avg((staleness_days > 0)::INT) FROM panel").fetchone()[0],
        "price_changes": con.execute("SELECT count(*) FROM price_changes").fetchone()[0],
        "rows_by_fuel": dict(con.execute("SELECT fuel, count(*) FROM panel GROUP BY fuel ORDER BY 2 DESC").fetchall()),
    }
    cov = coverage_table(con, prices, start, end, cfg()["panel"]["coverage_check_days"])
    cov.to_csv(out / "panel_coverage.csv", index=False)
    stats["coverage_share_min_mean"] = {c: [float(cov[c].min()), round(float(cov[c].mean()), 3)]
                                        for c in cov.columns if c.startswith("share_")}
    (out / "panel_report.json").write_text(json.dumps(stats, indent=2, default=str))
    log.info("panel: %s", stats)


if __name__ == "__main__":
    main()
