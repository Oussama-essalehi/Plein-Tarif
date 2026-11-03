"""Self-contained HTML maps (folium / Leaflet) in reports/maps/.

1. carte_departements_<year>.html: median price by department, Gazole and SP95-E10 layers, for the latest
   full year and for 2022 (crisis year). Sequential single-hue scale (light = cheaper, dark = more expensive).
2. carte_<city>.html: stations of the focus city area coloured by their average gap to the area median
   (diverging scale: blue = cheaper, grey = at the median, orange = more expensive). Popups: brand, road type,
   average prices. Only public station data is shown.

Department shapes: france-geojson (IGN Admin Express, Licence Ouverte). City basemap: © OpenStreetMap contributors.
"""

from __future__ import annotations

import json

import branca.colormap as cm
import folium
import numpy as np
import pandas as pd

from plein_tarif.config import cfg, get_logger, path
from plein_tarif.model import connect_db

log = get_logger(__name__)
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIVERGING = ["#2a78d6", "#86b6ef", "#e6e5e1", "#f4a582", "#eb6834"]
TILES = "OpenStreetMap"   # standard OSM tiles (CARTO basemaps now require an API key)


def department_medians(con, year: int) -> pd.DataFrame:
    return con.execute("""
        WITH st AS (
            SELECT s.department, f.fuel_code, p.station_key, avg(p.price_ttc) AS price
            FROM fact_price_daily p
            JOIN dim_date d USING (date_key) JOIN dim_station s USING (station_key) JOIN dim_fuel f USING (fuel_key)
            WHERE d.year = ? AND f.is_main_fuel
            GROUP BY ALL HAVING count(*) >= getvariable('min_days')
        )
        SELECT department, fuel_code, median(price) AS median_price, count(*) AS n_stations
        FROM st GROUP BY ALL HAVING count(*) >= getvariable('min_stations')""", [year]).df()


