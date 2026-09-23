# Radar Stages Agro

Outil de la promo **Data science, Institut Agro Rennes-Angers** : chaque matin, il récupère les offres de stage
de fin d'études publiées sur les sites carrières de ~360 entreprises (data, agro, luxe, sport, conseil, industrie ;
startups, PME, ETI et grands groupes) et sur 8 jobboards spécialisés,
garde les postes data **situés en France**, classe l'entreprise par taille (startup, PME, ETI, grand groupe)
et donne à chacun une **note d'accessibilité pour notre école** (A à D, score sur 100)
ainsi qu'un **match par profil** d'élève.

## Lancer

```bash
pip install -r requirements.txt
python -m radar                  # collecte + scoring, environ 5 minutes
python -m radar --only danone    # une seule entreprise (test)
python -m radar --no-fetch       # re-scorer après avoir modifié la grille
```

Résultats :
- `docs/index.html` : le tableau de bord (ouvrir via un petit serveur : `python -m http.server -d docs`, puis http://localhost:8000)
- `docs/data/offres_du_jour.csv` : la même liste pour Excel (séparateur `;`), aussi publiée en ligne à côté du tableau de bord
- `data/radar.db` : l'historique SQLite (date de première apparition, offres retirées)

## D'où viennent les offres

| Source | Comment | Clé |
|---|---|---|
| Sites carrières des entreprises (Workday, SmartRecruiters, Lever, Greenhouse, Ashby, Teamtailor, Workable, Recruitee, Personio, Breezy, DigitalRecruiters, Oracle Recruiting, SuccessFactors/Talentsoft en RSS, Eightfold, Jibe, recherche LVMH, API Capgemini) | flux publics que les pages carrières appellent elles-mêmes, liste dans `config/companies.yaml` | aucune |
| Jobboards de niche : PASS (stages de la fonction publique), INRAE, Apecita, iQuesta, Sport Jobs Hunter, Vitijob, Jobagri, Emploi-Environnement | flux RSS officiels, API publiques ou sitemap, statut juridique vérifié ; liste dans `config/sources.yaml` | aucune |
| Adzuna | API officielle d'agrégation (couvre une grande partie des jobboards français) | gratuite sur developer.adzuna.com, variables `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` |
| Ajouts manuels | `data/manual_offers.csv` : offres repérées sur LinkedIn, JobTeaser, le forum école… | aucune |

**France uniquement** : une offre est gardée si son lieu est en France (ville, code postal, département ou pays),
ou, quand le lieu n'est pas précisé, si l'annonce est rédigée en français. Le tableau de bord filtre ensuite par région.

**Taille d'entreprise** : indiquée dans `companies.yaml` quand on la connaît, sinon déterminée automatiquement avec
l'[annuaire officiel des entreprises](https://recherche-entreprises.api.gouv.fr) (API de l'État, gratuite, sans clé) :
catégorie INSEE GE → grand groupe, ETI → ETI, PME créée depuis 5 ans ou moins (12 ans dans le logiciel, la data ou la R&D) → startup, sinon PME.

LinkedIn, Indeed, HelloWork, JobTeaser, 1jeune1solution, Welcome to the Jungle, Jobs That Make Sense et
L'Etudiant interdisent l'aspiration de leurs offres (CGU ou robots.txt) : ils ne sont pas scrapés. Les ~90 startups
qui ne publient que sur Welcome to the Jungle sont listées dans `companies.yaml` (`ats: wttj`) pour mémoire. Les offres qu'on y trouve passent par le CSV manuel et reçoivent la même note.

## Comment la note est calculée

La note répond à : « un·e élève data science de l'Institut Agro a-t-il de vraies chances sur ce stage, et est-ce
un vrai stage data ? ». Elle combine trois axes indépendants, chacun détaillé sous « pourquoi » dans le tableau de bord
(grille complète et modifiable dans `config/scoring.yaml`) :

