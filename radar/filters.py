"""Filtres : est-ce un stage ? est-ce un poste data ? quel secteur ? quelle zone géographique ?"""
from __future__ import annotations

import re

from .text import compile_terms, fold

INTERNSHIP = compile_terms([
    "stage", "stages", "stagiaire*", "intern", "interns", "internship*", "pfe", "fin d'etudes",
    "end of studies", "end-of-studies", "praktikum", "praktikant*", "tirocinio", "stagista", "practicas", "trainee",
])
NOT_INTERNSHIP = compile_terms([
    "alternance", "alternant*", "apprenti*", "apprentissage", "work-study", "work study", "v.i.e",
    "volontariat international", "graduate program*", "graduate trainee", "management trainee", "werkstudent*", "stage 3e", "stage de 3e", "stage d'observation",
])


def looks_like_internship(title: str, *hints: str) -> bool:
    t = fold(title)
    if NOT_INTERNSHIP.search(t) or re.search(r"(?<![A-Za-z])VIE(?![A-Za-z])", title or ""):
        return False
    return bool(INTERNSHIP.search(t) or any(INTERNSHIP.search(fold(h)) for h in hints if h))


def prepare(text: str) -> str:
    """Texte replié + neutralisation de 'R&D' pour que la recherche du langage R reste fiable."""
    t = fold(text)
    return re.sub(r"\br\s?&\s?d\b|\brecherche et developpement\b", " rnd ", t)


# ---------------------------------------------------------------- secteur
SECTOR_TERMS = {
    "agro": compile_terms(["agro*", "agri*", "alimentaire", "food*", "semence*", "seed*", "elevage", "vegetal*",
                           "laiti*", "dairy", "cooperative", "viti*", "vin", "wine*", "cereal*", "boulanger*",
                           "nutrition", "crop*", "farm*", "ferme*", "animal*", "petfood", "brasserie"]),
    "luxe": compile_terms(["luxe", "luxury", "cosmeti*", "parfum*", "fragrance*", "beaute", "beauty", "joaillerie",
                           "jewel*", "horlog*", "maroquinerie", "haute couture", "fashion",
                           "skincare", "maquillage", "makeup", "champagne", "spirits"]),
    "sport": compile_terms(["sport*", "outdoor", "fitness", "athlet*", "running", "velo", "cycling", "bike*",
                            "football", "rugby", "tennis", "ski", "escalade", "climbing", "montagne", "trail",
                            "performance sportive"]),
    "conseil": compile_terms(["conseil", "consulting", "consultant*", "cabinet", "advisory", "strategy consult*"]),
    "industrie": compile_terms(["industri*", "automobile*", "automotive", "aeronauti*", "aerospace", "avion*",
                                "maintenance predictive", "predictive maintenance", "usine*", "manufactur*",
                                "energie", "energy", "ferroviaire", "railway", "pneumati*", "tyre*", "vehicule*"]),
}


