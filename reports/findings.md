# Plein Tarif — résultats

Période 2019-2025, France métropolitaine et Corse, 11 382 stations, 71 millions de prix quotidiens. Sauf mention
contraire, « 2025 » désigne la dernière année complète et les écarts sont en centimes par litre (cts).
Les tableaux sources sont dans `reports/tables/` ; la méthode dans `docs/methodology.md`.

## Six constats

### 1. Le lieu compte, mais surtout aux extrêmes
En 2025, la médiane des prix moyens des stations est de **1,580 €/L de gazole dans le Finistère** contre
**1,785 €/L à Paris**, soit **20,5 cts d'écart** (IC 95 % bootstrap : 1,576-1,586 et 1,780-1,792). Cet écart
tient surtout à la Corse et à la petite couronne parisienne : hors Corse, Paris et Hauts-de-Seine, Seine-Saint-Denis,
Val-de-Marne, il tombe à 10,7 cts, et 80 % des départements sont dans une fourchette de 5,9 cts. L'Ouest est le
moins cher (Bretagne 1,586 €, Pays de la Loire 1,593 €), la Corse la plus chère (1,759 €). Parmi les grandes villes,
Paris (+15,0 cts par rapport à la médiane nationale), Nice (+7,0) et Lyon (+4,0) sont chères ; Rennes (−2,3) et
Bordeaux (−1,8) sont bon marché (requête `q1_big_cities`).

### 2. L'autoroute coûte 18,5 centimes de plus par litre
À carburant et jour identiques, une station d'autoroute est en médiane **18,5 cts plus chère** pour le gazole et
**19,0 cts** pour le SP95-E10 que les stations hors autoroute situées à moins de 10 km (2025, 431 et 419 stations
d'autoroute). 93,5 % des stations d'autoroute sont plus chères que leurs voisines (95,7 % pour le SP95-E10) et une sur
dix l'est de plus de 33 cts. La prime augmente : 15,9 cts en 2019, 12,5 cts en 2022 (quand les prix hors autoroute ont flambé), 18,5 cts
en 2025.

### 3. Grande distribution contre pétroliers : environ 9 centimes
Hors autoroute, en 2025, la médiane du prix moyen des stations de la grande distribution est de **1,598 €/L** de
gazole contre **1,693 €/L** pour les stations des pétroliers (**9,5 cts**) et 1,645 € pour les indépendants. À
géographie égale (écart de chaque station à la médiane des autres stations à moins de 5 km, le même jour), Système U
(−3,6 cts), E.Leclerc (−3,5) et Intermarché (−3,1) sont les moins chers ; TotalEnergies (+5,4), Avia (+6,3) et
Agip/Eni (+9,2) les plus chers. L'écart entre grande distribution et pétroliers existe chaque année depuis 2019
(médianes station-jour, requête `q3_brand_category` : de 3,0 cts en 2022, année de la remise propre à
TotalEnergies, à 8,5 cts en 2020 sur le gazole ; 8,2 cts en 2025). Réserve : l'enseigne n'est connue que pour 62 % des stations actives (appariement
OpenStreetMap à 75 m).

### 4. « Rockets & feathers » : oui pour le gazole, à cause de 2022
Sur 2019-2025, une hausse de 1 ct/L du Brent fait monter le prix hors taxes du gazole de **1,85 ct en une semaine**,
alors qu'une baisse de 1 ct ne le fait baisser que de **0,70 ct**. L'écart est significatif à 1 semaine (p = 0,008) et
à 2 semaines (p = 0,037), plus après 4 semaines (p = 0,13) ; à long terme la différence n'est pas significative
(Wald Σβ⁺ = Σβ⁻ : p = 0,17). **Hors 2022, l'asymétrie disparaît** (0,93 ct contre 0,71 ct après une semaine,
p = 0,43). Pour le SP95-E10, elle n'est significative à aucun horizon (p = 0,06 à 1 semaine). Interprétation : en 2022
les marges de raffinage du gazole ont flambé en même temps que le Brent (marge brute de raffinage moyenne de 14,0 $/bl
en 2022 contre 1,7 à 4,2 $/bl en 2019-2021, source ministère), ce que le Brent seul ne capte pas. Le résultat ne
prouve donc pas que les distributeurs gonflent leurs marges à la hausse : il montre que le prix du gazole a suivi
les produits raffinés plus que le pétrole brut pendant la crise.

