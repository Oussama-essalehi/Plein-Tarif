"""Business questions 1-3 and 5-8: SQL queries, bootstrap confidence intervals, tests and figures.

Rockets and feathers (question 4) and the discount event study are in rockets_feathers.py.

Outputs:
- reports/tables/<query>.csv          one CSV per named query of sql/03_analysis_queries.sql
- reports/tables/*_ci.csv             medians with bootstrap 95% confidence intervals
- reports/tables/weekday_tests.json   chi-square and fixed-effects regression
- reports/tables/quality_checks.json  SQL vs pandas cross-checks, comparison with official averages
- reports/figures/*.png
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import statsmodels.formula.api as smf
from scipy import stats

from plein_tarif import charts as ch
from plein_tarif.config import ROOT, cfg, events, get_logger, path
from plein_tarif.model import connect_db

log = get_logger(__name__)


# --------------------------------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------------------------------
def load_queries() -> dict[str, str]:
    """Named queries of sql/03_analysis_queries.sql ('-- name: <id>' markers)."""
    text = (ROOT / "sql" / "03_analysis_queries.sql").read_text(encoding="utf-8")
    parts = re.split(r"^-- name: (\w+)\s*$", text, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def run_queries(con) -> dict[str, pd.DataFrame]:
    out = path("figures").parent / "tables"
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, sql in load_queries().items():
        df = con.execute(sql).df()
        df.to_csv(out / f"{name}.csv", index=False)
        results[name] = df
        log.info("query %-32s %6d rows", name, len(df))
    return results


def tables_dir():
    d = path("figures").parent / "tables"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------------------------------
def bootstrap_median_ci(values: np.ndarray, reps: int = 1000, seed: int = 42, alpha: float = 0.05):
    """Median and percentile bootstrap CI (resampling the units, e.g. stations)."""
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    if values.size == 0:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, values.size, size=(reps, values.size))
    meds = np.median(values[idx], axis=1)
    return float(np.median(values)), float(np.quantile(meds, alpha / 2)), float(np.quantile(meds, 1 - alpha / 2))


def station_year_table(con, year: int) -> pd.DataFrame:
    """One row per station and main fuel: mean price over the year, with station attributes."""
    return con.execute("""
        SELECT s.station_key, s.station_id, s.department, s.region, s.city_std, s.is_motorway,
               s.brand, s.brand_category, s.brand_category_label, f.fuel_code,
               count(*) AS n_days, avg(p.price_ttc) AS price_ttc, avg(p.price_ht) AS price_ht
        FROM fact_price_daily p
        JOIN dim_date d USING (date_key) JOIN dim_station s USING (station_key) JOIN dim_fuel f USING (fuel_key)
        WHERE d.year = ? AND f.is_main_fuel
        GROUP BY ALL
        HAVING count(*) >= getvariable('min_days')""", [year]).df()


def grouped_ci(st: pd.DataFrame, group: str, a: dict) -> pd.DataFrame:
    """Median of station yearly means per group and fuel, with bootstrap CI and gap to the national median."""
    rows = []
    for fuel, sub in st.groupby("fuel_code"):
        national = float(np.median(sub["price_ttc"]))
        for g, gsub in sub.groupby(group):
            if len(gsub) < a["min_stations"]:
                continue
            med, lo, hi = bootstrap_median_ci(gsub["price_ttc"].to_numpy(), a["bootstrap_reps"], a["random_seed"])
            rows.append({"fuel_code": fuel, group: g, "n_stations": len(gsub), "median_ttc": med,
                         "ci_low": lo, "ci_high": hi, "gap_vs_national_cts": 100 * (med - national),
                         "gap_ci_low_cts": 100 * (lo - national), "gap_ci_high_cts": 100 * (hi - national)})
    return pd.DataFrame(rows).round(4)


def weekday_tests(con, seed: int = 42) -> dict:
    """Chi-square on the number of changes per weekday; OLS of change size on weekday fixed effects."""
    res = {}
    for fuel in ("Gazole", "E10"):
        counts = con.execute("""
            SELECT d.weekday_num, count(*) AS n FROM fact_price_change c
            JOIN dim_date d USING (date_key) JOIN dim_fuel f USING (fuel_key)
            WHERE f.fuel_code = ? GROUP BY ALL ORDER BY 1""", [fuel]).df()
        days = con.execute("SELECT weekday_num, count(*) AS n FROM dim_date GROUP BY 1 ORDER BY 1").df()
        expected = counts["n"].sum() * days["n"] / days["n"].sum()
        chi2, p = stats.chisquare(counts["n"], expected)
        sample = con.execute("""
            SELECT d.weekday_num, c.change * 100 AS change_cts, c.station_key
            FROM fact_price_change c JOIN dim_date d USING (date_key) JOIN dim_fuel f USING (fuel_key)
            WHERE f.fuel_code = ? USING SAMPLE reservoir(400000 ROWS) REPEATABLE (42)""", [fuel]).df()
        sample["weekday"] = pd.Categorical(sample["weekday_num"], categories=range(1, 8))
        ols = smf.ols("change_cts ~ C(weekday, Treatment(reference=1))", data=sample).fit(
            cov_type="cluster", cov_kwds={"groups": sample["station_key"]})
        increases = sample.assign(up=(sample["change_cts"] > 0).astype(float))
        logit_like = smf.ols("up ~ C(weekday, Treatment(reference=1))", data=increases).fit(
            cov_type="cluster", cov_kwds={"groups": increases["station_key"]})
        res[fuel] = {
            "chi2_counts": round(float(chi2), 1), "chi2_p_value": float(p), "df": 6,
            "ols_n": int(ols.nobs),
            "ols_weekday_coefs_cts_vs_monday": {k.split("[T.")[-1].rstrip("]"): round(v, 3)
                                                for k, v in ols.params.items() if "T." in k},
            "ols_joint_f_p_value": float(ols.f_pvalue),
            "lpm_share_increase_coefs_vs_monday": {k.split("[T.")[-1].rstrip("]"): round(v, 4)
                                                   for k, v in logit_like.params.items() if "T." in k},
            "lpm_joint_f_p_value": float(logit_like.f_pvalue),
        }
    return res


def quality_checks(con, q: dict[str, pd.DataFrame]) -> dict:
    """Re-compute two SQL statistics in pandas, and compare national averages with the ministry's series."""
    out: dict = {}
    raw = con.execute("""
        SELECT d.year_month, p.price_ttc FROM fact_price_daily p JOIN dim_date d USING (date_key)
        JOIN dim_fuel f USING (fuel_key)
        WHERE f.fuel_code = 'Gazole' AND d.year_month IN ('2019-06', '2022-06', '2025-06')""").df()
    pandas_mean = raw.groupby("year_month")["price_ttc"].mean().round(5)
    sql_mean = q["check_national_monthly"].set_index("year_month")["mean_ttc"]
    out["national_mean_gazole"] = {m: {"sql": float(sql_mean[m]), "pandas": float(pandas_mean[m]),
                                       "abs_diff": abs(float(sql_mean[m]) - float(pandas_mean[m]))}
                                   for m in sql_mean.index}
    raw = con.execute("""
        SELECT s.department, p.price_ttc FROM fact_price_daily p JOIN dim_date d USING (date_key)
        JOIN dim_fuel f USING (fuel_key) JOIN dim_station s USING (station_key)
        WHERE f.fuel_code = 'Gazole' AND d.year = getvariable('analysis_year') AND s.department IN ('31', '75')""").df()
    pandas_med = raw.groupby("department")["price_ttc"].median().round(5)
    sql_med = q["check_department_median"].set_index("department")["median_ttc"]
    out["department_median_gazole"] = {d: {"sql": float(sql_med[d]), "pandas": float(pandas_med[d]),
                                           "abs_diff": abs(float(sql_med[d]) - float(pandas_med[d]))}
                                       for d in sql_med.index}

    official = pd.read_parquet(path("processed") / "official_prices.parquet")
    monthly_off = official[official["freq"] == "monthly"].assign(year_month=lambda d: d["date"].dt.strftime("%Y-%m"))
    ours = con.execute("""
        SELECT strftime(date, '%Y-%m') AS year_month, fuel_code, avg(price_ttc_mean) AS ttc_ours,
               avg(price_ttc_median) AS ttc_median_ours
        FROM v_national_daily GROUP BY ALL""").df()
    cmp_ = ours.merge(monthly_off.rename(columns={"fuel": "fuel_code", "ttc": "ttc_official"}),
                      on=["year_month", "fuel_code"])
    cmp_["gap_mean_cts"] = 100 * (cmp_["ttc_ours"] - cmp_["ttc_official"])
    cmp_["gap_median_cts"] = 100 * (cmp_["ttc_median_ours"] - cmp_["ttc_official"])
    cmp_[["year_month", "fuel_code", "ttc_ours", "ttc_median_ours", "ttc_official", "gap_mean_cts",
          "gap_median_cts"]].round(4).to_csv(tables_dir() / "official_comparison_monthly.csv", index=False)
    out["official_monthly_comparison"] = (
        cmp_.groupby("fuel_code")
            .agg(months=("gap_mean_cts", "size"), mean_gap_cts=("gap_mean_cts", "mean"),
                 min_gap_cts=("gap_mean_cts", "min"), max_gap_cts=("gap_mean_cts", "max"),
                 mean_gap_median_cts=("gap_median_cts", "mean"),
                 correlation=("ttc_ours", lambda s: float(np.corrcoef(s, cmp_.loc[s.index, "ttc_official"])[0, 1])))
            .round(3).to_dict("index"))
    out["tax_validation"] = pd.read_csv(path("processed") / "tax_validation.csv").to_dict("records")
    return out