def department_map(con, year: int, geojson: dict) -> None:
    med = department_medians(con, year)
    fmap = folium.Map(location=[46.6, 2.4], zoom_start=6, tiles=None, control_scale=True)   # no basemap needed
    labels = cfg()["fuels"]["labels"]
    for i, fuel in enumerate(("Gazole", "E10")):
        m = med[med["fuel_code"] == fuel].set_index("department")
        if m.empty:
            continue
        national = float(m["median_price"].median())
        lo, hi = float(m["median_price"].quantile(0.02)), float(m["median_price"].quantile(0.98))
        scale = cm.LinearColormap(SEQ_BLUE, vmin=lo, vmax=hi).to_step(7)
        scale.caption = f"{labels[fuel]} {year} : prix médian (€/L)"
        gj = json.loads(json.dumps(geojson))
        for feat in gj["features"]:
            code = feat["properties"]["code"]
            row = m.loc[code] if code in m.index else None
            feat["properties"]["prix"] = f"{row['median_price']:.3f} €" if row is not None else "n.d."
            feat["properties"]["ecart"] = (f"{100 * (row['median_price'] - national):+.1f} cts" if row is not None else "n.d.")
            feat["properties"]["stations"] = int(row["n_stations"]) if row is not None else 0
            feat["properties"]["_v"] = float(row["median_price"]) if row is not None else None
        layer = folium.FeatureGroup(name=f"{labels[fuel]} - prix médian {year}", overlay=False, show=(i == 0))
        folium.GeoJson(
            gj,
            style_function=lambda f, scale=scale: {
                "fillColor": scale(min(max(f["properties"]["_v"], scale.vmin), scale.vmax)) if f["properties"]["_v"] else "#f0f0f0",
                "color": "#fcfcfb", "weight": 1, "fillOpacity": 0.9},
            highlight_function=lambda f: {"weight": 3, "color": "#0b0b0b"},
            tooltip=folium.GeoJsonTooltip(fields=["nom", "code", "prix", "ecart", "stations"],
                                          aliases=["Département", "Code", "Prix médian", "Écart à la médiane des départements",
                                                   "Stations"]),
        ).add_to(layer)
        layer.add_to(fmap)
        scale.add_to(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    _add_title(fmap, f"Prix médian des carburants par département, {year}",
               "Médiane des prix moyens annuels des stations. Sources : prix-carburants.gouv.fr (Licence Ouverte), "
               "contours IGN Admin Express via france-geojson (Licence Ouverte).")
    out = path("maps") / f"carte_departements_{year}.html"
    fmap.save(str(out))
    log.info("map %s (%.1f MB)", out.name, out.stat().st_size / 1e6)


def city_map(con, year: int) -> None:
    g = cfg()["geography"]
    lat0, lon0 = g["focus_city_center"]
    radius = g["focus_city_radius_km"]
    df = con.execute("""
        SELECT s.station_id, s.address, s.city, s.brand, s.road_type, s.lat, s.lon, f.fuel_code,
               avg(p.price_ttc) AS price, count(*) AS n_days
        FROM fact_price_daily p
        JOIN dim_date d USING (date_key) JOIN dim_station s USING (station_key) JOIN dim_fuel f USING (fuel_key)
        WHERE d.year = ? AND f.is_main_fuel AND s.lat IS NOT NULL
          AND 6371 * 2 * asin(sqrt(pow(sin(radians(s.lat - ?) / 2), 2)
              + cos(radians(?)) * cos(radians(s.lat)) * pow(sin(radians(s.lon - ?) / 2), 2))) <= ?
        GROUP BY ALL HAVING count(*) >= getvariable('min_days')""", [year, lat0, lat0, lon0, radius]).df()
    if df.empty:
        log.warning("no station around the focus city")
        return
    wide = df.pivot_table(index=["station_id", "address", "city", "brand", "road_type", "lat", "lon"],
                          columns="fuel_code", values="price").reset_index()
    medians = {f: float(df.loc[df["fuel_code"] == f, "price"].median()) for f in ("Gazole", "E10")}
    gaps = []
    for f in ("Gazole", "E10"):
        if f in wide:
            gaps.append(100 * (wide[f] - medians[f]))
    wide["gap_cts"] = np.nanmean(np.vstack(gaps), axis=0)
    scale = cm.LinearColormap(DIVERGING, vmin=-10, vmax=10).to_step(5)
    scale.caption = "Écart moyen à la médiane locale (cts/L, Gazole et SP95-E10)"
    fmap = folium.Map(location=[lat0, lon0], zoom_start=11, tiles=TILES, control_scale=True)
    for _, r in wide.iterrows():
        gz = f"{r['Gazole']:.3f} €" if "Gazole" in r and pd.notna(r.get("Gazole")) else "n.d."
        e10 = f"{r['E10']:.3f} €" if "E10" in r and pd.notna(r.get("E10")) else "n.d."
        popup = (f"<b>{r['brand']}</b><br>{r['address']}, {r['city']}<br>Type de route : {r['road_type']}<br>"
                 f"Gazole {year} : {gz}<br>SP95-E10 {year} : {e10}<br>Écart à la médiane : {r['gap_cts']:+.1f} cts")
        folium.CircleMarker(
            location=[r["lat"], r["lon"]], radius=8 if r["road_type"] == "Autoroute" else 6,
            color="#0b0b0b" if r["road_type"] == "Autoroute" else "#fcfcfb", weight=2 if r["road_type"] == "Autoroute" else 1.5,
            fill=True, fill_opacity=0.95, fill_color=scale(float(np.clip(r["gap_cts"], -10, 10))),
            popup=folium.Popup(popup, max_width=320),
            tooltip=f"{r['brand']} : {r['gap_cts']:+.1f} cts",
        ).add_to(fmap)
    scale.add_to(fmap)
    _add_title(fmap, f"{g['focus_city']} et alentours ({radius} km) : qui est cher, qui ne l'est pas ? ({year})",
               f"{len(wide)} stations ; cercle cerclé de noir = autoroute. Médianes locales : Gazole {medians['Gazole']:.3f} €, "
               f"SP95-E10 {medians['E10']:.3f} €. Sources : prix-carburants.gouv.fr ; enseignes et fond de carte © contributeurs OpenStreetMap (ODbL).")
    out = path("maps") / f"carte_{g['focus_city'].lower()}.html"
    fmap.save(str(out))
    log.info("map %s (%.1f MB), %s stations", out.name, out.stat().st_size / 1e6, len(wide))


def _add_title(fmap: folium.Map, title: str, subtitle: str) -> None:
    html = (f"<div style='position:fixed;bottom:30px;left:10px;z-index:9999;background:rgba(252,252,251,0.95);"
            f"padding:8px 12px;border-radius:6px;max-width:560px;font-family:Inter,Segoe UI,Arial,sans-serif;"
            f"box-shadow:0 1px 4px rgba(0,0,0,0.2)'><div style='font-size:16px;font-weight:600;color:#0b0b0b'>{title}</div>"
            f"<div style='font-size:12px;color:#52514e;margin-top:4px'>{subtitle}</div></div>")
    fmap.get_root().html.add_child(folium.Element(html))


def main() -> None:
    year = cfg()["period"]["end_year"]
    geojson = json.loads((path("raw") / "geo" / "departements.geojson").read_text(encoding="utf-8"))
    con = connect_db()
    for y in sorted({year, 2022}):
        department_map(con, y, geojson)
    city_map(con, year)
    con.close()


if __name__ == "__main__":
    main()