def classify_sector(company: str, title: str, description: str) -> str:
    head, body = fold(f"{company} {title}"), fold(description[:3000])
    scores = {s: 3 * len(rx.findall(head)) + len(set(rx.findall(body))) for s, rx in SECTOR_TERMS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "data"


# ---------------------------------------------------------------- zone et région (France uniquement)
REGIONS = {
    "Île-de-France": (["75", "77", "78", "91", "92", "93", "94", "95"],
                      ["paris", "ile-de-france", "ile de france", "idf", "la defense", "levallois*", "neuilly*",
                       "boulogne-billancourt", "courbevoie", "puteaux", "nanterre", "issy*", "saint-denis",
                       "saint-ouen", "montrouge", "clichy", "rueil*", "suresnes", "massy", "saclay", "palaiseau",
                       "versailles", "velizy*", "guyancourt", "cergy", "evry", "creteil", "ivry*", "gennevilliers",
                       "roissy", "marne-la-vallee", "noisy*", "vincennes", "saint-quentin-en-yvelines"]),
    "Bretagne": (["22", "29", "35", "56"],
                 ["bretagne", "rennes", "brest", "quimper", "lorient", "vannes", "saint-brieuc", "saint-malo",
                  "lannion", "landerneau", "morlaix", "fougeres", "vitre", "loudeac", "ploermel", "pontivy"]),
    "Pays de la Loire": (["44", "49", "53", "72", "85"],
                         ["pays de la loire", "nantes", "angers", "le mans", "laval", "la roche-sur-yon", "cholet",
                          "saint-nazaire", "sable-sur-sarthe", "ancenis", "chateaubriant", "carquefou", "saint-herblain"]),
    "Auvergne-Rhône-Alpes": (["01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"],
                             ["auvergne*", "rhone-alpes", "lyon", "villeurbanne", "grenoble", "saint-etienne",
                              "clermont*", "annecy", "chambery", "valence", "vichy", "montlucon", "aurillac", "le puy*",
                              "roanne", "bourg-en-bresse", "annemasse", "villefontaine", "riom", "chappes"]),
    "Hauts-de-France": (["02", "59", "60", "62", "80"],
                        ["hauts-de-france", "lille", "roubaix", "tourcoing", "villeneuve-d'ascq", "amiens",
                         "dunkerque", "calais", "boulogne-sur-mer", "arras", "lens", "valenciennes", "beauvais",
                         "compiegne", "saint-quentin", "croix", "lesquin"]),
    "Normandie": (["14", "27", "50", "61", "76"],
                  ["normandie", "rouen", "caen", "le havre", "cherbourg", "evreux", "alencon", "dieppe", "saint-lo"]),
    "Grand Est": (["08", "10", "51", "52", "54", "55", "57", "67", "68", "88"],
                  ["grand est", "strasbourg", "reims", "metz", "nancy", "mulhouse", "colmar", "troyes",
                   "charleville*", "epinal", "chalons*", "thionville", "soultz"]),
    "Nouvelle-Aquitaine": (["16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"],
                           ["nouvelle-aquitaine", "bordeaux", "merignac", "pessac", "limoges", "poitiers", "pau",
                            "la rochelle", "bayonne", "biarritz", "niort", "angouleme", "perigueux", "agen",
                            "mont-de-marsan", "cognac", "brive*"]),
    "Occitanie": (["09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"],
                  ["occitanie", "toulouse", "blagnac", "colomiers", "labege", "montpellier", "nimes", "perpignan",
                   "beziers", "narbonne", "carcassonne", "albi", "castres", "tarbes", "rodez", "montauban", "auch"]),
    "Provence-Alpes-Côte d'Azur": (["04", "05", "06", "13", "83", "84"],
                                   ["provence*", "paca", "cote d'azur", "marseille", "aix-en-provence", "nice",
                                    "toulon", "avignon", "cannes", "antibes", "sophia antipolis", "sophia-antipolis",
                                    "grasse", "gap", "la ciotat", "vitrolles", "marignane", "cadarache"]),
    "Centre-Val de Loire": (["18", "28", "36", "37", "41", "45"],
                            ["centre-val de loire", "orleans", "tours", "bourges", "chartres", "blois",
                             "chateauroux", "vierzon"]),
    "Bourgogne-Franche-Comté": (["21", "25", "39", "58", "70", "71", "89", "90"],
                                ["bourgogne*", "franche-comte", "dijon", "besancon", "belfort", "montbeliard",
                                 "sochaux", "chalon-sur-saone", "macon", "auxerre", "nevers"]),
    "Corse": (["20", "2a", "2b"], ["corse", "ajaccio", "bastia"]),
}
_REGION_RX = {r: compile_terms(words) for r, (_, words) in REGIONS.items()}
_DEPT_REGION = {d: r for r, (depts, _) in REGIONS.items() for d in depts}
FOREIGN_WORDS = compile_terms([
    "belgi*", "brussels", "bruxelles", "germany", "allemagne", "deutschland", "munich", "berlin", "hamburg",
    "switzerland", "suisse", "geneva", "geneve", "lausanne", "zurich", "italy", "italie", "milan", "spain", "espagne",
    "madrid", "barcelona", "netherlands", "pays-bas", "amsterdam", "luxembourg", "portugal", "lisbon*", "ireland",
    "dublin", "united kingdom", "uk", "england", "london", "londres", "sweden", "stockholm", "denmark", "copenhagen",
    "austria", "vienna", "poland", "warsaw", "romania", "roumanie", "czech*", "prague", "hungary", "budapest",
    "usa", "united states", "etats-unis", "new york", "california", "boston", "chicago", "texas", "canada",
    "montreal", "toronto", "quebec", "china", "chine", "shanghai", "singapore", "singapour", "hong kong", "japan",
    "japon", "tokyo", "india", "inde", "bangalore", "bengaluru", "mumbai", "brazil", "bresil", "mexico", "dubai",
    "australia", "australie", "new zealand", "korea", "seoul", "south africa", "morocco", "maroc", "tunisia",
    "tunisie", "casablanca", "tunis", "emea", "apac", "latam"])
FRANCE_WORDS = compile_terms(["france", "french", "francais*", "hexagone"])
FR_STOPWORDS = compile_terms(["de", "la", "les", "et", "des", "vous", "pour", "une", "nous", "dans", "est", "au"])
EN_STOPWORDS = compile_terms(["the", "and", "you", "of", "to", "with", "we", "our", "is", "for", "in", "will"])


def region(location: str) -> str:
    loc = fold(location)
    for m in re.finditer(r"(?<!\d)(\d{5})(?!\d)|\((\d{2}|2[ab])\)|- (\d{2}|2[ab])(?!\w)", loc):
        code = m[1][:2] if m[1] else (m[2] or m[3])
        if code in _DEPT_REGION:
            return _DEPT_REGION[code]
    for r, rx in _REGION_RX.items():
        if rx.search(loc):
            return r
    return ""


def zone(country: str, location: str) -> str:
    """france / etranger / inconnu."""
    c = (country or "").lower()
    if c:
        return "france" if c == "fr" else "etranger"
    if region(location) or FRANCE_WORDS.search(fold(location)):
        return "france"
    # lieu non reconnu (petite ville, "Remote"...) : on ne tranche pas, la langue de l'annonce décidera
    return "etranger" if FOREIGN_WORDS.search(fold(location)) else "inconnu"


def written_in_french(text: str) -> bool:
    t = fold(text[:2000])
    fr, en = len(FR_STOPWORDS.findall(t)), len(EN_STOPWORDS.findall(t))
    return fr >= 5 and fr > en


def in_france(o) -> bool:
    """Offres en France uniquement. Sans lieu exploitable, on garde les annonces rédigées en français."""
    z = zone(o.country, o.location)
    return z == "france" or (z == "inconnu" and written_in_french(f"{o.title} {o.description}"))
