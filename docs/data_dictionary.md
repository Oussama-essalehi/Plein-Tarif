# Dictionnaire de données

Prix en euros par litre (€/L) ; colonnes `*_cts` en centimes par litre. Dates au format ISO. Clés : `date_key` =
`yyyymmdd`, `fuel_key` = identifiant officiel du carburant (1 Gazole, 2 SP95, 3 E85, 4 GPLc, 5 SP95-E10, 6 SP98),
`station_key` = clé de substitution de la station.

## Base DuckDB `data/plein_tarif.duckdb`

### `dim_date` — une ligne par jour (2019-01-01 → 2025-12-31)
| Colonne | Description |
|---|---|
| date_key, date | clé `yyyymmdd`, date |
| year, quarter, month, month_name, year_month, iso_week | calendrier (noms en français) |
| weekday_num, weekday_name, is_weekend | 1 = lundi … 7 = dimanche |
| is_holiday, holiday_name | jour férié national (11 jours, calcul de Pâques) |
| is_day_before_holiday_or_weekend | vendredi ou veille de jour férié |
| discount_ht, discount_label | remise de l'État en vigueur (€ HT/L) |
| event_label | annotation (confinements, invasion de l'Ukraine, grèves des raffineries…) |

### `dim_fuel` — une ligne par carburant
fuel_key, fuel_code (code des fichiers officiels), fuel_label (libellé affiché), is_main_fuel (Gazole, SP95-E10),
display_order.

### `dim_station` — une ligne par station ayant au moins un prix (11 382)
| Colonne | Description |
|---|---|
| station_key, station_id | clé de substitution ; identifiant officiel du point de vente |
| lat, lon | coordonnées en degrés décimaux (nulles si absentes ou hors de la zone) |
| address, postcode, city, city_std | adresse ; ville normalisée (majuscules sans accents, arrondissements fusionnés) |
| department, region_code, region, zone | département (2A/2B pour la Corse), région, `mainland` / `corsica` |
| tax_zone | `mainland`, `idf` (majoration Île-de-France Mobilités) ou `corsica` |
| is_motorway, road_type | station d'autoroute (`pop="A"`) ; « Autoroute » / « Route » |
| services, automate_24_24 | services (séparés par `|`) ; automate 24h/24 |
| brand, brand_category, brand_category_label | enseigne normalisée (OSM) ; catégorie : `supermarket`, `oil_company`, `independent_other`, `unmatched` |
| osm_match_distance_m, brand_ambiguous | distance à la station OSM retenue ; ambiguïté de l'appariement |
| first_year, last_year, motorway_flag_changes | années de présence ; nombre de changements route/autoroute |

### `dim_market` — jour × carburant (taxes métropole hors Île-de-France)
brent_usd_bbl, eurusd (USD pour 1 €), brent_eur_bbl, brent_eur_l, is_trading_day, vat, excise (€/L, majoration
régionale incluse), discount_ht, discount_ttc.

### `ref_tax` — jour × carburant × zone fiscale
vat, excise, discount_ht : utilisés pour calculer `price_ht` de chaque station.

### `fact_price_daily` — station × carburant × jour (71 393 582 lignes)
| Colonne | Description |
|---|---|
| date_key, station_key, fuel_key | clés |
| price_ttc | prix affiché à la pompe (net de la remise 2022) |
| price_ht | prix du distributeur avant accise, TVA et remise : `price_ttc / (1 + TVA) − accise + remise_HT` |
| staleness_days | jours depuis la dernière mise à jour (0 à 14) |

### `fact_price_change` — un changement de prix effectif (17 633 592 lignes)
station_key, fuel_key, date_key, ts (horodatage local), old_price, new_price, change, hours_since_previous.

### `bridge_station_neighbor` — paires de stations voisines
station_key, neighbor_key, relation (`motorway_vs_road` : autoroute → hors autoroute à moins de 10 km ; `nearby` :
toute station → stations à moins de 5 km), distance_km.

### Vues (`sql/02_views.sql`)
`v_price_daily` (faits + attributs lisibles), `v_national_daily` (moyennes et médianes nationales quotidiennes +
Brent et taxes), `v_department_month`, `v_station_year`, `v_motorway_gap_daily` (écart quotidien apparié de chaque
station d'autoroute).

## Couche gold `data/gold/` (entrée de Power BI)

| Fichier | Grain | Colonnes principales |
|---|---|---|
| dim_date, dim_fuel, dim_station, dim_market | comme ci-dessus | |
| dim_department | département | department, department_name, region, region_code, map_location (« Haute-Garonne, France »), n_stations |
| dim_event | événement | event_type (`remise_etat`, `remise_totalenergies`, `annotation`), label, start_date, end_date, amount_ht, amount_ttc |
| fact_national_daily | jour × carburant | n_stations, price_ttc_mean, price_ttc_median, price_ht_mean, price_ht_median |
| fact_department_month | mois × département × carburant | month_date_key (1er du mois), n_stations, n_days, n_station_days, price_ttc_median, price_ttc_mean, price_ht_mean, price_ttc_sum |
| fact_brand_category_month | mois × catégorie d'enseigne × type de route × carburant | mêmes indicateurs |
| fact_station_year | année × station × carburant | year_date_key (1er janvier), n_days, price_ttc_mean, price_ht_mean, gap_vs_department_median_cts, gap_vs_cheapest_5km_cts, motorway_premium_10km_cts |
| fact_rockets_feathers | échantillon × carburant × sens × semaine | horizon_weeks, direction (`hausse` / `baisse`), cumulative_response, ci_low, ci_high, sample |

`price_ttc_sum` et `n_station_days` permettent à Power BI de recalculer des moyennes exactes pondérées par les
stations-jours.

## Fichiers intermédiaires

| Fichier | Contenu |
|---|---|
| data/parsed/prices_<année>.parquet | station_id, fuel_id, fuel, ts, value_raw (valeur du fichier), price (€/L) |
| data/parsed/stations_<année>.parquet | métadonnées brutes de l'année (coordonnées brutes et converties, `pop`, services) |
| data/parsed/stockouts_*, closures_* | ruptures et fermetures |
| data/processed/prices_clean.parquet | prix après règles P1-P6 |
| data/processed/stations.parquet | stations dédoublonnées et géographie |
| data/processed/panel/year=*/ | panel quotidien partitionné par année |
| data/processed/price_changes.parquet | changements de prix |
| data/processed/market_daily.parquet, tax_daily.parquet | marché et taxes |
| data/processed/station_brands.parquet | appariement OSM (table séparée, ODbL) |
| data/processed/*_report.*, *_coverage.csv, tax_validation.csv | rapports chiffrés de chaque étape |
