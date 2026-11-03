# Méthodologie

Ce document décrit chaque étape du pipeline, les règles appliquées et leur effet chiffré. Tous les chiffres
proviennent des rapports générés par le pipeline (`data/parsed/parse_report.csv`, `data/processed/*_report.*`,
`reports/tables/`). Période : 1er janvier 2019 – 31 décembre 2025 (`config.yaml`).

## 1. Données sources

| Source | Contenu | Format | Licence |
|---|---|---|---|
| prix-carburants.gouv.fr (fichiers annuels, `donnees.roulez-eco.fr/opendata/annee/<année>`) | stations, prix, ruptures, fermetures | ZIP contenant un XML (ISO-8859-1), 280 à 390 Mo décompressé par an | Licence Ouverte / Open Licence |
| FRED `DCOILBRENTEU` | Brent, USD/baril, quotidien | CSV | domaine public (EIA) |
| FRED `DEXUSEU` (Fed H.10) ; BCE en secours | EUR/USD (USD pour 1 €), quotidien | CSV | domaine public / BCE |
| Ministère de la Transition écologique (DGEC) | prix moyens hebdomadaires, mensuels et annuels HTT et TTC ; marge brute de raffinage | XLSX | Licence Ouverte |
| Guide 2026 sur la fiscalité des énergies (ministère) | taux d'accise, coefficients de conversion, TVA | PDF | Licence Ouverte |
| OpenStreetMap (`amenity=fuel`) | enseignes des stations | extrait `.osm.pbf` (download.openstreetmap.fr) ou API Overpass | ODbL |
| france-geojson (IGN Admin Express) | contours des départements | GeoJSON | Licence Ouverte |

## 2. Lecture des fichiers XML (`parse_xml.py`)

Lecture en flux avec `lxml.etree.iterparse` directement dans l'archive ZIP : chaque `<pdv>` est traité puis
effacé (`clear()` + suppression des frères déjà lus), la mémoire reste stable quelle que soit la taille du fichier.
Les 7 années sont traitées en parallèle (4 processus), environ 25 s par année.

### Particularités de format trouvées en profilant chaque année

| Année | Encodage | Unité des prix | Exemple | Coordonnées | Enregistrements de prix | `<prix/>` vides |
|---|---|---|---|---|---|---|
| 2019 | ISO-8859-1 | millièmes d'euro | `valeur="1328"` = 1,328 € | degrés × 100 000 (`4620114`) | 4 211 139 | 3 295 |
| 2020 | ISO-8859-1 | millièmes d'euro | | idem | 3 860 225 | 3 570 |
| 2021 | ISO-8859-1 | millièmes d'euro | | idem | 4 283 692 | 3 695 |
| 2022 | ISO-8859-1 | euros | `valeur="1.572"` | idem | 5 044 596 | 4 010 |
| 2023 | ISO-8859-1 | euros | | idem | 4 683 353 | 4 177 |
| 2024 | ISO-8859-1 | euros | | idem | 4 587 613 | 4 573 |
| 2025 | ISO-8859-1 | euros | | idem | 5 297 800 | 4 658 |

- **Unité** : détectée par fichier (part des valeurs contenant un point > 50 %), jamais déduite de l'année. Les
  fichiers « euros » contiennent aussi quelques entiers (`"2"`, `"1"` : 3 256 en 2022, 642 en 2025), lus comme des
  euros puis traités par les règles de valeurs aberrantes. Contrôle de plausibilité : la médiane annuelle doit
  être entre 0,50 et 3,00 € (elle va de 1,33 € en 2020 à 1,86 € en 2023).
- **Coordonnées** : entiers ou décimaux en degrés × 100 000 (`4620093.59235`), divisés par 100 000 (diviseur
  détecté sur la médiane des latitudes) ; 69 stations par an n'ont aucune coordonnée.
- **Type de route** : attribut `pop` = `R` (route), `A` (autoroute), rarement `N`.
- **Ruptures** : l'attribut `type` (temporaire / définitive) n'apparaît qu'à partir de 2024.
- **Horodatage** : heure locale sans fuseau. En 2025 beaucoup de stations transmettent par lots vers 00h37.

Sorties : `data/parsed/prices_<année>.parquet`, `stations_<année>.parquet`, `stockouts_*`, `closures_*`.

## 3. Nettoyage (`clean.py`)

### Stations

