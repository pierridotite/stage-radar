# Radar Stages Agro

Outil de la promo **Data science, Institut Agro Rennes-Angers** : chaque matin, il récupère les offres de stage
de fin d'études publiées sur les sites carrières de ~380 entreprises (data, agro, luxe, sport, conseil, industrie ;
startups, PME, ETI et grands groupes) et sur 8 jobboards spécialisés,
garde les postes data **situés en France**, classe l'entreprise par taille (startup, PME, ETI, grand groupe)
et donne à chacun une **note d'accessibilité pour notre école** (A à D). Chaque élève peut aussi déposer
son CV pour obtenir un **fit personnel** avec chaque offre.

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
| Sites carrières des entreprises (Workday, SmartRecruiters, Lever, Greenhouse, Ashby, Teamtailor, Workable, Recruitee, Personio, Breezy, DigitalRecruiters, Oracle Recruiting, Phenom, Radancy, iCIMS, SuccessFactors/Talentsoft en RSS, Eightfold, Jibe, recherche LVMH, API Capgemini) | flux publics que les pages carrières appellent elles-mêmes, liste dans `config/companies.yaml` | aucune |
| Jobboards de niche : PASS (stages de la fonction publique), INRAE, Apecita, iQuesta, Sport Jobs Hunter, Vitijob, Jobagri, Emploi-Environnement | flux RSS officiels, API publiques ou sitemap, statut juridique vérifié ; liste dans `config/sources.yaml` | aucune |
| Adzuna | API officielle d'agrégation (couvre une grande partie des jobboards français), requêtes par mots-clés et par grand groupe inaccessible | gratuite sur developer.adzuna.com, variables `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` |
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

## Fit avec son CV

Dans le tableau de bord, « Déposer mon CV » (PDF, Word ou texte) affiche un **fit en %** sur chaque offre et trie
la liste par fit. Sans CV, seule la note A-D s'affiche.

1. **Lecture** : le texte du CV est extrait dans le navigateur (pdf.js, mammoth).
2. **Filtres du profil, par IA** : ce texte est envoyé au petit serveur `cv-worker/` (voir son README), qui demande à
   `gpt-5-nano` les filtres utiles, au **schéma JSON strict** : typologie, compétences et métiers visés (identifiants du
   dictionnaire `config/fit.yaml` uniquement), secteurs (data, agro, luxe, sport, conseil, industrie), régions, langues
   et jusqu'à 12 **thèmes précis** avec leurs synonymes (« maintenance prédictive », « nutrition animale », « trail »...).
   Environ 0,001 $ par CV, **rien n'est stocké** sur le serveur. Si le serveur ne répond pas, les mêmes filtres sont
   repérés par les règles du dictionnaire (sans thèmes précis).
3. **Fit, sans IA** : calculé dans le navigateur en comparant ces filtres au texte complet de chaque offre (compétences
   exigées et appréciées extraites à la collecte par `radar/fit.py`, mêmes règles).

Seuls les filtres sont gardés sur l'ordinateur de l'élève (localStorage), jamais le texte du CV. Les secteurs, thèmes
et régions du profil s'affichent en boutons pour filtrer la liste.

| Composante | Points |
|---|---|
| Compétences **exigées** par l'offre présentes dans le profil (lissé : 2/2 compte moins que 7/7) | jusqu'à 40 |
| Compétences **appréciées** (« un plus », « idéalement »...) présentes | jusqu'à 5 |
| Thèmes précis du profil trouvés dans l'intitulé (12) ou l'annonce (7) | jusqu'à 25 |
| Secteur de l'offre parmi les secteurs visés (3 au plus) | 15 |
| Métier de l'intitulé visé par le profil | 15 (6 si non précisé ou métier data voisin) |
| Région visée | 5 |
| Compétence "bloquante" exigée et absente (Spark, cloud, Java, C++...) | −5 chacune, max −15 |
| Langue exigée absente du profil | −15 |

Très bon fit ≥ 75, bon ≥ 60, moyen ≥ 45. Le détail de chaque offre liste les compétences présentes (✓) et manquantes
(✗) et les thèmes retrouvés. Poids modifiables dans `config/fit.yaml`.

**Les offres, elles, sont notées sans IA** (règles ci-dessus) : aucun coût par offre.

## Trouver des contacts

Sur chaque offre, **« Trouver des contacts »** cherche les personnes à qui demander une recommandation pour CE poste.
Le serveur `cv-worker/` interroge le moteur Tavily (gratuit, 1 000 recherches / mois), limité aux profils LinkedIn
publics, **en entonnoir** :

