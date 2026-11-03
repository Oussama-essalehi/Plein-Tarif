"""Geography and calendar helpers: department from postcode, regions, city names, French holidays."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

import numpy as np
import pandas as pd

# Department code -> (region code INSEE, region name). Regions as of 2016.
_REGIONS: dict[str, tuple[str, list[str]]] = {
    "Auvergne-Rhône-Alpes": ("84", ["01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"]),
    "Bourgogne-Franche-Comté": ("27", ["21", "25", "39", "58", "70", "71", "89", "90"]),
    "Bretagne": ("53", ["22", "29", "35", "56"]),
    "Centre-Val de Loire": ("24", ["18", "28", "36", "37", "41", "45"]),
    "Corse": ("94", ["2A", "2B"]),
    "Grand Est": ("44", ["08", "10", "51", "52", "54", "55", "57", "67", "68", "88"]),
    "Hauts-de-France": ("32", ["02", "59", "60", "62", "80"]),
    "Île-de-France": ("11", ["75", "77", "78", "91", "92", "93", "94", "95"]),
    "Normandie": ("28", ["14", "27", "50", "61", "76"]),
    "Nouvelle-Aquitaine": ("75", ["16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"]),
    "Occitanie": ("76", ["09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"]),
    "Pays de la Loire": ("52", ["44", "49", "53", "72", "85"]),
    "Provence-Alpes-Côte d'Azur": ("93", ["04", "05", "06", "13", "83", "84"]),
    "Guadeloupe": ("01", ["971"]),
    "Martinique": ("02", ["972"]),
    "Guyane": ("03", ["973"]),
    "La Réunion": ("04", ["974"]),
    "Mayotte": ("06", ["976"]),
}
DEPT_TO_REGION: dict[str, tuple[str, str]] = {
    d: (code, name) for name, (code, depts) in _REGIONS.items() for d in depts
}
OVERSEAS_DEPTS = {"971", "972", "973", "974", "976"}
IDF_DEPTS = set(_REGIONS["Île-de-France"][1])


def department_from_postcode(postcode: str | float | None) -> str | None:
    """INSEE department code from a French postcode.

    - mainland: first two digits (01-95);
    - Corsica: 200xx-201xx -> 2A (Corse-du-Sud), 202xx-206xx -> 2B (Haute-Corse);
    - overseas: first three digits (971-976);
    - anything else (Monaco 980xx, malformed) -> None.
    """
    if postcode is None or (isinstance(postcode, float) and np.isnan(postcode)):
        return None
    cp = re.sub(r"\D", "", str(postcode))
    if not cp:
        return None
    cp = cp.zfill(5)[:5]
    if cp.startswith("97"):
        return cp[:3] if cp[:3] in OVERSEAS_DEPTS else None
    if cp.startswith("20"):
        return "2A" if int(cp) < 20200 else "2B"
    dept = cp[:2]
    return dept if dept in DEPT_TO_REGION else None


def zone_of(dept: str | None) -> str | None:
    """'mainland', 'corsica' or 'overseas'."""
    if dept is None:
        return None
    if dept in ("2A", "2B"):
        return "corsica"
    if dept in OVERSEAS_DEPTS:
        return "overseas"
    return "mainland"


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def standard_city(city: str | None, postcode: str | None) -> str:
    """Upper-case, accent-free city name; Paris/Marseille/Lyon arrondissements merged by postcode."""
    cp = re.sub(r"\D", "", str(postcode or "")).zfill(5)
    if cp.startswith("750"):
        return "PARIS"
    if cp.startswith("130") and 1 <= int(cp[3:]) <= 16:
        return "MARSEILLE"
    if cp.startswith("6900") and cp[4] != "0":
        return "LYON"
    name = _strip_accents(str(city or "")).upper()
    name = re.sub(r"[^A-Z0-9 ]", " ", name.replace("'", " "))
    name = re.sub(r"\bST\b", "SAINT", name)
    name = re.sub(r"\bSTE\b", "SAINTE", name)
    name = re.sub(r"\b(CEDEX|CDX)\b.*$", "", name)
    name = re.sub(r"\s+\d+\s*(E|EME|ER)?$", "", name.strip())   # "TOULOUSE 2" -> "TOULOUSE"
    return re.sub(r"\s+", " ", name).strip()


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def french_holidays(year: int) -> dict[date, str]:
    """National public holidays in mainland France (11 days)."""
    easter = _easter(year)
    return {
        date(year, 1, 1): "Jour de l'an",
        easter + timedelta(days=1): "Lundi de Pâques",
        date(year, 5, 1): "Fête du travail",
        date(year, 5, 8): "Victoire 1945",
        easter + timedelta(days=39): "Ascension",
        easter + timedelta(days=50): "Lundi de Pentecôte",
        date(year, 7, 14): "Fête nationale",
        date(year, 8, 15): "Assomption",
        date(year, 11, 1): "Toussaint",
        date(year, 11, 11): "Armistice 1918",
        date(year, 12, 25): "Noël",
    }


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (vectorised)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * np.arcsin(np.sqrt(a))


def in_bbox(lat: pd.Series, lon: pd.Series, bbox: list[float]) -> pd.Series:
    lat_min, lat_max, lon_min, lon_max = bbox
    return lat.between(lat_min, lat_max) & lon.between(lon_min, lon_max)
