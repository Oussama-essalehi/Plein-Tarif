# Guide de construction du tableau de bord Power BI

Le projet `PleinTarif.pbip` (Power BI Project) contient :

- `PleinTarif.SemanticModel/` : le modèle sémantique au format **TMDL** (tables, relations, mesures DAX) ;
- `PleinTarif.Report/` : le rapport au format **PBIR** (une page = un dossier, un visuel = un `visual.json`) ;
- `theme.json` : le thème (palette testée pour les daltonismes, fond clair sobre) ;
- `tools/generate_pbip.py` : régénère le modèle et le rapport à partir des CSV de `data/gold/` ;
- `tools/validate_pbip.py` : valide les fichiers JSON contre les schémas officiels publiés par Microsoft et vérifie
  la structure TMDL et toutes les références (colonnes, mesures, champs des visuels).

La mise en page PBIR est fragile d'une version de Power BI Desktop à l'autre : ce guide décrit chaque visuel pour
pouvoir le reconstruire à la main si besoin.

## 1. Ouvrir le projet

1. Prérequis : Power BI Desktop récent (Windows), avec l'option **Aperçu > Format de rapport Power BI enhanced
   (PBIR)** et **Stockage du modèle sémantique au format TMDL** activées (Fichier > Options > Fonctionnalités en
   préversion) si votre version les présente encore comme des préversions.
2. Générer les données si nécessaire : `python run.py all` (ou `python run.py export` si la base existe déjà). Les
   fichiers `data/gold/*.csv` sont aussi versionnés dans le dépôt.
