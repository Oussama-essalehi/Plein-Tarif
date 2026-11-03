"""Market data and taxes: Brent in EUR/litre, excise and VAT by date/fuel/zone, pre-tax prices.

Pre-tax price (distributor's price before excise and VAT):

    price_ht = price_ttc / (1 + VAT) - excise + discount_ht

- VAT is 20% in mainland France and 13% in Corsica (config/taxes.yaml).
- `excise` is the national TICPE/accise rate plus the regional majoration (and the Île-de-France
  majoration in Île-de-France), minus the Corsica reduction on petrol, as in force on that date.
- `discount_ht` is the 2022 state discount: prices reported to prix-carburants.gouv.fr are the prices
  displayed at the pump, i.e. already net of the discount (the discount was deducted at the till by every
  station). Adding it back gives the price actually set by the distributor.

Outputs (data/processed/): market_daily.parquet, tax_daily.parquet, official_prices.parquet,
refining_margin.parquet, tax_validation.csv.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from plein_tarif.config import cfg, events, get_logger, path, taxes

log = get_logger(__name__)

LITRES_PER_BARREL = 158.987
ESSENCES = ("SP95", "SP98", "E10")
FUELS = ("Gazole", "SP95", "SP98", "E10", "E85", "GPLc")
TAX_ZONES = ("mainland", "idf", "corsica")
OFFICIAL_COLUMNS = ["Gazole", "SP95", "E10", "SP98", "E85", "GPLc"]   # order in the ministry's file


# --------------------------------------------------------------------------------------------------
# Taxes
# --------------------------------------------------------------------------------------------------
def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def excise_rate(fuel: str, day: date, zone: str = "mainland", tax_cfg: dict | None = None) -> float:
    """Excise in EUR/litre for a fuel, a day and a tax zone ('mainland', 'idf' or 'corsica')."""
    t = tax_cfg or taxes()
    day = _as_date(day)
    period = next(p for p in t["excise_national"]
                  if _as_date(p["valid_from"]) <= day <= _as_date(p["valid_to"]))
    if fuel == "GPLc":
        return period["GPLc_per_100kg"] / 100.0 * t["gplc_density_kg_per_l"]
    rate = float(period[fuel])
    family = "gazole" if fuel == "Gazole" else "essences" if fuel in ESSENCES else None
    if family is None:          # E85: particular tariff, no regional modulation
        return rate
    if zone == "corsica":
        return rate - (t["corsica_minoration"]["essences"] if family == "essences" else 0.0)
    if day <= _as_date(t["regional_majoration"]["valid_to"]):
        rate += t["regional_majoration"][family]
    if zone == "idf" and day <= _as_date(t["idf_majoration"]["valid_to"]):
        rate += t["idf_majoration"][family]
    return rate


def vat_rate(zone: str, tax_cfg: dict | None = None) -> float:
    t = tax_cfg or taxes()
    return t["vat"]["corsica"] if zone == "corsica" else t["vat"]["mainland"]


def discount_ht(day: date, ev_cfg: dict | None = None) -> float:
    """State discount (EUR/litre, excluding VAT) in force on a day; 0 outside 2022 discount periods."""
    ev = ev_cfg or events()
    day = _as_date(day)
    for p in ev["discount_periods"]:
        if _as_date(p["start"]) <= day <= _as_date(p["end"]):
            return float(p["amount_ht"])
    return 0.0


def price_ht(price_ttc, vat, excise, discount=0.0):
    """Pre-tax price per litre: TTC / (1 + VAT) - excise + discount (HT)."""
    return price_ttc / (1.0 + vat) - excise + discount


def tax_zone_of(department: str | None) -> str:
    if department in ("2A", "2B"):
        return "corsica"
    if department in taxes()["idf_majoration"]["departments"]:
        return "idf"
    return "mainland"


def build_tax_table(start: str, end: str) -> pd.DataFrame:
    """One row per day, fuel and tax zone with VAT, excise and discount."""
    days = pd.date_range(start, end, freq="D")
    t, ev = taxes(), events()
    rows = []
    for d in days:
        dd = d.date()
        disc = discount_ht(dd, ev)
        for fuel in FUELS:
            for zone in TAX_ZONES:
                vat = vat_rate(zone, t)
                rows.append((dd, fuel, zone, vat, round(excise_rate(fuel, dd, zone, t), 5), disc,
                             round(disc * (1 + vat), 5)))
    return pd.DataFrame(rows, columns=["date", "fuel", "tax_zone", "vat", "excise", "discount_ht",
                                       "discount_ttc"])


# --------------------------------------------------------------------------------------------------
# Market series
# --------------------------------------------------------------------------------------------------
def _read_fred(file, column: str) -> pd.Series:
    df = pd.read_csv(file, na_values=["."])
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    return df.set_index("observation_date")[column].astype(float)


def load_eurusd() -> pd.Series:
    raw = path("raw") / "market"
    if (raw / "eurusd_fred.csv").exists():
        return _read_fred(raw / "eurusd_fred.csv", "DEXUSEU")
    ecb = pd.read_csv(raw / "eurusd_ecb.csv")
    return ecb.assign(d=pd.to_datetime(ecb["TIME_PERIOD"])).set_index("d")["OBS_VALUE"].astype(float)


def build_market_daily(start: str, end: str, max_fill_days: int = 5) -> pd.DataFrame:
    """Daily Brent (USD/bbl, EUR/bbl, EUR/l) and EUR/USD; non-trading days carry the last quote."""
    brent = _read_fred(path("raw") / "market" / "brent_fred.csv", "DCOILBRENTEU")
    fx = load_eurusd()
    days = pd.date_range(pd.Timestamp(start) - pd.Timedelta(days=10), end, freq="D")  # margin for the fill
    df = pd.DataFrame(index=days)
    df["brent_usd_bbl_quote"] = brent.reindex(days)
    df["eurusd_quote"] = fx.reindex(days)
    df["brent_usd_bbl"] = df["brent_usd_bbl_quote"].ffill(limit=max_fill_days)
    df["eurusd"] = df["eurusd_quote"].ffill(limit=max_fill_days)
    df["brent_eur_bbl"] = df["brent_usd_bbl"] / df["eurusd"]
    df["brent_eur_l"] = df["brent_eur_bbl"] / LITRES_PER_BARREL
    df["is_trading_day"] = df["brent_usd_bbl_quote"].notna()
    df.index.name = "date"
    df = df.loc[pd.Timestamp(start):]
    return df.drop(columns=["brent_usd_bbl_quote", "eurusd_quote"]).reset_index()


def load_official() -> pd.DataFrame:
    """Ministry national average prices, HTT and TTC, long format (freq, date, fuel, htt, ttc).

    The sheet stacks three blocks: weekly prices (Friday dates), then 'MOYENNES MENSUELLES' (first day
    of each month), then 'MOYENNES ANNUELLES' (year labels). Each block is located by its header row.
    """
    raw = pd.read_excel(path("raw") / "official" / "prix_htt_ttc_hebdo.xlsx",
                        sheet_name="Carburants routiers", header=None)
    col0 = raw[0].astype(str).str.strip().str.upper()
    i_month = int(col0[col0.eq("MOYENNES MENSUELLES")].index[0])
    i_year = int(col0[col0.eq("MOYENNES ANNUELLES")].index[0])
    blocks = {"weekly": raw.iloc[2:i_month], "monthly": raw.iloc[i_month + 1:i_year],
              "yearly": raw.iloc[i_year + 1:]}
    out = []
    for freq, body in blocks.items():
        if freq == "yearly":
            dates = pd.to_datetime(body[0].astype(str).str.extract(r"(\d{4})")[0] + "-01-01", errors="coerce")
        else:
            dates = pd.to_datetime(body[0], errors="coerce")
        for i, fuel in enumerate(OFFICIAL_COLUMNS):
            out.append(pd.DataFrame({"freq": freq, "date": dates, "fuel": fuel,
                                     "htt": pd.to_numeric(body[1 + i], errors="coerce"),
                                     "ttc": pd.to_numeric(body[7 + i], errors="coerce")}))
    return pd.concat(out, ignore_index=True).dropna(subset=["date", "htt", "ttc"])


def load_refining_margin() -> pd.DataFrame:
    """Monthly gross refining margin on Brent (USD/bbl and EUR/t) from the ministry."""
    raw = pd.read_excel(path("raw") / "official" / "marge_raffinage.xlsx", header=None).iloc[2:]
    raw = raw[raw[0].map(lambda v: hasattr(v, "year") and not isinstance(v, (int, float)))]
    return pd.DataFrame({"month": pd.to_datetime(raw[0]),
                         "margin_usd_bbl": pd.to_numeric(raw[1], errors="coerce"),
                         "margin_eur_t": pd.to_numeric(raw[2], errors="coerce")}).dropna()


def validate_taxes(official: pd.DataFrame, tax: pd.DataFrame, idf_share: float) -> pd.DataFrame:
    """Compare the excise implied by the official series (TTC/1.2 - HTT) with the configured rates.

    The configured national average is a mix of the 'mainland' and 'idf' zones weighted by the share
    of stations located in Île-de-France. The ministry computes HTT from the displayed (discounted) TTC,
    so its HTT is net of the 2022 discount and no adjustment is needed for the comparison.
    """
    t = tax[tax["tax_zone"].isin(["mainland", "idf"])].pivot_table(
        index=["date", "fuel"], columns="tax_zone", values=["excise", "discount_ht"]).reset_index()
    t.columns = ["date", "fuel", "disc_idf", "disc_mainland", "excise_idf", "excise_mainland"]
    t["date"] = pd.to_datetime(t["date"])
    t["excise_model"] = (1 - idf_share) * t["excise_mainland"] + idf_share * t["excise_idf"]
    weekly = official[official["freq"] == "weekly"]
    m = weekly.merge(t[["date", "fuel", "excise_model"]], on=["date", "fuel"], how="inner")
    m["excise_implied"] = m["ttc"] / 1.2 - m["htt"]
    m["gap_cts"] = 100 * (m["excise_implied"] - m["excise_model"])
    m["year"] = m["date"].dt.year
    return (m.groupby(["fuel", "year"])
              .agg(weeks=("gap_cts", "size"), excise_model=("excise_model", "mean"),
                   excise_implied=("excise_implied", "mean"), gap_cts_mean=("gap_cts", "mean"),
                   gap_cts_max_abs=("gap_cts", lambda s: s.abs().max()))
              .round(4).reset_index())


def main() -> None:
    period = cfg()["period"]
    start, end = f"{period['start_year']}-01-01", f"{period['end_year']}-12-31"
    out = path("processed")

    market = build_market_daily(start, end)
    tax = build_tax_table(start, end)
    # dim_market grain: date x fuel, mainland (non Île-de-France) taxes
    mk = tax[tax["tax_zone"] == "mainland"].drop(columns="tax_zone").assign(date=lambda d: pd.to_datetime(d["date"]))
    mk = mk.merge(market, on="date", how="left")
    mk.to_parquet(out / "market_daily.parquet", index=False)
    tax.to_parquet(out / "tax_daily.parquet", index=False)

    official = load_official()
    official.to_parquet(out / "official_prices.parquet", index=False)
    load_refining_margin().to_parquet(out / "refining_margin.parquet", index=False)

    stations = pd.read_parquet(out / "stations.parquet")
    active = stations[stations["zone"] == "mainland"]
    idf_share = float(active["department"].isin(taxes()["idf_majoration"]["departments"]).mean())
    val = validate_taxes(official, tax, idf_share)
    val.to_csv(out / "tax_validation.csv", index=False)
    log.info("Brent missing days after fill: %s", int(market["brent_eur_l"].isna().sum()))
    log.info("tax validation (implied - model, cts/l):\n%s",
             val.pivot(index="year", columns="fuel", values="gap_cts_mean").round(2).to_string())


if __name__ == "__main__":
    main()
