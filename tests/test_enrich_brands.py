"""Brand normalisation, spatial matching (known pairs) and OSM file reading."""

from pathlib import Path

import pandas as pd
import pytest

from plein_tarif.enrich_brands import match_stations, normalize_brand, read_pbf

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("brand, name, expected", [
    ("TotalEnergies", None, ("TotalEnergies", "oil_company")),
    ("Total Access", None, ("TotalEnergies", "oil_company")),
    ("Elf", None, ("TotalEnergies", "oil_company")),
    ("E.Leclerc", None, ("E.Leclerc", "supermarket")),
    (None, "Station Leclerc", ("E.Leclerc", "supermarket")),
    ("Intermarché", None, ("Intermarché", "supermarket")),
    ("Super U", None, ("Système U", "supermarket")),
    ("Hyper U", None, ("Système U", "supermarket")),
    ("U", None, ("Système U", "supermarket")),
    ("Carrefour Market", None, ("Carrefour", "supermarket")),
    ("Géant Casino", None, ("Casino", "supermarket")),
    ("Esso Express", None, ("Esso", "oil_company")),
    ("BP", None, ("BP", "oil_company")),
    ("Avia", None, ("Avia", "oil_company")),
    ("Garage Dupont", None, ("Garage Dupont", "independent_other")),
    (None, None, ("Indépendant / sans enseigne", "independent_other")),
])
def test_normalize_brand(brand, name, expected):
    assert normalize_brand(brand, name) == expected


def test_match_known_pairs_and_radius():
    osm = pd.DataFrame({
        "osm_type": ["node", "node", "node"], "osm_id": [1, 2, 3],
        "lat": [43.60000, 43.61000, 43.70000], "lon": [1.44000, 1.45000, 1.50000],
        "brand": ["TotalEnergies", "E.Leclerc", "Esso"], "name": [None] * 3, "operator": [None] * 3,
    })
    stations = pd.DataFrame({
        "station_id": [10, 20, 30],
        "lat": [43.60030, 43.61000, 43.65000],   # ~33 m from Total ; exact Leclerc ; >3 km from anything
        "lon": [1.44000, 1.45000, 1.50000],
    })
    res = match_stations(stations, osm, radius_m=75).set_index("station_id")
    assert res.loc[10, "brand"] == "TotalEnergies" and res.loc[10, "match_distance_m"] == pytest.approx(33.4, abs=0.5)
    assert res.loc[20, "brand_category"] == "supermarket"
    assert not res.loc[30, "matched"] and res.loc[30, "brand_category"] == "unmatched"


def test_ambiguous_match_is_flagged():
    osm = pd.DataFrame({
        "osm_type": ["node", "node"], "osm_id": [1, 2], "lat": [43.6000, 43.6003], "lon": [1.4400, 1.4400],
        "brand": ["Shell", "Avia"], "name": [None, None], "operator": [None, None],
    })
    stations = pd.DataFrame({"station_id": [1], "lat": [43.60014], "lon": [1.4400]})
    res = match_stations(stations, osm, radius_m=75)
    assert bool(res.loc[0, "ambiguous"])


def test_read_osm_file_nodes_and_way_centroids():
    df = read_pbf(FIX / "osm_fuel_sample.osm")    # pyosmium reads .osm XML with the same code path
    assert sorted(df["osm_id"].tolist()) == [1, 2, 100]
    way = df[df["osm_type"] == "way"].iloc[0]
    assert way["lat"] == pytest.approx((43.63 + 43.6301 + 43.6301) / 3, abs=1e-6) and way["brand"] == "Esso Express"
