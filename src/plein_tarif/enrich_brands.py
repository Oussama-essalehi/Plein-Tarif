"""Brand enrichment from OpenStreetMap (ODbL).

The official files carry no brand. Each official station is matched to the nearest OpenStreetMap
fuel station (`amenity=fuel`, nodes and building/area centroids) within `brands.match_radius_m`
using a haversine BallTree. Brand strings are normalised (spelling variants -> one brand) and grouped
into categories: supermarket, oil company, independent/other.

OSM input, two interchangeable sources (config `brands.osm_source`):
- `overpass`: one Overpass API query for France (default);
- `pbf`: a Geofabrik-style .osm.pbf extract of France read with pyosmium (for networks where the
  Overpass API is not reachable).
Both produce data/raw/osm/osm_fuel_fr.parquet with one row per OSM fuel station. The environment variable
PLEIN_TARIF_OSM_SOURCE=pbf overrides the config. After extraction the .pbf can be deleted.

Outputs: data/processed/station_brands.parquet (separate table, ODbL), brand_match_report.json,
brand_match_by_department.csv.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata

import numpy as np
import pandas as pd
import requests
from sklearn.neighbors import BallTree

from plein_tarif.config import cfg, get_logger, path

log = get_logger(__name__)
EARTH_RADIUS_M = 6_371_008.8

OVERPASS_QUERY = """
[out:json][timeout:600];
area["ISO3166-1"="FR"][admin_level=2]->.fr;
( node["amenity"="fuel"](area.fr); way["amenity"="fuel"](area.fr); relation["amenity"="fuel"](area.fr); );
out center tags;
"""

# Canonical brand -> (category, regex on the normalised brand/name/operator text). Order matters.
BRAND_RULES: list[tuple[str, str, str]] = [
    ("E.Leclerc", "supermarket", r"\bleclerc\b"),
    ("Carrefour", "supermarket", r"\bcarrefour\b"),
    ("Intermarché", "supermarket", r"\bintermarche\b|\bmousquetaires\b"),
    ("Netto", "supermarket", r"\bnetto\b"),
    ("Système U", "supermarket", r"\bsysteme u\b|\bsuper u\b|\bhyper u\b|\bu express\b|\bmarche u\b|\bstation u\b|^u$|\bles magasins u\b"),
    ("Auchan", "supermarket", r"\bauchan\b"),
    ("Casino", "supermarket", r"\bcasino\b|\bgeant\b"),
    ("Cora", "supermarket", r"\bcora\b"),
    ("Match", "supermarket", r"\bsupermarche match\b|^match$"),
    ("Roady", "supermarket", r"\broady\b"),
    ("TotalEnergies", "oil_company", r"\btotal\s*energies\b|\btotal\b|\btotalenergies\b|\belf\b|\belan\b"),
    ("Esso", "oil_company", r"\besso\b"),
    ("BP", "oil_company", r"^bp\b|\bbp\b"),
    ("Shell", "oil_company", r"\bshell\b"),
    ("Avia", "oil_company", r"\bavia\b"),
    ("Agip / Eni", "oil_company", r"\bagip\b|\beni\b"),
    ("Dyneff", "oil_company", r"\bdyneff\b"),
    ("Q8", "oil_company", r"\bq8\b"),
    ("AS24", "independent_other", r"\bas ?24\b"),
    ("Indépendant / sans enseigne", "independent_other", r"^independa?n[td]e?s?$|^sans enseigne$|^station service$"),
]
CATEGORY_LABELS = {"supermarket": "Grande distribution", "oil_company": "Pétrolier",
                   "independent_other": "Indépendant / autre", "unmatched": "Non identifiée"}


def _norm(text: str | None) -> str:
    if not text or not isinstance(text, str):
        return ""
    t = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t.replace(".", " "))
    return re.sub(r"\s+", " ", t).strip()


def normalize_brand(brand: str | None, name: str | None = None, operator: str | None = None) -> tuple[str, str]:
    """(canonical brand, category) from OSM tags; `brand` takes precedence over `name` and `operator`."""
    for text in (brand, name, operator):
        t = _norm(text)
        if not t:
            continue
        for canonical, category, pattern in BRAND_RULES:
            if re.search(pattern, t):
                return canonical, category
        if text is brand:   # a brand tag that is not a known network: independent/other brand
            return brand.strip(), "independent_other"
    return "Indépendant / sans enseigne", "independent_other"


# --------------------------------------------------------------------------------------------------
# OSM download
# --------------------------------------------------------------------------------------------------
def _osm_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["osm_type", "osm_id", "lat", "lon", "brand", "name", "operator"])
    return df.dropna(subset=["lat", "lon"]).drop_duplicates(["osm_type", "osm_id"])


def download_overpass(url: str, timeout: int = 900) -> pd.DataFrame:
    r = requests.post(url, data={"data": OVERPASS_QUERY}, timeout=timeout)
    r.raise_for_status()
    rows = []
    for el in r.json()["elements"]:
        tags = el.get("tags", {})
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        rows.append({"osm_type": el["type"], "osm_id": el["id"], "lat": lat, "lon": lon,
                     "brand": tags.get("brand"), "name": tags.get("name"), "operator": tags.get("operator")})
    return _osm_frame(rows)


def read_pbf(pbf_path) -> pd.DataFrame:
    """amenity=fuel nodes and ways (centroid of the way's nodes) from an .osm.pbf extract.

    Two passes keep memory low on a whole-country extract: (1) tagged fuel nodes and ways, without
    locations; (2) only the nodes referenced by those ways, selected by id inside libosmium.
    """
    import osmium

    rows, ways = [], []
    fp = (osmium.FileProcessor(str(pbf_path))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.NODE | osmium.osm.WAY))
          .with_filter(osmium.filter.TagFilter(("amenity", "fuel"))))
    for obj in fp:
        tags = {"brand": obj.tags.get("brand"), "name": obj.tags.get("name"), "operator": obj.tags.get("operator")}
        if obj.is_node():
            rows.append({"osm_type": "node", "osm_id": obj.id, "lat": obj.location.lat,
                         "lon": obj.location.lon, **tags})
        else:
            refs = [n.ref for n in obj.nodes]
            if len(refs) > 1 and refs[0] == refs[-1]:   # closed way: do not count the first node twice
                refs = refs[:-1]
            ways.append((obj.id, refs, tags))
    needed = {r for _, refs, _ in ways for r in refs}
    coords: dict[int, tuple[float, float]] = {}
    if needed:
        fp2 = (osmium.FileProcessor(str(pbf_path), osmium.osm.NODE)
               .with_filter(osmium.filter.IdFilter(needed)))
        for n in fp2:
            if n.is_node() and n.location.valid():
                coords[n.id] = (n.location.lat, n.location.lon)
    for way_id, refs, tags in ways:
        pts = [coords[r] for r in refs if r in coords]
        if not pts:
            continue
        rows.append({"osm_type": "way", "osm_id": way_id, "lat": float(np.mean([p[0] for p in pts])),
                     "lon": float(np.mean([p[1] for p in pts])), **tags})
    return _osm_frame(rows)


def download_osm() -> pd.DataFrame:
    """Fetch OSM fuel stations for France (source chosen in config) to data/raw/osm/."""
    out = path("raw") / "osm" / "osm_fuel_fr.parquet"
    if out.exists():
        log.info("exists, skipped: %s", out.name)
        return pd.read_parquet(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bcfg = cfg()["brands"]
    source = os.environ.get("PLEIN_TARIF_OSM_SOURCE", bcfg.get("osm_source", "overpass"))
    if source == "pbf":
        from plein_tarif.download import fetch

        pbf = fetch(bcfg["pbf_url"], out.parent / "france-latest.osm.pbf", min_bytes=10_000_000, timeout=3600)
        df = read_pbf(pbf)
        if not bcfg.get("keep_pbf", False):
            pbf.unlink()   # ~6 GB, only the extracted fuel stations are kept
    else:
        df = download_overpass(cfg()["sources"]["overpass_url"])
    df.to_parquet(out, index=False)
    log.info("OSM fuel stations: %s", f"{len(df):,}")
    return df


# --------------------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------------------
def match_stations(stations: pd.DataFrame, osm: pd.DataFrame, radius_m: float,
                   ambiguity_ratio: float = 1.5) -> pd.DataFrame:
    """Nearest OSM station within `radius_m` for each official station (columns station_id, lat, lon).

    A match is flagged ambiguous when a second OSM station with a different brand lies within the radius
    and within `ambiguity_ratio` times the distance of the nearest one.
    """
    st = stations.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    tree = BallTree(np.radians(osm[["lat", "lon"]].to_numpy()), metric="haversine")
    k = min(2, len(osm))
    dist, idx = tree.query(np.radians(st[["lat", "lon"]].to_numpy()), k=k)
    dist_m = dist * EARTH_RADIUS_M
    norm = osm.apply(lambda r: normalize_brand(r["brand"], r["name"], r["operator"]), axis=1, result_type="expand")
    osm = osm.assign(brand_std=norm[0], brand_category=norm[1])

    first = osm.iloc[idx[:, 0]].reset_index(drop=True)
    res = pd.DataFrame({
        "station_id": st["station_id"],
        "osm_type": first["osm_type"], "osm_id": first["osm_id"],
        "osm_brand_raw": first["brand"].fillna(first["name"]),
        "brand": first["brand_std"], "brand_category": first["brand_category"],
        "match_distance_m": dist_m[:, 0].round(1),
    })
    matched = res["match_distance_m"] <= radius_m
    if k == 2:
        second = osm.iloc[idx[:, 1]].reset_index(drop=True)
        res["ambiguous"] = (matched & (dist_m[:, 1] <= radius_m)
                            & (dist_m[:, 1] <= ambiguity_ratio * np.maximum(dist_m[:, 0], 1.0))
                            & (second["brand_std"] != first["brand_std"]))
    else:
        res["ambiguous"] = False
    res.loc[~matched, ["osm_type", "osm_id", "osm_brand_raw"]] = None
    res.loc[~matched, "brand"] = "Non identifiée"
    res.loc[~matched, "brand_category"] = "unmatched"
    res["matched"] = matched
    return res


def main() -> None:
    osm_file = path("raw") / "osm" / "osm_fuel_fr.parquet"
    if not osm_file.exists():
        log.error("No OSM data (%s): run `python run.py download` with OpenStreetMap access. "
                  "Brand columns will be empty.", osm_file)
        return
    osm = pd.read_parquet(osm_file)
    stations = pd.read_parquet(path("processed") / "stations.parquet")
    active = stations[stations["zone"].isin(["mainland", "corsica"])]
    b = cfg()["brands"]
    res = match_stations(active, osm, b["match_radius_m"], b["ambiguity_ratio"])
    res.to_parquet(path("processed") / "station_brands.parquet", index=False)

    with_prices = set(pd.read_parquet(path("processed") / "prices_clean.parquet",
                                      columns=["station_id"])["station_id"].unique())
    r = res[res["station_id"].isin(with_prices)]
    by_dept = (r.merge(stations[["station_id", "department"]], on="station_id")
                 .groupby("department").agg(stations=("station_id", "size"), match_rate=("matched", "mean"))
                 .round(3).reset_index())
    by_dept.to_csv(path("processed") / "brand_match_by_department.csv", index=False)
    report = {
        "osm_fuel_stations": int(len(osm)),
        "radius_m": b["match_radius_m"],
        "stations_with_prices_and_coords": int(len(r)),
        "matched": int(r["matched"].sum()),
        "match_rate": round(float(r["matched"].mean()), 4),
        "ambiguous": int(r["ambiguous"].sum()),
        "median_distance_m": float(r.loc[r["matched"], "match_distance_m"].median()),
        "by_category": r["brand_category"].value_counts().to_dict(),
        "top_brands": r["brand"].value_counts().head(20).to_dict(),
        "department_match_rate_min": by_dept.nsmallest(5, "match_rate").to_dict("records"),
    }
    # Sensitivity of the match rate to the radius (stations active in the last year vs all)
    nearest = match_stations(active, osm, radius_m=1e9)
    last_year = (pd.read_parquet(path("processed") / "prices_clean.parquet", columns=["station_id", "ts"])
                   .groupby("station_id")["ts"].max().dt.year)
    nearest = nearest[nearest["station_id"].isin(with_prices)]
    is_active = nearest["station_id"].map(last_year).eq(cfg()["period"]["end_year"])
    sens = pd.DataFrame([{"radius_m": r, "match_rate_all": round(float((nearest["match_distance_m"] <= r).mean()), 3),
                          "match_rate_active_last_year": round(float((nearest.loc[is_active, "match_distance_m"] <= r).mean()), 3)}
                         for r in (25, 50, 75, 100, 150, 200, 300, 500)])
    sens.to_csv(path("processed") / "brand_match_radius_sensitivity.csv", index=False)
    report["match_rate_active_last_year"] = round(float(r.loc[r["station_id"].map(last_year).eq(cfg()["period"]["end_year"]), "matched"].mean()), 4)
    (path("processed") / "brand_match_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log.info("brand match: %s / %s (%.1f%%), ambiguous %s", report["matched"], report["stations_with_prices_and_coords"],
             100 * report["match_rate"], report["ambiguous"])


if __name__ == "__main__":
    main()