# --------------------------------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------------------------------
def _shade_discounts(fig: go.Figure, label: bool = True) -> None:
    for p in events()["discount_periods"]:
        extra = dict(annotation_text=p["label"], annotation_position="top left",
                     annotation_font=dict(size=11, color=ch.INK_2)) if label else {}
        fig.add_vrect(x0=p["start"], x1=p["end"], fillcolor=ch.SHADE, line_width=0, layer="below", **extra)


def fig_national(nat: pd.DataFrame) -> None:
    """Pump price TTC and pre-tax price vs Brent (all EUR/litre, same axis), weekly averages."""
    w = nat.assign(week=pd.to_datetime(nat["date"]).dt.to_period("W-FRI").dt.end_time.dt.normalize())
    w = w.groupby(["week", "fuel_code"], as_index=False)[["price_ttc_mean", "price_ht_mean", "brent_eur_l"]].mean()
    fig = go.Figure()
    for fuel in ("Gazole", "E10"):
        s = w[w["fuel_code"] == fuel]
        fig.add_scatter(x=s["week"], y=s["price_ttc_mean"], name=f"{ch.FUEL_LABELS[fuel]} TTC",
                        line=dict(color=ch.FUEL_COLORS[fuel], width=2))
        fig.add_scatter(x=s["week"], y=s["price_ht_mean"], name=f"{ch.FUEL_LABELS[fuel]} hors taxes",
                        line=dict(color=ch.FUEL_COLORS[fuel], width=2, dash="dot"))
    b = w[w["fuel_code"] == "Gazole"]
    fig.add_scatter(x=b["week"], y=b["brent_eur_l"], name="Brent (€/L)", line=dict(color=ch.INK_2, width=2))
    for a in events()["annotations"]:
        if a["label"].startswith(("1er confinement", "Invasion")):
            fig.add_vline(x=a["date"], line=dict(color=ch.MUTED, width=1, dash="dash"))
            fig.add_annotation(x=a["date"], y=2.25, text=a["label"], showarrow=False, xanchor="left",
                               font=dict(size=11, color=ch.INK_2))
    _shade_discounts(fig, label=False)
    fig.update_layout(title=ch.title("Prix à la pompe, prix hors taxes et Brent, 2019-2025",
                                     "Moyennes hebdomadaires nationales, € par litre (zone bleue : remise carburant 2022)"),
                      yaxis_title="€ / litre", yaxis_range=[0, 2.35])
    ch.source_note(fig, "Sources : prix-carburants.gouv.fr, FRED (Brent, EUR/USD), calculs Plein Tarif")
    ch.save(fig, "fig01_prix_pompe_vs_brent")


