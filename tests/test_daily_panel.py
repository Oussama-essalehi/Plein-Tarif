"""Daily panel: carry-forward with a maximum staleness, and price change extraction."""

import pandas as pd

from plein_tarif.config import duck
from plein_tarif.daily_panel import build_changes, build_panel


def _con(rows):
    df = pd.DataFrame(rows, columns=["station_id", "fuel", "ts", "price"])
    df["ts"] = pd.to_datetime(df["ts"])
    con = duck()
    con.register("prices", df)
    return con


def test_forward_fill_stops_after_max_staleness():
    con = _con([
        (1, "Gazole", "2022-01-01 08:00", 1.50),
        (1, "Gazole", "2022-01-01 18:00", 1.52),   # last price of the day wins
        (1, "Gazole", "2022-01-03 09:00", 1.55),
        (1, "Gazole", "2022-01-20 09:00", 1.60),   # after a 17-day gap
    ])
    build_panel(con, "prices", "2022-01-01", "2022-01-31", max_staleness=7)
    p = con.execute("SELECT date, price, staleness_days FROM panel ORDER BY date").df()
    p["date"] = pd.to_datetime(p["date"])
    by_day = p.set_index("date")["price"]
    assert by_day[pd.Timestamp("2022-01-01")] == 1.52
    assert by_day[pd.Timestamp("2022-01-02")] == 1.52
    assert by_day[pd.Timestamp("2022-01-10")] == 1.55          # 7 days after 3 Jan: still filled
    assert pd.Timestamp("2022-01-11") not in by_day.index       # 8 days: missing
    assert pd.Timestamp("2022-01-19") not in by_day.index
    assert by_day[pd.Timestamp("2022-01-27")] == 1.60
    assert p["date"].max() == pd.Timestamp("2022-01-27")         # filling ends 7 days after the last record
    assert p["staleness_days"].max() == 7


def test_panel_respects_period_bounds():
    con = _con([(1, "E10", "2021-12-30 08:00", 1.60), (1, "E10", "2022-01-02 08:00", 1.62)])
    build_panel(con, "prices", "2022-01-01", "2022-01-05", max_staleness=7)
    p = con.execute("SELECT date, price FROM panel ORDER BY date").fetchall()
    assert [str(d) for d, _ in p] == ["2022-01-01", "2022-01-02", "2022-01-03", "2022-01-04", "2022-01-05"]
    assert p[0][1] == 1.60   # value carried over from the previous year


def test_changes_skip_unchanged_confirmations():
    con = _con([
        (1, "Gazole", "2022-01-01 08:00", 1.50),
        (1, "Gazole", "2022-01-02 08:00", 1.50),   # confirmation, not a change
        (1, "Gazole", "2022-01-03 08:00", 1.53),
        (1, "Gazole", "2022-01-04 08:00", 1.49),
    ])
    build_changes(con, "prices")
    ch = con.execute("SELECT old_price, new_price, change FROM price_changes ORDER BY ts").fetchall()
    assert ch == [(1.50, 1.53, 0.03), (1.53, 1.49, -0.04)]