3. Ouvrir `powerbi/PleinTarif.pbip`.
4. **Régler le paramètre `DataFolder`** : Accueil > Transformer les données > Modifier les paramètres > `DataFolder` =
   chemin absolu du dossier `data\gold\` **terminé par une barre oblique inverse**, par exemple
   `C:\Users\oussama\plein-tarif\data\gold\`.
5. Accueil > **Actualiser**. Les 11 tables se chargent en quelques secondes (environ 21 Mo de CSV).
6. Enregistrer (Ctrl+S) : Power BI réécrit les fichiers du projet dans son propre format, c'est normal.

Les requêtes Power Query lisent les CSV en UTF-8 et convertissent les types avec la culture `en-US` : les décimales
avec un point sont donc bien lues même sur un Windows réglé en français.

## 2. Modèle sémantique

### Tables
| Table | Rôle | Source |
|---|---|---|
| dim_date | table de dates (marquée comme telle, colonne `date`) | dim_date.csv |
| dim_fuel, dim_station, dim_department, dim_event | dimensions | CSV du même nom |
| dim_market | Brent, EUR/USD, taxes et remise par jour et carburant | dim_market.csv |
| fact_national_daily | moyennes nationales quotidiennes | fact_national_daily.csv |
| fact_department_month | mois × département × carburant | fact_department_month.csv |
| fact_brand_category_month | mois × type d'enseigne × route × carburant | fact_brand_category_month.csv |
| fact_station_year | année × station × carburant | fact_station_year.csv |
| fact_rockets_feathers | réponses cumulées du modèle | fact_rockets_feathers.csv |
| _Mesures | table des mesures (colonne technique masquée) | — |
| Km par an, Conso L/100 | paramètres de simulation (« what-if ») | GENERATESERIES |

### Relations (toutes plusieurs-à-un, filtrage dans un seul sens)
| De | Vers |
|---|---|
| fact_national_daily[date_key], dim_market[date_key] | dim_date[date_key] |
| fact_department_month[month_date_key], fact_brand_category_month[month_date_key] | dim_date[date_key] (1er jour du mois) |
| fact_station_year[year_date_key] | dim_date[date_key] (1er janvier) |
| fact_*[fuel_key], dim_market[fuel_key] | dim_fuel[fuel_key] |
| fact_station_year[station_key] | dim_station[station_key] |
| fact_department_month[department], dim_station[department] | dim_department[department] |

Les faits mensuels et annuels sont rattachés au premier jour de leur période. Les mesures fondées sur
`fact_station_year` neutralisent le filtre de date et le remplacent par les années sélectionnées
(`TREATAS(VALUES(dim_date[year]), fact_station_year[year])`) : un filtre sur un mois donne bien la valeur de l'année.

### Mesures (`_Mesures`, dossiers d'affichage)
| Dossier | Mesure | Définition résumée |
|---|---|---|
| Prix | Prix Moyen TTC | Σ prix / Σ stations-jours (fact_department_month), BLANK sous le seuil |
| Prix | Prix Médian TTC | médiane des prix moyens annuels des stations (≥ 30 jours), BLANK sous le seuil |
| Prix | Prix Moyen HT | moyenne HT pondérée par les stations-jours |
| Prix | Prix National TTC / HT | moyenne nationale quotidienne (insensible aux filtres géographiques) |
| Prix | Prix N-1, Évolution Prix % | même période l'année précédente (SAMEPERIODLASTYEAR) |
| Marché | Brent €/L | moyenne de dim_market[brent_eur_l] |
| Marché | Part Taxes % | (accise + TVA) / prix national TTC |
| Écarts | Médiane Nationale TTC | Prix Médian TTC sans filtre géographique ni d'enseigne |
| Écarts | Écart vs Médiane Nationale (cts) | 100 × (médiane du contexte − médiane nationale) |
| Écarts | Prime Autoroute (cts) | médiane de l'écart apparié des stations d'autoroute aux stations à moins de 10 km |
| Écarts | Écart Enseigne (cts) | écart moyen des stations à la médiane de leur département le même jour |
| Écarts | Rang Département | RANKX croissant (1 = le moins cher) parmi les départements sélectionnés |
| Échantillon | Seuil Stations (10), Seuil Jours (30), Nb Stations, Nb Jours, Échantillon Suffisant | règle d'échantillon minimal |
| Simulateur | Prix Station TTC, Écart au Moins Cher 5 km (cts), Litres par an, Économie Annuelle € | simulateur |
| Rockets & Feathers | Réponse Cumulée (cts), IC Bas (cts), IC Haut (cts) | réponses du modèle |
| (tables paramètres) | Valeur Km par an (défaut 15 000), Valeur Conso L/100 (défaut 6) | SELECTEDVALUE |

Chaque mesure porte une description (visible au survol dans le volet Données).

## 3. Pages et visuels

Toutes les pages font 1280 × 720 px, mode « Ajuster à la page ». Chaque visuel a un titre (Format > Général >
Titre). Les positions ci-dessous sont en pixels (x, y, largeur, hauteur).

### Page 1 — Vue d'ensemble
| Visuel | Type | Champs | Position | Mise en forme |
|---|---|---|---|---|
| Titre | Zone de texte | « Plein Tarif : le prix des carburants en France, 2019-2025 » | 20, 10, 760, 50 | 20 pt gras |
| Carburant | Segment (liste déroulante) | dim_fuel[fuel_label] | 800, 10, 150, 60 | sélection unique conseillée |
| Année | Segment (liste déroulante) | dim_date[year] | 960, 10, 140, 60 | |
| Région | Segment (liste déroulante) | dim_department[region] | 1110, 10, 150, 60 | |
| Prix moyen TTC | Carte | [Prix Moyen TTC] | 20, 80, 235, 100 | |
| Prix moyen HT | Carte | [Prix Moyen HT] | 270, 80, 235, 100 | |
| Brent (€/L) | Carte | [Brent €/L] | 520, 80, 235, 100 | |
| Part des taxes | Carte | [Part Taxes %] | 770, 80, 235, 100 | |
| Évolution sur un an | Carte | [Évolution Prix %] | 1020, 80, 235, 100 | |
| Prix à la pompe, prix hors taxes et Brent (€/L) | Graphique en courbes | Axe X : dim_date[date] ; Y : [Prix National TTC], [Prix National HT], [Brent €/L] | 20, 195, 860, 505 | légende en haut ; ajouter une ligne constante ou des bandes pour les périodes de remise si souhaité (Analytique) |
| Événements et remise carburant | Tableau | dim_event[label], [start_date], [end_date] | 895, 195, 365, 505 | tri par start_date |

Note : le segment Région filtre les cartes (Prix Moyen TTC/HT) mais pas la courbe nationale, volontairement.

### Page 2 — Carte des prix
| Visuel | Type | Champs | Position |
|---|---|---|---|
| Carburant, Année | Segments | dim_fuel[fuel_label], dim_date[year] | 20, 10 et 230, 10 |
| Prix médian par département | Carte choroplèthe (« Carte remplie ») | Emplacement : dim_department[map_location] (catégorie de données : État ou province) ; Saturation des couleurs : [Prix Médian TTC] ; Info-bulles : [Écart vs Médiane Nationale (cts)], [Rang Département], [Nb Stations] | 20, 80, 640, 620 |
| Départements : du moins cher au plus cher | Tableau | dim_department[department_name], [Prix Médian TTC], [Écart vs Médiane Nationale (cts)], [Rang Département], [Nb Stations] | 680, 80, 580, 330 |
| Région > département > ville | Matrice | Lignes : dim_department[region], dim_department[department_name], dim_station[city_std] ; Valeurs : [Prix Médian TTC], [Écart vs Médiane Nationale (cts)], [Nb Stations] | 680, 420, 580, 280 |

Carte : Format > Couleurs de remplissage > fx > dégradé de `#CDE2FB` (moins cher) à `#0D366B` (plus cher). Si le
géocodage d'un département échoue (homonymie), utiliser une carte Azure Maps avec le même champ, ou une carte de
formes (Shape map) avec le GeoJSON `data/raw/geo/departements.geojson` et la clé `dim_department[department]`. Les
cartes HTML de `reports/maps/` donnent le rendu attendu.