def fig_decomposition(nat: pd.DataFrame) -> None:
    """Stacked monthly composition of the Gazole pump price: pre-tax price, excise, VAT."""
    g = nat[nat["fuel_code"] == "Gazole"].assign(month=lambda d: pd.to_datetime(d["date"]).dt.to_period("M").dt.to_timestamp())
    g = g.assign(vat_amount=g["price_ttc_mean"] * g["vat"] / (1 + g["vat"]))
    m = g.groupby("month", as_index=False)[["price_ttc_mean", "price_ht_mean", "excise", "vat_amount", "discount_ttc"]].mean()
    m["ht_net"] = m["price_ttc_mean"] - m["excise"] - m["vat_amount"]
    fig = go.Figure()
    for col, name, color in (("ht_net", "Prix hors taxes (net de remise)", ch.BLUE),
                             ("excise", "Accise (TICPE)", ch.ORANGE), ("vat_amount", "TVA", ch.AQUA)):
        fig.add_bar(x=m["month"], y=m[col], name=name, marker=dict(color=color, line=dict(color=ch.SURFACE, width=1)))
    fig.update_layout(barmode="stack", bargap=0.08, yaxis_title="€ / litre",
                      title=ch.title("De quoi est fait un litre de gazole ?",
                                     "Moyenne mensuelle nationale : prix hors taxes, accise et TVA, en € par litre"))
    ch.source_note(fig)
    ch.save(fig, "fig02_decomposition_gazole")


