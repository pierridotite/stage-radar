# Radar Stages Agro

Outil de la promo **Data science, Institut Agro Rennes-Angers** : chaque matin, il récupère les offres de stage
de fin d'études publiées sur les sites carrières de ~360 entreprises (data, agro, luxe, sport, conseil, industrie ;
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

## Analyse par IA (OpenAI, modèle nano)

Chaque offre plausible (pré-filtre par les règles, ~800 sur ~4 000 stages) est lue par `gpt-5-nano` (le moins cher,
repli automatique sur `gpt-4.1-nano`) qui remplit une fiche au **schéma JSON strict** (`radar/llm.py`) : stage ou non,
poste data ou non, métier, domaine, compétences exigées et appréciées (uniquement celles du dictionnaire
`config/fit.yaml`), formation visée, date de début, durée, langues exigées, intensité data et accessibilité pour un·e
élève de l'Institut Agro (chacune avec une phrase de justification), résumé de la mission.

Avec cette fiche, les axes **Data** et **Profil** de la note et la **date de début** viennent du modèle ; la
**concurrence** reste calculée par les règles (listes d'entreprises). Sans fiche (pas de clé, erreur), la notation par
règles s'applique. Les fiches sont gardées en cache dans la base : seules les offres nouvelles ou modifiées sont
réanalysées. Chaque exécution journalise le nombre d'appels et le coût (environ 0,0003 $ par offre : ~0,25 $ la
première fois, quelques centimes par jour ensuite). Réglages et prix dans `config/llm.yaml`.

**La clé n'est jamais dans le dépôt** : elle est lue dans la variable d'environnement `OPENAI_API_KEY`, déclarée comme
secret GitHub (*Settings → Secrets and variables → Actions → New repository secret*, nom `OPENAI_API_KEY`).
Mettre aussi une limite de dépense mensuelle sur le compte OpenAI.

Tests (API simulée, aucun appel réel) : `python -m unittest discover tests` et `node --test cv-worker/test/worker.test.mjs`.

## Fit avec son CV

Dans le tableau de bord, « Déposer mon CV » (PDF, Word ou texte) calcule un **fit** avec chaque offre et permet de
trier par fit. **Le CV est lu dans le navigateur et n'est envoyé nulle part** : le site est statique, sans serveur.
Seule la liste des compétences et domaines repérés est gardée sur l'ordinateur (localStorage), jamais le texte du CV.

**Affiner avec l'IA** (facultatif, sur clic de l'élève) : si le petit serveur `cv-worker/` est déployé (voir
`cv-worker/README.md`), le texte du CV lui est envoyé ; il demande au modèle la typologie du profil, les compétences,
domaines (avec leur force) et métiers visés, dans les identifiants du dictionnaire, puis renvoie la fiche sans rien
stocker. Le site statique ne peut pas appeler OpenAI lui-même : la clé y serait publique.

Le même dictionnaire, `config/fit.yaml`, sert à lire les offres (à la collecte, `radar/fit.py`) et le CV (dans la page) :

| Composante | Points |
|---|---|
| Compétences demandées par l'offre présentes dans le CV (lissé : 2/2 compte moins que 7/7) | jusqu'à 55 |
| Domaine de l'offre (agro, sport, industrie, luxe...) présent dans le CV | jusqu'à 25 |
| Métier de l'intitulé (data scientist, analyst, biostatisticien, R&D...) visé par le CV | 20 (8 si non précisé) |
| Compétence "bloquante" demandée et absente (Spark, cloud, Java, C++...) | −5 chacune, max −15 |
| Langue exigée absente du CV | −15 |

Très bon fit ≥ 75, bon ≥ 60, moyen ≥ 45. Le détail de chaque offre liste les compétences présentes (✓) et manquantes (✗).

## Networking : qui contacter ?

Dans le détail de chaque offre, « Qui contacter ? » propose des recherches LinkedIn classées par priorité : anciens de
l'Institut Agro Rennes-Angers qui font de la data dans l'entreprise, anciens de l'école, anciens d'AgroParisTech et de
l'Institut Agro Montpellier, l'équipe qui recrute (nommée par l'analyse IA), l'équipe data, le recrutement. Elles
s'ouvrent dans le compte LinkedIn de l'élève (outil « Anciens élèves » des pages école, filtré par entreprise) :
**LinkedIn interdit la collecte automatique de profils, l'outil ne récupère donc aucun nom.**

« Mes contacts » garde, sur l'ordinateur de l'élève, les personnes repérées ; les critères cochés (même école, autre
école agro, même entité, même équipe, manager ou recruteur, diplômé récent, relation commune) donnent un score de
match qui classe qui contacter en premier, et « Message » prépare une note d'invitation de moins de 300 caractères.
Critères, points, recherches et messages : `config/network.yaml`.

## Ajouter une entreprise

- **Entreprises** : lister nom, secteur, taille et page carrières dans un CSV (`name,sector,size,url`), puis
  `python -m radar.discover liste.csv`. Le détecteur lit la page, reconnaît la plateforme de recrutement et
  imprime les lignes vérifiées à coller dans `config/companies.yaml`. Vérifier ensuite avec
  `python -m radar --only <nom>`.

