"""Rockets and feathers: do pre-tax pump prices follow Brent as fast when it falls as when it rises?

Model, per main fuel, on weekly national averages (week ending Friday), in cents per litre:

    dp_t = a + sum_{i=0..k} b+_i dB+_{t-i} + sum_{i=0..k} b-_i dB-_{t-i} + sum_{j=1..m} g_j dp_{t-j} + e_t

- p: national average pre-tax price (distributor price, the 2022 discount added back), cts/l;
- B: Brent in cts/l (USD/bbl converted with EUR/USD, 158.987 l per barrel);
- dB+ = max(dB, 0), dB- = min(dB, 0); Newey-West (HAC) standard errors;
- k is chosen by BIC over 1..max_lag on a common estimation sample.

Cumulative response to a one-off permanent +1 ct (resp. -1 ct) move in Brent, h = 0..horizon weeks:
C+_h = sum_{s<=h} dp_s with dB_0 = +1; the fall is reported as the magnitude of the price decrease so both
curves are comparable. 95% bands come from 10,000 parameter draws from N(theta, V_HAC).
Tests: Wald tests of b+_0 = b-_0 (immediate), sum b+ = sum b- (cumulative over k weeks, equivalent to
long-run symmetry), and simulated tests of C+_h = C-_h at h = 1, 2, 4.

Limitation: Brent is crude oil. Wholesale diesel/gasoline prices (refined products) are not freely
available, and refining margins moved a lot in 2022; the gap partly reflects those margins.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import statsmodels.api as sm

from plein_tarif import charts as ch
from plein_tarif.analyze import load_queries, tables_dir
from plein_tarif.config import cfg, events, get_logger, path
from plein_tarif.model import connect_db

log = get_logger(__name__)
N_DRAWS = 10_000


def design(df: pd.DataFrame, k: int, m: int, start: int) -> tuple[pd.Series, pd.DataFrame]:
    """Regressors of the asymmetric ADL model; rows from `start` (common sample across k)."""
    dB = df["brent"].diff()
    dp = df["price"].diff()
    X = pd.DataFrame(index=df.index)
    for i in range(k + 1):
        X[f"up_{i}"] = dB.clip(lower=0).shift(i)
        X[f"down_{i}"] = dB.clip(upper=0).shift(i)
    for j in range(1, m + 1):
        X[f"ar_{j}"] = dp.shift(j)
    X = sm.add_constant(X)
    return dp.iloc[start:], X.iloc[start:]


def fit(df: pd.DataFrame, k: int, m: int, start: int, hac_lags: int, keep: pd.Series | None = None):
    """OLS with Newey-West errors; `keep` (bool by date) drops observations, e.g. the 2022 crisis."""
    y, X = design(df, k, m, start)
    if keep is not None:
        y, X = y[keep.reindex(y.index).to_numpy()], X[keep.reindex(X.index).to_numpy()]
    return sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})


def select_k(df: pd.DataFrame, max_lag: int, m: int, hac_lags: int,
             keep: pd.Series | None = None) -> tuple[int, pd.DataFrame]:
    start = max_lag + m + 1
    rows = []
    for k in range(1, max_lag + 1):
        r = fit(df, k, m, start, hac_lags, keep)
        rows.append({"k": k, "bic": r.bic, "aic": r.aic, "nobs": int(r.nobs)})
    table = pd.DataFrame(rows)
    return int(table.loc[table["bic"].idxmin(), "k"]), table


def cumulative_response(params: np.ndarray, names: list[str], k: int, m: int, horizon: int, sign: int) -> np.ndarray:
    """Cumulative price response (cts) to a permanent Brent move of `sign` cts at t = 0."""
    p = dict(zip(names, params))
    b = [p[f"{'up' if sign > 0 else 'down'}_{i}"] for i in range(k + 1)]
    g = [p[f"ar_{j}"] for j in range(1, m + 1)]
    dp = np.zeros(horizon + 1)
    for s in range(horizon + 1):
        direct = b[s] * sign if s <= k else 0.0
        dp[s] = direct + sum(g[j - 1] * dp[s - j] for j in range(1, m + 1) if s - j >= 0)
    return np.cumsum(dp) * sign       # falls reported as positive magnitudes


def analyse_fuel(df: pd.DataFrame, fuel: str, rf: dict, seed: int, keep: pd.Series | None = None) -> dict:
    m, hac = rf["ar_lags"], rf["hac_maxlags"]
    k, bic_table = select_k(df, rf["max_lag"], m, hac, keep)
    res = fit(df, k, m, rf["max_lag"] + m + 1, hac, keep)
    names = list(res.params.index)
    H = rf["horizon"]
    up = cumulative_response(res.params.values, names, k, m, H, +1)
    down = cumulative_response(res.params.values, names, k, m, H, -1)
    rng = np.random.default_rng(seed)
    draws = rng.multivariate_normal(res.params.values, res.cov_params().values, size=N_DRAWS)
    sims_up = np.array([cumulative_response(d, names, k, m, H, +1) for d in draws])
    sims_down = np.array([cumulative_response(d, names, k, m, H, -1) for d in draws])
    diff = sims_up - sims_down

    ups = " + ".join(f"up_{i}" for i in range(k + 1))
    downs = " + ".join(f"down_{i}" for i in range(k + 1))
    w_immediate = res.wald_test("up_0 = down_0", scalar=True)
    w_cum = res.wald_test(f"{ups} = {downs}", scalar=True)
    tests = {
        "immediate_b+0=b-0": {"stat": float(w_immediate.statistic), "p_value": float(w_immediate.pvalue)},
        f"cumulative_sum_over_{k}_lags": {"stat": float(w_cum.statistic), "p_value": float(w_cum.pvalue)},
    }
    for h in (1, 2, 4):
        if h <= H:
            p_two = 2 * min((diff[:, h] <= 0).mean(), (diff[:, h] >= 0).mean())
            tests[f"C+_{h}=C-_{h}_simulated"] = {"difference_cts": float(up[h] - down[h]), "p_value": float(p_two)}

    resp = pd.DataFrame({
        "fuel_code": fuel, "horizon_weeks": np.tile(np.arange(H + 1), 2),
        "direction": ["hausse"] * (H + 1) + ["baisse"] * (H + 1),
        "cumulative_response": np.r_[up, down],
        "ci_low": np.r_[np.quantile(sims_up, 0.025, axis=0), np.quantile(sims_down, 0.025, axis=0)],
        "ci_high": np.r_[np.quantile(sims_up, 0.975, axis=0), np.quantile(sims_down, 0.975, axis=0)],
    })
    (tables_dir() / f"rf_model_{fuel}.txt").write_text(res.summary().as_text())
    bic_table.to_csv(tables_dir() / f"rf_lag_selection_{fuel}.csv", index=False)
    half = lambda c: int(np.argmax(c >= 0.5 * c[-1])) if c[-1] > 0 else None  # noqa: E731
    return {
        "fuel": fuel, "k_selected_bic": k, "ar_lags": m, "nobs": int(res.nobs), "r2": float(res.rsquared),
        "sample": [str(df.index[rf["max_lag"] + m + 1].date()), str(df.index[-1].date())],
        "excluded_weeks": int((~keep).sum()) if keep is not None else 0,
        "cum_up": {h: round(float(up[h]), 3) for h in range(H + 1)},
        "cum_down": {h: round(float(down[h]), 3) for h in range(H + 1)},
        "weeks_to_half_of_final_response": {"hausse": half(up), "baisse": half(down)},
        "tests": tests, "response": resp,
    }


def weekly_series(con) -> pd.DataFrame:
    w = con.execute(load_queries()["q4_weekly_national"]).df()
    w["week_end"] = pd.to_datetime(w["week_end"])
    # drop incomplete first/last weeks (fewer than 7 days)
    return w[w["n_days"] == 7]


def fig_response(res_all: dict[str, dict], fuel: str, name: str) -> None:
    """Small multiples: cumulative responses to a rise vs a fall, full period vs excluding 2022."""
    from plotly.subplots import make_subplots

    panels = [(fuel, "2019-2025"), (f"{fuel}_hors_2022", "2019-2025 hors 2022")]
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.06,
                        subplot_titles=[p[1] for p in panels])
    bands = {"hausse": "rgba(235,104,52,0.16)", "baisse": "rgba(42,120,214,0.16)"}
    for col, (key, _) in enumerate(panels, start=1):
        resp = res_all[key]["response"]
        for direction, color, label in (("hausse", ch.RISE, "Brent en hausse (+1 ct)"),
                                        ("baisse", ch.FALL, "Brent en baisse (-1 ct)")):
            r = resp[resp["direction"] == direction]
            fig.add_scatter(x=np.r_[r["horizon_weeks"], r["horizon_weeks"][::-1]],
                            y=np.r_[r["ci_high"], r["ci_low"][::-1]], mode="lines", fill="toself",
                            fillcolor=bands[direction], line=dict(width=0), hoverinfo="skip",
                            showlegend=False, row=1, col=col)
            fig.add_scatter(x=r["horizon_weeks"], y=r["cumulative_response"], mode="lines+markers",
                            name=label, legendgroup=direction, showlegend=(col == 1),
                            line=dict(color=color, width=3), marker=dict(size=8, line=dict(color=ch.SURFACE, width=2)),
                            hovertemplate=f"{label}<br>semaine %{{x}} : %{{y:.2f}} ct<extra></extra>", row=1, col=col)
            for h in (1, int(r["horizon_weeks"].max())):
                v = float(r.loc[r["horizon_weeks"] == h, "cumulative_response"].iloc[0])
                fig.add_annotation(x=h, y=v, text=f"{v:.2f}", showarrow=False, yshift=14 if direction == "hausse" else -14,
                                   font=dict(size=12, color=ch.INK), row=1, col=col)
        fig.add_hline(y=1, line=dict(color=ch.MUTED, width=1, dash="dash"), row=1, col=col)
        p1 = res_all[key]["tests"]["C+_1=C-_1_simulated"]["p_value"]
        fig.add_annotation(x=8, y=0.05, xanchor="right", showarrow=False, row=1, col=col,
                           text=f"écart à 1 semaine : p = {p1:.3f}", font=dict(size=12, color=ch.INK_2))
    full = res_all[fuel]
    sub = (f"Réponse cumulée du prix hors taxes à un mouvement de 1 centime/L du Brent. Après 1 semaine : "
           f"+{full['cum_up'][1]:.2f} ct à la hausse, -{full['cum_down'][1]:.2f} ct à la baisse (bandes : IC 95 %)")
    fig.update_xaxes(title_text="semaines après le mouvement du Brent", dtick=1, range=[-0.3, 8.4])
    fig.update_yaxes(title_text="centimes par litre", row=1, col=1)
    fig.update_layout(title=ch.title(f"{ch.FUEL_LABELS[fuel]} : les prix montent-ils plus vite qu'ils ne baissent ?", sub),
                      hovermode="closest", margin=dict(l=80, r=40, t=170, b=120),
                      legend=dict(y=1.13, yanchor="bottom"))
    ch.source_note(fig, "Modèle ADL asymétrique sur moyennes hebdomadaires, erreurs Newey-West ; ligne pointillée : "
                        "transmission complète. Sources : prix-carburants.gouv.fr, FRED ; calculs Plein Tarif", y=-0.25)
    ch.save(fig, name, width=1400, height=700)


def event_study(con, window: int) -> pd.DataFrame:
    """Daily national pre-tax and pump prices around each discount change, relative to day -1."""
    ev = con.execute("""
        SELECT date AS event_date, discount_ht, LAG(discount_ht) OVER (ORDER BY date) AS old
        FROM dim_date QUALIFY discount_ht <> LAG(discount_ht) OVER (ORDER BY date)""").df()
    nat = con.execute("""SELECT date, fuel_code, price_ttc_mean, price_ht_mean, brent_eur_l
                         FROM v_national_daily WHERE fuel_code IN ('Gazole', 'E10')""").df()
    nat["date"] = pd.to_datetime(nat["date"])
    nat = nat.sort_values(["fuel_code", "date"])
    out = []
    for _, e in ev.iterrows():
        d0 = pd.Timestamp(e["event_date"])
        w = nat[(nat["date"] >= d0 - pd.Timedelta(days=window)) & (nat["date"] <= d0 + pd.Timedelta(days=window))].copy()
        w["rel_day"] = (w["date"] - d0).dt.days
        base = w[w["rel_day"] == -1].set_index("fuel_code")
        for col in ("price_ttc_mean", "price_ht_mean", "brent_eur_l"):
            w[f"{col}_vs_day_minus1_cts"] = 100 * (w[col] - w["fuel_code"].map(base[col]))
        w["event_date"] = d0.date()
        w["discount_change_ht"] = e["discount_ht"] - e["old"]
        out.append(w)
    return pd.concat(out, ignore_index=True)


def fig_event_study(es: pd.DataFrame) -> None:
    from plotly.subplots import make_subplots

    evs = sorted(es["event_date"].unique())
    titles = []
    for e in evs:
        chg = es.loc[es["event_date"] == e, "discount_change_ht"].iloc[0]
        titles.append(f"{pd.Timestamp(e):%d/%m/%Y} : remise {'+' if chg > 0 else ''}{100 * chg:.1f} cts HT")
    fig = make_subplots(rows=1, cols=len(evs), subplot_titles=titles, shared_yaxes=True, horizontal_spacing=0.03)
    for i, e in enumerate(evs, start=1):
        s = es[(es["event_date"] == e) & (es["fuel_code"] == "Gazole")]
        for col, name, color, dash in (("price_ttc_mean_vs_day_minus1_cts", "Prix pompe TTC", ch.ORANGE, "solid"),
                                       ("price_ht_mean_vs_day_minus1_cts", "Prix HT distributeur", ch.BLUE, "solid"),
                                       ("brent_eur_l_vs_day_minus1_cts", "Brent", ch.INK_2, "dot")):
            fig.add_scatter(x=s["rel_day"], y=s[col], name=name, legendgroup=name, showlegend=(i == 1),
                            line=dict(color=color, width=2, dash=dash), row=1, col=i)
        fig.add_vline(x=0, line=dict(color=ch.MUTED, width=1, dash="dash"), row=1, col=i)
    fig.update_yaxes(title_text="écart au jour -1 (cts/L)", row=1, col=1)
    fig.update_xaxes(title_text="jours", dtick=7)
    fig.update_layout(title=ch.title("Gazole : la remise de 2022 a-t-elle été répercutée ?",
                                     "Prix moyen national autour de chaque début, changement et fin de la remise (jour 0)"),
                      hovermode="x unified", margin=dict(l=70, r=30, t=170, b=110),
                      legend=dict(y=1.16, yanchor="bottom"))
    ch.source_note(fig, y=-0.25)
    ch.save(fig, "fig10_etude_evenement_remise", width=1400, height=620)


def fig_crisis(con) -> None:
    nat = con.execute("""SELECT date, fuel_code, price_ttc_mean, price_ht_mean, brent_eur_l FROM v_national_daily
                         WHERE fuel_code = 'Gazole' AND date BETWEEN DATE '2021-10-01' AND DATE '2023-03-31'
                         ORDER BY date""").df()
    fig = go.Figure()
    fig.add_scatter(x=nat["date"], y=nat["price_ttc_mean"], name="Prix pompe TTC (net de remise)", line=dict(color=ch.ORANGE, width=2))
    fig.add_scatter(x=nat["date"], y=nat["price_ht_mean"], name="Prix HT distributeur (avant remise)", line=dict(color=ch.BLUE, width=2))
    fig.add_scatter(x=nat["date"], y=nat["brent_eur_l"], name="Brent (€/L)", line=dict(color=ch.INK_2, width=2, dash="dot"))
    for p in events()["discount_periods"]:
        fig.add_vrect(x0=p["start"], x1=p["end"], fillcolor=ch.SHADE, line_width=0, layer="below",
                      annotation_text=p["label"], annotation_position="top left", annotation_font=dict(size=11, color=ch.INK_2))
    for a in events()["annotations"]:
        if a["label"] in ("Invasion de l'Ukraine", "Grèves des raffineries"):
            fig.add_vline(x=a["date"], line=dict(color=ch.MUTED, width=1, dash="dash"))
            fig.add_annotation(x=a["date"], y=0.25, text=a["label"], showarrow=False, xanchor="left",
                               font=dict(size=11, color=ch.INK_2))
    fig.update_layout(title=ch.title("La crise de 2022 au jour le jour (gazole)",
                                     "Moyennes nationales quotidiennes, € par litre ; zones bleues : remise carburant de l'État"),
                      yaxis_title="€ / litre", yaxis_range=[0, 2.3])
    ch.source_note(fig, "Sources : prix-carburants.gouv.fr, FRED ; calculs Plein Tarif")
    ch.save(fig, "fig09_crise_2022")


def main() -> None:
    rf, seed = cfg()["analysis"]["rockets_feathers"], cfg()["analysis"]["random_seed"]
    con = connect_db()
    weekly = weekly_series(con)
    results, summary, res_all = [], {}, {}
    for fuel in ("Gazole", "E10"):
        w = weekly[weekly["fuel_code"] == fuel].set_index("week_end").sort_index()
        df = pd.DataFrame({"price": 100 * w["price_ht"], "brent": 100 * w["brent_eur_l"]})
        r = analyse_fuel(df, fuel, rf, seed)
        # robustness: same model without observations whose current or lagged weeks fall in 2022
        # (differences and lags are built on the full series, then those rows are dropped)
        lag_weeks = rf["max_lag"] + rf["ar_lags"] + 1
        keep = pd.Series((df.index < "2022-01-01") | (df.index >= pd.Timestamp("2023-01-01") + pd.Timedelta(weeks=lag_weeks)),
                         index=df.index)
        r_ex = analyse_fuel(df, f"{fuel}_hors_2022", rf, seed, keep)
        results += [r, r_ex]
        res_all[fuel], res_all[f"{fuel}_hors_2022"] = r, r_ex
        summary[fuel] = {k_: v for k_, v in r.items() if k_ != "response"}
        summary[f"{fuel}_hors_2022"] = {k_: v for k_, v in r_ex.items() if k_ != "response"}
    pd.concat([r["response"] for r in results]).round(4).to_csv(tables_dir() / "rf_cumulative_response.csv", index=False)

    margin = pd.read_parquet(path("processed") / "refining_margin.parquet")
    margin["year"] = margin["month"].dt.year
    summary["refining_margin_usd_bbl_by_year"] = margin.groupby("year")["margin_usd_bbl"].mean().round(2).to_dict()
    (tables_dir() / "rf_results.json").write_text(json.dumps(summary, indent=2, default=str))
    fig_response(res_all, "Gazole", "fig08_rockets_feathers")
    fig_response(res_all, "E10", "fig08b_rockets_feathers_sp95e10")

    es = event_study(con, cfg()["analysis"]["event_window_days"])
    es.to_csv(tables_dir() / "event_study_daily.csv", index=False, float_format="%.5f")
    fig_event_study(es)
    fig_crisis(con)
    con.close()
    for f in ("Gazole", "E10"):
        s = summary[f]
        log.info("%s: k=%s, C+1=%.2f C-1=%.2f, C+8=%.2f C-8=%.2f, tests=%s", f, s["k_selected_bic"], s["cum_up"][1],
                 s["cum_down"][1], s["cum_up"][rf["horizon"]], s["cum_down"][rf["horizon"]],
                 {t: round(v["p_value"], 4) for t, v in s["tests"].items()})


if __name__ == "__main__":
    main()