1. même poste que l'offre (intitulé sans « stage », « H/F », durée...), dans la ville de l'offre ;
2. même poste en France, si l'étape 1 trouve moins de 6 personnes de l'entreprise ;
3. managers de l'équipe (mots de l'intitulé qui décrivent l'équipe, ex. « hors domicile ») ;
4. recrutement / talent acquisition, dans la ville de l'offre ;
5. toujours : les anciens de l'Institut Agro Rennes-Angers / Agrocampus Ouest dans l'entreprise.

Chaque profil est lu **sans IA** : poste actuel (titre et 1re expérience), entreprise, lieu, école. **Seules les
personnes qui travaillent aujourd'hui dans l'entreprise sont gardées** (« Ex Nestlé », une entreprise citée seulement
dans « Autres profils consultés » ou un homonyme sont écartés). Les équipes dites autrement en anglais sont
reconnues (« On Premise » = hors domicile, « Off Premise » = grande distribution...). **Les anciens de l'école
passent toujours en premier**, puis tout le monde est classé du plus utile au moins utile pour cette offre :

| Critère | Points |
|---|---|
| Ancien·ne de l'Institut Agro Rennes-Angers (toujours en tête) / d'une autre école agro | 40 / 20 |
| Même équipe ou entité que l'offre | 30 |
| Même métier que l'offre (data analyst, data scientist, data engineer, statisticien) | 25 (autre métier data : 8) |
| Manage l'équipe | 20 |
| Recrute (RH, talent acquisition) | 15 |
| Même ville que l'offre / même région / ailleurs en France | 15 / 10 / 5 |
| À l'étranger | −20 |

Barème : `config/network.yaml`. LinkedIn n'est pas aspiré : ce sont les résultats publics d'un moteur de recherche.
Les résultats d'une offre sont gardés 7 jours (cache partagé du serveur et navigateur de l'élève) : deux élèves sur la
même offre ne consomment qu'une recherche, soit 4 ou 5 requêtes Tavily par offre.

Tests (OpenAI, Tavily et Adzuna simulés, aucun appel réel) : `python -m unittest discover tests` et
`node --test cv-worker/test/worker.test.mjs`.

## Ajouter une entreprise

- **Entreprises** : lister nom, secteur, taille et page carrières dans un CSV (`name,sector,size,url`), puis
  `python -m radar.discover liste.csv`. Le détecteur lit la page, reconnaît la plateforme de recrutement et
  imprime les lignes vérifiées à coller dans `config/companies.yaml`. Vérifier ensuite avec
  `python -m radar --only <nom>`.

## Automatiser chaque jour

**GitHub Actions** : le workflow `.github/workflows/daily.yml` tourne chaque matin à 7h30 (heure de Paris),
collecte, score et publie le tableau de bord sur GitHub Pages. L'historique `data/radar.db` est gardé d'un jour
à l'autre dans le cache Actions (pas de commit quotidien). Lancer à la main : onglet *Actions → radar-quotidien →
Run workflow*. Pour Adzuna : *Settings → Secrets and variables → Actions*, ajouter `ADZUNA_APP_ID` et `ADZUNA_APP_KEY`. La collecte n'utilise aucune clé OpenAI : celle-ci n'est que dans le serveur `cv-worker/`.

**Sur un PC Windows** : Planificateur de tâches, action `python -m radar`, dossier de départ = ce dossier.

## Couverture et prochaines étapes

`companies.yaml` recense ~690 entreprises, dont ~380 collectées. Pour le CAC 40, la plupart des groupes sont collectés
(parfois via leurs filiales). Restent hors d'atteinte, car leur site carrières est protégé contre les robots
(Cloudflare, Akamai) ou sans flux public : BNP Paribas, Société Générale, Saint-Gobain, Safran, L'Oréal, Equans,
Dassault Systèmes. Leur contournement n'est pas envisagé : **Adzuna** (agrégateur officiel, secrets `ADZUNA_APP_ID` /
`ADZUNA_APP_KEY`) est interrogé pour chacun (« stage <groupe> », liste `adzuna_companies` de `config/sources.yaml`)
et seules les annonces publiées par le groupe lui-même sont gardées.

Plateformes encore non gérées parmi les entreprises recensées : sites maison, Avature, Taleo, Cornerstone, Flatchr,
WeRecruit, Beetween. Les flux Talentsoft et SuccessFactors renvoient au plus 20 offres par mot-clé : la collecte
interroge plusieurs mots-clés (stage, stagiaire, intern, internship) pour limiter les manques.

