"""Download every raw input into data/raw/ (idempotent: existing valid files are kept).

Sources (all checked before writing this module):
- yearly price archives, prix-carburants.gouv.fr open data (Licence Ouverte), one ZIP of XML per year;
- Brent crude oil, daily, FRED series DCOILBRENTEU (USD per barrel);
- EUR/USD, daily, FRED series DEXUSEU (USD per EUR, Federal Reserve H.10); ECB Data Portal as fallback;
- French departments GeoJSON (gregoiredavid/france-geojson, IGN Admin Express, Licence Ouverte);
- Ministry weekly average prices HTT/TTC and monthly refining margin (ecologie.gouv.fr), used for checks.
OpenStreetMap stations are downloaded by `enrich_brands.download_osm()`.
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import requests
from tqdm import tqdm

from plein_tarif.config import cfg, get_logger, path, years

log = get_logger(__name__)
# Default requests headers are used: FRED stalls requests carrying an unknown custom User-Agent.


def fetch(url: str, dest: Path, *, retries: int = 4, timeout: int = 120, min_bytes: int = 100) -> Path:
    """Stream `url` to `dest` with retries and exponential backoff; skip if `dest` already exists."""
    if dest.exists() and dest.stat().st_size >= min_bytes:
        log.info("exists, skipped: %s", dest.relative_to(path("raw").parent.parent))
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0)) or None
                with open(tmp, "wb") as fh, tqdm(total=total, unit="B", unit_scale=True,
                                                 desc=dest.name, leave=False) as bar:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
                        bar.update(len(chunk))
            if tmp.stat().st_size < min_bytes:
                raise OSError(f"file too small ({tmp.stat().st_size} bytes)")
            tmp.replace(dest)
            log.info("downloaded %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
            return dest
        except (requests.RequestException, OSError) as exc:
            if attempt == retries:
                raise
            wait = 2 ** (attempt + 1)
            log.warning("download failed (%s), retry in %ss: %s", exc, wait, url)
            time.sleep(wait)
    return dest


def download_prices() -> list[Path]:
    """Yearly price archives (one ZIP per year containing PrixCarburants_annuel_<year>.xml)."""
    out = []
    for year in years():
        url = cfg()["sources"]["prices_yearly_url"].format(year=year)
        dest = path("raw") / "prix" / f"PrixCarburants_annuel_{year}.zip"
        fetch(url, dest, min_bytes=1_000_000, timeout=600)
        with zipfile.ZipFile(dest) as zf:  # fails loudly on a truncated archive
            names = zf.namelist()
            if not any(n.endswith(".xml") for n in names):
                raise ValueError(f"{dest.name}: no XML inside ({names})")
        out.append(dest)
    return out


def download_market() -> None:
    """Brent and EUR/USD daily series (FRED); the ECB is used for EUR/USD if FRED fails."""
    src = cfg()["sources"]
    start, end = cfg()["period"]["start_year"] - 1, cfg()["period"]["end_year"] + 1
    raw = path("raw") / "market"
    fetch(src["brent_url"], raw / "brent_fred.csv")
    try:
        fetch(src["eurusd_url"], raw / "eurusd_fred.csv", retries=2, timeout=60)
    except Exception as exc:
        log.warning("FRED EUR/USD failed (%s), falling back to the ECB", exc)
        fetch(src["eurusd_fallback_url"].format(start=f"{start}-01-01", end=f"{end}-12-31"),
              raw / "eurusd_ecb.csv", retries=2, timeout=60)


def download_reference() -> None:
    """Department shapes and the Ministry's official series used for sanity checks."""
    src = cfg()["sources"]
    fetch(src["departments_geojson_url"], path("raw") / "geo" / "departements.geojson")
    fetch(src["official_weekly_prices_url"], path("raw") / "official" / "prix_htt_ttc_hebdo.xlsx")
    fetch(src["refining_margin_url"], path("raw") / "official" / "marge_raffinage.xlsx")
    fetch(src["tax_guide_url"], path("raw") / "official" / "guide_fiscalite_2026.pdf", min_bytes=100_000)


def main() -> None:
    download_prices()
    download_market()
    download_reference()
    # OSM stations: optional, the brand step reports clearly if the API cannot be reached.
    from plein_tarif.enrich_brands import download_osm

    try:
        download_osm()
    except Exception as exc:  # network policy, API outage...
        log.error("OpenStreetMap download failed (%s); brand enrichment will be skipped.", exc)


if __name__ == "__main__":
    main()