| Règle | Effet |
|---|---|
| S1 Département depuis le code postal (Corse : 200xx-201xx → 2A, 202xx-206xx → 2B ; DOM : 3 chiffres), région, zone | 14 879 stations distinctes : 14 719 en métropole, 158 en Corse, 2 outre-mer, 1 code postal invalide |
| S2 Coordonnées hors de la boîte englobante de la zone de la station → nulles et signalées | 484 lignes station-année hors boîte, 483 sans coordonnées ; 138 stations sans coordonnées valides au final |
| S3 Une ligne par station : métadonnées de la dernière année, dernières coordonnées valides, historique du drapeau autoroute | 899 stations d'autoroute ; 20 ont changé de statut route/autoroute sur la période |

L'outre-mer (prix administrés, fiscalité différente, 2 stations seulement dans les fichiers) est exclu des
comparaisons.

### Prix

| Règle | Avant | Retirés | Après |
|---|---|---|---|
| P1 Hors périmètre (outre-mer, département inconnu) | 31 968 418 | 0 | 31 968 418 |
| P2 Prix ≤ 0 | 31 968 418 | 0 | 31 968 418 |
| P3 Hors bornes [0,30 € ; 4,00 €] (ex. `9.999`, `0.004`) | 31 968 418 | 934 | 31 967 484 |
| P4 Doublons station / carburant / horodatage (on garde le premier) | 31 967 484 | 875 | 31 966 609 |
| P5 Écart à la médiane du jour > 8 écarts-types robustes (1,4826 × MAD, plancher 0,03 €), par jour, carburant et type de route | 31 966 609 | 40 579 | 31 926 030 |
| P6 Pic isolé : écart > 0,15 € au prix précédent ET au suivant, qui sont égaux à 0,05 € près (ex. 1,703 → 0,71 → 1,71) | 31 926 030 | 16 267 | 31 909 763 |

Au total 0,18 % des enregistrements sont retirés.

**Pourquoi 8 écarts-types robustes et non « 5 MAD » ?** Avec 5 MAD (non normalisé), 356 586 enregistrements
(1,1 %) étaient exclus, dont beaucoup de prix réels (stations chères de montagne ou de centre-ville à +0,25 €). Les
enregistrements retenus par la règle finale sont soit très au-dessus (+0,30 € en médiane), soit à environ la moitié
du prix (fautes de saisie). La médiane est calculée séparément pour l'autoroute pour ne pas confondre la prime
autoroute avec des erreurs.

## 4. Panel quotidien (`daily_panel.py`)

Les stations n'envoient un enregistrement que lorsqu'elles changent (ou confirment) un prix. Le prix d'une station
pour un carburant le jour *d* est le dernier prix connu à la fin du jour *d*, **reporté au plus N jours** ; au-delà,
la station est considérée inactive pour ce carburant (pas de ligne). Construction en DuckDB par `ASOF JOIN` entre
une grille station × carburant × jour et le dernier prix de chaque jour.

**Choix de N = 14 jours** (paramétrable). La valeur initiale de 7 jours perdait beaucoup de stations actives aux
périodes de prix calmes, parce qu'une station dont le prix ne bouge pas n'envoie rien :

| Stations ayant un prix dans les N derniers jours, en % de celles ayant un prix dans les 30 derniers jours (1er jour de chaque trimestre, 2019-2025) | min | moyenne |
|---|---|---|
| N = 7 jours, Gazole | 54,8 % | 77,0 % |
| N = 14 jours, Gazole | 85,0 % | 92,0 % |
| N = 7 jours, SP95-E10 | 62,3 % | 81,8 % |
| N = 14 jours, SP95-E10 | 86,9 % | 93,7 % |

Avec 7 jours, le panel surreprésentait les stations qui changent souvent leurs prix (grande distribution). Détail :
`data/processed/panel_coverage.csv`.

Taille du panel : **71 393 582 lignes** station × carburant × jour, 43 203 séries, 11 382 stations ; 60,2 % des
lignes sont des prix reportés (pas de mise à jour ce jour-là). Table des changements : 17 633 592 changements de prix
effectifs (les confirmations au même prix sont exclues).

## 5. Taxes et prix hors taxes (`market.py`)

```
prix_HT = prix_TTC / (1 + TVA) − accise + remise_HT
```

- **TVA** : 20 % en métropole, 13 % en Corse.
- **Accise (ex-TICPE)** : taux national gelé depuis 2018 jusqu'au 31 juillet 2025 (gazole 0,5940 €/L, SP95/SP98
  0,6829 €/L, SP95-E10 0,6629 €/L, E85 0,1183 €/L, GPLc 20,71 €/100 kg), plus la majoration régionale (gazole
  +0,0135 €/L, essences +0,0073 €/L) et, en Île-de-France, la majoration IDFM (gazole +0,0189 €/L, essences
  +0,0102 €/L). Corse : pas de majoration régionale et minoration de 0,0100 €/L sur les essences. À partir du 1er août
  2025, la majoration régionale est intégrée au tarif national (gazole 0,6075 €/L, SP95-E10 0,6702 €/L). Les taux
  du Guide 2026 sont publiés en €/MWh et convertis avec les coefficients officiels (gazole 3,6 GJ/hL, essences
  3,2 GJ/hL, E85 2,38 GJ/hL). Sources et citations : `config/taxes.yaml`.
