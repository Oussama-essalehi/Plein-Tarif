"""Generate the Power BI Project (PleinTarif.pbip): semantic model in TMDL, report in PBIR.

Run from the repository root after `python run.py export`:
    python powerbi/tools/generate_pbip.py
Column lists and types are read from data/gold/*.csv, so the model always matches the exported files.
Identifiers (lineage tags, relationship ids) are deterministic UUIDs: re-running the script gives the same files.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "data" / "gold"
PBI = ROOT / "powerbi"
SM = PBI / "PleinTarif.SemanticModel"
RP = PBI / "PleinTarif.Report"
NS = uuid.UUID("6b1f5f0e-8f43-4c55-9d3c-2f0d6c1d7a10")
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
VERSIONS = {"report": "3.3.0", "page": "2.1.0", "visualContainer": "2.13.0", "pagesMetadata": "1.1.0",
            "versionMetadata": "1.0.0", "definitionProperties": "2.0.0", "semanticModel": "1.0.0", "pbip": "1.0.0"}
DEFAULT_FOLDER = "C:\\plein-tarif\\data\\gold\\"


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def q(name: str) -> str:
    """Quote a TMDL object name when needed."""
    return name if name.replace("_", "").isalnum() and name.isascii() else "'" + name.replace("'", "''") + "'"


# --------------------------------------------------------------------------------------------------
# Semantic model
# --------------------------------------------------------------------------------------------------
TEXT_COLUMNS = {"department", "postcode", "region_code", "year_month", "label", "event_type"}
DATE_COLUMNS = {("dim_date", "date"), ("dim_event", "start_date"), ("dim_event", "end_date")}
HIDDEN = {"date_key", "fuel_key", "station_key", "month_date_key", "year_date_key", "event_key", "price_ttc_sum",
          "n_station_days"}
SORT_BY = {("dim_date", "month_name"): "month", ("dim_date", "weekday_name"): "weekday_num",
           ("dim_fuel", "fuel_label"): "display_order"}
DESCRIPTIONS = {
    "dim_date": "Calendrier quotidien 2019-2025 (jours fériés, remise carburant en vigueur, événements). Table de dates.",
    "dim_fuel": "Carburants : Gazole et SP95-E10 (principaux), SP98, SP95, E85, GPLc.",
    "dim_station": "Points de vente (métropole + Corse) avec enseigne OpenStreetMap (ODbL) quand elle a pu être rattachée.",
    "dim_department": "Départements, régions et libellé géographique pour les cartes.",
    "dim_market": "Brent (€/L), EUR/USD, accise, TVA et remise de l'État, par jour et carburant (taux métropole).",
    "dim_event": "Périodes de remise et événements marquants (annotations).",
    "fact_national_daily": "Moyenne et médiane nationales quotidiennes par carburant (TTC et HT).",
    "fact_department_month": "Statistiques mensuelles par département et carburant (sommes pour moyennes pondérées).",
    "fact_brand_category_month": "Statistiques mensuelles par type d'enseigne, type de route et carburant.",
    "fact_station_year": "Moyennes annuelles par station et carburant ; écarts à la médiane départementale, à la station la moins chère à 5 km et, pour l'autoroute, aux stations à moins de 10 km.",
    "fact_rockets_feathers": "Réponses cumulées du prix HT à une hausse / baisse de 1 ct/L du Brent (modèle ADL asymétrique).",
}
FORMATS = {"price": '#,0.000 "€"', "cts": '+0.0" cts";-0.0" cts";0.0" cts"', "int": "#,0"}


def column_spec(table: str, col: str, dtype) -> tuple[str, str, str | None]:
    """(M type, TMDL dataType, formatString) for one CSV column."""
    if (table, col) in DATE_COLUMNS:
        return "type date", "dateTime", "dd/mm/yyyy"
    if dtype is bool or pd.api.types.is_bool_dtype(dtype):
        return "type logical", "boolean", '"""TRUE"";""TRUE"";""FALSE"""'
    if col in TEXT_COLUMNS or not pd.api.types.is_numeric_dtype(dtype):
        return "type text", "string", None
    if pd.api.types.is_integer_dtype(dtype):
        return "Int64.Type", "int64", "0" if col.endswith("_key") or col in ("year", "month") else "#,0"
    if "price" in col or col in ("brent_eur_l", "excise", "discount_ht", "discount_ttc", "ci_low", "ci_high",
                                 "cumulative_response", "amount_ht", "amount_ttc"):
        return "type number", "double", "#,0.000"
    return "type number", "double", "#,0.00"


def table_tmdl(table: str) -> str:
    csv = GOLD / f"{table}.csv"
    df = pd.read_csv(csv, nrows=5000, dtype={c: str for c in TEXT_COLUMNS})
    bool_cols = [c for c in df.columns if df[c].dropna().astype(str).str.lower().isin(["true", "false"]).all()
                 and df[c].notna().any()]
    lines = [f"/// {DESCRIPTIONS.get(table, table)}", f"table {q(table)}", f"\tlineageTag: {tag(table)}"]
    if table == "dim_date":
        lines.append("\tdataCategory: Time")
    lines.append("")
    types = []
    for col in df.columns:
        dtype = bool if col in bool_cols else df[col].dtype
        mtype, dtype_tmdl, fmt = column_spec(table, col, dtype)
        types.append((col, mtype))
        lines.append(f"\tcolumn {q(col)}")
        lines.append(f"\t\tdataType: {dtype_tmdl}")
        if table == "dim_date" and col == "date":
            lines.append("\t\tisKey")
        if fmt and dtype_tmdl != "boolean":
            lines.append(f"\t\tformatString: {fmt}")
        if col in HIDDEN:
            lines.append("\t\tisHidden")
        lines.append(f"\t\tlineageTag: {tag(table, col)}")
        lines.append("\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {col}")
        if (table, col) in SORT_BY:
            lines.append(f"\t\tsortByColumn: {SORT_BY[(table, col)]}")
        if col == "map_location":
            lines.append("\t\tdataCategory: StateOrProvince")
        if col in ("lat",):
            lines.append("\t\tdataCategory: Latitude")
        if col in ("lon",):
            lines.append("\t\tdataCategory: Longitude")
        lines += ["", "\t\tannotation SummarizationSetBy = Automatic", ""]
    type_list = ", ".join(f'{{"{c}", {t}}}' for c, t in types)
    lines += [
        f"\tpartition {q(table)} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        f'\t\t\t\t    Source = Csv.Document(File.Contents(DataFolder & "{table}.csv"), [Delimiter=",", Columns={len(df.columns)}, Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
        "\t\t\t\t    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
        "\t\t\t\t    // culture en-US: the CSV files use '.' as decimal separator whatever the Windows locale",
        f'\t\t\t\t    Typed = Table.TransformColumnTypes(Promoted, {{{type_list}}}, "en-US")',
        "\t\t\t\tin",
        "\t\t\t\t    Typed",
        "",
        "\tannotation PBI_ResultType = Table",
        "",
    ]
    return "\n".join(lines)


# Measures: (name, folder, format, description, DAX lines)
YEAR_SCOPE = "REMOVEFILTERS ( dim_date ), TREATAS ( VALUES ( dim_date[year] ), fact_station_year[year] )"
# station-years with at least [Seuil Jours] days of prices (same rule as the SQL / Python analysis)
DAYS_RULE = "KEEPFILTERS ( FILTER ( ALL ( fact_station_year[n_days] ), fact_station_year[n_days] >= [Seuil Jours] ) )"
SCOPE = YEAR_SCOPE + ", " + DAYS_RULE
MEASURES: list[tuple[str, str, str, str, list[str]]] = [
    ("Seuil Stations", "Échantillon", "0", "Nombre minimal de stations pour publier une statistique (config.yaml : min_stations).",
     ["10"]),
    ("Seuil Jours", "Échantillon", "0", "Nombre minimal de jours pour publier une statistique (config.yaml : min_days).",
     ["30"]),
    ("Nb Stations", "Échantillon", "#,0",
     "Nombre de stations distinctes ayant au moins [Seuil Jours] jours de prix sur les années sélectionnées (respecte les filtres géographiques, enseigne et carburant).",
     ["CALCULATE (", "    DISTINCTCOUNT ( fact_station_year[station_key] ),", f"    {YEAR_SCOPE},", f"    {DAYS_RULE}", ")"]),
    ("Nb Jours", "Échantillon", "#,0",
     "Nombre de jours couverts par les données mensuelles dans le contexte (somme sur les mois du maximum de jours observés).",
     ["SUMX (", "    VALUES ( dim_date[year_month] ),", "    CALCULATE ( MAX ( fact_department_month[n_days] ) )", ")"]),
    ("Échantillon Suffisant", "Échantillon", '"""Oui"";""Oui"";""Non"""',
     "VRAI si le groupe compte au moins [Seuil Stations] stations et [Seuil Jours] jours (ou toute la période si elle est plus courte). Les statistiques renvoient BLANK sinon.",
     ["VAR joursPeriode = COUNTROWS ( dim_date )",
      "RETURN",
      "    [Nb Stations] >= [Seuil Stations]",
      "        && [Nb Jours] >= MIN ( [Seuil Jours], joursPeriode )"]),
    ("Prix Moyen TTC", "Prix", FORMATS["price"],
     "Prix moyen à la pompe (TTC, net de la remise 2022) sur les stations-jours du contexte, pondéré par le nombre de stations-jours. BLANK sous le seuil d'échantillon.",
     ["IF (", "    [Échantillon Suffisant],",
      "    DIVIDE ( SUM ( fact_department_month[price_ttc_sum] ), SUM ( fact_department_month[n_station_days] ) )", ")"]),
    ("Prix Médian TTC", "Prix", FORMATS["price"],
     "Médiane, sur les stations, du prix moyen annuel TTC (chaque station compte une fois). À utiliser au niveau année. BLANK sous le seuil d'échantillon.",
     ["IF (", "    [Échantillon Suffisant],",
      "    CALCULATE (", "        MEDIAN ( fact_station_year[price_ttc_mean] ),", f"        {YEAR_SCOPE},", f"        {DAYS_RULE}", "    )", ")"]),
    ("Prix Moyen HT", "Prix", FORMATS["price"],
     "Prix moyen hors taxes du distributeur (avant accise, TVA et remise de l'État), pondéré par les stations-jours. BLANK sous le seuil d'échantillon.",
     ["IF (", "    [Échantillon Suffisant],", "    DIVIDE (",
      "        SUMX ( fact_department_month, fact_department_month[price_ht_mean] * fact_department_month[n_station_days] ),",
      "        SUM ( fact_department_month[n_station_days] )", "    )", ")"]),
    ("Prix National TTC", "Prix", FORMATS["price"],
     "Moyenne nationale quotidienne du prix TTC (métropole + Corse), moyennée sur les jours du contexte. Ne réagit pas aux filtres géographiques.",
     ["AVERAGE ( fact_national_daily[price_ttc_mean] )"]),
    ("Prix National HT", "Prix", FORMATS["price"],
     "Moyenne nationale quotidienne du prix hors taxes du distributeur (remise 2022 réintégrée).",
     ["AVERAGE ( fact_national_daily[price_ht_mean] )"]),
    ("Brent €/L", "Marché", FORMATS["price"],
     "Cours du Brent converti en euros par litre (USD/baril ÷ EUR/USD ÷ 158,987), moyenne sur les jours du contexte.",
     ["AVERAGE ( dim_market[brent_eur_l] )"]),
    ("Part Taxes %", "Marché", "0.0 %",
     "Part de l'accise (taux métropole) et de la TVA dans le prix national TTC.",
     ["VAR ttc = [Prix National TTC]", "VAR tva = AVERAGE ( dim_market[vat] )", "VAR accise = AVERAGE ( dim_market[excise] )",
      "RETURN", "    DIVIDE ( accise + ttc * tva / ( 1 + tva ), ttc )"]),
    ("Prix N-1", "Prix", FORMATS["price"],
     "[Prix Moyen TTC] sur la même période un an plus tôt.",
     ["CALCULATE ( [Prix Moyen TTC], SAMEPERIODLASTYEAR ( dim_date[date] ) )"]),
    ("Évolution Prix %", "Prix", "+0.0 %;-0.0 %;0.0 %",
     "Variation de [Prix Moyen TTC] par rapport à la même période de l'année précédente.",
     ["DIVIDE ( [Prix Moyen TTC] - [Prix N-1], [Prix N-1] )"]),
    ("Médiane Nationale TTC", "Écarts", FORMATS["price"],
     "[Prix Médian TTC] sans aucun filtre géographique ni d'enseigne (référence nationale).",
     ["CALCULATE ( [Prix Médian TTC], REMOVEFILTERS ( dim_department ), REMOVEFILTERS ( dim_station ) )"]),
    ("Écart vs Médiane Nationale (cts)", "Écarts", FORMATS["cts"],
     "Écart en centimes par litre entre la médiane du contexte (département, région, enseigne...) et la médiane nationale.",
     ["VAR m = [Prix Médian TTC]", "RETURN", "    IF ( NOT ISBLANK ( m ), 100 * ( m - [Médiane Nationale TTC] ) )"]),
    ("Prime Autoroute (cts)", "Écarts", FORMATS["cts"],
     "Médiane, sur les stations d'autoroute, de leur écart quotidien médian aux stations hors autoroute à moins de 10 km le même jour (comparaison appariée). BLANK sous [Seuil Stations].",
     ["VAR n =", "    CALCULATE (", "        COUNTROWS ( fact_station_year ),", f"        {YEAR_SCOPE},",
      "        NOT ISBLANK ( fact_station_year[motorway_premium_10km_cts] )", "    )",
      "RETURN", "    IF (", "        n >= [Seuil Stations],",
      "        CALCULATE ( MEDIAN ( fact_station_year[motorway_premium_10km_cts] ), " + YEAR_SCOPE + " )", "    )"]),
    ("Écart Enseigne (cts)", "Écarts", FORMATS["cts"],
     "Écart moyen (cts/L) des stations du contexte à la médiane de leur département le même jour : neutralise l'effet géographique pour comparer les enseignes. BLANK sous [Seuil Stations].",
     ["IF (", "    [Nb Stations] >= [Seuil Stations],",
      "    CALCULATE ( AVERAGE ( fact_station_year[gap_vs_department_median_cts] ), " + SCOPE + " )", ")"]),
    ("Rang Département", "Écarts", "0",
     "Rang du département par [Prix Médian TTC] parmi les départements sélectionnés (1 = le moins cher).",
     ["IF (", "    HASONEVALUE ( dim_department[department] ) && NOT ISBLANK ( [Prix Médian TTC] ),",
      "    RANKX (", "        FILTER ( ALLSELECTED ( dim_department[department] ), NOT ISBLANK ( [Prix Médian TTC] ) ),",
      "        [Prix Médian TTC],", "        ,", "        ASC,", "        DENSE", "    )", ")"]),
    ("Prix Station TTC", "Simulateur", FORMATS["price"],
     "Prix moyen annuel TTC d'une station (sans seuil d'échantillon : valeur individuelle).",
     ["CALCULATE ( AVERAGE ( fact_station_year[price_ttc_mean] ), " + YEAR_SCOPE + " )"]),
    ("Écart au Moins Cher 5 km (cts)", "Simulateur", FORMATS["cts"],
     "Écart moyen (cts/L) entre le prix de la station et celui de la station la moins chère à moins de 5 km le même jour. Pour un groupe de stations : BLANK sous [Seuil Stations].",
     ["VAR v =", "    CALCULATE ( AVERAGE ( fact_station_year[gap_vs_cheapest_5km_cts] ), " + SCOPE + " )",
      "RETURN", "    IF ( HASONEVALUE ( dim_station[station_key] ) || [Nb Stations] >= [Seuil Stations], v )"]),
    ("Litres par an", "Simulateur", "#,0",
     "Consommation annuelle du conducteur type : Km par an × Conso L/100 ÷ 100.",
     ["[Valeur Km par an] * [Valeur Conso L/100] / 100"]),
    ("Économie Annuelle €", "Simulateur", '#,0 "€"',
     "Économie annuelle en euros en faisant le plein à la station la moins chère à moins de 5 km : écart (€/L) × litres par an.",
     ["DIVIDE ( [Écart au Moins Cher 5 km (cts)], 100 ) * [Litres par an]"]),
    ("Réponse Cumulée (cts)", "Rockets & Feathers", "0.00",
     "Réponse cumulée du prix HT (cts/L) à un mouvement de 1 ct/L du Brent, h semaines après.",
     ["AVERAGE ( fact_rockets_feathers[cumulative_response] )"]),
    ("IC Bas (cts)", "Rockets & Feathers", "0.00", "Borne basse de l'intervalle de confiance à 95 %.",
     ["AVERAGE ( fact_rockets_feathers[ci_low] )"]),
    ("IC Haut (cts)", "Rockets & Feathers", "0.00", "Borne haute de l'intervalle de confiance à 95 %.",
     ["AVERAGE ( fact_rockets_feathers[ci_high] )"]),
]


def measures_table() -> str:
    lines = ["/// Table des mesures DAX (dossiers d'affichage : Prix, Marché, Écarts, Échantillon, Simulateur, Rockets & Feathers).",
             "table _Mesures", f"\tlineageTag: {tag('_Mesures')}", ""]
    for name, folder, fmt, desc, dax in MEASURES:
        lines.append(f"\t/// {desc}")
        if len(dax) == 1:
            lines.append(f"\tmeasure {q(name)} = {dax[0]}")
        else:
            lines.append(f"\tmeasure {q(name)} =")
            lines += [f"\t\t\t{d}" for d in dax]
        lines.append(f"\t\tformatString: {fmt}")
        lines.append(f"\t\tdisplayFolder: {folder}")
        lines.append(f"\t\tlineageTag: {tag('_Mesures', name)}")
        lines.append("")
    lines += [
        "\tcolumn Colonne", "\t\tdataType: string", "\t\tisHidden", f"\t\tlineageTag: {tag('_Mesures', 'Colonne')}",
        "\t\tsummarizeBy: none", "\t\tsourceColumn: Colonne", "",
        "\t\tannotation SummarizationSetBy = Automatic", "",
        "\tpartition _Mesures = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet",
        "\t\t\t\t    Source = #table(type table [Colonne = text], {})", "\t\t\t\tin", "\t\t\t\t    Source", "",
        "\tannotation PBI_ResultType = Table", "",
    ]
    return "\n".join(lines)


def whatif_table(name: str, dax: str, default: str, fmt: str, dtype: str, desc: str) -> str:
    return "\n".join([
        f"/// {desc}",
        f"table {q(name)}", f"\tlineageTag: {tag(name)}", "",
        f"\tmeasure {q('Valeur ' + name)} = SELECTEDVALUE ( {q(name)}[{name}], {default} )",
        f"\t\tformatString: {fmt}", f"\t\tlineageTag: {tag(name, 'valeur')}", "",
        f"\tcolumn {q(name)}", f"\t\tdataType: {dtype}", f"\t\tformatString: {fmt}", f"\t\tlineageTag: {tag(name, 'col')}",
        "\t\tsummarizeBy: none", "\t\tisNameInferred", "\t\tsourceColumn: [Value]", "",
        "\t\textendedProperty ParameterMetadata =", "\t\t\t\t{", '\t\t\t\t  "version": 0', "\t\t\t\t}", "",
        "\t\tannotation SummarizationSetBy = User", "",
        f"\tpartition {q(name)} = calculated", "\t\tmode: import", f"\t\tsource = {dax}", "",
        f"\tannotation PBI_Id = {tag(name, 'pbi').replace('-', '')}", "",
    ])


RELATIONSHIPS = [
    ("fact_national_daily", "date_key", "dim_date", "date_key"),
    ("fact_national_daily", "fuel_key", "dim_fuel", "fuel_key"),
    ("dim_market", "date_key", "dim_date", "date_key"),
    ("dim_market", "fuel_key", "dim_fuel", "fuel_key"),
    ("fact_department_month", "month_date_key", "dim_date", "date_key"),
    ("fact_department_month", "fuel_key", "dim_fuel", "fuel_key"),
    ("fact_department_month", "department", "dim_department", "department"),
    ("fact_brand_category_month", "month_date_key", "dim_date", "date_key"),
    ("fact_brand_category_month", "fuel_key", "dim_fuel", "fuel_key"),
    ("fact_station_year", "year_date_key", "dim_date", "date_key"),
    ("fact_station_year", "station_key", "dim_station", "station_key"),
    ("fact_station_year", "fuel_key", "dim_fuel", "fuel_key"),
    ("dim_station", "department", "dim_department", "department"),
    ("fact_rockets_feathers", "fuel_key", "dim_fuel", "fuel_key"),
]
TABLES = ["dim_date", "dim_fuel", "dim_station", "dim_department", "dim_market", "dim_event", "fact_national_daily",
          "fact_department_month", "fact_brand_category_month", "fact_station_year", "fact_rockets_feathers"]


def write_semantic_model() -> None:
    d = SM / "definition"
    (d / "tables").mkdir(parents=True, exist_ok=True)
    (SM / "definition.pbism").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/{VERSIONS['semanticModel']}/schema.json",
        "version": "4.2", "settings": {}}, indent=2) + "\n", encoding="utf-8")
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1567\n\n", encoding="utf-8")
    all_tables = TABLES + ["_Mesures", "Km par an", "Conso L/100"]
    model = ["model Model", "\tculture: fr-FR", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
             "\tsourceQueryCulture: fr-FR", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
             "annotation __PBI_TimeIntelligenceEnabled = 0", "",
             'annotation PBI_QueryOrder = ["DataFolder",' + ",".join(f'"{t}"' for t in TABLES + ["_Mesures"]) + "]", ""]
    model += [f"ref table {q(t)}" for t in all_tables]
    (d / "model.tmdl").write_text("\n".join(model) + "\n", encoding="utf-8")
    (d / "expressions.tmdl").write_text("\n".join([
        "/// Dossier absolu contenant les CSV de data/gold (terminé par une barre oblique inverse).",
        f'expression DataFolder = "{DEFAULT_FOLDER}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
        f"\tlineageTag: {tag('DataFolder')}", "",
        "\tannotation PBI_ResultType = Text", "",
    ]) + "\n", encoding="utf-8")
    rel = []
    for f_t, f_c, t_t, t_c in RELATIONSHIPS:
        rel += [f"relationship {tag('rel', f_t, f_c, t_t, t_c)}", f"\tfromColumn: {q(f_t)}.{q(f_c)}",
                f"\ttoColumn: {q(t_t)}.{q(t_c)}", ""]
    (d / "relationships.tmdl").write_text("\n".join(rel), encoding="utf-8")
    for t in TABLES:
        (d / "tables" / f"{t}.tmdl").write_text(table_tmdl(t), encoding="utf-8")
    (d / "tables" / "_Mesures.tmdl").write_text(measures_table(), encoding="utf-8")
    (d / "tables" / "Km par an.tmdl").write_text(whatif_table(
        "Km par an", "GENERATESERIES ( 5000, 40000, 1000 )", "15000", "#,0", "int64",
        "Paramètre de simulation : kilomètres parcourus par an (5 000 à 40 000)."), encoding="utf-8")
    (d / "tables" / "Conso L-100.tmdl").write_text(whatif_table(
        "Conso L/100", "GENERATESERIES ( 4, 12, 0.5 )", "6", "0.0", "double",
        "Paramètre de simulation : consommation en litres aux 100 km (4 à 12)."), encoding="utf-8")


# --------------------------------------------------------------------------------------------------
# Report (PBIR)
# --------------------------------------------------------------------------------------------------
def lit(v) -> dict:
    if isinstance(v, bool):
        return {"expr": {"Literal": {"Value": "true" if v else "false"}}}
    if isinstance(v, (int, float)):
        return {"expr": {"Literal": {"Value": f"{v}D"}}}
    return {"expr": {"Literal": {"Value": "'" + str(v).replace("'", "''") + "'"}}}


def col(entity: str, prop: str) -> dict:
    return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
            "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}


def meas(prop: str, entity: str = "_Mesures") -> dict:
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
            "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}


def year_filter(name: str, year: int) -> dict:
    return {"name": name, "field": {"Column": {"Expression": {"SourceRef": {"Entity": "dim_date"}}, "Property": "year"}},
            "type": "Categorical",
            "filter": {"Version": 2, "From": [{"Name": "d", "Entity": "dim_date", "Type": 0}],
                       "Where": [{"Condition": {"In": {
                           "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": "year"}}],
                           "Values": [[{"Literal": {"Value": f"{year}L"}}]]}}}]}}


class Page:
    def __init__(self, name: str, display: str, filters: list | None = None):
        self.name, self.display, self.visuals, self.filters = name, display, [], filters or []

    def add(self, vid: str, vtype: str, x, y, w, h, title: str, roles: dict[str, list[dict]], objects: dict | None = None,
            filters: list | None = None) -> None:
        visual = {"visualType": vtype}
        if roles:
            visual["query"] = {"queryState": {r: {"projections": p} for r, p in roles.items()}}
        if objects:
            visual["objects"] = objects
        visual["visualContainerObjects"] = {"title": [{"properties": {"show": lit(True), "text": lit(title)}}]}
        visual["drillFilterOtherVisuals"] = True
        container = {"$schema": f"{SCHEMA}/item/report/definition/visualContainer/{VERSIONS['visualContainer']}/schema.json",
                     "name": vid, "position": {"x": x, "y": y, "z": len(self.visuals) * 1000, "height": h, "width": w,
                                                "tabOrder": len(self.visuals) * 1000},
                     "visual": visual}
        if filters:
            container["filterConfig"] = {"filters": filters}
        self.visuals.append(container)

    def textbox(self, vid: str, x, y, w, h, text: str, size: int = 12, bold: bool = False) -> None:
        run = {"value": text, "textStyle": {"fontSize": f"{size}pt"}}
        if bold:
            run["textStyle"]["fontWeight"] = "bold"
        container = {"$schema": f"{SCHEMA}/item/report/definition/visualContainer/{VERSIONS['visualContainer']}/schema.json",
                     "name": vid, "position": {"x": x, "y": y, "z": len(self.visuals) * 1000, "height": h, "width": w,
                                                "tabOrder": len(self.visuals) * 1000},
                     "visual": {"visualType": "textbox",
                                "objects": {"general": [{"properties": {"paragraphs": [{"textRuns": [run]}]}}]},
                                "drillFilterOtherVisuals": True}}
        self.visuals.append(container)


def slicer_objects(dropdown: bool = True) -> dict:
    return {"data": [{"properties": {"mode": lit("Dropdown" if dropdown else "Basic")}}]}


def build_pages() -> list[Page]:
    fuel, year, region = col("dim_fuel", "fuel_label"), col("dim_date", "year"), col("dim_department", "region")
    pages = []

    p = Page("p1_vue_ensemble", "Vue d'ensemble")
    p.textbox("p1_titre", 20, 10, 760, 50, "Plein Tarif : le prix des carburants en France, 2019-2025", 20, True)
    p.add("p1_slicer_carburant", "slicer", 800, 10, 150, 60, "Carburant", {"Values": [fuel]}, slicer_objects())
    p.add("p1_slicer_annee", "slicer", 960, 10, 140, 60, "Année", {"Values": [year]}, slicer_objects())
    p.add("p1_slicer_region", "slicer", 1110, 10, 150, 60, "Région", {"Values": [region]}, slicer_objects())
    for i, (m, t) in enumerate([("Prix Moyen TTC", "Prix moyen TTC"), ("Prix Moyen HT", "Prix moyen HT"),
                                ("Brent €/L", "Brent (€/L)"), ("Part Taxes %", "Part des taxes"),
                                ("Évolution Prix %", "Évolution sur un an")]):
        p.add(f"p1_carte_{i + 1}", "card", 20 + i * 250, 80, 235, 100, t, {"Values": [meas(m)]})
    p.add("p1_courbe_prix", "lineChart", 20, 195, 860, 505, "Prix à la pompe, prix hors taxes et Brent (€/L)",
          {"Category": [col("dim_date", "date")],
           "Y": [meas("Prix National TTC"), meas("Prix National HT"), meas("Brent €/L")]})
    p.add("p1_evenements", "tableEx", 895, 195, 365, 505, "Événements et remise carburant",
          {"Values": [col("dim_event", "label"), col("dim_event", "start_date"), col("dim_event", "end_date")]})
    pages.append(p)

    p = Page("p2_carte", "Carte des prix")
    p.add("p2_slicer_carburant", "slicer", 20, 10, 200, 60, "Carburant", {"Values": [fuel]}, slicer_objects())
    p.add("p2_slicer_annee", "slicer", 230, 10, 160, 60, "Année", {"Values": [year]}, slicer_objects())
    p.add("p2_carte", "filledMap", 20, 80, 640, 620, "Prix médian par département",
          {"Category": [col("dim_department", "map_location")], "Y": [meas("Prix Médian TTC")],
           "Tooltips": [meas("Écart vs Médiane Nationale (cts)"), meas("Rang Département"), meas("Nb Stations")]})
    p.add("p2_classement", "tableEx", 680, 80, 580, 330, "Départements : du moins cher au plus cher",
          {"Values": [col("dim_department", "department_name"), meas("Prix Médian TTC"),
                      meas("Écart vs Médiane Nationale (cts)"), meas("Rang Département"), meas("Nb Stations")]})
    p.add("p2_villes", "pivotTable", 680, 420, 580, 280, "Région > département > ville",
          {"Rows": [col("dim_department", "region"), col("dim_department", "department_name"), col("dim_station", "city_std")],
           "Values": [meas("Prix Médian TTC"), meas("Écart vs Médiane Nationale (cts)"), meas("Nb Stations")]})
    pages.append(p)

    p = Page("p3_enseignes", "Qui est moins cher ?")
    p.add("p3_slicer_carburant", "slicer", 20, 10, 200, 60, "Carburant", {"Values": [fuel]}, slicer_objects())
    p.add("p3_slicer_annee", "slicer", 230, 10, 160, 60, "Année", {"Values": [year]}, slicer_objects())
    p.add("p3_carte_prime", "card", 1010, 10, 250, 90, "Prime autoroute (vs stations à 10 km)",
          {"Values": [meas("Prime Autoroute (cts)")]})
    p.add("p3_categories", "clusteredBarChart", 20, 110, 610, 280, "Prix médian par type d'enseigne",
          {"Category": [col("dim_station", "brand_category_label")], "Y": [meas("Prix Médian TTC")]})
    p.add("p3_route", "clusteredColumnChart", 650, 110, 610, 280, "Autoroute ou route : prix médian",
          {"Category": [col("dim_station", "road_type")], "Y": [meas("Prix Médian TTC")]})
    p.add("p3_enseignes", "clusteredBarChart", 20, 400, 1240, 300,
          "Écart de chaque enseigne à la médiane de son département (cts/L)",
          {"Category": [col("dim_station", "brand")], "Y": [meas("Écart Enseigne (cts)")]})
    pages.append(p)

    p = Page("p4_rockets", "Rockets & Feathers")
    p.add("p4_slicer_carburant", "slicer", 20, 10, 200, 60, "Carburant", {"Values": [fuel]}, slicer_objects())
    p.add("p4_slicer_periode", "slicer", 230, 10, 260, 60, "Période d'estimation",
          {"Values": [col("fact_rockets_feathers", "sample")]}, slicer_objects())
    p.add("p4_reponses", "lineChart", 20, 80, 620, 420, "Réponse cumulée du prix HT à 1 ct de Brent (hausse vs baisse)",
          {"Category": [col("fact_rockets_feathers", "horizon_weeks")], "Series": [col("fact_rockets_feathers", "direction")],
           "Y": [meas("Réponse Cumulée (cts)")], "Tooltips": [meas("IC Bas (cts)"), meas("IC Haut (cts)")]})
    p.add("p4_crise_2022", "lineChart", 660, 80, 600, 420, "Crise de 2022 : prix TTC, prix HT et Brent (€/L)",
          {"Category": [col("dim_date", "date")],
           "Y": [meas("Prix National TTC"), meas("Prix National HT"), meas("Brent €/L")]},
          filters=[year_filter("filtre_2022", 2022)])
    p.textbox("p4_texte", 20, 515, 620, 185,
              "Sur 2019-2025, une hausse du Brent est répercutée plus vite qu'une baisse sur le gazole : après une semaine, "
              "le prix HT a absorbé environ 1,8 ct par centime de hausse contre 0,7 ct par centime de baisse (écart "
              "significatif à 1 et 2 semaines). L'asymétrie disparaît hors 2022 : elle tient à la crise, quand les marges "
              "de raffinage du gazole ont flambé avec le Brent. Chiffres exacts : reports/findings.md.", 11)
    p.add("p4_evenements", "tableEx", 660, 515, 600, 185, "Remise carburant et événements",
          {"Values": [col("dim_event", "label"), col("dim_event", "start_date"), col("dim_event", "end_date"),
                      col("dim_event", "amount_ttc")]})
    pages.append(p)

    p = Page("p5_simulateur", "Simulateur d'économies", filters=[year_filter("filtre_annee_simulateur", 2025)])
    p.add("p5_slicer_ville", "slicer", 20, 10, 240, 60, "Ville", {"Values": [col("dim_station", "city_std")]}, slicer_objects())
    p.add("p5_slicer_carburant", "slicer", 270, 10, 180, 60, "Carburant", {"Values": [fuel]}, slicer_objects())
    p.add("p5_slicer_km", "slicer", 460, 10, 390, 60, "Km par an", {"Values": [col("Km par an", "Km par an")]})
    p.add("p5_slicer_conso", "slicer", 860, 10, 400, 60, "Conso L/100", {"Values": [col("Conso L/100", "Conso L/100")]})
    p.add("p5_carte_economie", "card", 20, 85, 400, 120, "Économie annuelle moyenne", {"Values": [meas("Économie Annuelle €")]})
    p.add("p5_carte_ecart", "card", 440, 85, 400, 120, "Écart moyen au moins cher à 5 km",
          {"Values": [meas("Écart au Moins Cher 5 km (cts)")]})
    p.add("p5_carte_litres", "card", 860, 85, 400, 120, "Litres par an", {"Values": [meas("Litres par an")]})
    p.add("p5_stations", "tableEx", 20, 220, 1240, 480, "Stations de la ville : prix et économie possible",
          {"Values": [col("dim_station", "brand"), col("dim_station", "address"), meas("Prix Station TTC"),
                      meas("Écart au Moins Cher 5 km (cts)"), meas("Économie Annuelle €")]})
    pages.append(p)
    return pages


def write_report() -> None:
    d = RP / "definition"
    if d.exists():
        shutil.rmtree(d)
    (d / "pages").mkdir(parents=True)
    res = RP / "StaticResources" / "RegisteredResources"
    res.mkdir(parents=True, exist_ok=True)
    shutil.copy(PBI / "theme.json", res / "PleinTarifTheme.json")
    (RP / "definition.pbir").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definitionProperties/{VERSIONS['definitionProperties']}/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": "../PleinTarif.SemanticModel"}}}, indent=2) + "\n",
        encoding="utf-8")
    (d / "version.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/versionMetadata/{VERSIONS['versionMetadata']}/schema.json",
        "version": "2.0.0"}, indent=2) + "\n", encoding="utf-8")
    (d / "report.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/report/{VERSIONS['report']}/schema.json",
        "themeCollection": {"customTheme": {"name": "PleinTarifTheme.json",
                                            "reportVersionAtImport": {"visual": "2.0.0", "report": "3.0.0", "page": "2.0.0"},
                                            "type": "RegisteredResources"}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": "PleinTarifTheme.json", "path": "PleinTarifTheme.json", "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True,
                     "allowChangeFilterTypes": True},
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pages = build_pages()
    (d / "pages" / "pages.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/{VERSIONS['pagesMetadata']}/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name}, indent=2) + "\n", encoding="utf-8")
    for p in pages:
        pd_ = d / "pages" / p.name
        (pd_ / "visuals").mkdir(parents=True)
        page = {"$schema": f"{SCHEMA}/item/report/definition/page/{VERSIONS['page']}/schema.json",
                "name": p.name, "displayName": p.display, "displayOption": "FitToPage", "height": 720, "width": 1280}
        if p.filters:
            page["filterConfig"] = {"filters": p.filters}
        (pd_ / "page.json").write_text(json.dumps(page, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for v in p.visuals:
            vd = pd_ / "visuals" / v["name"]
            vd.mkdir()
            (vd / "visual.json").write_text(json.dumps(v, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    write_semantic_model()
    write_report()
    (PBI / "PleinTarif.pbip").write_text(json.dumps({
        "$schema": f"{SCHEMA}/pbip/pbipProperties/{VERSIONS['pbip']}/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": "PleinTarif.Report"}}],
        "settings": {"enableAutoRecovery": True}}, indent=2) + "\n", encoding="utf-8")
    print("Power BI project written to", PBI)


if __name__ == "__main__":
    main()