### Page 3 — Qui est moins cher ?
| Visuel | Type | Champs | Position |
|---|---|---|---|
| Carburant, Année | Segments | dim_fuel[fuel_label], dim_date[year] | 20, 10 et 230, 10 |
| Prime autoroute (vs stations à 10 km) | Carte | [Prime Autoroute (cts)] | 1010, 10, 250, 90 |
| Prix médian par type d'enseigne | Graphique à barres groupées | Axe Y : dim_station[brand_category_label] ; Axe X : [Prix Médian TTC] | 20, 110, 610, 280 |
| Autoroute ou route : prix médian | Histogramme groupé | Axe X : dim_station[road_type] ; Y : [Prix Médian TTC] | 650, 110, 610, 280 |
| Écart de chaque enseigne à la médiane de son département | Graphique à barres groupées | Axe Y : dim_station[brand] ; Axe X : [Écart Enseigne (cts)] ; tri croissant | 20, 400, 1240, 300 |

Conseil : sur le dernier visuel, ajouter un filtre de visuel « Premiers N = 15 par [Nb Stations] » sur
dim_station[brand]. Les enseignes de moins de 10 stations renvoient BLANK et disparaissent d'elles-mêmes.

### Page 4 — Rockets & Feathers
| Visuel | Type | Champs | Position |
|---|---|---|---|
| Carburant | Segment | dim_fuel[fuel_label] (choisir Gazole ou SP95-E10) | 20, 10, 200, 60 |
| Période d'estimation | Segment | fact_rockets_feathers[sample] (« 2019-2025 » ou « 2019-2025 hors 2022 ») | 230, 10, 260, 60 |
| Réponse cumulée du prix HT à 1 ct de Brent | Graphique en courbes | Axe X : fact_rockets_feathers[horizon_weeks] (type continu) ; Légende : [direction] ; Y : [Réponse Cumulée (cts)] ; Info-bulles : [IC Bas (cts)], [IC Haut (cts)] | 20, 80, 620, 420 |
| Crise de 2022 | Graphique en courbes | Axe X : dim_date[date] ; Y : [Prix National TTC], [Prix National HT], [Brent €/L] ; filtre de visuel dim_date[year] = 2022 | 660, 80, 600, 420 |
| Texte explicatif | Zone de texte | constat (voir reports/findings.md) | 20, 515, 620, 185 |
| Remise carburant et événements | Tableau | dim_event[label], [start_date], [end_date], [amount_ttc] | 660, 515, 600, 185 |

Couleurs conseillées : « hausse » `#EB6834` (orange), « baisse » `#2A78D6` (bleu) (Format > Lignes > Couleurs).
Une ligne constante Y = 1 (Analytique) matérialise la transmission complète.

### Page 5 — Simulateur d'économies
Filtre de page : dim_date[year] = 2025 (dernière année complète).