def department_names() -> dict[str, str]:
    """Department code -> name, from the departments GeoJSON."""
    gj = json.loads((path("raw") / "geo" / "departements.geojson").read_text(encoding="utf-8"))
    return {f["properties"]["code"]: f["properties"]["nom"] for f in gj["features"]}


def fig_departments(dept: pd.DataFrame, year: int) -> None:
    d = dept[dept["fuel_code"] == "Gazole"].sort_values("gap_vs_national_cts")
    sel = pd.concat([d.head(10), d.tail(10)])
    names = department_names()
    labels = [f"{names.get(c, c)} ({c})" for c in sel["department"].astype(str)]
    fig = go.Figure(go.Bar(
        x=sel["gap_vs_national_cts"], y=labels, orientation="h",
        marker=dict(color=np.where(sel["gap_vs_national_cts"] < 0, ch.BLUE, ch.ORANGE)),
        error_x=dict(type="data", symmetric=False, array=sel["gap_ci_high_cts"] - sel["gap_vs_national_cts"],
                     arrayminus=sel["gap_vs_national_cts"] - sel["gap_ci_low_cts"], color=ch.INK_2, thickness=1),
        text=[f"{v:+.1f}" for v in sel["gap_vs_national_cts"]], textposition="outside",
        hovertemplate="Département %{y}<br>%{x:+.1f} cts/L<extra></extra>"))
    fig.update_layout(title=ch.title(f"Gazole {year} : les 10 départements les moins chers et les 10 plus chers",
                                     "Écart de la médiane départementale à la médiane nationale, centimes par litre (IC 95 % bootstrap)"),
                      xaxis_title="centimes / litre", yaxis=dict(type="category", autorange="reversed"),
                      showlegend=False, margin=dict(l=230, r=60, t=110, b=120))
    ch.source_note(fig, y=-0.15)
    ch.save(fig, "fig03_departements_gazole", height=800)


