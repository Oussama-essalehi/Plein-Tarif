"""Parsing of each yearly format variant (fixtures in tests/fixtures/)."""

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from plein_tarif.parse_xml import (declared_encoding, detect_coord_scale, detect_price_unit,
                                   parse_file)

FIX = Path(__file__).parent / "fixtures"


def test_thousandths_format_is_detected_and_normalised():
    res = parse_file(FIX / "prix_2019_thousandths.xml")
    rep, prices = res["report"], res["prices"]
    assert rep["price_unit"] == "thousandth"
    assert rep["encoding"] == "ISO-8859-1"
    assert rep["empty_price_elements"] == 1
    assert prices["price"].tolist() == pytest.approx([1.328, 1.348, 1.399, 1.489])
    assert prices["fuel"].tolist() == ["Gazole", "Gazole", "E10", "Gazole"]
    assert prices["ts"].iloc[0] == pd.Timestamp("2019-01-04 10:53:48")


def test_station_metadata_and_coordinates():
    st = parse_file(FIX / "prix_2019_thousandths.xml")["stations"].set_index("station_id")
    assert st.loc[1000001, "city"] == "SAINT-DENIS-LÈS-BOURG"       # ISO-8859-1 decoded
    assert st.loc[1000001, "lat"] == pytest.approx(46.20114)
    assert st.loc[31000002, "lat"] == pytest.approx(43.6009359235)  # decimal raw coordinates
    assert bool(st.loc[31000002, "is_motorway"]) and not bool(st.loc[1000001, "is_motorway"])
    assert st.loc[1000001, "services"].split("|")[0] == "Station de gonflage"
    assert bool(st.loc[1000001, "automate_24_24"])


def test_euro_format_keeps_integer_values_as_euros():
    res = parse_file(FIX / "prix_2022_euros.xml")
    assert res["report"]["price_unit"] == "euro"
    assert res["report"]["integer_values_in_euro_file"] == 1
    sp98 = res["prices"].query("fuel == 'SP98'")["price"].tolist()
    assert sp98 == pytest.approx([2.0, 9.999])   # outliers are removed later, in clean.py


def test_stockout_type_and_missing_coordinates_2024():
    res = parse_file(FIX / "prix_2024_rupture_types.xml")
    assert res["report"]["stations_without_coordinates"] == 1
    assert np.isnan(res["stations"]["lat"].iloc[0])
    assert res["stockouts"]["type"].tolist() == ["temporaire", ""]
    assert res["stockouts"]["end"].isna().tolist() == [False, True]


def test_zip_input(tmp_path):
    z = tmp_path / "PrixCarburants_annuel_2019.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(FIX / "prix_2019_thousandths.xml", "PrixCarburants_annuel_2019.xml")
    assert declared_encoding(z) == "ISO-8859-1"
    assert len(parse_file(z)["prices"]) == 4


def test_unit_detection_rules():
    assert detect_price_unit(["1328", "1401", "1.5"]) == "thousandth"
    assert detect_price_unit(["1.328", "2", "1.401"]) == "euro"
    with pytest.raises(ValueError):
        detect_price_unit(["", ""], min_records=1)


def test_coordinate_scale_detection():
    assert detect_coord_scale(np.array([4620114.0, 4360093.5, np.nan])) == 100_000
    assert detect_coord_scale(np.array([46.2, 43.6])) == 1