| Visuel | Type | Champs | Position |
|---|---|---|---|
| Ville | Segment (liste déroulante, recherche activée) | dim_station[city_std] | 20, 10, 240, 60 |
| Carburant | Segment | dim_fuel[fuel_label] | 270, 10, 180, 60 |
| Km par an | Segment numérique | 'Km par an'[Km par an] → style « Valeur unique » (curseur) | 460, 10, 390, 60 |
| Conso L/100 | Segment numérique | 'Conso L/100'[Conso L/100] → style « Valeur unique » | 860, 10, 400, 60 |
| Économie annuelle moyenne | Carte | [Économie Annuelle €] | 20, 85, 400, 120 |
| Écart moyen au moins cher à 5 km | Carte | [Écart au Moins Cher 5 km (cts)] | 440, 85, 400, 120 |
| Litres par an | Carte | [Litres par an] | 860, 85, 400, 120 |
| Stations de la ville | Tableau | dim_station[brand], dim_station[address], [Prix Station TTC], [Écart au Moins Cher 5 km (cts)], [Économie Annuelle €] | 20, 220, 1240, 480 |

Les deux segments numériques sont créés comme des paramètres « what-if » : si Power BI les affiche comme des listes,
passer le style du segment à « Valeur unique » (Format > Paramètres du segment > Style).

## 4. Thème
Affichage > Thèmes > Parcourir les thèmes > `powerbi/theme.json` (déjà référencé par le rapport). Couleurs des
séries dans l'ordre : Gazole `#EB6834`, SP95-E10 `#1BAF7A`, bleu `#2A78D6`, puis jaune, rose, vert, violet, rouge.
Fond de page `#FCFCFB`, visuels sur fond blanc avec bordure grise arrondie.

## 5. Valeurs attendues (pour vérifier l'actualisation)
Calculées directement depuis les CSV de `data/gold/` (filtre Année = 2025, aucun filtre géographique) :

| Mesure | Gazole | SP95-E10 |
|---|---|---|
| Prix Moyen TTC | 1,641 € | 1,704 € |
| Prix Moyen HT | 0,759 € | 0,749 € |
| Prix Médian TTC | 1,618 € | 1,680 € |
| Nb Stations | 9 665 | 7 641 |
| Évolution Prix % (2025 vs 2024) | −4,3 % | −5,1 % |
| Part Taxes % | 53,7 % | 56,0 % |
| Prime Autoroute (cts) | +18,5 | +19,0 |
| Rang Département pour Paris (75) | 96 sur 96 | 94 sur 94 |
| Ville = TOULOUSE : Écart au Moins Cher 5 km (cts) | +8,0 | +7,4 |
| Ville = TOULOUSE : Économie Annuelle € (15 000 km, 6 L/100) | 72 € | 66 € |

Brent €/L 2025 : 0,386 €. Si un chiffre diffère nettement, vérifier d'abord `DataFolder` et la culture des types
(étape 1.4 et note sur `en-US`).

## 6. Ce qu'il faut vérifier à l'ouverture
1. Le projet s'ouvre sans erreur de lecture TMDL ou PBIR (sinon, régénérer avec `python powerbi/tools/generate_pbip.py`
   et relancer `python powerbi/tools/validate_pbip.py`).
2. `DataFolder` pointe vers `data\gold\` et l'actualisation charge les 11 tables sans erreur de type.
3. `dim_date` est bien marquée comme table de dates (Outils de table > Marquer comme table de dates > colonne `date`).
4. Les 14 relations apparaissent dans la vue Modèle, toutes en plusieurs-à-un et à sens unique.
5. Les valeurs de la section 5 sont retrouvées.
6. La carte remplie géocode bien les 96 départements (sinon, voir l'alternative en page 2).
7. Les segments « Km par an » et « Conso L/100 » sont en mode curseur.
8. Faire les captures d'écran des 5 pages : `reports/figures/dashboard_1_vue_ensemble.png` à
   `dashboard_5_simulateur.png` (elles sont déjà référencées dans le README).

## 7. Régénérer et valider
```bash
python run.py export
python powerbi/tools/generate_pbip.py
python powerbi/tools/validate_pbip.py
```
La validation télécharge les schémas JSON officiels (developer.microsoft.com/json-schemas/fabric) : rapport
3.3.0, page 2.1.0, conteneur de visuel 2.13.0, métadonnées de pages 1.1.0, définition PBIR 2.0.0, PBISM 1.0.0,
PBIP 1.0.0. La partie TMDL est un contrôle de structure et de références, pas le parseur officiel de Microsoft :
l'ouverture dans Power BI Desktop reste le test final.