def fig_regions(reg: pd.DataFrame, year: int) -> None:
    fig = go.Figure()
    order = reg[reg["fuel_code"] == "Gazole"].sort_values("median_ttc")["region"].tolist()
    for i, fuel in enumerate(("Gazole", "E10")):
        r = reg[reg["fuel_code"] == fuel].set_index("region").reindex(order).reset_index()
        fig.add_scatter(x=r["median_ttc"], y=r["region"], mode="markers", name=ch.FUEL_LABELS[fuel],
                        marker=dict(color=ch.FUEL_COLORS[fuel], size=11, line=dict(color=ch.SURFACE, width=2)),
                        error_x=dict(type="data", symmetric=False, array=r["ci_high"] - r["median_ttc"],
                                     arrayminus=r["median_ttc"] - r["ci_low"], color=ch.FUEL_COLORS[fuel], thickness=1.5))
    fig.update_layout(title=ch.title(f"Prix médian par région, {year}",
                                     "Médiane des prix moyens annuels des stations, € par litre (IC 95 % bootstrap)"),
                      xaxis_title="€ / litre", yaxis=dict(autorange="reversed"), hovermode="closest",
                      margin=dict(l=230, r=40, t=120, b=120))
    ch.source_note(fig, y=-0.17)
    ch.save(fig, "fig04_regions", height=700)


def _hist_panels(data: pd.DataFrame, value: str, bin_size: float, medians: dict[str, float], unit: str,
                 title_main: str, subtitle: str, xlabel: str, ylabel: str, name: str, note: str | None = None) -> None:
    """One histogram panel per main fuel (small multiples), with the median marked and labelled."""
    from plotly.subplots import make_subplots

    fuels = ("Gazole", "E10")
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.05,
                        subplot_titles=[ch.FUEL_LABELS[f] for f in fuels])
    for i, fuel in enumerate(fuels, start=1):
        s = data[data["fuel_code"] == fuel]
        fig.add_histogram(x=s[value], xbins=dict(size=bin_size), showlegend=False, name=ch.FUEL_LABELS[fuel],
                          marker=dict(color=ch.FUEL_COLORS[fuel], line=dict(color=ch.SURFACE, width=1.5)),
                          hovertemplate="%{x}<br>%{y} stations<extra></extra>", row=1, col=i)
        if fuel in medians:
            fig.add_vline(x=medians[fuel], line=dict(color=ch.INK, width=1.5, dash="dash"), row=1, col=i)
            fig.add_annotation(x=medians[fuel], yref=f"y{'' if i == 1 else i} domain", y=0.97, xanchor="left",
                               text=f" médiane : {medians[fuel]:.0f} {unit}", showarrow=False,
                               font=dict(size=12, color=ch.INK), row=1, col=i)
    fig.update_xaxes(title_text=xlabel)
    fig.update_yaxes(title_text=ylabel, row=1, col=1)
    fig.update_layout(title=ch.title(title_main, subtitle), hovermode="closest", bargap=0.05,
                      margin=dict(l=80, r=40, t=120, b=100))
    ch.source_note(fig, note or "Source : prix-carburants.gouv.fr (Licence Ouverte), calculs Plein Tarif", y=-0.2)
    ch.save(fig, name, width=1400, height=620)


def fig_motorway(by_station: pd.DataFrame, summary: pd.DataFrame, year: int) -> None:
    med = {f: float(summary[(summary["fuel_code"] == f) & (summary["year"] == year)]["median_premium_cts"].iloc[0])
           for f in ("Gazole", "E10") if len(summary[(summary["fuel_code"] == f) & (summary["year"] == year)])}
    _hist_panels(by_station, "median_premium_cts", 2, med, "cts",
                 f"La prime autoroute, station par station ({year})",
                 "Écart médian entre chaque station d'autoroute et les stations hors autoroute à moins de 10 km, le même jour",
                 "centimes / litre", "stations d'autoroute", "fig05_prime_autoroute")


