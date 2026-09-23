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

Base 55, puis des règles lisibles dans `config/scoring.yaml` (chaque offre affiche les siennes sous « pourquoi ») :

| Signal | Points |
|---|---|
| Profil agro / sciences du vivant demandé | +12 |
| Début janvier-avril 2027 | +10 |
| Entreprise qui recrute déjà dans l'école | +10 |
| Secteur agro | +10 |
| Statistique appliquée (plans d'expériences, modèles mixtes, sensométrie…) | +8 |
| Outils de la promo (Python, R, SQL…) | +2 par outil, max +8 |
| École d'ingénieur / Bac+5 visé ; stage de fin d'études 6 mois | +6 chacun |
| Publiée depuis moins de 7 jours | +3 |
| Stack hors cursus (C++, Scala, Kubernetes…) | −4 par outil, max −12 |
| Autre langue que l'anglais exigée ; écoles très ciblées (X, Centrale, ENSAE…) ; marque très convoitée ; publiée il y a plus de 60 jours | −8 chacun |
| Stage court (2 à 4 mois) | −10 |
| Début en 2026 ; profil école de commerce ; doctorat demandé | −15 chacun |

A ≥ 75, B ≥ 60, C ≥ 45, D en dessous. La grille est un point de départ : enrichir `network_companies`
avec les entreprises où la promo a déjà fait des stages est ce qui améliorera le plus la note.

Une offre n'est gardée que si elle ressemble à un poste data (intitulé data/stat/ML/analyste, ou au moins
3 compétences data dans la description) ; les intitulés juridiques, RH, contenu ou vente sont écartés.

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