- **Hypothèse sur les majorations régionales** : toutes les régions de métropole appliquent le plafond. Vérification
  avec la série officielle hebdomadaire HTT/TTC du ministère : l'accise implicite (TTC/1,2 − HTT) diffère de l'accise
  du modèle de **moins de 0,1 centime par litre pour chaque carburant et chaque année 2020-2025**
  (`data/processed/tax_validation.csv`).
- **GPLc** : taxé aux 100 kg ; masse volumique de 0,557 kg/L, calibrée sur la série officielle (0,1154 €/L d'accise
  implicite chaque année), dans la plage physique 0,51-0,58.
- **Remise carburant 2022** : 0,15 € HT (18 cts TTC) du 1er avril au 31 août, 0,25 € HT (30 cts) du 1er septembre au
  15 novembre, 0,0833 € HT (10 cts) du 16 novembre au 31 décembre (`config/events.yaml`). La remise était déduite à la
  caisse et les prix publiés sont les **prix affichés, nets de la remise** : on la réintègre pour obtenir le prix
  réellement fixé par le distributeur. Contrôle : en septembre 2022, notre moyenne TTC est à 0,5 ct de la moyenne
  officielle pour le SP95-E10 (les deux nettes de remise ; écart moyen habituel : 1,3 ct) ; le ministère calcule de son côté un HTT net de remise.
- **Remise TotalEnergies** (20 cts du 1er septembre au 15 novembre 2022, puis 10 cts) : c'est une baisse de prix
  décidée par le distributeur, elle reste donc dans son prix HT.

Exemple : gazole affiché 1,800 € à Toulouse le 1er mai 2023 → 1,800 / 1,2 − 0,6075 = 0,8925 € HT. Le même prix le
15 septembre 2022 (remise de 0,25 € HT) → 0,8925 + 0,25 = 1,1425 € HT.

**Brent** : USD/baril ÷ (USD par €) ÷ 158,987 L par baril ; les jours sans cotation reprennent la dernière valeur
(5 jours au plus).

## 6. Enseignes OpenStreetMap (`enrich_brands.py`)

- 12 539 stations `amenity=fuel` en France (8 189 points et 4 350 polygones, centroïde des nœuds). L'API Overpass
  n'étant pas joignable depuis l'environnement de développement, l'extrait France de download.openstreetmap.fr a été
  lu avec pyosmium en deux passes (étiquettes puis coordonnées des seuls nœuds utiles) ; les deux sources sont prévues
  (`brands.osm_source`).
- Appariement au plus proche voisin (BallTree, distance de haversine) dans un **rayon de 75 m**. Correspondance
  ambiguë : une deuxième station OSM d'une autre enseigne dans le rayon et à moins de 1,5 × la distance de la
  première (55 cas).
- Normalisation des enseignes (variantes → une enseigne : « Total Access », « Elf », « Elan » → TotalEnergies ;
  « Super U », « Hyper U », « U Express » → Système U, etc.) et catégories : grande distribution, pétrolier,
  indépendant / autre.