def fig_brand_categories(cat: pd.DataFrame, year: int) -> None:
    labels = {"supermarket": "Grande distribution", "oil_company": "Pétroliers",
              "independent_other": "Indépendants / autres", "unmatched": "Enseigne non identifiée"}
    fig = go.Figure()
    for fuel in ("Gazole", "E10"):
        c = cat[cat["fuel_code"] == fuel].copy()
        c["label"] = c["brand_category"].map(labels)
        fig.add_bar(x=c["label"], y=c["median_ttc"], name=ch.FUEL_LABELS[fuel],
                    marker=dict(color=ch.FUEL_COLORS[fuel], line=dict(color=ch.SURFACE, width=2)),
                    error_y=dict(type="data", symmetric=False, array=c["ci_high"] - c["median_ttc"],
                                 arrayminus=c["median_ttc"] - c["ci_low"], color=ch.INK_2, thickness=1),
                    text=[f"{v:.3f} €" for v in c["median_ttc"]], textposition="outside")
    lo = cat["median_ttc"].min()
    fig.update_layout(barmode="group", bargap=0.25, yaxis_range=[lo - 0.08, cat["median_ttc"].max() + 0.05],
                      yaxis_title="€ / litre", hovermode="closest",
                      title=ch.title(f"Qui est moins cher ? Prix médian par type d'enseigne ({year})",
                                     "Stations hors autoroute ; médiane des prix moyens annuels (IC 95 % bootstrap). Axe tronqué."))
    ch.source_note(fig, "Sources : prix-carburants.gouv.fr ; enseignes © contributeurs OpenStreetMap (ODbL) ; calculs Plein Tarif")
    ch.save(fig, "fig06_types_enseigne")


def fig_brand_local(local: pd.DataFrame, year: int) -> None:
    g = local[local["fuel_code"] == "Gazole"].sort_values("mean_local_gap_cts")
    fig = go.Figure(go.Bar(
        x=g["mean_local_gap_cts"], y=g["brand"], orientation="h",
        marker=dict(color=[ch.CATEGORY_COLORS.get(c, ch.MUTED) for c in g["brand_category"]]),
        text=[f"{v:+.1f}" for v in g["mean_local_gap_cts"]], textposition="outside", showlegend=False,
        hovertemplate="%{y}<br>%{x:+.1f} cts/L vs voisins<extra></extra>"))
    for cat, label in (("supermarket", "Grande distribution"), ("oil_company", "Pétrolier"),
                       ("independent_other", "Indépendant / autre")):
        fig.add_scatter(x=[None], y=[None], mode="markers", name=label,
                        marker=dict(color=ch.CATEGORY_COLORS[cat], size=12, symbol="square"))
    fig.update_layout(title=ch.title(f"Gazole {year} : écart de chaque enseigne à ses voisines",
                                     "Prix de la station moins le prix médian des autres stations à moins de 5 km, le même jour (cts/L)"),
                      xaxis_title="centimes / litre", yaxis=dict(autorange="reversed"), hovermode="closest",
                      margin=dict(l=230, r=60, t=120, b=120))
    ch.source_note(fig, "Sources : prix-carburants.gouv.fr ; enseignes © contributeurs OpenStreetMap (ODbL) ; calculs Plein Tarif", y=-0.17)
    ch.save(fig, "fig07_enseignes_vs_voisins", height=700)


def fig_weekday(wd: pd.DataFrame) -> None:
    fig = go.Figure()
    for fuel in ("Gazole", "E10"):
        w = wd[wd["fuel_code"] == fuel]
        fig.add_bar(x=w["weekday"], y=w["changes_per_day"], name=ch.FUEL_LABELS[fuel],
                    marker=dict(color=ch.FUEL_COLORS[fuel], line=dict(color=ch.SURFACE, width=2)),
                    customdata=np.c_[100 * w["share_increases"]],
                    hovertemplate="%{x}<br>%{y:,.0f} changements / jour<br>%{customdata[0]:.0f} % de hausses<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.25, hovermode="closest", yaxis_title="changements de prix par jour",
                      title=ch.title("Quand les prix changent-ils ?",
                                     "Nombre moyen de changements de prix par jour calendaire, toutes stations, 2019-2025"))
    ch.source_note(fig)
    ch.save(fig, "fig11_jours_de_semaine")