| Axe | Question | Ce qui compte |
|---|---|---|
| **Data** (0-100) | Est-ce un vrai poste data ? | intitulé data (+55) ou d'analyse (+30), compétences data demandées (+8 chacune, max +45), paillasse / terrain / vente (−25). Sous 35, l'offre est écartée. |
| **Profil** (0-100, base 50) | Notre profil correspond-il ? | domaine agro / bio / environnement (+15), statistique appliquée (+10), formation ingénieur / M2 visée (+10), outils de la promo (+4 chacun, max +16), outils hors cursus (−6 chacun, max −18), école de commerce comme **seul** profil cité (−25), doctorat (−25), autre langue exigée (−15) |
| **Concurrence** | Combien de candidats en face ? | forte (−10) : marque très convoitée ou annonce ciblant X/Centrale/HEC ; faible (+6) : entreprise qui recrute déjà à l'Institut Agro, PME, startup, public ; sinon moyenne |
| **Calendrier** | Peut-on commencer en février 2027 ? | début janvier-avril 2027 (+5) ; hors calendrier si début annoncé en 2026 ou stage de 2 à 4 mois ; annonce de plus de 90 jours (−6) |

`score = (Data + Profil) / 2 + concurrence + calendrier`

| Note | Condition |
|---|---|
| **A** | score ≥ 72, **et** Data ≥ 60, **et** Profil ≥ 60 : vrai poste data où notre profil colle |
| **B** | score ≥ 60 et Data ≥ 50 |
| **C** | score ≥ 48 |
| **D** | en dessous |
| **— hors calendrier** | début en 2026 ou stage court, quelle que soit la note |

Les dates ne comptent que si elles suivent une formule de début (« début », « à partir de », « start »...) ou figurent
dans l'intitulé, pour qu'une actualité (« renforcée depuis septembre 2026 ») ne soit pas prise pour une date de stage.
« École de commerce, d'ingénieur ou équivalent » n'est pas pénalisé : seules les annonces qui ne citent que l'école de
commerce le sont. Enrichir `network_companies` avec les entreprises où la promo a déjà fait des stages est ce qui
améliorera le plus la note.

## Ajouter un profil ou une entreprise

- **Profil** : un bloc dans `config/profiles.yaml` (secteurs visés, mots-clés). Sans toucher au code, le
  tableau de bord a aussi un mode « mon profil » où chacun tape ses mots-clés.
- **Entreprises** : lister nom, secteur, taille et page carrières dans un CSV (`name,sector,size,url`), puis
  `python -m radar.discover liste.csv`. Le détecteur lit la page, reconnaît la plateforme de recrutement et
  imprime les lignes vérifiées à coller dans `config/companies.yaml`. Vérifier ensuite avec
  `python -m radar --only <nom>`.

## Automatiser chaque jour

**GitHub Actions** : le workflow `.github/workflows/daily.yml` tourne chaque matin à 7h30 (heure de Paris),
collecte, score et publie le tableau de bord sur GitHub Pages. L'historique `data/radar.db` est gardé d'un jour
à l'autre dans le cache Actions (pas de commit quotidien). Lancer à la main : onglet *Actions → radar-quotidien →
Run workflow*. Pour Adzuna : *Settings → Secrets and variables → Actions*, ajouter `ADZUNA_APP_ID` et `ADZUNA_APP_KEY`.

**Sur un PC Windows** : Planificateur de tâches, action `python -m radar`, dossier de départ = ce dossier.

## Couverture et prochaines étapes

`companies.yaml` recense ~665 entreprises : ~360 collectées, les autres gardées pour mémoire avec leur plateforme
(`ats: unsupported`, `platform: ...`). Plateformes les plus fréquentes parmi les non collectées, donc prochains
connecteurs utiles : sites maison (~64), SuccessFactors sans flux RSS (~24), Flatchr (~18, coopératives et instituts
techniques de l'Ouest, Petzl, FFR), Talentsoft (~14), WeRecruit (~13), Cornerstone (~12).

Les flux Talentsoft ne renvoient que les 20 offres les plus récentes : pour les gros recruteurs (EDF, Dassault
Aviation), une partie des stages peut manquer.
