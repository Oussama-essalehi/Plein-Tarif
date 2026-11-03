"""Cleaning rules: geography, coordinates and the price outlier rules P2-P6."""

import numpy as np
import pandas as pd
import pytest

from plein_tarif.clean import add_geography, clean_prices
from plein_tarif.config import duck
from plein_tarif.geo import department_from_postcode, french_holidays, standard_city

PARAMS = {"hard_min_eur": 0.30, "hard_max_eur": 4.0, "mad_k": 8.0, "robust_sd_floor_eur": 0.03,
          "spike_jump_eur": 0.15, "spike_return_eur": 0.05}


@pytest.mark.parametrize("cp, dept", [
    ("01000", "01"), ("1000", "01"), ("75015", "75"), ("20000", "2A"), ("20167", "2A"),
    ("20200", "2B"), ("20600", "2B"), ("97400", "974"), ("98000", None), ("", None), (None, None),
])
def test_department_from_postcode(cp, dept):
    assert department_from_postcode(cp) == dept


def test_standard_city():
    assert standard_city("Paris 15e", "75015") == "PARIS"
    assert standard_city("MARSEILLE 8E", "13008") == "MARSEILLE"
    assert standard_city("St-Étienne", "42000") == "SAINT ETIENNE"
    assert standard_city("TOULOUSE CEDEX 1", "31100") == "TOULOUSE"


def test_holidays_2022():
    h = french_holidays(2022)
    assert len(h) == 11
    assert pd.Timestamp("2022-04-18").date() in h       # Easter Monday 2022
    assert pd.Timestamp("2022-06-06").date() in h       # Whit Monday 2022


def test_coordinates_outside_bbox_are_nulled():
    st = pd.DataFrame({
        "postcode": ["31000", "20000", "31000", "75001"],
        "city": ["Toulouse", "Ajaccio", "Toulouse", "Paris"],
        "lat": [43.60, 41.92, 4.36, np.nan],          # third one: missing a digit
        "lon": [1.44, 8.73, 1.44, np.nan],
    })
    out = add_geography(st)
    assert out["zone"].tolist() == ["mainland", "corsica", "mainland", "mainland"]
    assert out["coord_out_of_bbox"].tolist() == [False, False, True, False]
    assert out["coord_missing"].tolist() == [False, False, False, True]
    assert out["lat"].isna().tolist() == [False, False, True, True]
    assert out.loc[1, "region"] == "Corse"


def _prices():
    rows = []
    # 30 regular stations on the same day, prices around 1.80 +/- 0.05
    for i in range(30):
        rows.append((i, "Gazole", "2022-03-01 08:00:00", 1.75 + 0.1 * (i % 10) / 9))
    rows += [
        (100, "Gazole", "2022-03-01 09:00:00", -1.0),      # P2
        (101, "Gazole", "2022-03-01 09:00:00", 9.999),     # P3
        (102, "Gazole", "2022-03-01 09:00:00", 1.80),      # P4 duplicate pair...
        (102, "Gazole", "2022-03-01 09:00:00", 1.81),      # ...second one removed
        (103, "Gazole", "2022-03-01 09:00:00", 0.71),      # P5 (far from median)
        (104, "Gazole", "2022-03-01 07:00:00", 1.70),      # P6 spike: 1.70 -> 1.95 -> 1.71
        (104, "Gazole", "2022-03-01 08:00:00", 1.95),
        (104, "Gazole", "2022-03-01 09:00:00", 1.71),
        (105, "Gazole", "2022-03-01 09:00:00", 2.05),      # expensive but plausible: kept
        (900, "Gazole", "2022-03-01 09:00:00", 1.50),      # overseas station: P1
    ]
    df = pd.DataFrame(rows, columns=["station_id", "fuel", "ts", "price"])
    df["ts"] = pd.to_datetime(df["ts"])
    stations = pd.DataFrame({"station_id": sorted(df["station_id"].unique())})
    stations["zone"] = np.where(stations["station_id"] == 900, "overseas", "mainland")
    stations["is_motorway"] = False
    return df, stations


def test_price_rules_counts():
    df, stations = _prices()
    con = duck()
    con.register("raw_prices", df)
    steps = {s["rule"].split()[0]: s["removed"] for s in clean_prices(con, "raw_prices", stations, PARAMS)}
    assert steps == {"P0": 0, "P1": 1, "P2": 1, "P3": 1, "P4": 1, "P5": 1, "P6": 1}
    kept = con.execute("SELECT station_id, price FROM prices_clean WHERE station_id >= 100 ORDER BY ALL").fetchall()
    assert kept == [(102, 1.80), (104, 1.70), (104, 1.71), (105, 2.05)]