def fig_savings(con, year: int) -> pd.DataFrame:
    """Distribution of yearly savings per station in the focus city (cheapest within 5 km)."""
    a = cfg()["analysis"]
    df = con.execute("""
        WITH city AS (SELECT station_key FROM dim_station WHERE city_std = getvariable('focus_city')),
        nb AS (SELECT station_key, neighbor_key FROM bridge_station_neighbor WHERE relation = 'nearby'
               UNION ALL SELECT station_key, station_key FROM city),
        cheapest AS (
            SELECT c.station_key, p.date_key, p.fuel_key, min(p.price_ttc) AS cheapest
            FROM city c JOIN nb ON nb.station_key = c.station_key
            JOIN fact_price_daily p ON p.station_key = nb.neighbor_key JOIN dim_date d ON d.date_key = p.date_key
            WHERE d.year = getvariable('analysis_year') GROUP BY ALL)
        SELECT s.station_id, s.brand, f.fuel_code, avg(p.price_ttc - c.cheapest) AS mean_gap, count(*) AS n_days
        FROM fact_price_daily p JOIN cheapest c USING (station_key, date_key, fuel_key)
        JOIN dim_station s USING (station_key) JOIN dim_fuel f USING (fuel_key)
        WHERE f.is_main_fuel GROUP BY ALL HAVING count(*) >= getvariable('min_days')""").df()
    litres = a["km_per_year"] * a["litres_per_100km"] / 100
    df["yearly_savings_eur"] = df["mean_gap"] * litres
    city = cfg()["geography"]["focus_city"]
    med = {f: float(df.loc[df["fuel_code"] == f, "yearly_savings_eur"].median()) for f in ("Gazole", "E10")
           if (df["fuel_code"] == f).any()}
    _hist_panels(df, "yearly_savings_eur", 10, med, "€",
                 f"{city} {year} : combien rapporte d'aller à la station la moins chère à 5 km ?",
                 f"Économie annuelle si l'on quitte chaque station pour la moins chère à moins de 5 km le même jour, "
                 f"{a['km_per_year']:,} km/an à {a['litres_per_100km']} L/100 km".replace(",", " "),
                 "€ par an", "stations", "fig12_economies_toulouse")
    return df


def main() -> None:
    a, year = cfg()["analysis"], cfg()["period"]["end_year"]
    con = connect_db()
    q = run_queries(con)
    tdir = tables_dir()

    st = station_year_table(con, year)
    st.to_csv(tdir / "station_year_main_fuels.csv", index=False)
    dept = grouped_ci(st, "department", a)
    dept.to_csv(tdir / "department_median_ci.csv", index=False)
    reg = grouped_ci(st, "region", a)
    reg.to_csv(tdir / "region_median_ci.csv", index=False)
    road = st[~st["is_motorway"]]
    cat = grouped_ci(road, "brand_category", a)
    cat.to_csv(tdir / "brand_category_median_ci.csv", index=False)
    brand = grouped_ci(road, "brand", a)
    brand.to_csv(tdir / "brand_median_ci.csv", index=False)

    (tdir / "weekday_tests.json").write_text(json.dumps(weekday_tests(con), indent=2))
    (tdir / "quality_checks.json").write_text(json.dumps(quality_checks(con, q), indent=2, default=float))

    nat = con.execute("SELECT * FROM v_national_daily WHERE fuel_code IN ('Gazole', 'E10') ORDER BY date").df()
    fig_national(nat)
    fig_decomposition(nat)
    fig_departments(dept, year)
    fig_regions(reg, year)
    fig_motorway(q["q2_motorway_premium_by_station"], q["q2_motorway_premium_summary"], year)
    if cat["brand_category"].nunique() > 1:
        fig_brand_categories(cat[cat["brand_category"] != "unmatched"], year)
        fig_brand_local(q["q3_brand_local_gap"], year)
    fig_weekday(q["q7_weekday_changes"])
    fig_savings(con, year).to_csv(tdir / "savings_focus_city_by_station.csv", index=False)
    con.close()


if __name__ == "__main__":
    main()
