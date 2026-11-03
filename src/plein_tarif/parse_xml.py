"""Streaming parser for the yearly prix-carburants XML files.

File layout (identical element names in 2019-2025):

    <pdv_liste>
      <pdv id="1000001" latitude="4620114" longitude="519791" cp="01000" pop="R">
        <adresse>..</adresse> <ville>..</ville>
        <horaires automate-24-24="1"> <jour .../> </horaires>
        <services> <service>..</service> </services>
        <prix nom="Gazole" id="1" maj="2019-01-04T10:53:48" valeur="1328"/>   (one per price update)
        <rupture id="1" nom="Gazole" debut=".." fin=".." [type=".."]/>         (stock-outs; `type` from 2024)
        <fermeture type="T" debut=".." fin=".."/>                              (closures)
      </pdv>

Format quirks handled here (found by profiling every year, see docs/methodology.md):
- encoding is ISO-8859-1 (declared in the XML header, decoded by lxml);
- price unit changes across years: thousandths of a euro until 2021 (`1328` = 1.328 EUR),
  euros from 2022 (`1.572`). The unit is detected per file from the share of values containing a dot,
  never assumed from the year. Integer values inside a euro-unit file (`2`, `1`) are euros;
- coordinates are degrees x 100,000 (`4620114` = 46.20114), sometimes with decimals, sometimes empty;
- empty `<prix/>` elements (no attributes) exist in every year and are counted then skipped;
- `pop` is R (road), A (autoroute = motorway) or, rarely, N.
"""

from __future__ import annotations

import json
import zipfile
from array import array
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import IO, Iterator

import numpy as np
import pandas as pd
from lxml import etree
from tqdm import tqdm

from plein_tarif.config import get_logger, path, years

log = get_logger(__name__)

FUELS: dict[str, int] = {"Gazole": 1, "SP95": 2, "E85": 3, "GPLc": 4, "E10": 5, "SP98": 6}
FUEL_BY_ID: dict[int, str] = {v: k for k, v in FUELS.items()}
EPOCH = datetime(1970, 1, 1)


@contextmanager
def open_xml(source: Path) -> Iterator[IO[bytes]]:
    """Open a yearly file, either the ZIP archive or an extracted .xml."""
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
            with zf.open(name) as fh:
                yield fh
    else:
        with open(source, "rb") as fh:
            yield fh


def declared_encoding(source: Path) -> str:
    """Encoding declared in the XML header (e.g. ISO-8859-1)."""
    with open_xml(source) as fh:
        head = fh.read(200).decode("ascii", errors="ignore")
    marker = 'encoding="'
    return head.split(marker, 1)[1].split('"', 1)[0] if marker in head else "UTF-8"


def detect_price_unit(values: list[str] | np.ndarray, min_records: int = 1) -> str:
    """Return 'euro' or 'thousandth' from raw price strings of one file.

    A file is in euros when most non-empty values contain a decimal point.
    """
    vals = [v for v in values if v]
    if len(vals) < min_records:
        raise ValueError(f"not enough price records to detect the unit ({len(vals)})")
    share_dot = sum("." in v for v in vals) / len(vals)
    return "euro" if share_dot > 0.5 else "thousandth"


def detect_coord_scale(lat_values: np.ndarray) -> float:
    """Divisor that brings raw latitudes to decimal degrees (1 or 100,000)."""
    lat = np.abs(lat_values[~np.isnan(lat_values)])
    if lat.size == 0:
        return 1.0
    med = float(np.median(lat))
    if med > 1000:        # e.g. 4620114 -> 46.20114
        return 100_000.0
    return 1.0


def _to_float(value: str | None) -> float:
    try:
        return float(value) if value not in (None, "") else np.nan
    except ValueError:
        return np.nan


def _ts_seconds(value: str) -> int:
    """'2019-01-04T10:53:48' (local time, no zone) -> seconds since 1970 (naive)."""
    return int((datetime.fromisoformat(value) - EPOCH).total_seconds())