- **Taux d'appariement** : 60,7 % des 11 382 stations (62,4 % des stations actives en 2025), distance médiane
  33 m. Les coordonnées officielles sont souvent approximatives (géocodage de l'adresse) :

| Rayon | 25 m | 50 m | 75 m | 100 m | 150 m | 200 m | 300 m | 500 m |
|---|---|---|---|---|---|---|---|---|
| Stations actives en 2025 appariées | 22,5 % | 46,8 % | 62,4 % | 69,5 % | 77,1 % | 81,2 % | 86,5 % | 91,9 % |

75 m privilégie la précision (moins de faux rattachements). Le taux varie selon le département, de 40,7 % (Haute-Marne)
à 89,3 % (Orne) : `data/processed/brand_match_by_department.csv`. Les stations non
rattachées forment une catégorie à part (« Non identifiée »), toujours affichée.

## 7. Modèle en étoile (DuckDB, `model.py`, `sql/`)

Voir `docs/data_dictionary.md`. Le prix HT est calculé en SQL par jointure avec `ref_tax` (date × carburant × zone
fiscale). Paires de voisinage précalculées (BallTree) : stations d'autoroute → stations hors autoroute à moins de
10 km, et toute station → stations à moins de 5 km (97 087 paires).

**Règle d'échantillon minimal** : toute statistique publiée (SQL, Python, Power BI) exige au moins 10 stations et
30 jours par groupe (`config.yaml`).

## 8. Statistiques (`analyze.py`, `rockets_feathers.py`)

- **Médianes géographiques et par enseigne** : unité = station (prix moyen annuel de la station, au moins 30 jours),
  médiane par groupe, IC à 95 % par bootstrap (1 000 rééchantillonnages des stations, graine 42). Les requêtes SQL
  `q1_*` utilisent la médiane des prix station-jour : les deux définitions diffèrent de quelques dixièmes de centime.
- **Prime autoroute** : comparaison appariée. Pour chaque station d'autoroute et chaque jour, écart à la médiane des
  stations hors autoroute à moins de 10 km le même jour ; médiane par station, puis distribution entre stations.
- **Comparaison des enseignes à géographie égale** : écart de chaque station à la médiane des autres stations à moins
  de 5 km le même jour, moyenné par enseigne.
- **Rockets & feathers** : modèle à retards échelonnés asymétrique sur les moyennes nationales hebdomadaires
  (semaine finissant le vendredi), en centimes par litre :
  `Δp_t = α + Σ_{i=0..k} β⁺_i ΔB⁺_{t−i} + Σ_{i=0..k} β⁻_i ΔB⁻_{t−i} + γ Δp_{t−1} + ε_t`,
  p = prix HT national (remise réintégrée), B = Brent en c€/L, ΔB⁺ = max(ΔB, 0), ΔB⁻ = min(ΔB, 0). Erreurs de
  Newey-West (4 retards). k choisi par BIC entre 1 et 8 sur un échantillon commun (k = 2 pour les deux carburants).
  Réponses cumulées sur 0 à 8 semaines ; bandes à 95 % par 10 000 tirages des paramètres dans N(θ̂, V_HAC). Tests :
  Wald β⁺₀ = β⁻₀ ; Wald Σβ⁺ = Σβ⁻ (symétrie de long terme) ; égalité des réponses cumulées à 1, 2 et 4 semaines
  (simulation). Robustesse : même modèle sans les semaines de 2022 (et les 10 semaines suivantes, pour que les
  retards ne touchent pas 2022).
- **Remise 2022** : étude d'événement autour de chaque début, changement et fin de la remise : prix moyens TTC et HT
  sur les 14 jours avant vs les 14 jours à partir de l'événement, comparés au Brent sur la même fenêtre.
- **Jour de la semaine** : test du khi-deux sur le nombre de changements par jour (attendu proportionnel au nombre de
  jours calendaires) ; régression de la taille des changements et de la probabilité de hausse sur des effets fixes
  jour de semaine (échantillon aléatoire de 400 000 changements tous carburants, erreurs groupées par station) ; écart
  du prix moyen national du jour à sa moyenne mobile centrée sur 7 jours.
- **Économies** : pour chaque station de la ville choisie et chaque jour, écart au prix de la station la moins chère
  à moins de 5 km (elle-même comprise), moyenné par station ; économie annuelle = écart × litres par an (15 000 km à
  6 L/100 km = 900 L par défaut).

## 9. Contrôles qualité

- **SQL vs pandas** : moyenne nationale du gazole en juin 2019, juin 2022 et juin 2025, et médiane 2025 en
  Haute-Garonne (31) et à Paris (75), recalculées en pandas à partir des lignes brutes : écart nul
  (`reports/tables/quality_checks.json`).
- **Moyennes officielles (DGEC)** : sur 72 mois, notre moyenne mensuelle est corrélée à 0,997-1,000 avec la moyenne
  officielle et la dépasse de 1,2 à 2,1 centimes en moyenne selon le carburant (gazole +1,9 ct). Explication : la DGEC
  pondère par les parts de marché des distributeurs (volumes vendus), couvre la métropole hors Corse et utilise les
  prix du vendredi ; notre moyenne est non pondérée sur les stations. La grande distribution, moins chère, vend la
  majorité des volumes. Notre **médiane** est, elle, à −0,3 ct de la moyenne officielle pour le gazole
  (`reports/tables/official_comparison_monthly.csv`).
- **Taxes** : voir section 5 (écart < 0,1 ct).
- **Tests unitaires** : `pytest` (48 tests) sur les formats de chaque année, les unités, les règles de nettoyage,
  le report avec ancienneté maximale, le calcul HT, l'appariement spatial et la normalisation des enseignes.