### 5. La remise de 2022 est bien arrivée à la pompe
Autour du 1er avril 2022 (remise de 18 cts TTC), le prix moyen du gazole a baissé de **22,0 cts** entre les 14 jours
avant et les 14 jours après, pour une remise de 18 cts et un Brent en baisse de 7,4 cts/L ; le prix HT du distributeur
n'a baissé que de 3,3 cts. Au passage à 30 cts le 1er septembre, le prix du gazole a baissé de 14,1 cts pour 12 cts
de remise supplémentaire. Le SP95-E10 a baissé de 22,2 cts, la remise propre de TotalEnergies (20 cts) s'ajoutant à
celle de l'État. À la sortie, le 1er janvier 2023, le gazole a augmenté de 12,8 cts pour 10 cts de remise supprimée,
et le prix HT de 2,3 cts alors que le Brent baissait de 1,4 ct : une légère reprise de marge, de l'ordre de 2 cts. Le
16 novembre 2022, la baisse de la remise (+20 cts attendus) n'a fait monter le gazole que de 4,2 cts, car son prix HT
a chuté de 13,2 cts à la fin des grèves des raffineries.

### 6. Comparer les stations rapporte 20 fois plus que choisir le jour
À Toulouse en 2025, une station est en moyenne **8,0 cts/L plus chère que la station la moins chère à moins de
5 km** (32 stations, gazole). Pour 15 000 km par an à 6 L/100 km, faire le plein à la station la moins chère proche
fait économiser **72 € par an** en moyenne, et plus de 129 € pour une station sur dix (SP95-E10 : 66 €). Le jour
compte peu : les stations changent surtout leurs prix le vendredi (21 % des changements, dont 44 % seulement de
hausses) et augmentent plutôt le lundi, mais le prix moyen national ne varie que de 0,30 ct entre le mercredi (le
plus cher) et le vendredi (le moins cher), soit 3 € par an. Les différences par jour sont statistiquement nettes
(khi-deux, p < 0,001) mais économiquement minimes.

## Recommandations

### Pour les automobilistes
1. **Éviter l'autoroute pour faire le plein** : sortir à moins de 10 km fait économiser 18 à 19 cts/L, soit environ
   10 € sur un plein de 55 L.
2. **Privilégier la grande distribution** (Système U, E.Leclerc, Intermarché) : 3 à 4 cts de moins que les voisines
   proches, 9 cts de moins que les pétroliers.
3. **Comparer les prix dans un rayon de 5 km** sur prix-carburants.gouv.fr : environ 70 € par an pour un conducteur
   moyen à Toulouse.
4. **Ne pas s'organiser autour du jour de la semaine** : l'effet (moins d'un demi-centime) est négligeable ; à la
   rigueur, le vendredi ou le week-end plutôt que le mercredi.

### Pour un régulateur
1. **Autoroutes** : une prime médiane de 18,5 cts, en hausse depuis 2019 et présente dans 93,5 % des stations,
   justifie d'examiner les conditions des contrats de sous-concession et l'affichage des prix des stations hors
   autoroute à proximité des sorties.
2. **Corse et petite couronne parisienne** : 9 à 17 cts au-dessus de la médiane nationale. À Paris et en petite
   couronne, toutes les stations ont au moins un concurrent à moins de 5 km : le problème tient plus à la rareté et au coût du foncier
   qu'à l'isolement. En Corse, 10 % des stations n'ont aucun concurrent à 5 km. Les stations sans concurrent proche
   sont un peu plus chères partout (+0,65 ct par rapport à celles qui en ont 6 ou plus, gazole 2025).
3. **Transmission des prix en période de crise** : l'asymétrie de 2022 et la reprise d'environ 2 cts de marge HT à la
   fin de la remise plaident pour un suivi hebdomadaire public des marges de raffinage et de distribution, et pas
   seulement du Brent.
4. **Qualité des données ouvertes** : ajouter l'enseigne et des coordonnées vérifiées au flux officiel. Aujourd'hui
   l'enseigne doit être reconstituée par OpenStreetMap (62 % des stations rattachées), et les coordonnées sont souvent
   approximatives.

## Limites
Le Brent est du pétrole brut, pas du gazole ou de l'essence en gros (prix non disponibles librement) ; les stations
ne déclarent leurs prix que lorsqu'ils changent (report d'au plus 14 jours) ; les enseignes ne sont connues que pour
les stations rattachées ; l'outre-mer est exclu ; ces résultats décrivent des corrélations, pas des causes.