def parse_file(source: Path, *, min_records_for_unit: int = 1) -> dict[str, object]:
    """Parse one yearly file into DataFrames: prices, stations, stockouts, closures + a report dict."""
    pid, fid, ts, raw_val = array("q"), array("b"), array("q"), []
    stations: list[dict] = []
    stockouts: list[tuple] = []
    closures: list[tuple] = []
    counts: Counter = Counter()
    unknown_fuels: Counter = Counter()

    with open_xml(source) as fh:
        context = etree.iterparse(fh, events=("end",), tag="pdv", huge_tree=True)
        for _, pdv in tqdm(context, desc=source.stem, unit=" pdv", leave=False, mininterval=2):
            station_id = int(pdv.get("id"))
            services, automate = [], None
            for child in pdv:
                tag = child.tag
                if tag == "prix":
                    name, val, maj = child.get("nom"), child.get("valeur"), child.get("maj")
                    if not name or not val or not maj:
                        counts["empty_price_elements"] += 1
                        continue
                    if name not in FUELS:
                        unknown_fuels[name] += 1
                        continue
                    pid.append(station_id)
                    fid.append(FUELS[name])
                    ts.append(_ts_seconds(maj))
                    raw_val.append(val)
                elif tag == "services":
                    services = [s.text.strip() for s in child if s.text]
                elif tag == "horaires":
                    automate = child.get("automate-24-24") == "1"
                elif tag == "rupture":
                    stockouts.append((station_id, child.get("nom"), child.get("debut"),
                                      child.get("fin"), child.get("type")))
                elif tag == "fermeture" and child.get("type"):
                    closures.append((station_id, child.get("type"), child.get("debut"), child.get("fin")))
            stations.append({
                "station_id": station_id,
                "lat_raw": _to_float(pdv.get("latitude")),
                "lon_raw": _to_float(pdv.get("longitude")),
                "postcode": (pdv.get("cp") or "").strip(),
                "pop": pdv.get("pop"),
                "address": (pdv.findtext("adresse") or "").strip(),
                "city": (pdv.findtext("ville") or "").strip(),
                "services": "|".join(services),
                "automate_24_24": automate,
            })
            counts["stations"] += 1
            pdv.clear()
            while pdv.getprevious() is not None:   # free already-processed siblings
                del pdv.getparent()[0]

    unit = detect_price_unit(raw_val, min_records_for_unit)
    values = np.array(raw_val, dtype="float64")
    price = values / 1000.0 if unit == "thousandth" else values
    prices = pd.DataFrame({
        "station_id": np.frombuffer(pid, dtype="int64"),
        "fuel_id": np.frombuffer(fid, dtype="int8"),
        "ts": pd.to_datetime(np.frombuffer(ts, dtype="int64"), unit="s"),
        "value_raw": values,
        "price": price,
    })
    prices.insert(2, "fuel", pd.Categorical(prices["fuel_id"].map(FUEL_BY_ID), categories=list(FUELS)))

    st = pd.DataFrame(stations)
    scale = detect_coord_scale(st["lat_raw"].to_numpy())
    st["lat"] = st["lat_raw"] / scale
    st["lon"] = st["lon_raw"] / scale
    st["is_motorway"] = st["pop"].eq("A")

    stock = pd.DataFrame(stockouts, columns=["station_id", "fuel", "start", "end", "type"])
    for c in ("start", "end"):
        stock[c] = pd.to_datetime(stock[c].replace("", None), errors="coerce")
    clo = pd.DataFrame(closures, columns=["station_id", "type", "start", "end"])
    for c in ("start", "end"):
        clo[c] = pd.to_datetime(clo[c].replace("", None), errors="coerce")

    median_price = float(np.nanmedian(price)) if len(price) else float("nan")
    report = {
        "file": source.name,
        "encoding": declared_encoding(source),
        "price_unit": unit,
        "coord_divisor": scale,
        "stations": int(counts["stations"]),
        "stations_without_coordinates": int(st["lat_raw"].isna().sum()),
        "price_records": int(len(prices)),
        "empty_price_elements": int(counts["empty_price_elements"]),
        "unknown_fuels": dict(unknown_fuels),
        "integer_values_in_euro_file": int(sum("." not in v for v in raw_val)) if unit == "euro" else 0,
        "median_price_eur": round(median_price, 4),
        "stockouts": int(len(stock)),
        "closures": int(len(clo)),
        "pop_counts": st["pop"].value_counts(dropna=False).to_dict(),
    }
    if len(prices) and not 0.5 <= median_price <= 3.0:
        raise ValueError(f"{source.name}: implausible median price {median_price} after unit '{unit}'")
    return {"prices": prices, "stations": st, "stockouts": stock, "closures": clo, "report": report}


def parse_year(year: int) -> dict[str, object]:
    """Parse one year and write its Parquet files to data/parsed/."""
    source = path("raw") / "prix" / f"PrixCarburants_annuel_{year}.zip"
    out = path("parsed")
    res = parse_file(source, min_records_for_unit=1000)
    res["prices"].to_parquet(out / f"prices_{year}.parquet", index=False)
    st = res["stations"].assign(year=year)
    st.to_parquet(out / f"stations_{year}.parquet", index=False)
    res["stockouts"].assign(year=year).to_parquet(out / f"stockouts_{year}.parquet", index=False)
    res["closures"].assign(year=year).to_parquet(out / f"closures_{year}.parquet", index=False)
    report = res["report"] | {"year": year}
    (out / f"parse_report_{year}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log.info("%s: %s records, unit=%s, coords /%g, %s stations",
             year, f"{report['price_records']:,}", report["price_unit"], report["coord_divisor"], report["stations"])
    return report


def main() -> None:
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(parse_year, years()))
    pd.DataFrame(reports).to_csv(path("parsed") / "parse_report.csv", index=False)


if __name__ == "__main__":
    main()
